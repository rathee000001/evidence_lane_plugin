"""One generalized, ordered source-intake classifier for all canonical lanes."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .git_optional import normalize_git_arm_mode, probe_git_arm
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY, resolve_lane_id, route_source

_PROJECT_MARKERS = {
    "cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "src/",
}

_MANIFEST_BASENAMES = {
    "manifest.json",
    "project_brain_package_manifest.json",
    "project_sector_registry.json",
}
_GENERATOR_VERSION_KEYS = (
    "generator_version",
    "builder_version",
    "application_version",
    "app_version",
)
_GENERATOR_BINARY_HASH_KEYS = (
    "generator_binary_sha256",
    "builder_binary_sha256",
    "executable_sha256",
)
_MAX_MANIFEST_BYTES = 1024 * 1024


def _first_text(mapping: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def _normalized_sha256(value: str | None) -> str | None:
    if value and re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value.upper()
    return None


def _archive_profile(path: Path) -> dict[str, Any]:
    """Describe archive structure without extracting or inferring its generator."""

    try:
        with zipfile.ZipFile(path) as archive:
            infos = [info for info in archive.infolist() if not info.is_dir()]
            members = [info.filename.replace("\\", "/") for info in infos]
            lowered = [name.lower() for name in members]
            sqlite_members = sorted(
                name
                for name in members
                if Path(name).suffix.lower() in {".db", ".sqlite", ".sqlite3"}
            )
            manifest_infos = [
                info
                for info in infos
                if Path(info.filename.replace("\\", "/")).name.lower()
                in _MANIFEST_BASENAMES
            ]
            declarations: list[dict[str, Any]] = []
            for info in sorted(manifest_infos, key=lambda item: item.filename):
                declaration: dict[str, Any] = {
                    "member": info.filename.replace("\\", "/"),
                    "bytes": info.file_size,
                }
                if info.file_size <= _MAX_MANIFEST_BYTES:
                    try:
                        decoded = json.loads(archive.read(info).decode("utf-8-sig"))
                    except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                        declaration["json_state"] = "UNREADABLE_OR_NON_JSON"
                    else:
                        declaration["json_state"] = "PARSED"
                        if isinstance(decoded, dict):
                            for key in ("schema", "package_type", "format_version"):
                                value = decoded.get(key)
                                if isinstance(value, (str, int, float)):
                                    declaration[key] = str(value)
                            version = _first_text(decoded, _GENERATOR_VERSION_KEYS)
                            binary_hash = _first_text(
                                decoded, _GENERATOR_BINARY_HASH_KEYS
                            )
                            if version:
                                declaration["declared_generator_version"] = version
                            normalized_binary_hash = _normalized_sha256(binary_hash)
                            if normalized_binary_hash:
                                declaration["declared_generator_binary_sha256"] = (
                                    normalized_binary_hash
                                )
                            elif binary_hash:
                                declaration["invalid_generator_binary_sha256"] = True
                else:
                    declaration["json_state"] = "SKIPPED_SIZE_LIMIT"
                declarations.append(declaration)
    except (OSError, zipfile.BadZipFile):
        return {
            "schema": "evidence-lane.archive-profile.v1",
            "status": "MISMATCH",
            "package_format": "UNREADABLE_OR_GENERIC_ZIP",
            "generator_identity_status": "UNPROVEN_BY_ARCHIVE",
            "generator_identity_reason": (
                "An unreadable archive cannot bind a generator version or binary."
            ),
        }

    package_types = sorted(
        {
            str(row["package_type"])
            for row in declarations
            if row.get("package_type")
        }
    )
    project_marker_matches = sorted(
        marker
        for marker in _PROJECT_MARKERS
        if any(
            (
                name.startswith(marker)
                or f"/{marker}" in name
                if marker.endswith("/")
                else name == marker or name.endswith("/" + marker)
            )
            for name in lowered
        )
    )
    has_v2_mini_brain = any(
        value == "EvidenceOS_V2_FULL_VERTICAL_project_mini_brain"
        for value in package_types
    ) and any(name.endswith("/sql/mini_brain_graph.sqlite") for name in lowered)
    has_uepc_sector_layout = any(
        name == "env/env_sqlite.sqlite" or name.endswith("/env/env_sqlite.sqlite")
        for name in lowered
    ) and any(
        name.startswith("project/sectors/") or "/project/sectors/" in name
        for name in lowered
    )
    if has_v2_mini_brain:
        package_format = "EVIDENCEOS_V2_FULL_VERTICAL_MINI_BRAIN"
    elif has_uepc_sector_layout:
        package_format = "UEPC_SECTOR_PACKAGE"
    elif sqlite_members:
        package_format = "GENERIC_SQLITE_BRAIN_ARCHIVE"
    else:
        package_format = "GENERIC_ARCHIVE"

    versions = sorted(
        {
            str(row["declared_generator_version"])
            for row in declarations
            if row.get("declared_generator_version")
        }
    )
    binary_hashes = sorted(
        {
            str(row["declared_generator_binary_sha256"])
            for row in declarations
            if row.get("declared_generator_binary_sha256")
        }
    )
    manifest_binds_generator = any(
        row.get("declared_generator_version")
        and row.get("declared_generator_binary_sha256")
        for row in declarations
    )
    if manifest_binds_generator:
        generator_status = "BINARY_BOUND_BY_MANIFEST"
        generator_reason = (
            "A parsed manifest declares both a generator version and binary SHA-256; "
            "the declaration still requires independent binary-hash verification."
        )
    elif versions or binary_hashes:
        generator_status = "PARTIALLY_DECLARED_UNBOUND"
        generator_reason = (
            "The archive declares only part of generator identity and does not bind "
            "one independently verifiable application binary."
        )
    else:
        generator_status = "UNPROVEN_BY_ARCHIVE"
        generator_reason = (
            "Package structure and internal format do not identify the desktop "
            "application binary that generated the archive."
        )
    return {
        "schema": "evidence-lane.archive-profile.v1",
        "status": "PASS",
        "member_count": len(infos),
        "uncompressed_bytes": sum(info.file_size for info in infos),
        "sqlite_member_count": len(sqlite_members),
        "sqlite_members": sqlite_members,
        "manifest_members": declarations,
        "project_marker_matches": project_marker_matches,
        "declared_package_types": package_types,
        "package_format": package_format,
        "declared_generator_versions": versions,
        "declared_generator_binary_sha256": binary_hashes,
        "generator_identity_status": generator_status,
        "generator_identity_reason": generator_reason,
        "filename_used_as_generator_evidence": False,
        "archive_bytes_mutated": False,
    }


def _archive_lane(profile: dict[str, Any]) -> tuple[str, str]:
    if profile["status"] != "PASS":
        return "custom", "zip_unreadable_or_generic"
    sqlite_members = profile.get("sqlite_members", [])
    if profile.get("project_marker_matches"):
        return "project_engulf", "archive_project_markers"
    if sqlite_members:
        return "brain_loader", "archive_sqlite_brain_members"
    return "custom", "archive_generic"


def _classify_one(
    source: str, *, code_mode: str, override: str | None, git_mode: str
) -> dict[str, Any]:
    exact = source.strip()
    path = Path(exact).expanduser()
    git_arm_receipt: dict[str, Any] | None = None
    archive_profile: dict[str, Any] | None = None
    if override:
        lane_id = resolve_lane_id(override, code_mode=code_mode)
        reason = "explicit_override"
    else:
        parsed = urlparse(exact)
        remote_host = (parsed.hostname or "").lower()
        remote_path = parsed.path or exact
        if parsed.scheme in {"ssh", "git"} or (
            parsed.scheme in {"http", "https"}
            and (
                remote_host in {"github.com", "gitlab.com", "bitbucket.org"}
                or remote_path.lower().endswith(".git")
            )
        ):
            lane_id = "github_code"
            reason = "git_remote_url"
        elif parsed.scheme in {"http", "https"}:
            lane_id = route_source(remote_path, code_mode=code_mode)
            reason = "remote_content_type_router"
        elif path.exists() and path.is_dir():
            git_arm_receipt = probe_git_arm(path, requested_mode=git_mode)
            lane_id = (
                code_mode
                if git_arm_receipt["history_index_enabled"]
                else "project_engulf"
            )
            reason = (
                "local_git_directory" if lane_id == code_mode else "project_directory"
            )
        elif path.exists() and path.is_file() and path.suffix.lower() == ".zip":
            archive_profile = _archive_profile(path)
            lane_id, reason = _archive_lane(archive_profile)
        else:
            lane_id = route_source(exact, code_mode=code_mode)
            reason = "canonical_path_and_content_type_router"
    exists = path.exists()
    if exists and path.is_file():
        identity = {
            "kind": "file",
            "path": str(path.resolve()),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
            "identity_scope": "FULL_FILE_BYTES",
            "content_bytes_hashed": True,
            "content_identity_proven": True,
        }
    elif exists and path.is_dir():
        members = sorted(
            (item.relative_to(path).as_posix(), item.stat().st_size)
            for item in path.rglob("*")
            if item.is_file() and ".git" not in item.relative_to(path).parts
        )
        member_paths = [name for name, _ in members]
        member_path_sizes = [
            {"path": name, "bytes": size} for name, size in members
        ]
        identity = {
            "kind": "directory",
            "path": str(path.resolve()),
            "member_count": len(members),
            "total_bytes": sum(size for _, size in members),
            "member_path_sha256": sha256_bytes(canonical_json_bytes(member_paths)),
            "member_path_size_sha256": sha256_bytes(
                canonical_json_bytes(member_path_sizes)
            ),
            "identity_scope": "MEMBER_PATHS_AND_SIZES_ONLY",
            "content_bytes_hashed": False,
            "content_identity_proven": False,
        }
    else:
        identity = {
            "kind": "remote_or_declared",
            "pointer": exact,
            "pointer_sha256": sha256_bytes(exact.encode("utf-8")),
            "identity_scope": "POINTER_TEXT_ONLY",
            "content_bytes_hashed": False,
            "content_identity_proven": False,
        }
    if git_arm_receipt is None:
        git_arm_receipt = (
            probe_git_arm(path, requested_mode=git_mode)
            if exists and path.is_dir()
            else {
                "schema": "evidence-lane.git-optional-arm.v1",
                "requested_mode": git_mode,
                "state": "NOT_APPLICABLE",
                "history_index_enabled": False,
                "fallback_content_index_enabled": True,
                "remote_write_authorized": False,
            }
        )
    receipt = {
        "source": exact,
        "source_identity": identity,
        "canonical_lane_id": lane_id,
        "display_label": LANE_REGISTRY[lane_id].display_label,
        "classification_reason": reason,
        "explicit_override": bool(override),
        "git_optional_arm": git_arm_receipt,
    }
    if archive_profile is not None:
        receipt["archive_profile"] = archive_profile
    return receipt


def classify_source_intake(
    sources: list[str],
    *,
    code_mode: str,
    overrides: dict[str, str] | None = None,
    git_mode: str = "AUTO",
) -> dict[str, Any]:
    """Classify ordered inputs without copying, parsing, or mutating source bytes."""

    if code_mode not in {"github_code", "local_code"}:
        raise ValueError("code_mode must be github_code or local_code")
    normalized_git_mode = normalize_git_arm_mode(git_mode)
    exact_sources = [str(source).strip() for source in sources if str(source).strip()]
    if not exact_sources:
        raise ValueError("At least one non-empty source is required.")
    exact_overrides = overrides or {}
    unknown_overrides = sorted(set(exact_overrides) - set(exact_sources))
    if unknown_overrides:
        raise ValueError(
            "Every explicit source override must name one exact supplied source: "
            + ", ".join(unknown_overrides)
        )
    receipts = [
        _classify_one(
            source,
            code_mode=code_mode,
            override=exact_overrides.get(source),
            git_mode=normalized_git_mode,
        )
        for source in exact_sources
    ]
    ordered_lanes = ["chat_lineage"]
    for receipt in receipts:
        lane_id = str(receipt["canonical_lane_id"])
        if lane_id not in ordered_lanes:
            ordered_lanes.append(lane_id)
    return {
        "status": "PASS",
        "schema": "evidence-lane.source-intake-classification.v1",
        "sources": receipts,
        "source_count": len(receipts),
        "ordered_canonical_lanes": ordered_lanes,
        "chat_lineage_included": True,
        "all_canonical_lanes_supported": list(CANONICAL_LANE_IDS),
        "project_engulf_supported": True,
        "auto_detection": True,
        "explicit_overrides": bool(exact_overrides),
        "git_optional_arm": {
            "requested_mode": normalized_git_mode,
            "source_receipts": [row["git_optional_arm"] for row in receipts],
            "remote_write_authorized": False,
        },
        "source_bytes_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
