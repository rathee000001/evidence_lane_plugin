"""Separate immutable ENV/UOP authority from the Codex runtime projection."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .errors import EvidenceLaneError, require
from .flash_projection import FLASH_PROJECTION_SCHEMA
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .package_root import resolve_plugin_root

SOURCE_AUTHORITY_MANIFEST_SCHEMA = (
    "evidence-lane.env-uop-source-authority-manifest.v1"
)
CODEX_PROJECTION_MANIFEST_SCHEMA = (
    "evidence-lane.env-uop-codex-projection-manifest.v1"
)
FLASH_BUILD_IDENTITY_SCHEMA = "evidence-lane.env-uop-build-identity.v1"
FLASH_DUAL_IDENTITY_SCHEMA = "evidence-lane.env-uop-dual-identity.v1"
FLASH_IDENTITY_GENERATOR_VERSION = "1"

SOURCE_AUTHORITY_MEMBER_PATHS = frozenset(
    {
        "env/env_mmd.mmd",
        "env/env_mmd.dot",
        "env/env_sqlite.sqlite",
        "uop/uop_mmd.mmd",
        "uop/uop_mmd.dot",
        "uop/uop_sqlite.sqlite",
    }
)

REQUIRED_CODEX_PROJECTION_MEMBER_PATHS = frozenset(
    {
        "env/env_law.md",
        "env/locked_mmd_hash.txt",
        "uop/locked_mmd_hash.txt",
        "uop/uop_law.md",
    }
)


def _normalized_member(member: dict[str, Any]) -> dict[str, Any]:
    path = str(member.get("path") or "")
    digest = str(member.get("sha256") or "").upper()
    size = member.get("bytes")
    require(
        bool(path) and isinstance(size, int) and size >= 0 and len(digest) == 64,
        "SESSION_FLASH_DUAL_IDENTITY_MEMBER_INVALID",
        "A locked Flash member cannot participate in dual identity.",
        status="FAIL",
        member=path or None,
    )
    return {"path": path, "bytes": size, "sha256": digest}


def _plugin_build(plugin_manifest_path: Path) -> dict[str, Any]:
    require(
        plugin_manifest_path.is_file(),
        "SESSION_FLASH_PLUGIN_MANIFEST_MISSING",
        "Dual identity requires the package-local Codex plugin manifest.",
        status="BLOCKED",
        path=str(plugin_manifest_path),
    )
    raw = plugin_manifest_path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceLaneError(
            "SESSION_FLASH_PLUGIN_MANIFEST_INVALID",
            "The package-local Codex plugin manifest is not valid UTF-8 JSON.",
            status="BLOCKED",
            details={"path": str(plugin_manifest_path)},
        ) from exc
    name = str(manifest.get("name") or "").strip()
    version = str(manifest.get("version") or "").strip()
    require(
        name == "evidence-lane-plugin" and bool(version),
        "SESSION_FLASH_PLUGIN_IDENTITY_INVALID",
        "The package-local plugin name or version is invalid.",
        status="MISMATCH",
        plugin_name=name or None,
        plugin_version=version or None,
    )
    return {
        "plugin_name": name,
        "plugin_version": version,
        "plugin_manifest_sha256": sha256_bytes(raw),
    }


def _generator_identity() -> dict[str, str]:
    projection_generator_path = Path(__file__).with_name("flash_projection.py")
    identity_generator_path = Path(__file__)
    return {
        "name": "evidence_lane_plugin.flash_projection",
        "identity_version": FLASH_IDENTITY_GENERATOR_VERSION,
        "projection_schema": FLASH_PROJECTION_SCHEMA,
        "projection_generator_sha256": sha256_file(projection_generator_path),
        "identity_generator_sha256": sha256_file(identity_generator_path),
    }


def build_flash_dual_identity(
    *,
    manifest: dict[str, Any],
    manifest_sha256: str,
    source_audit: dict[str, Any],
    plugin_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build two sealed identities without changing any locked source byte."""

    members = [_normalized_member(item) for item in manifest.get("members", [])]
    by_path = {item["path"]: item for item in members}
    require(
        len(by_path) == len(members),
        "SESSION_FLASH_DUAL_IDENTITY_DUPLICATE_MEMBER",
        "The locked Flash manifest contains duplicate member paths.",
        status="FAIL",
    )
    missing_source_members = sorted(SOURCE_AUTHORITY_MEMBER_PATHS - by_path.keys())
    require(
        not missing_source_members,
        "SESSION_FLASH_SOURCE_AUTHORITY_MEMBER_MISSING",
        "The independently verified ENV/UOP source-authority subset is incomplete.",
        status="MISMATCH",
        missing=missing_source_members,
    )
    missing_projection_members = sorted(
        REQUIRED_CODEX_PROJECTION_MEMBER_PATHS - by_path.keys()
    )
    require(
        not missing_projection_members,
        "SESSION_FLASH_CODEX_PROJECTION_MEMBER_MISSING",
        "The required Codex-derived ENV/UOP projection members are incomplete.",
        status="MISMATCH",
        missing=missing_projection_members,
    )
    require(
        source_audit.get("overall_status") == "PARTIAL_INTEGRITY"
        and source_audit.get("whole_packet_accepted") is False
        and source_audit.get("usable_boundary")
        == "INDEPENDENTLY_VERIFIED_ENV15_UOP15_SUBSET_ONLY",
        "SESSION_FLASH_DUAL_IDENTITY_SOURCE_BOUNDARY_INVALID",
        "Dual identity must retain the accepted partial-integrity boundary.",
        status="FAIL",
    )
    missing_declared = source_audit.get("missing_declared_files")
    require(
        isinstance(missing_declared, list) and bool(missing_declared),
        "SESSION_FLASH_MISSING_DECLARED_MEMBERS_NOT_VISIBLE",
        "Missing parent-packet members must remain visible in dual identity.",
        status="FAIL",
    )
    missing_declared = cast(list[Any], missing_declared)

    source_members = [by_path[path] for path in sorted(SOURCE_AUTHORITY_MEMBER_PATHS)]
    subset_members_sha256 = sha256_bytes(canonical_json_bytes(source_members))
    source_manifest = {
        "schema": SOURCE_AUTHORITY_MANIFEST_SCHEMA,
        "status": "VERIFIED_SUBSET_ONLY",
        "source_packet_status": "PARTIAL_INTEGRITY",
        "whole_packet_accepted": False,
        "usable_boundary": "INDEPENDENTLY_VERIFIED_ENV15_UOP15_SUBSET_ONLY",
        "member_count": len(source_members),
        "members": source_members,
        "subset_members_sha256": subset_members_sha256,
        "missing_declared_members": sorted(str(item) for item in missing_declared),
    }
    source_manifest_sha256 = sha256_bytes(canonical_json_bytes(source_manifest))

    package_root = resolve_plugin_root(__file__)
    resolved_plugin_manifest = Path(plugin_manifest_path).resolve() if (
        plugin_manifest_path is not None
    ) else package_root / ".codex-plugin" / "plugin.json"
    plugin_build = _plugin_build(resolved_plugin_manifest)
    generator = _generator_identity()
    projection_members = [
        by_path[path]
        for path in sorted(by_path.keys() - SOURCE_AUTHORITY_MEMBER_PATHS)
    ]
    projection_manifest = {
        "schema": CODEX_PROJECTION_MANIFEST_SCHEMA,
        "authority_version": manifest.get("authority_version"),
        "locked_flash_manifest_sha256": manifest_sha256.upper(),
        "source_authority_manifest_sha256": source_manifest_sha256,
        "member_count": len(projection_members),
        "members": projection_members,
        "plugin_build": plugin_build,
        "generator": generator,
        "derived_projection_is_source_authority": False,
    }
    projection_identity_sha256 = sha256_bytes(
        canonical_json_bytes(projection_manifest)
    )
    cache_identity_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "schema": FLASH_PROJECTION_SCHEMA,
                "source_authority_manifest_sha256": source_manifest_sha256,
                "codex_projection_identity_sha256": projection_identity_sha256,
                "plugin_version": plugin_build["plugin_version"],
            }
        )
    )
    build_identity_body = {
        "schema": FLASH_BUILD_IDENTITY_SCHEMA,
        **plugin_build,
        "source_authority_manifest_sha256": source_manifest_sha256,
        "codex_projection_identity_sha256": projection_identity_sha256,
        "projection_cache_identity_sha256": cache_identity_sha256,
    }
    build_identity_sha256 = sha256_bytes(
        canonical_json_bytes(build_identity_body)
    )
    return {
        "schema": FLASH_DUAL_IDENTITY_SCHEMA,
        "source_authority": {
            "manifest": source_manifest,
            "manifest_sha256": source_manifest_sha256,
        },
        "codex_projection": {
            "manifest": projection_manifest,
            "identity_sha256": projection_identity_sha256,
        },
        "build_identity": {
            **build_identity_body,
            "identity_sha256": build_identity_sha256,
        },
        "same_version_projection_reuse_allowed": False,
    }
