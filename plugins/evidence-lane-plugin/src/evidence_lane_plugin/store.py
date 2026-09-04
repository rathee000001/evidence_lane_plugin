"""Immutable local project store with compare-and-swap accepted pointers."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import unicodedata
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from .authority_support import refresh_authority_support
from .capture_routing import CaptureRouteAuthority, normalize_capture_route
from .constants import POINTER_SCHEMA, PROJECT_REGISTRY_SCHEMA
from .errors import EvidenceLaneError, require
from .git_adapter import resolve_git_executable
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import CANONICAL_LANE_IDS
from .models import ActivePointer, ProjectConfig
from .plan_runtime import (
    DELTA_STATUSES,
    append_delta_event,
    append_planning_mode_event,
    append_sub_pv_acceptance,
    append_task_formula_event,
    ensure_event_ledger,
    plan_runtime_status,
    query_plan_runtime_projection,
    write_plan_runtime_projection,
)
from .project_authority import (
    PROJECT_AUTHORITY_CONFIRMATION,
    PROJECT_AUTHORITY_MIGRATION_SCHEMA,
    _remove_tree_with_retry,
    _replace_path_with_retry,
    copy_active_project_authority,
    is_working_sector_operational_member,
    materialize_project_authority_layout,
    materialize_project_authority_skeleton,
    refresh_working_sector_operational_checksums,
    remove_verified_active_source,
    resolved_chat_lineage_root,
    resolved_plan_auxiliary_path,
    resolved_plan_backlog_path,
    resolved_plan_runtime_path,
    validate_external_project_authority_root,
)
from .project_overlay import build_project_overlay, validate_project_overlay
from .project_pv_storage import (
    accepted_storage_status,
    build_project_pv_archive,
    materialized_project_pv_archive,
    validate_project_pv_archive,
    working_overlay_manifest,
)
from .pv_package import compare_package_bytes, validate_pv_package
from .redaction import redact
from .source_authority import (
    reconcile_legacy_source_authority_registry,
    snapshot_source_authority_registry,
)
from .tasking import classify_task
from .timeutil import utc_now

_PROJECT_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

_BATCH_COMPLETION_CONFIRMATION = "BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED"
_BATCH_CONTRACT_FIELDS = (
    "task_id",
    "sequence",
    "plan_id",
    "task_class",
    "requested_outcome",
    "permitted_paths",
    "permitted_tools",
    "acceptance_checks",
    "stop_condition",
)

_PLAN_PANEL_ROLES = {
    "STANDARD",
    "HIL_GATE",
    "PHYSICALLY_FINAL_HIL",
}

_HOST_PLAN_WINDOW_SIZE = 9

_GOAL_STATUS_BY_LIFECYCLE = {
    "ACTIVE": "in_progress",
    "QUEUED": "pending",
    "DONE": "completed",
    "ACCEPTED": "completed",
}

_PARKED_LIFECYCLE_STATUSES = {"DROPPED"}
_SUPERSEDED_LIFECYCLE_STATUSES = {"SUPERSEDED"}

_PLAN_METADATA_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_PLAN_VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9._-]+)?)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_PLAN_CURRENT_VERSION_DIRECTIVE_RE = re.compile(
    r"\bCURRENT_VERSION\s*=\s*"
    r"(v?\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9._-]+)?)",
    re.IGNORECASE,
)
_PLAN_BRANCH_RE = re.compile(r"\bagent/[A-Za-z0-9._/-]+", re.IGNORECASE)
_PLAN_CURRENT_BRANCH_DIRECTIVE_RE = re.compile(
    r"\bCURRENT_BRANCH\s*=\s*"
    r"([A-Za-z0-9_/-](?:[A-Za-z0-9._/-]*[A-Za-z0-9_/-])?)",
    re.IGNORECASE,
)
_PLAN_COMMIT_BATCH_DIRECTIVE_RE = re.compile(
    r"\bCOMMIT_BATCH\s*=\s*"
    r"([A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?)",
    re.IGNORECASE,
)
_PLAN_GIT_STAGE_DIRECTIVE_RE = re.compile(
    r"\bGIT_STAGE\s*=\s*"
    r"([A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?)",
    re.IGNORECASE,
)
_PLAN_GROUP_DIRECTIVE_RE = re.compile(
    r"\bPLAN_GROUP\s*=\s*"
    r"([A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?)",
    re.IGNORECASE,
)
_PLAN_DEPENDENCIES_DIRECTIVE_RE = re.compile(
    r"\bDEPENDS_ON\s*=\s*"
    r"([A-Za-z0-9._-]+(?:\s*[+,]\s*[A-Za-z0-9._-]+)*)",
    re.IGNORECASE,
)
_PLAN_CANDIDATE_PV_DIRECTIVE_RE = re.compile(
    r"\bCANDIDATE_PV\s*=\s*(PV\d+)\b",
    re.IGNORECASE,
)
_PLAN_ACCEPTED_PV_DIRECTIVE_RE = re.compile(
    r"\bACCEPTED_PV\s*=\s*(PV\d+)\b",
    re.IGNORECASE,
)
_PLAN_ACCEPTED_VERSION_DIRECTIVE_RE = re.compile(
    r"\bACCEPTED_VERSION\s*=\s*"
    r"(v?\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9._-]+)?)",
    re.IGNORECASE,
)
_PLAN_FALLBACK_OBSERVED_VERSION_DIRECTIVE_RE = re.compile(
    r"\bFALLBACK_OBSERVED_VERSION\s*=\s*"
    r"(v?\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9._-]+)?)",
    re.IGNORECASE,
)
_PLAN_FALLBACK_EXPECTED_ACCEPTED_VERSION_DIRECTIVE_RE = re.compile(
    r"\bFALLBACK_EXPECTED_ACCEPTED_VERSION\s*=\s*"
    r"(v?\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9._-]+)?)",
    re.IGNORECASE,
)


def _bounded_plan_metadata_id(value: Any, *, fallback: str) -> str:
    """Return one public-safe Plan metadata identifier without inventing it."""

    exact = str(value or "").strip()
    if not exact:
        return fallback
    require(
        len(exact) <= 128
        and all(character in _PLAN_METADATA_ID_CHARS for character in exact),
        "PLAN_PROJECTION_METADATA_ID_INVALID",
        "Plan group and commit-batch identifiers must be bounded public-safe IDs.",
        status="MISMATCH",
        value=exact,
    )
    return exact


def _bounded_plan_dependencies(value: Any, *, task_id: str) -> list[str]:
    """Validate explicit dependency IDs without inventing project topology."""

    require(
        isinstance(value, list)
        and len(value) <= 64
        and all(
            isinstance(dependency, str)
            and bool(dependency.strip())
            and len(dependency.strip()) <= 128
            and all(
                character in _PLAN_METADATA_ID_CHARS for character in dependency.strip()
            )
            for dependency in value
        ),
        "PLAN_DEPENDENCIES_INVALID",
        "Explicit Plan dependencies must be bounded public-safe task IDs.",
        status="MISMATCH",
        task_id=task_id,
    )
    dependencies = list(dict.fromkeys(dependency.strip() for dependency in value))
    require(
        task_id not in dependencies,
        "PLAN_DEPENDENCY_SELF_REFERENCE",
        "A Plan row may not depend on itself.",
        status="MISMATCH",
        task_id=task_id,
    )
    return dependencies


def _bounded_plan_version(value: Any, *, task_id: str) -> str:
    """Validate one exact current-version marker without guessing semantics."""

    exact = str(value or "").strip()
    if exact[:1].lower() == "v":
        exact = exact[1:]
    require(
        bool(exact) and _PLAN_VERSION_RE.fullmatch(exact) is not None,
        "PLAN_VERSION_MARKER_INVALID",
        "A current-version marker must be one bounded semantic version token.",
        status="MISMATCH",
        task_id=task_id,
        value=exact,
    )
    return exact


def _bounded_plan_branch(value: Any, *, task_id: str) -> str:
    """Validate one exact branch marker without treating a title as authority."""

    exact = str(value or "").strip()
    require(
        bool(exact)
        and len(exact) <= 192
        and not exact.startswith("/")
        and not exact.endswith("/")
        and ".." not in exact
        and all(
            character
            in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-"
            for character in exact
        ),
        "PLAN_BRANCH_MARKER_INVALID",
        "A current-branch marker must be one bounded public-safe branch name.",
        status="MISMATCH",
        task_id=task_id,
        value=exact,
    )
    return exact


def _latest_linked_directive(
    task: dict[str, Any],
    *,
    pattern: re.Pattern[str],
    directive_name: str,
) -> tuple[str | None, str | None]:
    """Return the latest exact linked-Delta directive and its provenance."""

    selected: tuple[str, str] | None = None
    for steer in task.get("steer_deltas") or []:
        if not isinstance(steer, dict):
            continue
        values = list(
            dict.fromkeys(
                match.group(1).strip()
                for match in pattern.finditer(str(steer.get("text") or ""))
            )
        )
        require(
            len(values) <= 1,
            "PLAN_LINKED_DIRECTIVE_CONFLICT",
            "One linked Delta contains conflicting exact Plan metadata directives.",
            status="MISMATCH",
            task_id=task.get("task_id"),
            delta_id=steer.get("delta_id"),
            directive=directive_name,
            values=values,
        )
        if values:
            selected = (
                values[0],
                f"LINKED_DELTA:{steer.get('delta_id') or 'UNKNOWN'!s}",
            )
    return selected or (None, None)


def _version_claims(task: dict[str, Any]) -> list[dict[str, str]]:
    """Inventory exact version tokens without deciding which claim is current."""

    sources = [
        (
            "TASK_CONTRACT",
            str(task.get("task_id") or ""),
            task.get("requested_outcome"),
        ),
        *[
            (
                "LINKED_DELTA",
                str(steer.get("delta_id") or "UNKNOWN"),
                steer.get("text"),
            )
            for steer in task.get("steer_deltas") or []
            if isinstance(steer, dict)
        ],
    ]
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for source_kind, source_id, text in sources:
        for match in _PLAN_VERSION_RE.finditer(str(text or "")):
            version = _bounded_plan_version(
                match.group(1), task_id=str(task.get("task_id") or "")
            )
            identity = (version, source_kind, source_id)
            if identity in seen:
                continue
            seen.add(identity)
            claims.append(
                {
                    "version": version,
                    "source_kind": source_kind,
                    "source_id": source_id,
                }
            )
    require(
        len(claims) <= 64,
        "PLAN_VERSION_CLAIMS_UNBOUNDED",
        "A Plan row contains too many version claims for deterministic projection.",
        status="MISMATCH",
        task_id=task.get("task_id"),
    )
    return claims


def _branch_claims(task: dict[str, Any]) -> list[dict[str, str]]:
    """Inventory exact governed branch tokens without selecting stale history."""

    sources = [
        (
            "TASK_CONTRACT",
            str(task.get("task_id") or ""),
            task.get("requested_outcome"),
        ),
        *[
            (
                "LINKED_DELTA",
                str(steer.get("delta_id") or "UNKNOWN"),
                steer.get("text"),
            )
            for steer in task.get("steer_deltas") or []
            if isinstance(steer, dict)
        ],
    ]
    claims: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for source_kind, source_id, text in sources:
        for match in _PLAN_BRANCH_RE.finditer(str(text or "")):
            branch = _bounded_plan_branch(
                match.group(0), task_id=str(task.get("task_id") or "")
            )
            identity = (branch, source_kind, source_id)
            if identity in seen:
                continue
            seen.add(identity)
            claims.append(
                {
                    "branch": branch,
                    "source_kind": source_kind,
                    "source_id": source_id,
                }
            )
    require(
        len(claims) <= 64,
        "PLAN_BRANCH_CLAIMS_UNBOUNDED",
        "A Plan row contains too many branch claims for deterministic projection.",
        status="MISMATCH",
        task_id=task.get("task_id"),
    )
    return claims


def _effective_version_marker(
    task: dict[str, Any],
    *,
    inherited_value: str | None = None,
    inherited_source: str | None = None,
) -> dict[str, Any]:
    """Resolve current version only from an exact declaration; conflicts stay visible."""

    task_id = str(task.get("task_id") or "")
    claims = _version_claims(task)
    linked_value, linked_source = _latest_linked_directive(
        task,
        pattern=_PLAN_CURRENT_VERSION_DIRECTIVE_RE,
        directive_name="CURRENT_VERSION",
    )
    if linked_value:
        marker = _bounded_plan_version(linked_value, task_id=task_id)
        source = str(linked_source)
    elif inherited_value:
        marker = _bounded_plan_version(inherited_value, task_id=task_id)
        source = str(inherited_source or "ACTIVE_PLAN_CONTEXT")
    else:
        explicit = task.get("current_version") or task.get("version_marker")
        if explicit:
            marker = _bounded_plan_version(explicit, task_id=task_id)
            source = "EXPLICIT_TASK_CONTRACT"
        else:
            unique = list(dict.fromkeys(claim["version"] for claim in claims))
            if len(unique) == 1:
                marker = unique[0]
                source = "SOLE_CURRENT_AUTHORITY_CLAIM"
            elif len(unique) > 1:
                marker = "CONFLICTING_DECLARATIONS"
                source = "RECONCILIATION_REQUIRED"
            else:
                marker = "NOT_DECLARED"
                source = "NO_CURRENT_AUTHORITY_CLAIM"
    return {
        "version_marker": marker,
        "version_marker_source": source,
        "version_claims": claims,
        "version_reconciliation_required": marker == "CONFLICTING_DECLARATIONS",
    }


def _effective_branch_marker(
    task: dict[str, Any],
    *,
    inherited_value: str | None = None,
    inherited_source: str | None = None,
) -> dict[str, Any]:
    """Resolve current branch only from an exact declaration; conflicts stay visible."""

    task_id = str(task.get("task_id") or "")
    claims = _branch_claims(task)
    linked_value, linked_source = _latest_linked_directive(
        task,
        pattern=_PLAN_CURRENT_BRANCH_DIRECTIVE_RE,
        directive_name="CURRENT_BRANCH",
    )
    if linked_value:
        marker = _bounded_plan_branch(linked_value, task_id=task_id)
        source = str(linked_source)
    elif inherited_value:
        marker = _bounded_plan_branch(inherited_value, task_id=task_id)
        source = str(inherited_source or "ACTIVE_PLAN_CONTEXT")
    else:
        explicit = task.get("current_branch") or task.get("git_branch")
        if explicit:
            marker = _bounded_plan_branch(explicit, task_id=task_id)
            source = "EXPLICIT_TASK_CONTRACT"
        else:
            unique = list(dict.fromkeys(claim["branch"] for claim in claims))
            if len(unique) == 1:
                marker = unique[0]
                source = "SOLE_CURRENT_AUTHORITY_CLAIM"
            elif len(unique) > 1:
                marker = "CONFLICTING_DECLARATIONS"
                source = "RECONCILIATION_REQUIRED"
            else:
                marker = "NOT_DECLARED"
                source = "NO_CURRENT_AUTHORITY_CLAIM"
    return {
        "branch_marker": marker,
        "branch_marker_source": source,
        "branch_claims": claims,
        "branch_reconciliation_required": marker == "CONFLICTING_DECLARATIONS",
    }


def _active_plan_release_context(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Project exact release directives from the sole ACTIVE executable row."""

    active = [task for task in tasks if task.get("status") == "ACTIVE"]
    if not active:
        return {
            "status": "NOT_DECLARED",
            "scope": "ACTIVE_AND_QUEUED_EXECUTABLE_ROWS_ONLY",
        }
    require(
        len(active) == 1,
        "PLAN_RELEASE_CONTEXT_MULTIPLE_ACTIVE_ROWS",
        "Current release context requires exactly one ACTIVE Plan row.",
        status="MISMATCH",
        active_task_ids=[task.get("task_id") for task in active],
    )
    task = active[0]
    task_id = str(task.get("task_id") or "")
    directives: tuple[tuple[str, re.Pattern[str], str, str], ...] = (
        (
            "target_version",
            _PLAN_CURRENT_VERSION_DIRECTIVE_RE,
            "CURRENT_VERSION",
            "version",
        ),
        (
            "target_branch",
            _PLAN_CURRENT_BRANCH_DIRECTIVE_RE,
            "CURRENT_BRANCH",
            "branch",
        ),
        (
            "candidate_pv_target",
            _PLAN_CANDIDATE_PV_DIRECTIVE_RE,
            "CANDIDATE_PV",
            "pv",
        ),
        (
            "accepted_pv",
            _PLAN_ACCEPTED_PV_DIRECTIVE_RE,
            "ACCEPTED_PV",
            "pv",
        ),
        (
            "accepted_version",
            _PLAN_ACCEPTED_VERSION_DIRECTIVE_RE,
            "ACCEPTED_VERSION",
            "version",
        ),
        (
            "fallback_observed_version",
            _PLAN_FALLBACK_OBSERVED_VERSION_DIRECTIVE_RE,
            "FALLBACK_OBSERVED_VERSION",
            "version",
        ),
        (
            "fallback_expected_accepted_version",
            _PLAN_FALLBACK_EXPECTED_ACCEPTED_VERSION_DIRECTIVE_RE,
            "FALLBACK_EXPECTED_ACCEPTED_VERSION",
            "version",
        ),
    )
    context: dict[str, Any] = {
        "status": "DECLARED",
        "source_task_id": task_id,
        "scope": "ACTIVE_AND_QUEUED_EXECUTABLE_ROWS_ONLY",
        "completed_rows_inherit_context": False,
        "history_rows_inherit_context": False,
        "creates_candidate": False,
        "moves_pointer": False,
    }
    for field, pattern, directive_name, value_kind in directives:
        value, source = _latest_linked_directive(
            task,
            pattern=pattern,
            directive_name=directive_name,
        )
        if not value:
            continue
        if value_kind == "version":
            exact = _bounded_plan_version(value, task_id=task_id)
        elif value_kind == "branch":
            exact = _bounded_plan_branch(value, task_id=task_id)
        else:
            exact = value.upper()
        context[field] = exact
        context[f"{field}_source"] = str(source)
    if "target_version" not in context and "target_branch" not in context:
        context["status"] = "NOT_DECLARED"
    return context


def _active_context_source(
    release_context: dict[str, Any] | None,
    field: str,
) -> str | None:
    """Return visible provenance for a marker inherited from the ACTIVE row."""

    if not release_context or field not in release_context:
        return None
    source = str(release_context.get(f"{field}_source") or "NOT_DECLARED")
    delta_id = source.removeprefix("LINKED_DELTA:")
    return (
        "ACTIVE_PLAN_CONTEXT:"
        f"{release_context.get('source_task_id') or 'UNKNOWN'}:{delta_id}"
    )


def _git_commit_stage(task: dict[str, Any]) -> tuple[str, str]:
    """Project Git intent only from an explicit contract or exact task text."""

    if task.get("current_contract_authority") == "ACTIVE_CONTRACT_REBIND":
        explicit = str(task.get("git_commit_stage") or "").strip().upper()
        if explicit:
            require(
                len(explicit) <= 64
                and all(character in _PLAN_METADATA_ID_CHARS for character in explicit),
                "PLAN_GIT_COMMIT_STAGE_INVALID",
                "An amended Git commit stage must be one bounded public-safe label.",
                status="MISMATCH",
                task_id=task.get("task_id"),
            )
            return explicit, "ACTIVE_CONTRACT_REBIND"
    linked_value, linked_source = _latest_linked_directive(
        task,
        pattern=_PLAN_GIT_STAGE_DIRECTIVE_RE,
        directive_name="GIT_STAGE",
    )
    if linked_value:
        explicit = linked_value.strip().upper()
        require(
            len(explicit) <= 64
            and all(character in _PLAN_METADATA_ID_CHARS for character in explicit),
            "PLAN_GIT_COMMIT_STAGE_INVALID",
            "An exact linked Git stage must be one bounded public-safe label.",
            status="MISMATCH",
            task_id=task.get("task_id"),
        )
        return explicit, str(linked_source)
    explicit = str(task.get("git_commit_stage") or "").strip().upper()
    if explicit:
        require(
            len(explicit) <= 64
            and all(character in _PLAN_METADATA_ID_CHARS for character in explicit),
            "PLAN_GIT_COMMIT_STAGE_INVALID",
            "An explicit Git commit stage must be one bounded public-safe label.",
            status="MISMATCH",
            task_id=task.get("task_id"),
        )
        return explicit, "EXPLICIT_TASK_CONTRACT"
    outcome = str(task.get("requested_outcome") or "")
    has_commit = re.search(r"\bcommit(?:ted|ting|s)?\b", outcome, re.IGNORECASE)
    has_push = re.search(r"\bpush(?:ed|ing|es)?\b", outcome, re.IGNORECASE)
    if has_commit and has_push:
        return "COMMIT_AND_PUSH", "EXACT_TASK_TEXT"
    if has_commit:
        return "COMMIT", "EXACT_TASK_TEXT"
    if has_push:
        return "PUSH", "EXACT_TASK_TEXT"
    return "NOT_DECLARED", "NO_EXPLICIT_CONTRACT_OR_TASK_TEXT"


def _plan_row_metadata(
    task: dict[str, Any],
    *,
    previous_executable_task_id: str | None,
    earlier_executable_task_ids: set[str],
    effective_for_execution: bool,
    active_release_context: dict[str, Any] | None = None,
    apply_active_release_context: bool = False,
) -> dict[str, Any]:
    """Build deterministic universal metadata for a host-visible Plan row."""

    active_contract_rebound = (
        task.get("current_contract_authority") == "ACTIVE_CONTRACT_REBIND"
    )
    linked_dependencies, linked_dependencies_source = (
        (None, None)
        if active_contract_rebound
        else _latest_linked_directive(
            task,
            pattern=_PLAN_DEPENDENCIES_DIRECTIVE_RE,
            directive_name="DEPENDS_ON",
        )
    )
    raw_dependencies = (
        re.split(r"\s*[+,]\s*", linked_dependencies)
        if linked_dependencies
        else task.get("dependencies")
    )
    if not effective_for_execution:
        dependencies = []
        dependency_source = "NON_EXECUTABLE_HISTORY"
    elif raw_dependencies is not None:
        dependencies = _bounded_plan_dependencies(
            raw_dependencies,
            task_id=str(task.get("task_id") or ""),
        )
        unknown_dependencies = sorted(set(dependencies) - earlier_executable_task_ids)
        require(
            not unknown_dependencies,
            "PLAN_DEPENDENCY_NOT_EARLIER_EXECUTABLE_ROW",
            "Explicit Plan dependencies must name earlier executable rows only.",
            status="MISMATCH",
            task_id=task.get("task_id"),
            invalid_dependencies=unknown_dependencies,
        )
        dependency_source = (
            str(linked_dependencies_source)
            if linked_dependencies
            else "EXPLICIT_TASK_CONTRACT"
        )
    elif previous_executable_task_id:
        dependencies = [previous_executable_task_id]
        dependency_source = "LINEAR_PREDECESSOR"
    else:
        dependencies = []
        dependency_source = "LINEAR_ROOT"
    linked_batch, linked_batch_source = (
        (None, None)
        if active_contract_rebound
        else _latest_linked_directive(
            task,
            pattern=_PLAN_COMMIT_BATCH_DIRECTIVE_RE,
            directive_name="COMMIT_BATCH",
        )
    )
    linked_group, linked_group_source = (
        (None, None)
        if active_contract_rebound
        else _latest_linked_directive(
            task,
            pattern=_PLAN_GROUP_DIRECTIVE_RE,
            directive_name="PLAN_GROUP",
        )
    )
    plan_group = _bounded_plan_metadata_id(
        linked_group or task.get("plan_group") or task.get("plan_id"),
        fallback="UNASSIGNED",
    )
    plan_group_source = (
        str(linked_group_source)
        if linked_group
        else (
            (
                "ACTIVE_CONTRACT_REBIND"
                if active_contract_rebound
                else "EXPLICIT_TASK_CONTRACT"
            )
            if task.get("plan_group")
            else "PLAN_ID_FALLBACK"
            if task.get("plan_id")
            else "NO_EXPLICIT_CONTRACT_OR_LINKED_DIRECTIVE"
        )
    )
    commit_batch_id = _bounded_plan_metadata_id(
        linked_batch or task.get("commit_batch_id") or task.get("batch_id"),
        fallback="UNASSIGNED",
    )
    commit_batch_source = (
        str(linked_batch_source)
        if linked_batch
        else (
            (
                "ACTIVE_CONTRACT_REBIND"
                if active_contract_rebound
                else "EXPLICIT_TASK_CONTRACT"
            )
            if task.get("commit_batch_id") or task.get("batch_id")
            else "NO_EXPLICIT_CONTRACT_OR_LINKED_DIRECTIVE"
        )
    )
    commit_stage, commit_stage_source = _git_commit_stage(task)
    inherited_version = (
        str(active_release_context.get("target_version"))
        if apply_active_release_context
        and active_release_context
        and active_release_context.get("target_version")
        else None
    )
    inherited_branch = (
        str(active_release_context.get("target_branch"))
        if apply_active_release_context
        and active_release_context
        and active_release_context.get("target_branch")
        else None
    )
    version = _effective_version_marker(
        task,
        inherited_value=inherited_version,
        inherited_source=_active_context_source(
            active_release_context,
            "target_version",
        ),
    )
    branch = _effective_branch_marker(
        task,
        inherited_value=inherited_branch,
        inherited_source=_active_context_source(
            active_release_context,
            "target_branch",
        ),
    )
    return {
        "task_classification": str(task.get("task_class") or "UNCLASSIFIED"),
        "plan_group": plan_group,
        "plan_group_source": plan_group_source,
        "commit_batch_id": commit_batch_id,
        "commit_batch_source": commit_batch_source,
        "dependencies": dependencies,
        "dependency_source": dependency_source,
        "git_commit_stage": commit_stage,
        "git_commit_stage_source": commit_stage_source,
        **version,
        **branch,
        "effective_for_execution": effective_for_execution,
    }


def _visible_plan_row_label(row: dict[str, Any]) -> str:
    """Return the compact deterministic host label; never embed Delta JSON."""

    dependencies = row.get("dependencies") or []
    dependency_label = "+".join(str(value) for value in dependencies) or "ROOT"
    return (
        f"Row {row['number']} / {row['task_id']} — "
        f"[CLASS={row['task_classification']}; GROUP={row['plan_group']}; "
        f"BATCH={row['commit_batch_id']}; DEP={dependency_label}; "
        f"GIT={row['git_commit_stage']}@{row['git_commit_stage_source']}; "
        f"VERSION={row['version_marker']}@{row['version_marker_source']}; "
        f"BRANCH={row['branch_marker']}@{row['branch_marker_source']}; "
        f"ROLE={row.get('panel_role') or 'STANDARD'}; "
        f"STATE={row['lifecycle_status']}] {row['step']}"
    )


def _persisted_host_plan_window_task_ids(project_root: Path) -> list[str] | None:
    """Read the exact persisted host batch without mutating session authority."""

    active_path = project_root / "active_session.json"
    if not active_path.is_file():
        return None
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        session_id = str(active.get("session_id") or "").strip()
        require(
            session_id.startswith("session_") and session_id.replace("_", "").isalnum(),
            "HOST_PLAN_WINDOW_SESSION_INVALID",
            "The active session cannot identify the persisted host Plan batch.",
            status="MISMATCH",
        )
        session_path = (project_root / "sessions" / f"{session_id}.json").resolve()
        session_path.relative_to(project_root.resolve())
        require(
            session_path.is_file(),
            "HOST_PLAN_WINDOW_SESSION_MISSING",
            "The active session record for the persisted host Plan batch is missing.",
            status="MISMATCH",
            session_id=session_id,
        )
        session = json.loads(session_path.read_text(encoding="utf-8"))
    except EvidenceLaneError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise EvidenceLaneError(
            "HOST_PLAN_WINDOW_SESSION_INVALID",
            "The persisted host Plan batch could not be read safely.",
            status="MISMATCH",
            details={"error": type(exc).__name__},
        ) from exc

    host_window = cast(
        dict[str, Any],
        cast(dict[str, Any], session.get("metadata") or {}).get("host_plan_window")
        or {},
    )
    raw_task_ids = host_window.get("window_task_ids")
    if raw_task_ids is None:
        return None
    require(
        isinstance(raw_task_ids, list),
        "HOST_PLAN_WINDOW_TASK_IDS_INVALID",
        "The persisted host Plan batch task IDs are invalid.",
        status="MISMATCH",
    )
    task_ids = [str(value).strip() for value in raw_task_ids]
    require(
        1 <= len(task_ids) <= _HOST_PLAN_WINDOW_SIZE
        and all(task_ids)
        and len(set(task_ids)) == len(task_ids),
        "HOST_PLAN_WINDOW_TASK_IDS_INVALID",
        "The persisted host Plan batch must contain one to nine unique task IDs.",
        status="MISMATCH",
        task_count=len(task_ids),
    )
    return task_ids


def _host_plan_window_fingerprint(
    status: dict[str, Any],
    *,
    fixed_window_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Hash only fields that can change the bounded host Step List."""

    goal = cast(dict[str, Any], status.get("goal_projection") or {})
    rows = cast(list[dict[str, Any]], goal.get("rows") or [])
    active_indexes = [
        index
        for index, row in enumerate(rows)
        if row.get("status") == "in_progress"
        and row.get("lifecycle_status") == "ACTIVE"
    ]
    require(
        len(active_indexes) <= 1,
        "HOST_PLAN_WINDOW_FINGERPRINT_ACTIVE_ROW_INVALID",
        "The host Plan window fingerprint requires at most one ACTIVE row.",
        status="MISMATCH",
        active_count=len(active_indexes),
    )
    if fixed_window_task_ids:
        row_indexes = {
            str(row.get("task_id") or ""): index for index, row in enumerate(rows)
        }
        first_task_id = fixed_window_task_ids[0]
        require(
            first_task_id in row_indexes,
            "HOST_PLAN_WINDOW_START_TASK_MISSING",
            "The persisted host Plan batch start task is not executable.",
            status="MISMATCH",
            task_id=first_task_id,
        )
        start = row_indexes[first_task_id]
    else:
        # Before a Plan is active this helper may fingerprint an empty host
        # surface for steer bookkeeping. It must never derive a visible batch.
        start = len(rows)
    window_rows = rows[start : start + _HOST_PLAN_WINDOW_SIZE]
    if active_indexes and fixed_window_task_ids:
        require(
            rows[active_indexes[0]] in window_rows,
            "HOST_PLAN_WINDOW_ACTIVE_ROW_OUTSIDE_PERSISTED_BATCH",
            "The sole ACTIVE row is outside the persisted host Plan batch.",
            status="MISMATCH",
            active_task_id=str(rows[active_indexes[0]].get("task_id") or ""),
            persisted_window_start=(
                fixed_window_task_ids[0] if fixed_window_task_ids else None
            ),
        )
    projected_rows = [
        {
            "number": int(row["number"]),
            "task_id": str(row["task_id"]),
            "status": str(row["status"]),
            "lifecycle_status": str(row["lifecycle_status"]),
            "task_classification": str(row["task_classification"]),
            "plan_group": str(row["plan_group"]),
            "commit_batch_id": str(row["commit_batch_id"]),
            "dependencies": list(row.get("dependencies") or []),
            "git_commit_stage": str(row["git_commit_stage"]),
            "panel_role": str(row.get("panel_role") or "STANDARD"),
        }
        for row in window_rows
    ]
    body = {
        "window_size": _HOST_PLAN_WINDOW_SIZE,
        "fixed_batch_bound": bool(fixed_window_task_ids),
        "projection_source": (
            "PERSISTED_FIXED_BATCH_PLUS_PLAN_SQLITE"
            if fixed_window_task_ids
            else "NO_HOST_BATCH_BOUND"
        ),
        "row_start": int(window_rows[0]["number"]) if window_rows else None,
        "row_end": int(window_rows[-1]["number"]) if window_rows else None,
        "active_task_id": (
            str(rows[active_indexes[0]]["task_id"]) if active_indexes else None
        ),
        "rows": projected_rows,
    }
    return {
        **body,
        "fingerprint_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def _next_plan_hil_task_id(tasks: list[dict[str, Any]]) -> str | None:
    """Return the next visible HIL row without treating stop text as a gate."""

    ordered = sorted(tasks, key=lambda row: int(row["sequence"]))
    active_sequence = next(
        (
            int(task["sequence"])
            for task in ordered
            if str(task.get("status")) == "ACTIVE"
        ),
        0,
    )
    unfinished = [
        task
        for task in ordered
        if int(task["sequence"]) > active_sequence
        and str(task.get("status")) not in {"ACCEPTED", "DONE", "SUPERSEDED"}
    ]
    for task in unfinished:
        if str(task.get("panel_role") or "").upper() in {
            "HIL_GATE",
            "PHYSICALLY_FINAL_HIL",
        }:
            return str(task["task_id"])
        outcome = str(task.get("requested_outcome") or "").upper()
        if "HIL" in outcome and (
            "PRESENT" in outcome or "DECISION" in outcome or "GATE" in outcome
        ):
            return str(task["task_id"])
    return None


class _ProjectLock:
    _process_locks: ClassVar[dict[str, threading.RLock]] = {}
    _guard: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def process_lock(cls, path: Path) -> threading.RLock:
        """Return the process-local lock shared by readers and writers."""

        with cls._guard:
            return cls._process_locks.setdefault(str(path), threading.RLock())

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self._lock = self.process_lock(path)
        self._fd: int | None = None

    def __enter__(self) -> Self:
        self._lock.acquire()
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._fd = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
                os.write(self._fd, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    self._lock.release()
                    raise EvidenceLaneError(
                        "PROJECT_STORE_LOCK_TIMEOUT",
                        "The project store remained locked.",
                        status="BLOCKED",
                        details={"lock": str(self.path)},
                    )
                time.sleep(0.05)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._fd is not None:
                os.close(self._fd)
            self.path.unlink(missing_ok=True)
        finally:
            self._lock.release()


class ProjectStore:
    def __init__(
        self,
        root: str | Path,
        *,
        configuration_source: str = "EXPLICIT_SERVICE_CONFIGURATION",
    ) -> None:
        raw_root = os.path.expandvars(os.fspath(root)).strip()
        require(
            bool(raw_root),
            "EVIDENCE_LANE_DATA_ROOT_INVALID",
            "The Evidence Lane data root cannot be empty.",
            status="BLOCKED",
        )
        try:
            resolved = Path(raw_root).expanduser().resolve()
            require(
                not resolved.exists() or resolved.is_dir(),
                "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE",
                "The configured Evidence Lane data root is not a directory.",
                status="BLOCKED",
                resolved_root=str(resolved),
            )
            resolved.mkdir(parents=True, exist_ok=True)
        except EvidenceLaneError:
            raise
        except OSError as exc:
            raise EvidenceLaneError(
                "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE",
                "The configured Evidence Lane data root could not be opened.",
                status="BLOCKED",
                details={
                    "resolved_root": str(Path(raw_root).expanduser()),
                    "os_error": type(exc).__name__,
                },
            ) from exc
        require(
            resolved.is_absolute()
            and resolved.is_dir()
            and os.access(resolved, os.R_OK | os.W_OK),
            "EVIDENCE_LANE_DATA_ROOT_UNAVAILABLE",
            "The configured Evidence Lane data root is not readable and writable.",
            status="BLOCKED",
            resolved_root=str(resolved),
        )
        self.root = resolved
        self.configuration_source = configuration_source

    @staticmethod
    def canonical_project_key(project_id: str) -> str:
        """Return the comparison-only key; the exact ID remains authoritative."""

        return unicodedata.normalize("NFKC", project_id).casefold()

    def _registry_path(self) -> Path:
        return self.root / "registry.json"

    def persisted_host_plan_window_task_ids(
        self,
        project_id: str,
    ) -> list[str] | None:
        """Return the fixed native host batch without creating a second projector."""

        return _persisted_host_plan_window_task_ids(self.project_root(project_id))

    def _registry_lock(self) -> _ProjectLock:
        return _ProjectLock(self.root / ".registry.lock")

    def _load_root_registry(self) -> dict[str, Any]:
        with _ProjectLock.process_lock(self.root / ".registry.lock"):
            return self._load_root_registry_unlocked()

    def _load_root_registry_unlocked(self) -> dict[str, Any]:
        path = self._registry_path()
        if not path.is_file():
            return {"schema": PROJECT_REGISTRY_SCHEMA, "projects": {}}
        try:
            registry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "PROJECT_REGISTRY_INVALID",
                "The Evidence Lane root project registry is unreadable.",
                status="FAIL",
                details={"registry": str(path), "error": type(exc).__name__},
            ) from exc
        require(
            registry.get("schema") == PROJECT_REGISTRY_SCHEMA
            and isinstance(registry.get("projects"), dict),
            "PROJECT_REGISTRY_INVALID",
            "The Evidence Lane root project registry has an invalid shape.",
            status="FAIL",
            registry=str(path),
        )
        return registry

    def _assert_exact_project_route(self, project_id: str) -> None:
        registry = self._load_root_registry()
        requested_key = self.canonical_project_key(project_id)
        collisions = sorted(
            existing_id
            for existing_id in registry["projects"]
            if self.canonical_project_key(existing_id) == requested_key
            and existing_id != project_id
        )
        require(
            not collisions,
            "PROJECT_ID_COLLISION",
            "The requested project ID collides by case or Unicode normalization with an existing exact binding.",
            status="BLOCKED",
            project_id=project_id,
            colliding_project_ids=collisions,
            normalization="NFKC_CASEFOLD_COMPARISON_EXACT_ID_AUTHORITY",
        )

    @staticmethod
    def _filesystem_route_key(path: str | Path) -> str:
        """Return the host-canonical identity for one absolute project route."""

        return os.path.normcase(str(Path(path).expanduser().resolve()))

    def _assert_project_authority_root_available(
        self,
        project_id: str,
        target: Path,
        *,
        registry: dict[str, Any] | None = None,
    ) -> None:
        """Keep an arbitrary user-selected root exclusive to one project ID."""

        exact_registry = (
            registry if registry is not None else self._load_root_registry()
        )
        target_key = self._filesystem_route_key(target)
        collisions = []
        for existing_id, row in exact_registry["projects"].items():
            if existing_id == project_id or not isinstance(row, dict):
                continue
            registered_root = str(row.get("project_authority_root") or "").strip()
            if not registered_root:
                continue
            if self._filesystem_route_key(registered_root) == target_key:
                collisions.append(existing_id)
        require(
            not collisions,
            "PROJECT_AUTHORITY_ROOT_BINDING_DUPLICATE",
            "The external Project/PV authority root is already bound to another governed project ID.",
            status="BLOCKED",
            project_id=project_id,
            target=str(target),
            existing_project_ids=sorted(collisions),
        )

    def inspect_root(self) -> dict[str, Any]:
        registry = self._load_root_registry()
        return {
            "schema": "evidence-lane.portable-store-root.v1",
            "status": "PASS",
            "resolved_root": str(self.root),
            "configuration_source": self.configuration_source,
            "root_is_absolute": self.root.is_absolute(),
            "root_exists": self.root.is_dir(),
            "root_readable": os.access(self.root, os.R_OK),
            "root_writable": os.access(self.root, os.W_OK),
            "durability_capability": "CONFIGURED_USER_DURABLE_FILESYSTEM",
            "projects_container": str(self.root / "projects"),
            "registered_project_count": len(registry["projects"]),
            "primary_runtime_storage": "PROJECT_LOCAL_SQLITE_UNLESS_EXPLICITLY_SELECTED_OTHERWISE",
            "google_drive_primary_runtime_allowed": False,
            "secret_values_persisted": False,
        }

    def inspect_project_route(self, project_id: str) -> dict[str, Any]:
        safe = self.validate_project_id(project_id)
        root = self.project_root(safe)
        legacy_root = self._legacy_project_root(safe)
        external = root != legacy_root
        return {
            "schema": "evidence-lane.project-store-route.v1",
            "status": "PASS",
            "project_id": safe,
            "canonical_comparison_key": self.canonical_project_key(safe),
            "canonical_id_policy": "ASCII_EXACT_WITH_NFKC_CASEFOLD_COLLISION_GUARD",
            "resolved_store_root": str(self.root),
            "relative_project_route": f"projects/{safe}",
            "resolved_project_root": str(root),
            "contained_beneath_store_root": not external,
            "project_authority_mode": (
                "EXPLICIT_USER_PROJECT_ROOT"
                if external
                else "LEGACY_COMBINED_STORE_ROOT"
            ),
            "governed_user_project_authority": external,
            "runtime_separated": external,
            "legacy_combined_project_root": str(legacy_root),
            "transport_project_binding": "EXPLICIT_PROJECT_ID_PER_PROJECT_SCOPED_TOOL",
            "cross_project_fallback_allowed": False,
            "secret_values_persisted": False,
        }

    @staticmethod
    def validate_project_id(project_id: str) -> str:
        require(
            bool(project_id)
            and len(project_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in project_id)
            and project_id not in {".", ".."}
            and not project_id.startswith("."),
            "PROJECT_ID_INVALID",
            "Project IDs may contain only letters, numbers, dot, underscore, and hyphen.",
            status="BLOCKED",
        )
        return project_id

    def _legacy_project_root(self, project_id: str) -> Path:
        safe = self.validate_project_id(project_id)
        result = (self.root / "projects" / safe).resolve()
        try:
            result.relative_to(self.root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "PROJECT_ROUTE_ESCAPE",
                "The legacy project route escaped the configured Evidence Lane control root.",
                status="BLOCKED",
                details={"project_id": safe},
            ) from exc
        return result

    def _registered_project_authority_root(self, project_id: str) -> Path | None:
        registry = self._load_root_registry()
        row = registry.get("projects", {}).get(project_id)
        if not isinstance(row, dict):
            return None
        raw = str(row.get("project_authority_root") or "").strip()
        if not raw:
            return None
        return validate_external_project_authority_root(
            raw,
            control_root=self.root,
            project_id=project_id,
        )

    def project_root(
        self,
        project_id: str,
        *,
        configured_root: str | Path | None = None,
    ) -> Path:
        safe = self.validate_project_id(project_id)
        self._assert_exact_project_route(safe)
        if configured_root is not None:
            return validate_external_project_authority_root(
                configured_root,
                control_root=self.root,
                project_id=safe,
            )
        registered = self._registered_project_authority_root(safe)
        return registered or self._legacy_project_root(safe)

    def uses_external_project_authority(self, project_id: str) -> bool:
        return self.project_root(project_id) != self._legacy_project_root(project_id)

    def project_authority_routes(self) -> list[tuple[str, Path]]:
        """Return every governed project ID with its current authority root.

        The root registry is authoritative for externally relocated projects.
        Legacy project directories are included for backward compatibility so
        host lifecycle discovery continues to work before a project has been
        refreshed into the registry. Distinct project IDs may never resolve to
        the same physical authority root.
        """

        registry = self._load_root_registry()
        project_ids = set(registry["projects"])
        legacy_container = self.root / "projects"
        if legacy_container.is_dir():
            for candidate in legacy_container.iterdir():
                if not candidate.is_dir():
                    continue
                try:
                    project_ids.add(self.validate_project_id(candidate.name))
                except EvidenceLaneError:
                    # Preserve the historical discovery behavior: unrelated or
                    # invalid directories under the legacy container are not
                    # project authority.
                    continue

        routes: list[tuple[str, Path]] = []
        route_owners: dict[str, str] = {}
        for project_id in sorted(
            project_ids,
            key=lambda value: (self.canonical_project_key(value), value),
        ):
            authority_root = self.project_root(project_id).resolve()
            route_key = os.path.normcase(str(authority_root))
            existing_owner = route_owners.get(route_key)
            require(
                existing_owner in {None, project_id},
                "PROJECT_AUTHORITY_ROUTE_COLLISION",
                "Distinct project IDs resolve to the same project authority root.",
                status="BLOCKED",
                project_id=project_id,
                existing_project_id=existing_owner,
                project_authority_root=str(authority_root),
            )
            if existing_owner is None:
                route_owners[route_key] = project_id
                routes.append((project_id, authority_root))
        return routes

    def _source_authority_path(self, project_id: str) -> Path:
        """Return the project-local registry path without creating it."""

        return self.project_root(project_id) / "sources" / "source_authority.sqlite"

    def source_authority_path(self, project_id: str) -> Path:
        reconcile_legacy_source_authority_registry(self.project_root(project_id))
        return self._source_authority_path(project_id)

    def source_authority_status(
        self, project_id: str, *, batch_id: str | None = None
    ) -> dict[str, Any]:
        reconcile_legacy_source_authority_registry(self.project_root(project_id))
        return snapshot_source_authority_registry(
            self._source_authority_path(project_id), batch_id
        )

    def _lock(self, project_id: str) -> _ProjectLock:
        return _ProjectLock(self.project_root(project_id) / ".store.lock")

    def state_travel_resume_lock(self, project_id: str) -> _ProjectLock:
        """Serialize one State Travel handoff consumption across MCP processes."""

        self.validate_project_id(project_id)
        return _ProjectLock(self.project_root(project_id) / ".state-travel-resume.lock")

    def register_project(self, config: ProjectConfig) -> dict[str, Any]:
        self.validate_project_id(config.project_id)
        config.capture_route = normalize_capture_route(config.capture_route)
        capture_binding: dict[str, Any]
        authority_skeleton: dict[str, Any] | None = None
        with self._registry_lock():
            registry = self._load_root_registry()
            canonical_key = self.canonical_project_key(config.project_id)
            repository_path_hash = sha256_bytes(config.repository_path.encode("utf-8"))
            id_collisions = sorted(
                existing_id
                for existing_id in registry["projects"]
                if existing_id != config.project_id
                and self.canonical_project_key(existing_id) == canonical_key
            )
            require(
                not id_collisions,
                "PROJECT_ID_COLLISION",
                "The project ID collides by case or Unicode normalization with an existing exact binding.",
                status="BLOCKED",
                project_id=config.project_id,
                colliding_project_ids=id_collisions,
            )
            source_collisions = sorted(
                existing_id
                for existing_id, row in registry["projects"].items()
                if existing_id != config.project_id
                and isinstance(row, dict)
                and row.get("repository_path_hash") == repository_path_hash
            )
            require(
                not source_collisions,
                "PROJECT_SOURCE_BINDING_DUPLICATE",
                "The repository path is already bound to another governed project ID.",
                status="BLOCKED",
                project_id=config.project_id,
                existing_project_ids=source_collisions,
            )
            root = self.project_root(
                config.project_id,
                configured_root=config.project_authority_root,
            )
            if config.project_authority_root is not None:
                self._assert_project_authority_root_available(
                    config.project_id,
                    root,
                    registry=registry,
                )
            root.mkdir(parents=True, exist_ok=True)
            with _ProjectLock(root / ".store.lock"):
                if config.project_authority_root is not None:
                    authority_skeleton = materialize_project_authority_skeleton(
                        root,
                        control_root=self.root,
                        project_id=config.project_id,
                        repository_path=config.repository_path,
                    )
                    folders = ["accepted", "receipts", "sessions"]
                else:
                    folders = [
                        "accepted",
                        "receipts",
                        "sessions",
                        "lineage",
                        "candidates",
                    ]
                for folder in folders:
                    (root / folder).mkdir(parents=True, exist_ok=True)
                project_path = root / "project.json"
                payload = {
                    "schema": PROJECT_REGISTRY_SCHEMA,
                    **config.as_dict(),
                }
                if project_path.exists():
                    existing = json.loads(project_path.read_text(encoding="utf-8"))
                    legacy_payload = {
                        key: value
                        for key, value in payload.items()
                        if key not in {"capture_route", "project_authority_root"}
                    }
                    compatibility_payload = dict(payload)
                    if compatibility_payload.get("project_authority_root") is None:
                        compatibility_payload.pop("project_authority_root", None)
                    require(
                        existing == payload
                        or existing == compatibility_payload
                        or (
                            "capture_route" not in existing
                            and existing == legacy_payload
                        ),
                        "PROJECT_REGISTRATION_CONFLICT",
                        "The project ID is already registered with different authority.",
                        status="MISMATCH",
                        project_id=config.project_id,
                    )
                else:
                    atomic_write_json(project_path, payload)
                pointer_path = root / "active_pointer.json"
                if not pointer_path.exists():
                    pointer = ActivePointer(
                        project_id=config.project_id,
                        accepted_pv=None,
                        accepted_manifest_sha256=None,
                        generation=0,
                        updated_at=utc_now(),
                    )
                    atomic_write_json(
                        pointer_path,
                        {"schema": POINTER_SCHEMA, **pointer.as_dict()},
                    )
                backlog_path = resolved_plan_backlog_path(root)
                plan_runtime_path = resolved_plan_runtime_path(root)
                if not backlog_path.is_file() or not plan_runtime_path.is_file():
                    initial_backlog = {
                        "schema": "evidence-lane.linear-task-backlog.v1",
                        "project_id": config.project_id,
                        "plans": [],
                        "tasks": [],
                    }
                    ensure_event_ledger(initial_backlog)
                    atomic_write_json(backlog_path, initial_backlog)
                    write_plan_runtime_projection(plan_runtime_path, initial_backlog)
                capture_binding = CaptureRouteAuthority(root).bind(
                    project_id=config.project_id,
                    route=config.capture_route,
                    selected_by="PROJECT_REGISTRATION",
                    reason="PROJECT_CAPTURE_ROUTE_SELECTED_BEFORE_INGESTION",
                )
                self._update_root_registry(config.project_id, payload, registry)
        return {
            **self.project_status(config.project_id),
            "capture_route_binding": capture_binding,
            "project_authority_skeleton": authority_skeleton,
        }

    def replace_branch_authority(
        self,
        project_id: str,
        *,
        branch: str,
        selected_by: str,
        repository: dict[str, Any],
        selection_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Replace, never broaden, the registered branch with one explicit branch."""

        exact_actor = selected_by.strip()
        require(
            bool(exact_actor),
            "PROJECT_BRANCH_SELECTION_ACTOR_REQUIRED",
            "Replacing branch authority requires the visible governed user.",
            status="BLOCKED",
        )
        project_path = self.project_root(project_id) / "project.json"
        with self._lock(project_id):
            require(
                project_path.is_file(),
                "PROJECT_NOT_REGISTERED",
                "The project is not registered in this Evidence Lane store.",
                status="MISMATCH",
                project_id=project_id,
            )
            existing = json.loads(project_path.read_text(encoding="utf-8"))
            prior_branches = list(existing.get("allowed_branches", []))
            if prior_branches == [branch]:
                return {
                    "status": "UNCHANGED",
                    "project_id": project_id,
                    "prior_allowed_branches": prior_branches,
                    "selected_branch": branch,
                    "authority_broadened": False,
                    "receipt": None,
                }
            updated = {**existing, "allowed_branches": [branch]}
            pointer = self.pointer(project_id)
            receipt_body = {
                "schema": "evidence-lane.branch-authority-selection.v1",
                "project_id": project_id,
                "prior_allowed_branches": prior_branches,
                "selected_branch": branch,
                "selected_by": exact_actor,
                "selected_at": utc_now(),
                "repository": repository,
                "pointer_generation": pointer.generation,
                "accepted_pv": pointer.accepted_pv,
                "authority_broadened": False,
                "remote_write_performed": False,
                "prior_project_sha256": sha256_bytes(canonical_json_bytes(existing)),
                "updated_project_sha256": sha256_bytes(canonical_json_bytes(updated)),
                **(
                    {"selection_context": selection_context}
                    if selection_context is not None
                    else {}
                ),
            }
            receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
            receipt = {
                **receipt_body,
                "receipt_id": f"branchauth_{receipt_sha256[:32].lower()}",
                "receipt_sha256": receipt_sha256,
            }
            receipt_path = (
                self.project_root(project_id)
                / "receipts"
                / f"{receipt['receipt_id']}.json"
            )
            atomic_write_json(receipt_path, receipt)
            atomic_write_json(project_path, updated)
        return {
            "status": "REPLACED",
            "project_id": project_id,
            "prior_allowed_branches": prior_branches,
            "selected_branch": branch,
            "authority_broadened": False,
            "receipt": receipt,
        }

    def _update_root_registry(
        self,
        project_id: str,
        project_payload: dict[str, Any],
        registry: dict[str, Any] | None = None,
    ) -> None:
        registry_path = self._registry_path()
        exact_registry = registry or self._load_root_registry()
        prior = exact_registry["projects"].get(project_id)
        prior_row = prior if isinstance(prior, dict) else {}
        explicit_root = str(project_payload.get("project_authority_root") or "").strip()
        exact_registry["projects"][project_id] = {
            "display_name": project_payload["display_name"],
            "enabled": project_payload["enabled"],
            "canonical_project_key": self.canonical_project_key(project_id),
            "relative_project_route": f"projects/{project_id}",
            "repository_path_hash": sha256_bytes(
                project_payload["repository_path"].encode("utf-8")
            ),
            **(
                {
                    "project_authority_root": explicit_root,
                    "project_authority_root_sha256": sha256_bytes(
                        explicit_root.encode("utf-8")
                    ),
                    "project_authority_mode": "EXPLICIT_USER_PROJECT_ROOT",
                }
                if explicit_root
                else {
                    key: prior_row[key]
                    for key in (
                        "project_authority_root",
                        "project_authority_root_sha256",
                        "project_authority_mode",
                    )
                    if key in prior_row
                }
            ),
        }
        atomic_write_json(registry_path, exact_registry)

    def _project_authority_registration_proof(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        registry = self._load_root_registry()
        row = registry.get("projects", {}).get(project_id)
        require(
            isinstance(row, dict),
            "PROJECT_AUTHORITY_REGISTRATION_PROOF_MISSING",
            "The exact root registry has no project authority binding.",
            status="MISMATCH",
            project_id=project_id,
        )
        registered = cast(dict[str, Any], row)
        root = self.project_root(project_id)
        registered_root = str(registered.get("project_authority_root") or "").strip()
        project_path = root / "project.json"
        require(
            bool(registered_root)
            and self._filesystem_route_key(registered_root)
            == self._filesystem_route_key(root)
            and registered.get("project_authority_root_sha256")
            == sha256_bytes(registered_root.encode("utf-8"))
            and registered.get("project_authority_mode")
            == "EXPLICIT_USER_PROJECT_ROOT"
            and project_path.is_file(),
            "PROJECT_AUTHORITY_REGISTRATION_PROOF_MISMATCH",
            "The root registry and external Project/PV authority route differ.",
            status="MISMATCH",
            project_id=project_id,
        )
        project = json.loads(project_path.read_text(encoding="utf-8"))
        require(
            project.get("project_id") == project_id
            and self._filesystem_route_key(
                str(project.get("project_authority_root") or root)
            )
            == self._filesystem_route_key(root),
            "PROJECT_AUTHORITY_REGISTRATION_PROJECT_MISMATCH",
            "The routed project registry does not bind the same authority root.",
            status="MISMATCH",
            project_id=project_id,
        )
        layout_path = root / "project_authority.json"
        layout_sha256: str | None = None
        if layout_path.is_file():
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            claimed_layout_sha256 = str(layout.get("layout_sha256") or "")
            layout_body = {
                key: value for key, value in layout.items() if key != "layout_sha256"
            }
            require(
                layout.get("project_id") == project_id
                and claimed_layout_sha256
                == sha256_bytes(canonical_json_bytes(layout_body)),
                "PROJECT_AUTHORITY_REGISTRATION_LAYOUT_MISMATCH",
                "The registered external layout failed its exact identity seal.",
                status="MISMATCH",
                project_id=project_id,
            )
            layout_sha256 = claimed_layout_sha256
        body = {
            "schema": "evidence-lane.project-authority-registration-proof.v1",
            "status": "PASS",
            "project_id": project_id,
            "registered_project_root": str(root),
            "registered_project_root_sha256": sha256_bytes(
                str(root).encode("utf-8")
            ),
            "registry_file_sha256": sha256_file(self._registry_path()),
            "registry_row_sha256": sha256_bytes(canonical_json_bytes(registered)),
            "project_registry_sha256": sha256_file(project_path),
            "layout_materialized": layout_sha256 is not None,
            "layout_sha256": layout_sha256,
            "route_recovered_from_registry": True,
            "idempotent_registration_proven": True,
            "candidate_created": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }
        return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}

    def project_authority_status(self, project_id: str) -> dict[str, Any]:
        """Return the bounded physical route and registry-derived layout state."""

        route = self.inspect_project_route(project_id)
        root = Path(route["resolved_project_root"])
        layout_path = root / "project_authority.json"
        manifest_path = root / "PROJECT_AUTHORITY_MANIFEST.json"
        layout = (
            json.loads(layout_path.read_text(encoding="utf-8"))
            if layout_path.is_file()
            else None
        )
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else None
        )
        registration_proof = (
            self._project_authority_registration_proof(project_id)
            if self.uses_external_project_authority(project_id)
            else None
        )
        lane_population = (
            dict(layout.get("lane_population") or {})
            if isinstance(layout, dict)
            else {}
        )
        return {
            "schema": "evidence-lane.project-authority-status.v1",
            "status": "PASS",
            "project_id": project_id,
            "route": route,
            "layout_materialized": isinstance(layout, dict),
            "canonical_lane_count": (
                layout.get("canonical_lane_count") if isinstance(layout, dict) else None
            ),
            "materialized_sector_directory_count": (
                layout.get("materialized_sector_directory_count")
                if isinstance(layout, dict)
                else 0
            ),
            "current_materialized_lane_count": lane_population.get(
                "current_materialized_lane_count"
            ),
            "accepted_history_materialized_lane_count": lane_population.get(
                "accepted_history_materialized_lane_count"
            ),
            "accepted_history_schema_ready_unpopulated_lane_count": (
                lane_population.get(
                    "accepted_history_schema_ready_unpopulated_lane_count"
                )
            ),
            "current_and_accepted_history_counts_are_separate": (
                lane_population.get(
                    "current_and_accepted_history_counts_are_separate"
                )
            ),
            "study_brain": (
                layout.get("study_brain") if isinstance(layout, dict) else None
            ),
            "layout_sha256": (
                layout.get("layout_sha256") if isinstance(layout, dict) else None
            ),
            "manifest_sha256": (
                manifest.get("manifest_sha256") if isinstance(manifest, dict) else None
            ),
            "registration_proof": registration_proof,
            "candidate_created": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }

    def ensure_project_authority_skeleton(self, project_id: str) -> dict[str, Any]:
        """Idempotently materialize the mandatory tree for one external project."""

        config = self.config(project_id)
        root = self.project_root(project_id)
        require(
            self.uses_external_project_authority(project_id),
            "PROJECT_AUTHORITY_SKELETON_EXTERNAL_ROOT_REQUIRED",
            "The current root skeleton is owned only by external Project/PV authority.",
            status="BLOCKED",
            project_id=project_id,
        )
        with self._lock(project_id):
            return materialize_project_authority_skeleton(
                root,
                control_root=self.root,
                project_id=project_id,
                repository_path=config.repository_path,
            )

    def project_pv_storage_status(self, project_id: str) -> dict[str, Any]:
        """Return the exact live-overlay/accepted layout without lifecycle writes."""

        root = self.project_root(project_id)
        pointer = self.pointer(project_id)
        return accepted_storage_status(
            root,
            project_id=project_id,
            accepted_pv=pointer.accepted_pv,
            pointer_generation=pointer.generation,
            accepted_manifest_sha256=pointer.accepted_manifest_sha256,
        )

    def migrate_project_authority(
        self,
        project_id: str,
        *,
        target_root: str | Path,
        selected_by: str,
        confirmation: str,
        expected_accepted_pv: str,
        expected_pointer_generation: int,
    ) -> dict[str, Any]:
        """Move active project authority externally; retain only legacy history."""

        actor = selected_by.strip()
        require(
            bool(actor) and confirmation == PROJECT_AUTHORITY_CONFIRMATION,
            "PROJECT_AUTHORITY_MIGRATION_CONFIRMATION_REQUIRED",
            "Project authority relocation requires the exact bounded move confirmation.",
            status="BLOCKED",
            required_confirmation=PROJECT_AUTHORITY_CONFIRMATION,
        )
        target = validate_external_project_authority_root(
            target_root,
            control_root=self.root,
            project_id=project_id,
        )
        self._assert_project_authority_root_available(project_id, target)
        current = self.project_root(project_id)
        if current == target:
            status = self.project_authority_status(project_id)
            pointer = self.pointer(project_id)
            require(
                status.get("layout_materialized") is True
                and pointer.accepted_pv == expected_accepted_pv
                and pointer.generation == expected_pointer_generation
                and cast(dict[str, Any], status.get("registration_proof") or {}).get(
                    "idempotent_registration_proven"
                )
                is True,
                "PROJECT_AUTHORITY_MIGRATION_INCOMPLETE",
                "The external route exists but its layout, pointer, or registration proof is incomplete.",
                status="MISMATCH",
            )
            return {**status, "state": "MIGRATED_IDEMPOTENT_REUSE"}

        legacy = self._legacy_project_root(project_id)
        require(
            current == legacy and legacy.is_dir() and not target.exists(),
            "PROJECT_AUTHORITY_MIGRATION_SOURCE_INVALID",
            "Relocation requires the exact registered legacy root and an absent target.",
            status="MISMATCH",
            current_root=str(current),
            legacy_root=str(legacy),
            target_root=str(target),
        )
        pointer = self.pointer(project_id)
        require(
            pointer.accepted_pv == expected_accepted_pv
            and pointer.generation == expected_pointer_generation
            and bool(pointer.accepted_manifest_sha256),
            "PROJECT_AUTHORITY_MIGRATION_POINTER_MISMATCH",
            "The accepted pointer changed before project authority relocation.",
            status="MISMATCH",
            accepted_pv=pointer.accepted_pv,
            pointer_generation=pointer.generation,
        )
        config = self.config(project_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        migration_identity = {
            "schema": PROJECT_AUTHORITY_MIGRATION_SCHEMA,
            "project_id": project_id,
            "source_root": str(legacy),
            "target_root": str(target),
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "selected_by": actor,
        }
        migration_sha256 = sha256_bytes(canonical_json_bytes(migration_identity))
        staging = (
            target.parent / f".{target.name}.migration-{migration_sha256[:16].lower()}"
        )
        journal_dir = self.root / "project-authority-migrations"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"{migration_sha256}.json"
        require(
            not staging.exists(),
            "PROJECT_AUTHORITY_MIGRATION_STAGING_CONFLICT",
            "A prior project authority staging root requires explicit recovery.",
            status="BLOCKED",
            staging_root=str(staging),
        )
        atomic_write_json(
            journal_path,
            {
                **migration_identity,
                "state": "COPYING_ACTIVE_AUTHORITY",
                "migration_sha256": migration_sha256,
                "staging_root": str(staging),
                "pointer_moved": False,
                "candidate_created": False,
                "hil_inferred": False,
            },
        )
        with _ProjectLock(legacy / ".store.lock", timeout=120.0):
            copy_receipt = copy_active_project_authority(
                legacy,
                staging,
                accepted_pv=expected_accepted_pv,
            )
            project_path = staging / "project.json"
            project_payload = json.loads(project_path.read_text(encoding="utf-8"))
            project_payload["project_authority_root"] = str(target)
            atomic_write_json(project_path, project_payload)
            layout = materialize_project_authority_layout(
                staging,
                published_root=target,
                control_root=self.root,
                project_id=project_id,
                repository_path=config.repository_path,
                accepted_pv=expected_accepted_pv,
                pointer_generation=expected_pointer_generation,
                accepted_manifest_sha256=str(pointer.accepted_manifest_sha256),
                legacy_history_root=legacy,
            )
            publish_replace = _replace_path_with_retry(
                staging,
                target,
                operation="PUBLISH_EXTERNAL_PROJECT_AUTHORITY",
            )
            with self._registry_lock():
                registry = self._load_root_registry_unlocked()
                row = registry.get("projects", {}).get(project_id)
                require(
                    isinstance(row, dict),
                    "PROJECT_AUTHORITY_REGISTRY_BINDING_MISSING",
                    "The project registry binding disappeared during relocation.",
                    status="MISMATCH",
                )
                row["project_authority_root"] = str(target)
                row["project_authority_root_sha256"] = sha256_bytes(
                    str(target).encode("utf-8")
                )
                row["project_authority_mode"] = "EXPLICIT_USER_PROJECT_ROOT"
                row["relative_project_route"] = f"projects/{project_id}"
                atomic_write_json(self._registry_path(), registry)

            routed_pointer = self.pointer(project_id)
            require(
                routed_pointer.as_dict() == pointer.as_dict()
                and self.project_root(project_id) == target,
                "PROJECT_AUTHORITY_POST_SWITCH_MISMATCH",
                "The published route or accepted pointer changed during relocation.",
                status="FAIL",
            )
            remove_verified_active_source(
                legacy,
                copied_roots=list(copy_receipt["copied_roots"]),
            )
            legacy_marker = {
                "schema": "evidence-lane.non-authoritative-legacy-history.v1",
                "project_id": project_id,
                "published_project_authority_root": str(target),
                "published_project_authority_root_sha256": sha256_bytes(
                    str(target).encode("utf-8")
                ),
                "publish_replace": publish_replace,
                "state": "NON_AUTHORITATIVE_HISTORY_AWAITING_R243_CAS_MIGRATION",
                "accepted_pointer_authority": False,
                "candidate_authority": False,
                "runtime_authority": False,
                "active_authority_bytes_removed_after_verification": True,
            }
            atomic_write_json(
                legacy / "NON_AUTHORITATIVE_LEGACY_HISTORY.json",
                legacy_marker,
            )

        registration_proof = self._project_authority_registration_proof(project_id)
        receipt_body = {
            **migration_identity,
            "status": "PASS",
            "state": "ACTIVE_PROJECT_AUTHORITY_MOVED_LEGACY_HISTORY_PRESERVED",
            "migration_sha256": migration_sha256,
            "copy_manifest_sha256": copy_receipt["source_manifest"]["manifest_sha256"],
            "copied_member_count": copy_receipt["source_manifest"]["member_count"],
            "copied_total_bytes": copy_receipt["source_manifest"]["total_bytes"],
            "layout_sha256": layout["layout_sha256"],
            "layout_manifest_sha256": layout["manifest_sha256"],
            "canonical_lane_count": layout["canonical_lane_count"],
            "materialized_sector_directory_count": layout[
                "materialized_sector_directory_count"
            ],
            "legacy_history_root": str(legacy),
            "legacy_history_authoritative": False,
            "registration_proof": registration_proof,
            "recovery_relocation_evidence": {
                "migration_journal": str(journal_path),
                "staging_root": str(staging),
                "publish_operation": publish_replace,
                "root_registry_route_verified": True,
                "idempotent_registration_recoverable": True,
            },
            "pointer_moved": False,
            "candidate_created": False,
            "hil_inferred": False,
            "completed_at": utc_now(),
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        atomic_write_json(
            target / "receipts" / "project-authority" / "migration.json",
            receipt,
        )
        atomic_write_json(
            journal_path,
            {**receipt, "journal_state": "COMMITTED"},
        )
        return receipt

    def _backlog_path(self, project_id: str) -> Path:
        return resolved_plan_backlog_path(self.project_root(project_id))

    def _plan_runtime_path(self, project_id: str) -> Path:
        return resolved_plan_runtime_path(self.project_root(project_id))

    def _plan_atomic_insertion_journal_path(
        self,
        project_id: str,
        batch_id: str,
    ) -> Path:
        return (
            resolved_plan_auxiliary_path(
                self.project_root(project_id), "plan_atomic_insertions"
            )
            / f"{batch_id}.json"
        )

    def _write_plan_atomic_insertion_journal(
        self,
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        """Persist one recoverable Plan insertion journal transition."""

        atomic_write_json(path, payload)
        refresh_working_sector_operational_checksums(
            path.parents[3],
            authority="PLAN",
        )

    def _persist_backlog(
        self,
        project_id: str,
        backlog: dict[str, Any],
    ) -> None:
        ensure_event_ledger(backlog)
        atomic_write_json(self._backlog_path(project_id), backlog)
        write_plan_runtime_projection(
            self._plan_runtime_path(project_id),
            backlog,
        )
        refresh_working_sector_operational_checksums(
            self.project_root(project_id),
            authority="PLAN",
        )

    def _load_backlog(self, project_id: str) -> dict[str, Any]:
        path = self._backlog_path(project_id)
        if not path.is_file():
            return {
                "schema": "evidence-lane.linear-task-backlog.v1",
                "project_id": project_id,
                "plans": [],
                "tasks": [],
            }
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(
            payload.get("schema") == "evidence-lane.linear-task-backlog.v1"
            and payload.get("project_id") == project_id,
            "TASK_BACKLOG_SCHEMA_MISMATCH",
            "The project task backlog is not a supported Evidence Lane authority.",
            status="MISMATCH",
        )
        return payload

    def plan_runtime_status(self, project_id: str) -> dict[str, Any]:
        self.config(project_id)
        backlog = self._load_backlog(project_id)
        return plan_runtime_status(
            self._plan_runtime_path(project_id),
            backlog,
        )

    def reconcile_dynamic_host_plan(
        self,
        project_id: str,
        *,
        snapshot: dict[str, Any],
        actor: str,
        expected_backlog_sha256: str,
        expected_runtime_sha256: str,
        protected_paths: list[str],
        expected_protected_file_sha256s: dict[str, str] | None = None,
        drop_contracts: list[dict[str, str]] | None = None,
        dry_run: bool = True,
        recorded_at: str | None = None,
        _fault_after_phase: str | None = None,
    ) -> dict[str, Any]:
        """Own one dynamic host projection under the canonical Plan lock.

        The canonical Plan remains the only ordered-work authority.  The host
        list is projection-only, native Changes remains ordinary Codex Changes,
        and the concise Goal never duplicates Plan rows.
        """

        from .host_plan_reconciliation import reconcile_dynamic_host_plan_files

        self.config(project_id)
        with self._lock(project_id):
            return reconcile_dynamic_host_plan_files(
                self.project_root(project_id),
                snapshot=snapshot,
                project_id=project_id,
                actor=actor,
                expected_backlog_sha256=expected_backlog_sha256,
                expected_runtime_sha256=expected_runtime_sha256,
                protected_paths=protected_paths,
                expected_protected_file_sha256s=(
                    expected_protected_file_sha256s
                ),
                drop_contracts=drop_contracts,
                dry_run=dry_run,
                recorded_at=recorded_at,
                _fault_after_phase=_fault_after_phase,
            )

    def plan_runtime_query(
        self,
        project_id: str,
        *,
        task_id: str | None = None,
        query: str | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        """Retrieve one exact Plan row or a bounded live-Plan FTS slice."""

        self.config(project_id)
        return query_plan_runtime_projection(
            self._plan_runtime_path(project_id),
            task_id=task_id,
            query=query,
            limit=limit,
        )

    def refresh_plan_runtime_projection(
        self,
        project_id: str,
        *,
        refresh_id: str,
        expected_backlog_sha256: str,
        expected_projection_content_sha256: str,
        refreshed_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """Rebuild only the disposable Plan SQLite index under exact CAS."""

        self.config(project_id)
        exact_refresh_id = str(refresh_id or "").strip()
        exact_actor = str(refreshed_by or "").strip()
        exact_reason = str(reason or "").strip()
        require(
            bool(exact_refresh_id)
            and len(exact_refresh_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in exact_refresh_id)
            and bool(exact_actor)
            and bool(exact_reason),
            "PLAN_RUNTIME_REFRESH_CONTRACT_INVALID",
            "A Plan runtime refresh requires one bounded identity, actor, and reason.",
            status="BLOCKED",
        )
        expected_backlog = str(expected_backlog_sha256 or "").strip().upper()
        expected_projection = (
            str(expected_projection_content_sha256 or "").strip().upper()
        )
        for field, value in (
            ("expected_backlog_sha256", expected_backlog),
            ("expected_projection_content_sha256", expected_projection),
        ):
            require(
                re.fullmatch(r"[0-9A-F]{64}", value) is not None,
                "PLAN_RUNTIME_REFRESH_SHA256_INVALID",
                "Plan runtime refresh authorities require exact SHA-256 values.",
                status="BLOCKED",
                field=field,
            )
        request_body = {
            "project_id": project_id,
            "refresh_id": exact_refresh_id,
            "expected_backlog_sha256": expected_backlog,
            "expected_projection_content_sha256": expected_projection,
            "refreshed_by": exact_actor,
            "reason": exact_reason,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request_body))
        receipt_path = (
            resolved_plan_auxiliary_path(
                self.project_root(project_id), "plan_runtime_refreshes"
            )
            / f"{exact_refresh_id}.json"
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            observed_backlog_sha256 = sha256_bytes(canonical_json_bytes(backlog))
            require(
                observed_backlog_sha256 == expected_backlog,
                "PLAN_RUNTIME_REFRESH_BACKLOG_MISMATCH",
                "The canonical Plan backlog changed before derived-index refresh.",
                status="MISMATCH",
                expected=expected_backlog,
                observed=observed_backlog_sha256,
                writes_performed=False,
            )
            before = plan_runtime_status(
                self._plan_runtime_path(project_id),
                copy.deepcopy(backlog),
            )
            require(
                before.get("expected_projection_content_sha256") == expected_projection,
                "PLAN_RUNTIME_REFRESH_PROJECTION_MISMATCH",
                "The source runtime expects a different derived Plan projection.",
                status="MISMATCH",
                expected=expected_projection,
                observed=before.get("expected_projection_content_sha256"),
                writes_performed=False,
            )
            existing: dict[str, Any] | None = None
            if receipt_path.is_file():
                try:
                    existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise EvidenceLaneError(
                        "PLAN_RUNTIME_REFRESH_RECEIPT_INVALID",
                        "The derived Plan refresh receipt is unreadable.",
                        status="FAIL",
                        details={"path": str(receipt_path)},
                    ) from exc
                require(
                    existing.get("schema")
                    == "evidence-lane.plan-runtime-refresh-receipt.v1"
                    and existing.get("request_sha256") == request_sha256,
                    "PLAN_RUNTIME_REFRESH_REPLAY_CONFLICT",
                    "The refresh identity already binds another request.",
                    status="BLOCKED",
                )
            receipt_reused = existing is not None
            rebuilt = before.get("status") != "PASS"
            if rebuilt:
                write_plan_runtime_projection(
                    self._plan_runtime_path(project_id),
                    backlog,
                )
            after = plan_runtime_status(
                self._plan_runtime_path(project_id),
                copy.deepcopy(backlog),
            )
            require(
                after.get("status") == "PASS"
                and after.get("projection_content_sha256") == expected_projection,
                "PLAN_RUNTIME_REFRESH_VERIFICATION_FAILED",
                "The rebuilt Plan SQLite projection did not match source authority.",
                status="FAIL",
                after_status=after.get("status"),
                observed_projection_content_sha256=after.get(
                    "projection_content_sha256"
                ),
            )
            if existing is None:
                receipt_body = {
                    "schema": "evidence-lane.plan-runtime-refresh-receipt.v1",
                    "status": "PASS",
                    **request_body,
                    "request_sha256": request_sha256,
                    "before_status": before.get("status"),
                    "before_sqlite_sha256": before.get("sqlite_sha256"),
                    "after_sqlite_sha256": after["sqlite_sha256"],
                    "projection_content_sha256": after["projection_content_sha256"],
                    "projection_rebuilt": rebuilt,
                    "recovered_unsealed_refresh": (
                        not rebuilt and before.get("status") == "PASS"
                    ),
                    "canonical_backlog_mutated": False,
                    "goal_completion_mutated": False,
                    "candidate_created": False,
                    "hil_invoked": False,
                    "pointer_moved": False,
                    "git_executed": False,
                    "install_executed": False,
                    "refreshed_at": utc_now(),
                }
                existing = {
                    **receipt_body,
                    "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
                }
                atomic_write_json(receipt_path, existing)
            refresh_working_sector_operational_checksums(
                self.project_root(project_id),
                authority="PLAN",
            )
        return {
            **cast(dict[str, Any], existing),
            "idempotent_replay": receipt_reused,
            "receipt_path": str(receipt_path),
        }

    def _normalize_plan_task_rows(
        self,
        tasks: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Normalize bounded task contracts without reading or writing Plan state."""

        normalized: list[dict[str, Any]] = []
        for position, task in enumerate(tasks, start=1):
            task_id = str(task.get("task_id", ""))
            require(
                bool(task_id)
                and len(task_id) <= 96
                and all(character in _PROJECT_ID_CHARS for character in task_id),
                "BACKLOG_TASK_ID_INVALID",
                "Every queued task requires a stable task ID.",
                status="BLOCKED",
                position=position,
            )
            requested_outcome = str(task.get("requested_outcome", "")).strip()
            stop_condition = str(task.get("stop_condition", "")).strip()
            require(
                bool(requested_outcome) and bool(stop_condition),
                "BACKLOG_TASK_CONTRACT_INCOMPLETE",
                "Every queued task requires an exact outcome and stop condition.",
                status="BLOCKED",
                task_id=task_id,
            )
            arrays: dict[str, list[str]] = {}
            for field, limit in (
                ("permitted_paths", 128),
                ("permitted_tools", 64),
                ("acceptance_checks", 32),
            ):
                values = task.get(field, [])
                require(
                    isinstance(values, list)
                    and len(values) <= limit
                    and all(isinstance(value, str) for value in values),
                    "BACKLOG_TASK_FIELD_INVALID",
                    "A queued task contains an invalid bounded list field.",
                    status="BLOCKED",
                    task_id=task_id,
                    field=field,
                )
                arrays[field] = list(dict.fromkeys(value.strip() for value in values))
            contract = classify_task(
                task_id=task_id,
                task_class=str(task.get("task_class", "")),
                requested_outcome=requested_outcome,
                permitted_paths=arrays["permitted_paths"],
                permitted_tools=arrays["permitted_tools"],
                acceptance_checks=arrays["acceptance_checks"],
                stop_condition=stop_condition,
            )
            normalized_task = {
                key: contract.as_dict()[key]
                for key in (
                    "task_id",
                    "task_class",
                    "requested_outcome",
                    "permitted_paths",
                    "permitted_tools",
                    "acceptance_checks",
                    "stop_condition",
                )
            }
            supersedes_task_id = str(task.get("supersedes_task_id") or "").strip()
            if supersedes_task_id:
                require(
                    supersedes_task_id != task_id
                    and len(supersedes_task_id) <= 96
                    and all(
                        character in _PROJECT_ID_CHARS
                        for character in supersedes_task_id
                    ),
                    "DELTA_SUPERSEDES_TASK_ID_INVALID",
                    "A superseding Delta must name one different stable task ID.",
                    status="BLOCKED",
                    task_id=task_id,
                    supersedes_task_id=supersedes_task_id,
                )
                normalized_task["supersedes_task_id"] = supersedes_task_id
            panel_role = str(task.get("panel_role") or "").strip().upper()
            if panel_role:
                require(
                    panel_role in _PLAN_PANEL_ROLES,
                    "BACKLOG_TASK_PANEL_ROLE_INVALID",
                    "A queued task contains an unsupported persistent-panel role.",
                    status="BLOCKED",
                    task_id=task_id,
                    panel_role=panel_role,
                    supported=sorted(_PLAN_PANEL_ROLES),
                )
                normalized_task["panel_role"] = panel_role
            plan_group = str(task.get("plan_group") or "").strip()
            if plan_group:
                normalized_task["plan_group"] = _bounded_plan_metadata_id(
                    plan_group,
                    fallback="UNASSIGNED",
                )
            commit_batch_id = str(
                task.get("commit_batch_id") or task.get("batch_id") or ""
            ).strip()
            if commit_batch_id:
                normalized_task["commit_batch_id"] = _bounded_plan_metadata_id(
                    commit_batch_id,
                    fallback="UNASSIGNED",
                )
            if "dependencies" in task:
                normalized_task["dependencies"] = _bounded_plan_dependencies(
                    task.get("dependencies"),
                    task_id=task_id,
                )
            git_commit_stage = str(task.get("git_commit_stage") or "").strip().upper()
            if git_commit_stage:
                normalized_task["git_commit_stage"] = _git_commit_stage(
                    {**normalized_task, "git_commit_stage": git_commit_stage}
                )[0]
            current_version = str(
                task.get("current_version") or task.get("version_marker") or ""
            ).strip()
            if current_version:
                normalized_task["current_version"] = _bounded_plan_version(
                    current_version,
                    task_id=task_id,
                )
            current_branch = str(
                task.get("current_branch") or task.get("git_branch") or ""
            ).strip()
            if current_branch:
                normalized_task["current_branch"] = _bounded_plan_branch(
                    current_branch,
                    task_id=task_id,
                )
            normalized.append(normalized_task)
        ids = [task["task_id"] for task in normalized]
        require(
            len(ids) == len(set(ids)),
            "BACKLOG_TASK_ID_DUPLICATE",
            "A task plan may not repeat a task ID.",
            status="BLOCKED",
        )
        return normalized

    def plan_tasks(
        self,
        project_id: str,
        *,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str,
        insert_before_task_id: str | None = None,
        insert_before_next_hil: bool = False,
        normalization_transition_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a bounded queue; planning never creates parallel active tasks."""
        self.config(project_id)
        require(
            1 <= len(tasks) <= 100,
            "TASK_PLAN_SIZE_INVALID",
            "A linear task plan must contain between one and one hundred tasks.",
            status="BLOCKED",
            count=len(tasks),
        )
        require(
            bool(planned_by.strip()),
            "TASK_PLAN_ACTOR_REQUIRED",
            "Task planning requires the visible human or agent identity.",
            status="BLOCKED",
        )
        require(
            bool(plan_id)
            and len(plan_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in plan_id),
            "TASK_PLAN_ID_INVALID",
            "The task plan ID is invalid.",
            status="BLOCKED",
        )
        normalized = self._normalize_plan_task_rows(tasks)
        ids = [task["task_id"] for task in normalized]
        exact_insert_before = str(insert_before_task_id or "").strip()
        if exact_insert_before:
            require(
                len(exact_insert_before) <= 96
                and all(
                    character in _PROJECT_ID_CHARS for character in exact_insert_before
                ),
                "TASK_PLAN_INSERTION_TARGET_INVALID",
                "A task-plan insertion target must be one stable task ID.",
                status="BLOCKED",
                insert_before_task_id=exact_insert_before,
            )
        input_body: dict[str, Any] = {
            "planned_by": planned_by.strip(),
            "tasks": normalized,
        }
        if exact_insert_before:
            input_body["insert_before_task_id"] = exact_insert_before
        if insert_before_next_hil:
            input_body["insert_before_next_hil"] = True
        exact_normalization_id = str(normalization_transition_id or "").strip()
        if exact_normalization_id:
            input_body["normalization_transition_id"] = exact_normalization_id
        input_sha256 = sha256_bytes(canonical_json_bytes(input_body))
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            existing_plan = next(
                (plan for plan in backlog["plans"] if plan["plan_id"] == plan_id),
                None,
            )
            if existing_plan:
                require(
                    existing_plan["input_sha256"] == input_sha256,
                    "TASK_PLAN_ID_CONFLICT",
                    "The task plan ID already binds a different task queue.",
                    status="BLOCKED",
                )
                self._persist_backlog(project_id, backlog)
                return self.backlog_status(project_id)
            existing_ids = {task["task_id"] for task in backlog["tasks"]}
            require(
                not existing_ids.intersection(ids),
                "BACKLOG_TASK_ALREADY_EXISTS",
                "A queued task ID already exists in this project.",
                status="BLOCKED",
                duplicates=sorted(existing_ids.intersection(ids)),
            )
            superseded_ids = {
                str(task.get("supersedes_task_id"))
                for task in normalized
                if task.get("supersedes_task_id")
            }
            missing_superseded = sorted(superseded_ids - existing_ids)
            require(
                not missing_superseded,
                "DELTA_SUPERSEDED_TASK_NOT_FOUND",
                "A superseding Delta references a task outside the existing backlog.",
                status="MISMATCH",
                task_ids=missing_superseded,
            )
            resolved_insert_before = exact_insert_before
            if not resolved_insert_before and insert_before_next_hil:
                resolved_insert_before = _next_plan_hil_task_id(backlog["tasks"]) or ""
            if resolved_insert_before:
                require(
                    resolved_insert_before in existing_ids,
                    "TASK_PLAN_INSERTION_TARGET_NOT_FOUND",
                    "The requested pre-HIL insertion target is not in the Plan Lane.",
                    status="MISMATCH",
                    insert_before_task_id=resolved_insert_before,
                )
            insertion_sequence = (
                int(
                    next(
                        task["sequence"]
                        for task in backlog["tasks"]
                        if str(task["task_id"]) == resolved_insert_before
                    )
                )
                if resolved_insert_before
                else len(backlog["tasks"]) + 1
            )
            earlier_executable_ids = {
                str(task["task_id"])
                for task in backlog["tasks"]
                if int(task["sequence"]) < insertion_sequence
                and str(task.get("status")) in _GOAL_STATUS_BY_LIFECYCLE
                and str(task["task_id"]) not in superseded_ids
            }
            for task in normalized:
                explicit_dependencies = task.get("dependencies")
                if explicit_dependencies is not None:
                    invalid_dependencies = sorted(
                        set(explicit_dependencies) - earlier_executable_ids
                    )
                    require(
                        not invalid_dependencies,
                        "PLAN_DEPENDENCY_NOT_EARLIER_EXECUTABLE_ROW",
                        "Explicit Plan dependencies must name earlier executable rows only.",
                        status="MISMATCH",
                        task_id=task["task_id"],
                        invalid_dependencies=invalid_dependencies,
                    )
                earlier_executable_ids.add(str(task["task_id"]))
            planned_at = utc_now()
            insertion_index = len(backlog["tasks"])
            first_sequence = len(backlog["tasks"]) + 1
            if resolved_insert_before:
                insertion_index = next(
                    index
                    for index, task in enumerate(backlog["tasks"])
                    if str(task["task_id"]) == resolved_insert_before
                )
                first_sequence = int(backlog["tasks"][insertion_index]["sequence"])
                for existing_task in backlog["tasks"]:
                    if int(existing_task["sequence"]) >= first_sequence:
                        existing_task["sequence"] = int(
                            existing_task["sequence"]
                        ) + len(normalized)
            added_tasks = [
                {
                    **task,
                    "sequence": first_sequence + offset,
                    "plan_id": plan_id,
                    "status": "QUEUED",
                    "planned_at": planned_at,
                    "history": [],
                }
                for offset, task in enumerate(normalized)
            ]
            backlog["tasks"][insertion_index:insertion_index] = added_tasks
            plan_row = {
                "plan_id": plan_id,
                "planned_by": planned_by.strip(),
                "input_sha256": input_sha256,
                "task_ids": ids,
                "planned_at": planned_at,
            }
            if resolved_insert_before:
                plan_row["insert_before_task_id"] = resolved_insert_before
            if exact_normalization_id:
                plan_row["normalization_transition_id"] = exact_normalization_id
            backlog["plans"].append(plan_row)
            for task in added_tasks:
                append_delta_event(
                    backlog,
                    task_id=task["task_id"],
                    event_type="ADDED",
                    to_status="QUEUED",
                    actor=planned_by.strip(),
                    event_id=f"{plan_id}__{task['task_id']}__added",
                    recorded_at=planned_at,
                    assume_initialized=True,
                    details={
                        "plan_id": plan_id,
                        "sequence": task["sequence"],
                        "insert_before_task_id": resolved_insert_before or None,
                    },
                )
            tasks_by_id = {str(row["task_id"]): row for row in backlog["tasks"]}
            for task in added_tasks:
                linked_task_id = str(task.get("supersedes_task_id") or "")
                if not linked_task_id:
                    continue
                superseded = tasks_by_id[linked_task_id]
                append_delta_event(
                    backlog,
                    task_id=linked_task_id,
                    event_type=(
                        "PLAN_NORMALIZATION_SUPERSEDED"
                        if exact_normalization_id
                        else "SUPERSEDED"
                    ),
                    to_status="SUPERSEDED",
                    actor=planned_by.strip(),
                    event_id=(
                        f"{plan_id}__{linked_task_id}__superseded_by__{task['task_id']}"
                    ),
                    recorded_at=planned_at,
                    assume_initialized=True,
                    details={
                        "replacement_task_id": task["task_id"],
                        "normalization_transition_id": (exact_normalization_id or None),
                    },
                )
                superseded["superseded_by_task_id"] = task["task_id"]
            self._persist_backlog(project_id, backlog)
        return self.backlog_status(project_id)

    def _build_plan_atomic_insertion_candidate(
        self,
        backlog: dict[str, Any],
        *,
        insertions: list[dict[str, Any]],
        planned_by: str,
        plan_id: str,
        batch_id: str,
        research_batch_sha256: str,
        input_sha256: str,
        planned_at: str,
        expected_physical_final_task_id: str,
    ) -> tuple[dict[str, Any], list[str]]:
        """Build and validate one multi-target Plan insertion in memory."""

        candidate = copy.deepcopy(backlog)
        ensure_event_ledger(candidate)
        ordered = sorted(candidate["tasks"], key=lambda row: int(row["sequence"]))
        require(
            [int(row["sequence"]) for row in ordered]
            == list(range(1, len(ordered) + 1)),
            "PLAN_SEQUENCE_NOT_CONTIGUOUS",
            "Atomic insertion requires one contiguous canonical Plan sequence.",
            status="MISMATCH",
        )
        existing_ids = {str(row["task_id"]) for row in ordered}
        target_ids = [str(group["insert_before_task_id"]) for group in insertions]
        missing_targets = sorted(set(target_ids) - existing_ids)
        require(
            not missing_targets,
            "TASK_PLAN_INSERTION_TARGET_NOT_FOUND",
            "One or more atomic insertion targets are absent from the Plan Lane.",
            status="MISMATCH",
            task_ids=missing_targets,
        )
        final_rows = [
            row
            for row in ordered
            if str(row.get("panel_role") or "").upper() == "PHYSICALLY_FINAL_HIL"
        ]
        require(
            len(final_rows) == 1
            and str(final_rows[0]["task_id"]) == expected_physical_final_task_id
            and str(ordered[-1]["task_id"]) == expected_physical_final_task_id,
            "PLAN_PHYSICAL_FINAL_HIL_MISMATCH",
            "Atomic insertion requires the expected HIL identity to remain physically final.",
            status="MISMATCH",
            expected_task_id=expected_physical_final_task_id,
            observed_task_ids=[str(row.get("task_id")) for row in final_rows],
            observed_last_task_id=(str(ordered[-1]["task_id"]) if ordered else None),
        )
        all_new_tasks = [task for group in insertions for task in group["tasks"]]
        new_ids = [str(task["task_id"]) for task in all_new_tasks]
        require(
            not existing_ids.intersection(new_ids),
            "BACKLOG_TASK_ALREADY_EXISTS",
            "An atomic insertion task ID already exists in this project.",
            status="BLOCKED",
            duplicates=sorted(existing_ids.intersection(new_ids)),
        )
        superseded_ids = [
            str(task["supersedes_task_id"])
            for task in all_new_tasks
            if task.get("supersedes_task_id")
        ]
        require(
            len(superseded_ids) == len(set(superseded_ids)),
            "DELTA_SUPERSEDED_TASK_DUPLICATE",
            "One atomic insertion may supersede an existing task only once.",
            status="BLOCKED",
        )
        missing_superseded = sorted(set(superseded_ids) - existing_ids)
        require(
            not missing_superseded,
            "DELTA_SUPERSEDED_TASK_NOT_FOUND",
            "A superseding Delta references a task outside the existing backlog.",
            status="MISMATCH",
            task_ids=missing_superseded,
        )
        added_by_target: dict[str, list[dict[str, Any]]] = {}
        for group in insertions:
            target_id = str(group["insert_before_task_id"])
            added_by_target[target_id] = [
                {
                    **task,
                    "plan_id": plan_id,
                    "status": "QUEUED",
                    "planned_at": planned_at,
                    "history": [],
                }
                for task in group["tasks"]
            ]
        merged: list[dict[str, Any]] = []
        insertion_target_by_task: dict[str, str] = {}
        for existing in ordered:
            target_id = str(existing["task_id"])
            for added in added_by_target.get(target_id, []):
                merged.append(added)
                insertion_target_by_task[str(added["task_id"])] = target_id
            merged.append(existing)
        for sequence, task in enumerate(merged, start=1):
            task["sequence"] = sequence
        candidate["tasks"] = merged
        candidate["plans"].append(
            {
                "plan_id": plan_id,
                "planned_by": planned_by,
                "input_sha256": input_sha256,
                "task_ids": new_ids,
                "planned_at": planned_at,
                "atomic_insertion": {
                    "batch_id": batch_id,
                    "research_batch_sha256": research_batch_sha256,
                    "group_count": len(insertions),
                    "insert_before_task_ids": target_ids,
                },
            }
        )
        for task in all_new_tasks:
            task_id = str(task["task_id"])
            added = next(row for row in merged if str(row["task_id"]) == task_id)
            append_delta_event(
                candidate,
                task_id=task_id,
                event_type="ADDED",
                to_status="QUEUED",
                actor=planned_by,
                event_id=f"{plan_id}__{task_id}__added",
                recorded_at=planned_at,
                assume_initialized=True,
                details={
                    "plan_id": plan_id,
                    "sequence": added["sequence"],
                    "atomic_batch_id": batch_id,
                    "insert_before_task_id": insertion_target_by_task[task_id],
                },
            )
        tasks_by_id = {str(row["task_id"]): row for row in candidate["tasks"]}
        for task in all_new_tasks:
            task_id = str(task["task_id"])
            linked_task_id = str(task.get("supersedes_task_id") or "")
            if not linked_task_id:
                continue
            append_delta_event(
                candidate,
                task_id=linked_task_id,
                event_type="SUPERSEDED",
                to_status="SUPERSEDED",
                actor=planned_by,
                event_id=f"{plan_id}__{linked_task_id}__superseded_by__{task_id}",
                recorded_at=planned_at,
                assume_initialized=True,
                details={
                    "replacement_task_id": task_id,
                    "atomic_batch_id": batch_id,
                },
            )
            tasks_by_id[linked_task_id]["superseded_by_task_id"] = task_id
        earlier_executable_ids: set[str] = set()
        for task in candidate["tasks"]:
            if str(task.get("status")) not in _GOAL_STATUS_BY_LIFECYCLE:
                continue
            active_contract_rebound = (
                task.get("current_contract_authority") == "ACTIVE_CONTRACT_REBIND"
            )
            linked_dependencies, _ = (
                (None, None)
                if active_contract_rebound
                else _latest_linked_directive(
                    task,
                    pattern=_PLAN_DEPENDENCIES_DIRECTIVE_RE,
                    directive_name="DEPENDS_ON",
                )
            )
            raw_dependencies = (
                re.split(r"\s*[+,]\s*", linked_dependencies)
                if linked_dependencies
                else task.get("dependencies")
            )
            if raw_dependencies is not None:
                dependencies = _bounded_plan_dependencies(
                    raw_dependencies,
                    task_id=str(task.get("task_id") or ""),
                )
                invalid_dependencies = sorted(
                    set(dependencies) - earlier_executable_ids
                )
                require(
                    not invalid_dependencies,
                    "PLAN_DEPENDENCY_NOT_EARLIER_EXECUTABLE_ROW",
                    "Explicit Plan dependencies must name earlier executable rows only.",
                    status="MISMATCH",
                    task_id=task["task_id"],
                    invalid_dependencies=invalid_dependencies,
                )
            earlier_executable_ids.add(str(task["task_id"]))
        final_rows = [
            row
            for row in candidate["tasks"]
            if str(row.get("panel_role") or "").upper() == "PHYSICALLY_FINAL_HIL"
        ]
        require(
            len(final_rows) == 1
            and str(final_rows[0]["task_id"]) == expected_physical_final_task_id
            and str(candidate["tasks"][-1]["task_id"])
            == expected_physical_final_task_id,
            "PLAN_PHYSICAL_FINAL_HIL_NOT_PRESERVED",
            "Atomic insertion would move or duplicate the physically final HIL.",
            status="MISMATCH",
        )
        return candidate, new_ids

    def plan_tasks_atomic_insert(
        self,
        project_id: str,
        *,
        insertions: list[dict[str, Any]],
        planned_by: str,
        plan_id: str,
        batch_id: str,
        research_batch_sha256: str,
        expected_backlog_sha256: str,
        expected_canonical_plan_sha256: str,
        expected_executable_projection_sha256: str,
        expected_physical_final_task_id: str,
    ) -> dict[str, Any]:
        """Insert bounded Plan groups at exact targets in one recoverable commit."""

        self.config(project_id)
        exact_actor = str(planned_by or "").strip()
        exact_plan_id = str(plan_id or "").strip()
        exact_batch_id = str(batch_id or "").strip()
        exact_final_task_id = str(expected_physical_final_task_id or "").strip()
        require(
            bool(exact_actor),
            "TASK_PLAN_ACTOR_REQUIRED",
            "Task planning requires the visible human or agent identity.",
            status="BLOCKED",
        )
        for value, code, label in (
            (exact_plan_id, "TASK_PLAN_ID_INVALID", "task plan ID"),
            (exact_batch_id, "TASK_PLAN_BATCH_ID_INVALID", "atomic batch ID"),
            (
                exact_final_task_id,
                "PLAN_PHYSICAL_FINAL_TASK_ID_INVALID",
                "physical-final task ID",
            ),
        ):
            require(
                bool(value)
                and len(value) <= 96
                and all(character in _PROJECT_ID_CHARS for character in value),
                code,
                f"The {label} is invalid.",
                status="BLOCKED",
            )
        normalized_hashes: dict[str, str] = {}
        for name, value in (
            ("research_batch_sha256", research_batch_sha256),
            ("expected_backlog_sha256", expected_backlog_sha256),
            ("expected_canonical_plan_sha256", expected_canonical_plan_sha256),
            (
                "expected_executable_projection_sha256",
                expected_executable_projection_sha256,
            ),
        ):
            exact_hash = str(value).strip().upper()
            require(
                re.fullmatch(r"[0-9A-F]{64}", exact_hash) is not None,
                "PLAN_ATOMIC_INSERTION_SHA256_INVALID",
                "Atomic Plan insertion requires exact SHA-256 authorities.",
                status="BLOCKED",
                field=name,
            )
            normalized_hashes[name] = exact_hash
        require(
            isinstance(insertions, list) and 1 <= len(insertions) <= 8,
            "PLAN_ATOMIC_INSERTION_GROUP_COUNT_INVALID",
            "Atomic Plan insertion requires between one and eight target groups.",
            status="BLOCKED",
        )
        normalized_insertions: list[dict[str, Any]] = []
        total_tasks = 0
        target_ids: list[str] = []
        for position, group in enumerate(insertions, start=1):
            require(
                isinstance(group, dict),
                "PLAN_ATOMIC_INSERTION_GROUP_INVALID",
                "Every atomic Plan insertion group must be an object.",
                status="BLOCKED",
                position=position,
            )
            target_id = str(group.get("insert_before_task_id") or "").strip()
            require(
                bool(target_id)
                and len(target_id) <= 96
                and all(character in _PROJECT_ID_CHARS for character in target_id),
                "TASK_PLAN_INSERTION_TARGET_INVALID",
                "Every atomic insertion group requires one stable target task ID.",
                status="BLOCKED",
                position=position,
            )
            raw_tasks = group.get("tasks")
            require(
                isinstance(raw_tasks, list) and 1 <= len(raw_tasks) <= 100,
                "PLAN_ATOMIC_INSERTION_GROUP_SIZE_INVALID",
                "Every atomic insertion group requires a bounded non-empty task list.",
                status="BLOCKED",
                position=position,
            )
            normalized_tasks = self._normalize_plan_task_rows(
                cast(list[dict[str, Any]], raw_tasks)
            )
            total_tasks += len(normalized_tasks)
            target_ids.append(target_id)
            normalized_insertions.append(
                {"insert_before_task_id": target_id, "tasks": normalized_tasks}
            )
        require(
            total_tasks <= 100,
            "TASK_PLAN_SIZE_INVALID",
            "An atomic task plan may contain at most one hundred tasks.",
            status="BLOCKED",
            count=total_tasks,
        )
        require(
            len(target_ids) == len(set(target_ids)),
            "PLAN_ATOMIC_INSERTION_TARGET_DUPLICATE",
            "Each atomic insertion target may appear only once per batch.",
            status="BLOCKED",
        )
        new_ids = [
            str(task["task_id"])
            for group in normalized_insertions
            for task in group["tasks"]
        ]
        require(
            len(new_ids) == len(set(new_ids)),
            "BACKLOG_TASK_ID_DUPLICATE",
            "An atomic task plan may not repeat a task ID across groups.",
            status="BLOCKED",
        )
        input_body = {
            "planned_by": exact_actor,
            "plan_id": exact_plan_id,
            "batch_id": exact_batch_id,
            **normalized_hashes,
            "expected_physical_final_task_id": exact_final_task_id,
            "insertions": normalized_insertions,
        }
        input_sha256 = sha256_bytes(canonical_json_bytes(input_body))
        journal_path = self._plan_atomic_insertion_journal_path(
            project_id,
            exact_batch_id,
        )
        receipt: dict[str, Any]
        idempotent_replay = False
        recovered_prepared = False
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            current_backlog_sha256 = sha256_bytes(canonical_json_bytes(backlog))
            journal: dict[str, Any] | None = None
            if journal_path.is_file():
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
                require(
                    journal.get("schema")
                    == "evidence-lane.plan-atomic-insertion-journal.v1"
                    and journal.get("project_id") == project_id
                    and journal.get("batch_id") == exact_batch_id
                    and journal.get("input_sha256") == input_sha256,
                    "PLAN_ATOMIC_INSERTION_JOURNAL_MISMATCH",
                    "The existing atomic insertion journal binds different input.",
                    status="MISMATCH",
                )
                state = str(journal.get("state") or "")
                before_sha = str(journal.get("before_backlog_sha256") or "")
                after_sha = str(journal.get("after_backlog_sha256") or "")
                require(
                    state in {"PREPARED", "COMMITTED"},
                    "PLAN_ATOMIC_INSERTION_JOURNAL_STATE_INVALID",
                    "The atomic insertion journal has an unsupported state.",
                    status="MISMATCH",
                    state=state,
                )
                if state == "COMMITTED":
                    require(
                        current_backlog_sha256 == after_sha,
                        "PLAN_ATOMIC_INSERTION_REPLAY_STATE_MISMATCH",
                        "The committed atomic insertion no longer matches the Plan authority.",
                        status="MISMATCH",
                    )
                    receipt = dict(journal["receipt"])
                    idempotent_replay = True
                else:
                    require(
                        current_backlog_sha256 in {before_sha, after_sha},
                        "PLAN_ATOMIC_INSERTION_RECOVERY_DIVERGED",
                        "The Plan changed outside the prepared atomic insertion boundary.",
                        status="MISMATCH",
                    )
                    if current_backlog_sha256 == before_sha:
                        candidate, _ = self._build_plan_atomic_insertion_candidate(
                            backlog,
                            insertions=normalized_insertions,
                            planned_by=exact_actor,
                            plan_id=exact_plan_id,
                            batch_id=exact_batch_id,
                            research_batch_sha256=normalized_hashes[
                                "research_batch_sha256"
                            ],
                            input_sha256=input_sha256,
                            planned_at=str(journal["planned_at"]),
                            expected_physical_final_task_id=exact_final_task_id,
                        )
                        require(
                            sha256_bytes(canonical_json_bytes(candidate)) == after_sha,
                            "PLAN_ATOMIC_INSERTION_RECOVERY_REBUILD_MISMATCH",
                            "The prepared insertion did not rebuild the sealed Plan result.",
                            status="MISMATCH",
                        )
                        self._persist_backlog(project_id, candidate)
                        backlog = candidate
                    else:
                        self._persist_backlog(project_id, backlog)
                    receipt = dict(journal["receipt"])
                    recovered_prepared = True
                    committed = {
                        **journal,
                        "state": "COMMITTED",
                        "committed_at": utc_now(),
                    }
                    self._write_plan_atomic_insertion_journal(
                        journal_path,
                        committed,
                    )
            else:
                existing_plan = next(
                    (
                        plan
                        for plan in backlog["plans"]
                        if str(plan.get("plan_id")) == exact_plan_id
                    ),
                    None,
                )
                require(
                    existing_plan is None,
                    "TASK_PLAN_ID_CONFLICT",
                    "The task plan ID already exists without its atomic journal.",
                    status="BLOCKED",
                )
                before_status = self.backlog_status(
                    project_id,
                    _loaded_backlog=copy.deepcopy(backlog),
                )
                observed_canonical_sha = str(
                    before_status["canonical_plan_projection"]["projection_sha256"]
                )
                observed_executable_sha = str(
                    before_status["goal_projection"]["projection_sha256"]
                )
                require(
                    current_backlog_sha256
                    == normalized_hashes["expected_backlog_sha256"]
                    and observed_canonical_sha
                    == normalized_hashes["expected_canonical_plan_sha256"]
                    and observed_executable_sha
                    == normalized_hashes["expected_executable_projection_sha256"],
                    "PLAN_ATOMIC_INSERTION_AUTHORITY_HASH_MISMATCH",
                    "The live Plan backlog or projections do not match the sealed insertion input.",
                    status="MISMATCH",
                    observed_backlog_sha256=current_backlog_sha256,
                    observed_canonical_plan_sha256=observed_canonical_sha,
                    observed_executable_projection_sha256=observed_executable_sha,
                )
                planned_at = utc_now()
                candidate, added_ids = self._build_plan_atomic_insertion_candidate(
                    backlog,
                    insertions=normalized_insertions,
                    planned_by=exact_actor,
                    plan_id=exact_plan_id,
                    batch_id=exact_batch_id,
                    research_batch_sha256=normalized_hashes["research_batch_sha256"],
                    input_sha256=input_sha256,
                    planned_at=planned_at,
                    expected_physical_final_task_id=exact_final_task_id,
                )
                after_status = self.backlog_status(
                    project_id,
                    _loaded_backlog=copy.deepcopy(candidate),
                )
                after_backlog_sha256 = sha256_bytes(canonical_json_bytes(candidate))
                final_row = next(
                    row
                    for row in after_status["goal_projection"]["rows"]
                    if row["task_id"] == exact_final_task_id
                )
                receipt = {
                    "schema": "evidence-lane.plan-atomic-insertion-receipt.v1",
                    "status": "PASS",
                    "project_id": project_id,
                    "plan_id": exact_plan_id,
                    "batch_id": exact_batch_id,
                    "input_sha256": input_sha256,
                    "research_batch_sha256": normalized_hashes["research_batch_sha256"],
                    "group_count": len(normalized_insertions),
                    "task_count": len(added_ids),
                    "task_ids": added_ids,
                    "before_backlog_sha256": current_backlog_sha256,
                    "after_backlog_sha256": after_backlog_sha256,
                    "before_canonical_plan_sha256": observed_canonical_sha,
                    "after_canonical_plan_sha256": after_status[
                        "canonical_plan_projection"
                    ]["projection_sha256"],
                    "before_executable_projection_sha256": observed_executable_sha,
                    "after_executable_projection_sha256": after_status[
                        "goal_projection"
                    ]["projection_sha256"],
                    "physical_final_task_id": exact_final_task_id,
                    "physical_final_row": final_row["number"],
                    "planned_at": planned_at,
                    "pointer_moved": False,
                    "candidate_created": False,
                    "hil_invoked": False,
                    "goal_mutated": False,
                    "git_executed": False,
                }
                prepared = {
                    "schema": "evidence-lane.plan-atomic-insertion-journal.v1",
                    "state": "PREPARED",
                    "project_id": project_id,
                    "batch_id": exact_batch_id,
                    "input_sha256": input_sha256,
                    "before_backlog_sha256": current_backlog_sha256,
                    "after_backlog_sha256": after_backlog_sha256,
                    "planned_at": planned_at,
                    "prepared_at": utc_now(),
                    "receipt": receipt,
                }
                self._write_plan_atomic_insertion_journal(journal_path, prepared)
                self._persist_backlog(project_id, candidate)
                self._write_plan_atomic_insertion_journal(
                    journal_path,
                    {**prepared, "state": "COMMITTED", "committed_at": utc_now()},
                )
        status = self.backlog_status(project_id)
        return {
            **status,
            "atomic_insertion_receipt": {
                **receipt,
                "idempotent_replay": idempotent_replay,
                "recovered_prepared_insertion": recovered_prepared,
            },
        }

    def amend_active_task_contract(
        self,
        project_id: str,
        *,
        amendment_id: str,
        session_id: str,
        active_task_id: str,
        replacement_contract: dict[str, Any],
        amended_by: str,
        approval_receipt_sha256: str,
        expected_backlog_sha256: str,
        expected_canonical_plan_sha256: str,
        expected_executable_projection_sha256: str,
        request_sha256: str,
    ) -> dict[str, Any]:
        """Append one exact active-contract amendment without changing row identity."""

        self.config(project_id)
        exact_amendment_id = str(amendment_id or "").strip()
        exact_session_id = str(session_id or "").strip()
        exact_task_id = str(active_task_id or "").strip()
        exact_actor = str(amended_by or "").strip()
        for field, value in (
            ("amendment_id", exact_amendment_id),
            ("active_task_id", exact_task_id),
        ):
            require(
                bool(value)
                and len(value) <= 96
                and all(character in _PROJECT_ID_CHARS for character in value),
                "ACTIVE_CONTRACT_REBIND_ID_INVALID",
                "Active-contract amendment identities must be bounded public-safe IDs.",
                status="BLOCKED",
                field=field,
            )
        require(
            exact_session_id.startswith("session_")
            and exact_session_id.replace("_", "").isalnum()
            and bool(exact_actor),
            "ACTIVE_CONTRACT_REBIND_ID_INVALID",
            "The active-contract amendment requires exact session and actor identities.",
            status="BLOCKED",
        )
        exact_hashes: dict[str, str] = {}
        for field, value in (
            ("approval_receipt_sha256", approval_receipt_sha256),
            ("expected_backlog_sha256", expected_backlog_sha256),
            ("expected_canonical_plan_sha256", expected_canonical_plan_sha256),
            (
                "expected_executable_projection_sha256",
                expected_executable_projection_sha256,
            ),
            ("request_sha256", request_sha256),
        ):
            exact = str(value or "").strip().upper()
            require(
                re.fullmatch(r"[0-9A-F]{64}", exact) is not None,
                "ACTIVE_CONTRACT_REBIND_SHA256_INVALID",
                "Active-contract amendment authorities require exact SHA-256 values.",
                status="BLOCKED",
                field=field,
            )
            exact_hashes[field] = exact
        require(
            isinstance(replacement_contract, dict),
            "ACTIVE_CONTRACT_REBIND_REPLACEMENT_INVALID",
            "The active-contract replacement must be one structured task contract.",
            status="BLOCKED",
        )
        normalized = self._normalize_plan_task_rows(
            [{**replacement_contract, "task_id": exact_task_id}]
        )[0]
        contract_fields = (
            "task_id",
            "task_class",
            "requested_outcome",
            "permitted_paths",
            "permitted_tools",
            "acceptance_checks",
            "stop_condition",
            "plan_group",
            "commit_batch_id",
            "dependencies",
            "git_commit_stage",
            "current_version",
            "current_branch",
        )
        receipt: dict[str, Any]
        idempotent_replay = False
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            target = next(
                (
                    task
                    for task in backlog["tasks"]
                    if str(task["task_id"]) == exact_task_id
                ),
                None,
            )
            require(
                isinstance(target, dict),
                "ACTIVE_CONTRACT_REBIND_TASK_NOT_FOUND",
                "The exact active Plan task is absent.",
                status="MISMATCH",
                task_id=exact_task_id,
            )
            target = cast(dict[str, Any], target)
            existing_amendments = target.get("task_contract_amendments")
            require(
                existing_amendments is None or isinstance(existing_amendments, list),
                "ACTIVE_CONTRACT_REBIND_HISTORY_INVALID",
                "The task contract amendment history is not append-only data.",
                status="MISMATCH",
            )
            amendments = (
                cast(list[dict[str, Any]], existing_amendments)
                if isinstance(existing_amendments, list)
                else []
            )
            existing = next(
                (
                    row
                    for row in amendments
                    if isinstance(row, dict)
                    and row.get("amendment_id") == exact_amendment_id
                ),
                None,
            )
            if existing is not None:
                require(
                    existing.get("request_sha256") == exact_hashes["request_sha256"],
                    "ACTIVE_CONTRACT_REBIND_REPLAY_CONFLICT",
                    "The amendment ID already binds a different request.",
                    status="BLOCKED",
                )
                receipt = dict(existing["receipt"])
                idempotent_replay = True
            else:
                current_backlog_sha256 = sha256_bytes(canonical_json_bytes(backlog))
                status = self.backlog_status(
                    project_id,
                    _loaded_backlog=copy.deepcopy(backlog),
                )
                observed_canonical_sha256 = str(
                    status["canonical_plan_projection"]["projection_sha256"]
                )
                observed_executable_sha256 = str(
                    status["goal_projection"]["projection_sha256"]
                )
                require(
                    current_backlog_sha256 == exact_hashes["expected_backlog_sha256"]
                    and observed_canonical_sha256
                    == exact_hashes["expected_canonical_plan_sha256"]
                    and observed_executable_sha256
                    == exact_hashes["expected_executable_projection_sha256"],
                    "ACTIVE_CONTRACT_REBIND_PLAN_PRECONDITION_MISMATCH",
                    "The live Plan authority differs from the approved rebind boundary.",
                    status="MISMATCH",
                    observed_backlog_sha256=current_backlog_sha256,
                    observed_canonical_plan_sha256=observed_canonical_sha256,
                    observed_executable_projection_sha256=(observed_executable_sha256),
                )
                active_ids = [
                    str(row["task_id"])
                    for row in backlog["tasks"]
                    if row.get("status") == "ACTIVE"
                ]
                require(
                    active_ids == [exact_task_id] and target.get("status") == "ACTIVE",
                    "ACTIVE_CONTRACT_REBIND_ACTIVE_TASK_MISMATCH",
                    "The replacement may amend only the sole exact ACTIVE Plan row.",
                    status="MISMATCH",
                    active_task_ids=active_ids,
                )
                prior_contract = {
                    field: copy.deepcopy(target[field])
                    for field in contract_fields
                    if field in target
                }
                replacement = {
                    field: copy.deepcopy(normalized[field])
                    for field in contract_fields
                    if field in normalized
                }
                for field in contract_fields:
                    if field != "task_id":
                        target.pop(field, None)
                target.update(replacement)
                target["current_contract_authority"] = "ACTIVE_CONTRACT_REBIND"
                amended_at = utc_now()
                prior_contract_sha256 = sha256_bytes(
                    canonical_json_bytes(prior_contract)
                )
                replacement_contract_sha256 = sha256_bytes(
                    canonical_json_bytes(replacement)
                )
                receipt_body = {
                    "schema": "evidence-lane.active-contract-amendment-receipt.v1",
                    "status": "PASS",
                    "project_id": project_id,
                    "session_id": exact_session_id,
                    "active_task_id": exact_task_id,
                    "amendment_id": exact_amendment_id,
                    "request_sha256": exact_hashes["request_sha256"],
                    "approval_receipt_sha256": exact_hashes["approval_receipt_sha256"],
                    "prior_contract_sha256": prior_contract_sha256,
                    "replacement_contract_sha256": (replacement_contract_sha256),
                    "plan_row_identity_preserved": True,
                    "plan_row_status_preserved": True,
                    "candidate_created": False,
                    "hil_invoked": False,
                    "pointer_moved": False,
                    "goal_completion_mutated": False,
                    "git_executed": False,
                    "install_executed": False,
                    "amended_at": amended_at,
                }
                receipt = {
                    **receipt_body,
                    "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
                }
                if existing_amendments is None:
                    target["task_contract_amendments"] = amendments
                amendments.append(
                    {
                        "schema": "evidence-lane.active-contract-amendment.v1",
                        "amendment_id": exact_amendment_id,
                        "session_id": exact_session_id,
                        "amended_by": exact_actor,
                        "request_sha256": exact_hashes["request_sha256"],
                        "approval_receipt_sha256": exact_hashes[
                            "approval_receipt_sha256"
                        ],
                        "prior_contract": prior_contract,
                        "replacement_contract": replacement,
                        "receipt": receipt,
                        "recorded_at": amended_at,
                    }
                )
                append_delta_event(
                    backlog,
                    task_id=exact_task_id,
                    event_type="TASK_CONTRACT_AMENDED",
                    to_status="ACTIVE",
                    actor=exact_actor,
                    event_id=f"{exact_amendment_id}__contract_amended",
                    recorded_at=amended_at,
                    assume_initialized=True,
                    details={
                        "session_id": exact_session_id,
                        "prior_contract_sha256": prior_contract_sha256,
                        "replacement_contract_sha256": (replacement_contract_sha256),
                        "approval_receipt_sha256": exact_hashes[
                            "approval_receipt_sha256"
                        ],
                    },
                )
                self._persist_backlog(project_id, backlog)
        status = self.backlog_status(project_id)
        return {
            **status,
            "active_contract_amendment_receipt": {
                **receipt,
                "idempotent_replay": idempotent_replay,
            },
        }

    def activate_plan_normalization(
        self,
        project_id: str,
        *,
        plan_id: str,
        review_task_id: str,
        replacement_task_id: str,
        session_id: str,
        runtime_task_id: str,
        approved_by: str,
        approval_receipt_sha256: str,
    ) -> dict[str, Any]:
        """Complete the approved review gate and activate its sole successor.

        All backlog events are persisted under one project lock.  Replays are
        accepted only when the already-active row has the exact same session
        and runtime-task binding.
        """

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks_by_id = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            review = tasks_by_id.get(review_task_id)
            replacement = tasks_by_id.get(replacement_task_id)
            require(
                isinstance(review, dict)
                and isinstance(replacement, dict)
                and review.get("plan_id") == plan_id
                and replacement.get("plan_id") == plan_id,
                "PLAN_NORMALIZATION_TASK_MISMATCH",
                "The normalization review and replacement rows must belong to the exact new plan.",
                status="MISMATCH",
                plan_id=plan_id,
                review_task_id=review_task_id,
                replacement_task_id=replacement_task_id,
            )
            review = cast(dict[str, Any], review)
            replacement = cast(dict[str, Any], replacement)
            changed = False
            now = utc_now()
            if review.get("status") == "QUEUED":
                append_delta_event(
                    backlog,
                    task_id=review_task_id,
                    event_type="PLAN_APPROVAL_STARTED",
                    to_status="ACTIVE",
                    actor=approved_by,
                    event_id=f"{plan_id}__{review_task_id}__approval_active",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "approval_receipt_sha256": approval_receipt_sha256,
                        "candidate_created": False,
                        "hil_inferred": False,
                    },
                )
                append_delta_event(
                    backlog,
                    task_id=review_task_id,
                    event_type="PLAN_APPROVAL_COMPLETED",
                    to_status="DONE",
                    actor=approved_by,
                    event_id=f"{plan_id}__{review_task_id}__approval_done",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "approval_receipt_sha256": approval_receipt_sha256,
                        "candidate_created": False,
                        "hil_inferred": False,
                    },
                )
                review.setdefault("history", []).append(
                    {
                        "event": "PLAN_APPROVAL_COMPLETED",
                        "approved_by": approved_by,
                        "approval_receipt_sha256": approval_receipt_sha256,
                        "recorded_at": now,
                    }
                )
                changed = True
            require(
                review.get("status") == "DONE",
                "PLAN_NORMALIZATION_REVIEW_NOT_DONE",
                "The approved normalization review gate is not complete.",
                status="MISMATCH",
                review_task_id=review_task_id,
                review_status=review.get("status"),
            )
            active = [
                task for task in backlog["tasks"] if task.get("status") == "ACTIVE"
            ]
            if replacement.get("status") == "QUEUED":
                require(
                    not active,
                    "PLAN_NORMALIZATION_ACTIVE_TASK_CONFLICT",
                    "The replacement row may activate only after the prior active row is superseded.",
                    status="MISMATCH",
                    active_task_ids=[task["task_id"] for task in active],
                )
                replacement["active_session_id"] = session_id
                replacement["runtime_task_id"] = runtime_task_id
                append_delta_event(
                    backlog,
                    task_id=replacement_task_id,
                    event_type="PLAN_NORMALIZATION_ACTIVATED",
                    to_status="ACTIVE",
                    actor=session_id,
                    event_id=(
                        f"{plan_id}__{replacement_task_id}__"
                        f"{runtime_task_id}__normalization_active"
                    ),
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "runtime_task_id": runtime_task_id,
                        "plan_id": plan_id,
                    },
                )
                replacement.setdefault("history", []).append(
                    {
                        "event": "CLAIMED_BY_PLAN_NORMALIZATION",
                        "session_id": session_id,
                        "runtime_task_id": runtime_task_id,
                        "recorded_at": now,
                    }
                )
                changed = True
            else:
                require(
                    replacement.get("status") == "ACTIVE"
                    and replacement.get("active_session_id") == session_id
                    and replacement.get("runtime_task_id") == runtime_task_id
                    and len(active) == 1
                    and active[0].get("task_id") == replacement_task_id,
                    "PLAN_NORMALIZATION_ACTIVE_BINDING_MISMATCH",
                    "The replayed normalization does not match the sole active replacement binding.",
                    status="MISMATCH",
                    replacement_task_id=replacement_task_id,
                    replacement_status=replacement.get("status"),
                )
            if changed:
                self._persist_backlog(project_id, backlog)
        return self.backlog_status(project_id)

    def activate_priority_steer(
        self,
        project_id: str,
        *,
        old_active_task_id: str,
        replacement_task_id: str,
        session_id: str,
        runtime_task_id: str,
        decided_by: str,
        reason_sha256: str,
        interruption_id: str,
    ) -> dict[str, Any]:
        """Pause one live Delta and activate one inserted correction atomically.

        The interrupted row is not completed, dropped, or superseded.  It is
        returned to QUEUED immediately behind the inserted correction so the
        same persistent Goal can resume it after the priority work finishes.
        Replays accept only the exact already-swapped binding.
        """

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks_by_id = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            old_active = tasks_by_id.get(old_active_task_id)
            replacement = tasks_by_id.get(replacement_task_id)
            require(
                isinstance(old_active, dict) and isinstance(replacement, dict),
                "PLAN_PRIORITY_STEER_TASK_MISMATCH",
                "The priority steer must bind the exact live and inserted Plan rows.",
                status="MISMATCH",
                old_active_task_id=old_active_task_id,
                replacement_task_id=replacement_task_id,
            )
            old_active = cast(dict[str, Any], old_active)
            replacement = cast(dict[str, Any], replacement)
            before = (
                old_active.get("status") == "ACTIVE"
                and replacement.get("status") == "QUEUED"
            )
            after = (
                old_active.get("status") == "QUEUED"
                and replacement.get("status") == "ACTIVE"
            )
            require(
                before or after,
                "PLAN_PRIORITY_STEER_STATE_MISMATCH",
                "The Plan rows are neither at the exact pre-steer nor committed state.",
                status="MISMATCH",
                old_active_status=old_active.get("status"),
                replacement_status=replacement.get("status"),
            )
            require(
                int(replacement.get("sequence") or 0) + 1
                == int(old_active.get("sequence") or 0),
                "PLAN_PRIORITY_STEER_ORDER_MISMATCH",
                "The inserted correction must be immediately before the paused Delta.",
                status="MISMATCH",
                replacement_sequence=replacement.get("sequence"),
                old_active_sequence=old_active.get("sequence"),
            )
            changed = False
            if before:
                active = [
                    task for task in backlog["tasks"] if task.get("status") == "ACTIVE"
                ]
                require(
                    len(active) == 1
                    and active[0].get("task_id") == old_active_task_id
                    and old_active.get("active_session_id") == session_id,
                    "PLAN_PRIORITY_STEER_ACTIVE_BINDING_MISMATCH",
                    "The priority steer requires the sole exact active session binding.",
                    status="MISMATCH",
                    active_task_ids=[task.get("task_id") for task in active],
                    active_session_id=old_active.get("active_session_id"),
                )
                now = utc_now()
                pause_event = append_delta_event(
                    backlog,
                    task_id=old_active_task_id,
                    event_type="PRIORITY_STEER_PAUSED",
                    to_status="QUEUED",
                    actor=decided_by,
                    event_id=f"{interruption_id}__paused",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "replacement_task_id": replacement_task_id,
                        "reason_sha256": reason_sha256,
                        "session_id": session_id,
                        "history_preserved": True,
                        "candidate_created": False,
                        "pending_hil": False,
                        "pointer_moved": False,
                    },
                )
                old_active.pop("active_session_id", None)
                old_active.pop("runtime_task_id", None)
                old_active.setdefault("history", []).append(
                    {
                        "event": "PRIORITY_STEER_PAUSED",
                        "event_id": pause_event["event_id"],
                        "replacement_task_id": replacement_task_id,
                        "reason_sha256": reason_sha256,
                        "recorded_at": now,
                    }
                )
                replacement["active_session_id"] = session_id
                replacement["runtime_task_id"] = runtime_task_id
                activation_event = append_delta_event(
                    backlog,
                    task_id=replacement_task_id,
                    event_type="PRIORITY_STEER_ACTIVATED",
                    to_status="ACTIVE",
                    actor=decided_by,
                    event_id=f"{interruption_id}__activated",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "paused_task_id": old_active_task_id,
                        "reason_sha256": reason_sha256,
                        "session_id": session_id,
                        "runtime_task_id": runtime_task_id,
                        "candidate_created": False,
                        "pending_hil": False,
                        "pointer_moved": False,
                    },
                )
                replacement.setdefault("history", []).append(
                    {
                        "event": "PRIORITY_STEER_ACTIVATED",
                        "event_id": activation_event["event_id"],
                        "paused_task_id": old_active_task_id,
                        "session_id": session_id,
                        "runtime_task_id": runtime_task_id,
                        "recorded_at": now,
                    }
                )
                self._persist_backlog(project_id, backlog)
                changed = True
            else:
                require(
                    replacement.get("active_session_id") == session_id
                    and replacement.get("runtime_task_id") == runtime_task_id
                    and len(
                        [
                            task
                            for task in backlog["tasks"]
                            if task.get("status") == "ACTIVE"
                        ]
                    )
                    == 1,
                    "PLAN_PRIORITY_STEER_REPLAY_BINDING_MISMATCH",
                    "The replayed priority steer does not match the sole active binding.",
                    status="MISMATCH",
                )
            result = self.backlog_status(project_id)
            receipt_body = {
                "schema": "evidence-lane.plan-priority-steer.v1",
                "status": "PASS",
                "interruption_id": interruption_id,
                "project_id": project_id,
                "session_id": session_id,
                "paused_task_id": old_active_task_id,
                "active_task_id": replacement_task_id,
                "runtime_task_id": runtime_task_id,
                "reason_sha256": reason_sha256,
                "idempotent_replay": not changed,
                "history_preserved": True,
                "candidate_created": False,
                "pending_hil": False,
                "pointer_moved": False,
            }
            result["priority_steer_receipt"] = {
                **receipt_body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
            }
            return result

    def promote_existing_priority_task(
        self,
        project_id: str,
        *,
        promotion_id: str,
        old_active_task_id: str,
        promoted_task_id: str,
        session_id: str,
        runtime_task_id: str,
        promoted_by: str,
        reason_sha256: str,
        expected_backlog_sha256: str,
        expected_canonical_plan_sha256: str,
        expected_executable_projection_sha256: str,
        expected_physical_final_task_id: str,
    ) -> dict[str, Any]:
        """Move one existing queued Delta immediately before the live row.

        This is deliberately separate from priority insertion: no task is appended,
        copied, superseded, completed, or dropped.  The queued task keeps its stable
        identity and metadata, the interrupted row returns to QUEUED immediately
        after it, and their dependency edge is rewired to preserve linear execution.
        """

        exact_hashes = {
            "expected_backlog_sha256": str(expected_backlog_sha256 or "").upper(),
            "expected_canonical_plan_sha256": str(
                expected_canonical_plan_sha256 or ""
            ).upper(),
            "expected_executable_projection_sha256": str(
                expected_executable_projection_sha256 or ""
            ).upper(),
        }
        require(
            all(
                len(value) == 64
                and all(character in "0123456789ABCDEF" for character in value)
                for value in exact_hashes.values()
            )
            and bool(promotion_id)
            and bool(promoted_by)
            and bool(reason_sha256),
            "PLAN_EXISTING_TASK_PROMOTION_CONTRACT_INVALID",
            "Existing-task promotion requires exact identities and Plan hashes.",
            status="BLOCKED",
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            before_status = self.backlog_status(
                project_id,
                _loaded_backlog=json.loads(json.dumps(backlog)),
            )
            observed_backlog_sha256 = sha256_bytes(canonical_json_bytes(backlog))
            mismatches = {
                name: {"expected": expected, "observed": observed}
                for name, expected, observed in (
                    (
                        "backlog_sha256",
                        exact_hashes["expected_backlog_sha256"],
                        observed_backlog_sha256,
                    ),
                    (
                        "canonical_plan_sha256",
                        exact_hashes["expected_canonical_plan_sha256"],
                        str(
                            before_status["canonical_plan_projection"][
                                "projection_sha256"
                            ]
                        ),
                    ),
                    (
                        "executable_projection_sha256",
                        exact_hashes["expected_executable_projection_sha256"],
                        str(before_status["goal_projection"]["projection_sha256"]),
                    ),
                )
                if expected != observed
            }
            require(
                not mismatches,
                "PLAN_EXISTING_TASK_PROMOTION_PRECONDITION_MISMATCH",
                "The live Plan changed before the existing queued Delta could be promoted.",
                status="MISMATCH",
                mismatches=mismatches,
                writes_performed=False,
            )
            require(
                before_status["tasks"][-1]["task_id"]
                == expected_physical_final_task_id,
                "PLAN_EXISTING_TASK_PROMOTION_FINAL_HIL_MISMATCH",
                "Existing-task promotion must preserve the exact physical-final HIL.",
                status="MISMATCH",
                writes_performed=False,
            )
            tasks = sorted(backlog["tasks"], key=lambda row: int(row["sequence"]))
            tasks_by_id = {str(task["task_id"]): task for task in tasks}
            old_active = tasks_by_id.get(old_active_task_id)
            promoted = tasks_by_id.get(promoted_task_id)
            require(
                isinstance(old_active, dict)
                and isinstance(promoted, dict)
                and old_active.get("status") == "ACTIVE"
                and promoted.get("status") == "QUEUED"
                and int(promoted.get("sequence") or 0)
                > int(old_active.get("sequence") or 0),
                "PLAN_EXISTING_TASK_PROMOTION_STATE_MISMATCH",
                "The exact queued Delta is not after the sole live row.",
                status="MISMATCH",
                writes_performed=False,
            )
            old_active = cast(dict[str, Any], old_active)
            promoted = cast(dict[str, Any], promoted)
            active = [task for task in tasks if task.get("status") == "ACTIVE"]
            require(
                len(active) == 1
                and active[0].get("task_id") == old_active_task_id
                and old_active.get("active_session_id") == session_id,
                "PLAN_EXISTING_TASK_PROMOTION_ACTIVE_BINDING_MISMATCH",
                "The live Plan does not have the exact sole active session binding.",
                status="MISMATCH",
                writes_performed=False,
            )
            old_index = tasks.index(old_active)
            promoted_index = tasks.index(promoted)
            predecessor_task_id = (
                str(tasks[old_index - 1]["task_id"]) if old_index > 0 else ""
            )
            predecessor = (
                tasks_by_id[predecessor_task_id] if predecessor_task_id else None
            )
            require(
                predecessor is None
                or predecessor.get("status") in {"DONE", "ACCEPTED"},
                "PLAN_EXISTING_TASK_PROMOTION_PREDECESSOR_INCOMPLETE",
                "The row immediately before the live row is not complete.",
                status="MISMATCH",
                predecessor_task_id=predecessor_task_id or None,
                predecessor_status=(
                    predecessor.get("status") if predecessor is not None else None
                ),
                writes_performed=False,
            )

            require(
                promoted_index > 0 and promoted_index + 1 < len(tasks),
                "PLAN_EXISTING_TASK_PROMOTION_CHAIN_BOUNDARY_INVALID",
                "The promoted Delta must have an existing physical predecessor and successor.",
                status="MISMATCH",
                writes_performed=False,
            )
            executable_before_promoted = [
                task
                for task in tasks[:promoted_index]
                if str(task.get("status")) in _GOAL_STATUS_BY_LIFECYCLE
            ]
            executable_after_promoted = [
                task
                for task in tasks[promoted_index + 1 :]
                if str(task.get("status")) in _GOAL_STATUS_BY_LIFECYCLE
            ]
            require(
                bool(executable_before_promoted) and bool(executable_after_promoted),
                "PLAN_EXISTING_TASK_PROMOTION_EXECUTABLE_CHAIN_BOUNDARY_INVALID",
                "The promoted Delta must have an executable predecessor and successor.",
                status="MISMATCH",
                writes_performed=False,
            )
            original_promoted_predecessor_task_id = str(
                executable_before_promoted[-1]["task_id"]
            )
            original_promoted_successor = executable_after_promoted[0]
            original_promoted_successor_task_id = str(
                original_promoted_successor["task_id"]
            )

            def dependency_ids(task: dict[str, Any]) -> list[str]:
                raw = task.get("dependencies")
                if isinstance(raw, str):
                    return [raw] if raw else []
                if isinstance(raw, list):
                    return [str(value) for value in raw if str(value)]
                return []

            promoted_dependencies = dependency_ids(promoted)
            successor_dependencies = dependency_ids(original_promoted_successor)
            promoted_dependency_mode = (
                "EXPLICIT"
                if promoted_dependencies
                else "IMPLICIT_EXECUTABLE_PREDECESSOR"
            )
            successor_dependency_mode = (
                "EXPLICIT"
                if successor_dependencies
                else "IMPLICIT_EXECUTABLE_PREDECESSOR"
            )
            allowed_promoted_chain_ids = {
                old_active_task_id,
                original_promoted_predecessor_task_id,
            }
            promoted_chain_safe = not promoted_dependencies or set(
                promoted_dependencies
            ).issubset(allowed_promoted_chain_ids)
            successor_chain_safe = (
                promoted_task_id in successor_dependencies
                if successor_dependencies
                else True
            )
            require(
                promoted_chain_safe and successor_chain_safe,
                "PLAN_EXISTING_TASK_PROMOTION_DEPENDENCY_CHAIN_MISMATCH",
                "The queued Delta and its executable successor do not form a safe explicit or implicit live dependency chain.",
                status="MISMATCH",
                promoted_dependencies=promoted_dependencies,
                promoted_dependency_mode=promoted_dependency_mode,
                original_promoted_predecessor_task_id=(
                    original_promoted_predecessor_task_id
                ),
                successor_task_id=original_promoted_successor_task_id,
                successor_dependencies=successor_dependencies,
                successor_dependency_mode=successor_dependency_mode,
                writes_performed=False,
            )

            original_promoted_dependencies = promoted.get("dependencies")
            original_active_dependencies = old_active.get("dependencies")
            original_successor_dependencies = original_promoted_successor.get(
                "dependencies"
            )
            tasks.pop(promoted_index)
            tasks.insert(old_index, promoted)
            for sequence, task in enumerate(tasks, start=1):
                task["sequence"] = sequence
            promoted["dependencies"] = (
                [predecessor_task_id] if predecessor_task_id else []
            )
            old_active["dependencies"] = [promoted_task_id]
            replacement_successor_dependencies = (
                [
                    original_promoted_predecessor_task_id
                    if dependency == promoted_task_id
                    else dependency
                    for dependency in successor_dependencies
                ]
                if successor_dependencies
                else [original_promoted_predecessor_task_id]
            )
            original_promoted_successor["dependencies"] = list(
                dict.fromkeys(replacement_successor_dependencies)
            )

            now = utc_now()
            pause_event = append_delta_event(
                backlog,
                task_id=old_active_task_id,
                event_type="EXISTING_PRIORITY_TASK_PAUSED",
                to_status="QUEUED",
                actor=promoted_by,
                event_id=f"{promotion_id}__paused",
                recorded_at=now,
                assume_initialized=True,
                details={
                    "promoted_task_id": promoted_task_id,
                    "reason_sha256": reason_sha256,
                    "session_id": session_id,
                    "history_preserved": True,
                    "candidate_created": False,
                    "pending_hil": False,
                    "pointer_moved": False,
                },
            )
            old_active.pop("active_session_id", None)
            old_active.pop("runtime_task_id", None)
            promoted["active_session_id"] = session_id
            promoted["runtime_task_id"] = runtime_task_id
            activation_event = append_delta_event(
                backlog,
                task_id=promoted_task_id,
                event_type="EXISTING_PRIORITY_TASK_ACTIVATED",
                to_status="ACTIVE",
                actor=promoted_by,
                event_id=f"{promotion_id}__activated",
                recorded_at=now,
                assume_initialized=True,
                details={
                    "paused_task_id": old_active_task_id,
                    "reason_sha256": reason_sha256,
                    "session_id": session_id,
                    "runtime_task_id": runtime_task_id,
                    "stable_task_identity_preserved": True,
                    "candidate_created": False,
                    "pending_hil": False,
                    "pointer_moved": False,
                },
            )
            promoted.setdefault("history", []).append(
                {
                    "event": "EXISTING_PRIORITY_TASK_ACTIVATED",
                    "event_id": activation_event["event_id"],
                    "prior_sequence": int(promoted_index + 1),
                    "prior_dependencies": original_promoted_dependencies,
                    "replacement_dependencies": promoted["dependencies"],
                    "recorded_at": now,
                }
            )
            old_active.setdefault("history", []).append(
                {
                    "event": "EXISTING_PRIORITY_TASK_PAUSED",
                    "event_id": pause_event["event_id"],
                    "prior_sequence": int(old_index + 1),
                    "prior_dependencies": original_active_dependencies,
                    "replacement_dependencies": promoted_task_id,
                    "recorded_at": now,
                }
            )
            original_promoted_successor.setdefault("history", []).append(
                {
                    "event": "EXISTING_PRIORITY_TASK_DEPENDENCY_REWIRED",
                    "promotion_id": promotion_id,
                    "prior_dependencies": original_successor_dependencies,
                    "replacement_dependencies": original_promoted_successor[
                        "dependencies"
                    ],
                    "recorded_at": now,
                }
            )
            backlog["tasks"] = tasks
            self._persist_backlog(project_id, backlog)

        result = self.backlog_status(project_id)
        active_rows = result["active"]
        require(
            len(active_rows) == 1
            and active_rows[0]["task_id"] == promoted_task_id
            and result["tasks"][-1]["task_id"] == expected_physical_final_task_id,
            "PLAN_EXISTING_TASK_PROMOTION_COMMIT_VERIFICATION_FAILED",
            "The committed promotion did not preserve its active/final boundaries.",
            status="FAIL",
        )
        receipt_body = {
            "schema": "evidence-lane.plan-existing-task-promotion.v1",
            "status": "PASS",
            "promotion_id": promotion_id,
            "project_id": project_id,
            "session_id": session_id,
            "paused_task_id": old_active_task_id,
            "active_task_id": promoted_task_id,
            "predecessor_task_id": predecessor_task_id or None,
            "original_promoted_predecessor_task_id": (
                original_promoted_predecessor_task_id
            ),
            "original_promoted_successor_task_id": (
                original_promoted_successor_task_id
            ),
            "promoted_dependency_mode": promoted_dependency_mode,
            "successor_dependency_mode": successor_dependency_mode,
            "runtime_task_id": runtime_task_id,
            "reason_sha256": reason_sha256,
            "stable_task_identity_preserved": True,
            "task_count_unchanged": len(result["tasks"]) == len(before_status["tasks"]),
            "physical_final_task_id": expected_physical_final_task_id,
            "candidate_created": False,
            "pending_hil": False,
            "pointer_moved": False,
        }
        result["existing_task_promotion_receipt"] = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        return result

    def correct_plan_normalization(
        self,
        project_id: str,
        *,
        original_transition_id: str,
        correction_transition_id: str,
        mistaken_task_id: str,
        restored_task_id: str,
        session_id: str,
        runtime_task_id: str,
        corrected_by: str,
        correction_receipt_sha256: str,
        goal_row_offset: int,
    ) -> dict[str, Any]:
        """Restore one mistakenly superseded live row without rewriting history.

        The two corrective events are persisted under one project lock. Replays
        accept only the exact already-corrected binding and append no event.
        """

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks_by_id = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            mistaken = tasks_by_id.get(mistaken_task_id)
            restored = tasks_by_id.get(restored_task_id)
            require(
                isinstance(mistaken, dict) and isinstance(restored, dict),
                "PLAN_NORMALIZATION_CORRECTION_TASK_MISMATCH",
                "The correction must reference the exact mistaken and restored Plan rows.",
                status="MISMATCH",
                mistaken_task_id=mistaken_task_id,
                restored_task_id=restored_task_id,
            )
            mistaken = cast(dict[str, Any], mistaken)
            restored = cast(dict[str, Any], restored)
            before = (
                mistaken.get("status") == "ACTIVE"
                and restored.get("status") == "SUPERSEDED"
            )
            after = (
                mistaken.get("status") == "SUPERSEDED"
                and restored.get("status") == "ACTIVE"
            )
            require(
                before or after,
                "PLAN_NORMALIZATION_CORRECTION_STATE_MISMATCH",
                "The Plan rows are neither at the exact mistaken state nor the exact corrected state.",
                status="MISMATCH",
                mistaken_status=mistaken.get("status"),
                restored_status=restored.get("status"),
            )
            event_details = {
                "original_transition_id": original_transition_id,
                "correction_transition_id": correction_transition_id,
                "correction_receipt_sha256": correction_receipt_sha256,
                "session_id": session_id,
                "candidate_created": False,
                "pending_hil": False,
                "pointer_moved": False,
                "goal_row_offset": goal_row_offset,
            }
            if before:
                now = utc_now()
                append_delta_event(
                    backlog,
                    task_id=mistaken_task_id,
                    event_type="PLAN_NORMALIZATION_CORRECTION_SUPERSEDED",
                    to_status="SUPERSEDED",
                    actor=corrected_by,
                    event_id=(
                        f"{correction_transition_id}__{mistaken_task_id}__superseded"
                    ),
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        **event_details,
                        "restored_task_id": restored_task_id,
                    },
                )
                append_delta_event(
                    backlog,
                    task_id=restored_task_id,
                    event_type="PLAN_NORMALIZATION_CORRECTION_RESTORED",
                    to_status="ACTIVE",
                    actor=corrected_by,
                    event_id=(
                        f"{correction_transition_id}__{restored_task_id}__restored"
                    ),
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        **event_details,
                        "mistaken_task_id": mistaken_task_id,
                    },
                )
                mistaken.pop("active_session_id", None)
                mistaken.pop("runtime_task_id", None)
                mistaken["superseded_by_task_id"] = restored_task_id
                mistaken.setdefault("history", []).append(
                    {
                        "event": "PLAN_NORMALIZATION_CORRECTION_SUPERSEDED",
                        "correction_transition_id": correction_transition_id,
                        "restored_task_id": restored_task_id,
                        "recorded_at": now,
                    }
                )
                restored.pop("superseded_by_task_id", None)
                restored["active_session_id"] = session_id
                restored["runtime_task_id"] = runtime_task_id
                restored.setdefault("history", []).append(
                    {
                        "event": "PLAN_NORMALIZATION_CORRECTION_RESTORED",
                        "correction_transition_id": correction_transition_id,
                        "mistaken_task_id": mistaken_task_id,
                        "recorded_at": now,
                    }
                )
                backlog["goal_row_offset"] = goal_row_offset
                self._persist_backlog(project_id, backlog)
            else:
                require(
                    restored.get("active_session_id") == session_id
                    and restored.get("runtime_task_id") == runtime_task_id
                    and backlog.get("goal_row_offset", 0) == goal_row_offset
                    and not any(
                        task.get("status") == "ACTIVE"
                        and task.get("task_id") != restored_task_id
                        for task in backlog["tasks"]
                    ),
                    "PLAN_NORMALIZATION_CORRECTION_ACTIVE_BINDING_MISMATCH",
                    "The replayed correction does not match the sole restored active binding.",
                    status="MISMATCH",
                )
        return self.backlog_status(project_id)

    def backlog_status(
        self,
        project_id: str,
        *,
        _loaded_backlog: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.config(project_id)
        backlog = (
            _loaded_backlog
            if _loaded_backlog is not None
            else self._load_backlog(project_id)
        )
        ensure_event_ledger(backlog)
        for task in backlog["tasks"]:
            task["lifecycle_events"] = [
                event
                for event in backlog["events"]
                if event["task_id"] == task["task_id"]
            ]
        counts = Counter(
            str(task.get("status", "UNKNOWN")) for task in backlog["tasks"]
        )
        active = [task for task in backlog["tasks"] if task.get("status") == "ACTIVE"]
        runtime = plan_runtime_status(
            self._plan_runtime_path(project_id),
            backlog,
        )
        ordered_tasks = sorted(backlog["tasks"], key=lambda row: int(row["sequence"]))
        active_release_context = _active_plan_release_context(ordered_tasks)
        goal_row_offset = backlog.get("goal_row_offset", 0)
        require(
            isinstance(goal_row_offset, int) and goal_row_offset >= 0,
            "PLAN_GOAL_ROW_OFFSET_INVALID",
            "The current executable Plan row offset must be a non-negative integer.",
            status="MISMATCH",
            goal_row_offset=goal_row_offset,
        )
        goal_rows: list[dict[str, Any]] = []
        history_rows: list[dict[str, Any]] = []
        canonical_rows: list[dict[str, Any]] = []
        earlier_executable_task_ids: set[str] = set()
        superseded_by: dict[str, list[str]] = {}
        for candidate in ordered_tasks:
            superseded_task_id = str(candidate.get("supersedes_task_id") or "").strip()
            if superseded_task_id:
                superseded_by.setdefault(superseded_task_id, []).append(
                    str(candidate["task_id"])
                )
        for task in ordered_tasks:
            lifecycle_status = str(task["status"])
            host_status = _GOAL_STATUS_BY_LIFECYCLE.get(lifecycle_status)
            previous_executable_task_id = (
                str(goal_rows[-1]["task_id"])
                if goal_rows and host_status is not None
                else None
            )
            projection_metadata = _plan_row_metadata(
                task,
                previous_executable_task_id=previous_executable_task_id,
                earlier_executable_task_ids=earlier_executable_task_ids,
                effective_for_execution=host_status is not None,
                active_release_context=active_release_context,
                apply_active_release_context=(lifecycle_status in {"ACTIVE", "QUEUED"}),
            )
            supersedes_task_id = str(task.get("supersedes_task_id") or "").strip()
            superseded_by_task_ids = list(
                dict.fromkeys(
                    [
                        *superseded_by.get(str(task["task_id"]), []),
                        *(
                            [str(task["superseded_by_task_id"])]
                            if task.get("superseded_by_task_id")
                            else []
                        ),
                    ]
                )
            )
            common = {
                "task_id": str(task["task_id"]),
                "step": str(task["requested_outcome"]),
                "plan_sequence": int(task["sequence"]),
                "lifecycle_status": lifecycle_status,
                "steer_deltas": list(task.get("steer_deltas") or []),
                **projection_metadata,
                **(
                    {"panel_role": str(task["panel_role"])}
                    if task.get("panel_role")
                    else {}
                ),
                **(
                    {"supersedes_task_id": supersedes_task_id}
                    if supersedes_task_id
                    else {}
                ),
                "superseded_by_task_ids": superseded_by_task_ids,
            }
            if host_status is not None:
                row = {
                    **common,
                    "authority_scope": "CURRENT_EXECUTABLE_PLAN",
                    "number": goal_row_offset + len(goal_rows) + 1,
                    "status": host_status,
                }
                row["visible_label"] = _visible_plan_row_label(row)
                goal_rows.append(row)
                earlier_executable_task_ids.add(str(task["task_id"]))
                canonical_rows.append(
                    {
                        **common,
                        "authority_scope": "CURRENT_EXECUTABLE_PLAN",
                        "projection_lane": "GOAL",
                        "goal_number": row["number"],
                    }
                )
                continue
            history_row = {
                **common,
                "authority_scope": (
                    "IMMUTABLE_SUPERSEDED_HISTORY"
                    if lifecycle_status in _SUPERSEDED_LIFECYCLE_STATUSES
                    else "IMMUTABLE_NON_EXECUTABLE_HISTORY"
                ),
                "history_number": len(history_rows) + 1,
                "execution_status": "NON_EXECUTABLE",
            }
            history_rows.append(history_row)
            canonical_rows.append(
                {
                    **common,
                    "authority_scope": history_row["authority_scope"],
                    "projection_lane": "HISTORY",
                    "history_number": history_row["history_number"],
                    "execution_status": "NON_EXECUTABLE",
                }
            )
        canonical_plan_body = {
            "canonical_authority": "PLAN_LANE",
            "project_id": project_id,
            "task_count": len(canonical_rows),
            "rows": canonical_rows,
        }
        canonical_plan_sha256 = sha256_bytes(canonical_json_bytes(canonical_plan_body))
        history_projection_body = {
            "canonical_authority": "PLAN_LANE",
            "project_id": project_id,
            "task_count": len(history_rows),
            "rows": history_rows,
            "parking_rows": [
                row
                for row in history_rows
                if row["lifecycle_status"] in _PARKED_LIFECYCLE_STATUSES
            ],
            "superseded_rows": [
                row
                for row in history_rows
                if row["lifecycle_status"] in _SUPERSEDED_LIFECYCLE_STATUSES
            ],
            "execution_policy": "IMMUTABLE_NON_EXECUTABLE_HISTORY",
            "parked_host_surfaces": [],
            "canonical_plan_sha256": canonical_plan_sha256,
        }
        history_projection = {
            **history_projection_body,
            "projection_sha256": sha256_bytes(
                canonical_json_bytes(history_projection_body)
            ),
        }
        goal_projection_body = {
            "canonical_authority": "PLAN_LANE",
            "project_id": project_id,
            "task_count": len(goal_rows),
            "canonical_task_count": len(canonical_rows),
            "history_task_count": len(history_rows),
            "row_offset": goal_row_offset,
            "row_start": goal_row_offset + 1 if goal_rows else None,
            "row_end": goal_row_offset + len(goal_rows) if goal_rows else None,
            "rows": goal_rows,
            "visible_label_contract": (
                "Row <number> / <task_id> — [CLASS=<classification>; "
                "GROUP=<plan_group>; BATCH=<commit_batch_id>; "
                "DEP=<task_ids_or_ROOT>; "
                "GIT=<stage>@<provenance>; "
                "VERSION=<marker>@<provenance>; "
                "BRANCH=<marker>@<provenance>; "
                "ROLE=<panel_role_or_STANDARD>; "
                "STATE=<lifecycle_status>] "
                "<exact description>"
            ),
            "visible_label_metadata_schema": (
                "evidence-lane.host-plan-row-metadata.v2"
            ),
            "visible_label_source": "STRUCTURED_CANONICAL_PLAN_METADATA",
            "raw_linked_delta_json_in_visible_label": False,
            "executable_authority_scope": "CURRENT_NON_SUPERSEDED_PLAN_ROWS_ONLY",
            "history_excluded_from_executable_markers": True,
            "active_release_context": active_release_context,
            "active_release_context_policy": (
                "TASK_LINKED_EXACT_DIRECTIVE_THEN_ACTIVE_PLAN_CONTEXT_"
                "THEN_TASK_CONTRACT_THEN_UNRESOLVED_CLAIMS"
            ),
            "version_conflict_policy": (
                "MARK_RECONCILIATION_REQUIRED_NEVER_INFER_CURRENT_VERSION"
            ),
            "commit_version_markers_change_lifecycle_status": False,
            "canonical_plan_sha256": canonical_plan_sha256,
            "history_projection_sha256": history_projection["projection_sha256"],
            "lifecycle_status_mapping": dict(_GOAL_STATUS_BY_LIFECYCLE),
            "non_executable_statuses": sorted(
                set(DELTA_STATUSES) - set(_GOAL_STATUS_BY_LIFECYCLE)
            ),
            "persistent_until": "NEXT_GOVERNED_HIL_PRESENTED",
            "steer_default_boundary": "BEFORE_NEXT_HIL",
            "linked_steer_policy": "APPEND_TO_EXISTING_STEP_WITHOUT_REPLACEMENT",
            "unlinked_steer_policy": (
                "INSERT_NEW_NUMBERED_STEP_BEFORE_NEXT_HIL_AND_INCREASE_COUNT"
            ),
        }
        goal_projection = {
            **goal_projection_body,
            "projection_sha256": sha256_bytes(
                canonical_json_bytes(goal_projection_body)
            ),
            "host_projections": {
                "CODEX": {
                    "native_plan_mode": True,
                    "plan_mode_shortcut": "/pl",
                    "plugin_skill": "$evi-plan",
                    "native_goal": True,
                    "native_task_panel": True,
                    "goal_start_requires_user_paste": True,
                    "state_travel_destination": {
                        "automatic_continue_in_new_chat_when_supported": True,
                        "exactly_one_destination_required": True,
                        "source_destination_task_binding_required": True,
                        "host_capability_failure_behavior": "FAIL_CLOSED",
                        "automatic_plan_projection": True,
                        "explicit_host_plan_acceptance_required": True,
                        "plan_acceptance_is_evidence_lane_hil": False,
                        "goal_or_source_work_before_plan_acceptance": False,
                        "manual_plan_mode_command_required": False,
                        "manual_evi_plan_command_required": False,
                        "goal_start_requires_user_paste": False,
                        "host_mode_selector_status_when_unavailable": (
                            "HOST_MODE_SELECTOR_UNAVAILABLE"
                        ),
                    },
                },
            },
            "goal_start_prompt": (
                f"Use the persisted Evidence Lane Plan Lane for project {project_id} "
                "as this Codex task's Goal. Resume the sole in-progress row, or the "
                "first pending row when none is active. Execute only Goal rows; "
                "DROPPED, SUPERSEDED, REJECTED, FAILED, and ROLLED_BACK rows remain "
                "immutable non-executable Plan history. Keep the full executable "
                "task panel visible through every steer, and stop at the next "
                "governed governed HIL."
            ),
        }
        return {
            "status": "PASS",
            "schema": backlog["schema"],
            "project_id": project_id,
            "linear_only": True,
            "active_task_limit": 1,
            "counts": dict(sorted(counts.items())),
            "active": active,
            "event_schema": backlog["event_schema"],
            "event_count": len(backlog["events"]),
            "event_head_sha256": backlog.get("event_head_sha256"),
            "planning_mode_event_count": len(backlog["planning_mode_events"]),
            "planning_mode_event_head_sha256": backlog.get(
                "planning_mode_event_head_sha256"
            ),
            "universal_statuses": list(DELTA_STATUSES),
            "plan_runtime_projection": runtime,
            "goal_projection": goal_projection,
            "history_projection": history_projection,
            "canonical_plan_projection": {
                **canonical_plan_body,
                "projection_sha256": canonical_plan_sha256,
            },
            "tasks": backlog["tasks"],
            "plans": backlog["plans"],
        }

    def record_steer_delta(
        self,
        project_id: str,
        *,
        delta_text: str,
        actor: str,
        delta_id: str,
        linked_task_id: str | None = None,
        new_task_contract: dict[str, Any] | None = None,
        boundary: str = "BEFORE_NEXT_HIL",
    ) -> dict[str, Any]:
        """Link a steer or insert one new row before the next visible HIL."""

        exact_text = delta_text
        exact_actor = actor.strip()
        exact_delta_id = delta_id.strip()
        exact_boundary = boundary.strip().upper() or "BEFORE_NEXT_HIL"
        exact_link = str(linked_task_id or "").strip()
        require(
            bool(exact_text.strip()) and len(exact_text) <= 50000,
            "STEER_DELTA_TEXT_INVALID",
            "A steer Delta requires its exact visible text.",
            status="BLOCKED",
        )
        require(
            bool(exact_actor),
            "STEER_DELTA_ACTOR_REQUIRED",
            "A steer Delta requires a visible actor.",
            status="BLOCKED",
        )
        require(
            bool(exact_delta_id)
            and len(exact_delta_id) <= 96
            and all(character in _PROJECT_ID_CHARS for character in exact_delta_id),
            "STEER_DELTA_ID_INVALID",
            "A steer Delta requires a stable public-safe ID.",
            status="BLOCKED",
        )
        require(
            bool(exact_boundary) and len(exact_boundary) <= 128,
            "STEER_DELTA_BOUNDARY_INVALID",
            "A steer Delta boundary must be a bounded visible label.",
            status="BLOCKED",
        )
        is_linked = bool(exact_link)
        is_new_step = new_task_contract is not None
        require(
            is_linked != is_new_step,
            "STEER_DELTA_CLASSIFICATION_REQUIRED",
            "Classify the steer as exactly one linked existing step or one new step.",
            status="BLOCKED",
        )
        fixed_window_task_ids = _persisted_host_plan_window_task_ids(
            self.project_root(project_id)
        )
        host_window_before = _host_plan_window_fingerprint(
            self.backlog_status(project_id),
            fixed_window_task_ids=fixed_window_task_ids,
        )

        if is_new_step:
            require(
                isinstance(new_task_contract, dict),
                "STEER_DELTA_NEW_TASK_CONTRACT_INVALID",
                "An unlinked steer requires one complete bounded task contract.",
                status="BLOCKED",
            )
            new_task = dict(cast(dict[str, Any], new_task_contract))
            explicit_insert_before = str(
                new_task.pop("insert_before_task_id", "") or ""
            ).strip()
            plan_digest = sha256_bytes(exact_delta_id.encode("utf-8")).lower()
            self.plan_tasks(
                project_id,
                tasks=[new_task],
                planned_by=exact_actor,
                plan_id=f"steerplan_{plan_digest[:32]}",
                insert_before_task_id=explicit_insert_before or None,
                insert_before_next_hil=(
                    not explicit_insert_before and exact_boundary == "BEFORE_NEXT_HIL"
                ),
            )
            exact_link = str(new_task.get("task_id") or "").strip()

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == exact_link),
                None,
            )
            require(
                task is not None,
                "STEER_DELTA_LINKED_TASK_NOT_FOUND",
                "The classified Plan Lane task does not exist.",
                status="MISMATCH",
                task_id=exact_link,
            )
            task = cast(dict[str, Any], task)
            steer_row = {
                "delta_id": exact_delta_id,
                "text": exact_text,
                "boundary": exact_boundary,
                "boundary_defaulted": boundary == "BEFORE_NEXT_HIL",
                "classification": "NEW_STEP" if is_new_step else "LINKED_EXISTING_STEP",
                "linked_task_id": exact_link,
                "recorded_by": exact_actor,
            }
            existing = next(
                (
                    row
                    for candidate in backlog["tasks"]
                    for row in candidate.get("steer_deltas", [])
                    if row.get("delta_id") == exact_delta_id
                ),
                None,
            )
            if existing is not None:
                require(
                    existing == steer_row,
                    "STEER_DELTA_ID_CONFLICT",
                    "The steer Delta ID already binds different immutable content.",
                    status="MISMATCH",
                    delta_id=exact_delta_id,
                )
                self._persist_backlog(project_id, backlog)
                event = None
                idempotent_reuse = True
            else:
                task.setdefault("steer_deltas", []).append(steer_row)
                event_type = (
                    "STEER_DELTA_NEW_STEP" if is_new_step else "STEER_DELTA_LINKED"
                )
                event = append_delta_event(
                    backlog,
                    task_id=exact_link,
                    event_type=event_type,
                    to_status=str(task["status"]),
                    actor=exact_actor,
                    event_id=(
                        "steer_"
                        f"{sha256_bytes(exact_delta_id.encode('utf-8'))[:32].lower()}"
                    ),
                    assume_initialized=True,
                    details={
                        "delta_id": exact_delta_id,
                        "delta_sha256": sha256_bytes(exact_text.encode("utf-8")),
                        "boundary": exact_boundary,
                        "classification": steer_row["classification"],
                        "task_count_changed": is_new_step,
                    },
                )
                self._persist_backlog(project_id, backlog)
                idempotent_reuse = False
        status = self.backlog_status(project_id)
        host_window_after = _host_plan_window_fingerprint(
            status,
            fixed_window_task_ids=fixed_window_task_ids,
        )
        window_task_ids = [str(row["task_id"]) for row in host_window_after["rows"]]
        active_row_present = bool(host_window_after["active_task_id"])
        linked_row_is_currently_visible = (
            active_row_present and exact_link in window_task_ids
        )
        visible_window_changed = (
            host_window_before["fingerprint_sha256"]
            != host_window_after["fingerprint_sha256"]
        )
        host_plan_window_effect = {
            "schema": "evidence-lane.plan-steer-host-window-effect.v2",
            "window_size": _HOST_PLAN_WINDOW_SIZE,
            "row_start": host_window_after["row_start"],
            "row_end": host_window_after["row_end"],
            "window_task_ids": window_task_ids,
            "linked_task_id": exact_link,
            "active_row_present": active_row_present,
            "linked_row_is_currently_visible": linked_row_is_currently_visible,
            "before_fingerprint_sha256": host_window_before["fingerprint_sha256"],
            "after_fingerprint_sha256": host_window_after["fingerprint_sha256"],
            "visible_window_changed": visible_window_changed,
            "action": (
                "SYNC_CURRENT_HOST_WINDOW_ONCE"
                if visible_window_changed
                else (
                    "LEDGER_ONLY_REUSE_CURRENT_HOST_WINDOW"
                    if active_row_present
                    else "LEDGER_ONLY_NO_ACTIVE_HOST_WINDOW"
                )
            ),
            "host_update_plan_required": visible_window_changed,
            "evi_refresh_invoked": False,
            "full_native_ledger_remains_authority": True,
            "text_only_linked_steer_rebuilds_host_window": False,
        }
        goal_status = cast(dict[str, Any], status.get("goal_projection") or {})
        runtime_status = cast(
            dict[str, Any], status.get("plan_runtime_projection") or {}
        )
        active_task_id = host_window_after.get("active_task_id")
        active_row = next(
            (
                int(row["number"])
                for row in host_window_after["rows"]
                if row.get("task_id") == active_task_id
            ),
            None,
        )
        compact_steer = {
            "delta_id": steer_row["delta_id"],
            "delta_sha256": sha256_bytes(exact_text.encode("utf-8")),
            "boundary": steer_row["boundary"],
            "boundary_defaulted": steer_row["boundary_defaulted"],
            "classification": steer_row["classification"],
            "linked_task_id": steer_row["linked_task_id"],
            "recorded_by": steer_row["recorded_by"],
            "delta_text_returned": False,
        }
        compact_event = (
            {
                "sequence": event["sequence"],
                "event_id": event["event_id"],
                "task_id": event["task_id"],
                "event_type": event["event_type"],
                "from_status": event["from_status"],
                "to_status": event["to_status"],
                "actor": event["actor"],
                "recorded_at": event["recorded_at"],
                "event_sha256": event["event_sha256"],
                "details": event["details"],
            }
            if event is not None
            else None
        )
        backlog_receipt = {
            "schema": "evidence-lane.plan-backlog-write-receipt.v1",
            "project_id": project_id,
            "canonical_task_count": int(goal_status.get("canonical_task_count") or 0),
            "executable_task_count": int(goal_status.get("task_count") or 0),
            "history_task_count": int(goal_status.get("history_task_count") or 0),
            "counts": status.get("counts"),
            "active_task_id": active_task_id,
            "absolute_active_row": active_row,
            "canonical_plan_sha256": goal_status.get("canonical_plan_sha256"),
            "executable_projection_sha256": goal_status.get("projection_sha256"),
            "plan_runtime_sqlite_sha256": runtime_status.get("sqlite_sha256"),
            "plan_runtime_projection_content_sha256": runtime_status.get(
                "projection_content_sha256"
            ),
            "full_backlog_returned": False,
            "full_plan_returned": False,
            "raw_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
        }
        return {
            "status": "PASS",
            "idempotent_reuse": idempotent_reuse,
            "steer": compact_steer,
            "event": compact_event,
            "task_count": status["goal_projection"]["task_count"],
            "task_count_changed": is_new_step,
            "host_plan_window_effect": host_plan_window_effect,
            "backlog_receipt": backlog_receipt,
        }

    def claim_backlog_task(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        contract: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            active = [
                task for task in backlog["tasks"] if task.get("status") == "ACTIVE"
            ]
            require(
                not active,
                "BACKLOG_ACTIVE_TASK_EXISTS",
                "The linear backlog already has one active task.",
                status="BLOCKED",
                active_task_ids=[task["task_id"] for task in active],
            )
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            require(
                task is not None and task.get("status") == "QUEUED",
                "BACKLOG_TASK_NOT_QUEUED",
                "The selected backlog task does not exist or is not queued.",
                status="BLOCKED",
                backlog_task_id=backlog_task_id,
            )
            task = cast(dict[str, Any], task)
            exact_fields = (
                "task_class",
                "requested_outcome",
                "permitted_paths",
                "permitted_tools",
                "acceptance_checks",
                "stop_condition",
            )
            mismatches = {
                field: {
                    "planned": task.get(field),
                    "classified": contract.get(field),
                }
                for field in exact_fields
                if task.get(field) != contract.get(field)
            }
            require(
                not mismatches,
                "BACKLOG_TASK_CONTRACT_MISMATCH",
                "Classification must exactly match the queued task contract.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            now = utc_now()
            task["active_session_id"] = session_id
            task["runtime_task_id"] = contract.get("task_id")
            append_delta_event(
                backlog,
                task_id=backlog_task_id,
                event_type="ACTIVATED",
                to_status="ACTIVE",
                actor=session_id,
                event_id=(
                    f"{backlog_task_id}__{session_id}__"
                    f"{contract.get('task_id')}__active"
                ),
                recorded_at=now,
                assume_initialized=True,
                details={
                    "session_id": session_id,
                    "runtime_task_id": contract.get("task_id"),
                },
            )
            task["history"].append(
                {
                    "event": "CLAIMED",
                    "session_id": session_id,
                    "runtime_task_id": contract.get("task_id"),
                    "recorded_at": now,
                }
            )
            self._persist_backlog(project_id, backlog)
            return task

    def advance_verified_state_travel_task(
        self,
        project_id: str,
        *,
        completed_backlog_task_id: str,
        replacement_backlog_task_id: str,
        session_id: str,
        prior_runtime_task_id: str,
        replacement_contract: dict[str, Any],
        completion_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically close one verified handoff row and activate its successor.

        This is deliberately narrower than normal task completion.  It accepts no
        implementation evidence and creates no candidate: the completion authority
        is the already-consumed, server-verified State Travel receipt.  Keeping both
        Delta events under the project lock prevents a transient zero-active or
        two-active Plan projection.
        """

        receipt_sha256 = str(completion_receipt.get("receipt_sha256") or "").strip()
        receipt_body = {
            key: value
            for key, value in completion_receipt.items()
            if key != "receipt_sha256"
        }
        require(
            completion_receipt.get("schema")
            == "evidence-lane.verified-state-travel-task-advance.v1"
            and len(receipt_sha256) == 64
            and receipt_sha256 == sha256_bytes(canonical_json_bytes(receipt_body))
            and completion_receipt.get("candidate_created") is False
            and completion_receipt.get("pending_hil") is False
            and completion_receipt.get("pointer_moved") is False
            and completion_receipt.get("hil_inferred") is False,
            "STATE_TRAVEL_TASK_ADVANCE_RECEIPT_INVALID",
            "The verified handoff completion receipt is missing, malformed, or not non-promoting.",
            status="MISMATCH",
        )
        handoff_id = str(completion_receipt.get("handoff_id") or "").strip()
        require(
            bool(handoff_id)
            and completion_receipt.get("project_id") == project_id
            and completion_receipt.get("session_id") == session_id
            and completion_receipt.get("completed_backlog_task_id")
            == completed_backlog_task_id
            and completion_receipt.get("replacement_backlog_task_id")
            == replacement_backlog_task_id,
            "STATE_TRAVEL_TASK_ADVANCE_RECEIPT_BINDING_MISMATCH",
            "The verified handoff completion receipt does not bind this exact Plan transition.",
            status="MISMATCH",
        )

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks_by_id = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            completed = tasks_by_id.get(completed_backlog_task_id)
            replacement = tasks_by_id.get(replacement_backlog_task_id)
            require(
                isinstance(completed, dict) and isinstance(replacement, dict),
                "STATE_TRAVEL_TASK_ADVANCE_PLAN_TASK_MISMATCH",
                "The handoff row or its requested successor is absent from the Plan Lane.",
                status="MISMATCH",
                completed_backlog_task_id=completed_backlog_task_id,
                replacement_backlog_task_id=replacement_backlog_task_id,
            )
            completed = cast(dict[str, Any], completed)
            replacement = cast(dict[str, Any], replacement)
            exact_fields = (
                "task_class",
                "requested_outcome",
                "permitted_paths",
                "permitted_tools",
                "acceptance_checks",
                "stop_condition",
            )
            mismatches = {
                field: {
                    "planned": replacement.get(field),
                    "classified": replacement_contract.get(field),
                }
                for field in exact_fields
                if replacement.get(field) != replacement_contract.get(field)
            }
            require(
                not mismatches,
                "STATE_TRAVEL_TASK_ADVANCE_CONTRACT_MISMATCH",
                "The successor classification must exactly match its queued Plan contract.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            replacement_runtime_task_id = str(
                replacement_contract.get("task_id") or ""
            ).strip()
            require(
                bool(replacement_runtime_task_id),
                "STATE_TRAVEL_TASK_ADVANCE_RUNTIME_TASK_ID_REQUIRED",
                "The successor classification has no runtime task identity.",
                status="BLOCKED",
            )

            active = [
                task for task in backlog["tasks"] if task.get("status") == "ACTIVE"
            ]
            first_queued = next(
                (
                    task
                    for task in sorted(
                        backlog["tasks"], key=lambda item: int(item["sequence"])
                    )
                    if task.get("status") == "QUEUED"
                ),
                None,
            )
            before = (
                len(active) == 1
                and active[0].get("task_id") == completed_backlog_task_id
                and completed.get("status") == "ACTIVE"
                and completed.get("active_session_id") == session_id
                and completed.get("runtime_task_id") == prior_runtime_task_id
                and replacement.get("status") == "QUEUED"
                and isinstance(first_queued, dict)
                and first_queued.get("task_id") == replacement_backlog_task_id
            )
            persisted_replacement_runtime_task_id = str(
                replacement.get("runtime_task_id") or ""
            ).strip()
            after = (
                len(active) == 1
                and active[0].get("task_id") == replacement_backlog_task_id
                and completed.get("status") == "DONE"
                and completed.get("state_travel_completion_receipt_sha256")
                == receipt_sha256
                and completed.get("state_travel_completion_receipt")
                == completion_receipt
                and replacement.get("status") == "ACTIVE"
                and replacement.get("active_session_id") == session_id
                and bool(persisted_replacement_runtime_task_id)
            )
            require(
                before or after,
                "STATE_TRAVEL_TASK_ADVANCE_PLAN_STATE_MISMATCH",
                "The Plan is neither at the exact verified handoff boundary nor its idempotent successor state.",
                status="MISMATCH",
                active_task_ids=[task.get("task_id") for task in active],
                completed_status=completed.get("status"),
                replacement_status=replacement.get("status"),
                first_queued_task_id=(
                    first_queued.get("task_id")
                    if isinstance(first_queued, dict)
                    else None
                ),
            )
            if after:
                replacement_runtime_task_id = persisted_replacement_runtime_task_id

            completed_event_id = (
                f"{completed_backlog_task_id}__{handoff_id}__state_travel_done"
            )
            replacement_event_id = (
                f"{replacement_backlog_task_id}__{session_id}__"
                f"{replacement_runtime_task_id}__state_travel_active"
            )
            completion_event: dict[str, Any] | None
            activation_event: dict[str, Any] | None
            if before:
                now = utc_now()
                completion_details = {
                    "handoff_id": handoff_id,
                    "session_id": session_id,
                    "completion_receipt_sha256": receipt_sha256,
                    "candidate_created": False,
                    "pending_hil": False,
                    "pointer_moved": False,
                    "hil_inferred": False,
                }
                completion_event = append_delta_event(
                    backlog,
                    task_id=completed_backlog_task_id,
                    event_type="STATE_TRAVEL_HANDOFF_COMPLETED",
                    to_status="DONE",
                    actor=session_id,
                    event_id=completed_event_id,
                    recorded_at=now,
                    assume_initialized=True,
                    details=completion_details,
                )
                completed.pop("active_session_id", None)
                completed.pop("runtime_task_id", None)
                completed["state_travel_completion_receipt_sha256"] = receipt_sha256
                completed["state_travel_completion_receipt"] = completion_receipt
                completed.setdefault("history", []).append(
                    {
                        "event": "STATE_TRAVEL_HANDOFF_COMPLETED",
                        "handoff_id": handoff_id,
                        "session_id": session_id,
                        "completion_receipt_sha256": receipt_sha256,
                        "recorded_at": now,
                    }
                )
                activation_event = append_delta_event(
                    backlog,
                    task_id=replacement_backlog_task_id,
                    event_type="ACTIVATED_AFTER_STATE_TRAVEL",
                    to_status="ACTIVE",
                    actor=session_id,
                    event_id=replacement_event_id,
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "runtime_task_id": replacement_runtime_task_id,
                        "completed_backlog_task_id": completed_backlog_task_id,
                        "handoff_id": handoff_id,
                        "completion_receipt_sha256": receipt_sha256,
                    },
                )
                replacement["active_session_id"] = session_id
                replacement["runtime_task_id"] = replacement_runtime_task_id
                replacement.setdefault("history", []).append(
                    {
                        "event": "CLAIMED_AFTER_STATE_TRAVEL",
                        "session_id": session_id,
                        "runtime_task_id": replacement_runtime_task_id,
                        "completed_backlog_task_id": completed_backlog_task_id,
                        "handoff_id": handoff_id,
                        "recorded_at": now,
                    }
                )
                self._persist_backlog(project_id, backlog)
            else:
                completion_event = next(
                    (
                        event
                        for event in backlog["events"]
                        if event.get("event_id") == completed_event_id
                    ),
                    None,
                )
                activation_event = next(
                    (
                        event
                        for event in backlog["events"]
                        if event.get("event_id") == replacement_event_id
                    ),
                    None,
                )
                require(
                    isinstance(completion_event, dict)
                    and isinstance(activation_event, dict),
                    "STATE_TRAVEL_TASK_ADVANCE_EVENT_LEDGER_MISMATCH",
                    "The idempotent Plan state is missing its exact completion or activation event.",
                    status="MISMATCH",
                )

            return {
                "status": "PASS",
                "idempotent_reuse": after,
                "completed_task": completed,
                "active_task": replacement,
                "completion_event": completion_event,
                "activation_event": activation_event,
                "completion_receipt": completion_receipt,
            }

    def advance_verified_task_checkpoint(
        self,
        project_id: str,
        *,
        completed_backlog_task_id: str,
        replacement_backlog_task_id: str,
        session_id: str,
        prior_runtime_task_id: str,
        replacement_contract: dict[str, Any],
        completion_receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Advance one verified row and keep the governed-session cursor joined.

        The Plan file and session file are separate authorities, so a process can
        stop after the Plan write but before the session mirror is updated.  The
        sealed receipt makes that partial state replayable: a retry repairs only
        the exact session cursor and never creates another Delta event or sub-PV.
        """

        receipt_sha256 = str(completion_receipt.get("receipt_sha256") or "").strip()
        receipt_body = {
            key: value
            for key, value in completion_receipt.items()
            if key != "receipt_sha256"
        }
        proof = completion_receipt.get("verification_proof")
        proof_body = (
            {key: value for key, value in proof.items() if key != "receipt_sha256"}
            if isinstance(proof, dict)
            else {}
        )
        preserved_candidate = completion_receipt.get(
            "existing_candidate_preserved"
        )
        pending_hil = completion_receipt.get("pending_hil")
        preserved_candidate_shape = (
            isinstance(preserved_candidate, dict)
            and preserved_candidate.get("preserved") is True
            and preserved_candidate.get("session_id") == session_id
            and bool(str(preserved_candidate.get("candidate_id") or "").strip())
            and re.fullmatch(
                r"[A-F0-9]{64}",
                str(preserved_candidate.get("session_file_sha256") or ""),
            )
            is not None
        )
        require(
            completion_receipt.get("schema")
            == "evidence-lane.verified-task-checkpoint-advance.v1"
            and len(receipt_sha256) == 64
            and receipt_sha256 == sha256_bytes(canonical_json_bytes(receipt_body))
            and isinstance(proof, dict)
            and proof.get("status") == "PASS"
            and len(str(proof.get("receipt_sha256") or "")) == 64
            and proof.get("receipt_sha256")
            == sha256_bytes(canonical_json_bytes(proof_body))
            and bool(str(completion_receipt.get("verification_kind") or "").strip())
            and completion_receipt.get("candidate_created") is False
            and (
                pending_hil is False
                or (pending_hil is True and preserved_candidate_shape)
            )
            and completion_receipt.get("pointer_moved") is False
            and completion_receipt.get("hil_inferred") is False,
            "TASK_CHECKPOINT_ADVANCE_RECEIPT_INVALID",
            "The verified task-checkpoint receipt is malformed, unsealed, or promoting.",
            status="MISMATCH",
        )
        require(
            completion_receipt.get("project_id") == project_id
            and completion_receipt.get("session_id") == session_id
            and completion_receipt.get("completed_backlog_task_id")
            == completed_backlog_task_id
            and completion_receipt.get("replacement_backlog_task_id")
            == replacement_backlog_task_id
            and completion_receipt.get("prior_runtime_task_id")
            == prior_runtime_task_id,
            "TASK_CHECKPOINT_ADVANCE_RECEIPT_BINDING_MISMATCH",
            "The verified checkpoint does not bind this exact Plan transition.",
            status="MISMATCH",
        )

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            session_path = (
                self.project_root(project_id) / "sessions" / f"{session_id}.json"
            )
            require(
                session_path.is_file(),
                "TASK_CHECKPOINT_SESSION_NOT_FOUND",
                "The verified checkpoint cannot advance without its governed session.",
                status="MISMATCH",
                session_id=session_id,
            )
            session_file_sha256_before = sha256_file(session_path)
            session_payload = json.loads(session_path.read_text(encoding="utf-8"))
            session_metadata = session_payload.setdefault("metadata", {})
            require(
                session_payload.get("session_id") == session_id
                and isinstance(session_metadata, dict),
                "TASK_CHECKPOINT_SESSION_IDENTITY_MISMATCH",
                "The checkpoint session file does not contain the exact governed session identity.",
                status="MISMATCH",
                session_id=session_id,
            )
            tasks_by_id = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            completed = tasks_by_id.get(completed_backlog_task_id)
            replacement = tasks_by_id.get(replacement_backlog_task_id)
            require(
                isinstance(completed, dict) and isinstance(replacement, dict),
                "TASK_CHECKPOINT_ADVANCE_PLAN_TASK_MISMATCH",
                "The verified row or requested successor is absent from Plan Lane.",
                status="MISMATCH",
                completed_backlog_task_id=completed_backlog_task_id,
                completed_present=completed is not None,
                replacement_backlog_task_id=replacement_backlog_task_id,
                replacement_present=replacement is not None,
                task_count=len(tasks_by_id),
            )
            completed = cast(dict[str, Any], completed)
            replacement = cast(dict[str, Any], replacement)
            exact_fields = (
                "task_class",
                "requested_outcome",
                "permitted_paths",
                "permitted_tools",
                "acceptance_checks",
                "stop_condition",
            )
            mismatches = {
                field: {
                    "planned": replacement.get(field),
                    "classified": replacement_contract.get(field),
                }
                for field in exact_fields
                if replacement.get(field) != replacement_contract.get(field)
            }
            require(
                not mismatches,
                "TASK_CHECKPOINT_ADVANCE_CONTRACT_MISMATCH",
                "The successor classification must exactly match its queued Plan contract.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            replacement_runtime_task_id = str(
                replacement_contract.get("task_id") or ""
            ).strip()
            require(
                bool(replacement_runtime_task_id),
                "TASK_CHECKPOINT_ADVANCE_RUNTIME_TASK_ID_REQUIRED",
                "The successor classification has no runtime task identity.",
                status="BLOCKED",
            )
            active = [
                task for task in backlog["tasks"] if task.get("status") == "ACTIVE"
            ]
            first_queued = next(
                (
                    task
                    for task in sorted(
                        backlog["tasks"], key=lambda item: int(item["sequence"])
                    )
                    if task.get("status") == "QUEUED"
                ),
                None,
            )
            before = (
                len(active) == 1
                and active[0].get("task_id") == completed_backlog_task_id
                and completed.get("status") == "ACTIVE"
                and completed.get("active_session_id") == session_id
                and completed.get("runtime_task_id") == prior_runtime_task_id
                and replacement.get("status") == "QUEUED"
                and isinstance(first_queued, dict)
                and first_queued.get("task_id") == replacement_backlog_task_id
            )
            persisted_replacement_runtime_task_id = str(
                replacement.get("runtime_task_id") or ""
            ).strip()
            after = (
                len(active) == 1
                and active[0].get("task_id") == replacement_backlog_task_id
                and completed.get("status") == "DONE"
                and completed.get("task_checkpoint_completion_receipt_sha256")
                == receipt_sha256
                and completed.get("task_checkpoint_completion_receipt")
                == completion_receipt
                and replacement.get("status") == "ACTIVE"
                and replacement.get("active_session_id") == session_id
                and bool(persisted_replacement_runtime_task_id)
            )
            require(
                before or after,
                "TASK_CHECKPOINT_ADVANCE_PLAN_STATE_MISMATCH",
                "Plan Lane is neither at the verified checkpoint nor its idempotent successor state.",
                status="MISMATCH",
                active_task_ids=[task.get("task_id") for task in active],
                completed_status=completed.get("status"),
                replacement_status=replacement.get("status"),
                first_queued_task_id=(
                    first_queued.get("task_id")
                    if isinstance(first_queued, dict)
                    else None
                ),
            )
            if after:
                replacement_runtime_task_id = persisted_replacement_runtime_task_id

            existing_session_bindings = session_metadata.setdefault(
                "task_checkpoint_session_bindings", []
            )
            require(
                isinstance(existing_session_bindings, list),
                "TASK_CHECKPOINT_SESSION_BINDING_HISTORY_INVALID",
                "The governed session checkpoint-binding history is not a list.",
                status="MISMATCH",
            )
            existing_session_binding = next(
                (
                    row
                    for row in existing_session_bindings
                    if isinstance(row, dict)
                    and row.get("checkpoint_receipt_sha256") == receipt_sha256
                ),
                None,
            )
            session_active_task_id = str(
                session_metadata.get("active_backlog_task_id") or ""
            ).strip()
            session_at_successor = (
                session_active_task_id == replacement_backlog_task_id
                and session_metadata.get("active_backlog_task_status") == "ACTIVE"
            )
            if pending_hil is True:
                exact_preserved_candidate = cast(
                    dict[str, Any], preserved_candidate
                )
                require(
                    session_payload.get("candidate_id")
                    == exact_preserved_candidate["candidate_id"]
                    and str(session_payload.get("state") or "").endswith(
                        "_CANDIDATE"
                    ),
                    "TASK_CHECKPOINT_PRESERVED_CANDIDATE_IDENTITY_MISMATCH",
                    "The existing pending candidate identity is not the sealed preserved boundary.",
                    status="MISMATCH",
                )
                if existing_session_binding is None:
                    require(
                        session_file_sha256_before
                        == exact_preserved_candidate["session_file_sha256"],
                        "TASK_CHECKPOINT_PRESERVED_CANDIDATE_SESSION_DRIFT",
                        "The pending-candidate session is neither the sealed pre-write state nor an idempotent bound state.",
                        status="MISMATCH",
                    )
            if before:
                require(
                    session_active_task_id == completed_backlog_task_id
                    and session_metadata.get("active_backlog_task_status") == "ACTIVE",
                    "TASK_CHECKPOINT_SESSION_PLAN_BEFORE_MISMATCH",
                    "The governed session must mirror the exact active Plan row before advancement.",
                    status="MISMATCH",
                    session_active_task_id=session_active_task_id or None,
                    completed_backlog_task_id=completed_backlog_task_id,
                )
            elif existing_session_binding is None and pending_hil is not True:
                require(
                    session_active_task_id
                    in {completed_backlog_task_id, replacement_backlog_task_id},
                    "TASK_CHECKPOINT_SESSION_PLAN_RECOVERY_MISMATCH",
                    "A candidate-free replay can repair only the exact predecessor or successor session cursor.",
                    status="MISMATCH",
                    session_active_task_id=session_active_task_id or None,
                )

            host_projection_repaired = False
            for projected_task, expected_host_status in (
                (completed, "completed"),
                (replacement, "in_progress"),
            ):
                host_projection = projected_task.get("host_step_projection")
                if not isinstance(host_projection, dict):
                    continue
                require(
                    host_projection.get("schema")
                    == "evidence-lane.host-step-row.v2",
                    "TASK_CHECKPOINT_HOST_PROJECTION_SCHEMA_MISMATCH",
                    "A projected Plan task has an unsupported host-row schema.",
                    status="MISMATCH",
                    task_id=projected_task.get("task_id"),
                )
                if host_projection.get("host_status") != expected_host_status:
                    host_projection["host_status"] = expected_host_status
                    host_projection_repaired = True

            proof_sha256 = str(cast(dict[str, Any], proof)["receipt_sha256"])
            completion_event_id = f"{completed_backlog_task_id}__{proof_sha256[:24].lower()}__checkpoint_done"
            activation_event_id = (
                f"{replacement_backlog_task_id}__{session_id}__"
                f"{replacement_runtime_task_id}__checkpoint_active"
            )
            completion_event: dict[str, Any] | None
            activation_event: dict[str, Any] | None
            sub_pv_acceptance: dict[str, Any] | None
            if before:
                now = utc_now()
                completion_event = append_delta_event(
                    backlog,
                    task_id=completed_backlog_task_id,
                    event_type="VERIFIED_TASK_CHECKPOINT_COMPLETED",
                    to_status="DONE",
                    actor=session_id,
                    event_id=completion_event_id,
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "verification_kind": completion_receipt.get(
                            "verification_kind"
                        ),
                        "completion_receipt_sha256": receipt_sha256,
                        "verification_proof_sha256": proof_sha256,
                        "candidate_created": False,
                        "pending_hil": bool(pending_hil),
                        "existing_candidate_id": (
                            cast(dict[str, Any], preserved_candidate).get(
                                "candidate_id"
                            )
                            if pending_hil is True
                            else None
                        ),
                        "pointer_moved": False,
                        "hil_inferred": False,
                    },
                )
                completed.pop("active_session_id", None)
                completed.pop("runtime_task_id", None)
                completed["task_checkpoint_completion_receipt_sha256"] = receipt_sha256
                completed["task_checkpoint_completion_receipt"] = completion_receipt
                sub_pv_acceptance = append_sub_pv_acceptance(
                    backlog,
                    task_id=completed_backlog_task_id,
                    successor_task_id=replacement_backlog_task_id,
                    session_id=session_id,
                    baseline_pv=str(completion_receipt.get("accepted_pv") or ""),
                    pointer_generation=int(
                        completion_receipt.get("pointer_generation") or 0
                    ),
                    task_checkpoint_completion_receipt_sha256=receipt_sha256,
                    verification_proof_sha256=proof_sha256,
                    delta_completion_event_sha256=str(
                        completion_event["event_sha256"]
                    ),
                    accepted_at=now,
                )
                completed["accepted_sub_pv"] = sub_pv_acceptance
                completed.setdefault("history", []).append(
                    {
                        "event": "VERIFIED_TASK_CHECKPOINT_COMPLETED",
                        "session_id": session_id,
                        "verification_kind": completion_receipt.get(
                            "verification_kind"
                        ),
                        "completion_receipt_sha256": receipt_sha256,
                        "verification_proof_sha256": proof_sha256,
                        "accepted_sub_pv_id": sub_pv_acceptance["sub_pv_id"],
                        "accepted_sub_pv_receipt_sha256": sub_pv_acceptance[
                            "receipt_sha256"
                        ],
                        "recorded_at": now,
                    }
                )
                activation_event = append_delta_event(
                    backlog,
                    task_id=replacement_backlog_task_id,
                    event_type="ACTIVATED_AFTER_VERIFIED_TASK_CHECKPOINT",
                    to_status="ACTIVE",
                    actor=session_id,
                    event_id=activation_event_id,
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "runtime_task_id": replacement_runtime_task_id,
                        "completed_backlog_task_id": completed_backlog_task_id,
                        "completion_receipt_sha256": receipt_sha256,
                        "entry_sub_pv_id": sub_pv_acceptance["sub_pv_id"],
                        "entry_sub_pv_receipt_sha256": sub_pv_acceptance[
                            "receipt_sha256"
                        ],
                    },
                )
                replacement["active_session_id"] = session_id
                replacement["runtime_task_id"] = replacement_runtime_task_id
                replacement["entry_sub_pv"] = sub_pv_acceptance
                replacement.setdefault("history", []).append(
                    {
                        "event": "CLAIMED_AFTER_VERIFIED_TASK_CHECKPOINT",
                        "session_id": session_id,
                        "runtime_task_id": replacement_runtime_task_id,
                        "completed_backlog_task_id": completed_backlog_task_id,
                        "entry_sub_pv_id": sub_pv_acceptance["sub_pv_id"],
                        "entry_sub_pv_receipt_sha256": sub_pv_acceptance[
                            "receipt_sha256"
                        ],
                        "recorded_at": now,
                    }
                )
                self._persist_backlog(project_id, backlog)
            else:
                completion_event = next(
                    (
                        event
                        for event in backlog["events"]
                        if event.get("event_id") == completion_event_id
                    ),
                    None,
                )
                activation_event = next(
                    (
                        event
                        for event in backlog["events"]
                        if event.get("event_id") == activation_event_id
                    ),
                    None,
                )
                require(
                    isinstance(completion_event, dict)
                    and isinstance(activation_event, dict),
                    "TASK_CHECKPOINT_ADVANCE_EVENT_LEDGER_MISMATCH",
                    "The idempotent checkpoint state lacks its completion or activation event.",
                    status="MISMATCH",
                )
                sub_pv_acceptance = next(
                    (
                        record
                        for record in backlog.get("sub_pv_acceptances", [])
                        if record.get("task_id") == completed_backlog_task_id
                    ),
                    None,
                )
                require(
                    isinstance(sub_pv_acceptance, dict)
                    and completed.get("accepted_sub_pv") == sub_pv_acceptance
                    and replacement.get("entry_sub_pv") == sub_pv_acceptance,
                    "TASK_CHECKPOINT_ADVANCE_SUB_PV_REPLAY_MISMATCH",
                    "The idempotent checkpoint state is missing its accepted Delta-row sub-PV.",
                    status="MISMATCH",
                )
                if host_projection_repaired:
                    self._persist_backlog(project_id, backlog)

            session_binding_repaired = not session_at_successor
            if existing_session_binding is not None:
                existing_binding_body = {
                    key: value
                    for key, value in existing_session_binding.items()
                    if key != "receipt_sha256"
                }
                require(
                    existing_session_binding.get("schema")
                    == "evidence-lane.task-checkpoint-session-binding.v1"
                    and existing_session_binding.get("status") == "PASS"
                    and existing_session_binding.get("project_id") == project_id
                    and existing_session_binding.get("session_id") == session_id
                    and existing_session_binding.get("completed_backlog_task_id")
                    == completed_backlog_task_id
                    and existing_session_binding.get("replacement_backlog_task_id")
                    == replacement_backlog_task_id
                    and existing_session_binding.get("replacement_runtime_task_id")
                    == replacement_runtime_task_id
                    and existing_session_binding.get("candidate_id")
                    == (
                        cast(dict[str, Any], preserved_candidate).get("candidate_id")
                        if pending_hil is True
                        else None
                    )
                    and existing_session_binding.get("receipt_sha256")
                    == sha256_bytes(canonical_json_bytes(existing_binding_body)),
                    "TASK_CHECKPOINT_SESSION_BINDING_REPLAY_MISMATCH",
                    "The existing session-cursor receipt does not match this exact checkpoint.",
                    status="MISMATCH",
                )
                session_binding = cast(dict[str, Any], existing_session_binding)
            else:
                bound_at = utc_now()
                binding_body = {
                    "schema": "evidence-lane.task-checkpoint-session-binding.v1",
                    "status": "PASS",
                    "project_id": project_id,
                    "session_id": session_id,
                    "checkpoint_receipt_sha256": receipt_sha256,
                    "completed_backlog_task_id": completed_backlog_task_id,
                    "replacement_backlog_task_id": replacement_backlog_task_id,
                    "replacement_runtime_task_id": replacement_runtime_task_id,
                    "prior_session_file_sha256": session_file_sha256_before,
                    "prior_session_active_backlog_task_id": (
                        session_active_task_id or None
                    ),
                    "candidate_id": (
                        cast(dict[str, Any], preserved_candidate).get("candidate_id")
                        if pending_hil is True
                        else None
                    ),
                    "candidate_preserved": pending_hil is True,
                    "pointer_moved": False,
                    "hil_inferred": False,
                    "bound_at": bound_at,
                }
                session_binding = {
                    **binding_body,
                    "receipt_sha256": sha256_bytes(
                        canonical_json_bytes(binding_body)
                    ),
                }
                existing_session_bindings.append(session_binding)

            session_metadata["active_backlog_task_id"] = replacement_backlog_task_id
            session_metadata["active_backlog_task_status"] = "ACTIVE"
            session_metadata["last_task_checkpoint_session_binding"] = session_binding
            executable_tasks = [
                task
                for task in sorted(
                    backlog["tasks"], key=lambda item: int(item["sequence"])
                )
                if task.get("status") in {"DONE", "ACCEPTED", "ACTIVE", "QUEUED"}
            ]
            successor_index = next(
                index
                for index, task in enumerate(executable_tasks)
                if task.get("task_id") == replacement_backlog_task_id
            )
            window_start = (
                successor_index // _HOST_PLAN_WINDOW_SIZE
            ) * _HOST_PLAN_WINDOW_SIZE
            aligned_window_task_ids = [
                str(task["task_id"])
                for task in executable_tasks[
                    window_start : window_start + _HOST_PLAN_WINDOW_SIZE
                ]
            ]
            prior_host_window = cast(
                dict[str, Any], session_metadata.get("host_plan_window") or {}
            )
            host_plan_window_repaired = (
                prior_host_window.get("window_task_ids")
                != aligned_window_task_ids
            )
            session_metadata["host_plan_window"] = {
                **prior_host_window,
                "schema": "evidence-lane.host-plan-window-state.v1",
                "window_task_ids": aligned_window_task_ids,
                "binding_source": "VERIFIED_TASK_CHECKPOINT_TRANSITION",
                "checkpoint_receipt_sha256": receipt_sha256,
            }
            session_advances = session_metadata.setdefault(
                "task_checkpoint_advances", []
            )
            require(
                isinstance(session_advances, list),
                "TASK_CHECKPOINT_SESSION_ADVANCE_HISTORY_INVALID",
                "The governed session checkpoint-advance history is not a list.",
                status="MISMATCH",
            )
            if not any(
                isinstance(row, dict)
                and row.get("receipt_sha256") == receipt_sha256
                for row in session_advances
            ):
                session_advances.append(completion_receipt)
            session_metadata["last_task_checkpoint_advance"] = completion_receipt
            session_payload["metadata"] = session_metadata
            if existing_session_binding is None or session_binding_repaired:
                atomic_write_json(session_path, session_payload)
            repaired_session = json.loads(session_path.read_text(encoding="utf-8"))
            require(
                repaired_session.get("candidate_id")
                == session_payload.get("candidate_id")
                and repaired_session.get("state") == session_payload.get("state")
                and repaired_session.get("metadata", {}).get(
                    "active_backlog_task_id"
                )
                == replacement_backlog_task_id
                and repaired_session.get("metadata", {}).get(
                    "active_backlog_task_status"
                )
                == "ACTIVE",
                "TASK_CHECKPOINT_SESSION_BINDING_READBACK_MISMATCH",
                "The governed session did not read back at the exact successor cursor.",
                status="MISMATCH",
            )
            return {
                "status": "PASS",
                "idempotent_reuse": after,
                "completed_task": completed,
                "active_task": replacement,
                "completion_event": completion_event,
                "activation_event": activation_event,
                "completion_receipt": completion_receipt,
                "sub_pv_acceptance": sub_pv_acceptance,
                "host_projection_repaired": host_projection_repaired,
                "session_binding_repaired": session_binding_repaired,
                "session_binding": session_binding,
                "session_file_sha256": sha256_file(session_path),
                "host_plan_window_repaired": host_plan_window_repaired,
                "host_plan_window_task_ids": aligned_window_task_ids,
            }

    def batch_completion_receipt(
        self,
        project_id: str,
        receipt_id: str,
    ) -> dict[str, Any] | None:
        """Return one immutable batch-completion receipt without changing Plan state."""

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            receipt = next(
                (
                    row
                    for row in backlog.get("batch_completion_receipts", [])
                    if row.get("receipt_id") == receipt_id
                ),
                None,
            )
            return dict(receipt) if isinstance(receipt, dict) else None

    def reconcile_verified_predecessor_sub_pv(
        self,
        project_id: str,
        *,
        active_task_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Backfill only an exact verified predecessor missed by older runtime bytes."""

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            executable = [
                task
                for task in sorted(
                    backlog.get("tasks", []),
                    key=lambda item: int(item["sequence"]),
                )
                if task.get("status") in {"DONE", "ACCEPTED", "ACTIVE", "QUEUED"}
            ]
            active_index = next(
                (
                    index
                    for index, task in enumerate(executable)
                    if task.get("task_id") == active_task_id
                ),
                None,
            )
            require(
                active_index is not None
                and executable[active_index].get("status") == "ACTIVE"
                and executable[active_index].get("active_session_id") == session_id,
                "SUB_PV_RECONCILIATION_ACTIVE_ROW_MISMATCH",
                "Sub-PV reconciliation requires the exact sole active Delta row.",
                status="MISMATCH",
                active_task_id=active_task_id,
            )
            active_index = cast(int, active_index)
            if active_index == 0:
                return {
                    "status": "PASS",
                    "state": "FULL_PROJECT_PV_BASELINE_NO_PREDECESSOR",
                    "sub_pv_acceptance": None,
                    "plan_task_advanced": False,
                    "project_pointer_moved": False,
                }
            active = cast(dict[str, Any], executable[active_index])
            predecessor = cast(dict[str, Any], executable[active_index - 1])
            predecessor_task_id = str(predecessor["task_id"])
            existing = next(
                (
                    record
                    for record in backlog.get("sub_pv_acceptances", [])
                    if record.get("task_id") == predecessor_task_id
                ),
                None,
            )
            if isinstance(existing, dict):
                require(
                    existing.get("successor_task_id") == active_task_id,
                    "SUB_PV_RECONCILIATION_SUCCESSOR_MISMATCH",
                    "The predecessor sub-PV is bound to another successor row.",
                    status="MISMATCH",
                    predecessor_task_id=predecessor_task_id,
                )
                changed = active.get("entry_sub_pv") != existing
                active["entry_sub_pv"] = existing
                if changed:
                    self._persist_backlog(project_id, backlog)
                return {
                    "status": "PASS",
                    "state": "EXISTING_SUB_PV_REUSED",
                    "sub_pv_acceptance": existing,
                    "plan_task_advanced": False,
                    "project_pointer_moved": False,
                }
            completion_receipt = predecessor.get(
                "task_checkpoint_completion_receipt"
            )
            receipt_sha256 = str(
                predecessor.get("task_checkpoint_completion_receipt_sha256")
                or ""
            )
            if predecessor.get("status") == "ACCEPTED" and not isinstance(
                completion_receipt, dict
            ):
                return {
                    "status": "PASS",
                    "state": "FULL_PROJECT_PV_ACCEPTED_PREDECESSOR",
                    "sub_pv_acceptance": None,
                    "plan_task_advanced": False,
                    "project_pointer_moved": False,
                }
            proof = (
                completion_receipt.get("verification_proof")
                if isinstance(completion_receipt, dict)
                else None
            )
            completion_event = next(
                (
                    event
                    for event in reversed(backlog.get("events", []))
                    if event.get("task_id") == predecessor_task_id
                    and event.get("event_type")
                    == "VERIFIED_TASK_CHECKPOINT_COMPLETED"
                    and event.get("to_status") == "DONE"
                ),
                None,
            )
            receipt_body = (
                {
                    key: value
                    for key, value in completion_receipt.items()
                    if key != "receipt_sha256"
                }
                if isinstance(completion_receipt, dict)
                else {}
            )
            proof_body = (
                {key: value for key, value in proof.items() if key != "receipt_sha256"}
                if isinstance(proof, dict)
                else {}
            )
            require(
                predecessor.get("status") == "DONE"
                and isinstance(completion_receipt, dict)
                and completion_receipt.get("schema")
                == "evidence-lane.verified-task-checkpoint-advance.v1"
                and receipt_sha256 == completion_receipt.get("receipt_sha256")
                and receipt_sha256
                == sha256_bytes(canonical_json_bytes(receipt_body))
                and isinstance(proof, dict)
                and proof.get("status") == "PASS"
                and proof.get("receipt_sha256")
                == sha256_bytes(canonical_json_bytes(proof_body))
                and isinstance(completion_event, dict)
                and completion_event.get("details", {}).get(
                    "completion_receipt_sha256"
                )
                == receipt_sha256,
                "SUB_PV_RECONCILIATION_VERIFIED_COMPLETION_REQUIRED",
                "A missing predecessor sub-PV may be repaired only from its exact verified completion receipt.",
                status="MISMATCH",
                predecessor_task_id=predecessor_task_id,
            )
            completion_receipt_dict = cast(dict[str, Any], completion_receipt)
            proof_dict = cast(dict[str, Any], proof)
            completion_event_dict = cast(dict[str, Any], completion_event)
            record = append_sub_pv_acceptance(
                backlog,
                task_id=predecessor_task_id,
                successor_task_id=active_task_id,
                session_id=str(completion_receipt_dict["session_id"]),
                baseline_pv=str(completion_receipt_dict["accepted_pv"]),
                pointer_generation=int(completion_receipt_dict["pointer_generation"]),
                task_checkpoint_completion_receipt_sha256=receipt_sha256,
                verification_proof_sha256=str(proof_dict["receipt_sha256"]),
                delta_completion_event_sha256=str(
                    completion_event_dict["event_sha256"]
                ),
                accepted_at=str(completion_event_dict["recorded_at"]),
                reconciled_from_verified_completion=True,
            )
            predecessor["accepted_sub_pv"] = record
            active["entry_sub_pv"] = record
            predecessor.setdefault("history", []).append(
                {
                    "event": "VERIFIED_SUB_PV_RECONCILED",
                    "successor_task_id": active_task_id,
                    "accepted_sub_pv_id": record["sub_pv_id"],
                    "accepted_sub_pv_receipt_sha256": record["receipt_sha256"],
                    "recorded_at": utc_now(),
                }
            )
            self._persist_backlog(project_id, backlog)
            return {
                "status": "PASS",
                "state": "VERIFIED_PREDECESSOR_SUB_PV_RECONCILED",
                "sub_pv_acceptance": record,
                "plan_task_advanced": False,
                "project_pointer_moved": False,
            }

    def record_backlog_done(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        """Mark one active Delta DONE when automatic Refresh seals its candidate."""

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            task = cast(dict[str, Any] | None, task)
            event_id = f"{backlog_task_id}__{candidate_id}__done"
            existing_event = next(
                (event for event in backlog["events"] if event["event_id"] == event_id),
                None,
            )
            if existing_event is not None and task is not None:
                append_delta_event(
                    backlog,
                    task_id=backlog_task_id,
                    event_type="TASK_DONE",
                    to_status="DONE",
                    actor=session_id,
                    event_id=event_id,
                    assume_initialized=True,
                    details={
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                    },
                )
                return task
            require(
                task is not None
                and task.get("status") == "ACTIVE"
                and task.get("active_session_id") == session_id,
                "BACKLOG_TASK_DONE_STATE_INVALID",
                "Only the active task in this session may become DONE.",
                status="BLOCKED",
            )
            task = cast(dict[str, Any], task)
            event = append_delta_event(
                backlog,
                task_id=backlog_task_id,
                event_type="TASK_DONE",
                to_status="DONE",
                actor=session_id,
                event_id=event_id,
                assume_initialized=True,
                details={
                    "candidate_id": candidate_id,
                    "session_id": session_id,
                },
            )
            task["completed_candidate_id"] = candidate_id
            task["history"].append(
                {
                    "event": "TASK_DONE",
                    "candidate_id": candidate_id,
                    "session_id": session_id,
                    "recorded_at": event["recorded_at"],
                }
            )
            self._persist_backlog(project_id, backlog)
            return task

    @staticmethod
    def _validate_batch_completion_evidence(
        backlog: dict[str, Any],
        *,
        task_evidence: list[dict[str, Any]],
        confirmation: str,
    ) -> list[dict[str, Any]]:
        require(
            confirmation == _BATCH_COMPLETION_CONFIRMATION,
            "BATCH_DELTA_COMPLETION_CONFIRMATION_INVALID",
            "Batch completion requires the exact implementation-evidence confirmation.",
            status="BLOCKED",
            required=_BATCH_COMPLETION_CONFIRMATION,
        )
        active = [
            task for task in backlog.get("tasks", []) if task.get("status") == "ACTIVE"
        ]
        require(
            not active,
            "BATCH_DELTA_ACTIVE_TASK_EXISTS",
            "Batch completion cannot bypass an already-active linear Delta.",
            status="BLOCKED",
            active_task_ids=[task.get("task_id") for task in active],
        )
        queued = sorted(
            (
                task
                for task in backlog.get("tasks", [])
                if task.get("status") == "QUEUED"
            ),
            key=lambda task: int(task.get("sequence", 0)),
        )
        require(
            bool(queued),
            "BATCH_DELTA_NO_QUEUED_TASKS",
            "Batch completion requires at least one queued Delta.",
            status="BLOCKED",
        )
        supplied_ids = [str(row.get("task_id", "")) for row in task_evidence]
        expected_ids = [str(task["task_id"]) for task in queued]
        require(
            supplied_ids == expected_ids,
            "BATCH_DELTA_ORDER_OR_SET_MISMATCH",
            "Batch evidence must name every queued Delta exactly once and in ledger order.",
            status="MISMATCH",
            expected_task_ids=expected_ids,
            supplied_task_ids=supplied_ids,
        )
        normalized: list[dict[str, Any]] = []
        for task, raw in zip(queued, task_evidence, strict=True):
            require(
                raw.get("status") == "PASS",
                "BATCH_DELTA_EVIDENCE_NOT_PASS",
                "Every completed Delta requires an explicit PASS evidence status.",
                status="BLOCKED",
                task_id=task["task_id"],
            )
            implementation = raw.get("implementation_evidence")
            verification = raw.get("verification_evidence")
            limitations = raw.get("limitations", [])
            require(
                isinstance(implementation, list)
                and 1 <= len(implementation) <= 64
                and all(
                    isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                    for value in implementation
                )
                and isinstance(verification, list)
                and 1 <= len(verification) <= 64
                and all(
                    isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                    for value in verification
                )
                and isinstance(limitations, list)
                and len(limitations) <= 32
                and all(
                    isinstance(value, str) and 1 <= len(value.strip()) <= 2048
                    for value in limitations
                ),
                "BATCH_DELTA_EVIDENCE_SHAPE_INVALID",
                "Each Delta needs bounded implementation and verification evidence.",
                status="BLOCKED",
                task_id=task["task_id"],
            )
            contract = {field: task.get(field) for field in _BATCH_CONTRACT_FIELDS}
            exact_implementation = cast(list[str], implementation)
            exact_verification = cast(list[str], verification)
            exact_limitations = cast(list[str], limitations)
            safe_evidence = cast(
                dict[str, Any],
                redact(
                    {
                        "task_id": task["task_id"],
                        "status": "PASS",
                        "implementation_evidence": [
                            value.strip() for value in exact_implementation
                        ],
                        "verification_evidence": [
                            value.strip() for value in exact_verification
                        ],
                        "limitations": [value.strip() for value in exact_limitations],
                    }
                ),
            )
            safe_evidence["task_contract_sha256"] = sha256_bytes(
                canonical_json_bytes(contract)
            )
            safe_evidence["evidence_sha256"] = sha256_bytes(
                canonical_json_bytes(safe_evidence)
            )
            normalized.append(safe_evidence)
        return normalized

    def validate_backlog_batch_completion(
        self,
        project_id: str,
        *,
        task_evidence: list[dict[str, Any]],
        confirmation: str,
    ) -> dict[str, Any]:
        self.config(project_id)
        backlog = self._load_backlog(project_id)
        ensure_event_ledger(backlog)
        normalized = self._validate_batch_completion_evidence(
            backlog,
            task_evidence=task_evidence,
            confirmation=confirmation,
        )
        return {
            "status": "PASS",
            "task_count": len(normalized),
            "task_ids": [row["task_id"] for row in normalized],
            "batch_evidence_sha256": sha256_bytes(canonical_json_bytes(normalized)),
            "pointer_mutated": False,
            "candidate_mutated": False,
        }

    def record_backlog_batch_done(
        self,
        project_id: str,
        *,
        session_id: str,
        candidate_id: str,
        task_evidence: list[dict[str, Any]],
        confirmation: str,
    ) -> dict[str, Any]:
        """Atomically append QUEUED -> ACTIVE -> DONE for an exact ordered batch."""

        require(
            bool(session_id.strip()) and bool(candidate_id.strip()),
            "BATCH_DELTA_COMPLETION_BINDING_REQUIRED",
            "Batch completion must bind one governed session and candidate.",
            status="BLOCKED",
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            supplied_ids = [str(row.get("task_id", "")) for row in task_evidence]
            replay = next(
                (
                    row
                    for row in backlog.get("batch_completion_receipts", [])
                    if row.get("session_id") == session_id
                    and row.get("candidate_id") == candidate_id
                    and row.get("task_ids") == supplied_ids
                ),
                None,
            )
            if replay is not None:
                require(
                    confirmation == _BATCH_COMPLETION_CONFIRMATION
                    and len(replay.get("task_evidence", [])) == len(task_evidence),
                    "BATCH_DELTA_REPLAY_INVALID",
                    "A batch replay must use the exact confirmation and task count.",
                    status="MISMATCH",
                )
                for supplied, recorded in zip(
                    task_evidence,
                    replay["task_evidence"],
                    strict=True,
                ):
                    comparable = cast(
                        dict[str, Any],
                        redact(
                            {
                                "task_id": str(supplied.get("task_id", "")),
                                "status": supplied.get("status"),
                                "implementation_evidence": [
                                    str(value).strip()
                                    for value in supplied.get(
                                        "implementation_evidence", []
                                    )
                                ],
                                "verification_evidence": [
                                    str(value).strip()
                                    for value in supplied.get(
                                        "verification_evidence", []
                                    )
                                ],
                                "limitations": [
                                    str(value).strip()
                                    for value in supplied.get("limitations", [])
                                ],
                            }
                        ),
                    )
                    require(
                        all(
                            recorded.get(key) == value
                            for key, value in comparable.items()
                        ),
                        "BATCH_DELTA_REPLAY_EVIDENCE_MISMATCH",
                        "The candidate already binds different batch evidence.",
                        status="MISMATCH",
                        task_id=comparable.get("task_id"),
                    )
                return {"status": "PASS", "idempotent": True, **replay}
            normalized = self._validate_batch_completion_evidence(
                backlog,
                task_evidence=task_evidence,
                confirmation=confirmation,
            )
            batch_basis = {
                "project_id": project_id,
                "session_id": session_id,
                "candidate_id": candidate_id,
                "task_evidence": normalized,
            }
            batch_sha256 = sha256_bytes(canonical_json_bytes(batch_basis))
            receipt_id = f"batchdone_{batch_sha256[:32].lower()}"
            receipts = backlog.setdefault("batch_completion_receipts", [])
            existing = next(
                (row for row in receipts if row.get("receipt_id") == receipt_id),
                None,
            )
            if existing is not None:
                require(
                    existing.get("batch_sha256") == batch_sha256,
                    "BATCH_DELTA_RECEIPT_ID_CONFLICT",
                    "The batch receipt ID already binds different evidence.",
                    status="MISMATCH",
                )
                return {"status": "PASS", "idempotent": True, **existing}
            now = utc_now()
            task_rows = {
                str(task["task_id"]): task for task in backlog.get("tasks", [])
            }
            event_ids: list[str] = []
            for position, evidence in enumerate(normalized, start=1):
                task_id = str(evidence["task_id"])
                task = task_rows[task_id]
                active_event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type="ACTIVATED",
                    to_status="ACTIVE",
                    actor=session_id,
                    event_id=f"{receipt_id}__{position:03d}__active",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "candidate_id": candidate_id,
                        "batch_receipt_id": receipt_id,
                        "evidence_sha256": evidence["evidence_sha256"],
                    },
                )
                task["active_session_id"] = session_id
                task["history"].append(
                    {
                        "event": "BATCH_CLAIMED",
                        "event_id": active_event["event_id"],
                        "session_id": session_id,
                        "candidate_id": candidate_id,
                        "recorded_at": now,
                    }
                )
                done_event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type="TASK_DONE",
                    to_status="DONE",
                    actor=session_id,
                    event_id=f"{receipt_id}__{position:03d}__done",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "candidate_id": candidate_id,
                        "batch_receipt_id": receipt_id,
                        "evidence_sha256": evidence["evidence_sha256"],
                        "task_contract_sha256": evidence["task_contract_sha256"],
                    },
                )
                task["completed_candidate_id"] = candidate_id
                task["batch_completion_receipt_id"] = receipt_id
                task["implementation_evidence_sha256"] = evidence["evidence_sha256"]
                task["history"].append(
                    {
                        "event": "TASK_DONE",
                        "event_id": done_event["event_id"],
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                        "evidence_sha256": evidence["evidence_sha256"],
                        "recorded_at": now,
                    }
                )
                event_ids.extend([active_event["event_id"], done_event["event_id"]])
            receipt = {
                "schema": "evidence-lane.batch-delta-completion.v1",
                "receipt_id": receipt_id,
                "batch_sha256": batch_sha256,
                "project_id": project_id,
                "session_id": session_id,
                "candidate_id": candidate_id,
                "task_count": len(normalized),
                "task_ids": [row["task_id"] for row in normalized],
                "task_evidence": normalized,
                "event_ids": event_ids,
                "completed_at": now,
                "resulting_status": "DONE_PENDING_HIL",
                "pointer_moved": False,
                "candidate_accepted": False,
                "hil_approval_inferred": False,
            }
            receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
            receipts.append(receipt)
            self._persist_backlog(project_id, backlog)
            return {"status": "PASS", "idempotent": False, **receipt}

    def record_backlog_outcome(
        self,
        project_id: str,
        *,
        backlog_task_id: str,
        session_id: str,
        decision: str,
        decided_by: str,
        candidate_id: str,
        accepted_pv: str | None,
        dual_hil_stamp: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            task = next(
                (row for row in backlog["tasks"] if row["task_id"] == backlog_task_id),
                None,
            )
            task = cast(dict[str, Any] | None, task)
            outcome_status = {
                "APPROVE": "ACCEPTED",
                "APPROVE_WITH_DELTA": "DONE",
                "MORE_RESEARCH": "DONE",
                "ROLLBACK": "ROLLED_BACK",
                "REJECT": "REJECTED",
                "FAIL": "FAILED",
            }[decision]
            event_type = (
                "HIL_FOLLOW_UP_REQUESTED"
                if decision in {"APPROVE_WITH_DELTA", "MORE_RESEARCH"}
                else "HIL_OUTCOME"
                if decision == "APPROVE" and dual_hil_stamp is not None
                else f"HIL_{decision}"
            )
            if dual_hil_stamp is not None:
                dual_hil_stamp_body = {
                    key: value
                    for key, value in dual_hil_stamp.items()
                    if key != "receipt_sha256"
                }
                require(
                    decision == "APPROVE"
                    and dual_hil_stamp.get("schema")
                    == "evidence-lane.plan-dual-hil-acceptance-stamp.v1"
                    and dual_hil_stamp.get("status") == "PASS"
                    and dual_hil_stamp.get("plan_task_id") == backlog_task_id
                    and dual_hil_stamp.get("target_pv") == accepted_pv
                    and dual_hil_stamp.get("project_decision") == "APPROVE"
                    and dual_hil_stamp.get("learning_decision") == "APPROVE"
                    and dual_hil_stamp.get("accepted_archive_queried_for_stamp")
                    is False
                    and dual_hil_stamp.get("receipt_sha256")
                    == sha256_bytes(canonical_json_bytes(dual_hil_stamp_body)),
                    "PLAN_DUAL_HIL_STAMP_INVALID",
                    "The Plan HIL stamp must bind both exact approvals for the same PV without archive access.",
                    status="MISMATCH",
                )
            outcome_details = {
                "decision": decision,
                "candidate_id": candidate_id,
                "accepted_pv": accepted_pv,
                **(
                    {"dual_hil_acceptance_stamp": dual_hil_stamp}
                    if dual_hil_stamp is not None
                    else {}
                ),
            }
            outcome_event_id = f"{backlog_task_id}__{candidate_id}__{decision}"
            existing_outcome = next(
                (
                    event
                    for event in backlog["events"]
                    if event["event_id"] == outcome_event_id
                ),
                None,
            )
            if existing_outcome is not None and task is not None:
                append_delta_event(
                    backlog,
                    task_id=backlog_task_id,
                    event_type=event_type,
                    to_status=outcome_status,
                    actor=decided_by,
                    event_id=outcome_event_id,
                    assume_initialized=True,
                    details=outcome_details,
                )
                return task
            require(
                task is not None
                and task.get("status") in {"ACTIVE", "DONE"}
                and task.get("active_session_id") == session_id,
                "BACKLOG_TASK_OUTCOME_STATE_INVALID",
                "Only the active or DONE task in this session may receive a HIL outcome.",
                status="BLOCKED",
            )
            task = cast(dict[str, Any], task)
            if task.get("status") == "ACTIVE":
                append_delta_event(
                    backlog,
                    task_id=backlog_task_id,
                    event_type="TASK_DONE",
                    to_status="DONE",
                    actor=session_id,
                    event_id=f"{backlog_task_id}__{candidate_id}__done",
                    assume_initialized=True,
                    details={
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                        "compatibility_path": "HIL_OUTCOME_AFTER_LEGACY_REFRESH",
                    },
                )
            lifecycle_event = append_delta_event(
                backlog,
                task_id=backlog_task_id,
                event_type=event_type,
                to_status=outcome_status,
                actor=decided_by,
                event_id=outcome_event_id,
                assume_initialized=True,
                details=outcome_details,
            )
            task["accepted_pv"] = accepted_pv if decision == "APPROVE" else None
            if dual_hil_stamp is not None:
                task["dual_hil_acceptance_stamp"] = dual_hil_stamp
            task["history"].append(
                {
                    "event": "HIL_DECISION",
                    "decision": decision,
                    "candidate_id": candidate_id,
                    "accepted_pv": accepted_pv,
                    "dual_hil_acceptance_stamp_sha256": (
                        dual_hil_stamp.get("receipt_sha256")
                        if dual_hil_stamp is not None
                        else None
                    ),
                    "recorded_at": lifecycle_event["recorded_at"],
                }
            )
            self._persist_backlog(project_id, backlog)
            return task

    def record_backlog_batch_outcome(
        self,
        project_id: str,
        *,
        task_ids: list[str],
        session_id: str,
        decision: str,
        decided_by: str,
        candidate_id: str,
        accepted_pv: str | None,
        dual_hil_stamp: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply one HIL outcome to the exact ordered DONE batch in one write."""

        outcome_status = {
            "APPROVE": "ACCEPTED",
            "APPROVE_WITH_DELTA": "DONE",
            "MORE_RESEARCH": "DONE",
            "ROLLBACK": "ROLLED_BACK",
            "REJECT": "REJECTED",
            "FAIL": "FAILED",
        }.get(decision)
        require(
            outcome_status is not None and bool(task_ids),
            "BATCH_DELTA_HIL_OUTCOME_INVALID",
            "Batch Delta outcome requires one supported HIL decision and task set.",
            status="BLOCKED",
            decision=decision,
        )
        if dual_hil_stamp is not None:
            dual_hil_stamp_body = {
                key: value
                for key, value in dual_hil_stamp.items()
                if key != "receipt_sha256"
            }
            require(
                decision == "APPROVE"
                and dual_hil_stamp.get("schema")
                == "evidence-lane.plan-dual-hil-acceptance-stamp.v1"
                and dual_hil_stamp.get("status") == "PASS"
                and dual_hil_stamp.get("target_pv") == accepted_pv
                and dual_hil_stamp.get("project_decision") == "APPROVE"
                and dual_hil_stamp.get("learning_decision") == "APPROVE"
                and dual_hil_stamp.get("accepted_archive_queried_for_stamp")
                is False
                and dual_hil_stamp.get("receipt_sha256")
                == sha256_bytes(canonical_json_bytes(dual_hil_stamp_body)),
                "PLAN_BATCH_DUAL_HIL_STAMP_INVALID",
                "The batch Plan HIL stamp must bind both exact approvals for the same PV without archive access.",
                status="MISMATCH",
            )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks = {str(row["task_id"]): row for row in backlog.get("tasks", [])}
            require(
                len(task_ids) == len(set(task_ids))
                and all(
                    task_id in tasks
                    and tasks[task_id].get("status") == "DONE"
                    and tasks[task_id].get("completed_candidate_id") == candidate_id
                    for task_id in task_ids
                ),
                "BATCH_DELTA_HIL_TASK_SET_INVALID",
                "The HIL outcome must bind the exact DONE batch for this candidate.",
                status="MISMATCH",
                candidate_id=candidate_id,
            )
            basis = {
                "project_id": project_id,
                "task_ids": task_ids,
                "session_id": session_id,
                "decision": decision,
                "decided_by": decided_by,
                "candidate_id": candidate_id,
                "accepted_pv": accepted_pv,
                "dual_hil_stamp": dual_hil_stamp,
            }
            basis_sha256 = sha256_bytes(canonical_json_bytes(basis))
            receipt_id = f"batchoutcome_{basis_sha256[:32].lower()}"
            receipts = backlog.setdefault("batch_outcome_receipts", [])
            existing = next(
                (row for row in receipts if row.get("receipt_id") == receipt_id),
                None,
            )
            if existing is not None:
                return {"status": "PASS", "idempotent": True, **existing}
            now = utc_now()
            event_ids: list[str] = []
            for position, task_id in enumerate(task_ids, start=1):
                event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type=(
                        "HIL_FOLLOW_UP_REQUESTED"
                        if outcome_status == "DONE"
                        else "HIL_OUTCOME"
                    ),
                    to_status=cast(str, outcome_status),
                    actor=decided_by,
                    event_id=f"{receipt_id}__{position:03d}",
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": accepted_pv,
                        "batch_outcome_receipt_id": receipt_id,
                        **(
                            {"dual_hil_acceptance_stamp": dual_hil_stamp}
                            if dual_hil_stamp is not None
                            else {}
                        ),
                    },
                )
                task = tasks[task_id]
                task["accepted_pv"] = accepted_pv if decision == "APPROVE" else None
                if dual_hil_stamp is not None:
                    task["dual_hil_acceptance_stamp"] = {
                        **dual_hil_stamp,
                        "batch_task_id": task_id,
                    }
                task["history"].append(
                    {
                        "event": "HIL_DECISION",
                        "event_id": event["event_id"],
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": task["accepted_pv"],
                        "dual_hil_acceptance_stamp_sha256": (
                            dual_hil_stamp.get("receipt_sha256")
                            if dual_hil_stamp is not None
                            else None
                        ),
                        "recorded_at": now,
                    }
                )
                event_ids.append(event["event_id"])
            receipt = {
                "schema": "evidence-lane.batch-delta-outcome.v1",
                "receipt_id": receipt_id,
                "basis_sha256": basis_sha256,
                "project_id": project_id,
                "session_id": session_id,
                "candidate_id": candidate_id,
                "task_ids": list(task_ids),
                "task_count": len(task_ids),
                "decision": decision,
                "resulting_status": outcome_status,
                "accepted_pv": accepted_pv if decision == "APPROVE" else None,
                "event_ids": event_ids,
                "recorded_at": now,
            }
            receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
            receipts.append(receipt)
            self._persist_backlog(project_id, backlog)
            return {"status": "PASS", "idempotent": False, **receipt}

    def transition_backlog_task(
        self,
        project_id: str,
        *,
        task_id: str,
        transition_name: str,
        decided_by: str,
        reason_sha256: str,
        replacement_task_id: str | None = None,
        event_id: str | None = None,
        correction_of_event_id: str | None = None,
        expected_backlog_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Apply one atomic transition or repair one proven lifecycle defect."""

        transition = transition_name.strip().upper()
        require(
            transition
            in {
                "DROP",
                "SUPERSEDE",
                "CORRECT_DROP",
                "CORRECT_PREAPPROVAL_DONE",
            },
            "DELTA_EXPLICIT_TRANSITION_INVALID",
            "Only DROP, SUPERSEDE, or a sealed lifecycle correction may be requested.",
            status="BLOCKED",
            transition=transition,
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            current_backlog_sha256 = sha256_bytes(canonical_json_bytes(backlog))
            candidate = copy.deepcopy(backlog)
            tasks = {str(row["task_id"]): row for row in candidate["tasks"]}
            require(
                task_id in tasks,
                "DELTA_TASK_NOT_FOUND",
                "The requested Delta does not exist.",
                status="MISMATCH",
                task_id=task_id,
            )
            task = tasks[task_id]
            if transition == "CORRECT_PREAPPROVAL_DONE":
                exact_correction_event_id = str(correction_of_event_id or "").strip()
                exact_expected_sha256 = (
                    str(expected_backlog_sha256 or "").strip().upper()
                )
                require(
                    bool(exact_correction_event_id)
                    and re.fullmatch(r"[0-9A-F]{64}", exact_expected_sha256)
                    is not None,
                    "DELTA_PREAPPROVAL_DONE_CORRECTION_AUTHORITY_MISMATCH",
                    "Preapproval DONE correction requires the exact event and backlog hash.",
                    status="MISMATCH",
                )
                correction_event = next(
                    (
                        row
                        for row in candidate.get("events", [])
                        if row.get("event_type")
                        == "HIL_PREAPPROVAL_DONE_CORRECTION_RESTORED"
                        and row.get("details", {}).get("correction_of_event_id")
                        == exact_correction_event_id
                    ),
                    None,
                )
                session_payload: dict[str, Any] | None = None
                session_path: Path | None = None
                if correction_event is not None:
                    correction_details = cast(dict[str, Any], correction_event).get(
                        "details", {}
                    )
                    require(
                        task.get("status") == "ACTIVE"
                        and task.get("last_event_id")
                        == correction_event.get("event_id")
                        and correction_details.get("before_backlog_sha256")
                        == exact_expected_sha256,
                        "DELTA_PREAPPROVAL_DONE_CORRECTION_REPLAY_MISMATCH",
                        "The existing correction does not bind the requested prior state.",
                        status="MISMATCH",
                    )
                    session_id = str(correction_details.get("session_id") or "")
                    session_path = (
                        self.project_root(project_id)
                        / "sessions"
                        / f"{session_id}.json"
                    )
                    session_payload = json.loads(
                        session_path.read_text(encoding="utf-8")
                    )
                    session_payload.setdefault("metadata", {})[
                        "active_backlog_task_status"
                    ] = "ACTIVE"
                    atomic_write_json(session_path, session_payload)
                    return {
                        "status": "PASS",
                        "idempotent": True,
                        "task": task,
                        "event": correction_event,
                        "backlog": self.backlog_status(project_id),
                    }
                require(
                    current_backlog_sha256 == exact_expected_sha256,
                    "DELTA_PREAPPROVAL_DONE_CORRECTION_BACKLOG_MISMATCH",
                    "The live backlog no longer matches the sealed correction basis.",
                    status="MISMATCH",
                    observed_backlog_sha256=current_backlog_sha256,
                )
                prior_event = next(
                    (
                        row
                        for row in candidate.get("events", [])
                        if row.get("event_id") == exact_correction_event_id
                    ),
                    None,
                )
                require(
                    isinstance(prior_event, dict)
                    and prior_event.get("task_id") == task_id
                    and prior_event.get("event_type") == "TASK_DONE"
                    and prior_event.get("from_status") == "ACTIVE"
                    and prior_event.get("to_status") == "DONE"
                    and task.get("status") == "DONE"
                    and task.get("last_event_id") == exact_correction_event_id,
                    "DELTA_PREAPPROVAL_DONE_CORRECTION_EVENT_MISMATCH",
                    "The sealed event is not the task's exact premature ACTIVE-to-DONE transition.",
                    status="MISMATCH",
                )
                prior_event = cast(dict[str, Any], prior_event)
                prior_details = cast(dict[str, Any], prior_event.get("details") or {})
                candidate_id = str(prior_details.get("candidate_id") or "")
                session_id = str(prior_details.get("session_id") or "")
                require(
                    bool(candidate_id) and bool(session_id),
                    "DELTA_PREAPPROVAL_DONE_CORRECTION_EVENT_UNBOUND",
                    "The premature event lacks its exact candidate or session binding.",
                    status="MISMATCH",
                )
                session_path = (
                    self.project_root(project_id) / "sessions" / f"{session_id}.json"
                )
                session_payload = json.loads(session_path.read_text(encoding="utf-8"))
                session_metadata = cast(
                    dict[str, Any], session_payload.get("metadata") or {}
                )
                overlay_path = (
                    self.project_root(project_id)
                    / "receipts"
                    / "candidate-overlays"
                    / f"{candidate_id}.json"
                )
                overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
                pointer = self.pointer(project_id)
                require(
                    session_payload.get("state") in {"PV1_CANDIDATE", "PVN1_CANDIDATE"}
                    and session_payload.get("candidate_id") == candidate_id
                    and session_metadata.get("active_backlog_task_id") == task_id
                    and session_metadata.get("active_backlog_task_status") == "DONE"
                    and session_payload.get("accepted_pointer_generation")
                    == session_metadata.get("candidate_pointer_generation")
                    == pointer.generation
                    and overlay.get("candidate_id") == candidate_id
                    and overlay.get("pointer_generation") == pointer.generation
                    and overlay.get("pointer_moved") is False
                    and overlay.get("hil_inferred") is False
                    and overlay.get("accepted_artifact_created") is False,
                    "DELTA_PREAPPROVAL_DONE_CORRECTION_CANDIDATE_MISMATCH",
                    "The pending proposal no longer matches the exact preapproval correction boundary.",
                    status="MISMATCH",
                )
                require(
                    not any(
                        row.get("task_id") == task_id
                        and str(row.get("event_type") or "").startswith("HIL_")
                        and (row.get("details") or {}).get("candidate_id")
                        == candidate_id
                        for row in candidate.get("events", [])
                    ),
                    "DELTA_PREAPPROVAL_DONE_CORRECTION_DECISION_EXISTS",
                    "A HIL outcome already exists for this candidate.",
                    status="BLOCKED",
                )
                correction_details = {
                    "reason_sha256": reason_sha256,
                    "history_preserved": True,
                    "correction_of_event_id": exact_correction_event_id,
                    "correction_of_event_sha256": prior_event.get("event_sha256"),
                    "before_backlog_sha256": current_backlog_sha256,
                    "candidate_id": candidate_id,
                    "session_id": session_id,
                    "pointer_generation": pointer.generation,
                    "approval_inferred": False,
                }
                lifecycle_event = append_delta_event(
                    candidate,
                    task_id=task_id,
                    event_type="HIL_PREAPPROVAL_DONE_CORRECTION_RESTORED",
                    to_status="ACTIVE",
                    actor=decided_by,
                    event_id=event_id,
                    assume_initialized=True,
                    details=correction_details,
                )
                task.pop("completed_candidate_id", None)
                task["active_session_id"] = session_id
                task["history"].append(
                    {
                        "event": "HIL_PREAPPROVAL_DONE_CORRECTION_RESTORED",
                        "event_id": lifecycle_event["event_id"],
                        "recorded_at": lifecycle_event["recorded_at"],
                        **correction_details,
                    }
                )
                after_backlog_sha256 = sha256_bytes(canonical_json_bytes(candidate))
                self._persist_backlog(project_id, candidate)
                session_payload.setdefault("metadata", {})[
                    "active_backlog_task_status"
                ] = "ACTIVE"
                atomic_write_json(session_path, session_payload)
                return {
                    "status": "PASS",
                    "idempotent": False,
                    "task": task,
                    "event": lifecycle_event,
                    "backlog": self.backlog_status(project_id),
                    "transition_correction_receipt": {
                        "schema": "evidence-lane.preapproval-done-correction-receipt.v1",
                        "status": "PASS",
                        "task_id": task_id,
                        "candidate_id": candidate_id,
                        "session_id": session_id,
                        "correction_of_event_id": exact_correction_event_id,
                        "before_backlog_sha256": current_backlog_sha256,
                        "after_backlog_sha256": after_backlog_sha256,
                        "history_preserved": True,
                        "pointer_moved": False,
                        "candidate_created": False,
                        "hil_invoked": False,
                        "approval_inferred": False,
                        "goal_mutated": False,
                        "git_executed": False,
                    },
                }
            if transition == "CORRECT_DROP":
                exact_correction_event_id = str(correction_of_event_id or "").strip()
                exact_expected_sha256 = (
                    str(expected_backlog_sha256 or "").strip().upper()
                )
                require(
                    bool(exact_correction_event_id)
                    and re.fullmatch(r"[0-9A-F]{64}", exact_expected_sha256) is not None
                    and current_backlog_sha256 == exact_expected_sha256,
                    "DELTA_DROP_CORRECTION_AUTHORITY_MISMATCH",
                    "DROP correction requires the exact prior event and live backlog hash.",
                    status="MISMATCH",
                    correction_of_event_id=exact_correction_event_id or None,
                    observed_backlog_sha256=current_backlog_sha256,
                )
                prior_event = next(
                    (
                        row
                        for row in candidate.get("events", [])
                        if row.get("event_id") == exact_correction_event_id
                    ),
                    None,
                )
                require(
                    isinstance(prior_event, dict)
                    and prior_event.get("task_id") == task_id
                    and prior_event.get("event_type") == "DROPPED"
                    and prior_event.get("from_status") == "QUEUED"
                    and prior_event.get("to_status") == "DROPPED"
                    and task.get("status") == "DROPPED"
                    and task.get("last_event_id") == exact_correction_event_id,
                    "DELTA_DROP_CORRECTION_EVENT_MISMATCH",
                    "The sealed event is not the task's exact partial QUEUED-to-DROPPED transition.",
                    status="MISMATCH",
                    task_id=task_id,
                    correction_of_event_id=exact_correction_event_id,
                )
                prior_event = cast(dict[str, Any], prior_event)
                dangling_dependents = sorted(
                    str(row["task_id"])
                    for row in candidate["tasks"]
                    if str(row.get("status")) in _GOAL_STATUS_BY_LIFECYCLE
                    and task_id
                    in {str(value) for value in row.get("dependencies") or []}
                )
                require(
                    bool(dangling_dependents),
                    "DELTA_DROP_CORRECTION_NOT_REQUIRED",
                    "DROP correction is allowed only for a persisted dependency failure.",
                    status="BLOCKED",
                    task_id=task_id,
                )
                correction_details = {
                    "reason_sha256": reason_sha256,
                    "history_preserved": True,
                    "correction_of_event_id": exact_correction_event_id,
                    "correction_of_event_sha256": prior_event.get("event_sha256"),
                    "dangling_dependent_task_ids": dangling_dependents,
                    "before_backlog_sha256": current_backlog_sha256,
                }
                lifecycle_event = append_delta_event(
                    candidate,
                    task_id=task_id,
                    event_type="PLAN_TRANSITION_CORRECTION_RESTORED",
                    to_status="QUEUED",
                    actor=decided_by,
                    event_id=event_id,
                    assume_initialized=True,
                    details=correction_details,
                )
                task["history"].append(
                    {
                        "event": "PLAN_TRANSITION_CORRECTION_RESTORED",
                        "event_id": lifecycle_event["event_id"],
                        "recorded_at": lifecycle_event["recorded_at"],
                        **correction_details,
                    }
                )
                candidate_status = self.backlog_status(
                    project_id,
                    _loaded_backlog=copy.deepcopy(candidate),
                )
                after_backlog_sha256 = sha256_bytes(canonical_json_bytes(candidate))
                self._persist_backlog(project_id, candidate)
                return {
                    "status": "PASS",
                    "task": task,
                    "event": lifecycle_event,
                    "backlog": candidate_status,
                    "transition_correction_receipt": {
                        "schema": (
                            "evidence-lane.plan-transition-correction-receipt.v1"
                        ),
                        "status": "PASS",
                        "task_id": task_id,
                        "correction_of_event_id": exact_correction_event_id,
                        "before_backlog_sha256": current_backlog_sha256,
                        "after_backlog_sha256": after_backlog_sha256,
                        "dangling_dependent_task_ids": dangling_dependents,
                        "history_preserved": True,
                        "pointer_moved": False,
                        "candidate_created": False,
                        "hil_invoked": False,
                        "goal_mutated": False,
                        "git_executed": False,
                    },
                }

            target_status = "DROPPED" if transition == "DROP" else "SUPERSEDED"
            transition_details: dict[str, Any] = {
                "reason_sha256": reason_sha256,
                "history_preserved": True,
            }
            if transition == "SUPERSEDE":
                exact_replacement = str(replacement_task_id or "").strip()
                require(
                    exact_replacement in tasks
                    and exact_replacement != task_id
                    and tasks[exact_replacement].get("status") == "QUEUED",
                    "DELTA_REPLACEMENT_TASK_INVALID",
                    "SUPERSEDE requires one different queued replacement Delta.",
                    status="BLOCKED",
                    replacement_task_id=exact_replacement or None,
                )
                transition_details["replacement_task_id"] = exact_replacement
                task["superseded_by_task_id"] = exact_replacement
                tasks[exact_replacement]["supersedes_task_id"] = task_id
            lifecycle_event = append_delta_event(
                candidate,
                task_id=task_id,
                event_type=target_status,
                to_status=target_status,
                actor=decided_by,
                event_id=event_id,
                assume_initialized=True,
                details=transition_details,
            )
            if not any(
                row.get("event_id") == lifecycle_event["event_id"]
                for row in task["history"]
            ):
                task["history"].append(
                    {
                        "event": target_status,
                        "event_id": lifecycle_event["event_id"],
                        "recorded_at": lifecycle_event["recorded_at"],
                        **transition_details,
                    }
                )
            # Validate the complete candidate before making any lifecycle
            # transition durable.  This prevents a failed dependency check
            # from leaving a partially mutated Plan authority behind.
            candidate_status = self.backlog_status(
                project_id,
                _loaded_backlog=copy.deepcopy(candidate),
            )
            self._persist_backlog(project_id, candidate)
            return {
                "status": "PASS",
                "task": task,
                "event": lifecycle_event,
                "backlog": candidate_status,
            }

    def record_planning_mode(
        self,
        project_id: str,
        *,
        source_event_id: str,
        session_id: str,
        request_sha256: str,
        selected_mode_ids: list[str],
        mode_intersection: str,
        canonical_lanes: list[str],
        lifecycle_state: str,
        pointer_generation: int,
    ) -> dict[str, Any]:
        """Append a Planning-mode control-plane event and rebuild its projection."""

        require(
            "PL" in selected_mode_ids and "plan" in canonical_lanes,
            "PLANNING_MODE_PROJECTION_NOT_APPLICABLE",
            "The Plan runtime projection requires a selected Planning mode.",
            status="BLOCKED",
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            event = append_planning_mode_event(
                backlog,
                source_event_id=source_event_id,
                session_id=session_id,
                request_sha256=request_sha256,
                selected_mode_ids=selected_mode_ids,
                mode_intersection=mode_intersection,
                canonical_lanes=canonical_lanes,
                lifecycle_state=lifecycle_state,
                pointer_generation=pointer_generation,
            )
            self._persist_backlog(project_id, backlog)
            return {
                "status": "PASS",
                "append_status": "APPENDED",
                "event": event,
                "projection": plan_runtime_status(
                    self._plan_runtime_path(project_id),
                    backlog,
                ),
                "canonical_plan_sector_mutated": False,
                "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
            }

    def record_task_formula(
        self,
        project_id: str,
        *,
        task_id: str,
        event_kind: str,
        source_event_id: str,
        session_id: str,
        formula: dict[str, Any],
        actor: str,
        prior_formula_sha256: str | None = None,
        changed_terms: dict[str, Any] | None = None,
        cause_evidence_locator: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        """Append a task-linked formula event and rebuild canonical Plan SQLite."""

        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            event = append_task_formula_event(
                backlog,
                task_id=task_id,
                event_kind=event_kind,
                source_event_id=source_event_id,
                session_id=session_id,
                formula=formula,
                actor=actor,
                prior_formula_sha256=prior_formula_sha256,
                changed_terms=changed_terms,
                cause_evidence_locator=cause_evidence_locator,
                event_id=event_id,
            )
            self._persist_backlog(project_id, backlog)
            return {
                "status": "PASS",
                "append_status": "APPENDED",
                "event": event,
                "projection": plan_runtime_status(
                    self._plan_runtime_path(project_id), backlog
                ),
                "new_executable_plan_row_created": False,
                "candidate_created": False,
                "pointer_moved": False,
                "hil_inferred": False,
            }

    def config(self, project_id: str) -> ProjectConfig:
        path = self.project_root(project_id) / "project.json"
        require(
            path.is_file(),
            "PROJECT_NOT_REGISTERED",
            "The project is not registered in this Evidence Lane store.",
            status="MISMATCH",
            project_id=project_id,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        fields = {
            key: value
            for key, value in payload.items()
            if key in ProjectConfig.__dataclass_fields__
        }
        return ProjectConfig(**fields)

    def bootstrap_pv0_baseline(
        self,
        project_id: str,
        *,
        source_intake_event_id: str,
        working_refresh_receipt_sha256: str,
        bootstrapped_by: str,
    ) -> dict[str, Any]:
        """Bind the first live-root sector projection as PV0 without HIL.

        PV0 is the starting authority, not an accepted HIL artifact.  This
        operation therefore never reads or writes ``accepted/`` and never
        creates a candidate.  Its manifest binding is the live sector bundle
        plus the exact Source Intake refresh that built it.
        """

        root = self.project_root(project_id)
        require(
            self.uses_external_project_authority(project_id),
            "PV0_BOOTSTRAP_LIVE_ROOT_REQUIRED",
            "The current PV0 bootstrap route requires live-root project authority.",
            status="BLOCKED",
        )
        exact_actor = str(bootstrapped_by).strip()
        exact_event_id = str(source_intake_event_id).strip()
        exact_refresh_sha256 = str(working_refresh_receipt_sha256).strip().upper()
        require(
            bool(exact_actor)
            and bool(exact_event_id)
            and re.fullmatch(r"[A-F0-9]{64}", exact_refresh_sha256) is not None,
            "PV0_BOOTSTRAP_SOURCE_INTAKE_BINDING_REQUIRED",
            "PV0 requires the exact Source Intake event and WORKING refresh receipt.",
            status="BLOCKED",
        )
        sectors_manifest = root / "sectors" / "manifest.json"
        require(
            sectors_manifest.is_file(),
            "PV0_BOOTSTRAP_SECTOR_MANIFEST_MISSING",
            "Initial Build must materialize the fired sector lanes through Source Intake before PV0 can be bound.",
            status="MISMATCH",
        )
        sector_manifest_payload = json.loads(
            sectors_manifest.read_text(encoding="utf-8")
        )
        emitted_lane_ids = list(sector_manifest_payload.get("emitted_lane_ids") or [])
        require(
            int(sector_manifest_payload.get("canonical_lane_count") or 0)
            == len(CANONICAL_LANE_IDS)
            and emitted_lane_ids
            == [
                lane_id
                for lane_id in CANONICAL_LANE_IDS
                if lane_id in emitted_lane_ids
            ],
            "PV0_BOOTSTRAP_SECTOR_REGISTRY_MISMATCH",
            "PV0 requires one ordered fired-lane projection from the current registry.",
            status="MISMATCH",
        )
        sector_manifest_sha256 = sha256_file(sectors_manifest)
        receipt_body = {
            "schema": "evidence-lane.pv0-live-root-bootstrap.v1",
            "status": "PASS",
            "project_id": project_id,
            "baseline_pv": "PV0",
            "pointer_generation": 0,
            "source_intake_event_id": exact_event_id,
            "working_refresh_receipt_sha256": exact_refresh_sha256,
            "sector_manifest_sha256": sector_manifest_sha256,
            "canonical_lane_count": len(CANONICAL_LANE_IDS),
            "emitted_lane_ids": emitted_lane_ids,
            "unfired_lane_ids": [
                lane_id
                for lane_id in CANONICAL_LANE_IDS
                if lane_id not in emitted_lane_ids
            ],
            "unfired_lane_directories_created": False,
            "fired_lane_directories_reused_when_unchanged": True,
            "bootstrapped_by": exact_actor,
            "initial_authority": "LIVE_PROJECT_ROOT_SECTORS",
            "human_hil_required": False,
            "candidate_created": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "accepted_archive_written": False,
            "project_overlay_refreshed": False,
            "learning_hil_invoked": False,
            "pointer_moved_from_accepted_hil": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
        receipt = {**receipt_body, "receipt_sha256": receipt_sha256}
        receipt_path = root / "receipts" / "pv0-live-root-bootstrap.json"
        pointer_path = root / "active_pointer.json"
        config = self.config(project_id)

        def ensure_layout() -> dict[str, Any]:
            layout_path = root / "project_authority.json"
            if layout_path.is_file():
                layout = json.loads(layout_path.read_text(encoding="utf-8"))
                accepted_pointer = dict(layout.get("accepted_pointer") or {})
                require(
                    layout.get("schema") == "evidence-lane.project-authority-layout.v1"
                    and int(layout.get("canonical_lane_count") or 0)
                    == len(CANONICAL_LANE_IDS)
                    and int(layout.get("materialized_sector_directory_count") or 0)
                    == len(emitted_lane_ids)
                    and layout.get("materialized_sector_lane_ids")
                    == emitted_lane_ids
                    and accepted_pointer.get("accepted_pv") == "PV0"
                    and accepted_pointer.get("generation") == 0
                    and accepted_pointer.get("accepted_manifest_sha256")
                    == receipt_sha256,
                    "PV0_PROJECT_AUTHORITY_LAYOUT_MISMATCH",
                    "The existing Project/PV authority layout does not match the PV0 bootstrap identity.",
                    status="MISMATCH",
                    project_id=project_id,
                )
                status = self.project_authority_status(project_id)
                require(
                    status.get("layout_materialized") is True,
                    "PV0_PROJECT_AUTHORITY_LAYOUT_INCOMPLETE",
                    "The PV0 Project/PV authority layout is incomplete.",
                    status="MISMATCH",
                    project_id=project_id,
                )
                return {**status, "state": "PV0_LAYOUT_IDEMPOTENT_REUSE"}
            return materialize_project_authority_layout(
                root,
                published_root=root,
                control_root=self.root,
                project_id=project_id,
                repository_path=config.repository_path,
                accepted_pv="PV0",
                pointer_generation=0,
                accepted_manifest_sha256=receipt_sha256,
                legacy_history_root=None,
            )

        with self._lock(project_id):
            before = self.pointer(project_id)
            if before.accepted_pv == "PV0":
                require(
                    receipt_path.is_file()
                    and json.loads(receipt_path.read_text(encoding="utf-8"))
                    == receipt
                    and before.generation == 0
                    and before.accepted_manifest_sha256 == receipt_sha256,
                    "PV0_BOOTSTRAP_REPLAY_MISMATCH",
                    "An existing PV0 pointer does not match the exact bootstrap receipt.",
                    status="MISMATCH",
                )
                layout = ensure_layout()
                return {
                    "status": "PASS",
                    "state": "PV0_BASELINE_IDEMPOTENT_REUSE",
                    "pointer": before.as_dict(),
                    "receipt": receipt,
                    "receipt_path": str(receipt_path),
                    "project_authority_layout": layout,
                }
            require(
                before.accepted_pv is None
                and before.accepted_manifest_sha256 is None
                and before.generation == 0,
                "PV0_BOOTSTRAP_POINTER_NOT_EMPTY",
                "PV0 may be bound only once before any accepted Project HIL.",
                status="BLOCKED",
                pointer=before.as_dict(),
            )
            if receipt_path.exists():
                require(
                    json.loads(receipt_path.read_text(encoding="utf-8")) == receipt,
                    "PV0_BOOTSTRAP_RECEIPT_CONFLICT",
                    "The deterministic PV0 bootstrap receipt already has different evidence.",
                    status="MISMATCH",
                )
            else:
                atomic_write_json(receipt_path, receipt)
            layout = ensure_layout()
            after = ActivePointer(
                project_id=project_id,
                accepted_pv="PV0",
                accepted_manifest_sha256=receipt_sha256,
                generation=0,
                prior_generation=None,
                updated_at=utc_now(),
            )
            atomic_write_json(
                pointer_path,
                {"schema": POINTER_SCHEMA, **after.as_dict()},
            )
        return {
            "status": "PASS",
            "state": "PV0_BASELINE_ESTABLISHED",
            "pointer": self.pointer(project_id).as_dict(),
            "receipt": receipt,
            "receipt_path": str(receipt_path),
            "project_authority_layout": layout,
        }

    def pointer(self, project_id: str) -> ActivePointer:
        path = self.project_root(project_id) / "active_pointer.json"
        require(
            path.is_file(),
            "ACTIVE_POINTER_NOT_FOUND",
            "The project has no active pointer.",
            status="MISMATCH",
            project_id=project_id,
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        require(
            payload.get("schema") == POINTER_SCHEMA,
            "ACTIVE_POINTER_SCHEMA_MISMATCH",
            "The project pointer schema is unsupported.",
            status="MISMATCH",
        )
        return ActivePointer(
            **{
                key: value
                for key, value in payload.items()
                if key in ActivePointer.__dataclass_fields__
            }
        )

    def candidate_path(self, project_id: str, candidate_id: str) -> Path:
        require(
            candidate_id.startswith("PV")
            and "_CANDIDATE__RUN_" in candidate_id
            and all(character in _PROJECT_ID_CHARS for character in candidate_id),
            "CANDIDATE_ID_INVALID",
            "The candidate ID does not match the governed naming scheme.",
            status="BLOCKED",
            candidate_id=candidate_id,
        )
        root = self.project_root(project_id)
        require(
            root == self._legacy_project_root(project_id),
            "PROJECT_CANDIDATE_DIRECTORY_FORBIDDEN",
            "External project authority uses the live root as its sole working overlay; no candidates directory is permitted.",
            status="BLOCKED",
            project_id=project_id,
            candidate_id=candidate_id,
            project_root=str(root),
        )
        target = (root / "candidates" / candidate_id).resolve()
        target.relative_to(root)
        return target

    def _candidate_overlay_receipt_path(
        self, project_id: str, candidate_id: str
    ) -> Path:
        root = self.project_root(project_id)
        target = (
            root / "receipts" / "candidate-overlays" / f"{candidate_id}.json"
        ).resolve()
        target.relative_to(root)
        return target

    def candidate_runtime_path(self, project_id: str, candidate_id: str) -> Path:
        """Return the exact acceptance-check root without inventing candidate bytes."""

        root = self.project_root(project_id)
        if root != self._legacy_project_root(project_id):
            receipt = self._candidate_overlay_receipt_path(project_id, candidate_id)
            require(
                receipt.is_file(),
                "PROJECT_CANDIDATE_OVERLAY_NOT_FOUND",
                "The live-root candidate overlay receipt does not exist.",
                status="MISMATCH",
                candidate_id=candidate_id,
            )
            return root
        return self.candidate_path(project_id, candidate_id)

    def candidate_validation(
        self,
        project_id: str,
        candidate_id: str,
        *,
        require_promotable: bool = True,
    ) -> dict[str, Any]:
        root = self.project_root(project_id)
        if root == self._legacy_project_root(project_id):
            return validate_pv_package(
                self.candidate_path(project_id, candidate_id),
                require_promotable=require_promotable,
            )
        receipt_path = self._candidate_overlay_receipt_path(project_id, candidate_id)
        require(
            receipt_path.is_file(),
            "PROJECT_CANDIDATE_OVERLAY_NOT_FOUND",
            "The live-root candidate overlay receipt does not exist.",
            status="MISMATCH",
            candidate_id=candidate_id,
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        claimed = str(receipt.get("receipt_sha256") or "")
        body = dict(receipt)
        body.pop("receipt_sha256", None)
        receipt_schema = receipt.get("schema")
        proposal_identity_valid = bool(
            receipt_schema == "evidence-lane.project-hil-proposal.v1"
            and receipt.get("proposal_id") == candidate_id
            and receipt.get("candidate_id") == candidate_id
            and receipt.get("full_candidate_package_built") is False
        )
        require(
            receipt_schema
            in {
                "evidence-lane.project-candidate-overlay.v1",
                "evidence-lane.project-hil-proposal.v1",
            }
            and receipt.get("project_id") == project_id
            and receipt.get("candidate_id") == candidate_id
            and (
                receipt_schema == "evidence-lane.project-candidate-overlay.v1"
                or proposal_identity_valid
            )
            and receipt.get("state") == "SEALED_AFTER_LIFECYCLE_APPEND"
            and claimed == sha256_bytes(canonical_json_bytes(body)),
            "PROJECT_CANDIDATE_OVERLAY_RECEIPT_MISMATCH",
            "The live-root candidate overlay receipt failed its exact identity checks.",
            status="MISMATCH",
            candidate_id=candidate_id,
        )
        current = working_overlay_manifest(root, project_id=project_id)
        expected_rows = {
            str(row.get("path")): row
            for row in receipt.get("working_members") or []
            if isinstance(row, dict)
        }
        current_rows = {
            str(row.get("path")): row
            for row in current.get("members") or []
            if isinstance(row, dict)
        }
        changed_paths = sorted(
            path
            for path in expected_rows.keys() & current_rows.keys()
            if expected_rows[path] != current_rows[path]
        )
        added_paths = sorted(current_rows.keys() - expected_rows.keys())
        removed_paths = sorted(expected_rows.keys() - current_rows.keys())
        validation = dict(receipt.get("validation") or {})
        pointer = self.pointer(project_id)
        operational_sector_changed_paths = [
            path
            for path in changed_paths
            if path in {"sectors/manifest.json", "sectors/SHA256SUMS.json"}
            or (
                path.startswith("sectors/")
                and is_working_sector_operational_member(path.removeprefix("sectors/"))
            )
        ]
        post_promotion_receipts_and_operational_drift_only = bool(
            added_paths
            and not removed_paths
            and len(operational_sector_changed_paths) == len(changed_paths)
            and all(
                path.startswith("receipts/")
                and path.count("/") == 1
                and path.endswith(".json")
                for path in added_paths
            )
            and pointer.accepted_pv == validation.get("proposed_pv")
            and pointer.generation == int(receipt.get("pointer_generation") or -1) + 1
        )
        require(
            current["working_identity_sha256"] == receipt.get("working_identity_sha256")
            or post_promotion_receipts_and_operational_drift_only,
            "PROJECT_CANDIDATE_OVERLAY_STALE",
            "The live project root changed after the candidate overlay was sealed.",
            status="STALE",
            candidate_id=candidate_id,
            expected=receipt.get("working_identity_sha256"),
            actual=current["working_identity_sha256"],
            added_paths=added_paths,
            removed_paths=removed_paths,
            changed_paths=changed_paths,
        )
        if post_promotion_receipts_and_operational_drift_only:
            validation["post_promotion_lifecycle_receipts"] = added_paths
            validation["post_promotion_operational_sector_members"] = (
                operational_sector_changed_paths
            )
            validation["candidate_content_identity_unchanged"] = True
        require(
            not require_promotable or validation.get("promotable") is True,
            "PROJECT_CANDIDATE_OVERLAY_NOT_PROMOTABLE",
            "The live-root candidate overlay did not pass the candidate build gates.",
            status="FAIL",
            candidate_id=candidate_id,
        )
        return validation

    def candidate_preservation_identity(
        self,
        project_id: str,
        candidate_id: str,
    ) -> dict[str, Any]:
        """Verify immutable candidate identity without requiring live-root equality.

        Direct same-worktree State Travel preserves an already sealed candidate
        while the live worktree continues independently.  Requiring the current
        live-root overlay to equal that earlier candidate is the obsolete gate
        that rejected valid pending-HIL continuity.
        """

        root = self.project_root(project_id)
        if root == self._legacy_project_root(project_id):
            validation = validate_pv_package(
                self.candidate_path(project_id, candidate_id),
                require_promotable=False,
            )
            return {
                **validation,
                "status": "PASS",
                "candidate_id": candidate_id,
                "preservation_scope": "IMMUTABLE_CANDIDATE_PACKAGE_IDENTITY",
                "current_live_root_equality_required": False,
            }
        receipt_path = self._candidate_overlay_receipt_path(project_id, candidate_id)
        require(
            receipt_path.is_file(),
            "PROJECT_CANDIDATE_OVERLAY_NOT_FOUND",
            "The live-root candidate overlay receipt does not exist.",
            status="MISMATCH",
            candidate_id=candidate_id,
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        claimed = str(receipt.get("receipt_sha256") or "")
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        receipt_schema = receipt.get("schema")
        proposal_identity_valid = bool(
            receipt_schema == "evidence-lane.project-hil-proposal.v1"
            and receipt.get("proposal_id") == candidate_id
            and receipt.get("candidate_id") == candidate_id
            and receipt.get("full_candidate_package_built") is False
        )
        require(
            receipt_schema
            in {
                "evidence-lane.project-candidate-overlay.v1",
                "evidence-lane.project-hil-proposal.v1",
            }
            and receipt.get("project_id") == project_id
            and receipt.get("candidate_id") == candidate_id
            and (
                receipt_schema == "evidence-lane.project-candidate-overlay.v1"
                or proposal_identity_valid
            )
            and receipt.get("state") == "SEALED_AFTER_LIFECYCLE_APPEND"
            and claimed == sha256_bytes(canonical_json_bytes(body)),
            "PROJECT_CANDIDATE_PRESERVATION_RECEIPT_MISMATCH",
            "The preserved candidate receipt failed exact identity validation.",
            status="MISMATCH",
            candidate_id=candidate_id,
        )
        validation = dict(receipt.get("validation") or {})
        return {
            "status": "PASS",
            "candidate_id": candidate_id,
            "receipt_schema": receipt_schema,
            "receipt_sha256": claimed,
            "manifest_sha256": validation.get("manifest_sha256"),
            "package_sha256": validation.get("package_sha256"),
            "working_identity_sha256": receipt.get("working_identity_sha256"),
            "promotable": validation.get("promotable"),
            "preservation_scope": (
                "IMMUTABLE_CANDIDATE_RECEIPT_IDENTITY_WITHOUT_LIVE_ROOT_EQUALITY"
            ),
            "current_live_root_equality_required": False,
            "candidate_cleared": False,
            "candidate_rebuilt": False,
        }

    def candidate_metadata(self, project_id: str, candidate_id: str) -> dict[str, Any]:
        root = self.project_root(project_id)
        if root == self._legacy_project_root(project_id):
            candidate = self.candidate_path(project_id, candidate_id)
            return {
                name: json.loads(
                    (candidate / f"{name}.json").read_text(encoding="utf-8")
                )
                for name in ("manifest", "project_identity", "entry_slip", "exit_slip")
            }
        receipt = json.loads(
            self._candidate_overlay_receipt_path(project_id, candidate_id).read_text(
                encoding="utf-8"
            )
        )
        return dict(receipt.get("package_metadata") or {})

    def accepted_path(self, project_id: str, pv_id: str) -> Path:
        require(
            pv_id.startswith("PV") and pv_id[2:].isdigit() and int(pv_id[2:]) >= 1,
            "PV_ID_INVALID",
            "Accepted PV IDs must use the PV1, PV2, ... sequence.",
            status="BLOCKED",
            pv_id=pv_id,
        )
        root = self.project_root(project_id)
        archives = sorted((root / "accepted").glob(f"{pv_id}__*.zip"))
        if root != self._legacy_project_root(project_id):
            require(
                len(archives) == 1,
                "PROJECT_ACCEPTED_SINGLE_ZIP_REQUIRED",
                "Current accepted storage requires exactly one numbered full-root ZIP.",
                status="MISMATCH",
                project_id=project_id,
                pv_id=pv_id,
                archive_count=len(archives),
            )
            return archives[0].resolve()
        directory = (root / "accepted" / pv_id).resolve()
        if directory.is_dir():
            return directory
        require(
            len(archives) <= 1,
            "PROJECT_ACCEPTED_ARCHIVE_AMBIGUOUS",
            "Accepted project storage contains multiple archives for one PV.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
            archives=[str(path) for path in archives],
        )
        return archives[0].resolve() if archives else directory

    @contextmanager
    def accepted_view(self, project_id: str, pv_id: str) -> Iterator[Path]:
        artifact = self.accepted_path(project_id, pv_id)
        if artifact.is_dir():
            yield artifact
            return
        require(
            artifact.is_file() and artifact.suffix.lower() == ".zip",
            "ACCEPTED_PV_NOT_FOUND",
            "The accepted PV artifact does not exist.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
        )
        with materialized_project_pv_archive(artifact) as view:
            yield view

    def validate_accepted(
        self,
        project_id: str,
        pv_id: str,
        *,
        require_promotable: bool = False,
    ) -> dict[str, Any]:
        artifact = self.accepted_path(project_id, pv_id)
        if artifact.is_file() and artifact.suffix.lower() == ".zip":
            validation = validate_project_pv_archive(artifact)
            require(
                validation.get("pv_id") == pv_id,
                "PROJECT_ACCEPTED_ARCHIVE_PV_MISMATCH",
                "The accepted archive filename and manifest PV identity differ.",
                status="MISMATCH",
                pv_id=pv_id,
                archive_pv=validation.get("pv_id"),
            )
            return validation
        return validate_pv_package(
            artifact,
            require_promotable=require_promotable,
        )

    def accepted_manifest(self, project_id: str, pv_id: str) -> dict[str, Any]:
        artifact = self.accepted_path(project_id, pv_id)
        if artifact.is_file() and artifact.suffix.lower() == ".zip":
            validation = validate_project_pv_archive(artifact)
            manifest = validation.get("candidate_package_manifest")
            return dict(manifest) if isinstance(manifest, dict) else {}
        return json.loads((artifact / "manifest.json").read_text(encoding="utf-8"))

    def accepted_metadata(self, project_id: str, pv_id: str) -> dict[str, Any]:
        """Return bounded package metadata for either accepted storage format."""

        artifact = self.accepted_path(project_id, pv_id)
        if artifact.is_file() and artifact.suffix.lower() == ".zip":
            validation = validate_project_pv_archive(artifact)
            return {
                "manifest": dict(validation.get("candidate_package_manifest") or {}),
                "project_identity": dict(validation.get("project_identity") or {}),
                "exit_slip": dict(validation.get("exit_slip") or {}),
            }
        return {
            name: json.loads((artifact / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("manifest", "project_identity", "entry_slip", "exit_slip")
        }

    def next_pv_id(self, project_id: str) -> str:
        highest = self.highest_accepted_ordinal(project_id)
        if highest == 0:
            return "PV1"
        return f"PV{highest + 1}"

    def accepted_ids(self, project_id: str) -> list[str]:
        root = self.project_root(project_id) / "accepted"
        accepted = {
            path.name
            for path in root.glob("PV*")
            if path.is_dir() and path.name[2:].isdigit() and int(path.name[2:]) >= 1
        }
        for path in root.glob("PV*__*.zip"):
            pv_id = path.name.split("__", 1)[0]
            if pv_id[2:].isdigit() and int(pv_id[2:]) >= 1:
                accepted.add(pv_id)
        return sorted(
            accepted,
            key=lambda value: int(value[2:]),
        )

    def live_root_pointer_continuity(
        self,
        project_id: str,
        pv_id: str,
    ) -> dict[str, Any]:
        """Validate the current external pointer from root receipts only.

        Current promotions emit one v2 receipt plus one committed swap journal.
        External authorities migrated before that route existed may instead carry
        the original v1 promotion receipt and exact task-binding receipts that
        all bind the same accepted package hash.  That legacy baseline remains a
        root-receipt reference only: this verifier never opens ``accepted/`` and
        the next governed promotion must replace it with the v2 pair.
        """

        require(
            self.uses_external_project_authority(project_id),
            "LIVE_ROOT_POINTER_CONTINUITY_EXTERNAL_REQUIRED",
            "Live-root pointer continuity is only valid for external project authority.",
            status="MISMATCH",
            project_id=project_id,
        )
        root = self.project_root(project_id)
        pointer = self.pointer(project_id)
        require(
            pointer.accepted_pv == pv_id,
            "LIVE_ROOT_POINTER_CONTINUITY_PV_MISMATCH",
            "The requested live-root baseline is not the current accepted pointer.",
            status="MISMATCH",
            project_id=project_id,
            pv_id=pv_id,
        )
        matches: list[dict[str, Any]] = []
        journal_root = root / "receipts" / "accepted-swap-journals"
        for journal_path in sorted(journal_root.glob("*.json")):
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if (
                journal.get("schema") != "evidence-lane.project-accepted-swap.v1"
                or journal.get("project_id") != project_id
                or journal.get("state")
                != "COMMITTED_PRIOR_ACCEPTED_PURGED_AFTER_VERIFICATION"
                or journal.get("next_pointer") != pointer.as_dict()
                or journal.get("archive_manifest_sha256")
                != pointer.accepted_manifest_sha256
            ):
                continue
            decision_id = str(journal.get("decision_id") or "")
            receipt_path = root / "receipts" / f"{decision_id}.json"
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            prior_pointer = dict(journal.get("prior_pointer") or {})
            package_sha256 = str(receipt.get("archive_sha256") or "").upper()
            valid = bool(
                receipt.get("schema") == "evidence-lane.pv-promotion.receipt.v2"
                and receipt.get("decision_id") == decision_id
                and receipt.get("candidate_id") == journal.get("candidate_id")
                and receipt.get("accepted_pv") == pv_id
                and receipt.get("manifest_sha256") == pointer.accepted_manifest_sha256
                and receipt.get("archive_sha256")
                == journal.get("staged_archive_sha256")
                and receipt.get("pointer_generation_before")
                == prior_pointer.get("generation")
                and receipt.get("pointer_generation_after") == pointer.generation
                and receipt.get("live_working_identity_preserved") is True
                and receipt.get("candidate_directory_created") is False
                and receipt.get(
                    "prior_accepted_artifact_purged_after_new_archive_verified"
                )
                is True
                and journal.get("prior_accepted_artifact_purged") is True
                and len(package_sha256) == 64
                and all(character in "0123456789ABCDEF" for character in package_sha256)
            )
            if valid:
                matches.append(
                    {
                        "decision_id": decision_id,
                        "candidate_id": receipt["candidate_id"],
                        "manifest_sha256": pointer.accepted_manifest_sha256,
                        "package_sha256": package_sha256,
                        "promotion_receipt_sha256": sha256_bytes(
                            receipt_path.read_bytes()
                        ),
                        "swap_journal_sha256": sha256_bytes(journal_path.read_bytes()),
                    }
                )
        if len(matches) == 1:
            exact_match = {
                **matches[0],
                "continuity_mode": "CURRENT_V2_PROMOTION_RECEIPT_PAIR",
                "validation_scope": "LIVE_ROOT_PROMOTION_RECEIPT_PAIR",
                "archive_verification": "VERIFIED_DURING_GOVERNED_PROMOTION",
                "migration_required_at_next_promotion": False,
            }
        else:
            require(
                not matches,
                "LIVE_ROOT_POINTER_CONTINUITY_RECEIPT_MISMATCH",
                "The current external pointer has ambiguous v2 root promotion receipt pairs.",
                status="MISMATCH",
                project_id=project_id,
                pv_id=pv_id,
                matching_receipt_count=len(matches),
            )
            legacy_promotions: list[dict[str, Any]] = []
            for receipt_path in sorted((root / "receipts").glob("*.json")):
                try:
                    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                decision_id = str(receipt.get("decision_id") or "")
                candidate_id = str(receipt.get("candidate_id") or "")
                if not (
                    receipt.get("schema") == "evidence-lane.pv-promotion.receipt.v1"
                    and receipt.get("decision") == "APPROVE"
                    and decision_id
                    and receipt_path.stem == decision_id
                    and candidate_id
                    and receipt.get("accepted_pv") == pv_id
                    and receipt.get("manifest_sha256")
                    == pointer.accepted_manifest_sha256
                    and receipt.get("pointer_generation_before")
                    == pointer.prior_generation
                    and receipt.get("pointer_generation_after") == pointer.generation
                    and receipt.get("candidate_bytes_preserved") is True
                ):
                    continue
                legacy_promotions.append(
                    {
                        "decision_id": decision_id,
                        "candidate_id": candidate_id,
                        "manifest_sha256": pointer.accepted_manifest_sha256,
                        "promotion_receipt_sha256": sha256_bytes(
                            receipt_path.read_bytes()
                        ),
                    }
                )

            binding_hashes: dict[str, list[str]] = {}
            binding_root = root / "receipts" / "codex-task-bindings"
            for binding_path in sorted(binding_root.glob("*.json")):
                try:
                    binding = json.loads(binding_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    continue
                accepted_pointer = binding.get("accepted_pointer")
                package_sha256 = str(
                    accepted_pointer.get("package_sha256")
                    if isinstance(accepted_pointer, dict)
                    else ""
                ).strip().upper()
                package_hash_valid = bool(
                    len(package_sha256) == 64
                    and all(
                        character in "0123456789ABCDEF"
                        for character in package_sha256
                    )
                )
                if not (
                    binding.get("schema")
                    == "evidence-lane.codex-exact-task-project-session-binding.v1"
                    and binding.get("status") == "PASS"
                    and binding.get("project_id") == project_id
                    and isinstance(accepted_pointer, dict)
                    and accepted_pointer.get("accepted_pv") == pv_id
                    and accepted_pointer.get("generation") == pointer.generation
                    and accepted_pointer.get("manifest_sha256")
                    == pointer.accepted_manifest_sha256
                    and package_hash_valid
                ):
                    continue
                binding_hashes.setdefault(package_sha256, []).append(
                    sha256_bytes(binding_path.read_bytes())
                )

            migration_receipt_hash: str | None = None
            migration_path = (
                root / "receipts" / "project-authority" / "migration.json"
            )
            if not binding_hashes and migration_path.is_file():
                try:
                    migration = json.loads(
                        migration_path.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeError, json.JSONDecodeError):
                    migration = {}
                migration_body = {
                    key: value
                    for key, value in migration.items()
                    if key != "receipt_sha256"
                }
                migration_package_identity = str(
                    migration.get("copy_manifest_sha256") or ""
                ).upper()
                migration_valid = bool(
                    migration.get("schema")
                    == "evidence-lane.project-authority-migration.v1"
                    and migration.get("status") == "PASS"
                    and migration.get("project_id") == project_id
                    and migration.get("accepted_pv") == pv_id
                    and migration.get("pointer_generation") == pointer.generation
                    and migration.get("pointer_moved") is False
                    and migration.get("candidate_created") is False
                    and migration.get("hil_inferred") is False
                    and migration.get("legacy_history_authoritative") is False
                    and len(migration_package_identity) == 64
                    and all(
                        character in "0123456789ABCDEF"
                        for character in migration_package_identity
                    )
                    and migration.get("receipt_sha256")
                    == sha256_bytes(canonical_json_bytes(migration_body))
                )
                if migration_valid:
                    migration_receipt_hash = sha256_bytes(
                        migration_path.read_bytes()
                    )
                    binding_hashes[migration_package_identity] = [
                        migration_receipt_hash
                    ]

            require(
                len(legacy_promotions) == 1 and len(binding_hashes) == 1,
                "LIVE_ROOT_POINTER_CONTINUITY_RECEIPT_MISMATCH",
                "The current external pointer requires one exact v2 pair or one unambiguous legacy root baseline.",
                status="MISMATCH",
                project_id=project_id,
                pv_id=pv_id,
                matching_receipt_count=0,
                legacy_promotion_receipt_count=len(legacy_promotions),
                legacy_package_hash_count=len(binding_hashes),
            )
            package_sha256, binding_receipt_hashes = next(iter(binding_hashes.items()))
            exact_match = {
                **legacy_promotions[0],
                "package_sha256": package_sha256,
                "swap_journal_sha256": None,
                "supporting_task_binding_receipt_count": len(
                    binding_receipt_hashes
                ),
                "supporting_task_binding_receipts_sha256": sha256_bytes(
                    canonical_json_bytes(sorted(binding_receipt_hashes))
                ),
                "continuity_mode": (
                    "PROJECT_AUTHORITY_MIGRATION_PLAN_POINTER_BASELINE"
                    if migration_receipt_hash is not None
                    else "LEGACY_V1_PROMOTION_POINTER_BOUND"
                ),
                "validation_scope": (
                    "LIVE_ROOT_MIGRATION_RECEIPT_AND_PLAN_POINTER"
                    if migration_receipt_hash is not None
                    else "LIVE_ROOT_LEGACY_PROMOTION_AND_TASK_BINDINGS"
                ),
                "archive_verification": "UNAVAILABLE_LEGACY_BASELINE",
                "migration_required_at_next_promotion": True,
            }
        return {
            "status": "PASS",
            "schema": "evidence-lane.live-root-pointer-continuity.v1",
            "project_id": project_id,
            "pointer": pointer.as_dict(),
            **exact_match,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
        }

    def highest_accepted_ordinal(self, project_id: str) -> int:
        accepted = self.accepted_ids(project_id)
        pointer = self.pointer(project_id)
        ordinals = [int(value[2:]) for value in accepted]
        if pointer.accepted_pv:
            ordinals.append(int(pointer.accepted_pv[2:]))
        return max(ordinals, default=0)

    def place_live_root_hil_proposal(
        self,
        project_id: str,
        proposal_id: str,
        *,
        proposed_pv: str,
        project_overlay_source: str | Path,
        package_metadata: dict[str, Any],
        validation: dict[str, Any],
    ) -> dict[str, Any]:
        """Commit one Project Overlay proposal without building a PV tree.

        External project authority works exclusively in the live root.  The
        pre-HIL operation therefore refreshes only ``project_overlay/`` and
        seals a receipt over the live bytes.  The accepted CAS ZIP is created
        later, and only after exact human approval.
        """

        root = self.project_root(project_id)
        require(
            root != self._legacy_project_root(project_id),
            "LIVE_ROOT_HIL_PROPOSAL_EXTERNAL_REQUIRED",
            "The live-root HIL proposal route is only valid for external project authority.",
            status="MISMATCH",
            project_id=project_id,
        )
        pointer = self.pointer(project_id)
        overlay_source = Path(project_overlay_source).resolve()
        source_overlay_validation = validate_project_overlay(overlay_source)
        require(
            source_overlay_validation.get("valid") is True,
            "PROJECT_HIL_OVERLAY_PROJECTION_INVALID",
            "The proposed Project Overlay failed validation.",
            status="MISMATCH",
            proposal_id=proposal_id,
        )
        project_overlay_target = root / "project_overlay"
        overlay_stage = root / f".project-overlay-stage-{proposal_id}"
        overlay_prior = root / f".project-overlay-prior-{proposal_id}"
        require(
            not overlay_stage.exists() and not overlay_prior.exists(),
            "PROJECT_HIL_OVERLAY_PROJECTION_CONFLICT",
            "A prior Project Overlay projection transaction requires recovery.",
            status="BLOCKED",
            proposal_id=proposal_id,
        )
        shutil.copytree(overlay_source, overlay_stage)
        staged_overlay_validation = validate_project_overlay(overlay_stage)
        require(
            staged_overlay_validation == source_overlay_validation,
            "PROJECT_HIL_OVERLAY_PROJECTION_COPY_MISMATCH",
            "Project Overlay bytes changed while staging the live-root refresh.",
            status="FAIL",
            proposal_id=proposal_id,
        )
        with self._lock(project_id):
            prior_moved = False
            try:
                if project_overlay_target.exists():
                    project_overlay_target.replace(overlay_prior)
                    prior_moved = True
                overlay_stage.replace(project_overlay_target)
                require(
                    validate_project_overlay(project_overlay_target)
                    == source_overlay_validation,
                    "PROJECT_HIL_OVERLAY_PROJECTION_COMMIT_MISMATCH",
                    "The committed live-root Project Overlay failed validation.",
                    status="FAIL",
                    proposal_id=proposal_id,
                )
            except Exception:
                if prior_moved and not project_overlay_target.exists():
                    overlay_prior.replace(project_overlay_target)
                raise
            if overlay_prior.exists():
                resolved_prior = overlay_prior.resolve()
                resolved_prior.relative_to(root)
                require(
                    resolved_prior.name == f".project-overlay-prior-{proposal_id}",
                    "PROJECT_HIL_OVERLAY_PRIOR_PATH_INVALID",
                    "The prior Project Overlay path escaped its transaction.",
                    status="BLOCKED",
                )
                shutil.rmtree(resolved_prior)

        working = working_overlay_manifest(root, project_id=project_id)
        proposal_validation = {
            **validation,
            "storage_kind": "LIVE_PROJECT_ROOT_HIL_PROPOSAL",
            # Compatibility fields remain bound to the exact live working
            # identity until all public candidate terminology is migrated.
            "manifest_sha256": working["working_identity_sha256"],
            "package_sha256": working["working_identity_sha256"],
            "working_identity_sha256": working["working_identity_sha256"],
            "working_member_count": working["member_count"],
            "candidate_directory_created": False,
            "full_candidate_package_built": False,
            "project_overlay_refreshed_in_live_root": True,
            "project_overlay_validation": source_overlay_validation,
        }
        receipt_body = {
            "schema": "evidence-lane.project-hil-proposal.v1",
            "state": "PROVISIONAL_OVERLAY_REFRESH",
            "project_id": project_id,
            "project_root": str(root),
            "proposal_id": proposal_id,
            "candidate_id": proposal_id,
            "proposed_pv": proposed_pv,
            "parent_accepted_pv": pointer.accepted_pv,
            "parent_accepted_manifest_sha256": pointer.accepted_manifest_sha256,
            "pointer_generation": pointer.generation,
            "working_identity_sha256": working["working_identity_sha256"],
            "working_member_count": working["member_count"],
            "working_total_bytes": working["total_bytes"],
            "working_unique_blob_count": working["unique_blob_count"],
            "working_members": working["members"],
            "package_metadata": package_metadata,
            "validation": proposal_validation,
            "candidate_directory_created": False,
            "full_candidate_package_built": False,
            "accepted_artifact_created": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "pointer_moved": False,
            "hil_inferred": False,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        destination = self._candidate_overlay_receipt_path(project_id, proposal_id)
        with self._lock(project_id):
            if destination.exists():
                existing = json.loads(destination.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "PROJECT_HIL_PROPOSAL_CONFLICT",
                    "The proposal ID already binds different live-root bytes.",
                    status="BLOCKED",
                    proposal_id=proposal_id,
                )
            else:
                atomic_write_json(destination, receipt)
        return proposal_validation

    def place_candidate(
        self,
        project_id: str,
        candidate_id: str,
        built_directory: str | Path,
    ) -> dict[str, Any]:
        source = Path(built_directory).resolve()
        root = self.project_root(project_id)
        require(
            root == self._legacy_project_root(project_id),
            "OBSOLETE_EXTERNAL_CANDIDATE_CONSTRUCTION_ROUTE",
            "External project authority must refresh the live-root HIL proposal; "
            "building or extracting a full candidate package is obsolete.",
            status="BLOCKED",
            required_current_route="place_live_root_hil_proposal",
            project_id=project_id,
            candidate_id=candidate_id,
        )
        validation = validate_pv_package(source)
        require(
            validation["candidate_id"] == candidate_id,
            "CANDIDATE_ID_MISMATCH",
            "The candidate directory and package manifest IDs differ.",
            status="MISMATCH",
        )
        destination = self.candidate_path(project_id, candidate_id)
        with self._lock(project_id):
            if destination.exists():
                comparison = compare_package_bytes(source, destination)
                require(
                    comparison["identical"],
                    "IMMUTABLE_CANDIDATE_CONFLICT",
                    "An immutable candidate already exists with different bytes.",
                    status="BLOCKED",
                    candidate_id=candidate_id,
                )
            else:
                shutil.copytree(source, destination)
                copied = compare_package_bytes(source, destination)
                require(
                    copied["identical"],
                    "CANDIDATE_COPY_MISMATCH",
                    "Candidate bytes changed while entering the immutable store.",
                    status="FAIL",
                )
        return validate_pv_package(destination)

    def reconcile_pending_hil_project_overlay(
        self,
        project_id: str,
        candidate_id: str,
        *,
        session_id: str,
    ) -> dict[str, Any]:
        """Append one final HIL blast-radius transition without rebuilding a candidate."""

        root = self.project_root(project_id)
        require(
            root != self._legacy_project_root(project_id),
            "PROJECT_OVERLAY_RECONCILIATION_EXTERNAL_REQUIRED",
            "Pending-HIL Project Overlay reconciliation requires live-root authority.",
            status="MISMATCH",
        )
        receipt_path = self._candidate_overlay_receipt_path(project_id, candidate_id)
        require(
            receipt_path.is_file(),
            "PROJECT_CANDIDATE_OVERLAY_NOT_FOUND",
            "The pending candidate overlay receipt is unavailable.",
            status="MISMATCH",
            candidate_id=candidate_id,
        )
        candidate_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        validation = dict(candidate_receipt.get("validation") or {})
        proposed_pv = str(validation.get("proposed_pv") or "").strip()
        pointer = self.pointer(project_id)
        sectors_manifest = root / "sectors" / "manifest.json"
        require(
            sectors_manifest.is_file()
            and proposed_pv == self.next_pv_id(project_id),
            "PROJECT_OVERLAY_RECONCILIATION_BINDING_INVALID",
            "HIL overlay reconciliation requires current sectors and the next PV identity.",
            status="MISMATCH",
            candidate_id=candidate_id,
            proposed_pv=proposed_pv or None,
        )
        sector_identity = sha256_file(sectors_manifest)
        transition_id = (
            f"{candidate_id}__FINAL_HIL_RECONCILIATION__{sector_identity[:16]}"
        )
        overlay_root = root / "project_overlay"
        overlay_database = overlay_root / "project_overlay.sqlite"
        if overlay_database.is_file():
            connection = sqlite3.connect(overlay_database)
            try:
                tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                existing = (
                    connection.execute(
                        "SELECT transition_sha256 FROM pv_hil_transition WHERE transition_id=?",
                        (transition_id,),
                    ).fetchone()
                    if "pv_hil_transition" in tables
                    else None
                )
            finally:
                connection.close()
            if existing is not None:
                overlay_support = refresh_authority_support(
                    root,
                    "project_overlay",
                )
                current = validate_project_overlay(overlay_root)
                return {
                    "status": "PASS",
                    "idempotent_reuse": True,
                    "candidate_id": candidate_id,
                    "transition_id": transition_id,
                    "transition_sha256": str(existing[0]),
                    "project_overlay_validation": current,
                    "project_overlay_refreshed": True,
                    "project_overlay_support": overlay_support,
                    "candidate_rebuilt": False,
                    "pointer_moved": False,
                }
        stage = root / f".project-overlay-reconcile-{sector_identity[:16]}"
        prior = root / f".project-overlay-prior-{sector_identity[:16]}"
        require(
            not stage.exists() and not prior.exists(),
            "PROJECT_OVERLAY_RECONCILIATION_TRANSACTION_CONFLICT",
            "A prior HIL overlay reconciliation requires explicit recovery.",
            status="BLOCKED",
        )
        lineage = resolved_chat_lineage_root(root) / f"{session_id}.jsonl"
        built = build_project_overlay(
            stage,
            lane_bundle_path=root / "sectors",
            lineage_source=lineage if lineage.is_file() else None,
            candidate_id=transition_id,
            proposed_pv=proposed_pv,
            parent_accepted_pv=pointer.accepted_pv,
            pointer_generation=pointer.generation,
            code_mode="local_code",
            created_at=utc_now(),
            truth_state="HIL_PROPOSAL_ONLY",
            prior_overlay_path=overlay_root if overlay_root.is_dir() else None,
        )
        require(
            built.get("valid") is True
            and built.get("accepted_archive_opened") is False
            and built.get("accepted_archive_queried") is False,
            "PROJECT_OVERLAY_RECONCILIATION_BUILD_FAILED",
            "The final HIL Project Overlay reconciliation failed validation.",
            status="FAIL",
        )
        staged_database_sha256 = sha256_file(stage / "project_overlay.sqlite")
        with self._lock(project_id):
            prior_moved = False
            try:
                if overlay_root.exists():
                    overlay_root.replace(prior)
                    prior_moved = True
                stage.replace(overlay_root)
                committed_pre_support_sha256 = sha256_file(
                    overlay_root / "project_overlay.sqlite"
                )
                require(
                    committed_pre_support_sha256 == staged_database_sha256,
                    "PROJECT_OVERLAY_RECONCILIATION_ATOMIC_SWAP_MISMATCH",
                    "The committed HIL Project Overlay changed during the atomic directory swap.",
                    status="FAIL",
                    staged_database_sha256=staged_database_sha256,
                    committed_pre_support_sha256=committed_pre_support_sha256,
                )
                overlay_support = refresh_authority_support(
                    root,
                    "project_overlay",
                )
                committed = validate_project_overlay(overlay_root)
                committed_database_sha256 = sha256_file(
                    overlay_root / "project_overlay.sqlite"
                )
                require(
                    committed.get("valid") is True
                    and committed_database_sha256
                    == overlay_support["database"]["sha256"],
                    "PROJECT_OVERLAY_RECONCILIATION_COMMIT_MISMATCH",
                    "The committed HIL Project Overlay differs from its governed support refresh.",
                    status="FAIL",
                    staged_manifest_sha256=built.get("manifest_sha256"),
                    committed_manifest_sha256=committed.get("manifest_sha256"),
                    staged_transition_head_sha256=built.get(
                        "transition_head_sha256"
                    ),
                    committed_transition_head_sha256=committed.get(
                        "transition_head_sha256"
                    ),
                    staged_delta_sha256=dict(
                        built.get("project_overlay_delta") or {}
                    ).get("delta_sha256"),
                    committed_delta_sha256=dict(
                        committed.get("project_overlay_delta") or {}
                    ).get("delta_sha256"),
                    staged_database_sha256=staged_database_sha256,
                    committed_database_sha256=committed_database_sha256,
                )
            except Exception:
                if prior_moved and not overlay_root.exists():
                    prior.replace(overlay_root)
                raise
            if prior.exists():
                shutil.rmtree(prior)
        receipt_body = {
            "schema": "evidence-lane.project-overlay-hil-reconciliation.v1",
            "status": "PASS",
            "project_id": project_id,
            "session_id": session_id,
            "candidate_id": candidate_id,
            "transition_id": transition_id,
            "transition_head_sha256": built.get("transition_head_sha256"),
            "sector_identity_sha256": sector_identity,
            "project_overlay_delta_sha256": dict(
                built.get("project_overlay_delta") or {}
            ).get("delta_sha256"),
            "project_overlay_refreshed": True,
            "project_overlay_support_receipt_sha256": overlay_support[
                "receipt_sha256"
            ],
            "candidate_id_preserved": True,
            "candidate_rebuilt": False,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "pointer_moved": False,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        reconciliation_path = (
            root
            / "receipts"
            / "project-overlay-reconciliations"
            / f"{transition_id}.json"
        )
        atomic_write_json(reconciliation_path, receipt)
        return {
            "status": "PASS",
            "idempotent_reuse": False,
            "candidate_id": candidate_id,
            "transition_id": transition_id,
            "transition_sha256": built.get("transition_head_sha256"),
            "project_overlay_validation": built,
            "project_overlay_refreshed": True,
            "candidate_rebuilt": False,
            "pointer_moved": False,
            "receipt": receipt,
            "receipt_path": str(reconciliation_path),
        }

    def finalize_candidate_overlay(
        self, project_id: str, candidate_id: str
    ) -> dict[str, Any]:
        """Seal external working identity after all exit lifecycle writes finish."""

        root = self.project_root(project_id)
        if root == self._legacy_project_root(project_id):
            return self.candidate_validation(project_id, candidate_id)
        receipt_path = self._candidate_overlay_receipt_path(project_id, candidate_id)
        require(
            receipt_path.is_file(),
            "PROJECT_CANDIDATE_OVERLAY_NOT_FOUND",
            "The provisional live-root candidate overlay receipt does not exist.",
            status="MISMATCH",
            candidate_id=candidate_id,
        )
        with self._lock(project_id):
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            prior_claimed = str(receipt.get("receipt_sha256") or "")
            prior_body = dict(receipt)
            prior_body.pop("receipt_sha256", None)
            receipt_schema = receipt.get("schema")
            require(
                receipt_schema
                in {
                    "evidence-lane.project-candidate-overlay.v1",
                    "evidence-lane.project-hil-proposal.v1",
                }
                and receipt.get("project_id") == project_id
                and receipt.get("candidate_id") == candidate_id
                and (
                    receipt_schema == "evidence-lane.project-candidate-overlay.v1"
                    or (
                        receipt.get("proposal_id") == candidate_id
                        and receipt.get("full_candidate_package_built") is False
                    )
                )
                and receipt.get("state")
                in {
                    "PROVISIONAL_ENGINE_BUILD",
                    "PROVISIONAL_OVERLAY_REFRESH",
                    "SEALED_AFTER_LIFECYCLE_APPEND",
                }
                and prior_claimed == sha256_bytes(canonical_json_bytes(prior_body)),
                "PROJECT_CANDIDATE_OVERLAY_RECEIPT_MISMATCH",
                "The provisional candidate overlay receipt failed its identity checks.",
                status="MISMATCH",
                candidate_id=candidate_id,
            )
            observed_working = working_overlay_manifest(root, project_id=project_id)
            if (
                receipt.get("state") == "SEALED_AFTER_LIFECYCLE_APPEND"
                and receipt.get("working_identity_sha256")
                == observed_working["working_identity_sha256"]
            ):
                return self.candidate_validation(project_id, candidate_id)
            revision_path = (
                root
                / "receipts"
                / "candidate-overlay-revisions"
                / candidate_id
                / f"{prior_claimed}.json"
            )
            if revision_path.is_file():
                require(
                    json.loads(revision_path.read_text(encoding="utf-8")) == receipt,
                    "PROJECT_CANDIDATE_OVERLAY_REVISION_CONFLICT",
                    "The preserved prior candidate-overlay seal differs on replay.",
                    status="MISMATCH",
                    candidate_id=candidate_id,
                    prior_receipt_sha256=prior_claimed,
                )
            else:
                atomic_write_json(revision_path, receipt)
            working = working_overlay_manifest(root, project_id=project_id)
            validation = dict(receipt.get("validation") or {})
            validation.update(
                {
                    "storage_kind": (
                        "LIVE_PROJECT_ROOT_HIL_PROPOSAL"
                        if receipt_schema == "evidence-lane.project-hil-proposal.v1"
                        else "LIVE_PROJECT_ROOT_CANDIDATE_OVERLAY"
                    ),
                    "manifest_sha256": working["working_identity_sha256"],
                    "package_sha256": working["working_identity_sha256"],
                    "working_identity_sha256": working["working_identity_sha256"],
                    "working_member_count": working["member_count"],
                    "candidate_directory_created": False,
                    "full_candidate_package_built": (
                        False
                        if receipt_schema == "evidence-lane.project-hil-proposal.v1"
                        else validation.get("full_candidate_package_built")
                    ),
                }
            )
            receipt.update(
                {
                    "state": "SEALED_AFTER_LIFECYCLE_APPEND",
                    "prior_overlay_receipt_sha256": prior_claimed,
                    "overlay_reconciliation_count": int(
                        receipt.get("overlay_reconciliation_count") or 0
                    )
                    + 1,
                    "candidate_id_preserved": True,
                    "candidate_rebuilt": False,
                    "candidate_history_preserved": True,
                    "working_identity_sha256": working["working_identity_sha256"],
                    "working_member_count": working["member_count"],
                    "working_total_bytes": working["total_bytes"],
                    "working_unique_blob_count": working["unique_blob_count"],
                    "working_members": working["members"],
                    "validation": validation,
                }
            )
            receipt.pop("receipt_sha256", None)
            receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
            atomic_write_json(receipt_path, receipt)
        return self.candidate_validation(project_id, candidate_id)

    def _validate_postseal_acceptance_receipt(
        self,
        project_id: str,
        candidate_id: str,
        *,
        pointer: ActivePointer,
        exit_slip: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Apply one identical post-seal promotion gate to every storage format."""

        acceptance = exit_slip.get("acceptance_checks") or {}
        pending_postseal = int(
            (acceptance.get("counts") or {}).get("PENDING_POSTSEAL") or 0
        )
        if not pending_postseal:
            return None
        receipt_path = (
            self.project_root(project_id)
            / "receipts"
            / f"postseal_{candidate_id.lower()}.json"
        )
        require(
            receipt_path.is_file(),
            "POSTSEAL_ACCEPTANCE_RECEIPT_REQUIRED",
            "This candidate declares a post-seal acceptance check, but its "
            "external immutable-candidate receipt is missing.",
            status="BLOCKED",
            candidate_id=candidate_id,
            receipt_path=str(receipt_path),
        )
        try:
            postseal_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise EvidenceLaneError(
                "POSTSEAL_ACCEPTANCE_RECEIPT_INVALID",
                "The external post-seal acceptance receipt is unreadable.",
                status="FAIL",
                details={"receipt_path": str(receipt_path)},
            ) from exc
        claimed_receipt_sha = postseal_receipt.get("receipt_sha256")
        receipt_payload = dict(postseal_receipt)
        receipt_payload.pop("receipt_sha256", None)
        actual_receipt_sha = sha256_bytes(canonical_json_bytes(receipt_payload))
        postseal_health = postseal_receipt.get("acceptance") or {}
        source_commit = (exit_slip.get("repository_exit") or {}).get("commit_sha")
        require(
            postseal_receipt.get("schema")
            == "evidence-lane.postseal-acceptance.receipt.v1"
            and postseal_receipt.get("project_id") == project_id
            and postseal_receipt.get("candidate_id") == candidate_id
            and postseal_receipt.get("accepted_pv_retained") == pointer.accepted_pv
            and postseal_receipt.get("pointer_generation_retained")
            == pointer.generation
            and postseal_receipt.get("source_commit_sha") == source_commit
            and claimed_receipt_sha == actual_receipt_sha
            and postseal_health.get("status") == "PASS"
            and postseal_health.get("verdict") == "ALL_EXECUTABLE_CHECKS_PASS"
            and postseal_health.get("source_unchanged") is True
            and int((postseal_health.get("counts") or {}).get("PASS") or 0)
            == pending_postseal,
            "POSTSEAL_ACCEPTANCE_RECEIPT_MISMATCH",
            "The post-seal receipt does not prove every declared post-seal "
            "check against this exact candidate, source commit, and pointer.",
            status="FAIL",
            candidate_id=candidate_id,
            expected_postseal_checks=pending_postseal,
            expected_source_commit=source_commit,
            receipt_path=str(receipt_path),
        )
        return {
            "status": "PASS",
            "path": str(receipt_path),
            "receipt_sha256": actual_receipt_sha,
            "checks": pending_postseal,
        }

    def promote(
        self,
        project_id: str,
        candidate_id: str,
        *,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
    ) -> dict[str, Any]:
        if self.project_root(project_id) != self._legacy_project_root(project_id):
            return self._promote_external_project_overlay(
                project_id,
                candidate_id,
                expected_pointer_generation=expected_pointer_generation,
                decided_by=decided_by,
                decision_id=decision_id,
            )
        candidate = self.candidate_path(project_id, candidate_id)
        candidate_validation = validate_pv_package(candidate)
        exit_slip = json.loads(
            (candidate / "exit_slip.json").read_text(encoding="utf-8")
        )
        proposed_pv = candidate_validation["proposed_pv"]
        accepted = self.accepted_path(project_id, proposed_pv)
        with self._lock(project_id):
            pointer = self.pointer(project_id)
            require(
                pointer.generation == expected_pointer_generation,
                "POINTER_COMPARE_AND_SWAP_FAILED",
                "The accepted pointer changed after the candidate was created.",
                status="STALE",
                expected_generation=expected_pointer_generation,
                actual_generation=pointer.generation,
            )
            postseal_receipt_validation = self._validate_postseal_acceptance_receipt(
                project_id,
                candidate_id,
                pointer=pointer,
                exit_slip=exit_slip,
            )
            require(
                proposed_pv == self.next_pv_id(project_id),
                "PV_SEQUENCE_MISMATCH",
                "The candidate is not the next canonical project version.",
                status="MISMATCH",
                proposed_pv=proposed_pv,
                expected=self.next_pv_id(project_id),
            )
            if accepted.exists():
                comparison = compare_package_bytes(candidate, accepted)
                require(
                    comparison["identical"],
                    "IMMUTABLE_ACCEPTED_PV_CONFLICT",
                    "The accepted PV path already contains different bytes.",
                    status="BLOCKED",
                    proposed_pv=proposed_pv,
                )
            else:
                shutil.copytree(candidate, accepted)
            comparison = compare_package_bytes(candidate, accepted)
            require(
                comparison["identical"],
                "PROMOTION_CHANGED_CANDIDATE_BYTES",
                "Accepted promotion did not preserve candidate bytes.",
                status="FAIL",
            )
            new_pointer = ActivePointer(
                project_id=project_id,
                accepted_pv=proposed_pv,
                accepted_manifest_sha256=candidate_validation["manifest_sha256"],
                generation=pointer.generation + 1,
                prior_generation=pointer.generation,
                updated_at=utc_now(),
            )
            atomic_write_json(
                self.project_root(project_id) / "active_pointer.json",
                {"schema": POINTER_SCHEMA, **new_pointer.as_dict()},
            )
            receipt = {
                "schema": "evidence-lane.pv-promotion.receipt.v1",
                "decision_id": decision_id,
                "decision": "APPROVE",
                "candidate_id": candidate_id,
                "accepted_pv": proposed_pv,
                "manifest_sha256": candidate_validation["manifest_sha256"],
                "candidate_bytes_preserved": True,
                "decided_by": decided_by,
                "pointer_generation_before": pointer.generation,
                "pointer_generation_after": new_pointer.generation,
                "postseal_acceptance": postseal_receipt_validation,
                "decided_at": utc_now(),
            }
            receipt_path = (
                self.project_root(project_id) / "receipts" / f"{decision_id}.json"
            )
            if receipt_path.exists():
                existing = json.loads(receipt_path.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "PROMOTION_RECEIPT_CONFLICT",
                    "The decision ID already has different promotion evidence.",
                    status="BLOCKED",
                )
            else:
                atomic_write_json(receipt_path, receipt)
        return {
            "accepted": validate_pv_package(accepted),
            "pointer": self.pointer(project_id).as_dict(),
            "receipt": receipt,
        }

    def _promote_external_project_overlay(
        self,
        project_id: str,
        candidate_id: str,
        *,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
    ) -> dict[str, Any]:
        """Seal the live root once, then replace accepted storage under pointer CAS."""

        root = self.project_root(project_id)
        validation = self.candidate_validation(project_id, candidate_id)
        metadata = self.candidate_metadata(project_id, candidate_id)
        proposed_pv = str(validation["proposed_pv"])
        with self._lock(project_id):
            before = self.pointer(project_id)
            require(
                before.generation == expected_pointer_generation,
                "POINTER_COMPARE_AND_SWAP_FAILED",
                "The accepted pointer changed after the live-root candidate was created.",
                status="STALE",
                expected_generation=expected_pointer_generation,
                actual_generation=before.generation,
            )
            postseal_receipt_validation = self._validate_postseal_acceptance_receipt(
                project_id,
                candidate_id,
                pointer=before,
                exit_slip=dict(metadata.get("exit_slip") or {}),
            )
            require(
                proposed_pv == self.next_pv_id(project_id),
                "PV_SEQUENCE_MISMATCH",
                "The live-root candidate is not the next canonical project version.",
                status="MISMATCH",
                proposed_pv=proposed_pv,
                expected=self.next_pv_id(project_id),
            )
            updated_at = utc_now()
            staging_parent = root / ".accepted-staging"
            staging = staging_parent / decision_id
            require(
                not staging.exists(),
                "PROJECT_ACCEPTED_STAGING_CONFLICT",
                "A prior accepted-archive staging directory requires explicit recovery.",
                status="BLOCKED",
                staging=str(staging),
            )
            staging.mkdir(parents=True)
            staged_archive = staging / f"{proposed_pv}__staged.zip"
            package_metadata = {
                "manifest": metadata.get("manifest"),
                "project_identity": metadata.get("project_identity"),
                "exit_slip": metadata.get("exit_slip"),
            }
            archive_validation = build_project_pv_archive(
                root,
                staged_archive,
                project_id=project_id,
                pv_id=proposed_pv,
                candidate_id=candidate_id,
                parent_accepted_pv=before.accepted_pv,
                parent_accepted_manifest_sha256=before.accepted_manifest_sha256,
                pointer_override={
                    "schema": POINTER_SCHEMA,
                    "project_id": project_id,
                    "accepted_pv": proposed_pv,
                    "generation": before.generation + 1,
                    "prior_generation": before.generation,
                    "updated_at": updated_at,
                    "accepted_manifest_binding": "THIS_ARCHIVE_MANIFEST_SHA256",
                },
                candidate_package_metadata=package_metadata,
            )
            archive_manifest_sha256 = str(archive_validation["archive_manifest_sha256"])
            final_archive_name = f"{proposed_pv}__{archive_manifest_sha256[:16]}.zip"
            final_staged_archive = staging / final_archive_name
            staged_archive.replace(final_staged_archive)
            archive_validation = validate_project_pv_archive(final_staged_archive)
            after = ActivePointer(
                project_id=project_id,
                accepted_pv=proposed_pv,
                accepted_manifest_sha256=archive_manifest_sha256,
                generation=before.generation + 1,
                prior_generation=before.generation,
                updated_at=updated_at,
            )
            accepted = root / "accepted"
            prior = root / f".accepted-prior-{decision_id}"
            require(
                accepted.is_dir() and not prior.exists(),
                "PROJECT_ACCEPTED_SWAP_BOUNDARY_INVALID",
                "The single-retention accepted directory cannot be swapped safely.",
                status="MISMATCH",
                accepted=str(accepted),
                prior=str(prior),
            )
            journal_path = (
                root / "receipts" / "accepted-swap-journals" / f"{decision_id}.json"
            )
            journal = {
                "schema": "evidence-lane.project-accepted-swap.v1",
                "project_id": project_id,
                "candidate_id": candidate_id,
                "decision_id": decision_id,
                "prior_pointer": before.as_dict(),
                "next_pointer": after.as_dict(),
                "staged_archive": str(final_staged_archive),
                "staged_archive_sha256": archive_validation["archive_sha256"],
                "archive_manifest_sha256": archive_manifest_sha256,
                "state": "VERIFIED_READY_TO_SWAP",
            }
            atomic_write_json(journal_path, journal)
            pointer_path = root / "active_pointer.json"
            accepted_swapped = False
            pointer_swapped = False
            try:
                accepted.replace(prior)
                staging.replace(accepted)
                accepted_swapped = True
                atomic_write_json(
                    pointer_path,
                    {"schema": POINTER_SCHEMA, **after.as_dict()},
                )
                pointer_swapped = True
                journal["state"] = "POINTER_AND_ACCEPTED_SWAPPED"
                atomic_write_json(journal_path, journal)
            except Exception:
                if accepted_swapped and not pointer_swapped:
                    failed_new = root / f".accepted-failed-{decision_id}"
                    if accepted.exists():
                        accepted.replace(failed_new)
                    if prior.exists():
                        prior.replace(accepted)
                    if failed_new.exists():
                        shutil.rmtree(failed_new)
                raise
            require(
                len(list(accepted.iterdir())) == 1
                and (accepted / final_archive_name).is_file()
                and self.pointer(project_id).as_dict() == after.as_dict(),
                "PROJECT_ACCEPTED_SWAP_POSTCONDITION_FAILED",
                "The accepted archive and pointer did not commit atomically.",
                status="FAIL",
            )
            resolved_prior = prior.resolve()
            resolved_prior.relative_to(root)
            require(
                resolved_prior.name == f".accepted-prior-{decision_id}",
                "PROJECT_ACCEPTED_RETENTION_PATH_INVALID",
                "The prior accepted retention target escaped its exact transaction path.",
                status="BLOCKED",
            )
            shutil.rmtree(resolved_prior)
            if staging_parent.is_dir() and not any(staging_parent.iterdir()):
                staging_parent.rmdir()
            journal["state"] = "COMMITTED_PRIOR_ACCEPTED_PURGED_AFTER_VERIFICATION"
            journal["prior_accepted_artifact_purged"] = True
            atomic_write_json(journal_path, journal)
            receipt = {
                "schema": "evidence-lane.pv-promotion.receipt.v2",
                "decision_id": decision_id,
                "decision": "APPROVE",
                "candidate_id": candidate_id,
                "accepted_pv": proposed_pv,
                "manifest_sha256": archive_manifest_sha256,
                "archive_sha256": archive_validation["archive_sha256"],
                "archive_filename": final_archive_name,
                "live_working_identity_preserved": True,
                "candidate_directory_created": False,
                "accepted_artifact_count": 1,
                "prior_accepted_artifact_purged_after_new_archive_verified": True,
                "decided_by": decided_by,
                "pointer_generation_before": before.generation,
                "pointer_generation_after": after.generation,
                "postseal_acceptance": postseal_receipt_validation,
                "decided_at": utc_now(),
            }
            receipt_path = root / "receipts" / f"{decision_id}.json"
            atomic_write_json(receipt_path, receipt)
        return {
            "accepted": self.validate_accepted(project_id, proposed_pv),
            "pointer": self.pointer(project_id).as_dict(),
            "receipt": receipt,
        }

    def record_nonpromotion_decision(
        self,
        project_id: str,
        *,
        candidate_id: str,
        decision_id: str,
        decision: str,
        decided_by: str,
        reason: str | None,
        correction_delta: str | None,
        research_question: str | None,
    ) -> dict[str, Any]:
        candidate_validation = self.candidate_validation(
            project_id,
            candidate_id,
            require_promotable=False,
        )
        pointer = self.pointer(project_id)
        receipt = {
            "schema": "evidence-lane.hil-decision.receipt.v1",
            "decision_id": decision_id,
            "decision": decision,
            "candidate_id": candidate_id,
            "decided_by": decided_by,
            "reason": reason,
            "correction_delta": correction_delta,
            "research_question": research_question,
            "pointer_generation_before": pointer.generation,
            "pointer_generation_after": pointer.generation,
            "accepted_pv_retained": pointer.accepted_pv,
            "candidate_manifest_sha256": candidate_validation["manifest_sha256"],
            "candidate_package_sha256": candidate_validation["package_sha256"],
            "candidate_promotable": candidate_validation["promotable"],
            "candidate_integrity_validated": True,
            "decided_at": utc_now(),
        }
        path = self.project_root(project_id) / "receipts" / f"{decision_id}.json"
        with self._lock(project_id):
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "HIL_DECISION_ID_CONFLICT",
                    "The HIL decision ID already binds different evidence.",
                    status="BLOCKED",
                )
                return existing
            atomic_write_json(path, receipt)
        return receipt

    def rollback(
        self,
        project_id: str,
        *,
        target_pv: str,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
        default_entry_target_used: bool,
        entry_pv: str | None,
        candidate_id: str | None,
        freshness: dict[str, Any],
        resolution_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        accepted_index = self.accepted_ids(project_id)
        require(
            target_pv in accepted_index,
            "ROLLBACK_TARGET_NOT_ACCEPTED",
            "Rollback may target only an immutable accepted PV in this project.",
            status="BLOCKED",
            target_pv=target_pv,
            accepted=accepted_index,
        )
        target_validation = self.validate_accepted(
            project_id,
            target_pv,
            require_promotable=False,
        )
        with self._lock(project_id):
            before = self.pointer(project_id)
            require(
                before.generation == expected_pointer_generation,
                "ROLLBACK_POINTER_COMPARE_AND_SWAP_FAILED",
                "The accepted pointer changed after the rollback target was verified.",
                status="STALE",
                expected_generation=expected_pointer_generation,
                actual_generation=before.generation,
            )
            pointer_moved = before.accepted_pv != target_pv
            if pointer_moved:
                after = ActivePointer(
                    project_id=project_id,
                    accepted_pv=target_pv,
                    accepted_manifest_sha256=target_validation["manifest_sha256"],
                    generation=before.generation + 1,
                    prior_generation=before.generation,
                    updated_at=utc_now(),
                )
                atomic_write_json(
                    self.project_root(project_id) / "active_pointer.json",
                    {"schema": POINTER_SCHEMA, **after.as_dict()},
                )
            else:
                after = before
            receipt = {
                "schema": "evidence-lane.rollback.receipt.v1",
                "decision_id": decision_id,
                "decision": "ROLLBACK",
                "decided_by": decided_by,
                "requested_target": target_pv,
                "resolved_target": target_pv,
                "default_entry_target_used": default_entry_target_used,
                "session_entry_pv": entry_pv,
                "resolution_reference": resolution_reference,
                "candidate_preserved_unaccepted": candidate_id,
                "pointer_before": before.as_dict(),
                "pointer_after": after.as_dict(),
                "pointer_moved": pointer_moved,
                "target_manifest_sha256": target_validation["manifest_sha256"],
                "target_package_sha256": target_validation["package_sha256"],
                "accepted_ordinal_index": accepted_index,
                "highest_accepted_ordinal": self.highest_accepted_ordinal(project_id),
                "history_preserved": True,
                "live_source_rewritten": False,
                "freshness_after_state_travel": freshness,
                "decided_at": utc_now(),
            }
            path = self.project_root(project_id) / "receipts" / f"{decision_id}.json"
            if path.exists():
                existing = json.loads(path.read_text(encoding="utf-8"))
                require(
                    existing == receipt,
                    "ROLLBACK_DECISION_ID_CONFLICT",
                    "The rollback decision ID already binds different evidence.",
                    status="BLOCKED",
                )
            else:
                atomic_write_json(path, receipt)
        return {
            "status": "PASS",
            "pointer": self.pointer(project_id).as_dict(),
            "receipt": receipt,
            "pointer_moved": pointer_moved,
            "candidate_promoted": False,
        }

    def rollback_state_catalog(self, project_id: str) -> dict[str, Any]:
        """Return Plan-stamped full-PV and sub-PV logical rollback states.

        External live-root projects never consult the rotating accepted ZIP for
        ordinary navigation. Full-PV Overlay transitions are comparison evidence;
        Plan acceptance stamps remain the acceptance authority. Sub-PVs are exact
        auto-accepted Plan rows and intentionally have no Project Overlay entry.
        """

        require(
            self.uses_external_project_authority(project_id),
            "ROLLBACK_LIVE_ROOT_PROJECT_REQUIRED",
            "Plan-stamped logical rollback applies only to live-root projects.",
            status="BLOCKED",
        )
        root = self.project_root(project_id)
        pointer = self.pointer(project_id)
        backlog = self._load_backlog(project_id)
        full_states: dict[str, dict[str, Any]] = {}
        if pointer.accepted_pv:
            full_states[pointer.accepted_pv] = {
                "state_ref": pointer.accepted_pv,
                "state_kind": "FULL_PV",
                "acceptance_authority": "ACCEPTED_POINTER_BASELINE",
                "acceptance_receipt_sha256": pointer.accepted_manifest_sha256,
                "pointer_generation_at_acceptance": pointer.generation,
            }
        for task in backlog.get("tasks", []):
            stamp = task.get("dual_hil_acceptance_stamp")
            if not isinstance(stamp, dict) or stamp.get("status") != "PASS":
                continue
            target = str(stamp.get("target_pv") or "").upper()
            if target:
                full_states[target] = {
                    "state_ref": target,
                    "state_kind": "FULL_PV",
                    "acceptance_authority": "PLAN_DUAL_HIL_ACCEPTANCE_STAMP",
                    "acceptance_receipt_sha256": stamp.get("receipt_sha256"),
                    "plan_task_id": task.get("task_id"),
                    "plan_row_number": task.get("sequence"),
                    "pointer_generation_at_acceptance": stamp.get(
                        "pointer_generation"
                    ),
                }
        sub_states = [
            {
                "state_ref": str(row["sub_pv_id"]),
                "state_kind": "SUB_PV_DELTA",
                "acceptance_authority": "PLAN_SQLITE_SUB_PV_ACCEPTANCE",
                "acceptance_receipt_sha256": row.get("receipt_sha256"),
                "baseline_pv": row.get("baseline_pv"),
                "target_project_pv": row.get("target_project_pv"),
                "plan_task_id": row.get("task_id"),
                "successor_task_id": row.get("successor_task_id"),
                "plan_row_number": row.get("delta_row_number"),
                "pointer_generation_at_acceptance": row.get("pointer_generation"),
            }
            for row in backlog.get("sub_pv_acceptances", [])
            if row.get("state") == "AUTO_ACCEPTED_DELTA_ROW_WORK"
            and row.get("usable_by_successor") is True
        ]
        overlay_path = root / "project_overlay" / "project_overlay.sqlite"
        overlay_transitions: list[dict[str, Any]] = []
        if overlay_path.is_file():
            connection = sqlite3.connect(
                f"file:{overlay_path.as_posix()}?mode=ro", uri=True
            )
            connection.row_factory = sqlite3.Row
            try:
                overlay_transitions = [
                    {
                        "transition_id": str(row["transition_id"]),
                        "parent_pv": str(row["parent_pv"] or ""),
                        "proposed_pv": str(row["proposed_pv"]),
                        "truth_state": str(row["truth_state"]),
                        "transition_sha256": str(row["transition_sha256"]),
                    }
                    for row in connection.execute(
                        "SELECT transition_id,parent_pv,proposed_pv,truth_state,"
                        "transition_sha256 FROM pv_hil_transition ORDER BY sequence"
                    ).fetchall()
                ]
            finally:
                connection.close()
        states = [
            {
                **row,
                "project_overlay_applicable": row["state_kind"] == "FULL_PV",
                "hard_restore_requires_user_supplied_full_pv_zip": (
                    row["state_kind"] == "FULL_PV"
                ),
            }
            for row in [*full_states.values(), *sub_states]
        ]
        core = {
            "schema": "evidence-lane.rollback-state-catalog.v1",
            "status": "PASS",
            "project_id": project_id,
            "accepted_pointer": pointer.as_dict(),
            "state_count": len(states),
            "states": states,
            "full_pv_state_count": len(full_states),
            "sub_pv_state_count": len(sub_states),
            "project_overlay": {
                "state": "AVAILABLE" if overlay_path.is_file() else "ABSENT",
                "database_sha256": (
                    sha256_file(overlay_path) if overlay_path.is_file() else None
                ),
                "transition_count": len(overlay_transitions),
                "transitions": overlay_transitions,
                "sub_pv_overlay_claimed": False,
            },
            "ordinary_query_authority": "LIVE_ROOT_PLAN_LANES_AND_OVERLAY",
            "accepted_folder_queried": False,
            "accepted_zip_opened": False,
            "hard_restore_route": (
                "SEPARATE_EXPLICIT_USER_SELECTED_FULL_PV_ZIP_TRANSACTION"
            ),
        }
        return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}

    def rollback_live_root_state(
        self,
        project_id: str,
        *,
        target_state_ref: str,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
        candidate_id: str | None,
        resolution_reference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move one logical rollback cursor without rewriting source or accepted truth."""

        catalog = self.rollback_state_catalog(project_id)
        target = next(
            (
                row
                for row in catalog["states"]
                if row["state_ref"] == target_state_ref
            ),
            None,
        )
        require(
            isinstance(target, dict),
            "ROLLBACK_STATE_REF_NOT_ACCEPTED",
            "Logical rollback may target only a Plan-stamped full PV or sub-PV.",
            status="BLOCKED",
            target_state_ref=target_state_ref,
        )
        target = cast(dict[str, Any], target)
        root = self.project_root(project_id)
        cursor_path = root / "rollback_state_cursor.json"
        with self._lock(project_id):
            pointer = self.pointer(project_id)
            require(
                pointer.generation == expected_pointer_generation,
                "ROLLBACK_POINTER_COMPARE_AND_SWAP_FAILED",
                "The accepted pointer changed after rollback target verification.",
                status="STALE",
                expected_generation=expected_pointer_generation,
                actual_generation=pointer.generation,
            )
            before = (
                json.loads(cursor_path.read_text(encoding="utf-8"))
                if cursor_path.is_file()
                else {
                    "state_ref": pointer.accepted_pv,
                    "state_kind": "FULL_PV",
                    "source": "CURRENT_ACCEPTED_POINTER",
                }
            )
            cursor_core = {
                "schema": "evidence-lane.rollback-state-cursor.v1",
                "project_id": project_id,
                "state_ref": target["state_ref"],
                "state_kind": target["state_kind"],
                "acceptance_authority": target["acceptance_authority"],
                "acceptance_receipt_sha256": target[
                    "acceptance_receipt_sha256"
                ],
                "selected_by": decided_by,
                "selected_at": utc_now(),
                "accepted_pointer_generation": pointer.generation,
                "accepted_pointer_moved": False,
                "live_source_rewritten": False,
            }
            cursor = {
                **cursor_core,
                "cursor_sha256": sha256_bytes(canonical_json_bytes(cursor_core)),
            }
            atomic_write_json(cursor_path, cursor)
            receipt = {
                "schema": "evidence-lane.rollback.receipt.v2",
                "status": "PASS",
                "decision_id": decision_id,
                "decision": "ROLLBACK_LOGICAL_STATE",
                "decided_by": decided_by,
                "requested_target": target_state_ref,
                "resolved_target": target,
                "resolution_reference": resolution_reference,
                "candidate_preserved_unaccepted": candidate_id,
                "cursor_before": before,
                "cursor_after": cursor,
                "logical_cursor_moved": before.get("state_ref")
                != cursor["state_ref"],
                "accepted_pointer": pointer.as_dict(),
                "accepted_pointer_moved": False,
                "live_source_rewritten": False,
                "accepted_folder_queried": False,
                "accepted_zip_opened": False,
                "project_overlay_applicable": target[
                    "project_overlay_applicable"
                ],
                "hard_restore_performed": False,
                "hard_restore_requires": (
                    "EXPLICIT_USER_SELECTED_MATCHING_FULL_PV_ZIP"
                    if target["state_kind"] == "FULL_PV"
                    else "UNAVAILABLE_FOR_SUB_PV"
                ),
                "decided_at": utc_now(),
            }
            receipt["receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(receipt)
            )
            receipt_path = root / "receipts" / "rollback" / f"{decision_id}.json"
            if receipt_path.is_file():
                require(
                    json.loads(receipt_path.read_text(encoding="utf-8")) == receipt,
                    "ROLLBACK_DECISION_ID_CONFLICT",
                    "The rollback decision ID already binds different evidence.",
                    status="BLOCKED",
                )
            else:
                atomic_write_json(receipt_path, receipt)
        return {
            "status": "PASS",
            "state": "LOGICAL_ROLLBACK_CURSOR_SELECTED",
            "catalog_receipt_sha256": catalog["receipt_sha256"],
            "cursor": cursor,
            "receipt": receipt,
            "pointer": self.pointer(project_id).as_dict(),
            "pointer_moved": False,
            "candidate_promoted": False,
        }

    @staticmethod
    def _plan_task_contains_exact_value(task: dict[str, Any], value: str) -> bool:
        encoded = canonical_json_bytes(task).decode("utf-8")
        escaped = re.escape(value)
        return re.search(
            rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
            encoded,
            flags=re.IGNORECASE,
        ) is not None

    def _seal_rollback_replan_gate(
        self,
        *,
        project_root: Path,
        project_id: str,
        target_plan_task_id: str,
        decision_id: str,
        rollback_mode: str,
        selected_by: str,
        target_pv: str | None = None,
        target_commit_sha: str | None = None,
    ) -> dict[str, Any]:
        """Preserve Plan history and make every later executable row inert."""

        backlog_path = resolved_plan_backlog_path(project_root)
        require(
            backlog_path.is_file(),
            "ROLLBACK_PLAN_AUTHORITY_MISSING",
            "Hard rollback requires the exact canonical Plan backlog.",
            status="MISMATCH",
        )
        backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
        require(
            backlog.get("schema") == "evidence-lane.linear-task-backlog.v1"
            and backlog.get("project_id") == project_id,
            "ROLLBACK_PLAN_AUTHORITY_MISMATCH",
            "The rollback target Plan belongs to another project or schema.",
            status="MISMATCH",
        )
        tasks = {
            str(task.get("task_id")): task
            for task in backlog.get("tasks", [])
            if isinstance(task, dict)
        }
        target = tasks.get(target_plan_task_id)
        require(
            isinstance(target, dict),
            "ROLLBACK_PLAN_TARGET_TASK_MISSING",
            "Hard rollback requires one exact Plan-stamped target row.",
            status="BLOCKED",
            target_plan_task_id=target_plan_task_id,
        )
        target = cast(dict[str, Any], target)
        if target_commit_sha is not None:
            require(
                self._plan_task_contains_exact_value(target, target_commit_sha),
                "ROLLBACK_PLAN_COMMIT_STAMP_MISMATCH",
                "The selected Plan row does not stamp the exact Git commit.",
                status="MISMATCH",
                target_plan_task_id=target_plan_task_id,
                target_commit_sha=target_commit_sha,
            )
        if target_pv is not None:
            stamp = target.get("dual_hil_acceptance_stamp")
            stamp_matches = bool(
                isinstance(stamp, dict)
                and str(stamp.get("target_pv") or "").upper() == target_pv.upper()
            )
            require(
                stamp_matches
                or self._plan_task_contains_exact_value(target, target_pv),
                "ROLLBACK_PLAN_PV_STAMP_MISMATCH",
                "The selected Plan row does not stamp the exact full PV.",
                status="MISMATCH",
                target_plan_task_id=target_plan_task_id,
                target_pv=target_pv,
            )
        target_sequence = int(target["sequence"])
        request = {
            "project_id": project_id,
            "decision_id": decision_id,
            "rollback_mode": rollback_mode,
            "target_plan_task_id": target_plan_task_id,
            "target_sequence": target_sequence,
            "target_pv": target_pv,
            "target_commit_sha": target_commit_sha,
            "selected_by": selected_by,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request))
        existing_gates = list(backlog.get("rollback_replan_gates") or [])
        replay = next(
            (
                row
                for row in existing_gates
                if isinstance(row, dict) and row.get("decision_id") == decision_id
            ),
            None,
        )
        if replay is not None:
            require(
                replay.get("request_sha256") == request_sha256,
                "ROLLBACK_REPLAN_GATE_REPLAY_CONFLICT",
                "The rollback decision already binds another Plan target.",
                status="BLOCKED",
            )
            return cast(dict[str, Any], replay)
        before_sha256 = sha256_bytes(canonical_json_bytes(backlog))
        superseded: list[str] = []
        already_non_executable: list[str] = []
        for task in backlog.get("tasks", []):
            if not isinstance(task, dict) or int(task.get("sequence") or 0) <= target_sequence:
                continue
            task_id = str(task["task_id"])
            status = str(task.get("status") or "")
            if status in {"ACTIVE", "QUEUED"}:
                event_id = (
                    f"{decision_id}__supersede__"
                    f"{sha256_bytes(task_id.encode('utf-8'))[:16].lower()}"
                )
                event = append_delta_event(
                    backlog,
                    task_id=task_id,
                    event_type="SUPERSEDED",
                    to_status="SUPERSEDED",
                    actor=selected_by,
                    event_id=event_id,
                    assume_initialized=True,
                    details={
                        "rollback_mode": rollback_mode,
                        "rollback_decision_id": decision_id,
                        "target_plan_task_id": target_plan_task_id,
                        "fresh_evi_plan_required": True,
                        "history_preserved": True,
                    },
                )
                task.setdefault("history", []).append(
                    {
                        "event": "SUPERSEDED_BY_HARD_ROLLBACK_REPLAN_GATE",
                        "event_id": event["event_id"],
                        "recorded_at": event["recorded_at"],
                        "rollback_mode": rollback_mode,
                        "rollback_decision_id": decision_id,
                    }
                )
                superseded.append(task_id)
            else:
                already_non_executable.append(task_id)
        gate_body = {
            "schema": "evidence-lane.rollback-replan-gate.v1",
            "status": "PASS",
            **request,
            "request_sha256": request_sha256,
            "history_preserved_through_target": True,
            "later_executable_rows_superseded": superseded,
            "later_rows_already_non_executable": already_non_executable,
            "fresh_user_brief_required": True,
            "fresh_evi_plan_required": True,
            "explicit_plan_acceptance_required": True,
            "new_goal_before_work_required": True,
            "old_plan_continuation_allowed": False,
            "candidate_created": False,
            "hil_inferred": False,
            "accepted_pointer_moved": False,
            "sealed_at": utc_now(),
        }
        gate = {
            **gate_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(gate_body)),
        }
        backlog.setdefault("rollback_replan_gates", []).append(gate)
        ensure_event_ledger(backlog)
        atomic_write_json(backlog_path, backlog)
        write_plan_runtime_projection(
            resolved_plan_runtime_path(project_root),
            backlog,
        )
        refresh_working_sector_operational_checksums(
            project_root,
            authority="PLAN",
        )
        gate["backlog_sha256_before"] = before_sha256
        gate["backlog_sha256_after"] = sha256_bytes(canonical_json_bytes(backlog))
        return gate

    def hard_restore_live_root(
        self,
        project_id: str,
        *,
        archive_path: str | Path,
        expected_archive_sha256: str,
        restore_root: str | Path,
        target_plan_task_id: str,
        expected_pointer_generation: int,
        selected_by: str,
        decision_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Restore a user-selected full-PV ZIP into one fresh Project/PV root."""

        require(
            confirmation == "RESTORE_EXACT_ACCEPTED_ZIP_AND_REQUIRE_NEW_PLAN",
            "HARD_ROLLBACK_CONFIRMATION_REQUIRED",
            "Hard restore requires its exact destructive-boundary confirmation.",
            status="BLOCKED",
        )
        current_root = self.project_root(project_id)
        require(
            current_root != self._legacy_project_root(project_id),
            "HARD_ROLLBACK_EXTERNAL_PROJECT_REQUIRED",
            "Hard restore requires the external Project/PV authority model.",
            status="BLOCKED",
        )
        pointer_before = self.pointer(project_id)
        require(
            pointer_before.generation == expected_pointer_generation,
            "HARD_ROLLBACK_POINTER_COMPARE_AND_SWAP_FAILED",
            "The accepted pointer changed before hard restore.",
            status="STALE",
        )
        archive = Path(archive_path).expanduser().resolve()
        expected_sha256 = str(expected_archive_sha256 or "").upper()
        require(
            re.fullmatch(r"[A-F0-9]{64}", expected_sha256) is not None
            and archive.is_file()
            and sha256_file(archive) == expected_sha256,
            "HARD_ROLLBACK_ARCHIVE_IDENTITY_MISMATCH",
            "The user-selected accepted ZIP failed its exact SHA-256 binding.",
            status="MISMATCH",
            archive=str(archive),
        )
        validation = validate_project_pv_archive(archive)
        target_pv = str(validation.get("pv_id") or "")
        require(
            validation.get("project_id") == project_id
            and re.fullmatch(r"PV[1-9][0-9]*", target_pv) is not None,
            "HARD_ROLLBACK_ARCHIVE_PROJECT_MISMATCH",
            "The selected ZIP is not one full PV for this project.",
            status="MISMATCH",
        )
        target = validate_external_project_authority_root(
            restore_root,
            control_root=self.root,
            project_id=project_id,
        )
        require(
            target != current_root and not target.exists(),
            "HARD_ROLLBACK_RESTORE_ROOT_NOT_FRESH",
            "Hard restore requires one absent user-selected Project/PV root.",
            status="BLOCKED",
            current_root=str(current_root),
            restore_root=str(target),
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with materialized_project_pv_archive(archive) as materialized:
                shutil.copytree(materialized, target)
            archived_pointer = dict(validation.get("pointer") or {})
            require(
                archived_pointer.get("project_id") == project_id
                and archived_pointer.get("accepted_pv") == target_pv,
                "HARD_ROLLBACK_ARCHIVE_POINTER_MISMATCH",
                "The selected full-PV ZIP does not bind its own accepted pointer.",
                status="MISMATCH",
            )
            pointer_after = ActivePointer(
                project_id=project_id,
                accepted_pv=target_pv,
                accepted_manifest_sha256=str(
                    validation["archive_manifest_sha256"]
                ),
                generation=int(archived_pointer.get("generation") or 0),
                prior_generation=(
                    int(archived_pointer["prior_generation"])
                    if archived_pointer.get("prior_generation") is not None
                    else None
                ),
                updated_at=utc_now(),
            )
            atomic_write_json(
                target / "active_pointer.json",
                {"schema": POINTER_SCHEMA, **pointer_after.as_dict()},
            )
            accepted = target / "accepted"
            accepted.mkdir(parents=True, exist_ok=False)
            restored_archive = (
                accepted
                / f"{target_pv}__{validation['archive_manifest_sha256']}.zip"
            )
            shutil.copy2(archive, restored_archive)
            config = self.config(project_id)
            project_path = target / "project.json"
            project_payload = json.loads(project_path.read_text(encoding="utf-8"))
            project_payload["project_authority_root"] = str(target)
            atomic_write_json(project_path, project_payload)
            replan_gate = self._seal_rollback_replan_gate(
                project_root=target,
                project_id=project_id,
                target_plan_task_id=target_plan_task_id,
                decision_id=decision_id,
                rollback_mode="HARD_ACCEPTED_ZIP_RESTORE",
                selected_by=selected_by,
                target_pv=target_pv,
            )
            layout_path = target / "project_authority.json"
            require(
                layout_path.is_file(),
                "HARD_ROLLBACK_PROJECT_AUTHORITY_LAYOUT_MISSING",
                "The selected full-PV ZIP lacks its project-authority layout.",
                status="MISMATCH",
            )
            layout = json.loads(layout_path.read_text(encoding="utf-8"))
            layout.pop("layout_sha256", None)
            layout.update(
                {
                    "resolved_project_root": str(target),
                    "repository_path": config.repository_path,
                    "host_control_root": str(self.root),
                    "runtime_separated": True,
                    "accepted_pointer": {
                        "accepted_pv": target_pv,
                        "generation": pointer_after.generation,
                        "accepted_manifest_sha256": validation[
                            "archive_manifest_sha256"
                        ],
                        "moved": pointer_before.accepted_pv != target_pv,
                    },
                    "hard_restore_source_root": str(current_root),
                    "hard_restore_decision_id": decision_id,
                }
            )
            layout["layout_sha256"] = sha256_bytes(canonical_json_bytes(layout))
            atomic_write_json(layout_path, layout)
            projected_members = {
                path.relative_to(target).as_posix(): sha256_file(path)
                for path in sorted(target.rglob("*"))
                if path.is_file()
                and path.name != "PROJECT_AUTHORITY_MANIFEST.json"
                and (
                    path.name.startswith("project_authority")
                    or path.name
                    in {
                        "authority.ref.json",
                        "historical_authority.ref.json",
                        "operational-authority.ref.json",
                        "study_brain.json",
                        "legacy_history.ref.json",
                    }
                )
            }
            authority_manifest_body = {
                "schema": "evidence-lane.project-authority-projection-manifest.v1",
                "project_id": project_id,
                "member_count": len(projected_members),
                "members": projected_members,
                "canonical_lane_count": len(CANONICAL_LANE_IDS),
                "layout_sha256": layout["layout_sha256"],
            }
            atomic_write_json(
                target / "PROJECT_AUTHORITY_MANIFEST.json",
                {
                    **authority_manifest_body,
                    "manifest_sha256": sha256_bytes(
                        canonical_json_bytes(authority_manifest_body)
                    ),
                },
            )
            receipt_body = {
                "schema": "evidence-lane.hard-rollback-receipt.v1",
                "status": "PASS",
                "decision_id": decision_id,
                "project_id": project_id,
                "selected_by": selected_by,
                "archive_path": str(archive),
                "archive_sha256": expected_sha256,
                "archive_manifest_sha256": validation[
                    "archive_manifest_sha256"
                ],
                "target_pv": target_pv,
                "prior_project_root": str(current_root),
                "restored_project_root": str(target),
                "restored_archive": str(restored_archive),
                "pointer_before": pointer_before.as_dict(),
                "pointer_after": pointer_after.as_dict(),
                "replan_gate_sha256": replan_gate["receipt_sha256"],
                "layout_sha256": layout["layout_sha256"],
                "fresh_user_brief_required": True,
                "fresh_evi_plan_required": True,
                "old_plan_continuation_allowed": False,
                "current_dirty_workspace_reset": False,
                "candidate_created": False,
                "hil_inferred": False,
                "completed_at": utc_now(),
            }
            receipt = {
                **receipt_body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
            }
            atomic_write_json(
                target / "receipts" / "rollback" / f"{decision_id}.json",
                receipt,
            )
            with self._registry_lock():
                registry = self._load_root_registry_unlocked()
                row = registry.get("projects", {}).get(project_id)
                require(
                    isinstance(row, dict),
                    "HARD_ROLLBACK_REGISTRY_BINDING_MISSING",
                    "The project registry binding disappeared during hard restore.",
                    status="MISMATCH",
                )
                row["project_authority_root"] = str(target)
                row["project_authority_root_sha256"] = sha256_bytes(
                    str(target).encode("utf-8")
                )
                row["project_authority_mode"] = "EXPLICIT_USER_PROJECT_ROOT"
                atomic_write_json(self._registry_path(), registry)
            require(
                self.project_root(project_id) == target,
                "HARD_ROLLBACK_POST_BINDING_MISMATCH",
                "The hidden registry did not bind the restored Project/PV root.",
                status="FAIL",
            )
            return {
                "status": "PASS",
                "state": "HARD_RESTORE_COMPLETE_REPLAN_REQUIRED",
                "receipt": receipt,
                "pointer": pointer_after.as_dict(),
                "pointer_moved": pointer_before.accepted_pv != target_pv,
                "replan_required": True,
                "candidate_promoted": False,
            }
        except Exception:
            if target.exists() and self.project_root(project_id) != target:
                _remove_tree_with_retry(
                    target,
                    operation="ROLLBACK_FAILED_HARD_RESTORE_TARGET",
                )
            raise

    def git_restore_live_root(
        self,
        project_id: str,
        *,
        repository_path: str | Path,
        branch: str,
        commit_sha: str,
        restore_workspace: str | Path,
        target_plan_task_id: str,
        expected_pointer_generation: int,
        selected_by: str,
        decision_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        """Restore one exact commit into a fresh workspace and rebuild Local Code."""

        require(
            confirmation
            == "RESTORE_EXACT_GIT_COMMIT_TO_FRESH_WORKSPACE_AND_REQUIRE_NEW_PLAN",
            "GIT_ROLLBACK_CONFIRMATION_REQUIRED",
            "Git restore requires its exact fresh-workspace confirmation.",
            status="BLOCKED",
        )
        root = self.project_root(project_id)
        config = self.config(project_id)
        repository = Path(repository_path).expanduser().resolve()
        configured_repository = Path(config.repository_path).expanduser().resolve()
        exact_branch = str(branch or "").strip()
        exact_commit = str(commit_sha or "").strip().lower()
        require(
            repository == configured_repository
            and (repository / ".git").exists()
            and exact_branch in config.allowed_branches
            and re.fullmatch(r"(?!-)(?!.*\.\.)[A-Za-z0-9._/-]+", exact_branch)
            is not None
            and re.fullmatch(r"[0-9a-f]{40}", exact_commit) is not None,
            "GIT_ROLLBACK_SOURCE_IDENTITY_INVALID",
            "Git restore requires the exact registered repository, allowed branch, and full commit SHA.",
            status="BLOCKED",
        )
        pointer = self.pointer(project_id)
        require(
            pointer.generation == expected_pointer_generation,
            "GIT_ROLLBACK_POINTER_COMPARE_AND_SWAP_FAILED",
            "The accepted pointer changed before Git restore.",
            status="STALE",
        )
        workspace = Path(restore_workspace).expanduser().resolve()
        require(
            workspace.is_absolute()
            and not workspace.exists()
            and workspace != repository
            and workspace != root
            and not workspace.is_relative_to(repository)
            and not workspace.is_relative_to(root)
            and not workspace.is_relative_to(self.root),
            "GIT_ROLLBACK_WORKSPACE_NOT_FRESH",
            "Git restore requires one absent user-selected workspace outside current authorities.",
            status="BLOCKED",
            restore_workspace=str(workspace),
        )

        def git(*arguments: str, cwd: Path = repository) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [resolve_git_executable(repository), "-C", str(cwd), *arguments],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        branch_commit = git("rev-parse", f"{exact_branch}^{{commit}}").stdout.strip().lower()
        git("cat-file", "-e", f"{exact_commit}^{{commit}}")
        git("merge-base", "--is-ancestor", exact_commit, exact_branch)
        require(
            bool(branch_commit),
            "GIT_ROLLBACK_BRANCH_UNRESOLVED",
            "The selected branch has no exact commit authority.",
            status="MISMATCH",
        )
        workspace.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                resolve_git_executable(repository),
                "clone",
                "--no-hardlinks",
                "--no-checkout",
                str(repository),
                str(workspace),
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            git("checkout", "-B", exact_branch, exact_commit, cwd=workspace)
            restored_head = git("rev-parse", "HEAD", cwd=workspace).stdout.strip().lower()
            status = git("status", "--porcelain=v1", cwd=workspace).stdout
            require(
                restored_head == exact_commit and not status.strip(),
                "GIT_ROLLBACK_WORKSPACE_VERIFICATION_FAILED",
                "The fresh workspace does not contain the exact clean commit.",
                status="FAIL",
            )
            from .lane_engine import build_lane_bundle, validate_lane_bundle
            from .lanes import route_source

            tracked = [
                value
                for value in git("ls-files", "-z", cwd=workspace).stdout.split("\0")
                if value
            ]
            code_paths = [
                path
                for path in tracked
                if route_source(path, code_mode="local_code") == "local_code"
            ]
            require(
                bool(code_paths),
                "GIT_ROLLBACK_LOCAL_CODE_SOURCE_EMPTY",
                "The selected Git commit contains no Local Code lane sources.",
                status="MISMATCH",
            )
            with tempfile.TemporaryDirectory(
                prefix=f".{project_id}.git-rollback-",
                dir=root.parent,
            ) as temporary:
                stage = Path(temporary) / "sectors"
                build = build_lane_bundle(
                    repository_root=workspace,
                    output_directory=stage,
                    code_mode="local_code",
                    parent_lane_bundle=root / "sectors",
                    parent_pv=pointer.accepted_pv,
                    proposed_pv=pointer.accepted_pv or "PV0",
                    pointer_generation=pointer.generation,
                    source_overrides={path: "local_code" for path in code_paths},
                    git_mode="REQUIRED",
                    include_untracked=False,
                    materialize_all_lanes=True,
                    source_paths_override=code_paths,
                    preserve_parent_unmentioned=True,
                    index_git_history=True,
                    allow_parent_operational_authority_drift=True,
                )
                validation = validate_lane_bundle(stage)
                require(
                    validation.get("valid") is True,
                    "GIT_ROLLBACK_LOCAL_CODE_REBUILD_INVALID",
                    "The restored Git commit failed Project Sector validation.",
                    status="FAIL",
                )
                current_sectors = root / "sectors"
                backup = root / f".sectors-before-{decision_id}"
                require(
                    current_sectors.is_dir() and not backup.exists(),
                    "GIT_ROLLBACK_SECTOR_SWAP_CONFLICT",
                    "The live Project Sector swap boundary is not clean.",
                    status="BLOCKED",
                )
                _replace_path_with_retry(
                    current_sectors,
                    backup,
                    operation="GIT_ROLLBACK_PRESERVE_PRIOR_SECTORS",
                )
                try:
                    _replace_path_with_retry(
                        stage,
                        current_sectors,
                        operation="GIT_ROLLBACK_PUBLISH_REBUILT_SECTORS",
                    )
                    published_validation = validate_lane_bundle(current_sectors)
                    require(
                        published_validation.get("valid") is True,
                        "GIT_ROLLBACK_PUBLISHED_SECTORS_INVALID",
                        "Published Project Sectors failed post-swap validation.",
                        status="FAIL",
                    )
                except Exception:
                    if current_sectors.exists():
                        _remove_tree_with_retry(
                            current_sectors,
                            operation="GIT_ROLLBACK_REMOVE_FAILED_SECTORS",
                        )
                    _replace_path_with_retry(
                        backup,
                        current_sectors,
                        operation="GIT_ROLLBACK_RESTORE_PRIOR_SECTORS",
                    )
                    raise
                _remove_tree_with_retry(
                    backup,
                    operation="GIT_ROLLBACK_REMOVE_VERIFIED_PRIOR_SECTORS",
                )
            replan_gate = self._seal_rollback_replan_gate(
                project_root=root,
                project_id=project_id,
                target_plan_task_id=target_plan_task_id,
                decision_id=decision_id,
                rollback_mode="GIT_BRANCH_COMMIT_RESTORE",
                selected_by=selected_by,
                target_commit_sha=exact_commit,
            )
            project_path = root / "project.json"
            project_payload = json.loads(project_path.read_text(encoding="utf-8"))
            project_payload["repository_path"] = str(workspace)
            atomic_write_json(project_path, project_payload)
            with self._registry_lock():
                registry = self._load_root_registry_unlocked()
                row = registry.get("projects", {}).get(project_id)
                require(
                    isinstance(row, dict),
                    "GIT_ROLLBACK_REGISTRY_BINDING_MISSING",
                    "The project registry binding disappeared during Git restore.",
                    status="MISMATCH",
                )
                row["repository_path"] = str(workspace)
                row["rollback_source_repository_path"] = str(repository)
                row["rollback_source_branch"] = exact_branch
                row["rollback_source_commit_sha"] = exact_commit
                atomic_write_json(self._registry_path(), registry)
            receipt_body = {
                "schema": "evidence-lane.git-rollback-receipt.v1",
                "status": "PASS",
                "decision_id": decision_id,
                "project_id": project_id,
                "selected_by": selected_by,
                "source_repository": str(repository),
                "source_branch": exact_branch,
                "source_branch_head_at_restore": branch_commit,
                "source_commit_sha": exact_commit,
                "restored_workspace": str(workspace),
                "restored_head_sha": restored_head,
                "worktree_clean": True,
                "local_code_source_count": len(code_paths),
                "sector_bundle_sha256": build["bundle_sha256"],
                "replan_gate_sha256": replan_gate["receipt_sha256"],
                "accepted_pointer": pointer.as_dict(),
                "accepted_pointer_moved": False,
                "candidate_created": False,
                "hil_inferred": False,
                "fresh_user_brief_required": True,
                "fresh_evi_plan_required": True,
                "old_plan_continuation_allowed": False,
                "dirty_current_workspace_reset": False,
                "completed_at": utc_now(),
            }
            receipt = {
                **receipt_body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
            }
            atomic_write_json(
                root / "receipts" / "rollback" / f"{decision_id}.json",
                receipt,
            )
            return {
                "status": "PASS",
                "state": "GIT_RESTORE_COMPLETE_REPLAN_REQUIRED",
                "receipt": receipt,
                "pointer": pointer.as_dict(),
                "pointer_moved": False,
                "replan_required": True,
                "candidate_promoted": False,
            }
        except Exception:
            if workspace.exists():
                _remove_tree_with_retry(
                    workspace,
                    operation="ROLLBACK_FAILED_GIT_WORKSPACE",
                )
            raise

    def project_status(self, project_id: str) -> dict[str, Any]:
        root = self.project_root(project_id)
        pointer = self.pointer(project_id)
        external_project_authority = root != self._legacy_project_root(project_id)
        accepted = (
            [pointer.accepted_pv]
            if external_project_authority and pointer.accepted_pv
            else self.accepted_ids(project_id)
        )
        if pointer.accepted_pv and pointer.accepted_pv not in accepted:
            accepted.append(pointer.accepted_pv)
            accepted.sort(key=lambda value: int(value[2:]))
        backlog = self._load_backlog(project_id)
        backlog_counts = Counter(
            str(task.get("status", "UNKNOWN")) for task in backlog["tasks"]
        )
        if root == self._legacy_project_root(project_id):
            candidates = sorted(
                path.name for path in (root / "candidates").glob("PV*") if path.is_dir()
            )
        else:
            overlay_root = root / "receipts" / "candidate-overlays"
            candidates = sorted(
                path.stem for path in overlay_root.glob("PV*.json") if path.is_file()
            )
        return {
            "project": self.config(project_id).as_dict(),
            "pointer": pointer.as_dict(),
            "accepted": accepted,
            "highest_accepted_ordinal": (
                int(pointer.accepted_pv[2:])
                if external_project_authority and pointer.accepted_pv
                else self.highest_accepted_ordinal(project_id)
            ),
            "next_candidate_pv": (
                f"PV{int(pointer.accepted_pv[2:]) + 1}"
                if external_project_authority and pointer.accepted_pv
                else "PV1"
                if external_project_authority
                else self.next_pv_id(project_id)
            ),
            "candidates": candidates,
            "task_backlog": {
                "count": len(backlog["tasks"]),
                "counts": dict(sorted(backlog_counts.items())),
                "active_task_ids": [
                    task["task_id"]
                    for task in backlog["tasks"]
                    if task.get("status") == "ACTIVE"
                ],
            },
        }
