"""One generalized, ordered source-intake classifier for all canonical lanes."""

from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import Field, JsonValue, model_validator

from .bounded_io import IOBudget, bounded_existing_path, bounded_file_identity, run_bounded_process
from .errors import EvidenceLaneError, LaneError, require
from .git_optional import normalize_git_arm_mode, probe_git_arm
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import (
    SECTOR_LANE_IDS,
    LaneRegistryError,
    get_lane,
    lane_artifact_contract,
    resolve_lane_id,
    route_source,
)
from .registry import ActionSpec, Contract
from .source_authority import (
    SourceAuthoritySpec,
    _policy_reason,
    archive_safety_profile,
    register_source_batch,
)
from .source_policy import normalize_source_path, path_exclusion_reason
from .storage import LaneStore, ProjectStore, project_snapshot

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
_MAX_DIRECTORY_ENTRIES = 100_000


def _is_authoritative_code_config_path(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts[:-1]}
    name = path.name.casefold()
    return name in _CODE_MANIFEST_NAMES or bool(
        parts & _CODE_CONTEXT_PARTS and path.suffix.casefold() in _CODE_CONFIG_SUFFIXES
    )


def _classification_exclusion_reason(relative: str) -> str | None:
    normalized = normalize_source_path(relative)
    if normalized.casefold().startswith(_LOCAL_HISTORY_PREFIXES):
        return "LOCAL_HISTORY_PATH_EXCLUDED"
    state, reason = _policy_reason(normalized, {})
    return path_exclusion_reason(normalized) or (reason if state == 'EXCLUDED' else None)


def _git_directory_candidates(path: Path, *, member_scopes: list[Path] | None = None) -> list[str] | None:
    """Return tracked plus safe untracked names without traversing ignored trees."""

    from .git_optional import _exact_worktree_root
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
        *(['.'] if member_scopes is None else [':(literal)' + item.relative_to(path).as_posix() for item in member_scopes]),
    ]
    try:
        if not _exact_worktree_root('git', path):
            return None
        completed = run_bounded_process(command, cwd=path, timeout_seconds=15,
            max_stdout_bytes=8_388_608, max_stderr_bytes=65_536)
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    if completed.stdout.count(b'\0') > _MAX_DIRECTORY_ENTRIES:
        raise LaneError('SOURCE_DIRECTORY_ENTRY_BUDGET', 'The selected Git member listing exceeds its entry bound.')
    return sorted(
        {
            raw.decode("utf-8", errors="surrogateescape").replace("\\", "/")
            for raw in completed.stdout.split(b"\0")
            if raw
        }
    )


def _bounded_directory_members(
    path: Path, *, required_selection: str | None = None, capture=None, member_scopes: list[Path] | None = None,
) -> tuple[list[tuple[str, int]], dict[str, Any]]:
    budget = IOBudget()
    members: list[tuple[str, int]] = []
    excluded_counts: dict[str, int] = {}
    if member_scopes is not None and (not member_scopes or any(not item.is_relative_to(path) for item in member_scopes)):
        raise LaneError('SOURCE_SELECTION_SCOPE', 'Select existing file or directory scopes inside this source root.')
    git_candidates = (_git_directory_candidates(path) if member_scopes is None
        else _git_directory_candidates(path, member_scopes=member_scopes))
    selection = 'GIT_INDEX_AND_SAFE_UNTRACKED' if git_candidates is not None else 'BOUNDED_FILESYSTEM_FALLBACK'
    if required_selection is not None and selection != required_selection:
        raise LaneError('SOURCE_SELECTION_CHANGED', 'The registered directory selection method is unavailable or changed; register a new source observation.')
    if git_candidates is not None:
        candidates = [
            (relative, path / Path(relative)) for relative in git_candidates
        ]
        selection = "GIT_INDEX_AND_SAFE_UNTRACKED"
    else:
        selection = "BOUNDED_FILESYSTEM_FALLBACK"
        started = time.monotonic()
        pending: list[tuple[Path, int]] = []
        enumerated: list[tuple[str, Path]] = []
        for scope in member_scopes or [path]:
            if scope.is_file():
                enumerated.append((scope.relative_to(path).as_posix(), scope))
            elif scope.is_dir():
                pending.append((scope, 0))
        directory_count, entry_count = 0, 0
        while pending:
            if capture:
                capture.boundary(path)
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
                entry_count += 1
                if entry_count > _MAX_DIRECTORY_ENTRIES or time.monotonic() - started > _MAX_DIRECTORY_SECONDS:
                    raise LaneError('SOURCE_DIRECTORY_ENTRY_BUDGET', 'The selected directory listing exceeds its entry or elapsed-time bound.')
                if capture:
                    capture.entry(item)
                relative = item.relative_to(path).as_posix()
                reason = _classification_exclusion_reason(relative)
                if reason is not None:
                    excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
                    continue
                if item.is_symlink() or (
                    hasattr(item, "is_junction") and item.is_junction()
                ):
                    excluded_counts["REPARSE_POINT_PATH_EXCLUDED"] = (
                        excluded_counts.get("REPARSE_POINT_PATH_EXCLUDED", 0) + 1
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
        candidates = sorted(set(enumerated))

    for relative, item in candidates:
        if capture:
            capture.entry(item)
        reason = _classification_exclusion_reason(relative)
        if reason is not None:
            excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
            continue
        try:
            item = bounded_existing_path(item, root=path)
        except (EvidenceLaneError, OSError, ValueError):
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


def _archive_lane(profile: dict[str, Any], code_mode: str) -> tuple[str, str]:
    if profile["status"] != "PASS":
        return "custom", "zip_unreadable_or_generic"
    sqlite_members = profile.get("sqlite_members", [])
    if profile.get("project_marker_matches"):
        return code_mode, "archive_project_markers"
    if sqlite_members:
        return "custom", "archive_selected_sqlite_members"
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
                "SOURCE_INTAKE_PRIMARY_CODE_CHANGE_REQUIRES_NEW_PROJECT",
                "Changing the central code project requires a separately registered "
                "project root; Source Intake cannot replace it in place.",
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
            access = "LOCAL_READ_ONLY_UNVERIFIED"
        require(
            access != "REGISTERED_PROJECT_AUTHORITY" or registered,
            "SOURCE_INTAKE_REGISTERED_AUTHORITY_MISMATCH",
            "A source assertion cannot make another path the registered project.",
            status="BLOCKED",
        )
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
            "schema": "evidence-lane.code-source-routing.v4",
            "role": role,
            "repository_access": access,
            "git_history_authorized": history_authorized,
            "github_code_materialization_authorized": bool(
                history_authorized
                and assertion.get("governed_git_checkpoint") is True
            ),
            "lane_scoped_study_brain": role == "LANE_SCOPED_STUDY_BRAIN",
            "lane_artifact_contract": lane_artifact_contract(lane_id),
            "central_project_replacement_allowed": False,
            "new_project_required_for_central_change": True,
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
        "schema": "evidence-lane.code-source-routing-batch.v4",
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
        "central_project_change_requires_new_project": True,
        "source_intake_materializes_lanes": False,
        "lane_materialization": "separate_registered_lane_operations",
        "artifact_selection": "owning_lane_consumer_contract",
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
        try:
            lane_id = resolve_lane_id(override, code_mode=code_mode)
        except LaneRegistryError as exc:
            raise EvidenceLaneError("SOURCE_INTAKE_SECTOR_REQUIRED",
                "Select a retained sector lane for a source override.", status="BLOCKED") from exc
        require(get_lane(lane_id).kind == "sector", "SOURCE_INTAKE_SECTOR_REQUIRED",
                "Source overrides select sectors; authorities have separate workflows.", status="BLOCKED")
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
                else "custom"
            )
            reason = (
                "local_git_directory" if lane_id == code_mode else "project_directory"
            )
        elif path.exists() and path.is_file() and _is_authoritative_code_config_path(path):
            lane_id = code_mode
            reason = "authoritative_code_config_path_context"
        elif path.exists() and path.is_file() and path.suffix.lower() == ".zip":
            archive_profile = _archive_profile(path)
            lane_id, reason = _archive_lane(archive_profile, code_mode)
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
        "display_label": get_lane(lane_id).display_label,
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
    authority_registry_path: ProjectStore | LaneStore | None = None,
    source_assertions: dict[str, dict[str, Any]] | None = None,
    registered_repository_path: str | Path | None = None,
    writer=None,
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
    require(len(exact_sources) <= 256 and len(set(exact_sources)) == len(exact_sources),
            "SOURCE_INTAKE_SOURCE_BUDGET", "Select at most 256 distinct ordered sources.", status="BLOCKED")
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
                    directory_selection=receipt['source_identity'].get('bounded_io', {}).get('source_selection'),
                    expected_directory_path_size_sha256=(receipt['source_identity'].get('member_path_size_sha256')
                        if receipt['source_identity']['kind'] == 'directory' else None),
                )
                for index, receipt in enumerate(receipts, start=1)
            ],
            writer=writer,
        )
    else:
        authority = {
            "status": "NOT_REQUESTED",
            "authority_mode": "CLASSIFICATION_ONLY",
            "registry_mutated": False,
        }
    ordered_lanes = []
    for receipt in receipts:
        lane_id = str(receipt["canonical_lane_id"])
        if lane_id not in ordered_lanes:
            ordered_lanes.append(lane_id)
    return {
        "status": "PASS",
        "schema": "evidence-lane.source-intake-classification.v4",
        "sources": receipts,
        "source_count": len(receipts),
        "ordered_canonical_lanes": ordered_lanes,
        "chat_lineage_included": False,
        "chat_lineage_capture": "separate_host_event_workflow",
        "all_canonical_lanes_supported": list(SECTOR_LANE_IDS),
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


SourceText = Annotated[str, Field(min_length=1, max_length=4096)]
SourceKey = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")]


class SourceRequest(Contract):
    @model_validator(mode="after")
    def bounded_request(self):
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > 131_072:
            raise ValueError("Source action arguments exceed 128 KiB")
        return self


class SourceIntakeRequest(SourceRequest):
    sources: list[SourceText] = Field(min_length=1, max_length=256)
    code_mode: Literal["local_code", "github_code"] = "local_code"
    overrides: dict[str, str] = Field(default_factory=dict, max_length=256)
    git_mode: Literal["AUTO", "REQUIRED", "DISABLED"] = "AUTO"
    source_assertions: dict[str, dict[str, JsonValue]] = Field(default_factory=dict, max_length=256)
    parent_route_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


class SourceRouteConfigure(SourceIntakeRequest):
    overrides: dict[str, str] = Field(min_length=1, max_length=100)


class SourceBatchRequest(SourceRequest):
    batch_id: SourceKey


class SourceSQLiteRequest(SourceBatchRequest):
    max_embedded_member_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=768 * 1024 * 1024)
    exact_count_max_database_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=32 * 1024 * 1024)
    max_assets: int = Field(default=512, ge=1, le=512, strict=True)
    max_total_input_bytes: int = Field(default=256 * 1024 * 1024, ge=1, le=4 * 1024**3, strict=True)
    max_metadata_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024, strict=True)
    max_batch_vm_steps: int = Field(default=40_000_000, ge=1000, le=200_000_000, strict=True)
    timeout_seconds: float = Field(default=15, gt=0, le=60, allow_inf_nan=False, strict=True)


class SourceIdentityRequest(SourceBatchRequest):
    entities: list[dict[str, JsonValue]] = Field(max_length=256)
    profiles: list[dict[str, JsonValue]] = Field(max_length=256)
    relations: list[dict[str, JsonValue]] = Field(max_length=1024)


class SourceCrosswalkRequest(SourceBatchRequest):
    crosswalk_path: SourceText


class SourceGraphRequest(SourceBatchRequest):
    occurrence_ordinals: list[Annotated[int, Field(ge=1, le=256)]] | None = Field(default=None, max_length=256)
    member_path_prefixes: list[SourceText] | None = Field(default=None, max_length=256)
    max_files: int = Field(default=2000, ge=1, le=25_000)
    max_total_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_file_bytes: int = Field(default=4 * 1024 * 1024, ge=1, le=8 * 1024 * 1024)
    max_nodes: int = Field(default=20_000, ge=1, le=500_000)
    max_edges: int = Field(default=40_000, ge=1, le=1_000_000)


class SourceGraphDiffRequest(SourceRequest):
    from_graph_id: SourceKey
    to_graph_id: SourceKey
    sample_limit: int = Field(default=100, ge=1, le=1000)


class SourceGraphImpactRequest(SourceRequest):
    graph_id: SourceKey
    seed_node_ids: list[SourceKey] = Field(min_length=1, max_length=256)
    relations: list[SourceKey] | None = Field(default=None, max_length=64)
    direction: Literal["UPSTREAM", "DOWNSTREAM", "BOTH"] = "UPSTREAM"
    max_depth: int = Field(default=3, ge=1, le=10)
    max_nodes: int = Field(default=1000, ge=1, le=10_000)


class SourceGitRequest(SourceBatchRequest):
    occurrence_ordinal: int = Field(ge=1, le=256)
    max_refs: int = Field(default=1000, ge=1, le=20_000)
    max_commits: int = Field(default=1000, ge=1, le=100_000)
    max_objects: int = Field(default=20_000, ge=1, le=2_000_000)
    max_tree_entries: int = Field(default=50_000, ge=1, le=5_000_000)
    max_file_changes: int = Field(default=20_000, ge=1, le=2_000_000)
    max_hunks: int = Field(default=20_000, ge=1, le=2_000_000)
    max_changed_lines: int = Field(default=50_000, ge=1, le=5_000_000)
    max_patch_bytes: int = Field(default=32 * 1024 * 1024, ge=1, le=2 * 1024 * 1024 * 1024)
    max_single_object_bytes: int = Field(default=8 * 1024 * 1024, ge=1, le=1024 * 1024 * 1024)
    max_total_object_bytes: int = Field(default=64 * 1024 * 1024, ge=1, le=8 * 1024 * 1024 * 1024)


class SourceGitImpactRequest(SourceRequest):
    snapshot_id: SourceKey
    graph_id: SourceKey
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    parent_ordinal: int = Field(default=0, ge=0, le=64)
    relations: list[SourceKey] | None = Field(default=None, max_length=64)
    direction: Literal["UPSTREAM", "DOWNSTREAM", "BOTH"] = "UPSTREAM"
    max_depth: int = Field(default=3, ge=1, le=10)
    max_nodes: int = Field(default=1000, ge=1, le=10_000)


class SourceSchemaRequest(SourceBatchRequest):
    definition: dict[str, JsonValue]


class SourceSchemaConfigureRequest(SourceSchemaRequest):
    operation: Literal["ADD", "MODIFY"]
    pill_name: str = Field(min_length=1, max_length=80)
    expected_previous_schema_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class SourceReadRequest(SourceRequest):
    collection: Literal["batches", "occurrences", "relations", "provenance", "graphs", "schemas", "identities",
        "sqlite_assets", "sqlite_receipts", "sqlite_schema", "sqlite_tables", "sqlite_foreign_keys"] = "batches"
    batch_id: SourceKey | None = None
    inspection_id: SourceKey | None = None
    offset: int = Field(default=0, ge=0, le=1_000_000)
    limit: int = Field(default=20, ge=1, le=200)
    max_bytes: int = Field(default=65_536, ge=1024, le=262_144)


class SourceOperationResult(Contract):
    project_id: str
    lane_id: Literal["sources"] = "sources"
    operation: str
    result: dict[str, JsonValue]
    source_bytes_mutated: Literal[False] = False
    metadata_provenance: Literal["source_measurements_and_attributed_assertions"] = "source_measurements_and_attributed_assertions"


def _source_action_result(store, action, result):
    value = SourceOperationResult(project_id=store.project_id, operation=action, result=result)
    if len(canonical_json_bytes(value.model_dump(mode="json"))) > 2 * 1024 * 1024:
        raise LaneError("SOURCE_RESULT_BUDGET", "Select a smaller source batch or analysis budget.")
    return value


def _authorize_source_paths(store, context, values):
    from .projects import ProjectAccess
    for value in values:
        parsed = urlparse(value)
        if parsed.scheme.lower() in {"http", "https", "ssh", "git"}:
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise LaneError("SOURCE_CREDENTIAL_FREE_POINTER_REQUIRED", "Use a source URL without credentials, query tokens or fragments.")
            continue  # Pointer metadata only; these operations never fetch a URL.
        path = Path(value).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise LaneError("SOURCE_ABSOLUTE_PATH_REQUIRED", "Select an absolute source path in a current read grant.")
        ProjectAccess(store).authorize(context.client_id, "read", path=path)


def _source_batch_paths(store, batch_id):
    with store.lane("sources").connection(read_only=True) as connection:
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='intake_batch'").fetchone() is None:
            raise LaneError("SOURCE_AUTHORITY_BATCH_MISSING", "Register the selected source batch first.")
        rows = connection.execute("SELECT supplied_pointer FROM source_occurrence WHERE batch_id=? ORDER BY ordinal LIMIT 257", (batch_id,)).fetchall()
        if not rows:
            raise LaneError("SOURCE_AUTHORITY_BATCH_MISSING", "Select a registered source batch.")
        if len(rows) > 256:
            raise LaneError("SOURCE_INTAKE_SOURCE_BUDGET", "The batch exceeds the supported native source-count budget.")
        return [row[0] for row in rows]


def read_source_records(store, request):
    # These are explicit SQL projections, never a caller-supplied table/column.
    sqlite_collections = {
        'sqlite_assets': ('source_sqlite_asset', 'canonical_asset_id'),
        'sqlite_receipts': ('source_sqlite_receipt', 'canonical_asset_id'),
        'sqlite_schema': ('source_sqlite_schema_object', 'inspection_id'),
        'sqlite_tables': ('source_sqlite_table_stat', 'inspection_id'),
        'sqlite_foreign_keys': ('source_sqlite_foreign_key', 'canonical_asset_id'),
    }
    collections = {
        "batches": ("intake_batch", True),
        "occurrences": ("source_occurrence", True),
        "relations": ("source_relation", True),
        "provenance": ("source_provenance", True),
        "graphs": ("source_graph_snapshot", True),
        "schemas": ("source_custom_schema", False),
        "identities": ("source_identity_entity", False),
        **{name: (table, False) for name, (table, _) in sqlite_collections.items()},
    }
    table, has_batch = collections[request.collection]
    if request.batch_id and not has_batch:
        raise LaneError("SOURCE_COLLECTION_SCOPE", "This collection is project-scoped; omit batch_id.")
    if request.inspection_id and request.collection not in sqlite_collections:
        raise LaneError('SOURCE_COLLECTION_SCOPE', 'An inspection id applies only to SQLite history collections.')
    with store.lane("sources").connection(read_only=True) as connection:
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone() is None:
            return {"collection": request.collection, "rows": [], "next_offset": None, "initialized": False}
        deadline = time.monotonic() + 5
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
        try:
            clause, parameters = (" WHERE batch_id=?", [request.batch_id]) if request.batch_id else ("", [])
            if request.inspection_id:
                column = sqlite_collections[request.collection][1]
                clause, parameters = ' WHERE ' + column + '=?', [request.inspection_id]
            cursor = connection.execute("SELECT * FROM " + table + clause + " ORDER BY rowid LIMIT ? OFFSET ?",
                                        [*parameters, request.limit + 1, request.offset])
            selected, size, truncated = [], 0, False
            for row in cursor:
                record = dict(row)
                size += len(canonical_json_bytes(record))
                if len(selected) >= request.limit or size > request.max_bytes:
                    if not selected:
                        raise LaneError("SOURCE_ROW_BUDGET", "Increase max_bytes to read this source record.")
                    truncated = True
                    break
                selected.append(record)
            return {"collection": request.collection, "rows": selected, "initialized": True,
                    "next_offset": request.offset + len(selected) if truncated else None}
        finally:
            connection.set_progress_handler(None, 0)


def register_source_actions(engine):
    from .custom_source_schema import (
        compile_and_map_custom_source_schema,
        configure_source_intake_schema_pill,
    )
    from .source_authority import (
        SOURCES_MIGRATIONS,
        reconcile_archive_counterparts,
        register_source_crosswalk,
        verify_source_batch_unchanged,
    )
    from .source_git_history import build_registered_git_history, build_source_git_commit_impact
    from .source_graph import build_registered_source_graph, diff_source_graphs, source_graph_impact
    from .source_identity import register_source_identity_matrix
    from .source_routing import (
        ROUTE_MIGRATIONS,
        SourceRoutesRead,
        inherited_routes,
        load_route,
        publish_routes,
        replay_registration,
    )
    from .source_sqlite import inspect_registered_sqlite_assets

    def intake(context, request, *, write, action=None):
        action = action or ('source_register' if write else 'source_classify')
        store = engine.directory.open(context.project_id, write=write)
        values = request.model_dump(mode="json")
        _authorize_source_paths(store, context, request.sources)
        def classify_arguments():
            effective, inheritance = inherited_routes(store, request.sources, request.overrides, request.parent_route_id)
            return {key: value for key, value in values.items() if key != 'parent_route_id'} | {'overrides': effective}, inheritance
        if not write:
            arguments, inheritance = classify_arguments()
            result = classify_source_intake(**arguments, registered_repository_path=store.source_root)
            result['route_inheritance'] = inheritance
            return _source_action_result(store, "source_classify", result)
        with engine.project_work.mutation(store) as lease:
            _authorize_source_paths(store, context, request.sources)
            replay = replay_registration(store, context, action, values)
            if replay is not None:
                return _source_action_result(store, action, replay)
            arguments, inheritance = classify_arguments()
            with lease.coordinated_transaction(['sources']):
                _authorize_source_paths(store, context, request.sources)
                context.authorize('write')
                result = classify_source_intake(**arguments, registered_repository_path=store.source_root,
                    authority_mode="GOVERNED_CONTENT_REGISTRY", authority_registry_path=store, writer=lease)
                result = publish_routes(store, context, action, values, result, inheritance, lease)
                return _source_action_result(store, action, result)

    engine.registry.register(ActionSpec("source_classify", "Classify ordered granted sources into retained sectors without registering or copying bytes.",
        SourceIntakeRequest, SourceOperationResult, lambda c, r: intake(c, r, write=False),
        profile="sources", workflow="source-intake", read_migrations=(*SOURCES_MIGRATIONS, *ROUTE_MIGRATIONS)))
    engine.registry.register(ActionSpec("source_register", "Freeze and register ordered source identities and assertions in the Sources authority.",
        SourceIntakeRequest, SourceOperationResult, lambda c, r: intake(c, r, write=True),
        permission="write", mutates=True, profile="sources", workflow="source-intake"))
    engine.registry.register(ActionSpec('lane_configure_routes',
        'Consume exact source-to-sector overrides in one attributed registration, optionally inheriting a prior route receipt.',
        SourceRouteConfigure, SourceOperationResult,
        lambda c, r: intake(c, r, write=True, action='lane_configure_routes'),
        permission='write', mutates=True, profile='sources', workflow='source-intake'))
    engine.registry.register(ActionSpec('source_routes_read', 'Inspect an exact immutable source-routing receipt and its source identities.',
        SourceRoutesRead, SourceOperationResult,
        lambda c, r: _source_action_result(engine.directory.open(c.project_id), 'source_routes_read',
            load_route(engine.directory.open(c.project_id), r.route_id)),
        profile='sources', workflow='source-intake', queryable_in_delta=True, cross_project_read=True,
        studio_read=True, read_migrations=(*SOURCES_MIGRATIONS, *ROUTE_MIGRATIONS)))
    engine.registry.register(ActionSpec("source_read", "Read a bounded page of registered source records without reading source payloads.",
        SourceReadRequest, SourceOperationResult,
        lambda c, r: _source_action_result(engine.directory.open(c.project_id), "source_read", read_source_records(engine.directory.open(c.project_id), r)),
        profile="sources", workflow="source-intake", queryable_in_delta=True, cross_project_read=True,
        studio_read=True, read_migrations=SOURCES_MIGRATIONS))

    def bind(action, function, *, write, reads_source=False):
        def handler(context, request):
            store = engine.directory.open(context.project_id, write=write)
            values = request.model_dump(mode="json")
            def authorize():
                if reads_source:
                    _authorize_source_paths(store, context, _source_batch_paths(store, request.batch_id))
                if isinstance(request, SourceCrosswalkRequest):
                    _authorize_source_paths(store, context, [request.crosswalk_path])
            if not write:
                with project_snapshot(store.root):
                    authorize()
                    return _source_action_result(store, action, function(store, **values))
            authorize()
            with engine.project_work.mutation(store) as lease, lease.coordinated_transaction(["sources"]):
                authorize()
                result = function(store, **values, writer=lease)
                value = _source_action_result(store, action, result)
                store.append_receipt("source_sdk_action", {"action": action, "client_id": context.client_id,
                    "request_id": context.request_id, "request_digest": sha256_bytes(canonical_json_bytes(values))})
                return value
        return handler

    operations = (
        ("source_verify", SourceBatchRequest, verify_source_batch_unchanged, False, True, "Verify the selected registered source bytes remain unchanged."),
        ("source_reconcile_archives", SourceBatchRequest, reconcile_archive_counterparts, True, False, "Append exact archive and extracted-counterpart relationships."),
        ("source_crosswalk", SourceCrosswalkRequest, register_source_crosswalk, True, False, "Register a granted crosswalk file with its content identity."),
        ("source_inspect_sqlite", SourceSQLiteRequest, inspect_registered_sqlite_assets, True, True, "Inspect registered selected SQLite assets read-only and append measured schema receipts."),
        ("source_identity", SourceIdentityRequest, register_source_identity_matrix, True, False, "Register typed identity entities, attributed assertions and evidence relationships."),
        ("source_graph", SourceGraphRequest, build_registered_source_graph, True, True, "Build a bounded source graph only when an analysis consumer requests it."),
        ("source_graph_diff", SourceGraphDiffRequest, diff_source_graphs, True, False, "Append a bounded comparison of two exact registered source graphs."),
        ("source_graph_impact", SourceGraphImpactRequest, source_graph_impact, True, False, "Append bounded impact evidence from selected source graph nodes."),
        ("source_git_history", SourceGitRequest, build_registered_git_history, True, True, "Index explicitly authorized Git history within the selected source budgets."),
        ("source_git_impact", SourceGitImpactRequest, build_source_git_commit_impact, True, False, "Append commit impact tied to exact source history and graph identities."),
        ("source_schema_map", SourceSchemaRequest, compile_and_map_custom_source_schema, True, False, "Compile a declarative custom schema and append its source mappings."),
        ("source_schema_configure", SourceSchemaConfigureRequest, configure_source_intake_schema_pill, True, False, "Add or version a source schema contract with exact predecessor checks."),
    )
    for action, model, function, write, reads_source, description in operations:
        engine.registry.register(ActionSpec(action, description, model, SourceOperationResult,
            bind(action, function, write=write, reads_source=reads_source), profile="sources", workflow="source-intake",
            permission="write" if write else "read", mutates=write,
            read_migrations=() if write else SOURCES_MIGRATIONS))
