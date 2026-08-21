"""Hash-locked ENV/UOP session authority kept outside project versions."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .flash_projection import FlashRuntimeProjection
from .hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .timeutil import utc_now

FLASH_MANIFEST_SCHEMA = "evidence-lane.session-flash-manifest.v1"
FLASH_RECEIPT_SCHEMA = "evidence-lane.session-flash-receipt.v1"
FLASH_AUTHORITY_VERSION = "ENV15_UOP15_PUBLIC_LOCKED_20260807"
FLASH_MANIFEST_SHA256 = (
    "4585D703515D2DE245F688E3047F192C6BD3D507475B57855918561933C5293A"
)
ENV_MMD_SHA256 = "360C9878658106A24CFE60E0CDD98BB95EA8BAABB7229C84C9D9163F397981F1"
UOP_MMD_SHA256 = "7D9A51F7B29D26B2B7AB120A08D7CF504AB86C2FEFE709B9C2681E32ACCD1189"


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
            Path(asset_root).resolve()
            if asset_root
            else Path(__file__).resolve().parent / "session_flash" / "env15"
        )
        self.manifest_path = self.asset_root / "SESSION_FLASH_MANIFEST.json"
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
        actual_names = sorted(
            path.relative_to(self.asset_root).as_posix()
            for path in self.asset_root.rglob("*")
            if path.is_file() and path.name != self.manifest_path.name
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
            sqlite_path = self._safe_member(self.asset_root, authority["sqlite"])
            lock_path = self.asset_root / authority_name / "locked_mmd_hash.txt"
            lock = self._parse_lock(lock_path)
            prefix = "env_mmd" if authority_name == "env" else "uop_mmd"
            require(
                lock.get("final_mmd_sha256", "").upper() == sha256_file(mmd_path)
                and lock.get("svg_sha256", "").upper()
                == sha256_file(self.asset_root / authority_name / f"{prefix}.svg")
                and lock.get("png_sha256", "").upper()
                == sha256_file(self.asset_root / authority_name / f"{prefix}.png"),
                "SESSION_FLASH_MMD_LOCK_MISMATCH",
                "A locked ENV/UOP Mermaid source or render does not match its lock.",
                status="MISMATCH",
                authority=authority_name,
            )
            authority_reports[authority_name] = {
                "version": authority["version"],
                "mmd_sha256": sha256_file(mmd_path),
                "sqlite_sha256": sha256_file(sqlite_path),
                "sqlite": self._sqlite_report(
                    sqlite_path, int(authority["sqlite_user_version"])
                ),
            }

        source_audit = json.loads(
            (self.asset_root / "SOURCE_PACKET_AUDIT.json").read_text(encoding="utf-8")
        )
        require(
            source_audit.get("overall_status") == "PARTIAL_INTEGRITY"
            and source_audit.get("whole_packet_accepted") is False
            and source_audit.get("usable_boundary")
            == "INDEPENDENTLY_VERIFIED_ENV15_UOP15_SUBSET_ONLY",
            "SESSION_FLASH_SOURCE_AUDIT_INVALID",
            "The source packet audit does not preserve the partial-integrity boundary.",
            status="FAIL",
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
            "manifest_sha256": sha256_file(self.manifest_path),
            "member_count": len(members),
            "flash_scope": manifest["flash_scope"],
            "persistence_state": manifest["persistence_state"],
            "environment_operator_data_inside_pv": False,
            "source_packet": {
                "status": source_audit["overall_status"],
                "whole_packet_accepted": False,
                "usable_boundary": source_audit["usable_boundary"],
            },
            "authorities": authority_reports,
            "prompt": {
                "sha256": sha256_file(self.asset_root / "UNIVERSAL_FLASH_PROMPT.md"),
                "purpose": "SESSION_BEHAVIOR_ONLY",
                "inside_pv": False,
            },
            "warnings": [manifest["warning"]],
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
            return self.status()
        receipt = {
            "schema": FLASH_RECEIPT_SCHEMA,
            "receipt_id": f"flash_{report['authority_digest'][:24].lower()}",
            "plugin_id": "evidence-lane-plugin",
            "state": "FLASHED_UNTIL_PLUGIN_REMOVED",
            "authority_version": report["authority_version"],
            "authority_digest": report["authority_digest"],
            "manifest_sha256": report["manifest_sha256"],
            "flashed_at": utc_now(),
            "scope": "PLUGIN_INSTALLATION_OUTSIDE_PV",
            "inside_pv": False,
            "hil_approval_inferred": False,
        }
        atomic_write_json(self.receipt_path, receipt)
        created = self.status()
        created["flash_action"] = "CREATED"
        return created
