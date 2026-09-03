"""Hash-locked ENV/UOP session authority kept outside project versions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .flash_identity import build_flash_dual_identity
from .flash_projection import FlashRuntimeProjection
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .package_root import resolve_plugin_root
from .timeutil import utc_now

FLASH_MANIFEST_SCHEMA = "evidence-lane.session-flash-manifest.v1"
FLASH_RECEIPT_SCHEMA = "evidence-lane.session-flash-receipt.v1"
FLASH_AUTHORITY_VERSION = "ENV15_UOP15_PUBLIC_LOCKED_20260807"
FLASH_MANIFEST_SHA256 = (
    "45BE5437386E92DB772DB0214B8AC4B11006919E524E60EEC26591C9A14F014D"
)
NESTED_SOURCE_LAYOUT_FLASH_MANIFEST_SHA256 = (
    "4585D703515D2DE245F688E3047F192C6BD3D507475B57855918561933C5293A"
)
NESTED_SOURCE_LAYOUT_AUTHORITY_DIGEST = (
    "644AEEAE1434B3808E544BA9C634ACE3685F21F73D3F86E0CF5DE31D4A6B48A5"
)
NESTED_SOURCE_LAYOUT_SOURCE_AUTHORITY_MANIFEST_SHA256 = (
    "D2DB9386672B64D0EEDFD709036699763A7B002A05360F27E2C726622FDE7B71"
)

_MIGRATABLE_NEW_BUILD_FLASH_ERRORS = {
    "SESSION_FLASH_PROJECTION_BUILD_CHANGED",
    "SESSION_FLASH_SOURCE_AUTHORITY_CHANGED",
    "SESSION_FLASH_BUILD_IDENTITY_CHANGED",
    "SESSION_FLASH_AUTHORITY_CHANGED",
}
ENV_MMD_SHA256 = "9F564A8A5F9476B114DB7DDA91D09CDBFA7BDA2DCC97DD7047FEDD6286DE99CB"
UOP_MMD_SHA256 = "71CA1C1DDBB0E3132295D4FDF6CAC8003DDEBAE94986A850CE921D145A9197F9"
ENV_DOT_SHA256 = "F766A1BE94DCA98A18AE840806FA8067078AB692776F867425CF0FF8FA0E1A8A"
UOP_DOT_SHA256 = "95186C96941E36E77944A7447FD4F88FB90E57A70FDB2AFD553B4B9AC4E402CA"


class SessionFlashAuthority:
    """Verify and persist one idempotent installation-scoped authority flash."""

    def __init__(
        self,
        *,
        data_root: str | Path,
        asset_root: str | Path | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.asset_root = (
            Path(asset_root).resolve() if asset_root else resolve_plugin_root(__file__)
        )
        self.manifest_path = self.asset_root / "env" / "SESSION_FLASH_MANIFEST.json"
        self.receipt_path = (
            self.data_root / "installation" / "session_flash_receipt.json"
        )

    @staticmethod
    def _safe_member(root: Path, relative_name: str) -> Path:
        relative = Path(relative_name)
        require(
            bool(relative_name)
            and not relative.is_absolute()
            and ".." not in relative.parts,
            "SESSION_FLASH_MEMBER_PATH_INVALID",
            "The session-flash manifest contains an unsafe member path.",
            status="FAIL",
            member=relative_name,
        )
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "SESSION_FLASH_MEMBER_PATH_ESCAPE",
                "A session-flash member escaped the locked authority root.",
                status="FAIL",
                details={"member": relative_name},
            ) from exc
        return target

    @staticmethod
    def _parse_lock(path: Path) -> dict[str, str]:
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
        return values

    @staticmethod
    def _sqlite_report(path: Path, expected_user_version: int) -> dict[str, Any]:
        uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=30) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign_keys = [
                dict(row) for row in connection.execute("PRAGMA foreign_key_check")
            ]
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            table_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
                ).fetchone()[0]
            )
        require(
            integrity == ["ok"]
            and not foreign_keys
            and user_version == expected_user_version,
            "SESSION_FLASH_SQLITE_INVALID",
            "A locked session authority SQLite file failed validation.",
            status="MISMATCH",
            member=path.name,
            integrity=integrity,
            foreign_key_errors=foreign_keys,
            user_version=user_version,
            expected_user_version=expected_user_version,
        )
        result = {
            "integrity": integrity,
            "foreign_key_errors": 0,
            "user_version": user_version,
            "tables": table_count,
            "read_mode": "mode=ro&immutable=1",
        }
        return result

    def _manifest(self) -> dict[str, Any]:
        require(
            self.manifest_path.is_file(),
            "SESSION_FLASH_MANIFEST_MISSING",
            "The locked session-flash manifest is missing.",
            status="MISMATCH",
        )
        actual_manifest_hash = sha256_file(self.manifest_path)
        require(
            actual_manifest_hash == FLASH_MANIFEST_SHA256,
            "SESSION_FLASH_MANIFEST_HASH_MISMATCH",
            "The locked session-flash manifest hash does not match authority.",
            status="MISMATCH",
            expected=FLASH_MANIFEST_SHA256,
            actual=actual_manifest_hash,
        )
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EvidenceLaneError(
                "SESSION_FLASH_MANIFEST_JSON_INVALID",
                "The session-flash manifest is not valid JSON.",
                status="FAIL",
                details={"error": str(exc)},
            ) from exc
        require(
            manifest.get("schema") == FLASH_MANIFEST_SCHEMA
            and manifest.get("plugin_id") == "evidence-lane-plugin"
            and manifest.get("authority_version") == FLASH_AUTHORITY_VERSION,
            "SESSION_FLASH_MANIFEST_AUTHORITY_MISMATCH",
            "The session-flash manifest identity is not authorized.",
            status="MISMATCH",
        )
        authorities = manifest.get("authorities", {})
        require(
            authorities.get("env", {}).get("mmd_sha256", "").upper() == ENV_MMD_SHA256
            and authorities.get("uop", {}).get("mmd_sha256", "").upper()
            == UOP_MMD_SHA256,
            "SESSION_FLASH_MMD_ANCHOR_MISMATCH",
            "The ENV/UOP Mermaid anchors do not match locked authority.",
            status="MISMATCH",
        )
        return manifest

    def verify(self) -> dict[str, Any]:
        """Verify exact bundled bytes without writing installation state."""

        manifest = self._manifest()
        members = manifest.get("members", [])
        require(
            isinstance(members, list)
            and len(members) == manifest.get("member_count")
            and all(isinstance(member, dict) for member in members),
            "SESSION_FLASH_MEMBER_LIST_INVALID",
            "The session-flash member list is invalid.",
            status="FAIL",
        )
        member_names = [str(member.get("path", "")) for member in members]
        require(
            len(member_names) == len(set(member_names)),
            "SESSION_FLASH_DUPLICATE_MEMBER",
            "The session-flash manifest contains duplicate members.",
            status="FAIL",
        )
        package_projection_metadata = {
            "env/README.md",
            "env/authority-manifest.v1.json",
            "uop/README.md",
            "uop/authority-manifest.v1.json",
        }
        actual_names = sorted(
            path.relative_to(self.asset_root).as_posix()
            for authority_root in (self.asset_root / "env", self.asset_root / "uop")
            for path in authority_root.rglob("*")
            if path.is_file()
            and path != self.manifest_path
            and path.relative_to(self.asset_root).as_posix()
            not in package_projection_metadata
        )
        require(
            sorted(member_names) == actual_names,
            "SESSION_FLASH_MEMBER_SET_MISMATCH",
            "The locked session-flash directory does not match its manifest.",
            status="MISMATCH",
            missing=sorted(set(member_names) - set(actual_names)),
            unexpected=sorted(set(actual_names) - set(member_names)),
        )
        mismatches: dict[str, dict[str, Any]] = {}
        normalized_members: list[dict[str, Any]] = []
        for member in members:
            relative_name = str(member["path"])
            target = self._safe_member(self.asset_root, relative_name)
            actual_bytes = target.stat().st_size if target.is_file() else None
            actual_sha256 = sha256_file(target) if target.is_file() else None
            expected_sha256 = str(member.get("sha256", "")).upper()
            expected_bytes = int(member.get("bytes", -1))
            if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
                mismatches[relative_name] = {
                    "expected_bytes": expected_bytes,
                    "actual_bytes": actual_bytes,
                    "expected_sha256": expected_sha256,
                    "actual_sha256": actual_sha256,
                }
            normalized_members.append(
                {
                    "path": relative_name,
                    "bytes": expected_bytes,
                    "sha256": expected_sha256,
                }
            )
        require(
            not mismatches,
            "SESSION_FLASH_MEMBER_HASH_MISMATCH",
            "One or more locked session-flash members failed SHA-256 validation.",
            status="MISMATCH",
            mismatches=mismatches,
        )

        authority_reports: dict[str, dict[str, Any]] = {}
        for authority_name in ("env", "uop"):
            authority = manifest["authorities"][authority_name]
            mmd_path = self._safe_member(self.asset_root, authority["mmd"])
            dot_path = self._safe_member(self.asset_root, authority["dot"])
            sqlite_path = self._safe_member(self.asset_root, authority["sqlite"])
            lock_path = self.asset_root / authority_name / "locked_mmd_hash.txt"
            lock = self._parse_lock(lock_path)
            expected_authority_version = "ENV15" if authority_name == "env" else "UOP15"
            require(
                lock.get("authority_version") == expected_authority_version
                and lock.get("mmd_sha256", "").upper() == sha256_file(mmd_path)
                and lock.get("dot_sha256", "").upper() == sha256_file(dot_path)
                and lock.get("sqlite_sha256", "").upper() == sha256_file(sqlite_path),
                "SESSION_FLASH_MMD_LOCK_MISMATCH",
                "A locked ENV/UOP SQLite, Mermaid, or DOT source does not match its lock.",
                status="MISMATCH",
                authority=authority_name,
            )
            require(
                lock.get("predecessor_database_copied") == "false"
                and lock.get("action_plane_build_receipt_sha256", "").upper()
                == str(authority["action_plane_build_receipt_sha256"]).upper(),
                "SESSION_FLASH_ACTION_PLANE_LOCK_MISMATCH",
                "The clean ENV/UOP action-plane build is not lock-bound.",
                status="MISMATCH",
                authority=authority_name,
            )
            authority_reports[authority_name] = {
                "version": authority["version"],
                "mmd_sha256": sha256_file(mmd_path),
                "dot_sha256": sha256_file(dot_path),
                "sqlite_sha256": sha256_file(sqlite_path),
                "sqlite": self._sqlite_report(
                    sqlite_path, int(authority["sqlite_user_version"])
                ),
            }

        source_audit = json.loads(
            (self.asset_root / "env" / "SOURCE_PACKET_AUDIT.json").read_text(
                encoding="utf-8"
            )
        )
        require(
            source_audit.get("overall_status") == "PASS"
            and source_audit.get("whole_packet_accepted") is False
            and source_audit.get("complete_working_behavior_graph_adapted") is True
            and source_audit.get("usable_boundary")
            == "CURRENT_CODEX_ACTION_PLANE_PLUS_ADAPTED_ENV15_3_BEHAVIOR"
            and source_audit.get("chatgpt_host_identity_imported") is False
            and source_audit.get("historical_active_state_imported") is False,
            "SESSION_FLASH_SOURCE_AUDIT_INVALID",
            "The source audit does not bind the clean current Codex action plane.",
            status="FAIL",
        )
        manifest_sha256 = sha256_file(self.manifest_path)
        dual_identity = build_flash_dual_identity(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            source_audit=source_audit,
        )
        authority_digest = sha256_bytes(
            canonical_json_bytes(
                {
                    "schema": manifest["schema"],
                    "authority_version": manifest["authority_version"],
                    "members": sorted(
                        normalized_members, key=lambda item: item["path"]
                    ),
                }
            )
        )
        result = {
            "status": "PASS",
            "authority_version": manifest["authority_version"],
            "authority_digest": authority_digest,
            "manifest_sha256": manifest_sha256,
            "member_count": len(members),
            "flash_scope": manifest["flash_scope"],
            "persistence_state": manifest["persistence_state"],
            "environment_operator_data_inside_pv": False,
            "source_packet": {
                "status": source_audit["overall_status"],
                "whole_packet_accepted": bool(source_audit["whole_packet_accepted"]),
                "complete_working_behavior_graph_adapted": bool(
                    source_audit["complete_working_behavior_graph_adapted"]
                ),
                "usable_boundary": source_audit["usable_boundary"],
            },
            "authorities": authority_reports,
            "prompt": {
                "sha256": sha256_file(
                    self.asset_root / "env" / "UNIVERSAL_FLASH_PROMPT.md"
                ),
                "purpose": "SESSION_BEHAVIOR_ONLY",
                "inside_pv": False,
            },
            "warnings": [manifest["warning"]] if manifest["warning"] else [],
            "dual_identity": dual_identity,
        }
        result["runtime_projection"] = FlashRuntimeProjection(
            data_root=self.data_root,
            asset_root=self.asset_root,
        ).ensure(result)
        return result

    def _validated_receipt(
        self, report: dict[str, Any], receipt: dict[str, Any]
    ) -> dict[str, Any]:
        require(
            receipt.get("schema") == FLASH_RECEIPT_SCHEMA
            and receipt.get("plugin_id") == "evidence-lane-plugin"
            and receipt.get("state") == "FLASHED_UNTIL_PLUGIN_REMOVED",
            "SESSION_FLASH_RECEIPT_INVALID",
            "The installation-scoped session-flash receipt is invalid.",
            status="MISMATCH",
        )
        identity = report["dual_identity"]
        source_identity = identity["source_authority"]["manifest_sha256"]
        projection_identity = identity["codex_projection"]["identity_sha256"]
        build_identity = identity["build_identity"]
        identity_fields = (
            "source_authority_manifest_sha256",
            "codex_projection_identity_sha256",
            "projection_cache_identity_sha256",
            "build_identity_sha256",
            "plugin_version",
        )
        present_identity_fields = [
            field for field in identity_fields if field in receipt
        ]
        require(
            not present_identity_fields
            or len(present_identity_fields) == len(identity_fields),
            "SESSION_FLASH_DUAL_IDENTITY_RECEIPT_PARTIAL",
            "The installed Flash receipt contains an incomplete dual identity.",
            status="FAIL",
            present_fields=present_identity_fields,
        )
        if present_identity_fields:
            same_plugin_version = (
                receipt.get("plugin_version") == build_identity["plugin_version"]
            )
            require(
                receipt.get("codex_projection_identity_sha256") == projection_identity,
                (
                    "SESSION_FLASH_SAME_VERSION_PROJECTION_REUSE_FORBIDDEN"
                    if same_plugin_version
                    else "SESSION_FLASH_PROJECTION_BUILD_CHANGED"
                ),
                (
                    "A changed Codex ENV/UOP projection cannot reuse the same "
                    "plugin version, channel, or cache identity."
                ),
                status="BLOCKED",
                installed_plugin_version=receipt.get("plugin_version"),
                bundled_plugin_version=build_identity["plugin_version"],
                installed_projection_identity=receipt.get(
                    "codex_projection_identity_sha256"
                ),
                bundled_projection_identity=projection_identity,
            )
            require(
                receipt.get("source_authority_manifest_sha256") == source_identity,
                "SESSION_FLASH_SOURCE_AUTHORITY_CHANGED",
                "The independently verified ENV/UOP source-authority identity changed.",
                status="BLOCKED",
            )
            require(
                receipt.get("projection_cache_identity_sha256")
                == build_identity["projection_cache_identity_sha256"]
                and receipt.get("build_identity_sha256")
                == build_identity["identity_sha256"],
                "SESSION_FLASH_BUILD_IDENTITY_CHANGED",
                "The ENV/UOP build identity changed and cannot reuse this receipt.",
                status="BLOCKED",
            )
        require(
            receipt.get("authority_version") == report["authority_version"]
            and receipt.get("authority_digest") == report["authority_digest"]
            and receipt.get("manifest_sha256") == report["manifest_sha256"],
            "SESSION_FLASH_AUTHORITY_CHANGED",
            "The installed flash authority differs from the verified bundle; explicit authority migration is required.",
            status="BLOCKED",
            installed_authority_version=receipt.get("authority_version"),
            bundled_authority_version=report["authority_version"],
        )
        require(
            receipt.get("inside_pv") is False
            and receipt.get("hil_approval_inferred") is False,
            "SESSION_FLASH_RECEIPT_BOUNDARY_INVALID",
            "The session-flash receipt violates the PV or HIL boundary.",
            status="FAIL",
        )
        return receipt

    def status(self) -> dict[str, Any]:
        """Return verified flash status without creating or changing a receipt."""

        report = self.verify()
        if not self.receipt_path.is_file():
            return {
                **report,
                "flash_state": "NOT_FLASHED",
                "flash_action": "NONE",
                "receipt": None,
            }
        try:
            receipt = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise EvidenceLaneError(
                "SESSION_FLASH_RECEIPT_JSON_INVALID",
                "The installation-scoped flash receipt is not valid JSON.",
                status="FAIL",
                details={"error": str(exc)},
            ) from exc
        self._validated_receipt(report, receipt)
        return {
            **report,
            "flash_state": receipt["state"],
            "flash_action": "REUSED",
            "receipt": receipt,
            "receipt_sha256": sha256_file(self.receipt_path),
        }

    def ensure_flashed(self) -> dict[str, Any]:
        """Create the first receipt or idempotently reuse the exact same flash."""

        report = self.verify()
        if self.receipt_path.is_file():
            try:
                return self.status()
            except EvidenceLaneError as exc:
                if exc.code not in _MIGRATABLE_NEW_BUILD_FLASH_ERRORS:
                    raise
                prior_bytes = self.receipt_path.read_bytes()
                prior = json.loads(prior_bytes.decode("utf-8"))
                current_build = report["dual_identity"]["build_identity"]
                nested_layout_migration = (
                    prior.get("manifest_sha256")
                    == NESTED_SOURCE_LAYOUT_FLASH_MANIFEST_SHA256
                    and prior.get("authority_digest")
                    == NESTED_SOURCE_LAYOUT_AUTHORITY_DIGEST
                    and prior.get("source_authority_manifest_sha256")
                    == NESTED_SOURCE_LAYOUT_SOURCE_AUTHORITY_MANIFEST_SHA256
                )
                current_source_identity = report["dual_identity"]["source_authority"][
                    "manifest_sha256"
                ]
                require(
                    prior.get("authority_version") == report["authority_version"]
                    and prior.get("plugin_version") != current_build["plugin_version"],
                    "SESSION_FLASH_BUILD_MIGRATION_INVALID",
                    "A Flash authority migration requires the same authority contract version and a new plugin build.",
                    status="MISMATCH",
                )
                authority_changed = (
                    prior.get("authority_digest") != report["authority_digest"]
                    or prior.get("manifest_sha256") != report["manifest_sha256"]
                )
                source_authority_changed = (
                    prior.get("source_authority_manifest_sha256")
                    != current_source_identity
                )
                projection_changed = (
                    prior.get("codex_projection_identity_sha256")
                    != report["dual_identity"]["codex_projection"]["identity_sha256"]
                )
                prior_sha256 = sha256_bytes(prior_bytes)
                migration_root = (
                    self.data_root
                    / "installation"
                    / "flash_authority_migrations"
                    / (
                        f"{str(prior.get('build_identity_sha256') or prior_sha256)[:32]}_to_"
                        f"{str(current_build['identity_sha256'])[:32]}"
                    )
                )
                archived_receipt = migration_root / "session_flash_receipt.prior.json"
                if archived_receipt.is_file():
                    require(
                        sha256_file(archived_receipt) == prior_sha256,
                        "SESSION_FLASH_BUILD_MIGRATION_ARCHIVE_CONFLICT",
                        "The append-only prior Flash receipt archive contains other bytes.",
                        status="MISMATCH",
                    )
                else:
                    atomic_write_bytes(archived_receipt, prior_bytes)
                migrated = self._receipt_from_report(report)
                migration = {
                    "schema": "evidence-lane.session-flash-build-migration.v1",
                    "status": "PASS",
                    "authority_digest": report["authority_digest"],
                    "source_authority_manifest_sha256": current_source_identity,
                    "prior_plugin_version": prior.get("plugin_version"),
                    "current_plugin_version": current_build["plugin_version"],
                    "prior_receipt_sha256": prior_sha256,
                    "current_build_identity_sha256": current_build["identity_sha256"],
                    "same_version_projection_reuse": False,
                    "migration_scope": (
                        "NEW_PLUGIN_BUILD_FULL_FLASH_AUTHORITY"
                        if authority_changed or source_authority_changed
                        else "NEW_PLUGIN_BUILD_PROJECTION_ONLY"
                    ),
                    "authority_changed": authority_changed,
                    "source_authority_changed": source_authority_changed,
                    "projection_changed": projection_changed,
                    "prior_authority_digest": prior.get("authority_digest"),
                    "current_authority_digest": report["authority_digest"],
                    "prior_manifest_sha256": prior.get("manifest_sha256"),
                    "current_manifest_sha256": report["manifest_sha256"],
                    "prior_source_authority_manifest_sha256": prior.get(
                        "source_authority_manifest_sha256"
                    ),
                    "current_source_authority_manifest_sha256": (
                        current_source_identity
                    ),
                    "source_layout_migrated_from_nested_package": (
                        nested_layout_migration
                    ),
                    "project_state_mutated": False,
                    "pointer_moved": False,
                    "candidate_mutated": False,
                    "migrated_at": utc_now(),
                }
                migration["receipt_sha256"] = sha256_bytes(
                    canonical_json_bytes(migration)
                )
                atomic_write_json(migration_root / "migration.json", migration)
                atomic_write_json(self.receipt_path, migrated)
                result = self.status()
                result["flash_action"] = "BUILD_IDENTITY_MIGRATED"
                result["build_migration"] = migration
                return result
        receipt = self._receipt_from_report(report)
        atomic_write_json(self.receipt_path, receipt)
        created = self.status()
        created["flash_action"] = "CREATED"
        return created

    @staticmethod
    def _receipt_from_report(report: dict[str, Any]) -> dict[str, Any]:
        """Build one installation-scoped Flash receipt from verified assets."""

        receipt = {
            "schema": FLASH_RECEIPT_SCHEMA,
            "receipt_id": f"flash_{report['authority_digest'][:24].lower()}",
            "plugin_id": "evidence-lane-plugin",
            "state": "FLASHED_UNTIL_PLUGIN_REMOVED",
            "authority_version": report["authority_version"],
            "authority_digest": report["authority_digest"],
            "manifest_sha256": report["manifest_sha256"],
            "source_authority_manifest_sha256": report["dual_identity"][
                "source_authority"
            ]["manifest_sha256"],
            "codex_projection_identity_sha256": report["dual_identity"][
                "codex_projection"
            ]["identity_sha256"],
            "projection_cache_identity_sha256": report["dual_identity"][
                "build_identity"
            ]["projection_cache_identity_sha256"],
            "build_identity_sha256": report["dual_identity"]["build_identity"][
                "identity_sha256"
            ],
            "plugin_version": report["dual_identity"]["build_identity"][
                "plugin_version"
            ],
            "flashed_at": utc_now(),
            "scope": "PLUGIN_INSTALLATION_OUTSIDE_PV",
            "inside_pv": False,
            "hil_approval_inferred": False,
        }
        return receipt
