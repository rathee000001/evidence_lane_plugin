"""One generalized, ordered source-intake classifier for all canonical lanes."""

from __future__ import annotations

import json
import os
import re
import subprocess  # nosec B404 - argv-only bounded Git enumeration
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .bounded_io import IOBudget, bounded_file_identity
from .errors import EvidenceLaneError, require
from .git_optional import normalize_git_arm_mode, probe_git_arm
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import CANONICAL_LANE_IDS, LANE_REGISTRY, resolve_lane_id, route_source
from .source_authority import (
    SourceAuthoritySpec,
    archive_safety_profile,
    register_source_batch,
)
from .source_policy import path_exclusion_reason

_PROJECT_MARKERS = {
    "cargo.toml",
    "go.mod",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "src/",
}

_CODE_PROJECT_ROLES = {
    "PRIMARY_PROJECT_CODE",
    "LANE_SCOPED_STUDY_BRAIN",
}
_REPOSITORY_ACCESS_CLASSES = {
    "REGISTERED_PROJECT_AUTHORITY",
    "OWNED_OR_EXPLICITLY_AUTHORIZED",
    "PUBLIC_READ_ONLY_UNOWNED",
    "LOCAL_READ_ONLY_UNVERIFIED",
}
_LANE_STUDY_BRAIN_ARTIFACTS = [
    "LANE_SQLITE_FTS5",
    "LANE_MMD",
    "LANE_DOT",
    "TOOLS_JSON",
    "LANE_POINTER_JSON",
    "LANE_MANIFEST_JSON",
    "STUDY_BRAIN_JSON",
]

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
_CODE_CONTEXT_PARTS = {
    ".codex-plugin",
    ".github",
    "commands",
    "config",
    "configs",
    "hooks",
    "plugins",
    "runtime",
    "scripts",
    "skills",
    "src",
    "tests",
}
_CODE_MANIFEST_NAMES = {
    "cargo.lock",
    "cargo.toml",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "plugin.json",
    "pnpm-lock.yaml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
_CODE_CONFIG_SUFFIXES = {".json", ".lock", ".toml", ".yaml", ".yml"}
_LOCAL_HISTORY_PREFIXES = ("evidence/",)
_MAX_DIRECTORY_DEPTH = 64
_MAX_DIRECTORY_SECONDS = 15.0
_MAX_DIRECTORY_COUNT = 25_000


def _is_authoritative_code_config_path(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts[:-1]}
    name = path.name.casefold()
    return name in _CODE_MANIFEST_NAMES or bool(
        parts & _CODE_CONTEXT_PARTS and path.suffix.casefold() in _CODE_CONFIG_SUFFIXES
    )


def _classification_exclusion_reason(relative: str) -> str | None:
    normalized = relative.replace("\\", "/").lstrip("./")
    if normalized.casefold().startswith(_LOCAL_HISTORY_PREFIXES):
        return "LOCAL_HISTORY_PATH_EXCLUDED"
    return path_exclusion_reason(normalized)


def _git_directory_candidates(path: Path) -> list[str] | None:
    """Return tracked plus safe untracked names without traversing ignored trees."""

    command = [
        "git",
        "-C",
        str(path),
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        ".",
    ]
    try:
        completed = subprocess.run(  # nosec B603
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=15,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if os.name == "nt"
                else 0
            ),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return sorted(
        {
            raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
            for raw in completed.stdout.split(b"\0")
            if raw
        }
    )


def _bounded_directory_members(
    path: Path,
) -> tuple[list[tuple[str, int]], dict[str, Any]]:
    budget = IOBudget()
    members: list[tuple[str, int]] = []
    excluded_counts: dict[str, int] = {}
    git_candidates = _git_directory_candidates(path)
    if git_candidates is not None:
        candidates = [
            (relative, path / Path(relative)) for relative in git_candidates
        ]
        selection = "GIT_INDEX_AND_SAFE_UNTRACKED"
    else:
        selection = "BOUNDED_FILESYSTEM_FALLBACK"
        started = time.monotonic()
        pending: list[tuple[Path, int]] = [(path, 0)]
        enumerated: list[tuple[str, Path]] = []
        directory_count = 0
        while pending:
            require(
                time.monotonic() - started <= _MAX_DIRECTORY_SECONDS,
                "SOURCE_DIRECTORY_TIME_BUDGET_EXCEEDED",
                "Directory classification exceeded its bounded enumeration time.",
                status="BLOCKED",
                max_seconds=_MAX_DIRECTORY_SECONDS,
            )
            directory, depth = pending.pop()
            directory_count += 1
            require(
                directory_count <= _MAX_DIRECTORY_COUNT,
                "SOURCE_DIRECTORY_COUNT_BUDGET_EXCEEDED",
                "Directory classification exceeded its bounded directory count.",
                status="BLOCKED",
                max_directory_count=_MAX_DIRECTORY_COUNT,
            )
            for item in directory.iterdir():
                relative = item.relative_to(path).as_posix()
                reason = _classification_exclusion_reason(relative)
                if reason is not None:
                    excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
                    continue
                if item.is_symlink():
                    excluded_counts["SYMLINK_PATH_EXCLUDED"] = (
                        excluded_counts.get("SYMLINK_PATH_EXCLUDED", 0) + 1
                    )
                    continue
                if item.is_dir():
                    require(
                        depth + 1 <= _MAX_DIRECTORY_DEPTH,
                        "SOURCE_DIRECTORY_DEPTH_BUDGET_EXCEEDED",
                        "Directory classification exceeded its bounded depth.",
                        status="BLOCKED",
                        max_depth=_MAX_DIRECTORY_DEPTH,
                    )
                    pending.append((item, depth + 1))
                elif item.is_file():
                    enumerated.append((relative, item))
        candidates = sorted(enumerated)

    for relative, item in candidates:
        reason = _classification_exclusion_reason(relative)
        if reason is not None:
            excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
            continue
        try:
            lexical = Path(os.path.abspath(item))
            lexical.relative_to(path.resolve())
        except (OSError, ValueError):
            excluded_counts["PATH_ESCAPE_EXCLUDED"] = (
                excluded_counts.get("PATH_ESCAPE_EXCLUDED", 0) + 1
            )
            continue
        if item.is_symlink() or not item.is_file():
            excluded_counts["NON_REGULAR_PATH_EXCLUDED"] = (
                excluded_counts.get("NON_REGULAR_PATH_EXCLUDED", 0) + 1
            )
            continue
        try:
            size = item.stat().st_size
        except OSError as exc:
            raise EvidenceLaneError(
                "SOURCE_DIRECTORY_MEMBER_UNREADABLE",
                "A governed directory member could not be inspected.",
                status="MISMATCH",
            ) from exc
        budget.reserve(size_bytes=size)
        members.append((relative, size))
    members.sort()
    return members, {
        **budget.receipt(),
        "source_selection": selection,
        "excluded_member_count": sum(excluded_counts.values()),
        "excluded_class_counts": dict(sorted(excluded_counts.items())),
        "raw_excluded_paths_returned": False,
        "ignored_paths_traversed": False if git_candidates is not None else None,
        "local_history_content_read": False,
    }


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

    safety = archive_safety_profile(path)
    if safety["status"] != "PASS":
        return {
            "schema": "evidence-lane.archive-profile.v1",
            "status": safety["status"],
            "package_format": "UNSAFE_OR_UNREADABLE_ZIP",
            "generator_identity_status": "UNPROVEN_BY_ARCHIVE",
            "generator_identity_reason": (
                "An unsafe or unreadable archive cannot bind a reusable package "
                "or generator identity."
            ),
            "archive_safety": safety,
            "archive_bytes_mutated": False,
        }
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
        "archive_safety": safety,
        "reuse_eligible": True,
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


def _is_code_project_directory(path: Path) -> bool:
    return any(
        (path / marker.removesuffix("/")).exists() for marker in _PROJECT_MARKERS
    )


def _same_resolved_path(left: str, right: str | Path | None) -> bool:
    if right is None:
        return False
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except (OSError, RuntimeError):
        return False


def _apply_code_source_roles(
    receipts: list[dict[str, Any]],
    assertions: dict[str, dict[str, Any]],
    *,
    registered_repository_path: str | Path | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    effective_assertions = {
        source: dict(value) for source, value in assertions.items()
    }
    code_rows: list[dict[str, Any]] = []
    primary_count = 0
    for receipt in receipts:
        source = str(receipt["source"])
        lane_id = str(receipt["canonical_lane_id"])
        parsed = urlparse(source)
        is_remote_git = lane_id == "github_code" or parsed.scheme in {
            "git",
            "ssh",
        }
        is_local_code = lane_id == "local_code"
        if not (is_remote_git or is_local_code):
            continue
        assertion = effective_assertions.setdefault(source, {})
        registered = _same_resolved_path(source, registered_repository_path)
        requested_role = str(assertion.get("code_project_role") or "").upper()
        require(
            not requested_role or requested_role in _CODE_PROJECT_ROLES,
            "SOURCE_INTAKE_CODE_PROJECT_ROLE_INVALID",
            "A code source role must be PRIMARY_PROJECT_CODE or "
            "LANE_SCOPED_STUDY_BRAIN.",
            status="BLOCKED",
        )
        role = requested_role or (
            "PRIMARY_PROJECT_CODE" if registered else "LANE_SCOPED_STUDY_BRAIN"
        )
        if role == "PRIMARY_PROJECT_CODE":
            require(
                registered,
                "SOURCE_INTAKE_PRIMARY_CODE_CHANGE_REQUIRES_NEW_PROJECT_PV",
                "Changing the central code project requires a separately registered "
                "project/PV root; Source Intake cannot replace it in place.",
                status="BLOCKED",
                source_identity_sha256=sha256_bytes(source.encode()),
            )
            primary_count += 1
        requested_access = str(assertion.get("repository_access") or "").upper()
        require(
            not requested_access
            or requested_access in _REPOSITORY_ACCESS_CLASSES,
            "SOURCE_INTAKE_REPOSITORY_ACCESS_INVALID",
            "Repository access must use one current governed access class.",
            status="BLOCKED",
        )
        if requested_access:
            access = requested_access
        elif registered:
            access = "REGISTERED_PROJECT_AUTHORITY"
        elif parsed.scheme in {"http", "https", "git", "ssh"}:
            access = "PUBLIC_READ_ONLY_UNOWNED"
        else:
            access = "OWNED_OR_EXPLICITLY_AUTHORIZED"
        access_allows_history = access in {
            "REGISTERED_PROJECT_AUTHORITY",
            "OWNED_OR_EXPLICITLY_AUTHORIZED",
        }
        history_authorized = (
            access_allows_history
            and assertion.get("git_history_authorized", access_allows_history) is True
        )
        git_arm = dict(receipt.get("git_optional_arm") or {})
        require(
            not (
                git_arm.get("requested_mode") == "REQUIRED"
                and not history_authorized
            ),
            "SOURCE_INTAKE_GIT_HISTORY_AUTHORIZATION_REQUIRED",
            "Required Git history needs registered ownership or explicit access.",
            status="BLOCKED",
        )
        if git_arm.get("history_index_enabled") is True and not history_authorized:
            git_arm.update(
                {
                    "state": "UNAUTHORIZED_HISTORY_DISABLED",
                    "history_index_enabled": False,
                    "reason": (
                        "Code remains readable as lane-scoped evidence, but Git "
                        "history is disabled without ownership/authorization."
                    ),
                }
            )
        assertion.update(
            {
                "code_project_role": role,
                "repository_access": access,
                "git_history_authorized": history_authorized,
            }
        )
        receipt["git_optional_arm"] = git_arm
        receipt["code_source_routing"] = {
            "schema": "evidence-lane.code-source-routing.v1",
            "role": role,
            "repository_access": access,
            "git_history_authorized": history_authorized,
            "github_code_materialization_authorized": bool(
                history_authorized
                and assertion.get("governed_git_checkpoint") is True
            ),
            "lane_scoped_study_brain": role == "LANE_SCOPED_STUDY_BRAIN",
            "lane_owned_artifacts": list(_LANE_STUDY_BRAIN_ARTIFACTS),
            "central_project_replacement_allowed": False,
            "new_project_pv_required_for_central_change": True,
        }
        code_rows.append(
            {
                "source_identity_sha256": sha256_bytes(source.encode()),
                "lane_id": lane_id,
                **receipt["code_source_routing"],
            }
        )
    require(
        primary_count <= 1,
        "SOURCE_INTAKE_MULTIPLE_PRIMARY_CODE_PROJECTS",
        "One governed project can have only one central code project.",
        status="BLOCKED",
    )
    contract = {
        "schema": "evidence-lane.code-source-routing-batch.v1",
        "status": "PASS",
        "central_code_project_count": primary_count,
        "code_source_count": len(code_rows),
        "study_brain_source_count": sum(
            row["role"] == "LANE_SCOPED_STUDY_BRAIN" for row in code_rows
        ),
        "local_code_and_github_code_distinct": True,
        "local_code_refresh_source": "CURRENT_DELTA_DIRTY_BYTES",
        "github_code_refresh_source": "EXACT_GOVERNED_GIT_CHECKPOINT_ONLY",
        "unowned_public_repository_history_allowed": False,
        "central_project_change_requires_new_project_pv": True,
        "source_intake_materializes_lanes": False,
        "initial_build_materializes_lane_artifacts": True,
        "delta_refresh_updates_changed_lane_artifacts": True,
        "hil_refresh_adds_project_overlay": True,
        "ordinary_delta_refresh_adds_project_overlay": False,
        "code_sources": code_rows,
    }
    return receipts, effective_assertions, contract


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
                if git_arm_receipt["repository_is_git"]
                or _is_code_project_directory(path)
                else "project_engulf"
            )
            reason = (
                "local_git_directory" if lane_id == code_mode else "project_directory"
            )
        elif path.exists() and path.is_file() and _is_authoritative_code_config_path(path):
            lane_id = code_mode
            reason = "authoritative_code_config_path_context"
        elif path.exists() and path.is_file() and path.suffix.lower() == ".zip":
            archive_profile = _archive_profile(path)
            lane_id, reason = _archive_lane(archive_profile)
        else:
            lane_id = route_source(exact, code_mode=code_mode)
            reason = "canonical_path_and_content_type_router"
    exists = path.exists()
    if exists and path.is_file():
        file_budget = IOBudget()
        bounded_identity = bounded_file_identity(
            path,
            budget=file_budget,
            root=path.parent,
        )
        identity = {
            "kind": "file",
            "path": str(path.resolve()),
            "bytes": bounded_identity["size_bytes"],
            "sha256": bounded_identity["sha256"],
            "identity_scope": "FULL_FILE_BYTES",
            "content_bytes_hashed": True,
            "content_identity_proven": True,
            "bounded_io": file_budget.receipt(),
            "descriptor_identity_stable": bounded_identity[
                "descriptor_identity_stable"
            ],
            "symlink_followed": bounded_identity["symlink_followed"],
        }
    elif exists and path.is_dir():
        members, directory_budget = _bounded_directory_members(path)
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
            "bounded_io": directory_budget,
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
    authority_mode: str = "CLASSIFICATION_ONLY",
    authority_registry_path: str | Path | None = None,
    source_assertions: dict[str, dict[str, Any]] | None = None,
    registered_repository_path: str | Path | None = None,
) -> dict[str, Any]:
    """Classify ordered inputs and optionally register read-only byte authority."""

    require(
        code_mode in {"github_code", "local_code"},
        "SOURCE_INTAKE_CODE_MODE_INVALID",
        "Source Intake code mode must be github_code or local_code.",
        status="BLOCKED",
    )
    normalized_authority_mode = authority_mode.strip().upper()
    require(
        normalized_authority_mode
        in {"CLASSIFICATION_ONLY", "GOVERNED_CONTENT_REGISTRY"},
        "SOURCE_INTAKE_AUTHORITY_MODE_INVALID",
        "Source Intake authority mode must be CLASSIFICATION_ONLY or "
        "GOVERNED_CONTENT_REGISTRY.",
        status="BLOCKED",
    )
    try:
        normalized_git_mode = normalize_git_arm_mode(git_mode)
    except ValueError as exc:
        raise EvidenceLaneError(
            code="SOURCE_INTAKE_GIT_MODE_INVALID",
            message="Source Intake Git mode must be AUTO, REQUIRED, or DISABLED.",
            status="BLOCKED",
        ) from exc
    exact_sources = [str(source).strip() for source in sources if str(source).strip()]
    require(
        bool(exact_sources),
        "SOURCE_INTAKE_SOURCE_REQUIRED",
        "Source Intake requires at least one non-empty source.",
        status="BLOCKED",
    )
    exact_overrides = overrides or {}
    unknown_overrides = sorted(set(exact_overrides) - set(exact_sources))
    if unknown_overrides:
        raise EvidenceLaneError(
            code="SOURCE_INTAKE_OVERRIDE_SOURCE_MISMATCH",
            message="Every source override must name one exact supplied source.",
            status="MISMATCH",
            details={"unknown_source_count": len(unknown_overrides)},
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
    assertions = source_assertions or {}
    unknown_assertions = sorted(set(assertions) - set(exact_sources))
    if unknown_assertions:
        raise EvidenceLaneError(
            code="SOURCE_INTAKE_ASSERTION_SOURCE_MISMATCH",
            message="Every source assertion must name one exact supplied source.",
            status="MISMATCH",
            details={"unknown_source_count": len(unknown_assertions)},
        )
    receipts, assertions, code_source_routing = _apply_code_source_roles(
        receipts,
        assertions,
        registered_repository_path=registered_repository_path,
    )
    authority: dict[str, Any]
    if normalized_authority_mode == "GOVERNED_CONTENT_REGISTRY":
        if authority_registry_path is None:
            raise EvidenceLaneError(
                code="SOURCE_INTAKE_AUTHORITY_REGISTRY_REQUIRED",
                message=(
                    "GOVERNED_CONTENT_REGISTRY requires its project-scoped registry."
                ),
                status="BLOCKED",
            )
        authority = register_source_batch(
            authority_registry_path,
            [
                SourceAuthoritySpec(
                    source=str(receipt["source"]),
                    ordinal=index,
                    lane_id=str(receipt["canonical_lane_id"]),
                    assertions=assertions.get(str(receipt["source"]), {}),
                )
                for index, receipt in enumerate(receipts, start=1)
            ],
        )
    else:
        authority = {
            "status": "NOT_REQUESTED",
            "authority_mode": "CLASSIFICATION_ONLY",
            "registry_mutated": False,
        }
    ordered_lanes = ["chat_lineage"]
    for receipt in receipts:
        lane_id = str(receipt["canonical_lane_id"])
        if lane_id not in ordered_lanes:
            ordered_lanes.append(lane_id)
    return {
        "status": "PASS",
        "schema": "evidence-lane.source-intake-classification.v2",
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
        "authority_mode": normalized_authority_mode,
        "source_authority": authority,
        "source_assertion_count": sum(len(row) for row in assertions.values()),
        "code_source_routing": code_source_routing,
        "source_bytes_mutated": False,
        "source_payloads_copied": False,
        "local_registry_mutated": normalized_authority_mode
        == "GOVERNED_CONTENT_REGISTRY",
        "candidate_created": False,
        "pointer_moved": False,
    }
