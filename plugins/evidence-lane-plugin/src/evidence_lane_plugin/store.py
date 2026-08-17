"""Immutable local project store with compare-and-swap accepted pointers."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from .capture_routing import CaptureRouteAuthority, normalize_capture_route
from .constants import POINTER_SCHEMA, PROJECT_REGISTRY_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .models import ActivePointer, ProjectConfig
from .plan_runtime import (
    DELTA_STATUSES,
    append_delta_event,
    append_planning_mode_event,
    ensure_event_ledger,
    plan_runtime_status,
    query_plan_runtime_projection,
    write_plan_runtime_projection,
)
from .pv_package import compare_package_bytes, validate_pv_package
from .redaction import redact
from .source_authority import snapshot_source_authority_registry
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

_HOST_PLAN_WINDOW_SIZE = 10

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
                character in _PLAN_METADATA_ID_CHARS
                for character in dependency.strip()
            )
            for dependency in value
        ),
        "PLAN_DEPENDENCIES_INVALID",
        "Explicit Plan dependencies must be bounded public-safe task IDs.",
        status="MISMATCH",
        task_id=task_id,
    )
    dependencies = list(
        dict.fromkeys(dependency.strip() for dependency in value)
    )
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
        ("TASK_CONTRACT", str(task.get("task_id") or ""), task.get("requested_outcome")),
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
        ("TASK_CONTRACT", str(task.get("task_id") or ""), task.get("requested_outcome")),
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
    directives: tuple[
        tuple[str, re.Pattern[str], str, str], ...
    ] = (
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
                and all(
                    character in _PLAN_METADATA_ID_CHARS
                    for character in explicit
                ),
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
            and all(
                character in _PLAN_METADATA_ID_CHARS for character in explicit
            ),
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
            and all(
                character in _PLAN_METADATA_ID_CHARS for character in explicit
            ),
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
        unknown_dependencies = sorted(
            set(dependencies) - earlier_executable_task_ids
        )
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


def _host_plan_window_fingerprint(status: dict[str, Any]) -> dict[str, Any]:
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
    start = (
        (active_indexes[0] // _HOST_PLAN_WINDOW_SIZE) * _HOST_PLAN_WINDOW_SIZE
        if active_indexes
        else 0
    )
    window_rows = rows[start : start + _HOST_PLAN_WINDOW_SIZE]
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
            "PRESENT" in outcome
            or "DECISION" in outcome
            or "GATE" in outcome
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
            resolved.is_absolute() and resolved.is_dir() and os.access(resolved, os.R_OK | os.W_OK),
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
        return {
            "schema": "evidence-lane.project-store-route.v1",
            "status": "PASS",
            "project_id": safe,
            "canonical_comparison_key": self.canonical_project_key(safe),
            "canonical_id_policy": "ASCII_EXACT_WITH_NFKC_CASEFOLD_COLLISION_GUARD",
            "resolved_store_root": str(self.root),
            "relative_project_route": f"projects/{safe}",
            "resolved_project_root": str(root),
            "contained_beneath_store_root": True,
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

    def project_root(self, project_id: str) -> Path:
        safe = self.validate_project_id(project_id)
        self._assert_exact_project_route(safe)
        result = (self.root / "projects" / safe).resolve()
        try:
            result.relative_to(self.root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "PROJECT_ROUTE_ESCAPE",
                "The project route escaped the configured Evidence Lane store root.",
                status="BLOCKED",
                details={"project_id": safe},
            ) from exc
        return result

    def _source_authority_path(self, project_id: str) -> Path:
        """Return the project-local registry path without creating it."""

        return self.project_root(project_id) / "source_authority.sqlite"

    def source_authority_path(self, project_id: str) -> Path:
        return self._source_authority_path(project_id)

    def source_authority_status(
        self, project_id: str, *, batch_id: str | None = None
    ) -> dict[str, Any]:
        return snapshot_source_authority_registry(
            self._source_authority_path(project_id), batch_id
        )

    def _lock(self, project_id: str) -> _ProjectLock:
        return _ProjectLock(self.project_root(project_id) / ".store.lock")

    def state_travel_resume_lock(self, project_id: str) -> _ProjectLock:
        """Serialize one State Travel handoff consumption across MCP processes."""

        self.validate_project_id(project_id)
        return _ProjectLock(
            self.project_root(project_id) / ".state-travel-resume.lock"
        )

    def register_project(self, config: ProjectConfig) -> dict[str, Any]:
        self.validate_project_id(config.project_id)
        config.capture_route = normalize_capture_route(config.capture_route)
        capture_binding: dict[str, Any]
        with self._registry_lock():
            registry = self._load_root_registry()
            canonical_key = self.canonical_project_key(config.project_id)
            repository_path_hash = sha256_bytes(
                config.repository_path.encode("utf-8")
            )
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
            root = self.project_root(config.project_id)
            with self._lock(config.project_id):
                for folder in (
                    "accepted",
                    "candidates",
                    "receipts",
                    "sessions",
                    "lineage",
                ):
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
                        if key != "capture_route"
                    }
                    require(
                        existing == payload
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
        exact_registry["projects"][project_id] = {
            "display_name": project_payload["display_name"],
            "enabled": project_payload["enabled"],
            "canonical_project_key": self.canonical_project_key(project_id),
            "relative_project_route": f"projects/{project_id}",
            "repository_path_hash": sha256_bytes(
                project_payload["repository_path"].encode("utf-8")
            ),
        }
        atomic_write_json(registry_path, exact_registry)

    def _backlog_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "task_backlog.json"

    def _plan_runtime_path(self, project_id: str) -> Path:
        return self.project_root(project_id) / "plan_runtime_projection.sqlite"

    def _plan_atomic_insertion_journal_path(
        self,
        project_id: str,
        batch_id: str,
    ) -> Path:
        return (
            self.project_root(project_id)
            / "plan_atomic_insertions"
            / f"{batch_id}.json"
        )

    def _write_plan_atomic_insertion_journal(
        self,
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        """Persist one recoverable Plan insertion journal transition."""

        atomic_write_json(path, payload)

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
            and all(
                character in _PROJECT_ID_CHARS
                for character in exact_refresh_id
            )
            and bool(exact_actor)
            and bool(exact_reason),
            "PLAN_RUNTIME_REFRESH_CONTRACT_INVALID",
            "A Plan runtime refresh requires one bounded identity, actor, and reason.",
            status="BLOCKED",
        )
        expected_backlog = str(expected_backlog_sha256 or "").strip().upper()
        expected_projection = str(
            expected_projection_content_sha256 or ""
        ).strip().upper()
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
            self.project_root(project_id)
            / "plan_runtime_refreshes"
            / f"{exact_refresh_id}.json"
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            observed_backlog_sha256 = sha256_bytes(
                canonical_json_bytes(backlog)
            )
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
                before.get("expected_projection_content_sha256")
                == expected_projection,
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
                    existing = json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    )
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
                and after.get("projection_content_sha256")
                == expected_projection,
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
                    "projection_content_sha256": after[
                        "projection_content_sha256"
                    ],
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
                    "receipt_sha256": sha256_bytes(
                        canonical_json_bytes(receipt_body)
                    ),
                }
                atomic_write_json(receipt_path, existing)
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
            git_commit_stage = str(
                task.get("git_commit_stage") or ""
            ).strip().upper()
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
                    character in _PROJECT_ID_CHARS
                    for character in exact_insert_before
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
                resolved_insert_before = (
                    _next_plan_hil_task_id(backlog["tasks"]) or ""
                )
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
                        existing_task["sequence"] = int(existing_task["sequence"]) + len(
                            normalized
                        )
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
                        "normalization_transition_id": (
                            exact_normalization_id or None
                        ),
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
            if str(row.get("panel_role") or "").upper()
            == "PHYSICALLY_FINAL_HIL"
        ]
        require(
            len(final_rows) == 1
            and str(final_rows[0]["task_id"])
            == expected_physical_final_task_id
            and str(ordered[-1]["task_id"])
            == expected_physical_final_task_id,
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
            dependencies = task.get("dependencies")
            if dependencies is not None:
                invalid_dependencies = sorted(
                    {str(value) for value in dependencies}
                    - earlier_executable_ids
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
            if str(row.get("panel_role") or "").upper()
            == "PHYSICALLY_FINAL_HIL"
        ]
        require(
            len(final_rows) == 1
            and str(final_rows[0]["task_id"])
            == expected_physical_final_task_id
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
        exact_final_task_id = str(
            expected_physical_final_task_id or ""
        ).strip()
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
                    == normalized_hashes[
                        "expected_executable_projection_sha256"
                    ],
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
                    research_batch_sha256=normalized_hashes[
                        "research_batch_sha256"
                    ],
                    input_sha256=input_sha256,
                    planned_at=planned_at,
                    expected_physical_final_task_id=exact_final_task_id,
                )
                after_status = self.backlog_status(
                    project_id,
                    _loaded_backlog=copy.deepcopy(candidate),
                )
                after_backlog_sha256 = sha256_bytes(
                    canonical_json_bytes(candidate)
                )
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
                    "research_batch_sha256": normalized_hashes[
                        "research_batch_sha256"
                    ],
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
                existing_amendments is None
                or isinstance(existing_amendments, list),
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
                    existing.get("request_sha256")
                    == exact_hashes["request_sha256"],
                    "ACTIVE_CONTRACT_REBIND_REPLAY_CONFLICT",
                    "The amendment ID already binds a different request.",
                    status="BLOCKED",
                )
                receipt = dict(existing["receipt"])
                idempotent_replay = True
            else:
                current_backlog_sha256 = sha256_bytes(
                    canonical_json_bytes(backlog)
                )
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
                    current_backlog_sha256
                    == exact_hashes["expected_backlog_sha256"]
                    and observed_canonical_sha256
                    == exact_hashes["expected_canonical_plan_sha256"]
                    and observed_executable_sha256
                    == exact_hashes[
                        "expected_executable_projection_sha256"
                    ],
                    "ACTIVE_CONTRACT_REBIND_PLAN_PRECONDITION_MISMATCH",
                    "The live Plan authority differs from the approved rebind boundary.",
                    status="MISMATCH",
                    observed_backlog_sha256=current_backlog_sha256,
                    observed_canonical_plan_sha256=observed_canonical_sha256,
                    observed_executable_projection_sha256=(
                        observed_executable_sha256
                    ),
                )
                active_ids = [
                    str(row["task_id"])
                    for row in backlog["tasks"]
                    if row.get("status") == "ACTIVE"
                ]
                require(
                    active_ids == [exact_task_id]
                    and target.get("status") == "ACTIVE",
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
                    "approval_receipt_sha256": exact_hashes[
                        "approval_receipt_sha256"
                    ],
                    "prior_contract_sha256": prior_contract_sha256,
                    "replacement_contract_sha256": (
                        replacement_contract_sha256
                    ),
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
                    "receipt_sha256": sha256_bytes(
                        canonical_json_bytes(receipt_body)
                    ),
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
                        "replacement_contract_sha256": (
                            replacement_contract_sha256
                        ),
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
                    task
                    for task in backlog["tasks"]
                    if task.get("status") == "ACTIVE"
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
                "receipt_sha256": sha256_bytes(
                    canonical_json_bytes(receipt_body)
                ),
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
            original_promoted_predecessor_task_id = str(
                tasks[promoted_index - 1]["task_id"]
            )
            original_promoted_successor = tasks[promoted_index + 1]
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
            require(
                old_active_task_id in promoted_dependencies
                and promoted_task_id in successor_dependencies,
                "PLAN_EXISTING_TASK_PROMOTION_DEPENDENCY_CHAIN_MISMATCH",
                "The queued Delta and its successor do not form the expected live dependency chain.",
                status="MISMATCH",
                promoted_dependencies=promoted_dependencies,
                successor_task_id=original_promoted_successor_task_id,
                successor_dependencies=successor_dependencies,
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
            original_promoted_successor["dependencies"] = [
                original_promoted_predecessor_task_id
                if dependency == promoted_task_id
                else dependency
                for dependency in successor_dependencies
            ]

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
            "runtime_task_id": runtime_task_id,
            "reason_sha256": reason_sha256,
            "stable_task_identity_preserved": True,
            "task_count_unchanged": len(result["tasks"])
            == len(before_status["tasks"]),
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
            superseded_task_id = str(
                candidate.get("supersedes_task_id") or ""
            ).strip()
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
                apply_active_release_context=(
                    lifecycle_status in {"ACTIVE", "QUEUED"}
                ),
            )
            supersedes_task_id = str(
                task.get("supersedes_task_id") or ""
            ).strip()
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
        canonical_plan_sha256 = sha256_bytes(
            canonical_json_bytes(canonical_plan_body)
        )
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
            "persistent_until": "NEXT_SIX_WAY_HIL_PRESENTED",
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
                    "plugin_command": "/evi-plan",
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
                "governed six-way HIL."
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
        host_window_before = _host_plan_window_fingerprint(
            self.backlog_status(project_id)
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
                    not explicit_insert_before
                    and exact_boundary == "BEFORE_NEXT_HIL"
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
        host_window_after = _host_plan_window_fingerprint(status)
        window_task_ids = [
            str(row["task_id"]) for row in host_window_after["rows"]
        ]
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
            "linked_task_id": exact_link,
            "active_row_present": active_row_present,
            "linked_row_is_currently_visible": linked_row_is_currently_visible,
            "before_fingerprint_sha256": host_window_before[
                "fingerprint_sha256"
            ],
            "after_fingerprint_sha256": host_window_after[
                "fingerprint_sha256"
            ],
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
            "canonical_task_count": int(
                goal_status.get("canonical_task_count") or 0
            ),
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
                completed["state_travel_completion_receipt_sha256"] = (
                    receipt_sha256
                )
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

    def advance_verified_fallback_prewarmer_task(
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
        """Close the exact disabled-PV11 fallback row without building a PV."""

        receipt_sha256 = str(completion_receipt.get("receipt_sha256") or "").strip()
        receipt_body = {
            key: value
            for key, value in completion_receipt.items()
            if key != "receipt_sha256"
        }
        fallback_proof = completion_receipt.get("fallback_prewarmer_proof")
        require(
            completion_receipt.get("schema")
            == "evidence-lane.verified-fallback-prewarm-task-advance.v1"
            and len(receipt_sha256) == 64
            and receipt_sha256 == sha256_bytes(canonical_json_bytes(receipt_body))
            and completed_backlog_task_id
            == "EL-CODEX-PV11-FALLBACK-SLOT-INSTALL-PREWARM-DELTA-149"
            and isinstance(fallback_proof, dict)
            and fallback_proof.get("schema")
            == "evidence-lane.codex-fallback-prewarm-proof.v1"
            and fallback_proof.get("status") == "PASS"
            and completion_receipt.get("candidate_created") is False
            and completion_receipt.get("pending_hil") is False
            and completion_receipt.get("pointer_moved") is False
            and completion_receipt.get("hil_inferred") is False,
            "FALLBACK_PREWARM_TASK_ADVANCE_RECEIPT_INVALID",
            "The fallback prewarm completion receipt is malformed or promoting.",
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
            "FALLBACK_PREWARM_TASK_ADVANCE_RECEIPT_BINDING_MISMATCH",
            "The fallback prewarm receipt does not bind this exact Plan transition.",
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
                "FALLBACK_PREWARM_TASK_ADVANCE_PLAN_TASK_MISMATCH",
                "The fallback row or its requested successor is absent from the Plan Lane.",
                status="MISMATCH",
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
                "FALLBACK_PREWARM_TASK_ADVANCE_CONTRACT_MISMATCH",
                "The successor classification must exactly match its queued Plan contract.",
                status="MISMATCH",
                mismatches=mismatches,
            )
            replacement_runtime_task_id = str(
                replacement_contract.get("task_id") or ""
            ).strip()
            require(
                bool(replacement_runtime_task_id),
                "FALLBACK_PREWARM_TASK_ADVANCE_RUNTIME_TASK_ID_REQUIRED",
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
                and completed.get("fallback_prewarmer_completion_receipt_sha256")
                == receipt_sha256
                and completed.get("fallback_prewarmer_completion_receipt")
                == completion_receipt
                and replacement.get("status") == "ACTIVE"
                and replacement.get("active_session_id") == session_id
                and bool(persisted_replacement_runtime_task_id)
            )
            require(
                before or after,
                "FALLBACK_PREWARM_TASK_ADVANCE_PLAN_STATE_MISMATCH",
                "The Plan is neither at the fallback row nor its idempotent successor state.",
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

            proof_sha256 = str(
                cast(dict[str, Any], fallback_proof).get("receipt_sha256") or ""
            )
            completion_event_id = (
                f"{completed_backlog_task_id}__{proof_sha256[:24].lower()}__done"
            )
            activation_event_id = (
                f"{replacement_backlog_task_id}__{session_id}__"
                f"{replacement_runtime_task_id}__fallback_active"
            )
            completion_event: dict[str, Any] | None
            activation_event: dict[str, Any] | None
            if before:
                now = utc_now()
                completion_event = append_delta_event(
                    backlog,
                    task_id=completed_backlog_task_id,
                    event_type="FALLBACK_PREWARM_VERIFIED",
                    to_status="DONE",
                    actor=session_id,
                    event_id=completion_event_id,
                    recorded_at=now,
                    assume_initialized=True,
                    details={
                        "session_id": session_id,
                        "completion_receipt_sha256": receipt_sha256,
                        "fallback_prewarmer_proof_sha256": proof_sha256,
                        "fallback_activated": False,
                        "restart_invoked": False,
                        "candidate_created": False,
                        "pending_hil": False,
                        "pointer_moved": False,
                        "hil_inferred": False,
                    },
                )
                completed.pop("active_session_id", None)
                completed.pop("runtime_task_id", None)
                completed["fallback_prewarmer_completion_receipt_sha256"] = (
                    receipt_sha256
                )
                completed["fallback_prewarmer_completion_receipt"] = (
                    completion_receipt
                )
                completed.setdefault("history", []).append(
                    {
                        "event": "FALLBACK_PREWARM_VERIFIED",
                        "session_id": session_id,
                        "completion_receipt_sha256": receipt_sha256,
                        "fallback_prewarmer_proof_sha256": proof_sha256,
                        "recorded_at": now,
                    }
                )
                activation_event = append_delta_event(
                    backlog,
                    task_id=replacement_backlog_task_id,
                    event_type="ACTIVATED_AFTER_FALLBACK_PREWARM",
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
                    },
                )
                replacement["active_session_id"] = session_id
                replacement["runtime_task_id"] = replacement_runtime_task_id
                replacement.setdefault("history", []).append(
                    {
                        "event": "CLAIMED_AFTER_FALLBACK_PREWARM",
                        "session_id": session_id,
                        "runtime_task_id": replacement_runtime_task_id,
                        "completed_backlog_task_id": completed_backlog_task_id,
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
                    "FALLBACK_PREWARM_TASK_ADVANCE_EVENT_LEDGER_MISMATCH",
                    "The idempotent fallback state is missing its completion or activation event.",
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
        """Atomically advance one independently verified, candidate-free row."""

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
            and completion_receipt.get("pending_hil") is False
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

            proof_sha256 = str(cast(dict[str, Any], proof)["receipt_sha256"])
            completion_event_id = (
                f"{completed_backlog_task_id}__{proof_sha256[:24].lower()}__checkpoint_done"
            )
            activation_event_id = (
                f"{replacement_backlog_task_id}__{session_id}__"
                f"{replacement_runtime_task_id}__checkpoint_active"
            )
            completion_event: dict[str, Any] | None
            activation_event: dict[str, Any] | None
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
                        "pending_hil": False,
                        "pointer_moved": False,
                        "hil_inferred": False,
                    },
                )
                completed.pop("active_session_id", None)
                completed.pop("runtime_task_id", None)
                completed["task_checkpoint_completion_receipt_sha256"] = (
                    receipt_sha256
                )
                completed["task_checkpoint_completion_receipt"] = completion_receipt
                completed.setdefault("history", []).append(
                    {
                        "event": "VERIFIED_TASK_CHECKPOINT_COMPLETED",
                        "session_id": session_id,
                        "verification_kind": completion_receipt.get(
                            "verification_kind"
                        ),
                        "completion_receipt_sha256": receipt_sha256,
                        "verification_proof_sha256": proof_sha256,
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
                    },
                )
                replacement["active_session_id"] = session_id
                replacement["runtime_task_id"] = replacement_runtime_task_id
                replacement.setdefault("history", []).append(
                    {
                        "event": "CLAIMED_AFTER_VERIFIED_TASK_CHECKPOINT",
                        "session_id": session_id,
                        "runtime_task_id": replacement_runtime_task_id,
                        "completed_backlog_task_id": completed_backlog_task_id,
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
            return {
                "status": "PASS",
                "idempotent_reuse": after,
                "completed_task": completed,
                "active_task": replacement,
                "completion_event": completion_event,
                "activation_event": activation_event,
                "completion_receipt": completion_receipt,
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
                        all(recorded.get(key) == value for key, value in comparable.items()),
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
                        "task_contract_sha256": evidence[
                            "task_contract_sha256"
                        ],
                    },
                )
                task["completed_candidate_id"] = candidate_id
                task["batch_completion_receipt_id"] = receipt_id
                task["implementation_evidence_sha256"] = evidence[
                    "evidence_sha256"
                ]
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
            receipt["receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(receipt)
            )
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
                else f"HIL_{decision}"
            )
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
                    details={
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": accepted_pv,
                    },
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
                details={
                    "decision": decision,
                    "candidate_id": candidate_id,
                    "accepted_pv": accepted_pv,
                },
            )
            task["accepted_pv"] = accepted_pv if decision == "APPROVE" else None
            task["history"].append(
                {
                    "event": "HIL_DECISION",
                    "decision": decision,
                    "candidate_id": candidate_id,
                    "accepted_pv": accepted_pv,
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
                    },
                )
                task = tasks[task_id]
                task["accepted_pv"] = accepted_pv if decision == "APPROVE" else None
                task["history"].append(
                    {
                        "event": "HIL_DECISION",
                        "event_id": event["event_id"],
                        "decision": decision,
                        "candidate_id": candidate_id,
                        "accepted_pv": task["accepted_pv"],
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
            receipt["receipt_sha256"] = sha256_bytes(
                canonical_json_bytes(receipt)
            )
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
    ) -> dict[str, Any]:
        """Apply one explicit DROP or SUPERSEDE without deleting history."""

        transition = transition_name.strip().upper()
        require(
            transition in {"DROP", "SUPERSEDE"},
            "DELTA_EXPLICIT_TRANSITION_INVALID",
            "Only DROP or SUPERSEDE may be requested directly.",
            status="BLOCKED",
            transition=transition,
        )
        with self._lock(project_id):
            backlog = self._load_backlog(project_id)
            ensure_event_ledger(backlog)
            tasks = {str(row["task_id"]): row for row in backlog["tasks"]}
            require(
                task_id in tasks,
                "DELTA_TASK_NOT_FOUND",
                "The requested Delta does not exist.",
                status="MISMATCH",
                task_id=task_id,
            )
            task = tasks[task_id]
            target_status = "DROPPED" if transition == "DROP" else "SUPERSEDED"
            details: dict[str, Any] = {
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
                details["replacement_task_id"] = exact_replacement
                task["superseded_by_task_id"] = exact_replacement
                tasks[exact_replacement]["supersedes_task_id"] = task_id
            lifecycle_event = append_delta_event(
                backlog,
                task_id=task_id,
                event_type=target_status,
                to_status=target_status,
                actor=decided_by,
                event_id=event_id,
                assume_initialized=True,
                details=details,
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
                        **details,
                    }
                )
            self._persist_backlog(project_id, backlog)
            return {
                "status": "PASS",
                "task": task,
                "event": lifecycle_event,
                "backlog": self.backlog_status(project_id),
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
        target = (root / "candidates" / candidate_id).resolve()
        target.relative_to(root)
        return target

    def accepted_path(self, project_id: str, pv_id: str) -> Path:
        require(
            pv_id.startswith("PV") and pv_id[2:].isdigit() and int(pv_id[2:]) >= 1,
            "PV_ID_INVALID",
            "Accepted PV IDs must use the PV1, PV2, ... sequence.",
            status="BLOCKED",
            pv_id=pv_id,
        )
        root = self.project_root(project_id)
        return (root / "accepted" / pv_id).resolve()

    def next_pv_id(self, project_id: str) -> str:
        accepted = self.accepted_ids(project_id)
        if not accepted:
            return "PV1"
        return f"PV{max(int(pv_id[2:]) for pv_id in accepted) + 1}"

    def accepted_ids(self, project_id: str) -> list[str]:
        root = self.project_root(project_id) / "accepted"
        return sorted(
            (
                path.name
                for path in root.glob("PV*")
                if path.is_dir() and path.name[2:].isdigit() and int(path.name[2:]) >= 1
            ),
            key=lambda value: int(value[2:]),
        )

    def highest_accepted_ordinal(self, project_id: str) -> int:
        accepted = self.accepted_ids(project_id)
        return max((int(value[2:]) for value in accepted), default=0)

    def place_candidate(
        self,
        project_id: str,
        candidate_id: str,
        built_directory: str | Path,
    ) -> dict[str, Any]:
        source = Path(built_directory).resolve()
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

    def promote(
        self,
        project_id: str,
        candidate_id: str,
        *,
        expected_pointer_generation: int,
        decided_by: str,
        decision_id: str,
    ) -> dict[str, Any]:
        candidate = self.candidate_path(project_id, candidate_id)
        candidate_validation = validate_pv_package(candidate)
        exit_slip = json.loads((candidate / "exit_slip.json").read_text(encoding="utf-8"))
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
            acceptance = exit_slip.get("acceptance_checks") or {}
            pending_postseal = int(
                (acceptance.get("counts") or {}).get("PENDING_POSTSEAL") or 0
            )
            postseal_receipt_validation = None
            if pending_postseal:
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
                    postseal_receipt = json.loads(
                        receipt_path.read_text(encoding="utf-8")
                    )
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
                actual_receipt_sha = sha256_bytes(
                    canonical_json_bytes(receipt_payload)
                )
                postseal_health = postseal_receipt.get("acceptance") or {}
                source_commit = (
                    (exit_slip.get("repository_exit") or {}).get("commit_sha")
                )
                require(
                    postseal_receipt.get("schema")
                    == "evidence-lane.postseal-acceptance.receipt.v1"
                    and postseal_receipt.get("project_id") == project_id
                    and postseal_receipt.get("candidate_id") == candidate_id
                    and postseal_receipt.get("accepted_pv_retained")
                    == pointer.accepted_pv
                    and postseal_receipt.get("pointer_generation_retained")
                    == pointer.generation
                    and postseal_receipt.get("source_commit_sha") == source_commit
                    and claimed_receipt_sha == actual_receipt_sha
                    and postseal_health.get("status") == "PASS"
                    and postseal_health.get("verdict")
                    == "ALL_EXECUTABLE_CHECKS_PASS"
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
                postseal_receipt_validation = {
                    "status": "PASS",
                    "path": str(receipt_path),
                    "receipt_sha256": actual_receipt_sha,
                    "checks": pending_postseal,
                }
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
        candidate_validation = validate_pv_package(
            self.candidate_path(project_id, candidate_id),
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
        target = self.accepted_path(project_id, target_pv)
        target_validation = validate_pv_package(target)
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

    def project_status(self, project_id: str) -> dict[str, Any]:
        root = self.project_root(project_id)
        pointer = self.pointer(project_id)
        accepted = self.accepted_ids(project_id)
        backlog = self._load_backlog(project_id)
        backlog_counts = Counter(
            str(task.get("status", "UNKNOWN")) for task in backlog["tasks"]
        )
        candidates = sorted(
            path.name for path in (root / "candidates").glob("PV*") if path.is_dir()
        )
        return {
            "project": self.config(project_id).as_dict(),
            "pointer": pointer.as_dict(),
            "accepted": accepted,
            "highest_accepted_ordinal": self.highest_accepted_ordinal(project_id),
            "next_candidate_pv": self.next_pv_id(project_id),
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
