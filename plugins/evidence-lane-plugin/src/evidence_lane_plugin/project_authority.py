"""User-owned project authority layout and bounded legacy relocation helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .git_adapter import calculate_worktree_change_identity, inspect_repository
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .lane_engine import build_lane_bundle, validate_lane_bundle
from .lane_traversal import validate_lane_query_traversal
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
_WINDOWS_TRANSIENT_PATH_WINERRORS = frozenset({5, 32, 33})
_WINDOWS_PATH_RETRY_ATTEMPTS = 12
_WINDOWS_PATH_RETRY_BASE_SECONDS = 0.05

PLAN_SECTOR_ID = "plan"
CHAT_LINEAGE_SECTOR_ID = "chat_lineage"
OPERATIONAL_AUTHORITY_MARKER = ".operational-authority.json"

_OPERATIONAL_SECTOR_MEMBER_SELECTORS = {
    "PLAN": (
        "plan/task_backlog.json",
        "plan/plan_runtime_projection.sqlite",
        "plan/plan_atomic_insertions/",
        "plan/plan_normalization/",
        "plan/plan_runtime_refreshes/",
    ),
    "CHAT_LINEAGE": (
        "chat_lineage/.operational-authority.json",
        "chat_lineage/.chat-lineage-writer.lock",
        "chat_lineage/chat_lineage.sqlite",
        "chat_lineage/chat_lineage_head.json",
        "chat_lineage/codex_turn_control.sqlite",
        "chat_lineage/session_*",
        # Installed versions before the direct-sector route placed the same
        # operational members in one redundant nested directory.  Retain this
        # selector only for bounded migration/removal from the checksum set.
        "chat_lineage/lineage/",
    ),
}
_OPERATIONAL_SECTOR_CHECKSUM_LOCKS: dict[str, threading.RLock] = {}
_OPERATIONAL_SECTOR_CHECKSUM_LOCK_GUARD = threading.Lock()

_LEGACY_PLAN_PATHS = (
    "task_backlog.json",
    "plan_runtime_projection.sqlite",
    "plan_atomic_insertions",
    "plan_normalization",
    "plan_runtime_refreshes",
)
_LEGACY_REFERENCE_PATHS = ("plan", "chat_lineage")


def initialize_project_authority_database(database: str | Path) -> None:
    """Create the sole SQLite authority for project/root/workspace/pointer state."""

    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS project_registration(
                project_id TEXT PRIMARY KEY,
                project_root TEXT NOT NULL,
                project_root_identity_sha256 TEXT NOT NULL,
                registration_state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                registered_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS workspace_binding(
                binding_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                host_task_id TEXT NOT NULL,
                workspace_root TEXT NOT NULL,
                repository_identity_sha256 TEXT,
                branch TEXT,
                commit_sha TEXT,
                tree_sha TEXT,
                dirty_identity_sha256 TEXT,
                state TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS project_pointer_history(
                generation INTEGER PRIMARY KEY,
                project_id TEXT NOT NULL,
                accepted_pv TEXT,
                accepted_manifest_sha256 TEXT,
                prior_generation INTEGER,
                movement_kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS project_authority_member(
                member_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                authority_kind TEXT NOT NULL,
                authority_id TEXT NOT NULL,
                sqlite_path TEXT,
                mmd_path TEXT NOT NULL,
                dot_path TEXT NOT NULL,
                tools_path TEXT NOT NULL,
                authority_sha256 TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(project_id, authority_kind, authority_id)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS project_authority_migration_receipt(
                sequence INTEGER PRIMARY KEY,
                migration_id TEXT NOT NULL UNIQUE,
                from_layout_sha256 TEXT NOT NULL,
                to_layout_sha256 TEXT NOT NULL,
                migrated_member_count INTEGER NOT NULL,
                removed_redundant_file_count INTEGER NOT NULL,
                source_bytes_preserved INTEGER NOT NULL
                    CHECK(source_bytes_preserved IN (0,1)),
                receipt_json TEXT NOT NULL,
                receipt_sha256 TEXT NOT NULL UNIQUE,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE VIRTUAL TABLE IF NOT EXISTS project_authority_fts USING fts5(
                record_id UNINDEXED,
                record_kind,
                project_id,
                authority_id,
                payload_text,
                tokenize='unicode61'
            );
            CREATE INDEX IF NOT EXISTS workspace_binding_project_task_idx
            ON workspace_binding(project_id, host_task_id, recorded_at);
            CREATE INDEX IF NOT EXISTS project_authority_member_kind_idx
            ON project_authority_member(project_id, authority_kind, authority_id);
            """
        )
        connection.commit()
    finally:
        connection.close()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _operational_selector_matches(relative_path: str, selector: str) -> bool:
    if selector.endswith("/"):
        return relative_path.startswith(selector)
    if selector.endswith("*"):
        return relative_path.startswith(selector[:-1])
    return relative_path == selector


def is_working_sector_operational_member(relative_path: str) -> bool:
    """Return whether a wrapper member is a mutable Plan/ChatLineage sidecar."""

    normalized = str(relative_path or "").replace("\\", "/").lstrip("/")
    return any(
        _operational_selector_matches(normalized, selector)
        for selectors in _OPERATIONAL_SECTOR_MEMBER_SELECTORS.values()
        for selector in selectors
    )


def _chat_lineage_operational_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    exact = {
        ".chat-lineage-writer.lock",
        "chat_lineage.sqlite",
        "chat_lineage_head.json",
        "codex_turn_control.sqlite",
    }
    return tuple(
        path
        for path in sorted(root.iterdir(), key=lambda value: value.name)
        if path.is_file()
        and (
            path.name in exact
            or (
                path.name.startswith("session_")
                and path.suffix.lower() in {".jsonl", ".sqlite"}
            )
        )
    )


def _is_transient_windows_path_error(exc: OSError) -> bool:
    """Return whether Windows reported a temporary sharing/access conflict."""

    return bool(
        os.name == "nt"
        and (
            isinstance(exc, PermissionError)
            or getattr(exc, "winerror", None) in _WINDOWS_TRANSIENT_PATH_WINERRORS
        )
    )


def _replace_path_with_retry(
    source: Path,
    destination: Path,
    *,
    operation: str,
) -> dict[str, Any]:
    """Replace one exact path, tolerating only bounded Windows sharing races."""

    for attempt in range(1, _WINDOWS_PATH_RETRY_ATTEMPTS + 1):
        try:
            os.replace(source, destination)
            return {
                "operation": operation,
                "attempt_count": attempt,
                "transient_retry_count": attempt - 1,
                "source": str(source),
                "destination": str(destination),
            }
        except OSError as exc:
            transient = _is_transient_windows_path_error(exc)
            if not transient:
                raise
            if attempt == _WINDOWS_PATH_RETRY_ATTEMPTS:
                raise EvidenceLaneError(
                    "PROJECT_WORKING_WINDOWS_PATH_REPLACE_BLOCKED",
                    "Windows kept one exact project-authority path open through the bounded replace window.",
                    status="BLOCKED",
                    details={
                        "operation": operation,
                        "attempt_count": attempt,
                        "source": str(source),
                        "destination": str(destination),
                        "exception_type": type(exc).__name__,
                        "errno": exc.errno,
                        "winerror": getattr(exc, "winerror", None),
                    },
                ) from exc
            time.sleep(
                min(
                    _WINDOWS_PATH_RETRY_BASE_SECONDS * attempt,
                    0.5,
                )
            )
    raise AssertionError("unreachable")


def _remove_tree_with_retry(path: Path, *, operation: str) -> dict[str, Any]:
    """Remove one exact tree with the same bounded Windows sharing policy."""

    for attempt in range(1, _WINDOWS_PATH_RETRY_ATTEMPTS + 1):
        try:
            shutil.rmtree(path)
            return {
                "operation": operation,
                "attempt_count": attempt,
                "transient_retry_count": attempt - 1,
                "path": str(path),
            }
        except OSError as exc:
            transient = _is_transient_windows_path_error(exc)
            if not transient:
                raise
            if attempt == _WINDOWS_PATH_RETRY_ATTEMPTS:
                raise EvidenceLaneError(
                    "PROJECT_WORKING_WINDOWS_TREE_REMOVE_BLOCKED",
                    "Windows kept one exact non-authoritative migration tree open through the bounded cleanup window.",
                    status="BLOCKED",
                    details={
                        "operation": operation,
                        "attempt_count": attempt,
                        "path": str(path),
                        "exception_type": type(exc).__name__,
                        "errno": exc.errno,
                        "winerror": getattr(exc, "winerror", None),
                    },
                ) from exc
            time.sleep(
                min(
                    _WINDOWS_PATH_RETRY_BASE_SECONDS * attempt,
                    0.5,
                )
            )
    raise AssertionError("unreachable")


def _unlink_file_with_retry(path: Path, *, operation: str) -> dict[str, Any]:
    """Unlink one exact duplicate file with bounded Windows sharing retries."""

    for attempt in range(1, _WINDOWS_PATH_RETRY_ATTEMPTS + 1):
        try:
            path.unlink()
            return {
                "operation": operation,
                "attempt_count": attempt,
                "transient_retry_count": attempt - 1,
                "path": str(path),
            }
        except OSError as exc:
            transient = _is_transient_windows_path_error(exc)
            if not transient:
                raise
            if attempt == _WINDOWS_PATH_RETRY_ATTEMPTS:
                raise EvidenceLaneError(
                    "PROJECT_WORKING_WINDOWS_FILE_REMOVE_BLOCKED",
                    "Windows kept one exact duplicate authority file open through the bounded cleanup window.",
                    status="BLOCKED",
                    details={
                        "operation": operation,
                        "attempt_count": attempt,
                        "path": str(path),
                        "exception_type": type(exc).__name__,
                        "errno": exc.errno,
                        "winerror": getattr(exc, "winerror", None),
                    },
                ) from exc
            time.sleep(min(_WINDOWS_PATH_RETRY_BASE_SECONDS * attempt, 0.5))
    raise AssertionError("unreachable")


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
        return sector
    return root / "lineage"


def reconcile_chat_lineage_operational_layout(
    project_root: str | Path,
) -> dict[str, Any]:
    """Move only proven ChatLineage sidecars into the direct sector root.

    The semantic ``chat_lineage_sector_v001.sqlite`` lane remains untouched.
    Byte-identical duplicates are retired; differing collisions fail closed.
    No Plan, PV, HIL, candidate, or accepted pointer state is changed.
    """

    root = Path(project_root).resolve()
    sector = chat_lineage_sector_root(root)
    require(
        _sector_operational_authority_ready(sector),
        "PROJECT_CHAT_LINEAGE_SECTOR_AUTHORITY_REQUIRED",
        "Direct ChatLineage layout requires the active sector-owned authority.",
        status="MISMATCH",
        sector_root=str(sector),
    )
    legacy_roots = tuple(
        candidate
        for candidate in (sector / "lineage", root / "lineage")
        if candidate.is_dir()
    )
    moved: list[dict[str, Any]] = []
    deduplicated: list[dict[str, Any]] = []
    cleanup: list[dict[str, Any]] = []
    for legacy_root in legacy_roots:
        recognized = set(_chat_lineage_operational_files(legacy_root))
        unrecognized = [
            path
            for path in legacy_root.rglob("*")
            if path.is_file() and path not in recognized
        ]
        require(
            not unrecognized,
            "PROJECT_CHAT_LINEAGE_LEGACY_MEMBER_UNRECOGNIZED",
            "The legacy ChatLineage directory contains a non-operational member.",
            status="MISMATCH",
            legacy_root=str(legacy_root),
            unrecognized_members=[
                path.relative_to(legacy_root).as_posix() for path in unrecognized
            ],
        )
        for source in sorted(recognized, key=lambda value: value.name):
            destination = sector / source.name
            if destination.is_file():
                source_sha256 = sha256_file(source)
                destination_sha256 = sha256_file(destination)
                require(
                    source_sha256 == destination_sha256,
                    "PROJECT_CHAT_LINEAGE_DIRECT_COLLISION_MISMATCH",
                    "Legacy and direct ChatLineage members differ; neither was overwritten.",
                    status="MISMATCH",
                    source=str(source),
                    destination=str(destination),
                    source_sha256=source_sha256,
                    destination_sha256=destination_sha256,
                )
                report = _unlink_file_with_retry(
                    source,
                    operation="RETIRE_BYTE_IDENTICAL_CHAT_LINEAGE_DUPLICATE",
                )
                deduplicated.append(
                    {**report, "sha256": source_sha256, "destination": str(destination)}
                )
            else:
                report = _replace_path_with_retry(
                    source,
                    destination,
                    operation="MOVE_CHAT_LINEAGE_MEMBER_TO_DIRECT_SECTOR",
                )
                moved.append({**report, "sha256": sha256_file(destination)})
        require(
            not any(legacy_root.iterdir()),
            "PROJECT_CHAT_LINEAGE_LEGACY_DIRECTORY_NOT_EMPTY",
            "The bounded ChatLineage migration left unexpected legacy members.",
            status="MISMATCH",
            legacy_root=str(legacy_root),
        )
        cleanup.append(
            _remove_tree_with_retry(
                legacy_root,
                operation="RETIRE_EMPTY_CHAT_LINEAGE_LEGACY_DIRECTORY",
            )
        )

    members = {
        path.name: sha256_file(path)
        for path in _chat_lineage_operational_files(sector)
    }
    require(
        "chat_lineage.sqlite" in members
        and "chat_lineage_head.json" in members,
        "PROJECT_CHAT_LINEAGE_DIRECT_CANONICAL_FILES_MISSING",
        "The direct ChatLineage SQLite and head are required after migration.",
        status="MISMATCH",
        direct_members=sorted(members),
    )
    marker_path = sector / OPERATIONAL_AUTHORITY_MARKER
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker.update(
        {
            "layout": "DIRECT_SECTOR_ROOT",
            "lineage_member_count": len(members),
            "lineage_manifest_sha256": sha256_bytes(canonical_json_bytes(members)),
        }
    )
    atomic_write_json(marker_path, marker)
    checksum_refresh = refresh_working_sector_operational_checksums(
        root,
        authority="CHAT_LINEAGE",
    )
    receipt = {
        "schema": "evidence-lane.chat-lineage-operational-layout.v1",
        "status": "PASS",
        "state": "DIRECT_SECTOR_ROOT",
        "project_root": str(root),
        "canonical_root": str(sector),
        "legacy_root_count": len(legacy_roots),
        "moved": moved,
        "deduplicated": deduplicated,
        "cleanup": cleanup,
        "member_count": len(members),
        "member_manifest_sha256": sha256_bytes(canonical_json_bytes(members)),
        "checksum_refresh": checksum_refresh,
        "pointer_moved": False,
        "candidate_created": False,
        "hil_inferred": False,
        "recorded_at": utc_now(),
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    receipt_path = (
        root
        / "receipts"
        / "project-authority"
        / "chat-lineage-operational-layout.json"
    )
    atomic_write_json(receipt_path, receipt)
    return {**receipt, "receipt_path": str(receipt_path)}


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
        "accepted_pv_identity": accepted_pv,
        "accepted_folder_queried": False,
        "accepted_archive_copied": False,
    }


def _lane_reference(
    project_root: Path,
    *,
    lane_id: str,
    accepted_pv: str,
    emitted_lane_ids: set[str],
) -> dict[str, Any]:
    lane = LANE_REGISTRY[lane_id]
    live_relative = f"sectors/{lane_id}"
    live_lane_root = project_root / live_relative
    live_database = live_lane_root / lane.sqlite_filename
    populated = lane_id in emitted_lane_ids and live_database.is_file()
    schema = lane_schema_asset(lane_id)
    artifacts = lane_artifact_contract(lane_id)
    return {
        "schema": "evidence-lane.project-sector-reference.v1",
        "lane_id": lane_id,
        "display_label": lane.display_label,
        "state": "LIVE_WORKING" if populated else "SCHEMA_READY_UNPOPULATED",
        "historical_parent_pv": accepted_pv,
        "authority_relative_path": live_relative,
        "database_relative_path": f"{live_relative}/{lane.sqlite_filename}",
        "authority_path_exists": live_lane_root.is_dir(),
        "database_path_exists": live_database.is_file(),
        "lane_schema_id": schema["schema_id"],
        "lane_schema_version": schema["schema_version"],
        "lane_schema_contract_sha256": schema["contract_sha256"],
        "artifact_contract_sha256": artifacts["contract_sha256"],
        "empty_lane_payload_fabricated": False,
        "independent_project_truth_authority": True,
        "accepted_archive_queried": False,
    }


def _accepted_lane_history_reference(
    project_root: Path,
    *,
    project_id: str,
    lane_id: str,
    accepted_pv: str,
) -> dict[str, Any]:
    """Bind a live lane to its root-nested immutable PV history copy."""

    lane = LANE_REGISTRY[lane_id]
    lane_relative = f"sectors/{lane_id}/accepted_history/{accepted_pv}"
    lane_root = project_root / Path(lane_relative)
    database = lane_root / lane.sqlite_filename
    manifest = lane_root / "lane_manifest.json"
    materialized = lane_root.is_dir()
    if not materialized:
        return {
            "schema": "evidence-lane.accepted-lane-history-reference.v1",
            "state": "SCHEMA_READY_UNPOPULATED",
            "project_id": project_id,
            "lane_id": lane_id,
            "accepted_pv": accepted_pv,
            "authority_relative_path": None,
            "database_relative_path": None,
            "database_sha256": None,
            "lane_manifest_sha256": None,
            "independent_writable_authority": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
        }
    require(
        database.is_file() and manifest.is_file(),
        "PROJECT_WORKING_ACCEPTED_LANE_MATERIALIZATION_MISSING",
        "A declared accepted historical lane requires its database and sealed manifest.",
        status="MISMATCH",
        lane_id=lane_id,
        accepted_pv=accepted_pv,
    )
    lane_manifest = json.loads(manifest.read_text(encoding="utf-8"))
    expected_database_sha256 = str(
        dict(lane_manifest.get("stable_artifacts") or {}).get(lane.sqlite_filename)
        or ""
    )
    actual_database_sha256 = sha256_file(database)
    require(
        expected_database_sha256 == actual_database_sha256,
        "PROJECT_WORKING_ACCEPTED_LANE_DATABASE_HASH_MISMATCH",
        "The accepted historical lane database differs from its sealed manifest.",
        status="MISMATCH",
        lane_id=lane_id,
        accepted_pv=accepted_pv,
    )
    return {
        "schema": "evidence-lane.accepted-lane-history-reference.v1",
        "state": "IMMUTABLE_ACCEPTED_HISTORY",
        "project_id": project_id,
        "lane_id": lane_id,
        "accepted_pv": accepted_pv,
        "authority_relative_path": lane_relative,
        "database_relative_path": (
            f"{lane_relative}/{lane.sqlite_filename}"
        ),
        "database_sha256": actual_database_sha256,
        "lane_manifest_sha256": sha256_file(manifest),
        "independent_writable_authority": False,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
    }


def _preserve_root_nested_lane_histories(
    source_sectors: Path | None,
    staged_sectors: Path,
) -> list[dict[str, Any]]:
    """Carry immutable root-nested PV histories across a live-sector refresh.

    The source is the current project-root sector projection, never the
    accepted archive.  These bytes are historical support material inside the
    live authority and must survive an otherwise complete working rebuild.
    """

    if source_sectors is None or not source_sectors.is_dir():
        return []
    reports: list[dict[str, Any]] = []
    for lane_id in CANONICAL_LANE_IDS:
        source = source_sectors / lane_id / "accepted_history"
        if not source.is_dir():
            continue
        target = staged_sectors / lane_id / "accepted_history"
        require(
            not target.exists(),
            "PROJECT_WORKING_ROOT_HISTORY_TARGET_EXISTS",
            "A staged lane unexpectedly already contains root-nested PV history.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        source_members = _path_members(source)
        shutil.copytree(source, target, copy_function=shutil.copy2)
        target_members = _path_members(target)
        require(
            source_members == target_members,
            "PROJECT_WORKING_ROOT_HISTORY_COPY_MISMATCH",
            "Root-nested immutable PV history changed during working refresh.",
            status="FAIL",
            lane_id=lane_id,
        )
        reports.append(
            {
                "lane_id": lane_id,
                "source_relative_path": f"sectors/{lane_id}/accepted_history",
                "target_relative_path": f"sectors/{lane_id}/accepted_history",
                "member_count": len(source_members),
                "member_manifest_sha256": sha256_bytes(
                    canonical_json_bytes(source_members)
                ),
                "source_scope": "CURRENT_PROJECT_ROOT_ONLY",
                "accepted_archive_opened": False,
                "accepted_archive_queried": False,
                "exact_byte_parity": True,
            }
        )
    return reports


def _inspect_source_scaffold_retirement(project_root: Path) -> dict[str, Any]:
    """Validate source scaffolds and any prior receipt without changing bytes."""

    source_root = project_root / "sources"
    receipt_path = (
        project_root
        / "receipts"
        / "project-authority"
        / "source-scaffold-retirement.json"
    )
    existing: dict[str, Any] | None = None
    existing_receipt_sha256: str | None = None
    if receipt_path.is_file():
        existing_receipt_sha256 = sha256_file(receipt_path)
        try:
            decoded = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "PROJECT_SOURCE_SCAFFOLD_RECEIPT_MISMATCH",
                "The existing source-scaffold receipt is unreadable.",
                status="MISMATCH",
                details={"receipt_path": str(receipt_path)},
            ) from exc
        require(
            isinstance(decoded, dict),
            "PROJECT_SOURCE_SCAFFOLD_RECEIPT_MISMATCH",
            "The existing source-scaffold receipt must be a JSON object.",
            status="MISMATCH",
        )
        existing = decoded
        existing_body = {
            key: value for key, value in existing.items() if key != "receipt_sha256"
        }
        existing_rows = list(existing.get("rows") or [])
        route_is_current = bool(
            existing.get("active_source_authority")
            == "sources/source_authority.sqlite"
            and existing.get("sources_namespace_authoritative") is True
        )
        route_is_retired_legacy = bool(
            existing.get("active_source_authority") == "source_authority.sqlite"
            and existing.get("sources_namespace_authoritative") is False
        )
        require(
            existing.get("schema")
            == "evidence-lane.source-scaffold-retirement.v1"
            and (route_is_current or route_is_retired_legacy)
            and isinstance(existing.get("recorded_at"), str)
            and existing.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(existing_body))
            and len(existing_rows) == 2
            and all(isinstance(row, dict) for row in existing_rows)
            and {str(row.get("path") or "") for row in existing_rows}
            == {"sources/objects", "sources/source_manifests"}
            and all(
                str(row.get("state") or "")
                in {"ABSENT", "PRESERVED_NONEMPTY", "RETIRED_EMPTY"}
                for row in existing_rows
                if isinstance(row, dict)
            ),
            "PROJECT_SOURCE_SCAFFOLD_RECEIPT_MISMATCH",
            "The existing source-scaffold receipt is not valid authority.",
            status="MISMATCH",
        )

    rows: list[dict[str, Any]] = []
    empty_paths: list[str] = []
    for name in ("objects", "source_manifests"):
        path = source_root / name
        if not path.exists():
            rows.append({"path": f"sources/{name}", "state": "ABSENT"})
            continue
        require(
            path.is_dir(),
            "PROJECT_SOURCE_SCAFFOLD_PATH_INVALID",
            "A retired source scaffold path must be a directory.",
            status="MISMATCH",
            path=str(path),
        )
        members = list(path.iterdir())
        if members:
            rows.append(
                {
                    "path": f"sources/{name}",
                    "state": "PRESERVED_NONEMPTY",
                    "member_count": len(members),
                }
            )
            continue
        empty_paths.append(name)
        rows.append({"path": f"sources/{name}", "state": "RETIRED_EMPTY"})
    return {
        "existing": existing,
        "existing_receipt_sha256": existing_receipt_sha256,
        "rows": rows,
        "empty_paths": empty_paths,
    }


def _retire_empty_source_scaffolds(
    project_root: Path,
    *,
    preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply one unchanged, fully preflighted empty-scaffold retirement."""

    inspected = _inspect_source_scaffold_retirement(project_root)
    if preflight is not None:
        require(
            inspected == preflight,
            "PROJECT_SOURCE_SCAFFOLD_CHANGED_DURING_RETIREMENT",
            "Source scaffolds or their receipt changed after bounded preflight.",
            status="MISMATCH",
        )
    existing = inspected["existing"]
    rows = list(inspected["rows"])
    source_root = project_root / "sources"
    empty_paths = [source_root / name for name in inspected["empty_paths"]]
    stable_receipt_body = {
        "schema": "evidence-lane.source-scaffold-retirement.v1",
        "active_source_authority": "sources/source_authority.sqlite",
        "sources_namespace_authoritative": True,
        "rows": rows,
    }
    for path in empty_paths:
        require(
            path.is_dir() and not any(path.iterdir()),
            "PROJECT_SOURCE_SCAFFOLD_CHANGED_DURING_RETIREMENT",
            "A source scaffold changed after the bounded retirement inspection.",
            status="MISMATCH",
            path=str(path),
        )
    for path in empty_paths:
        path.rmdir()
    if isinstance(existing, dict):
        existing_rows = {
            str(row.get("path")): row
            for row in existing.get("rows") or []
            if isinstance(row, dict)
        }
        current_rows = {str(row["path"]): row for row in rows}
        rows_equivalent = existing_rows == current_rows
        if existing_rows.keys() == current_rows.keys():
            rows_equivalent = all(
                existing_rows[path] == current_rows[path]
                or (
                    existing_rows[path].get("state") == "RETIRED_EMPTY"
                    and current_rows[path].get("state") == "ABSENT"
                )
                for path in current_rows
            )
        existing_stable_body = {
            key: existing.get(key)
            for key in stable_receipt_body
            if key != "rows"
        }
        current_stable_body = {
            key: value
            for key, value in stable_receipt_body.items()
            if key != "rows"
        }
        existing_receipt_body = {
            key: value
            for key, value in existing.items()
            if key != "receipt_sha256"
        }
        if (
            existing_stable_body == current_stable_body
            and rows_equivalent
            and isinstance(existing.get("recorded_at"), str)
            and existing.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(existing_receipt_body))
        ):
            return existing
    receipt_body = {**stable_receipt_body, "recorded_at": utc_now()}
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    receipt_path = (
        project_root
        / "receipts"
        / "project-authority"
        / "source-scaffold-retirement.json"
    )
    atomic_write_json(receipt_path, receipt)
    return receipt


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
    lane_manifest_path = root / "sectors" / "manifest.json"
    if lane_manifest_path.is_file():
        lane_manifest = json.loads(lane_manifest_path.read_text(encoding="utf-8"))
    else:
        emitted_lane_ids = [
            lane_id
            for lane_id in CANONICAL_LANE_IDS
            if (root / "sectors" / lane_id / LANE_REGISTRY[lane_id].sqlite_filename).is_file()
        ]
        lane_manifest = {
            "schema": "evidence-lane.live-sector-registry-bootstrap.v1",
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "emitted_lane_ids": emitted_lane_ids,
            "lane_registry_derived": True,
            "accepted_folder_queried": False,
            "accepted_archive_used_as_authority": False,
            "empty_lane_payload_fabricated": False,
        }
        atomic_write_json(lane_manifest_path, lane_manifest)
    emitted = {str(value) for value in lane_manifest.get("emitted_lane_ids") or []}
    require(
        int(lane_manifest.get("canonical_lane_count") or 0) == len(CANONICAL_LANE_IDS)
        and emitted <= set(CANONICAL_LANE_IDS),
        "PROJECT_AUTHORITY_ACCEPTED_LANE_REGISTRY_MISMATCH",
        "The live lane bundle does not bind the current canonical registry.",
        status="MISMATCH",
    )

    layout_dirs = (
        "accepted",
        "sectors",
        "ai_learning",
        "canon",
        "memory",
        "sources",
        "universe",
        "project_overlay",
        "connector_brain",
        "project_authority",
        "receipts",
        "sessions",
    )
    for relative in layout_dirs:
        (root / relative).mkdir(parents=True, exist_ok=True)

    from .receipt_ledger import initialize_receipt_ledger
    from .session_authority import initialize_session_authority

    project_authority_database = (
        root / "project_authority" / "project-authority.sqlite"
    )
    receipt_database = root / "receipts" / "receipt-ledger.sqlite"
    session_database = root / "sessions" / "session-authority.sqlite"
    initialize_project_authority_database(project_authority_database)
    initialize_receipt_ledger(receipt_database)
    initialize_session_authority(session_database)

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
            "paths": [
                "sectors/plan/plan_sector_v001.sqlite",
                "sectors/plan/plan.mmd",
                "sectors/plan/plan.dot",
                "sectors/plan/tools.json",
            ],
            "sector_authority": True,
        },
        "chat_lineage": {
            "state": "CANONICAL_ACTIVE",
            "paths": [
                "sectors/chat_lineage/chat_lineage_sector_v001.sqlite",
                "sectors/chat_lineage/chat_lineage.mmd",
                "sectors/chat_lineage/chat_lineage.dot",
                "sectors/chat_lineage/tools.json",
            ],
            "sector_authority": True,
        },
        "ai_learning": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": [
                "ai_learning/agent-learning.sqlite",
                "ai_learning/agent-learning.mmd",
                "ai_learning/agent-learning.dot",
                "ai_learning/agent-learning.tools.json",
            ],
        },
        "canon": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": [
                "canon/canon-input.sqlite",
                "canon/canon-input.mmd",
                "canon/canon-input.dot",
                "canon/canon-input.tools.json",
            ],
        },
        "memory": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": [
                "memory/memory.sqlite",
                "memory/memory.mmd",
                "memory/memory.dot",
                "memory/memory.tools.json",
            ],
        },
        "universe": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": [
                "universe/project_universe.sqlite",
                "universe/project_universe.mmd",
                "universe/project_universe.dot",
                "universe/project_universe.tools.json",
            ],
        },
        "project_overlay": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": [
                "project_overlay/project_overlay.sqlite",
                "project_overlay/project_overlay.mmd",
                "project_overlay/project_overlay.dot",
                "project_overlay/project_overlay.tools.json",
            ],
        },
        "sources": {
            "state": "CANONICAL_ACTIVE",
            "paths": [
                "sources/source_authority.sqlite",
                "sources/source_authority.mmd",
                "sources/source_authority.dot",
                "sources/source_authority.tools.json",
            ],
        },
        "connector_brain": {
            "state": "CANONICAL_ACTIVE_OR_SCHEMA_READY",
            "paths": [
                "connector_brain/connector-brain.sqlite",
                "connector_brain/connector_brain.mmd",
                "connector_brain/connector_brain.dot",
                "connector_brain/connector_brain.tools.json",
            ],
        },
        "project_authority": {
            "state": "CANONICAL_ACTIVE",
            "paths": [
                "project_authority/project-authority.sqlite",
                "project_authority/project_authority.mmd",
                "project_authority/project_authority.dot",
                "project_authority/project_authority.tools.json",
            ],
        },
        "receipt_ledger": {
            "state": "CANONICAL_ACTIVE",
            "paths": [
                "receipts/receipt-ledger.sqlite",
                "receipts/receipt-ledger.mmd",
                "receipts/receipt-ledger.dot",
                "receipts/receipt-ledger.tools.json",
            ],
        },
        "session_authority": {
            "state": "CANONICAL_ACTIVE",
            "paths": [
                "sessions/session-authority.sqlite",
                "sessions/session-authority.mmd",
                "sessions/session-authority.dot",
                "sessions/session-authority.tools.json",
            ],
        },
    }
    for authority in named_authorities:
        reference_root = (
            root / "sectors" / authority
            if authority in {PLAN_SECTOR_ID, CHAT_LINEAGE_SECTOR_ID}
            else root / authority
        )
        reference_root.mkdir(parents=True, exist_ok=True)
        reference_root.mkdir(parents=True, exist_ok=True)

    legacy_history = (
        Path(legacy_history_root).resolve() if legacy_history_root is not None else None
    )
    legacy_history_receipt = {
        "schema": "evidence-lane.legacy-project-history-reference.v1",
        "state": "NON_AUTHORITATIVE_HISTORY_PENDING_HIDDEN_RUNTIME_MIGRATION",
        "legacy_history_root": str(legacy_history) if legacy_history else None,
        "accepted_current_local": accepted_pv,
        "candidate_authority": False,
        "accepted_pointer_authority": False,
        "pointer_movement_allowed": False,
    }
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
        "central_code_project_count": 1,
        "additional_code_folders_role": "LANE_SCOPED_STUDY_BRAIN",
        "central_code_project_change_requires_new_project_pv": True,
        "lane_owned_artifacts": [
            "sqlite_fts5",
            "mmd",
            "dot",
            "tools.json",
            "lane_pointer.json",
            "lane_manifest.json",
            "study_brain.json",
        ],
        "project_truth_promotion_allowed": False,
        "raw_payload_duplication_allowed": False,
    }

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
    from .graph_pipeline import SemanticGraph
    from .receipt_ledger import append_receipt
    from .sqlite_indexing import rebuild_connection_authority_index

    authority_connection = sqlite3.connect(project_authority_database, timeout=30)
    try:
        authority_connection.execute("PRAGMA foreign_keys=ON")
        layout_bytes = canonical_json_bytes(layout)
        layout_sha256 = sha256_bytes(layout_bytes)
        registered_at = utc_now()
        project_root_identity_sha256 = sha256_bytes(
            canonical_json_bytes(
                {"project_id": project_id, "project_root": str(published)}
            )
        )
        authority_connection.execute(
            """
            INSERT INTO project_registration(
                project_id,project_root,project_root_identity_sha256,
                registration_state,payload_json,payload_sha256,
                registered_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id) DO UPDATE SET
                project_root=excluded.project_root,
                project_root_identity_sha256=excluded.project_root_identity_sha256,
                registration_state=excluded.registration_state,
                payload_json=excluded.payload_json,
                payload_sha256=excluded.payload_sha256,
                updated_at=excluded.updated_at
            """,
            (
                project_id,
                str(published),
                project_root_identity_sha256,
                "ACTIVE",
                layout_bytes.decode("utf-8"),
                layout_sha256,
                registered_at,
                registered_at,
            ),
        )
        pointer_payload = {
            "project_id": project_id,
            "accepted_pv": accepted_pv,
            "accepted_manifest_sha256": accepted_manifest_sha256,
            "generation": int(pointer_generation),
            "movement_kind": "LAYOUT_BIND_NO_POINTER_MOVE",
        }
        authority_connection.execute(
            """
            INSERT OR IGNORE INTO project_pointer_history(
                generation,project_id,accepted_pv,accepted_manifest_sha256,
                prior_generation,movement_kind,payload_json,payload_sha256,
                recorded_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                int(pointer_generation),
                project_id,
                accepted_pv,
                accepted_manifest_sha256,
                int(pointer_generation) - 1 if int(pointer_generation) > 0 else None,
                "LAYOUT_BIND_NO_POINTER_MOVE",
                canonical_json_bytes(pointer_payload).decode("utf-8"),
                sha256_bytes(canonical_json_bytes(pointer_payload)),
                registered_at,
            ),
        )
        authority_connection.execute(
            "DELETE FROM project_authority_member WHERE project_id=?",
            (project_id,),
        )
        for reference in sector_rows:
            lane_id = str(reference["lane_id"])
            member_payload = canonical_json_bytes(reference)
            authority_connection.execute(
                """
                INSERT INTO project_authority_member(
                    member_id,project_id,authority_kind,authority_id,
                    sqlite_path,mmd_path,dot_path,tools_path,
                    authority_sha256,payload_json,recorded_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"sector:{lane_id}",
                    project_id,
                    "PROJECT_SECTOR",
                    lane_id,
                    str(reference["database_relative_path"]),
                    f"sectors/{lane_id}/{LANE_REGISTRY[lane_id].mmd_filename}",
                    f"sectors/{lane_id}/{LANE_REGISTRY[lane_id].dot_filename}",
                    f"sectors/{lane_id}/tools.json",
                    sha256_bytes(member_payload),
                    member_payload.decode("utf-8"),
                    registered_at,
                ),
            )
        rebuild_connection_authority_index(
            authority_connection,
            authority_id="project_authority",
        )
        authority_connection.commit()
    finally:
        authority_connection.close()

    append_receipt(
        receipt_database,
        logical_path="project-authority/legacy-history-reference.json",
        receipt=legacy_history_receipt,
        receipt_kind="PROJECT_AUTHORITY_MIGRATION",
        project_id=project_id,
    )
    from .authority_support import refresh_authority_support

    receipt_support = refresh_authority_support(root, "receipt_ledger")
    session_support = refresh_authority_support(root, "session_authority")

    authority_graph = SemanticGraph(
        "evidence_lane_project_authority",
        direction="TB",
        role="AUTHORITY_TRAVERSAL",
    )
    for node_id, label, kind in (
        ("PROJECT", "User project authority SQLite", "root"),
        ("RUNTIME", "Hidden plugin runtime", "source"),
        ("SECTORS", "18 typed sector SQLite authorities", "semantic"),
        ("NAMED", "Named SQLite authorities", "semantic"),
        ("PROFILE", "Study Brain routing profile", "retrieval"),
    ):
        authority_graph.add_node(node_id, label, kind)
    authority_graph.add_edge("RUNTIME", "PROJECT", "registry binding")
    authority_graph.add_edge("PROJECT", "SECTORS", "owns")
    authority_graph.add_edge("PROJECT", "NAMED", "owns")
    authority_graph.add_edge("PROFILE", "SECTORS", "routes")
    authority_mmd, authority_dot, graph_pipeline_receipt = (
        authority_graph.render_pair()
    )
    authority_root = root / "project_authority"
    atomic_write_bytes(
        authority_root / "project_authority.mmd", authority_mmd.encode("utf-8")
    )
    atomic_write_bytes(
        authority_root / "project_authority.dot", authority_dot.encode("utf-8")
    )
    atomic_write_json(
        authority_root / "project_authority.tools.json",
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
            "sqlite_authority": "project-authority.sqlite",
            "graph_pipeline_receipt": graph_pipeline_receipt,
            "receipt_support_sha256": receipt_support["receipt_sha256"],
            "session_support_sha256": session_support["receipt_sha256"],
        },
    )
    projected_members = {
        path.relative_to(authority_root).as_posix(): sha256_file(path)
        for path in sorted(authority_root.rglob("*"))
        if path.is_file()
        and path.name != "manifest.json"
    }
    manifest_body = {
        "schema": "evidence-lane.project-authority-projection-manifest.v1",
        "project_id": project_id,
        "member_count": len(projected_members),
        "members": projected_members,
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "layout_sha256": layout["layout_sha256"],
        "graph_pipeline_receipt": graph_pipeline_receipt,
    }
    manifest = {
        **manifest_body,
        "manifest_sha256": sha256_bytes(canonical_json_bytes(manifest_body)),
    }
    atomic_write_json(authority_root / "manifest.json", manifest)
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
        "project_authority_database": str(project_authority_database),
        "receipt_database": str(receipt_database),
        "session_database": str(session_database),
        "legacy_json_reference_files_created": False,
        "accepted_folder_queried": False,
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
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    return completed.stdout


def _working_repository_identity(repository: Path) -> dict[str, Any]:
    """Return bounded hashes for the exact live index/worktree identity."""
    return calculate_worktree_change_identity(repository)


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
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
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
    all_paths = sorted(staged | unstaged | untracked)
    local_only_prefixes = (
        "evidence/",
        ".github-pages-build/",
        ".tmp-flash-check/",
        ".runtime/",
    )
    excluded_paths = [
        relative
        for relative in all_paths
        if relative.casefold().startswith(local_only_prefixes)
    ]
    paths = [relative for relative in all_paths if relative not in excluded_paths]
    excluded_prefix_counts = {
        prefix: sum(
            relative.casefold().startswith(prefix) for relative in excluded_paths
        )
        for prefix in local_only_prefixes
    }
    exclusion_summary = {
        "schema": "evidence-lane.working-local-only-exclusion.v1",
        "excluded_path_count": len(excluded_paths),
        "excluded_prefix_counts": excluded_prefix_counts,
        "excluded_path_set_sha256": sha256_bytes(
            canonical_json_bytes(excluded_paths)
        ),
        "raw_excluded_paths_returned": False,
        "excluded_content_read": False,
    }
    route_overrides: dict[str, str] = {}
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
        exclusion_reason = (
            "UNTRACKED_NOT_GIT_INDEX_AUTHORITY"
            if exists and relative in untracked
            else path_exclusion_reason(relative)
            if exists
            else None
        )
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
    return rows, sorted(content_paths), exclusion_summary


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


def refresh_working_sector_operational_checksums(
    project_root: str | Path,
    *,
    authority: str,
) -> dict[str, Any]:
    """Refresh only mutable Plan or ChatLineage wrapper members.

    Immutable and unchanged lane members reuse their already sealed hashes.  This
    keeps the live sector wrapper valid after an operational authority write
    without re-reading every accepted-history or working-lane byte.
    """

    root = Path(project_root).resolve()
    sectors = root / "sectors"
    manifest_path = sectors / "manifest.json"
    checksums_path = sectors / "SHA256SUMS.json"
    normalized_authority = str(authority or "").strip().upper()
    require(
        normalized_authority in _OPERATIONAL_SECTOR_MEMBER_SELECTORS,
        "PROJECT_OPERATIONAL_CHECKSUM_AUTHORITY_INVALID",
        "Operational checksum refresh requires Plan or ChatLineage authority.",
        status="BLOCKED",
        authority=normalized_authority or None,
    )
    if not manifest_path.is_file() or not checksums_path.is_file():
        return {
            "status": "NOT_APPLICABLE",
            "authority": normalized_authority,
            "reason": "WORKING_SECTOR_WRAPPER_NOT_MATERIALIZED",
            "full_bundle_rehashed": False,
        }

    lock_key = str(sectors)
    with _OPERATIONAL_SECTOR_CHECKSUM_LOCK_GUARD:
        process_lock = _OPERATIONAL_SECTOR_CHECKSUM_LOCKS.setdefault(
            lock_key, threading.RLock()
        )
    with process_lock:
        try:
            checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "PROJECT_OPERATIONAL_CHECKSUM_WRAPPER_INVALID",
                "The working-sector checksum wrapper is unreadable.",
                status="MISMATCH",
                details={"authority": normalized_authority},
            ) from exc
        members = {
            str(path): str(value)
            for path, value in dict(checksums.get("members") or {}).items()
        }
        require(
            checksums.get("schema") == "evidence-lane.recursive-sha256.v1"
            and manifest.get("schema")
            == "evidence-lane.universal-lane-bundle.v2"
            and manifest.get("bundle_sha256")
            == sha256_bytes(canonical_json_bytes(members)),
            "PROJECT_OPERATIONAL_CHECKSUM_WRAPPER_INVALID",
            "The working-sector checksum wrapper is not a valid prior authority.",
            status="MISMATCH",
            authority=normalized_authority,
        )
        previous_members = dict(members)
        selected_paths: set[str] = set()
        for selector in _OPERATIONAL_SECTOR_MEMBER_SELECTORS[normalized_authority]:
            if selector.endswith("/"):
                selected_paths.update(
                    path for path in members if path.startswith(selector)
                )
                directory = sectors / Path(selector.rstrip("/"))
                if directory.is_dir():
                    selected_paths.update(
                        path.relative_to(sectors).as_posix()
                        for path in directory.rglob("*")
                        if path.is_file()
                    )
            elif selector.endswith("*"):
                prefix = selector[:-1]
                selected_paths.update(
                    path for path in members if path.startswith(prefix)
                )
                parent = sectors / Path(prefix).parent
                if parent.is_dir():
                    selected_paths.update(
                        path.relative_to(sectors).as_posix()
                        for path in parent.iterdir()
                        if path.is_file()
                        and path.relative_to(sectors).as_posix().startswith(prefix)
                    )
            else:
                selected_paths.add(selector)
        changed_member_paths: list[str] = []
        removed_member_paths: list[str] = []
        for relative in sorted(selected_paths):
            target = sectors / Path(relative)
            if target.is_file():
                current_sha256 = sha256_file(target)
                if members.get(relative) != current_sha256:
                    changed_member_paths.append(relative)
                members[relative] = current_sha256
            elif relative in members:
                removed_member_paths.append(relative)
                members.pop(relative)
        bundle_sha256 = sha256_bytes(canonical_json_bytes(members))
        atomic_write_json(
            checksums_path,
            {
                "schema": "evidence-lane.recursive-sha256.v1",
                "members": members,
                "member_count": len(members),
            },
        )
        manifest["bundle_sha256"] = bundle_sha256
        manifest["member_count"] = len(members) + 2
        atomic_write_json(manifest_path, manifest)
        unchanged_member_count = sum(
            previous_members.get(path) == value
            for path, value in members.items()
            if path not in selected_paths
        )
        return {
            "status": "PASS",
            "schema": "evidence-lane.operational-sector-checksum-refresh.v1",
            "authority": normalized_authority,
            "selected_member_count": len(selected_paths),
            "changed_member_paths": changed_member_paths,
            "removed_member_paths": removed_member_paths,
            "unchanged_member_hash_count": unchanged_member_count,
            "unchanged_member_hashes_reused": True,
            "full_bundle_rehashed": False,
            "before_bundle_sha256": sha256_bytes(
                canonical_json_bytes(previous_members)
            ),
            "after_bundle_sha256": bundle_sha256,
        }


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


def _load_validated_committed_working_receipt(
    receipt_path: Path,
    *,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    working_identity: dict[str, Any],
    sector_bundle_sha256: str,
    missing_code: str,
    mismatch_code: str,
    migration_id: str | None = None,
    allow_validated_working_successor_bundle: bool = False,
) -> dict[str, Any]:
    """Load one complete COMMITTED receipt without changing any authority."""

    require(
        receipt_path.is_file(),
        missing_code,
        "The external committed working-sector receipt is required.",
        status="MISMATCH",
    )
    try:
        decoded = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceLaneError(
            mismatch_code,
            "The external committed working-sector receipt is unreadable.",
            status="MISMATCH",
            details={"project_id": project_id},
        ) from exc
    require(
        isinstance(decoded, dict),
        mismatch_code,
        "The external committed working-sector receipt must be a JSON object.",
        status="MISMATCH",
        project_id=project_id,
    )
    receipt = decoded
    receipt_body = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    receipt_sector_bundle_sha256 = str(receipt.get("sector_bundle_sha256") or "")
    sector_bundle_binding_valid = bool(
        receipt_sector_bundle_sha256 == sector_bundle_sha256
        or (
            allow_validated_working_successor_bundle
            and re.fullmatch(r"[A-Fa-f0-9]{64}", receipt_sector_bundle_sha256)
            and re.fullmatch(r"[A-Fa-f0-9]{64}", sector_bundle_sha256)
        )
    )
    valid = bool(
        receipt.get("schema") == "evidence-lane.working-sector-migration.v1"
        and receipt.get("state") == "COMMITTED"
        and bool(str(receipt.get("migration_id") or ""))
        and receipt.get("project_id") == project_id
        and receipt.get("historical_parent_pv") == accepted_pv
        and receipt.get("pointer_generation") == pointer_generation
        and receipt.get("working_identity") == working_identity
        and sector_bundle_binding_valid
        and receipt.get("all_18_sectors_materialized") is True
        and receipt.get("candidate_created") is False
        and receipt.get("pointer_moved") is False
        and receipt.get("hil_inferred") is False
        and receipt.get("accepted_storage_changed") is False
        and receipt.get("accepted_directory_purged") is False
        and receipt.get("receipt_sha256")
        == sha256_bytes(canonical_json_bytes(receipt_body))
        and (migration_id is None or receipt.get("migration_id") == migration_id)
    )
    require(
        valid,
        mismatch_code,
        "The external committed working-sector receipt is not bound to this authority.",
        status="MISMATCH",
        project_id=project_id,
    )
    return receipt


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
    stages = sorted(root.glob(".sectors-working-*.staging"))
    if not stages:
        return None
    require(
        len(stages) == 1,
        "PROJECT_WORKING_RECOVERY_STAGE_AMBIGUOUS",
        "Recovery requires exactly one classifiable interrupted sector stage.",
        status="BLOCKED",
        staged_paths=[str(path) for path in stages],
    )
    staging = stages[0]
    receipt_path = staging / "working_migration_receipt.json"
    require(
        staging.is_dir() and receipt_path.is_file(),
        "PROJECT_WORKING_RECOVERY_STAGE_RECEIPT_MISSING",
        "The interrupted sector stage has no sealed migration receipt.",
        status="MISMATCH",
        staged_path=str(staging),
    )
    try:
        decoded_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        validation = validate_lane_bundle(staging)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        sqlite3.DatabaseError,
        EvidenceLaneError,
    ) as exc:
        raise EvidenceLaneError(
            "PROJECT_WORKING_RECOVERY_STAGE_INVALID",
            "The interrupted sector stage is unreadable or invalid.",
            status="MISMATCH",
            details={"staged_path": str(staging)},
        ) from exc
    require(
        isinstance(decoded_receipt, dict),
        "PROJECT_WORKING_RECOVERY_STAGE_INVALID",
        "The interrupted sector stage receipt must be a JSON object.",
        status="MISMATCH",
        staged_path=str(staging),
    )
    receipt = decoded_receipt
    staged_identity = dict(receipt.get("working_identity") or {})
    require(
        validation.get("valid") is True
        and receipt.get("schema")
        == "evidence-lane.working-sector-migration.v1"
        and bool(str(receipt.get("migration_id") or ""))
        and receipt.get("project_id") == project_id
        and receipt.get("historical_parent_pv") == accepted_pv
        and receipt.get("pointer_generation") == pointer_generation
        and staged_identity == working_identity
        and receipt.get("all_18_sectors_materialized") is True
        and receipt.get("candidate_created") is False
        and receipt.get("pointer_moved") is False
        and receipt.get("hil_inferred") is False,
        "PROJECT_WORKING_RECOVERY_STAGE_BINDING_MISMATCH",
        "The interrupted sector stage is not bound to the exact live authority.",
        status="MISMATCH",
        staged_path=str(staging),
    )
    committed_receipt_path = (
        root
        / "receipts"
        / "project-authority"
        / "working-sector-migration.json"
    )
    committed_receipt = _load_validated_committed_working_receipt(
        committed_receipt_path,
        project_id=project_id,
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
        working_identity=working_identity,
        sector_bundle_sha256=str(validation.get("bundle_sha256") or ""),
        migration_id=str(receipt["migration_id"]),
        missing_code="PROJECT_WORKING_RECOVERY_COMMITTED_RECEIPT_MISSING",
        mismatch_code="PROJECT_WORKING_RECOVERY_COMMITTED_RECEIPT_MISMATCH",
    )
    projected_fields = (
        "schema",
        "migration_id",
        "project_id",
        "historical_parent_pv",
        "pointer_generation",
        "working_identity",
        "all_18_sectors_materialized",
        "candidate_created",
        "pointer_moved",
        "hil_inferred",
    )
    require(
        all(receipt.get(field) == committed_receipt.get(field) for field in projected_fields),
        "PROJECT_WORKING_RECOVERY_COMMITTED_RECEIPT_MISMATCH",
        "The staged and external migration receipts do not project the same authority.",
        status="MISMATCH",
    )
    recovery_swap = _replace_path_with_retry(
        staging,
        active_sectors,
        operation="RECOVER_INTERRUPTED_STAGE",
    )
    recovered_validation = validate_lane_bundle(active_sectors)
    require(
        recovered_validation.get("valid") is True
        and recovered_validation.get("bundle_sha256")
        == validation.get("bundle_sha256"),
        "PROJECT_WORKING_RECOVERY_PROMOTED_BUNDLE_MISMATCH",
        "The promoted interrupted stage differs from its preflighted sealed bundle.",
        status="FAIL",
    )
    recovery_record = {
        "schema": "evidence-lane.working-sector-recovery.v1",
        "status": "PASS",
        "project_id": project_id,
        "historical_parent_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "working_identity_sha256": working_identity["working_identity_sha256"],
        "recovered_stage": staging.name,
        "committed_receipt_sha256": committed_receipt["receipt_sha256"],
        "in_bundle_receipt_sha256": sha256_file(
            active_sectors / "working_migration_receipt.json"
        ),
        "sector_bundle_sha256_before_promotion": validation.get("bundle_sha256"),
        "sector_bundle_sha256": recovered_validation.get("bundle_sha256"),
        "path_operation": recovery_swap,
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


def _accepted_lane_schema_binding_compatibility(
    validation: dict[str, Any],
) -> dict[str, Any]:
    """Recognize sealed pre-registry lane DBs without rewriting accepted bytes.

    Lane schema bindings were added after some accepted PV packages had already
    been sealed.  Those immutable databases cannot be backfilled in place.  A
    historical compatibility pass is therefore allowed only when the complete
    bundle remains hash-exact and every otherwise-invalid lane fails solely
    because the new ``lane_meta`` binding keys are absent.  Current WORKING and
    candidate databases still require the exact binding through the normal
    validator.
    """

    if validation.get("valid") is True:
        return {
            "status": "PASS",
            "state": "CURRENT_SCHEMA_BINDING_VALIDATION",
            "compatible": False,
            "compatible_lane_ids": [],
            "invalid_lane_ids": [],
            "accepted_bytes_rewritten": False,
        }

    lane_reports = dict(validation.get("lanes") or {})
    compatible_lane_ids: list[str] = []
    invalid_lane_ids: list[str] = []
    for lane_id, report in lane_reports.items():
        if report.get("valid") is True:
            continue
        counts = dict(report.get("counts") or {})
        legacy_binding_only = bool(
            report.get("lane_schema_binding") == {}
            and report.get("integrity") == ["ok"]
            and not report.get("foreign_key_errors")
            and str(report.get("schema_version") or "").strip()
            == "evidence-lane.universal-lane.v2"
            and dict(report.get("lane_schema_builder_projection") or {}).get(
                "status"
            )
            == "PASS"
            and dict(report.get("lane_schema_evolution") or {}).get("valid")
            is True
            and report.get("fts_rows") == counts.get("chunk_index")
        )
        if legacy_binding_only:
            compatible_lane_ids.append(str(lane_id))
        else:
            invalid_lane_ids.append(str(lane_id))

    bundle_invariants_valid = bool(
        validation.get("checksum_set_match") is True
        and not validation.get("checksum_mismatches")
        and validation.get("bundle_sha256")
        == validation.get("declared_bundle_sha256")
        and validation.get("lane_emission_contract_valid") is True
        and validation.get("lane_directory_set_valid") is True
        and validation.get("parallel_execution_valid") is True
        and validation.get("topology_valid") is True
        and validation.get("source_routes_valid") is True
        and not validation.get("lane_manifest_errors")
        and dict(validation.get("lane_disposition_contract") or {}).get("valid")
        is True
    )
    compatible = bool(
        bundle_invariants_valid
        and compatible_lane_ids
        and not invalid_lane_ids
    )
    return {
        "status": "PASS" if compatible else "FAIL",
        "state": (
            "SEALED_PRE_REGISTRY_SCHEMA_BINDING_COMPATIBILITY"
            if compatible
            else "HISTORICAL_SCHEMA_BINDING_COMPATIBILITY_REJECTED"
        ),
        "compatible": compatible,
        "bundle_invariants_valid": bundle_invariants_valid,
        "compatible_lane_ids": sorted(compatible_lane_ids),
        "invalid_lane_ids": sorted(invalid_lane_ids),
        "accepted_bytes_rewritten": False,
    }


def _working_sector_source_rebuild_required(
    validation: dict[str, Any],
) -> bool:
    """Recognize narrow stale seals that Source Intake can rebuild safely.

    This does not accept or reseal the changed lane databases.  It only permits
    the explicit working-sector Source Intake action to discard those derived
    databases and rebuild all eighteen lanes from the governed current source
    boundary.  Any structural, topology, route, database-integrity, or
    non-operational wrapper mismatch remains fail-closed.
    """

    lane_errors = dict(validation.get("lane_manifest_errors") or {})
    lane_reports = dict(validation.get("lanes") or {})
    checksum_mismatches = dict(validation.get("checksum_mismatches") or {})
    structural_contracts_valid = bool(
        lane_errors
        and set(lane_errors) <= set(CANONICAL_LANE_IDS)
        and set(lane_reports) == set(CANONICAL_LANE_IDS)
        and all(row.get("valid") is True for row in lane_reports.values())
        and validation.get("lane_emission_contract_valid") is True
        and validation.get("lane_directory_set_valid") is True
        and validation.get("parallel_execution_valid") is True
        and validation.get("topology_valid") is True
        and validation.get("source_routes_valid") is True
        and dict(validation.get("lane_disposition_contract") or {}).get("valid")
        is True
        and all(
            is_working_sector_operational_member(path)
            for path in checksum_mismatches
        )
    )
    if not structural_contracts_valid:
        return False

    for lane_id, error in lane_errors.items():
        lane = LANE_REGISTRY[lane_id]
        declared_stable = dict(error.get("declared_stable_artifacts") or {})
        actual_stable = dict(error.get("actual_stable_artifacts") or {})
        declared_evidence = dict(error.get("declared_evidence_artifacts") or {})
        actual_evidence = dict(error.get("actual_evidence_artifacts") or {})
        changed_stable = {
            name
            for name in declared_stable.keys() | actual_stable.keys()
            if declared_stable.get(name) != actual_stable.get(name)
        }
        changed_evidence = {
            name
            for name in declared_evidence.keys() | actual_evidence.keys()
            if declared_evidence.get(name) != actual_evidence.get(name)
        }
        four_file = dict(error.get("four_file_contract") or {})
        artifact_roles = dict(error.get("artifact_role_contract") or {})
        narrow_database_seal_drift = bool(
            error.get("schema") == "evidence-lane.lane-manifest.v3"
            and error.get("lane_id") == lane_id
            and changed_stable == {lane.sqlite_filename}
            and changed_evidence == {lane.sqlite_filename}
            and not error.get("missing_required_artifacts")
            and error.get("mmd_valid") is True
            and error.get("dot_valid") is True
            and dict(error.get("topology_reconciliation") or {}).get("status")
            == "PASS"
            and not four_file.get("missing")
            and four_file.get("tools_json_valid") is True
            and four_file.get("computed_tool_identity_sha256")
            == four_file.get("declared_tool_identity_sha256")
            and not artifact_roles.get("extensions")
            and not artifact_roles.get("undeclared_extension_files")
            and all(
                row.get("exists") is True
                for row in artifact_roles.get("required_roles") or []
            )
        )
        if not narrow_database_seal_drift:
            return False
    return True


def migrate_working_project_sectors(
    project_root: str | Path,
    *,
    repository_root: str | Path,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    expected_branch: str | None = None,
    expected_head: str | None = None,
    bootstrap_pv0: bool = False,
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
    pointer_generation_value = pointer.get("generation")
    actual_pointer_generation = (
        int(pointer_generation_value) if pointer_generation_value is not None else -1
    )
    pointer_matches_current = (
        pointer.get("project_id") == project_id
        and pointer.get("accepted_pv") == accepted_pv
        and actual_pointer_generation == pointer_generation
    )
    pointer_matches_pv0_bootstrap = (
        bootstrap_pv0
        and accepted_pv == "PV0"
        and pointer_generation == 0
        and pointer.get("project_id") == project_id
        and pointer.get("accepted_pv") is None
        and actual_pointer_generation == 0
    )
    require(
        pointer_matches_current or pointer_matches_pv0_bootstrap,
        "PROJECT_WORKING_POINTER_MISMATCH",
        "Working-sector migration cannot change or reinterpret the accepted pointer.",
        status="MISMATCH",
    )
    pointer_before = sha256_file(pointer_path)
    accepted_lane_schema_compatibility = {
        "status": "PASS",
        "state": (
            "INITIAL_PV0_LIVE_ROOT_BASELINE_PENDING"
            if pointer_matches_pv0_bootstrap
            else "ACCEPTED_HIL_ARCHIVE_POINTER_BASELINE_ONLY"
        ),
        "accepted_pv": accepted_pv,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
        "accepted_bytes_rewritten": False,
        "pv0_bootstrap": pointer_matches_pv0_bootstrap,
    }
    source_scaffold_preflight = _inspect_source_scaffold_retirement(root)
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
    active_sector_bundle_ready = bool(
        active_sectors.is_dir()
        and (active_sectors / "manifest.json").is_file()
        and (active_sectors / "SHA256SUMS.json").is_file()
        and (active_sectors / "routes.json").is_file()
        and all(
            (active_sectors / lane_id).is_dir()
            for lane_id in CANONICAL_LANE_IDS
        )
    )
    build_parent_bundle: Path | None = (
        active_sectors if active_sector_bundle_ready else None
    )
    build_parent_kind = (
        "CURRENT_ROOT_WORKING_SECTORS"
        if build_parent_bundle is not None
        else "NO_PRIOR_WORKING_SECTOR_BUNDLE"
    )
    refreshing_existing = bool(
        active_sector_bundle_ready
        and _sector_operational_authority_ready(plan_sector_root(root))
        and _sector_operational_authority_ready(chat_lineage_sector_root(root))
        and all((active_sectors / lane_id).is_dir() for lane_id in CANONICAL_LANE_IDS)
        and not any((root / name).exists() for name in (*_LEGACY_PLAN_PATHS, "lineage"))
    )
    if refreshing_existing:
        validation = validate_lane_bundle(active_sectors)
        checksum_mismatches = dict(validation.get("checksum_mismatches") or {})
        operational_authority_only_drift = bool(checksum_mismatches) and all(
            is_working_sector_operational_member(path)
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
        source_rebuild_required = _working_sector_source_rebuild_required(validation)
        require(
            validation.get("valid") is True
            or refreshable_existing_authority
            or source_rebuild_required,
            "PROJECT_WORKING_EXISTING_SECTORS_INVALID",
            "The existing sector-owned working authority failed validation.",
            status="MISMATCH",
            refreshable_existing_authority=refreshable_existing_authority,
            source_rebuild_required=source_rebuild_required,
            checksum_mismatch_paths=sorted(checksum_mismatches),
        )
        # A working-to-working Refresh must compare every canonical lane with
        # the last validated working bundle.  Falling back to the immutable
        # accepted PV here loses all post-PV reuse knowledge and turns an
        # ordinary Delta refresh into a full validation rebuild.  The accepted
        # PV remains the historical authority and pointer base; it is not the
        # incremental byte parent once a valid working authority exists.
        if source_rebuild_required:
            # Do not treat the stale derived databases as a byte-reuse parent.
            # The explicit Source Intake action rebuilds from the governed Git
            # source boundary and later carries root-nested immutable history
            # and operational Plan/ChatLineage authorities independently.
            build_parent_bundle = None
            build_parent_kind = "CURRENT_WORKING_SECTORS_SOURCE_REBUILD_REQUIRED"
        else:
            build_parent_bundle = active_sectors
            build_parent_kind = "CURRENT_VALIDATED_WORKING_SECTORS"
        existing_receipt_path = active_sectors / "working_migration_receipt.json"
        try:
            decoded_existing_receipt = (
                json.loads(existing_receipt_path.read_text(encoding="utf-8"))
                if existing_receipt_path.is_file()
                else {}
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "PROJECT_WORKING_EXISTING_RECEIPT_BINDING_MISMATCH",
                "The existing WORKING bundle has an unreadable migration receipt.",
                status="MISMATCH",
                details={"receipt_path": str(existing_receipt_path)},
            ) from exc
        require(
            isinstance(decoded_existing_receipt, dict),
            "PROJECT_WORKING_EXISTING_RECEIPT_BINDING_MISMATCH",
            "The existing WORKING migration receipt must be a JSON object.",
            status="MISMATCH",
        )
        existing_receipt = decoded_existing_receipt
        require(
            existing_receipt.get("schema")
            == "evidence-lane.working-sector-migration.v1"
            and bool(str(existing_receipt.get("migration_id") or ""))
            and existing_receipt.get("project_id") == project_id
            and existing_receipt.get("historical_parent_pv") == accepted_pv
            and existing_receipt.get("pointer_generation") == pointer_generation
            and existing_receipt.get("all_18_sectors_materialized") is True
            and existing_receipt.get("candidate_created") is False
            and existing_receipt.get("pointer_moved") is False
            and existing_receipt.get("hil_inferred") is False,
            "PROJECT_WORKING_EXISTING_RECEIPT_BINDING_MISMATCH",
            "The existing WORKING bundle has an invalid migration receipt binding.",
            status="MISMATCH",
        )
        existing_identity = dict(existing_receipt.get("working_identity") or {})
        if (
            validation.get("valid") is True
            and existing_identity.get("working_identity_sha256")
            == identity_before.get("working_identity_sha256")
        ):
            existing_manifest = json.loads(
                (active_sectors / "manifest.json").read_text(encoding="utf-8")
            )
            existing_reports = list(existing_manifest.get("reports") or [])
            existing_lane_ids = [str(row.get("lane_id") or "") for row in existing_reports]
            require(
                existing_manifest.get("lane_emission_policy")
                == "ALL_18_WORKING_AUTHORITY"
                and int(existing_manifest.get("canonical_lane_count") or 0)
                == len(CANONICAL_LANE_IDS)
                and existing_lane_ids == list(CANONICAL_LANE_IDS),
                "PROJECT_WORKING_IDEMPOTENT_LANE_SCOPE_MISMATCH",
                "Idempotent working authority reuse requires the exact all-18-lane manifest.",
                status="MISMATCH",
            )
            existing_fallbacks = list(
                dict(existing_manifest.get("summary") or {}).get(
                    "full_validation_fallbacks"
                )
                or []
            )
            require(
                all(
                    str(row.get("reason") or "").strip()
                    and row.get("reason") != "UNDECLARED"
                    for row in existing_fallbacks
                ),
                "PROJECT_WORKING_IDEMPOTENT_FALLBACK_UNDECLARED",
                "Every full validation fallback must retain an explicit bounded reason.",
                status="MISMATCH",
            )
            canonical_lane_refresh = {
                "authority_scope": "ALL_18_CANONICAL_LANES",
                "refresh_action": "IDEMPOTENT_WORKING_AUTHORITY_REUSE",
                "canonical_lane_count": len(CANONICAL_LANE_IDS),
                "emitted_lane_ids": existing_lane_ids,
                "lane_report_count": len(existing_reports),
                "lane_reports": [
                    {
                        "lane_id": row["lane_id"],
                        "build_mode": row["build_mode"],
                        "full_validation_fallback_reason": row.get(
                            "full_validation_fallback_reason"
                        ),
                        "byte_reused": bool(row.get("byte_reused")),
                    }
                    for row in existing_reports
                ],
                "full_validation_fallbacks": existing_fallbacks,
                "build_parent_kind": build_parent_kind,
            }
            committed_receipt_path = (
                root
                / "receipts"
                / "project-authority"
                / "working-sector-migration.json"
            )
            committed_receipt = _load_validated_committed_working_receipt(
                committed_receipt_path,
                project_id=project_id,
                accepted_pv=accepted_pv,
                pointer_generation=pointer_generation,
                working_identity=identity_before,
                sector_bundle_sha256=str(validation.get("bundle_sha256") or ""),
                migration_id=str(existing_receipt["migration_id"]),
                missing_code="PROJECT_WORKING_IDEMPOTENT_COMMITTED_RECEIPT_MISSING",
                mismatch_code=(
                    "PROJECT_WORKING_IDEMPOTENT_COMMITTED_RECEIPT_MISMATCH"
                ),
            )
            source_scaffold_retirement = _retire_empty_source_scaffolds(
                root,
                preflight=source_scaffold_preflight,
            )
            return {
                "status": "PASS",
                "state": "WORKING_SECTOR_AUTHORITY_IDEMPOTENT_REUSE",
                "receipt_sha256": committed_receipt["receipt_sha256"],
                "working_identity": identity_before,
                "lane_validation": validation,
                "build_parent_kind": build_parent_kind,
                "canonical_lane_refresh": canonical_lane_refresh,
                "interrupted_stage_recovery": interrupted_stage_recovery,
                "source_scaffold_retirement": source_scaffold_retirement,
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

    delta_rows, content_paths, local_only_exclusion = _working_delta_inventory(
        repository,
        historical_parent_pv=accepted_pv,
        working_identity_sha256=identity_before["working_identity_sha256"],
    )
    copy_reports: list[dict[str, Any]] = []
    path_operation_reports: list[dict[str, Any]] = []
    swapped = False
    migration_phase = "BUILD_LANE_BUNDLE"
    try:
        lane_build = build_lane_bundle(
            repository_root=repository,
            output_directory=staging,
            code_mode="local_code",
            parent_lane_bundle=build_parent_bundle,
            parent_pv=accepted_pv,
            proposed_pv=f"{accepted_pv}_WORKING",
            pointer_generation=pointer_generation,
            # Local Code and the other current sectors derive from the Git
            # index/tracked source set.  Current worktree bytes for those
            # tracked paths are indexed; unstaged local testing/evidence is
            # retained only in the dirty inventory until it is explicitly
            # staged or independently admitted through Source Intake.
            include_untracked=False,
            materialize_all_lanes=True,
            source_overrides={
                row["path"]: row["lane_id"]
                for row in delta_rows
                if row["content_policy"] == "BOUNDED_LANE_CONTENT"
                and not row["untracked"]
            },
            # WORKING is the complete governed repository projection, not a
            # dirty-path overlay.  The dirty inventory remains the mutation
            # receipt, while the lane build freezes every current governed
            # tracked source so a tool-identity fallback cannot preserve stale
            # parent rows or omit clean current files.
            source_paths_override=None,
            preserve_parent_unmentioned=True,
            index_git_history=False,
            allow_parent_operational_authority_drift=refreshing_existing,
        )
        lane_reports = list(lane_build.get("reports") or [])
        emitted_lane_ids = [str(row.get("lane_id") or "") for row in lane_reports]
        require(
            lane_build.get("lane_emission_policy") == "ALL_18_WORKING_AUTHORITY"
            and int(lane_build.get("canonical_lane_count") or 0)
            == len(CANONICAL_LANE_IDS)
            and emitted_lane_ids == list(CANONICAL_LANE_IDS),
            "PROJECT_WORKING_LANE_REFRESH_SCOPE_MISMATCH",
            "Working-sector Refresh must emit exactly the ordered 18 canonical lanes.",
            status="MISMATCH",
        )
        full_validation_fallbacks = list(
            dict(lane_build.get("summary") or {}).get("full_validation_fallbacks")
            or []
        )
        require(
            all(
                str(row.get("reason") or "").strip()
                and row.get("reason") != "UNDECLARED"
                for row in full_validation_fallbacks
            ),
            "PROJECT_WORKING_LANE_FALLBACK_UNDECLARED",
            "Every full validation fallback must expose one explicit bounded reason.",
            status="MISMATCH",
        )
        canonical_lane_refresh = {
            "authority_scope": "ALL_18_CANONICAL_LANES",
            "refresh_action": (
                "WORKING_TO_WORKING_INCREMENTAL_REFRESH"
                if refreshing_existing
                else "INITIAL_WORKING_AUTHORITY_MIGRATION"
            ),
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "emitted_lane_ids": emitted_lane_ids,
            "lane_report_count": len(lane_reports),
            "lane_reports": [
                {
                    "lane_id": row["lane_id"],
                    "build_mode": row["build_mode"],
                    "full_validation_fallback_reason": row.get(
                        "full_validation_fallback_reason"
                    ),
                    "byte_reused": bool(row.get("byte_reused")),
                }
                for row in lane_reports
            ],
            "full_validation_fallbacks": full_validation_fallbacks,
            "build_parent_kind": build_parent_kind,
            "complete_governed_source_path_count": int(
                lane_build.get("source_count") or 0
            ),
            "complete_governed_source_snapshot_sha256": lane_build.get(
                "source_snapshot_sha256"
            ),
            "complete_governed_source_replay": True,
            "current_source_authority_scope": "GIT_INDEX_CURRENT_WORKTREE_BYTES",
        }
        initial_validation = validate_lane_bundle(staging)
        require(
            initial_validation.get("valid") is True,
            "PROJECT_WORKING_LANE_BUILD_INVALID",
            "The staged all-lane working authority failed validation.",
            status="FAIL",
            invalid_lane_ids=sorted(
                lane_id
                for lane_id, row in dict(
                    initial_validation.get("lanes") or {}
                ).items()
                if row.get("valid") is not True
            ),
            source_routes_valid=initial_validation.get("source_routes_valid"),
            topology_valid=initial_validation.get("topology_valid"),
            lane_directory_set_valid=initial_validation.get(
                "lane_directory_set_valid"
            ),
            lane_manifest_errors=initial_validation.get("lane_manifest_errors"),
            checksum_mismatches=initial_validation.get("checksum_mismatches"),
        )

        migration_phase = "PRESERVE_ROOT_NESTED_PV_HISTORY"
        root_nested_history_reports = _preserve_root_nested_lane_histories(
            active_sectors if active_sectors_existed else None,
            staging,
        )

        migration_phase = "STAGE_PLAN_AUTHORITY"
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
        migration_phase = "STAGE_WORKING_DELTA_INVENTORY"
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

        migration_phase = "STAGE_CHAT_LINEAGE_AUTHORITY"
        require(
            lineage_source.is_dir(),
            "PROJECT_WORKING_CHAT_LINEAGE_MISSING",
            "The live ChatLineage authority is required for sector migration.",
            status="MISMATCH",
        )
        lineage_stage = staging / CHAT_LINEAGE_SECTOR_ID
        lineage_sources = _chat_lineage_operational_files(lineage_source)
        lineage_source_names = {path.name for path in lineage_sources}
        require(
            {"chat_lineage.sqlite", "chat_lineage_head.json"}
            <= lineage_source_names,
            "PROJECT_WORKING_CHAT_LINEAGE_CANONICAL_FILE_MISSING",
            "The current canonical ChatLineage SQLite and head are required.",
            status="MISMATCH",
            source=str(lineage_source),
            direct_members=sorted(lineage_source_names),
        )
        for source in lineage_sources:
            copy_reports.append(
                _copy_authority_path(source, lineage_stage / source.name)
            )

        migration_phase = "SEAL_CANONICAL_LANE_REFERENCES"
        historical_lane_references: list[dict[str, Any]] = []
        for lane_id in CANONICAL_LANE_IDS:
            lane_root = staging / lane_id
            schema = lane_schema_asset(lane_id)
            artifacts = lane_artifact_contract(lane_id)
            historical_reference = _accepted_lane_history_reference(
                root,
                project_id=project_id,
                lane_id=lane_id,
                accepted_pv=accepted_pv,
            )
            historical_lane_references.append(historical_reference)
            atomic_write_json(
                lane_root / "historical_authority.ref.json",
                historical_reference,
            )
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
                    "historical_authority_reference": (
                        "historical_authority.ref.json"
                    ),
                    "historical_authority_state": historical_reference["state"],
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
                    "source_registry_authority": (
                        f"sectors/{lane_id}/{LANE_REGISTRY[lane_id].sqlite_filename}"
                    ),
                    "lane_owned_artifacts": [
                        LANE_REGISTRY[lane_id].sqlite_filename,
                        LANE_REGISTRY[lane_id].mmd_filename,
                        LANE_REGISTRY[lane_id].dot_filename,
                        "tools.json",
                        "lane_pointer.json",
                        "lane_manifest.json",
                        "study_brain.json",
                    ],
                    "additional_sources_remain_lane_scoped": True,
                    "raw_payload_model_context_loading": False,
                    "candidate_created": False,
                    "pointer_moved": False,
                },
            )

        plan_files = {
            relative: sha256_file(plan_stage / relative)
            for relative in ("task_backlog.json", "plan_runtime_projection.sqlite")
        }
        lineage_members = {
            path.name: sha256_file(path)
            for path in _chat_lineage_operational_files(
                staging / CHAT_LINEAGE_SECTOR_ID
            )
        }
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
                "layout": "DIRECT_SECTOR_ROOT",
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
            "accepted_lane_schema_compatibility": (
                accepted_lane_schema_compatibility
            ),
            "build_parent_kind": build_parent_kind,
            "build_parent_manifest_sha256": (
                sha256_file(build_parent_bundle / "manifest.json")
                if build_parent_bundle is not None
                else None
            ),
            "pointer_generation": pointer_generation,
            "pv0_bootstrap": pointer_matches_pv0_bootstrap,
            "working_identity": identity_before,
            "all_18_sectors_materialized": True,
            "historical_lanes_replayed": bool(root_nested_history_reports),
            "historical_parent_copied_once": False,
            "historical_parent_reference_mode": (
                "LIVE_ROOT_NESTED_HISTORY_ONLY"
            ),
            "root_nested_history_reports": root_nested_history_reports,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "historical_lane_reference_count": len(historical_lane_references),
            "historical_materialized_lane_count": sum(
                row["state"] == "IMMUTABLE_ACCEPTED_HISTORY"
                for row in historical_lane_references
            ),
            "content_delta_path_count": len(content_paths),
            "complete_governed_source_path_count": int(
                lane_build.get("source_count") or 0
            ),
            "complete_governed_source_snapshot_sha256": lane_build.get(
                "source_snapshot_sha256"
            ),
            "complete_governed_source_replay": True,
            "current_source_authority_scope": "GIT_INDEX_CURRENT_WORKTREE_BYTES",
            "complete_dirty_path_count": len(delta_rows),
            "working_delta_inventory": inventory_report,
            "local_only_exclusion": local_only_exclusion,
            "changed_lane_ids": sorted(
                {str(row["lane_id"]) for row in delta_rows}
                | {PLAN_SECTOR_ID, CHAT_LINEAGE_SECTOR_ID}
            ),
            "canonical_lane_refresh": canonical_lane_refresh,
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

        migration_phase = "COMMIT_ACTIVE_SECTOR_SWAP"
        if active_sectors_existed:
            path_operation_reports.append(
                _replace_path_with_retry(
                    active_sectors,
                    backup,
                    operation="MOVE_CURRENT_AUTHORITY_TO_EXACT_BACKUP",
                )
            )
        path_operation_reports.append(
            _replace_path_with_retry(
                staging,
                active_sectors,
                operation="PROMOTE_STAGED_AUTHORITY",
            )
        )
        swapped = True

        migration_phase = "RELOCATE_LEGACY_RECEIPTS"
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

        if lineage_source == root / "lineage":
            lineage_receipt_root = root / "receipts" / "chat-lineage-runtime"
            recognized_sources = set(lineage_sources)
            for member in sorted(lineage_source.iterdir()):
                if member in recognized_sources:
                    continue
                destination = lineage_receipt_root / member.name
                if not destination.exists():
                    copy_reports.append(_copy_authority_path(member, destination))

        migration_phase = "REMOVE_LEGACY_DUPLICATE_AUTHORITIES"
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
        source_scaffold_retirement = _retire_empty_source_scaffolds(
            root,
            preflight=source_scaffold_preflight,
        )
        if backup.exists():
            path_operation_reports.append(
                _remove_tree_with_retry(
                    backup,
                    operation="REMOVE_EXACT_PREVIOUS_AUTHORITY_BACKUP",
                )
            )

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
            "path_operation_reports": path_operation_reports,
            "source_scaffold_retirement": source_scaffold_retirement,
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
            "build_parent_kind": build_parent_kind,
            "canonical_lane_refresh": canonical_lane_refresh,
            "path_operation_reports": path_operation_reports,
            "plan_authority_root": str(plan_sector_root(root)),
            "chat_lineage_authority_root": str(chat_lineage_sector_root(root)),
            "source_scaffold_retirement": source_scaffold_retirement,
            "root_plan_duplicates_present": any(
                (root / name).exists() for name in _LEGACY_PLAN_PATHS
            ),
            "root_chat_lineage_duplicate_present": (root / "lineage").exists(),
            "pointer_moved": False,
            "candidate_created": False,
            "hil_inferred": False,
        }
    except Exception as exc:
        try:
            if swapped:
                failed = root / f".sectors-working-{migration_id}.failed"
                if active_sectors.exists():
                    _replace_path_with_retry(
                        active_sectors,
                        failed,
                        operation="QUARANTINE_FAILED_PROMOTED_AUTHORITY",
                    )
                if backup.exists():
                    _replace_path_with_retry(
                        backup,
                        active_sectors,
                        operation="RESTORE_EXACT_PREVIOUS_AUTHORITY",
                    )
            if staging.exists():
                _remove_tree_with_retry(
                    staging,
                    operation="REMOVE_FAILED_STAGING_AUTHORITY",
                )
        except Exception as rollback_exc:
            raise EvidenceLaneError(
                "PROJECT_WORKING_MIGRATION_ROLLBACK_BLOCKED",
                "The working-sector migration failed and Windows also blocked its exact rollback path.",
                status="BLOCKED",
                details={
                    "migration_phase": migration_phase,
                    "original_exception_type": type(exc).__name__,
                    "rollback_exception_type": type(rollback_exc).__name__,
                    "active_sectors_present": active_sectors.exists(),
                    "backup_present": backup.exists(),
                    "staging_present": staging.exists(),
                },
            ) from rollback_exc
        if isinstance(exc, EvidenceLaneError):
            raise
        if isinstance(exc, OSError):
            raise EvidenceLaneError(
                "PROJECT_WORKING_MIGRATION_OS_ERROR",
                "The working-sector migration encountered an operating-system error at an exact bounded phase.",
                status="BLOCKED",
                details={
                    "migration_phase": migration_phase,
                    "exception_type": type(exc).__name__,
                    "errno": exc.errno,
                    "winerror": getattr(exc, "winerror", None),
                },
            ) from exc
        raise


def _validate_working_project_query_authority(
    project_root: Path,
    *,
    repository_root: Path,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    projected_identity: dict[str, Any],
) -> dict[str, Any]:
    """Validate the complete WORKING bundle and its project binding once."""

    sectors_root = project_root / "sectors"
    try:
        bundle_validation = validate_lane_bundle(sectors_root)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        sqlite3.DatabaseError,
    ) as exc:
        raise EvidenceLaneError(
            "PROJECT_WORKING_QUERY_BUNDLE_INTEGRITY_MISMATCH",
            "The complete WORKING sector bundle has unreadable or invalid sealed authority.",
            status="MISMATCH",
            details={"project_id": project_id},
        ) from exc
    checksum_mismatches = dict(bundle_validation.get("checksum_mismatches") or {})
    operational_authority_only_drift = bool(checksum_mismatches) and all(
        is_working_sector_operational_member(path)
        for path in checksum_mismatches
    )
    validated_working_authority = bool(
        operational_authority_only_drift
        and not bundle_validation.get("lane_manifest_errors")
        and all(
            lane.get("valid") is True
            for lane in dict(bundle_validation.get("lanes") or {}).values()
        )
        and bundle_validation.get("lane_directory_set_valid") is True
        and bundle_validation.get("source_routes_valid") is True
        and bundle_validation.get("topology_valid") is True
    )
    require(
        bundle_validation.get("valid") is True or validated_working_authority,
        "PROJECT_WORKING_QUERY_BUNDLE_INTEGRITY_MISMATCH",
        "The complete WORKING sector bundle differs from its sealed authority.",
        status="MISMATCH",
        checksum_mismatch_paths=sorted(checksum_mismatches),
        lane_manifest_error_ids=sorted(
            dict(bundle_validation.get("lane_manifest_errors") or {})
        ),
    )
    sealed_sector_bundle_sha256 = str(
        (
            bundle_validation.get("declared_bundle_sha256")
            if validated_working_authority
            else bundle_validation.get("bundle_sha256")
        )
        or ""
    )
    accepted_schema_compatibility = {
        "status": "PASS",
        "state": "ACCEPTED_HIL_ARCHIVE_POINTER_BASELINE_ONLY",
        "accepted_pv": accepted_pv,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
    }

    layout_path = project_root / "project_authority.json"
    committed_receipt_path = (
        project_root
        / "receipts"
        / "project-authority"
        / "working-sector-migration.json"
    )
    require(
        layout_path.is_file(),
        "PROJECT_WORKING_QUERY_PROJECT_BINDING_MISSING",
        "A WORKING query requires the sealed project layout.",
        status="MISMATCH",
    )
    try:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceLaneError(
            "PROJECT_WORKING_QUERY_PROJECT_BINDING_MISMATCH",
            "The WORKING project binding is unreadable.",
            status="MISMATCH",
            details={"project_id": project_id},
        ) from exc

    layout_body = {
        key: value for key, value in layout.items() if key != "layout_sha256"
    }
    accepted_pointer = dict(layout.get("accepted_pointer") or {})
    layout_valid = bool(
        layout.get("schema") == PROJECT_AUTHORITY_LAYOUT_SCHEMA
        and layout.get("layout_sha256")
        == sha256_bytes(canonical_json_bytes(layout_body))
        and layout.get("project_id") == project_id
        and Path(str(layout.get("resolved_project_root") or "")).resolve()
        == project_root
        and Path(str(layout.get("repository_path") or "")).resolve()
        == repository_root
        and int(layout.get("canonical_lane_count") or 0)
        == len(CANONICAL_LANE_IDS)
        and layout.get("ordered_lane_ids") == list(CANONICAL_LANE_IDS)
        and accepted_pointer.get("accepted_pv") == accepted_pv
        and int(accepted_pointer.get("generation") or -1) == pointer_generation
        and accepted_pointer.get("moved") is False
        and layout.get("shadow_authority_created") is False
    )
    require(
        layout_valid,
        "PROJECT_WORKING_QUERY_PROJECT_BINDING_MISMATCH",
        "The WORKING project layout is not bound to this exact project and repository.",
        status="MISMATCH",
        project_id=project_id,
    )

    committed_receipt = _load_validated_committed_working_receipt(
        committed_receipt_path,
        project_id=project_id,
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
        working_identity=projected_identity,
        sector_bundle_sha256=sealed_sector_bundle_sha256,
        missing_code="PROJECT_WORKING_QUERY_PROJECT_BINDING_MISSING",
        mismatch_code="PROJECT_WORKING_QUERY_COMMITTED_RECEIPT_MISMATCH",
        allow_validated_working_successor_bundle=True,
    )
    committed_baseline_bundle_sha256 = str(
        committed_receipt.get("sector_bundle_sha256") or ""
    )
    return {
        "status": "PASS",
        "migration_id": committed_receipt["migration_id"],
        "bundle_sha256": sealed_sector_bundle_sha256,
        "observed_bundle_sha256": bundle_validation["bundle_sha256"],
        "accepted_bundle_sha256": None,
        "accepted_schema_binding_compatibility": accepted_schema_compatibility,
        "ordinary_query_authority": "LIVE_PROJECT_ROOT",
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
        "layout_sha256": layout["layout_sha256"],
        "committed_receipt_sha256": committed_receipt["receipt_sha256"],
        "committed_baseline_bundle_sha256": committed_baseline_bundle_sha256,
        "bundle_binding_state": (
            "EXACT_COMMITTED_BUNDLE"
            if committed_baseline_bundle_sha256 == sealed_sector_bundle_sha256
            else "VALIDATED_OPERATIONAL_SUCCESSOR_BUNDLE"
        ),
        "canonical_lane_count": len(CANONICAL_LANE_IDS),
        "operational_checksum_drift_ignored": validated_working_authority,
        "operational_checksum_drift_paths": (
            sorted(checksum_mismatches) if validated_working_authority else []
        ),
    }


def _validate_working_lane_query_authority(
    sectors_root: Path,
    *,
    lane_id: str,
    project_id: str,
    accepted_pv: str,
    pointer_generation: int,
    working_identity_sha256: str,
) -> dict[str, Any]:
    """Validate one sealed WORKING lane before opening its SQLite authority."""

    lane = LANE_REGISTRY[lane_id]
    lane_root = sectors_root / lane_id
    checksums_path = sectors_root / "SHA256SUMS.json"
    manifest_path = sectors_root / "manifest.json"
    lane_manifest_path = lane_root / "lane_manifest.json"
    authority_path = lane_root / "authority.ref.json"
    historical_reference_path = lane_root / "historical_authority.ref.json"
    study_brain_path = lane_root / "study_brain.json"
    required = (
        checksums_path,
        manifest_path,
        lane_manifest_path,
        authority_path,
        historical_reference_path,
        study_brain_path,
        lane_root / lane.sqlite_filename,
    )
    require(
        all(path.is_file() for path in required),
        "PROJECT_WORKING_QUERY_LANE_INTEGRITY_MISMATCH",
        "A selected WORKING lane is missing sealed authority members.",
        status="MISMATCH",
        lane_id=lane_id,
    )
    try:
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        bundle_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        lane_manifest = json.loads(lane_manifest_path.read_text(encoding="utf-8"))
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        historical_reference = json.loads(
            historical_reference_path.read_text(encoding="utf-8")
        )
        study_brain = json.loads(study_brain_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceLaneError(
            "PROJECT_WORKING_QUERY_LANE_INTEGRITY_MISMATCH",
            "A selected WORKING lane has unreadable sealed authority metadata.",
            status="MISMATCH",
            details={"lane_id": lane_id},
        ) from exc

    declared_members = dict(checksums.get("members") or {})
    prefix = f"{lane_id}/"
    declared_lane_members = {
        path: str(value)
        for path, value in declared_members.items()
        if str(path).startswith(prefix)
    }
    actual_lane_members = {
        path.relative_to(sectors_root).as_posix(): sha256_file(path)
        for path in sorted(lane_root.rglob("*"))
        if path.is_file()
    }
    declared_semantic_lane_members = {
        path: value
        for path, value in declared_lane_members.items()
        if not is_working_sector_operational_member(path)
    }
    actual_semantic_lane_members = {
        path: value
        for path, value in actual_lane_members.items()
        if not is_working_sector_operational_member(path)
    }
    operational_member_paths = sorted(
        {
            path
            for path in declared_lane_members.keys() | actual_lane_members.keys()
            if is_working_sector_operational_member(path)
            and declared_lane_members.get(path) != actual_lane_members.get(path)
        }
    )
    declared_stable = dict(lane_manifest.get("stable_artifacts") or {})
    actual_stable = {
        filename: sha256_file(lane_root / filename)
        for filename in declared_stable
        if (lane_root / filename).is_file()
    }
    lane_body = dict(lane_manifest.get("lane") or {})
    schema = lane_schema_asset(lane_id)
    artifacts = lane_artifact_contract(lane_id)
    bundle_members_sha256 = sha256_bytes(canonical_json_bytes(declared_members))
    valid = bool(
        checksums.get("schema") == "evidence-lane.recursive-sha256.v1"
        and bundle_manifest.get("bundle_sha256") == bundle_members_sha256
        and declared_semantic_lane_members == actual_semantic_lane_members
        and declared_stable
        and declared_stable == actual_stable
        and lane_body.get("canonical_lane_id") == lane_id
        and authority.get("schema") == "evidence-lane.working-sector-authority.v1"
        and authority.get("state") == "LIVE_WORKING"
        and authority.get("project_id") == project_id
        and authority.get("lane_id") == lane_id
        and authority.get("accepted_pv") == accepted_pv
        and authority.get("historical_parent_pv") == accepted_pv
        and int(authority.get("pointer_generation") or -1) == pointer_generation
        and authority.get("working_identity_sha256") == working_identity_sha256
        and authority.get("lane_schema_contract_sha256")
        == schema["contract_sha256"]
        and authority.get("artifact_contract_sha256")
        == artifacts["contract_sha256"]
        and authority.get("historical_authority_reference")
        == "historical_authority.ref.json"
        and authority.get("historical_authority_state")
        == historical_reference.get("state")
        and authority.get("candidate_directory_created") is False
        and authority.get("pointer_moved") is False
        and historical_reference.get("project_id") == project_id
        and historical_reference.get("lane_id") == lane_id
        and historical_reference.get("accepted_pv") == accepted_pv
        and historical_reference.get("independent_writable_authority") is False
        and study_brain.get("schema") == "evidence-lane.lane-study-brain.v1"
        and study_brain.get("project_id") == project_id
        and study_brain.get("lane_id") == lane_id
        and study_brain.get("state") == "ACTIVE"
        and study_brain.get("working_identity_sha256") == working_identity_sha256
        and study_brain.get("bounded_query_only") is True
        and study_brain.get("raw_payload_model_context_loading") is False
        and study_brain.get("candidate_created") is False
        and study_brain.get("pointer_moved") is False
    )
    require(
        valid,
        "PROJECT_WORKING_QUERY_LANE_INTEGRITY_MISMATCH",
        "A selected WORKING lane differs from its sealed fingerprint or project binding.",
        status="MISMATCH",
        lane_id=lane_id,
    )
    return {
        "lane_id": lane_id,
        "status": "PASS",
        "member_count": len(actual_lane_members),
        "member_manifest_sha256": sha256_bytes(
            canonical_json_bytes(actual_lane_members)
        ),
        "authority_reference_sha256": sha256_file(authority_path),
        "historical_reference_sha256": sha256_file(historical_reference_path),
        "study_brain_sha256": sha256_file(study_brain_path),
        "database_sha256": actual_stable[lane.sqlite_filename],
        "operational_checksum_drift_ignored": bool(operational_member_paths),
        "operational_checksum_drift_paths": operational_member_paths,
    }


def _working_source_path_key(value: Any) -> str:
    """Return the platform-correct identity key for one repository-relative path."""

    normalized = str(value).replace("\\", "/")
    return normalized.casefold() if os.name == "nt" else normalized


def _query_root_nested_pv_history(
    database_path: Path,
    *,
    lane_id: str,
    fts_query: str,
    current_paths: set[str],
    limit: int,
) -> dict[str, Any]:
    """Query root-nested PV history after suppressing every current path."""

    lane = LANE_REGISTRY[lane_id]
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    current_path_keys = {_working_source_path_key(value) for value in current_paths}
    try:
        connection.create_function(
            "evi_is_current_path",
            1,
            lambda value: int(_working_source_path_key(value) in current_path_keys),
            deterministic=True,
        )
        rows = connection.execute(
            (
                "SELECT CAST(chunk_id AS INTEGER), path, locator, "
                f"snippet({lane.fts_table}, 2, '[', ']', ' ... ', 24), "
                f"bm25({lane.fts_table}) FROM {lane.fts_table} "
                f"WHERE {lane.fts_table} MATCH ? "
                "AND evi_is_current_path(path)=0 "
                "ORDER BY 5, path, locator LIMIT ?"
            ),
            (fts_query, int(limit)),
        ).fetchall()
        excluded_current_path_count = int(
            connection.execute(
                (
                    f"SELECT COUNT(*) FROM {lane.fts_table} "
                    f"WHERE {lane.fts_table} MATCH ? "
                    "AND evi_is_current_path(path)=1"
                ),
                (fts_query,),
            ).fetchone()[0]
        )
    finally:
        connection.close()
    return {
        "rows": rows,
        "excluded_current_path_count": excluded_current_path_count,
    }


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
    """Read an already materialized WORKING authority without mutating it."""

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
    root = Path(project_root).resolve()
    repository = Path(repository_root).resolve()
    require(
        root.is_dir() and root.name == project_id,
        "PROJECT_WORKING_QUERY_BINDING_INVALID",
        "Working-sector query requires the exact governed project root.",
        status="BLOCKED",
    )
    pointer_path = root / "active_pointer.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    require(
        pointer.get("project_id") == project_id
        and pointer.get("accepted_pv") == accepted_pv
        and int(pointer.get("generation") or -1) == pointer_generation,
        "PROJECT_WORKING_QUERY_POINTER_MISMATCH",
        "Working-sector query cannot reinterpret the accepted pointer.",
        status="MISMATCH",
    )
    receipt_path = root / "sectors" / "working_migration_receipt.json"
    require(
        receipt_path.is_file(),
        "PROJECT_WORKING_QUERY_REFRESH_REQUIRED",
        "No materialized WORKING sector authority exists; invoke the explicit "
        "working-sector refresh action before querying.",
        status="BLOCKED",
    )
    projection_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    projected_identity = dict(projection_receipt.get("working_identity") or {})
    require(
        projection_receipt.get("project_id") == project_id
        and projection_receipt.get("historical_parent_pv") == accepted_pv
        and int(projection_receipt.get("pointer_generation") or -1)
        == pointer_generation
        and bool(projected_identity.get("working_identity_sha256")),
        "PROJECT_WORKING_QUERY_RECEIPT_MISMATCH",
        "The materialized WORKING projection receipt does not match this project.",
        status="MISMATCH",
    )
    live_repository = inspect_repository(repository)
    if expected_branch is not None:
        require(
            live_repository.branch == expected_branch,
            "PROJECT_WORKING_QUERY_BRANCH_MISMATCH",
            "The live branch differs from the query binding.",
            status="MISMATCH",
        )
    if expected_head is not None:
        require(
            live_repository.commit_sha == expected_head,
            "PROJECT_WORKING_QUERY_HEAD_MISMATCH",
            "The live HEAD differs from the query binding.",
            status="MISMATCH",
        )
    require(
        projected_identity.get("branch") == live_repository.branch
        and projected_identity.get("head") == live_repository.commit_sha,
        "PROJECT_WORKING_QUERY_REFRESH_REQUIRED",
        "The materialized WORKING projection is from another branch or HEAD; "
        "invoke the explicit refresh action before querying.",
        status="BLOCKED",
    )
    project_integrity_receipt = _validate_working_project_query_authority(
        root,
        repository_root=repository,
        project_id=project_id,
        accepted_pv=accepted_pv,
        pointer_generation=pointer_generation,
        projected_identity=projected_identity,
    )
    require(
        projection_receipt.get("migration_id")
        == project_integrity_receipt.get("migration_id"),
        "PROJECT_WORKING_QUERY_COMMITTED_RECEIPT_MISMATCH",
        "The in-bundle projection receipt and committed migration receipt disagree.",
        status="MISMATCH",
        project_id=project_id,
    )
    routes_path = root / "sectors" / "routes.json"
    routes_manifest = json.loads(routes_path.read_text(encoding="utf-8"))
    project_current_routes = dict(routes_manifest.get("routes") or {})
    require(
        routes_manifest.get("schema") == "evidence-lane.source-route-authority.v1"
        and all(
            isinstance(path, str) and lane_id in CANONICAL_LANE_IDS
            for path, lane_id in project_current_routes.items()
        ),
        "PROJECT_WORKING_QUERY_GLOBAL_SOURCE_ROUTES_MISMATCH",
        "The sealed all-lane source-route authority is invalid.",
        status="MISMATCH",
        project_id=project_id,
    )
    project_current_paths = set(project_current_routes)
    fts = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
    hits: list[dict[str, Any]] = []
    study_brains: list[dict[str, Any]] = []
    historical_query_receipts: list[dict[str, Any]] = []
    working_lane_integrity_receipts: list[dict[str, Any]] = []
    lane_traversal_receipts: list[dict[str, Any]] = []
    per_lane_limit = min(int(limit), 8)
    for lane_id in exact_lanes:
        lane = LANE_REGISTRY[lane_id]
        lane_root = root / "sectors" / lane_id
        working_lane_integrity_receipts.append(
            _validate_working_lane_query_authority(
                root / "sectors",
                lane_id=lane_id,
                project_id=project_id,
                accepted_pv=accepted_pv,
                pointer_generation=pointer_generation,
                working_identity_sha256=str(
                    projected_identity["working_identity_sha256"]
                ),
            )
        )
        database_path = lane_root / lane.sqlite_filename
        require(
            database_path.is_file(),
            "PROJECT_WORKING_QUERY_DATABASE_MISSING",
            "A selected WORKING sector has no canonical SQLite projection.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        traversal = validate_lane_query_traversal(lane_root, lane)
        lane_traversal_receipts.append(traversal)
        fts_table = str(traversal["selected_fts_table"])
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            rows = connection.execute(
                (
                    "SELECT CAST(chunk_id AS INTEGER), path, locator, "
                    f"snippet({fts_table}, 2, '[', ']', ' ... ', 24), "
                    f"bm25({fts_table}) FROM {fts_table} "
                    f"WHERE {fts_table} MATCH ? "
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
                    "authority_tier": "LIVE_WORKING",
                    "path": path,
                    "locator": locator,
                    "snippet": str(snippet)[:1000],
                    "rank": rank,
                }
            )

        historical_reference_path = lane_root / "historical_authority.ref.json"
        require(
            historical_reference_path.is_file(),
            "PROJECT_WORKING_QUERY_HISTORICAL_REFERENCE_MISSING",
            "A WORKING sector query requires its immutable accepted-history reference.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        historical_reference = json.loads(
            historical_reference_path.read_text(encoding="utf-8")
        )
        require(
            historical_reference.get("schema")
            == "evidence-lane.accepted-lane-history-reference.v1"
            and historical_reference.get("project_id") == project_id
            and historical_reference.get("lane_id") == lane_id
            and historical_reference.get("accepted_pv") == accepted_pv
            and historical_reference.get("independent_writable_authority") is False,
            "PROJECT_WORKING_QUERY_HISTORICAL_REFERENCE_MISMATCH",
            "A WORKING sector historical reference is not bound to this project lane and pointer.",
            status="MISMATCH",
            lane_id=lane_id,
        )
        historical_rows: list[tuple[Any, ...]] = []
        excluded_current_path_count = 0
        historical_state = str(historical_reference.get("state") or "")
        if historical_state == "IMMUTABLE_ACCEPTED_HISTORY":
            relative_authority = str(
                historical_reference.get("authority_relative_path") or ""
            )
            relative_database = str(
                historical_reference.get("database_relative_path") or ""
            )
            historical_manifest = (
                root / Path(relative_authority) / "lane_manifest.json"
            ).resolve()
            historical_database = (root / Path(relative_database)).resolve()
            require(
                bool(relative_authority)
                and bool(relative_database)
                and _is_relative_to(historical_manifest, root)
                and historical_manifest.is_file()
                and sha256_file(historical_manifest)
                == historical_reference.get("lane_manifest_sha256")
                and _is_relative_to(historical_database, root)
                and historical_database.is_file()
                and sha256_file(historical_database)
                == historical_reference.get("database_sha256"),
                "PROJECT_WORKING_QUERY_HISTORICAL_DATABASE_MISMATCH",
                "The immutable accepted-history database does not match its sealed lane reference.",
                status="MISMATCH",
                lane_id=lane_id,
            )
            historical_query = _query_root_nested_pv_history(
                historical_database,
                lane_id=lane_id,
                fts_query=fts,
                current_paths=project_current_paths,
                limit=per_lane_limit,
            )
            historical_rows = list(historical_query["rows"])
            excluded_current_path_count = int(
                historical_query["excluded_current_path_count"]
            )
        else:
            require(
                historical_state == "SCHEMA_READY_UNPOPULATED",
                "PROJECT_WORKING_QUERY_HISTORICAL_STATE_INVALID",
                "A WORKING sector historical reference has an unknown state.",
                status="MISMATCH",
                lane_id=lane_id,
            )
        for chunk_id, path, locator, snippet, rank in historical_rows:
            hits.append(
                {
                    "lane_id": lane_id,
                    "ref_id": (
                        f"root-history:{accepted_pv}:{lane_id}:chunk:{chunk_id}"
                    ),
                    "authority_tier": "ROOT_NESTED_IMMUTABLE_PV_HISTORY",
                    "path": path,
                    "locator": locator,
                    "snippet": str(snippet)[:1000],
                    "rank": rank,
                }
            )
        historical_query_receipts.append(
            {
                "lane_id": lane_id,
                "state": historical_state,
                "reference_sha256": sha256_file(historical_reference_path),
                "database_sha256": historical_reference.get("database_sha256"),
                "current_source_path_count": len(project_current_paths),
                "current_source_path_scope": "PROJECT_GLOBAL_ALL_WORKING_LANES",
                "root_nested_history_hit_count": len(historical_rows),
                "root_nested_rows_suppressed_by_current_path": (
                    excluded_current_path_count
                ),
                "query_order": "LIVE_WORKING_THEN_ROOT_NESTED_PV_HISTORY",
                "source_scope": "CURRENT_PROJECT_ROOT_ONLY",
                "accepted_archive_opened": False,
                "accepted_archive_queried": False,
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
        key=lambda row: (
            0 if row["authority_tier"] == "LIVE_WORKING" else 1,
            float(row["rank"]),
            row["lane_id"],
            row["path"],
        ),
    )[: int(limit)]
    return {
        "status": "PASS",
        "schema": "evidence-lane.working-sector-query.v2",
        "authority": "LIVE_PROJECT_ROOT_SIX_AUTHORITY_SECTOR_ARM",
        "project_id": project_id,
        "historical_parent_pv": accepted_pv,
        "pointer_generation": pointer_generation,
        "working_identity": projected_identity,
        "projection_state": "MATERIALIZED_BY_EXPLICIT_REFRESH",
        "projection_receipt_sha256": sha256_file(receipt_path),
        "project_integrity_receipt": project_integrity_receipt,
        "query_mutated_project_authority": False,
        "query_rehashed_dirty_content": False,
        "refresh": {
            "status": "NOT_PERFORMED",
            "reason": "READ_ONLY_QUERY_BOUNDARY",
        },
        "terms": terms,
        "queried_lane_ids": exact_lanes,
        "hits": bounded_hits,
        "hit_count": len(bounded_hits),
        "bounded_result_limit": int(limit),
        "result_truncated": len(hits) > int(limit),
        "working_lane_integrity_receipts": working_lane_integrity_receipts,
        "lane_traversal_receipts": lane_traversal_receipts,
        "lane_traversal_receipt_count": len(lane_traversal_receipts),
        "mmd_dot_traversal_used_for_every_queried_lane": (
            len(lane_traversal_receipts) == len(exact_lanes)
        ),
        "tools_json_query_role": "BUILD_REFRESH_PROVENANCE_ONLY",
        "historical_query_receipts": historical_query_receipts,
        "accepted_history_loaded_as_independent_writable_authority": False,
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
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
