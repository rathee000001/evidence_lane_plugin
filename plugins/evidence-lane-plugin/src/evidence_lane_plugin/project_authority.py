"""User-owned project authority layout and bounded legacy relocation helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lane_engine import build_lane_bundle, validate_lane_bundle
from .lanes import (
    CANONICAL_LANE_IDS,
    LANE_REGISTRY,
    lane_artifact_contract,
    lane_schema_asset,
    route_batch,
)
from .source_policy import content_exclusion_reason, path_exclusion_reason
from .timeutil import utc_now

PROJECT_AUTHORITY_SCHEMA = "evidence-lane.project-authority.v1"
PROJECT_AUTHORITY_LAYOUT_SCHEMA = "evidence-lane.project-authority-layout.v1"
PROJECT_AUTHORITY_MIGRATION_SCHEMA = "evidence-lane.project-authority-migration.v1"
PROJECT_AUTHORITY_CONFIRMATION = "MOVE_ACTIVE_PROJECT_AUTHORITY_PRESERVE_LEGACY_HISTORY"
STUDY_BRAIN_PROFILE_ID = "study-brain-routing-profile-v1"
_WORKING_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]{1,63}")

_LEGACY_HISTORY_TOP_LEVEL = frozenset(
    {
        ".build",
        "candidates",
        "single_pv0_to_pv13_bundle",
    }
)
_TRANSIENT_LOCK_NAMES = frozenset({".store.lock", ".state-travel-resume.lock"})

PLAN_SECTOR_ID = "plan"
CHAT_LINEAGE_SECTOR_ID = "chat_lineage"
OPERATIONAL_AUTHORITY_MARKER = ".operational-authority.json"

_LEGACY_PLAN_PATHS = (
    "task_backlog.json",
    "plan_runtime_projection.sqlite",
    "plan_atomic_insertions",
    "plan_normalization",
    "plan_runtime_refreshes",
)
_LEGACY_REFERENCE_PATHS = ("plan", "chat_lineage")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def plan_sector_root(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / "sectors" / PLAN_SECTOR_ID


def chat_lineage_sector_root(project_root: str | Path) -> Path:
    return Path(project_root).resolve() / "sectors" / CHAT_LINEAGE_SECTOR_ID


def _sector_operational_authority_ready(sector_root: Path) -> bool:
    marker = sector_root / OPERATIONAL_AUTHORITY_MARKER
    if not marker.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(
        payload.get("schema") == "evidence-lane.sector-operational-authority.v1"
        and payload.get("state") == "CANONICAL_ACTIVE"
    )


def resolved_plan_authority_root(project_root: str | Path) -> Path:
    """Return the sector-owned Plan root, with one bounded legacy fallback."""

    root = Path(project_root).resolve()
    sector = plan_sector_root(root)
    return sector if _sector_operational_authority_ready(sector) else root


def resolved_chat_lineage_root(project_root: str | Path) -> Path:
    """Return the sector-owned ChatLineage runtime root after migration."""

    root = Path(project_root).resolve()
    sector = chat_lineage_sector_root(root)
    if _sector_operational_authority_ready(sector):
        return sector / "lineage"
    return root / "lineage"


def resolved_plan_backlog_path(project_root: str | Path) -> Path:
    return resolved_plan_authority_root(project_root) / "task_backlog.json"


def resolved_plan_runtime_path(project_root: str | Path) -> Path:
    return resolved_plan_authority_root(project_root) / "plan_runtime_projection.sqlite"


def resolved_plan_auxiliary_path(project_root: str | Path, name: str) -> Path:
    require(
        name
        in {
            "plan_atomic_insertions",
            "plan_normalization",
            "plan_runtime_refreshes",
        },
        "PROJECT_PLAN_AUXILIARY_PATH_INVALID",
        "The requested Plan auxiliary authority is not allowlisted.",
        status="BLOCKED",
        name=name,
    )
    return resolved_plan_authority_root(project_root) / name


def validate_external_project_authority_root(
    value: str | Path,
    *,
    control_root: str | Path,
    project_id: str,
) -> Path:
    """Resolve one explicit user-project root outside host runtime control."""

    raw = os.path.expandvars(os.fspath(value)).strip()
    require(
        bool(raw),
        "PROJECT_AUTHORITY_ROOT_REQUIRED",
        "Project authority relocation requires one explicit user-owned root.",
        status="BLOCKED",
    )
    target = Path(raw).expanduser().resolve()
    control = Path(control_root).expanduser().resolve()
    require(
        target.is_absolute()
        and target.name == project_id
        and target != control
        and not _is_relative_to(target, control)
        and not _is_relative_to(control, target),
        "PROJECT_AUTHORITY_ROOT_INVALID",
        "The user-project authority root must be an exact project-named directory outside host runtime control.",
        status="BLOCKED",
        project_id=project_id,
        target=str(target),
    )
    return target


def _file_manifest(root: Path, relative_roots: list[str]) -> dict[str, Any]:
    members: dict[str, dict[str, Any]] = {}
    for relative_root in sorted(set(relative_roots)):
        path = root / relative_root
        if path.is_file():
            members[path.relative_to(root).as_posix()] = {
                "size_bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            continue
        if not path.is_dir():
            continue
        for member in sorted(path.rglob("*")):
            if member.is_file() and member.name not in _TRANSIENT_LOCK_NAMES:
                members[member.relative_to(root).as_posix()] = {
                    "size_bytes": member.stat().st_size,
                    "sha256": sha256_file(member),
                }
    body = {
        "schema": "evidence-lane.project-authority-member-manifest.v1",
        "member_count": len(members),
        "total_bytes": sum(int(row["size_bytes"]) for row in members.values()),
        "members": members,
    }
    return {**body, "manifest_sha256": sha256_bytes(canonical_json_bytes(body))}


def copy_active_project_authority(
    source_root: str | Path,
    staging_root: str | Path,
    *,
    accepted_pv: str,
) -> dict[str, Any]:
    """Copy and byte-verify active authority without duplicating legacy history."""

    source = Path(source_root).resolve()
    staging = Path(staging_root).resolve()
    require(
        source.is_dir() and not staging.exists(),
        "PROJECT_AUTHORITY_MIGRATION_BOUNDARY_INVALID",
        "The legacy project must exist and the migration staging root must be absent.",
        status="BLOCKED",
        source=str(source),
        staging=str(staging),
    )
    staging.mkdir(parents=True)
    copied_roots: list[str] = []
    for item in sorted(source.iterdir(), key=lambda value: value.name.casefold()):
        if item.name in _TRANSIENT_LOCK_NAMES:
            continue
        if item.name in _LEGACY_HISTORY_TOP_LEVEL or item.name == "accepted":
            continue
        destination = staging / item.name
        if item.is_dir():
            shutil.copytree(item, destination, copy_function=shutil.copy2)
        elif item.is_file():
            shutil.copy2(item, destination)
        else:
            continue
        copied_roots.append(item.name)

    accepted_relative = f"accepted/{accepted_pv}"
    accepted_source = source / accepted_relative
    require(
        accepted_source.is_dir(),
        "PROJECT_AUTHORITY_ACCEPTED_PV_MISSING",
        "The active accepted PV must be present before project authority relocation.",
        status="MISMATCH",
        accepted_pv=accepted_pv,
    )
    (staging / "accepted").mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        accepted_source,
        staging / accepted_relative,
        copy_function=shutil.copy2,
    )
    copied_roots.append(accepted_relative)
    source_manifest = _file_manifest(source, copied_roots)
    staged_manifest = _file_manifest(staging, copied_roots)
    require(
        source_manifest["members"] == staged_manifest["members"]
        and source_manifest["member_count"] == staged_manifest["member_count"]
        and source_manifest["total_bytes"] == staged_manifest["total_bytes"],
        "PROJECT_AUTHORITY_COPY_VERIFICATION_FAILED",
        "The staged project authority bytes do not exactly match the selected legacy authority bytes.",
        status="FAIL",
        source_manifest_sha256=source_manifest["manifest_sha256"],
        staged_manifest_sha256=staged_manifest["manifest_sha256"],
    )
    return {
        "status": "PASS",
        "copied_roots": copied_roots,
        "source_manifest": source_manifest,
        "staged_manifest": staged_manifest,
        "legacy_history_roots": sorted(_LEGACY_HISTORY_TOP_LEVEL | {"accepted"}),
    }


def _lane_reference(
    project_root: Path,
    *,
    lane_id: str,
    accepted_pv: str,
    emitted_lane_ids: set[str],
) -> dict[str, Any]:
    lane = LANE_REGISTRY[lane_id]
    accepted_relative = f"accepted/{accepted_pv}/lanes/{lane_id}"
    accepted_lane_root = project_root / accepted_relative
    populated = lane_id in emitted_lane_ids and accepted_lane_root.is_dir()
    schema = lane_schema_asset(lane_id)
    artifacts = lane_artifact_contract(lane_id)
    return {
        "schema": "evidence-lane.project-sector-reference.v1",
        "lane_id": lane_id,
        "display_label": lane.display_label,
        "state": "ACCEPTED_MATERIALIZED" if populated else "SCHEMA_READY_UNPOPULATED",
        "accepted_pv": accepted_pv,
        "authority_relative_path": accepted_relative if populated else None,
        "authority_path_exists": populated,
        "lane_schema_id": schema["schema_id"],
        "lane_schema_version": schema["schema_version"],
        "lane_schema_contract_sha256": schema["contract_sha256"],
        "artifact_contract_sha256": artifacts["contract_sha256"],
        "empty_lane_payload_fabricated": False,
        "independent_project_truth_authority": False,
    }


def materialize_project_authority_layout(
    project_root: str | Path,
    *,
    published_root: str | Path,
    control_root: str | Path,
    project_id: str,
    repository_path: str | Path,
    accepted_pv: str,
    pointer_generation: int,
    accepted_manifest_sha256: str,
    legacy_history_root: str | Path | None = None,
) -> dict[str, Any]:
    """Materialize one registry-derived layout without inventing lane payload."""

    root = Path(project_root).resolve()
    published = Path(published_root).resolve()
    control = Path(control_root).resolve()
    repository = Path(repository_path).resolve()
    require(
        root.is_dir() and published != control,
        "PROJECT_AUTHORITY_LAYOUT_ROOT_INVALID",
        "Project authority layout requires a prepared external project root.",
        status="BLOCKED",
    )
    lane_manifest_path = root / "accepted" / accepted_pv / "lanes" / "manifest.json"
    require(
        lane_manifest_path.is_file(),
        "PROJECT_AUTHORITY_ACCEPTED_LANE_MANIFEST_MISSING",
        "The accepted PV lane manifest is required for honest sector materialization.",
        status="MISMATCH",
        accepted_pv=accepted_pv,
    )
    lane_manifest = json.loads(lane_manifest_path.read_text(encoding="utf-8"))
    emitted = {str(value) for value in lane_manifest.get("emitted_lane_ids") or []}
    require(
        int(lane_manifest.get("canonical_lane_count") or 0) == len(CANONICAL_LANE_IDS)
        and emitted <= set(CANONICAL_LANE_IDS),
        "PROJECT_AUTHORITY_ACCEPTED_LANE_REGISTRY_MISMATCH",
        "The accepted lane bundle does not bind the current canonical registry.",
        status="MISMATCH",
    )

    layout_dirs = (
        "sectors",
        "ai_learning",
        "canon",
        "memory",
        "sources/objects",
        "sources/source_manifests",
        "profiles",
        "receipts/project-authority",
        "receipts/sdk-replay",
        "receipts/active-contract-rebindings",
    )
    for relative in layout_dirs:
        (root / relative).mkdir(parents=True, exist_ok=True)

    sector_rows: list[dict[str, Any]] = []
    for lane_id in CANONICAL_LANE_IDS:
        sector_root = root / "sectors" / lane_id
        sector_root.mkdir(parents=True, exist_ok=True)
        reference = _lane_reference(
            root,
            lane_id=lane_id,
            accepted_pv=accepted_pv,
            emitted_lane_ids=emitted,
        )
        atomic_write_json(sector_root / "authority.ref.json", reference)
        sector_rows.append(reference)

    named_authorities: dict[str, dict[str, Any]] = {
        "plan": {
            "state": "CANONICAL_ACTIVE",
            "paths": ["plan_runtime_projection.sqlite", "task_backlog.json"],
            "sector_reference_only": True,
        },
        "chat_lineage": {
            "state": "CANONICAL_ACTIVE",
            "paths": ["lineage"],
            "sector_reference_only": True,
        },
        "ai_learning": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": ["ai_learning/agent-learning.sqlite"],
        },
        "canon": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": ["canon/canon-input.sqlite"],
        },
        "memory": {
            "state": "SCHEMA_READY_AWAITING_R242",
            "paths": [],
            "payload_fabricated": False,
        },
        "universe": {
            "state": "ABSENT_AWAITING_R244",
            "paths": [],
            "payload_fabricated": False,
        },
        "sources": {
            "state": "CANONICAL_ACTIVE",
            "paths": ["source_authority.sqlite"],
        },
    }
    for authority, binding in named_authorities.items():
        reference_root = (
            root / "sectors" / authority
            if authority in {PLAN_SECTOR_ID, CHAT_LINEAGE_SECTOR_ID}
            else root / authority
        )
        reference_root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            reference_root / "operational-authority.ref.json",
            {
                "schema": "evidence-lane.named-project-authority-reference.v1",
                "authority": authority.upper(),
                "project_id": project_id,
                **binding,
            },
        )

    legacy_history = (
        Path(legacy_history_root).resolve() if legacy_history_root is not None else None
    )
    atomic_write_json(
        root / "receipts" / "project-authority" / "legacy_history.ref.json",
        {
            "schema": "evidence-lane.legacy-project-history-reference.v1",
            "state": "NON_AUTHORITATIVE_HISTORY_AWAITING_R243_CAS_MIGRATION",
            "legacy_history_root": str(legacy_history) if legacy_history else None,
            "accepted_current_local": accepted_pv,
            "candidate_authority": False,
            "accepted_pointer_authority": False,
            "pointer_movement_allowed": False,
        },
    )
    study_profile = {
        "schema": "evidence-lane.source-routing-profile.v1",
        "profile_id": STUDY_BRAIN_PROFILE_ID,
        "profile_kind": "STUDY_BRAIN",
        "stored_lane_created": False,
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "required_fanout_lane_ids": [
            "brain_loader",
            "sqlite_brain",
            "project_engulf",
            "research",
        ],
        "additional_lane_routing": "SOURCE_CLASSIFICATION_DERIVED",
        "project_truth_promotion_allowed": False,
        "raw_payload_duplication_allowed": False,
    }
    atomic_write_json(root / "profiles" / "study_brain.json", study_profile)

    layout = {
        "schema": PROJECT_AUTHORITY_LAYOUT_SCHEMA,
        "project_id": project_id,
        "resolved_project_root": str(published),
        "repository_path": str(repository),
        "host_control_root": str(control),
        "runtime_separated": not _is_relative_to(published, control),
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "ordered_lane_ids": list(CANONICAL_LANE_IDS),
        "materialized_sector_directory_count": len(sector_rows),
        "accepted_materialized_lane_count": len(emitted),
        "schema_ready_unpopulated_lane_count": len(CANONICAL_LANE_IDS) - len(emitted),
        "study_brain": study_profile,
        "named_authorities": named_authorities,
        "accepted_pointer": {
            "accepted_pv": accepted_pv,
            "generation": pointer_generation,
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "moved": False,
        },
        "sources_root_authority": False,
        "shadow_authority_created": False,
        "created_at": utc_now(),
    }
    layout["layout_sha256"] = sha256_bytes(canonical_json_bytes(layout))
    atomic_write_json(root / "project_authority.json", layout)

    mmd_lines = [
        "flowchart TB",
        '  PROJECT["User project authority"]',
        '  RUNTIME["Host-managed plugin runtime"]',
        "  PROJECT -. hashed binding only .-> RUNTIME",
        '  PROJECT --> SECTORS["18 typed sectors"]',
        '  PROJECT --> ADAPTIVE["Plan / Lineage / Learning / Canon / Memory / Universe"]',
        '  PROFILE["Study Brain routing profile"] --> SECTORS',
    ]
    dot_lines = [
        "digraph evidence_lane_project_authority {",
        '  PROJECT [label="User project authority"];',
        '  RUNTIME [label="Host-managed plugin runtime"];',
        '  SECTORS [label="18 typed sectors"];',
        '  ADAPTIVE [label="Named adaptive authorities"];',
        '  PROFILE [label="Study Brain routing profile"];',
        "  PROJECT -> RUNTIME [style=dashed];",
        "  PROJECT -> SECTORS;",
        "  PROJECT -> ADAPTIVE;",
        "  PROFILE -> SECTORS;",
        "}",
    ]
    atomic_write_bytes(
        root / "project_authority.mmd", ("\n".join(mmd_lines) + "\n").encode("utf-8")
    )
    atomic_write_bytes(
        root / "project_authority.dot", ("\n".join(dot_lines) + "\n").encode("utf-8")
    )
    atomic_write_json(
        root / "project_authority.tools.json",
        {
            "schema": "evidence-lane.project-authority-tools.v1",
            "project_id": project_id,
            "query_routes": [
                "project_authority_status",
                "pv_task_backlog",
                "lane_status",
                "lane_search",
                "lane_fetch",
            ],
            "write_routes": ["project_authority_migrate"],
            "study_brain_profile_id": STUDY_BRAIN_PROFILE_ID,
            "candidate_effect": "NONE",
            "hil_effect": "NONE",
            "pointer_effect": "NONE",
        },
    )
    projected_members = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != "PROJECT_AUTHORITY_MANIFEST.json"
        and (
            path.name.startswith("project_authority")
            or path.name in {"authority.ref.json", "operational-authority.ref.json"}
            or path.name == "study_brain.json"
            or path.name == "legacy_history.ref.json"
        )
    }
    manifest_body = {
        "schema": "evidence-lane.project-authority-projection-manifest.v1",
        "project_id": project_id,
        "member_count": len(projected_members),
        "members": projected_members,
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "layout_sha256": layout["layout_sha256"],
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": sha256_bytes(canonical_json_bytes(manifest_body)),
    }
    atomic_write_json(root / "PROJECT_AUTHORITY_MANIFEST.json", manifest)
    return {
        "status": "PASS",
        "schema": PROJECT_AUTHORITY_SCHEMA,
        "project_id": project_id,
        "resolved_project_root": str(published),
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "materialized_sector_directory_count": len(sector_rows),
        "accepted_materialized_lane_count": len(emitted),
        "schema_ready_unpopulated_lane_ids": [
            lane_id for lane_id in CANONICAL_LANE_IDS if lane_id not in emitted
        ],
        "study_brain_profile": study_profile,
        "layout_sha256": layout["layout_sha256"],
        "manifest_sha256": manifest["manifest_sha256"],
        "pointer_moved": False,
        "candidate_created": False,
        "hil_inferred": False,
    }


def _git_bytes(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(  # nosec B603
        ["git", *arguments],
        cwd=repository,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return completed.stdout


def _working_repository_identity(repository: Path) -> dict[str, Any]:
    """Return bounded hashes for the exact live index/worktree identity."""

    branch = (
        _git_bytes(repository, "branch", "--show-current")
        .decode("utf-8", errors="strict")
        .strip()
    )
    head = (
        _git_bytes(repository, "rev-parse", "HEAD")
        .decode("ascii", errors="strict")
        .strip()
    )
    tree = (
        _git_bytes(repository, "rev-parse", "HEAD^{tree}")
        .decode("ascii", errors="strict")
        .strip()
    )
    index_paths = _git_bytes(
        repository,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        ".",
    )
    status = _git_bytes(
        repository, "status", "--porcelain=v2", "-z", "--untracked-files=all"
    )
    cached_diff = _git_bytes(
        repository, "diff", "--cached", "--binary", "--full-index", "--", "."
    )
    unstaged_diff = _git_bytes(
        repository, "diff", "--binary", "--full-index", "--", "."
    )
    body = {
        "branch": branch,
        "head": head,
        "tree": tree,
        "complete_path_set_sha256": sha256_bytes(index_paths),
        "status_sha256": sha256_bytes(status),
        "cached_diff_sha256": sha256_bytes(cached_diff),
        "unstaged_diff_sha256": sha256_bytes(unstaged_diff),
        "path_count": len([value for value in index_paths.split(b"\0") if value]),
    }
    return {
        **body,
        "working_identity_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _zero_delimited_git_paths(repository: Path, *arguments: str) -> set[str]:
    return {
        value.decode("utf-8", errors="strict").replace("\\", "/")
        for value in _git_bytes(repository, *arguments).split(b"\0")
        if value
    }


def _working_delta_inventory(
    repository: Path,
    *,
    historical_parent_pv: str,
    working_identity_sha256: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Account every dirty/untracked path without re-indexing archived trees."""

    staged = _zero_delimited_git_paths(
        repository,
        "diff",
        "--cached",
        "--name-only",
        "-z",
        "--diff-filter=ACMRD",
        "--",
        ".",
    )
    unstaged = _zero_delimited_git_paths(
        repository,
        "diff",
        "--name-only",
        "-z",
        "--diff-filter=ACMRD",
        "--",
        ".",
    )
    untracked = _zero_delimited_git_paths(
        repository,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        ".",
    )
    paths = sorted(staged | unstaged | untracked)
    metadata_only_prefixes = (
        "evidence/",
        ".github-pages-build/",
        ".tmp-flash-check/",
    )
    route_overrides = {
        path: "artifacts"
        for path in paths
        if path.casefold().startswith(metadata_only_prefixes)
    }
    routes = route_batch(
        paths,
        # A live worktree is local-code authority even when its remote provider
        # is GitHub.  Git history/checkpoint ingestion owns github_code.
        code_mode="local_code",
        overrides=route_overrides,
    )
    rows: list[dict[str, Any]] = []
    content_paths: list[str] = []
    for relative in paths:
        target = repository / Path(relative)
        exists = target.is_file()
        exclusion_reason = path_exclusion_reason(relative) if exists else None
        if exists and relative not in route_overrides and exclusion_reason is None:
            try:
                exclusion_reason = content_exclusion_reason(target.read_bytes())
            except OSError:
                exclusion_reason = "SOURCE_FILE_UNREADABLE"
        metadata_only = relative in route_overrides or exclusion_reason is not None
        if exists and not metadata_only:
            content_paths.append(relative)
        rows.append(
            {
                "path": relative,
                "lane_id": routes[relative],
                "staged": relative in staged,
                "unstaged": relative in unstaged,
                "untracked": relative in untracked,
                "exists": exists,
                "size_bytes": target.stat().st_size if exists else None,
                "sha256": sha256_file(target) if exists else None,
                "content_policy": (
                    "BOUNDED_LANE_CONTENT"
                    if exists and not metadata_only
                    else "HASH_LOCATOR_ONLY"
                    if exists
                    else "DELETION_TOMBSTONE"
                ),
                "content_policy_reason": (
                    "ARCHIVED_EVIDENCE_METADATA_ONLY"
                    if relative in route_overrides
                    else exclusion_reason
                ),
                "historical_parent_pv": historical_parent_pv,
                "working_identity_sha256": working_identity_sha256,
            }
        )
    return rows, sorted(content_paths)


def _write_working_delta_inventory(
    destination: Path,
    *,
    rows: list[dict[str, Any]],
    historical_parent_pv: str,
    pointer_generation: int,
    working_identity_sha256: str,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            PRAGMA synchronous=FULL;
            CREATE TABLE working_path_inventory(
                path TEXT PRIMARY KEY,
                lane_id TEXT NOT NULL,
                staged INTEGER NOT NULL,
                unstaged INTEGER NOT NULL,
                untracked INTEGER NOT NULL,
                exists_now INTEGER NOT NULL,
                size_bytes INTEGER,
                sha256 TEXT,
                content_policy TEXT NOT NULL,
                content_policy_reason TEXT,
                historical_parent_pv TEXT NOT NULL,
                working_identity_sha256 TEXT NOT NULL
            );
            CREATE INDEX working_path_inventory_lane_idx
                ON working_path_inventory(lane_id, content_policy);
            CREATE VIRTUAL TABLE working_path_inventory_fts USING fts5(
                path, lane_id, content_policy, content='working_path_inventory',
                content_rowid='rowid'
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO working_path_inventory(
                path,lane_id,staged,unstaged,untracked,exists_now,size_bytes,
                sha256,content_policy,content_policy_reason,historical_parent_pv,
                working_identity_sha256
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    row["path"],
                    row["lane_id"],
                    int(row["staged"]),
                    int(row["unstaged"]),
                    int(row["untracked"]),
                    int(row["exists"]),
                    row["size_bytes"],
                    row["sha256"],
                    row["content_policy"],
                    row["content_policy_reason"],
                    historical_parent_pv,
                    working_identity_sha256,
                )
                for row in rows
            ],
        )
        connection.execute(
            "INSERT INTO working_path_inventory_fts(rowid,path,lane_id,content_policy) "
            "SELECT rowid,path,lane_id,content_policy FROM working_path_inventory"
        )
        connection.execute(f"PRAGMA user_version={pointer_generation}")
        connection.commit()
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        connection.close()
    require(
        integrity.lower() == "ok",
        "PROJECT_WORKING_DELTA_INVENTORY_INVALID",
        "The compact dirty-path inventory failed SQLite integrity validation.",
        status="FAIL",
    )
    lane_counts: dict[str, int] = {}
    policy_counts: dict[str, int] = {}
    for row in rows:
        lane_counts[row["lane_id"]] = lane_counts.get(row["lane_id"], 0) + 1
        policy_counts[row["content_policy"]] = (
            policy_counts.get(row["content_policy"], 0) + 1
        )
    body = {
        "schema": "evidence-lane.working-delta-inventory.v1",
        "historical_parent_pv": historical_parent_pv,
        "pointer_generation": pointer_generation,
        "working_identity_sha256": working_identity_sha256,
        "path_count": len(rows),
        "lane_counts": lane_counts,
        "content_policy_counts": policy_counts,
        "sqlite_sha256": sha256_file(destination),
    }
    return {
        **body,
        "inventory_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _path_members(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    base = path.parent if path.is_file() else path
    targets = [path] if path.is_file() else sorted(path.rglob("*"))
    members: dict[str, dict[str, Any]] = {}
    for target in targets:
        if not target.is_file() or target.name in _TRANSIENT_LOCK_NAMES:
            continue
        relative = (
            target.name if path.is_file() else target.relative_to(base).as_posix()
        )
        members[relative] = {
            "size_bytes": target.stat().st_size,
            "sha256": sha256_file(target),
        }
    return members


def _sqlite_logical_report(path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=30
    )
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        schema_rows = [
            tuple(row)
            for row in connection.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            )
        ]
        table_counts: dict[str, int] = {}
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            escaped = str(name).replace('"', '""')
            table_counts[str(name)] = int(
                connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
            )
        return {
            "integrity_check": integrity,
            "schema_sha256": sha256_bytes(canonical_json_bytes(schema_rows)),
            "table_counts": table_counts,
            "user_version": int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            ),
        }
    finally:
        connection.close()


def _copy_sqlite_snapshot(source: Path, destination: Path) -> dict[str, Any]:
    before = _path_members(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"{source.resolve().as_uri()}?mode=ro", uri=True, timeout=30
    )
    destination_connection = sqlite3.connect(destination, timeout=30)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()
    after = _path_members(source)
    require(
        before == after,
        "PROJECT_WORKING_SQLITE_CHANGED_DURING_SNAPSHOT",
        "A live SQLite authority changed while its sector snapshot was created.",
        status="MISMATCH",
        source=str(source),
    )
    source_report = _sqlite_logical_report(source)
    destination_report = _sqlite_logical_report(destination)
    require(
        source_report == destination_report
        and destination_report["integrity_check"].lower() == "ok",
        "PROJECT_WORKING_SQLITE_SNAPSHOT_MISMATCH",
        "The sector-owned SQLite snapshot is not logically identical to its source.",
        status="FAIL",
        source=str(source),
    )
    return {
        "copy_mode": "SQLITE_BACKUP",
        "source": str(source),
        "destination": str(destination),
        "logical_report": destination_report,
        "destination_sha256": sha256_file(destination),
    }


def _copy_authority_path(source: Path, destination: Path) -> dict[str, Any]:
    require(
        source.exists() and not destination.exists(),
        "PROJECT_WORKING_AUTHORITY_COPY_BOUNDARY_INVALID",
        "A migration source must exist and its staged destination must be absent.",
        status="BLOCKED",
        source=str(source),
        destination=str(destination),
    )
    before = _path_members(source)
    reports: list[dict[str, Any]] = []
    if source.is_file():
        if source.suffix.casefold() in {".sqlite", ".db"}:
            reports.append(_copy_sqlite_snapshot(source, destination))
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            require(
                sha256_file(source) == sha256_file(destination),
                "PROJECT_WORKING_FILE_COPY_MISMATCH",
                "A staged authority file did not preserve exact bytes.",
                status="FAIL",
                source=str(source),
            )
    else:
        destination.mkdir(parents=True)
        for member in sorted(source.rglob("*")):
            if not member.is_file() or member.name in _TRANSIENT_LOCK_NAMES:
                continue
            target = destination / member.relative_to(source)
            if member.suffix.casefold() in {".sqlite", ".db"}:
                reports.append(_copy_sqlite_snapshot(member, target))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(member, target)
                require(
                    sha256_file(member) == sha256_file(target),
                    "PROJECT_WORKING_FILE_COPY_MISMATCH",
                    "A staged authority file did not preserve exact bytes.",
                    status="FAIL",
                    source=str(member),
                )
    after = _path_members(source)
    require(
        before == after,
        "PROJECT_WORKING_AUTHORITY_CHANGED_DURING_COPY",
        "A live project authority changed during staged migration.",
        status="MISMATCH",
        source=str(source),
    )
    destination_members = _path_members(destination)
    return {
        "source": str(source),
        "destination": str(destination),
        "source_member_count": len(before),
        "destination_member_count": len(destination_members),
        "source_manifest_sha256": sha256_bytes(canonical_json_bytes(before)),
        "destination_manifest_sha256": sha256_bytes(
            canonical_json_bytes(destination_members)
        ),
        "sqlite_snapshots": reports,
    }


def _refresh_lane_bundle_checksums(bundle_root: Path) -> None:
    members = {
        path.relative_to(bundle_root).as_posix(): sha256_file(path)
        for path in sorted(bundle_root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    atomic_write_json(
        bundle_root / "SHA256SUMS.json",
        {
            "schema": "evidence-lane.recursive-sha256.v1",
            "members": members,
            "member_count": len(members),
        },
    )
    manifest_path = bundle_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["bundle_sha256"] = sha256_bytes(canonical_json_bytes(members))
    manifest["member_count"] = len(members) + 2
    atomic_write_json(manifest_path, manifest)


def _remove_exact_migration_target(root: Path, relative: str) -> None:
    target = (root / relative).resolve()
    require(
        target != root and _is_relative_to(target, root),
        "PROJECT_WORKING_CLEANUP_ESCAPE",
        "A migration cleanup target escaped the exact project root.",
        status="FAIL",
        target=str(target),
    )
    if target.is_dir():
        shutil.rmtree(target)
    elif target.is_file():
        target.unlink()


def _recover_interrupted_working_sector_stage(
    root: Path,
    *,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    working_identity: dict[str, Any],
) -> dict[str, Any] | None:
    """Promote one complete interrupted stage only when active sectors are absent."""

    active_sectors = root / "sectors"
    if active_sectors.exists():
        return None
    matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    for staging in sorted(root.glob(".sectors-working-*.staging")):
        receipt_path = staging / "working_migration_receipt.json"
        if not staging.is_dir() or not receipt_path.is_file():
            continue
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            validation = validate_lane_bundle(staging)
        except (OSError, json.JSONDecodeError, EvidenceLaneError):
            continue
        staged_identity = dict(receipt.get("working_identity") or {})
        if (
            validation.get("valid") is True
            and receipt.get("project_id") == project_id
            and receipt.get("historical_parent_pv") == accepted_pv
            and int(receipt.get("pointer_generation") or -1) == pointer_generation
            and staged_identity.get("working_identity_sha256")
            == working_identity.get("working_identity_sha256")
        ):
            matches.append((staging, receipt, validation))
    require(
        len(matches) <= 1,
        "PROJECT_WORKING_RECOVERY_STAGE_AMBIGUOUS",
        "More than one complete interrupted working-sector stage matches the live identity.",
        status="BLOCKED",
        matching_stages=[str(row[0]) for row in matches],
    )
    if not matches:
        return None
    staging, receipt, validation = matches[0]
    recovered_body = {
        **receipt,
        "state": "RECOVERED_COMMITTED",
        "recovery_kind": "INTERRUPTED_STAGE_EXACT_IDENTITY_RECOVERY",
        "recovered_at": utc_now(),
    }
    recovered_receipt = {
        **recovered_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(recovered_body)),
    }
    atomic_write_json(staging / "working_migration_receipt.json", recovered_receipt)
    _refresh_lane_bundle_checksums(staging)
    recovered_validation = validate_lane_bundle(staging)
    require(
        recovered_validation.get("valid") is True,
        "PROJECT_WORKING_RECOVERY_STAGE_INVALID",
        "The interrupted working-sector stage failed validation after recovery sealing.",
        status="FAIL",
    )
    os.replace(staging, active_sectors)
    recovery_record = {
        "schema": "evidence-lane.working-sector-recovery.v1",
        "status": "PASS",
        "project_id": project_id,
        "historical_parent_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "working_identity_sha256": working_identity["working_identity_sha256"],
        "recovered_stage": staging.name,
        "sector_bundle_sha256_before_reseal": validation.get("bundle_sha256"),
        "sector_bundle_sha256": recovered_validation.get("bundle_sha256"),
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    recovery_record["receipt_sha256"] = sha256_bytes(
        canonical_json_bytes(recovery_record)
    )
    atomic_write_json(
        root / "receipts" / "project-authority" / "working-sector-recovery.json",
        recovery_record,
    )
    return recovery_record


def migrate_working_project_sectors(
    project_root: str | Path,
    *,
    repository_root: str | Path,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    expected_branch: str | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    """Make all 18 sectors own the live WORKING state without PV movement."""

    root = Path(project_root).resolve()
    repository = Path(repository_root).resolve()
    require(
        root.is_dir() and root.name == project_id and (repository / ".git").exists(),
        "PROJECT_WORKING_MIGRATION_BINDING_INVALID",
        "Working-sector migration requires the exact project and Git workspace.",
        status="BLOCKED",
    )
    pointer_path = root / "active_pointer.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    require(
        pointer.get("project_id") == project_id
        and pointer.get("accepted_pv") == accepted_pv
        and int(pointer.get("generation") or -1) == pointer_generation,
        "PROJECT_WORKING_POINTER_MISMATCH",
        "Working-sector migration cannot change or reinterpret the accepted pointer.",
        status="MISMATCH",
    )
    pointer_before = sha256_file(pointer_path)
    accepted_lane_bundle = root / "accepted" / accepted_pv / "lanes"
    require(
        (accepted_lane_bundle / "manifest.json").is_file(),
        "PROJECT_WORKING_ACCEPTED_BASE_MISSING",
        "The accepted PV lane bundle is required as the historical parent.",
        status="MISMATCH",
        accepted_pv=accepted_pv,
    )
    identity_before = _working_repository_identity(repository)
    if expected_branch is not None:
        require(
            identity_before["branch"] == expected_branch,
            "PROJECT_WORKING_BRANCH_MISMATCH",
            "The live branch differs from the migration binding.",
            status="MISMATCH",
        )
    if expected_head is not None:
        require(
            identity_before["head"] == expected_head,
            "PROJECT_WORKING_HEAD_MISMATCH",
            "The live HEAD differs from the migration binding.",
            status="MISMATCH",
        )

    active_sectors = root / "sectors"
    interrupted_stage_recovery = _recover_interrupted_working_sector_stage(
        root,
        project_id=project_id,
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
        working_identity=identity_before,
    )
    active_sectors_existed = active_sectors.is_dir()
    refreshing_existing = bool(
        _sector_operational_authority_ready(plan_sector_root(root))
        and _sector_operational_authority_ready(chat_lineage_sector_root(root))
        and all((active_sectors / lane_id).is_dir() for lane_id in CANONICAL_LANE_IDS)
        and not any((root / name).exists() for name in (*_LEGACY_PLAN_PATHS, "lineage"))
    )
    if refreshing_existing:
        validation = validate_lane_bundle(active_sectors)
        checksum_mismatches = dict(validation.get("checksum_mismatches") or {})
        operational_prefixes = (
            f"{PLAN_SECTOR_ID}/task_backlog.json",
            f"{PLAN_SECTOR_ID}/plan_runtime_projection.sqlite",
            f"{CHAT_LINEAGE_SECTOR_ID}/lineage/",
        )
        operational_authority_only_drift = bool(checksum_mismatches) and all(
            path in operational_prefixes[:2]
            or path.startswith(operational_prefixes[2])
            for path in checksum_mismatches
        )
        refreshable_existing_authority = bool(
            operational_authority_only_drift
            and not validation.get("lane_manifest_errors")
            and all(
                lane.get("valid") is True
                for lane in dict(validation.get("lanes") or {}).values()
            )
            and validation.get("lane_directory_set_valid") is True
            and validation.get("source_routes_valid") is True
            and validation.get("topology_valid") is True
        )
        require(
            validation.get("valid") is True or refreshable_existing_authority,
            "PROJECT_WORKING_EXISTING_SECTORS_INVALID",
            "The existing sector-owned working authority failed validation.",
            status="MISMATCH",
            refreshable_existing_authority=refreshable_existing_authority,
            checksum_mismatch_paths=sorted(checksum_mismatches),
        )
        existing_receipt_path = active_sectors / "working_migration_receipt.json"
        existing_receipt = (
            json.loads(existing_receipt_path.read_text(encoding="utf-8"))
            if existing_receipt_path.is_file()
            else {}
        )
        existing_identity = dict(existing_receipt.get("working_identity") or {})
        if (
            validation.get("valid") is True
            and existing_identity.get("working_identity_sha256")
            == identity_before.get("working_identity_sha256")
        ):
            return {
                "status": "PASS",
                "state": "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE",
                "working_identity": identity_before,
                "lane_validation": validation,
                "interrupted_stage_recovery": interrupted_stage_recovery,
                "pointer_moved": False,
                "candidate_created": False,
                "hil_inferred": False,
            }

    plan_source_root = resolved_plan_authority_root(root)
    lineage_source = resolved_chat_lineage_root(root)

    migration_id = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "accepted_pv": accepted_pv,
                "pointer_generation": pointer_generation,
                "working_identity_sha256": identity_before["working_identity_sha256"],
                "started_at": utc_now(),
            }
        )
    )[:16]
    staging = root / f".sectors-working-{migration_id}.staging"
    backup = root / f".sectors-working-{migration_id}.previous"
    require(
        not staging.exists() and not backup.exists(),
        "PROJECT_WORKING_MIGRATION_REPLAY_PATH_EXISTS",
        "A working-sector migration staging or backup path already exists.",
        status="BLOCKED",
    )

    delta_rows, content_paths = _working_delta_inventory(
        repository,
        historical_parent_pv=accepted_pv,
        working_identity_sha256=identity_before["working_identity_sha256"],
    )
    copy_reports: list[dict[str, Any]] = []
    swapped = False
    try:
        build_lane_bundle(
            repository_root=repository,
            output_directory=staging,
            code_mode="local_code",
            parent_lane_bundle=accepted_lane_bundle,
            parent_pv=accepted_pv,
            proposed_pv=f"{accepted_pv}_WORKING",
            pointer_generation=pointer_generation,
            include_untracked=True,
            materialize_all_lanes=True,
            source_overrides={
                row["path"]: row["lane_id"]
                for row in delta_rows
                if row["content_policy"] == "BOUNDED_LANE_CONTENT"
            },
            source_paths_override=content_paths,
            preserve_parent_unmentioned=True,
            index_git_history=False,
        )
        initial_validation = validate_lane_bundle(staging)
        require(
            initial_validation.get("valid") is True,
            "PROJECT_WORKING_LANE_BUILD_INVALID",
            "The staged all-lane working authority failed validation.",
            status="FAIL",
        )

        plan_stage = staging / PLAN_SECTOR_ID
        for relative in _LEGACY_PLAN_PATHS:
            source = plan_source_root / relative
            if source.exists():
                copy_reports.append(_copy_authority_path(source, plan_stage / relative))
        require(
            (plan_stage / "task_backlog.json").is_file()
            and (plan_stage / "plan_runtime_projection.sqlite").is_file(),
            "PROJECT_WORKING_PLAN_AUTHORITY_MISSING",
            "The canonical Plan JSON and SQLite were not staged into the Plan sector.",
            status="MISMATCH",
        )
        inventory_report = _write_working_delta_inventory(
            staging / "artifacts" / "working_delta_inventory.sqlite",
            rows=delta_rows,
            historical_parent_pv=accepted_pv,
            pointer_generation=pointer_generation,
            working_identity_sha256=identity_before["working_identity_sha256"],
        )
        atomic_write_json(
            staging / "artifacts" / "working_delta_inventory.json",
            inventory_report,
        )

        require(
            lineage_source.is_dir(),
            "PROJECT_WORKING_CHAT_LINEAGE_MISSING",
            "The live ChatLineage authority is required for sector migration.",
            status="MISMATCH",
        )
        lineage_stage = staging / CHAT_LINEAGE_SECTOR_ID / "lineage"
        lineage_stage.mkdir(parents=True, exist_ok=False)
        for filename in ("chat_lineage.sqlite", "chat_lineage_head.json"):
            source = lineage_source / filename
            require(
                source.is_file(),
                "PROJECT_WORKING_CHAT_LINEAGE_CANONICAL_FILE_MISSING",
                "The current canonical ChatLineage SQLite and head are required.",
                status="MISMATCH",
                source=str(source),
            )
            copy_reports.append(_copy_authority_path(source, lineage_stage / filename))

        for lane_id in CANONICAL_LANE_IDS:
            lane_root = staging / lane_id
            schema = lane_schema_asset(lane_id)
            artifacts = lane_artifact_contract(lane_id)
            atomic_write_json(
                lane_root / "authority.ref.json",
                {
                    "schema": "evidence-lane.working-sector-authority.v1",
                    "state": "LIVE_WORKING",
                    "project_id": project_id,
                    "lane_id": lane_id,
                    "accepted_pv": accepted_pv,
                    "historical_parent_pv": accepted_pv,
                    "pointer_generation": pointer_generation,
                    "lane_schema_contract_sha256": schema["contract_sha256"],
                    "artifact_contract_sha256": artifacts["contract_sha256"],
                    "working_identity_sha256": identity_before[
                        "working_identity_sha256"
                    ],
                    "candidate_directory_created": False,
                    "pointer_moved": False,
                },
            )
            atomic_write_json(
                lane_root / "study_brain.json",
                {
                    "schema": "evidence-lane.lane-study-brain.v1",
                    "profile_id": f"{STUDY_BRAIN_PROFILE_ID}:{lane_id}",
                    "parent_profile_id": STUDY_BRAIN_PROFILE_ID,
                    "profile_kind": "LANE_SCOPED_STUDY_BRAIN",
                    "project_id": project_id,
                    "lane_id": lane_id,
                    "state": "ACTIVE",
                    "working_identity_sha256": identity_before[
                        "working_identity_sha256"
                    ],
                    "bounded_query_only": True,
                    "raw_payload_model_context_loading": False,
                    "candidate_created": False,
                    "pointer_moved": False,
                },
            )

        plan_files = {
            relative: sha256_file(plan_stage / relative)
            for relative in ("task_backlog.json", "plan_runtime_projection.sqlite")
        }
        lineage_members = _path_members(staging / CHAT_LINEAGE_SECTOR_ID / "lineage")
        marker_base = {
            "schema": "evidence-lane.sector-operational-authority.v1",
            "state": "CANONICAL_ACTIVE",
            "project_id": project_id,
            "historical_parent_pv": accepted_pv,
            "pointer_generation": pointer_generation,
            "working_identity_sha256": identity_before["working_identity_sha256"],
            "pointer_moved": False,
            "candidate_created": False,
            "hil_inferred": False,
        }
        atomic_write_json(
            plan_stage / OPERATIONAL_AUTHORITY_MARKER,
            {**marker_base, "authority": "PLAN", "canonical_files": plan_files},
        )
        atomic_write_json(
            staging / CHAT_LINEAGE_SECTOR_ID / OPERATIONAL_AUTHORITY_MARKER,
            {
                **marker_base,
                "authority": "CHAT_LINEAGE",
                "lineage_member_count": len(lineage_members),
                "lineage_manifest_sha256": sha256_bytes(
                    canonical_json_bytes(lineage_members)
                ),
            },
        )
        preliminary_receipt = {
            "schema": "evidence-lane.working-sector-migration.v1",
            "migration_id": migration_id,
            "project_id": project_id,
            "historical_parent_pv": accepted_pv,
            "pointer_generation": pointer_generation,
            "working_identity": identity_before,
            "all_18_sectors_materialized": True,
            "historical_lanes_replayed": False,
            "historical_parent_copied_once": True,
            "content_delta_path_count": len(content_paths),
            "complete_dirty_path_count": len(delta_rows),
            "working_delta_inventory": inventory_report,
            "changed_lane_ids": sorted(
                {str(row["lane_id"]) for row in delta_rows}
                | {PLAN_SECTOR_ID, CHAT_LINEAGE_SECTOR_ID}
            ),
            "plan_sector_owned": True,
            "chat_lineage_sector_owned": True,
            "candidate_created": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }
        atomic_write_json(
            staging / "working_migration_receipt.json", preliminary_receipt
        )
        _refresh_lane_bundle_checksums(staging)
        final_validation = validate_lane_bundle(staging)
        require(
            final_validation.get("valid") is True,
            "PROJECT_WORKING_STAGED_AUTHORITY_INVALID",
            "The complete staged sector authority failed final validation.",
            status="FAIL",
        )
        identity_after_build = _working_repository_identity(repository)
        require(
            identity_after_build == identity_before,
            "PROJECT_WORKING_IDENTITY_CHANGED_DURING_MIGRATION",
            "The exact dirty workspace changed during sector migration.",
            status="MISMATCH",
        )
        require(
            sha256_file(pointer_path) == pointer_before,
            "PROJECT_WORKING_POINTER_CHANGED_DURING_MIGRATION",
            "The accepted pointer changed during working-sector migration.",
            status="MISMATCH",
        )

        if active_sectors_existed:
            os.replace(active_sectors, backup)
        os.replace(staging, active_sectors)
        swapped = True

        receipt_moves = (
            (root / "sdk" / "replay", root / "receipts" / "sdk-replay"),
            (
                root / "active_contract_rebindings",
                root / "receipts" / "active-contract-rebindings",
            ),
        )
        for source, destination in receipt_moves:
            if not source.exists():
                continue
            if destination.exists():
                destination_members = _path_members(destination)
                if not destination_members and destination.is_dir():
                    destination.rmdir()
                else:
                    require(
                        _path_members(source) == destination_members,
                        "PROJECT_WORKING_RECEIPT_RELOCATION_CONFLICT",
                        "A project receipt destination exists with different content.",
                        status="MISMATCH",
                        source=str(source),
                        destination=str(destination),
                    )
            if not destination.exists():
                copy_reports.append(_copy_authority_path(source, destination))

        lineage_receipt_root = root / "receipts" / "chat-lineage-runtime"
        for member in sorted(lineage_source.iterdir()):
            if member.name in {"chat_lineage.sqlite", "chat_lineage_head.json"}:
                continue
            destination = lineage_receipt_root / member.name
            if not destination.exists():
                copy_reports.append(_copy_authority_path(member, destination))

        cleanup_targets = [
            *_LEGACY_PLAN_PATHS,
            *_LEGACY_REFERENCE_PATHS,
            "lineage",
            "snapshots",
            "sdk",
            "active_contract_rebindings",
        ]
        for relative in cleanup_targets:
            _remove_exact_migration_target(root, relative)
        if backup.exists():
            shutil.rmtree(backup)

        require(
            identity_after_build == identity_before
            and sha256_file(pointer_path) == pointer_before,
            "PROJECT_WORKING_POST_COMMIT_IDENTITY_MISMATCH",
            "The workspace or accepted pointer changed while sector authority committed.",
            status="MISMATCH",
        )
        receipt_body = {
            **preliminary_receipt,
            "state": "COMMITTED",
            "commit_kind": (
                "WORKING_AUTHORITY_REFRESH"
                if refreshing_existing
                else "INITIAL_WORKING_AUTHORITY_MIGRATION"
            ),
            "sector_bundle_sha256": final_validation.get("bundle_sha256"),
            "copy_reports": copy_reports,
            "removed_legacy_targets": cleanup_targets,
            "accepted_storage_changed": False,
            "accepted_directory_purged": False,
            "completed_at": utc_now(),
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        atomic_write_json(
            root / "receipts" / "project-authority" / "working-sector-migration.json",
            receipt,
        )
        return {
            "status": "PASS",
            "state": (
                "WORKING_SECTOR_AUTHORITY_REFRESHED"
                if refreshing_existing
                else "WORKING_SECTOR_AUTHORITY_COMMITTED"
            ),
            "migration_id": migration_id,
            "receipt_sha256": receipt["receipt_sha256"],
            "working_identity": identity_after_build,
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "plan_authority_root": str(plan_sector_root(root)),
            "chat_lineage_authority_root": str(chat_lineage_sector_root(root)),
            "root_plan_duplicates_present": any(
                (root / name).exists() for name in _LEGACY_PLAN_PATHS
            ),
            "root_chat_lineage_duplicate_present": (root / "lineage").exists(),
            "pointer_moved": False,
            "candidate_created": False,
            "hil_inferred": False,
        }
    except Exception:
        if swapped:
            failed = root / f".sectors-working-{migration_id}.failed"
            if active_sectors.exists():
                os.replace(active_sectors, failed)
            if backup.exists():
                os.replace(backup, active_sectors)
        if staging.exists():
            shutil.rmtree(staging)
        raise


def query_working_project_sectors(
    project_root: str | Path,
    *,
    repository_root: str | Path,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    query: str,
    lane_ids: list[str] | tuple[str, ...],
    limit: int = 8,
    expected_branch: str | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    """Refresh exact WORKING authority and return bounded lane/Study-Brain hits."""

    require(
        1 <= int(limit) <= 20,
        "PROJECT_WORKING_QUERY_LIMIT_INVALID",
        "Working-sector retrieval is bounded to at most twenty hits.",
        status="BLOCKED",
        limit=limit,
    )
    terms = [token.casefold() for token in _WORKING_QUERY_TOKEN_RE.findall(query)][:12]
    require(
        bool(terms),
        "PROJECT_WORKING_QUERY_EMPTY",
        "Working-sector retrieval requires at least one bounded lexical term.",
        status="BLOCKED",
    )
    exact_lanes = list(dict.fromkeys(str(value).strip() for value in lane_ids))
    require(
        bool(exact_lanes)
        and len(exact_lanes) <= len(CANONICAL_LANE_IDS)
        and all(value in CANONICAL_LANE_IDS for value in exact_lanes),
        "PROJECT_WORKING_QUERY_LANE_INVALID",
        "Working-sector retrieval accepts only canonical lane IDs.",
        status="BLOCKED",
        lane_ids=exact_lanes,
    )
    refresh = migrate_working_project_sectors(
        project_root,
        repository_root=repository_root,
        project_id=project_id,
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
        expected_branch=expected_branch,
        expected_head=expected_head,
    )
    root = Path(project_root).resolve()
    fts = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
    hits: list[dict[str, Any]] = []
    study_brains: list[dict[str, Any]] = []
    per_lane_limit = min(int(limit), 8)
    for lane_id in exact_lanes:
        lane = LANE_REGISTRY[lane_id]
        lane_root = root / "sectors" / lane_id
        database_path = lane_root / lane.sqlite_filename
        require(
            database_path.is_file(),
            "PROJECT_WORKING_QUERY_DATABASE_MISSING",
            "A selected WORKING sector has no canonical SQLite projection.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        connection = sqlite3.connect(
            f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            rows = connection.execute(
                (
                    "SELECT CAST(chunk_id AS INTEGER), path, locator, "
                    f"snippet({lane.fts_table}, 2, '[', ']', ' ... ', 24), "
                    f"bm25({lane.fts_table}) FROM {lane.fts_table} "
                    f"WHERE {lane.fts_table} MATCH ? "
                    "ORDER BY 5, path, locator LIMIT ?"
                ),
                (fts, per_lane_limit),
            ).fetchall()
        finally:
            connection.close()
        for chunk_id, path, locator, snippet, rank in rows:
            hits.append(
                {
                    "lane_id": lane_id,
                    "ref_id": f"working:{lane_id}:chunk:{chunk_id}",
                    "path": path,
                    "locator": locator,
                    "snippet": str(snippet)[:1000],
                    "rank": rank,
                }
            )
        profile = json.loads(
            (lane_root / "study_brain.json").read_text(encoding="utf-8")
        )
        study_brains.append(
            {
                "profile_id": profile["profile_id"],
                "lane_id": lane_id,
                "state": profile["state"],
            }
        )
    bounded_hits = sorted(
        hits,
        key=lambda row: (float(row["rank"]), row["lane_id"], row["path"]),
    )[: int(limit)]
    identity = _working_repository_identity(Path(repository_root).resolve())
    return {
        "status": "PASS",
        "schema": "evidence-lane.working-sector-query.v1",
        "authority": "LIVE_DIRTY_WORKSPACE_AND_INDEX",
        "project_id": project_id,
        "historical_parent_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "working_identity": identity,
        "refresh": refresh,
        "terms": terms,
        "queried_lane_ids": exact_lanes,
        "hits": bounded_hits,
        "hit_count": len(bounded_hits),
        "bounded_result_limit": int(limit),
        "result_truncated": len(hits) > int(limit),
        "lane_study_brains": study_brains,
        "raw_plan_loaded": False,
        "raw_pv_loaded": False,
        "raw_chat_lineage_loaded": False,
        "full_source_payload_loaded": False,
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }


def remove_verified_active_source(
    source_root: str | Path,
    *,
    copied_roots: list[str],
) -> None:
    """Remove only byte-verified active roots after the route has switched."""

    source = Path(source_root).resolve()
    for relative in copied_roots:
        target = (source / relative).resolve()
        require(
            _is_relative_to(target, source) and target != source,
            "PROJECT_AUTHORITY_SOURCE_CLEANUP_ESCAPE",
            "A project authority cleanup target escaped the exact legacy root.",
            status="FAIL",
            target=str(target),
        )
        if target.is_dir():
            shutil.rmtree(target)
        elif target.is_file():
            target.unlink()
