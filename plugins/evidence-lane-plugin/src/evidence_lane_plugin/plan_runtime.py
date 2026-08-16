"""Append-only Delta lifecycle law and derived Plan runtime projection."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .ids import prefixed_id
from .timeutil import utc_now

DELTA_EVENT_SCHEMA = "evidence-lane.delta-lifecycle-event.v1"
PLANNING_MODE_EVENT_SCHEMA = "evidence-lane.planning-mode-event.v1"
PLAN_RUNTIME_SCHEMA = "evidence-lane.plan-runtime-projection.v2"
PLAN_RUNTIME_USER_VERSION = 2

_EXECUTABLE_HOST_STATUS = {
    "ACTIVE": "in_progress",
    "QUEUED": "pending",
    "DONE": "completed",
    "ACCEPTED": "completed",
}
_PLAN_GROUP_DIRECTIVE_RE = re.compile(
    r"\bPLAN_GROUP\s*=\s*([A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?)",
    re.IGNORECASE,
)
_PLAN_BATCH_DIRECTIVE_RE = re.compile(
    r"\bCOMMIT_BATCH\s*=\s*([A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?)",
    re.IGNORECASE,
)
_PLAN_GIT_STAGE_DIRECTIVE_RE = re.compile(
    r"\bGIT_STAGE\s*=\s*([A-Za-z0-9_-](?:[A-Za-z0-9._-]*[A-Za-z0-9_-])?)",
    re.IGNORECASE,
)
_PLAN_DEPENDENCIES_DIRECTIVE_RE = re.compile(
    r"\bDEPENDS_ON\s*=\s*"
    r"([A-Za-z0-9._-]+(?:\s*[+,]\s*[A-Za-z0-9._-]+)*)",
    re.IGNORECASE,
)
_PLAN_VERSION_DIRECTIVE_RE = re.compile(
    r"\bCURRENT_VERSION\s*=\s*"
    r"(v?\d+\.\d+(?:\.\d+)?(?:\+[A-Za-z0-9._-]+)?)",
    re.IGNORECASE,
)
_PLAN_BRANCH_DIRECTIVE_RE = re.compile(
    r"\bCURRENT_BRANCH\s*=\s*"
    r"([A-Za-z0-9_/-](?:[A-Za-z0-9._/-]*[A-Za-z0-9_/-])?)",
    re.IGNORECASE,
)

DELTA_STATUSES = (
    "QUEUED",
    "ACTIVE",
    "DONE",
    "ACCEPTED",
    "REJECTED",
    "DROPPED",
    "SUPERSEDED",
    "FAILED",
    "ROLLED_BACK",
)

_LEGACY_STATUS_MAP = {
    "QUEUED": "QUEUED",
    "ACTIVE": "ACTIVE",
    "DONE": "DONE",
    "ACCEPTED": "ACCEPTED",
    "COMPLETED_ACCEPTED": "ACCEPTED",
    "FOLLOW_UP_PENDING": "DONE",
    "REJECTED": "REJECTED",
    "DROPPED": "DROPPED",
    "SUPERSEDED": "SUPERSEDED",
    "FAILED": "FAILED",
    "ROLLED_BACK": "ROLLED_BACK",
    "ROLLED_BACK_UNACCEPTED": "ROLLED_BACK",
}

_ALLOWED_TRANSITIONS = {
    "QUEUED": {"ACTIVE", "DROPPED", "SUPERSEDED"},
    "ACTIVE": {"DONE", "FAILED"},
    "DONE": {
        "ACCEPTED",
        "REJECTED",
        "DROPPED",
        "SUPERSEDED",
        "FAILED",
        "ROLLED_BACK",
    },
    "ACCEPTED": set(),
    "REJECTED": set(),
    "DROPPED": set(),
    "SUPERSEDED": set(),
    "FAILED": set(),
    "ROLLED_BACK": set(),
}

_SAME_STATUS_EVENTS = {
    "HIL_FOLLOW_UP_REQUESTED",
    "IDEMPOTENT_REPLAY",
    "STEER_DELTA_LINKED",
    "STEER_DELTA_NEW_STEP",
    "TASK_CONTRACT_AMENDED",
}


def _stable_event_id(prefix: str, value: dict[str, Any]) -> str:
    digest = sha256_bytes(canonical_json_bytes(value)).lower()
    return f"{prefix}_{digest[:32]}"


def _event_without_hash(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "event_sha256"}


def _event_sha256(event: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(_event_without_hash(event)))


def _task_rows(backlog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(task["task_id"]): task for task in backlog.get("tasks", [])}


def _current_task_status(
    backlog: dict[str, Any],
    task_id: str,
) -> tuple[str | None, str | None]:
    for event in reversed(backlog.get("events", [])):
        if event.get("task_id") == task_id:
            return str(event["to_status"]), str(event["event_sha256"])
    return None, None


def _validate_transition(
    from_status: str | None,
    to_status: str,
    *,
    event_type: str,
    allow_legacy_initial: bool = False,
) -> None:
    require(
        to_status in DELTA_STATUSES,
        "DELTA_STATUS_INVALID",
        "The requested Delta lifecycle status is not supported.",
        status="BLOCKED",
        to_status=to_status,
        supported=list(DELTA_STATUSES),
    )
    if from_status is None:
        require(
            to_status == "QUEUED" or allow_legacy_initial,
            "DELTA_INITIAL_STATUS_INVALID",
            "A new Delta must enter the universal lifecycle as QUEUED.",
            status="BLOCKED",
            to_status=to_status,
        )
        return
    if from_status == to_status:
        require(
            event_type in _SAME_STATUS_EVENTS,
            "DELTA_STATUS_SELF_TRANSITION_INVALID",
            "A Delta status may repeat only for an idempotent or follow-up receipt.",
            status="BLOCKED",
            task_status=from_status,
            event_type=event_type,
        )
        return
    if (
        from_status == "ACTIVE"
        and to_status == "QUEUED"
        and event_type == "PRIORITY_STEER_PAUSED"
    ):
        # An explicit user priority steer may pause, but never complete or
        # supersede, the sole live Delta.  The only caller is the journaled
        # Plan insertion/session-rebind route, which activates the inserted
        # correction under the same project/session lock boundary and leaves
        # the interrupted row immediately queued behind it.
        return
    if (
        from_status == "ACTIVE"
        and to_status == "SUPERSEDED"
        and event_type
        in {
            "PLAN_NORMALIZATION_SUPERSEDED",
            "PLAN_NORMALIZATION_CORRECTION_SUPERSEDED",
        }
    ):
        # A normal steer may not silently retire the sole live task.  The
        # Plan-normalization transaction is the one exception: it verifies
        # and journals the matching session rebind before exposing the new
        # active row, so the append-only Plan and live task cannot diverge.
        return
    if (
        from_status == "SUPERSEDED"
        and to_status == "ACTIVE"
        and event_type == "PLAN_NORMALIZATION_CORRECTION_RESTORED"
    ):
        # This reverse transition is available only to the journaled correction
        # path. It preserves the mistaken supersession as immutable history while
        # restoring the exact pre-normalization live task contract.
        return
    require(
        to_status in _ALLOWED_TRANSITIONS[from_status],
        "DELTA_STATUS_TRANSITION_INVALID",
        "The requested Delta lifecycle transition is not allowed.",
        status="BLOCKED",
        from_status=from_status,
        to_status=to_status,
        allowed=sorted(_ALLOWED_TRANSITIONS[from_status]),
    )


def append_delta_event(
    backlog: dict[str, Any],
    *,
    task_id: str,
    event_type: str,
    to_status: str,
    actor: str,
    details: dict[str, Any] | None = None,
    event_id: str | None = None,
    recorded_at: str | None = None,
    assume_initialized: bool = False,
    allow_legacy_initial: bool = False,
) -> dict[str, Any]:
    """Append one idempotent, hash-chained Delta transition."""

    if not assume_initialized:
        ensure_event_ledger(backlog)
    tasks = _task_rows(backlog)
    require(
        task_id in tasks,
        "DELTA_TASK_NOT_FOUND",
        "The Delta lifecycle event references an unknown task.",
        status="MISMATCH",
        task_id=task_id,
    )
    exact_details = details or {}
    exact_actor = actor.strip()
    require(
        bool(exact_actor),
        "DELTA_EVENT_ACTOR_REQUIRED",
        "A visible actor is required for every Delta lifecycle event.",
        status="BLOCKED",
    )
    exact_event_id = event_id or prefixed_id("deltaevt")
    existing = next(
        (
            row
            for row in backlog.setdefault("events", [])
            if row.get("event_id") == exact_event_id
        ),
        None,
    )
    if existing is not None:
        immutable_match = (
            existing.get("task_id") == task_id
            and existing.get("event_type") == event_type
            and existing.get("to_status") == to_status
            and existing.get("actor") == exact_actor
            and existing.get("details") == exact_details
        )
        require(
            immutable_match,
            "DELTA_EVENT_ID_CONFLICT",
            "The Delta event ID already binds different immutable content.",
            status="MISMATCH",
            event_id=exact_event_id,
        )
        return existing
    from_status, previous_task_sha256 = _current_task_status(backlog, task_id)
    _validate_transition(
        from_status,
        to_status,
        event_type=event_type,
        allow_legacy_initial=allow_legacy_initial,
    )
    event = {
        "schema": DELTA_EVENT_SCHEMA,
        "sequence": len(backlog["events"]) + 1,
        "event_id": exact_event_id,
        "task_id": task_id,
        "event_type": event_type,
        "from_status": from_status,
        "to_status": to_status,
        "actor": exact_actor,
        "recorded_at": recorded_at or utc_now(),
        "previous_global_event_sha256": backlog.get("event_head_sha256"),
        "previous_task_event_sha256": previous_task_sha256,
        "details": exact_details,
    }
    event["event_sha256"] = _event_sha256(event)
    backlog["events"].append(event)
    backlog["event_head_sha256"] = event["event_sha256"]
    task = tasks[task_id]
    task["status"] = to_status
    task["last_event_id"] = exact_event_id
    task["last_event_sha256"] = event["event_sha256"]
    return event


def _validate_delta_events(backlog: dict[str, Any]) -> dict[str, str]:
    tasks = _task_rows(backlog)
    current: dict[str, str] = {}
    task_heads: dict[str, str] = {}
    global_head: str | None = None
    events = backlog.setdefault("events", [])
    seen_ids: set[str] = set()
    for position, event in enumerate(events, start=1):
        require(
            event.get("schema") == DELTA_EVENT_SCHEMA
            and event.get("sequence") == position,
            "DELTA_EVENT_SCHEMA_MISMATCH",
            "The Delta lifecycle event ledger is not canonical.",
            status="MISMATCH",
            position=position,
        )
        event_id = str(event.get("event_id", ""))
        task_id = str(event.get("task_id", ""))
        require(
            bool(event_id)
            and event_id not in seen_ids
            and task_id in tasks
            and event.get("previous_global_event_sha256") == global_head
            and event.get("previous_task_event_sha256") == task_heads.get(task_id),
            "DELTA_EVENT_CHAIN_MISMATCH",
            "The Delta lifecycle event chain is not append-only or self-consistent.",
            status="MISMATCH",
            event_id=event_id,
        )
        from_status = current.get(task_id)
        require(
            event.get("from_status") == from_status,
            "DELTA_EVENT_FROM_STATUS_MISMATCH",
            "A Delta event does not begin at the prior derived status.",
            status="MISMATCH",
            event_id=event_id,
            expected=from_status,
            actual=event.get("from_status"),
        )
        _validate_transition(
            from_status,
            str(event.get("to_status", "")),
            event_type=str(event.get("event_type", "")),
            allow_legacy_initial=event.get("event_type") == "LEGACY_STATUS_IMPORTED",
        )
        expected_sha256 = _event_sha256(event)
        require(
            event.get("event_sha256") == expected_sha256,
            "DELTA_EVENT_HASH_MISMATCH",
            "A Delta lifecycle event hash does not match its immutable content.",
            status="MISMATCH",
            event_id=event_id,
        )
        seen_ids.add(event_id)
        current[task_id] = str(event["to_status"])
        task_heads[task_id] = expected_sha256
        global_head = expected_sha256
    require(
        backlog.get("event_head_sha256") in {None, global_head},
        "DELTA_EVENT_HEAD_MISMATCH",
        "The Delta event head does not match the append-only ledger.",
        status="MISMATCH",
    )
    backlog["event_head_sha256"] = global_head
    return current


def _validate_mode_events(backlog: dict[str, Any]) -> None:
    head: str | None = None
    seen_ids: set[str] = set()
    events = backlog.setdefault("planning_mode_events", [])
    for position, event in enumerate(events, start=1):
        event_id = str(event.get("event_id", ""))
        require(
            event.get("schema") == PLANNING_MODE_EVENT_SCHEMA
            and event.get("sequence") == position
            and bool(event_id)
            and event_id not in seen_ids
            and event.get("previous_event_sha256") == head
            and event.get("event_sha256") == _event_sha256(event),
            "PLANNING_MODE_EVENT_CHAIN_MISMATCH",
            "The Planning-mode projection event chain is invalid.",
            status="MISMATCH",
            event_id=event_id,
        )
        seen_ids.add(event_id)
        head = str(event["event_sha256"])
    require(
        backlog.get("planning_mode_event_head_sha256") in {None, head},
        "PLANNING_MODE_EVENT_HEAD_MISMATCH",
        "The Planning-mode event head does not match its append-only ledger.",
        status="MISMATCH",
    )
    backlog["planning_mode_event_head_sha256"] = head


def ensure_event_ledger(backlog: dict[str, Any]) -> dict[str, Any]:
    """Validate the ledger and import legacy task statuses without dropping history."""

    backlog.setdefault("events", [])
    backlog.setdefault("planning_mode_events", [])
    backlog.setdefault("event_head_sha256", None)
    backlog.setdefault("planning_mode_event_head_sha256", None)
    current = _validate_delta_events(backlog)
    plans = {str(plan.get("plan_id")): plan for plan in backlog.get("plans", [])}
    for task in sorted(backlog.get("tasks", []), key=lambda row: row["sequence"]):
        task_id = str(task["task_id"])
        if task_id in current:
            continue
        legacy_status = _LEGACY_STATUS_MAP.get(str(task.get("status", "QUEUED")))
        require(
            legacy_status is not None,
            "DELTA_LEGACY_STATUS_INVALID",
            "A legacy task status cannot enter the universal Delta lifecycle.",
            status="MISMATCH",
            task_id=task_id,
            legacy_status=task.get("status"),
        )
        legacy_status = cast(str, legacy_status)
        plan = plans.get(str(task.get("plan_id")), {})
        actor = str(plan.get("planned_by") or "legacy-store-migration")
        event_type = "ADDED" if legacy_status == "QUEUED" else "LEGACY_STATUS_IMPORTED"
        event_id = _stable_event_id(
            "deltaevt",
            {
                "task_id": task_id,
                "event_type": event_type,
                "status": legacy_status,
                "planned_at": task.get("planned_at"),
            },
        )
        append_delta_event(
            backlog,
            task_id=task_id,
            event_type=event_type,
            to_status=legacy_status,
            actor=actor,
            event_id=event_id,
            recorded_at=str(task.get("planned_at") or utc_now()),
            assume_initialized=True,
            allow_legacy_initial=legacy_status != "QUEUED",
            details={
                "legacy_history_preserved": True,
                "plan_id": task.get("plan_id"),
            },
        )
        current[task_id] = legacy_status
    current = _validate_delta_events(backlog)
    for task_id, task in _task_rows(backlog).items():
        task["status"] = current[task_id]
        _, task_head = _current_task_status(backlog, task_id)
        task["last_event_sha256"] = task_head
        task["last_event_id"] = next(
            (
                event["event_id"]
                for event in reversed(backlog["events"])
                if event["task_id"] == task_id
            ),
            None,
        )
    _validate_mode_events(backlog)
    backlog["event_schema"] = DELTA_EVENT_SCHEMA
    backlog["universal_statuses"] = list(DELTA_STATUSES)
    return backlog


def append_planning_mode_event(
    backlog: dict[str, Any],
    *,
    source_event_id: str,
    session_id: str,
    request_sha256: str,
    selected_mode_ids: list[str],
    mode_intersection: str,
    canonical_lanes: list[str],
    lifecycle_state: str,
    pointer_generation: int,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Append a privacy-minimized Planning-mode control-plane receipt."""

    ensure_event_ledger(backlog)
    exact_event_id = _stable_event_id(
        "planevt",
        {
            "source_event_id": source_event_id,
            "session_id": session_id,
            "request_sha256": request_sha256,
        },
    )
    existing = next(
        (
            row
            for row in backlog["planning_mode_events"]
            if row["event_id"] == exact_event_id
        ),
        None,
    )
    if existing is not None:
        immutable_match = (
            existing.get("source_chat_lineage_event_id") == source_event_id
            and existing.get("session_id") == session_id
            and existing.get("request_sha256") == request_sha256
            and existing.get("selected_mode_ids") == selected_mode_ids
            and existing.get("mode_intersection") == mode_intersection
            and existing.get("canonical_lanes") == canonical_lanes
            and existing.get("lifecycle_state") == lifecycle_state
            and existing.get("pointer_generation") == pointer_generation
        )
        require(
            immutable_match,
            "PLANNING_MODE_EVENT_ID_CONFLICT",
            "The Planning-mode event ID already binds different immutable content.",
            status="MISMATCH",
            event_id=exact_event_id,
        )
        return existing
    event = {
        "schema": PLANNING_MODE_EVENT_SCHEMA,
        "sequence": len(backlog["planning_mode_events"]) + 1,
        "event_id": exact_event_id,
        "source_chat_lineage_event_id": source_event_id,
        "session_id": session_id,
        "request_sha256": request_sha256,
        "selected_mode_ids": selected_mode_ids,
        "mode_intersection": mode_intersection,
        "canonical_lanes": canonical_lanes,
        "lifecycle_state": lifecycle_state,
        "pointer_generation": pointer_generation,
        "recorded_at": recorded_at or utc_now(),
        "previous_event_sha256": backlog.get("planning_mode_event_head_sha256"),
        "private_reasoning_excluded": True,
        "canonical_plan_sector_mutated": False,
        "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
    }
    event["event_sha256"] = _event_sha256(event)
    backlog["planning_mode_events"].append(event)
    backlog["planning_mode_event_head_sha256"] = event["event_sha256"]
    return event


def _string_list(value: Any) -> list[str]:
    """Normalize one already-governed bounded list for SQLite projection."""

    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _latest_directive(
    task: dict[str, Any],
    pattern: re.Pattern[str],
) -> tuple[str | None, str | None]:
    """Resolve only the latest exact linked-Delta directive."""

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
            "PLAN_RUNTIME_LINKED_DIRECTIVE_CONFLICT",
            "One linked Delta contains conflicting Plan runtime directives.",
            status="MISMATCH",
            task_id=task.get("task_id"),
            delta_id=steer.get("delta_id"),
            values=values,
        )
        if values:
            selected = (
                values[0],
                f"LINKED_DELTA:{steer.get('delta_id') or 'UNKNOWN'!s}",
            )
    return selected or (None, None)


def _task_contract_projection(
    task: dict[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any]:
    """Project the full bounded task contract, not only its outcome hash."""

    contract = {
        "task_id": str(task["task_id"]),
        "sequence": int(task["sequence"]),
        "plan_id": str(task["plan_id"]),
        "task_class": str(task["task_class"]),
        "requested_outcome": str(task["requested_outcome"]),
        "permitted_paths": _string_list(task.get("permitted_paths")),
        "permitted_tools": _string_list(task.get("permitted_tools")),
        "acceptance_checks": _string_list(task.get("acceptance_checks")),
        "stop_condition": str(task.get("stop_condition") or ""),
        "panel_role": str(task.get("panel_role") or "STANDARD"),
        "plan_group": (
            str(task["plan_group"]) if task.get("plan_group") else None
        ),
        "commit_batch_id": (
            str(task.get("commit_batch_id") or task.get("batch_id"))
            if task.get("commit_batch_id") or task.get("batch_id")
            else None
        ),
        "dependencies": _string_list(task.get("dependencies")),
        "git_commit_stage": (
            str(task["git_commit_stage"])
            if task.get("git_commit_stage")
            else None
        ),
        "current_version": (
            str(task.get("current_version") or task.get("version_marker"))
            if task.get("current_version") or task.get("version_marker")
            else None
        ),
        "current_branch": (
            str(task.get("current_branch") or task.get("git_branch"))
            if task.get("current_branch") or task.get("git_branch")
            else None
        ),
        "current_status": str(task["status"]),
        "supersedes_task_id": task.get("supersedes_task_id"),
        "superseded_by_task_id": task.get("superseded_by_task_id"),
        "last_event_id": task.get("last_event_id"),
        "last_event_sha256": task.get("last_event_sha256"),
        "updated_at": updated_at,
    }
    static_contract = {
        key: contract[key]
        for key in (
            "task_id",
            "sequence",
            "plan_id",
            "task_class",
            "requested_outcome",
            "permitted_paths",
            "permitted_tools",
            "acceptance_checks",
            "stop_condition",
            "panel_role",
            "plan_group",
            "commit_batch_id",
            "dependencies",
            "git_commit_stage",
            "current_version",
            "current_branch",
        )
    }
    return {
        **contract,
        "requested_outcome_sha256": sha256_bytes(
            str(contract["requested_outcome"]).encode("utf-8")
        ),
        "task_contract_sha256": sha256_bytes(
            canonical_json_bytes(static_contract)
        ),
    }


def _runtime_row_metadata(
    task: dict[str, Any],
    *,
    previous_executable_task_id: str | None,
    executable: bool,
) -> dict[str, Any]:
    """Derive the live row index from task contracts plus linked directives."""

    active_contract_rebound = (
        task.get("current_contract_authority") == "ACTIVE_CONTRACT_REBIND"
    )
    if active_contract_rebound:
        group = group_source = None
        batch = batch_source = None
        git_stage = git_source = None
        dependency_text = dependency_source = None
    else:
        group, group_source = _latest_directive(task, _PLAN_GROUP_DIRECTIVE_RE)
        batch, batch_source = _latest_directive(task, _PLAN_BATCH_DIRECTIVE_RE)
        git_stage, git_source = _latest_directive(
            task, _PLAN_GIT_STAGE_DIRECTIVE_RE
        )
        dependency_text, dependency_source = _latest_directive(
            task, _PLAN_DEPENDENCIES_DIRECTIVE_RE
        )
    version, version_source = _latest_directive(
        task, _PLAN_VERSION_DIRECTIVE_RE
    )
    branch, branch_source = _latest_directive(
        task, _PLAN_BRANCH_DIRECTIVE_RE
    )
    if dependency_text:
        dependencies = [
            value.strip()
            for value in re.split(r"\s*[+,]\s*", dependency_text)
            if value.strip()
        ]
    elif task.get("dependencies") is not None:
        dependencies = _string_list(task.get("dependencies"))
        dependency_source = (
            "ACTIVE_CONTRACT_REBIND"
            if active_contract_rebound
            else "EXPLICIT_TASK_CONTRACT"
        )
    elif executable and previous_executable_task_id:
        dependencies = [previous_executable_task_id]
        dependency_source = "LINEAR_PREDECESSOR"
    else:
        dependencies = []
        dependency_source = (
            "LINEAR_ROOT" if executable else "NON_EXECUTABLE_HISTORY"
        )
    if not git_stage:
        explicit_git_stage = str(task.get("git_commit_stage") or "").strip()
        if explicit_git_stage:
            git_stage = explicit_git_stage
            git_source = (
                "ACTIVE_CONTRACT_REBIND"
                if active_contract_rebound
                else "EXPLICIT_TASK_CONTRACT"
            )
        else:
            outcome = str(task.get("requested_outcome") or "")
            has_commit = bool(
                re.search(r"\bcommit(?:ted|ting|s)?\b", outcome, re.IGNORECASE)
            )
            has_push = bool(
                re.search(r"\bpush(?:ed|ing|es)?\b", outcome, re.IGNORECASE)
            )
            git_stage = (
                "COMMIT_AND_PUSH"
                if has_commit and has_push
                else "COMMIT"
                if has_commit
                else "PUSH"
                if has_push
                else "NOT_DECLARED"
            )
            git_source = (
                "EXACT_TASK_TEXT"
                if has_commit or has_push
                else "NO_EXPLICIT_CONTRACT_OR_TASK_TEXT"
            )
    if not version:
        version = str(
            task.get("current_version") or task.get("version_marker") or "NOT_DECLARED"
        )
        version_source = (
            "EXPLICIT_TASK_CONTRACT"
            if task.get("current_version") or task.get("version_marker")
            else "NO_CURRENT_AUTHORITY_CLAIM"
        )
    if not branch:
        branch = str(
            task.get("current_branch") or task.get("git_branch") or "NOT_DECLARED"
        )
        branch_source = (
            "EXPLICIT_TASK_CONTRACT"
            if task.get("current_branch") or task.get("git_branch")
            else "NO_CURRENT_AUTHORITY_CLAIM"
        )
    return {
        "plan_group": str(group or task.get("plan_group") or task.get("plan_id")),
        "plan_group_source": str(
            group_source
            or (
                "ACTIVE_CONTRACT_REBIND"
                if active_contract_rebound and task.get("plan_group")
                else "EXPLICIT_TASK_CONTRACT"
                if task.get("plan_group")
                else "PLAN_ID_FALLBACK"
            )
        ),
        "commit_batch_id": str(
            batch
            or task.get("commit_batch_id")
            or task.get("batch_id")
            or "UNASSIGNED"
        ),
        "commit_batch_source": str(
            batch_source
            or (
                "EXPLICIT_TASK_CONTRACT"
                if (
                    task.get("commit_batch_id") or task.get("batch_id")
                ) and not active_contract_rebound
                else "ACTIVE_CONTRACT_REBIND"
                if task.get("commit_batch_id") or task.get("batch_id")
                else "NO_EXPLICIT_CONTRACT_OR_LINKED_DIRECTIVE"
            )
        ),
        "dependencies": dependencies,
        "dependency_source": str(dependency_source),
        "git_commit_stage": str(git_stage),
        "git_commit_stage_source": str(git_source),
        "version_marker": str(version).removeprefix("v"),
        "version_marker_source": str(version_source),
        "branch_marker": str(branch),
        "branch_marker_source": str(branch_source),
    }


def _projection_payload(backlog: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    updated_at_by_task = {
        str(event["task_id"]): str(event["recorded_at"]) for event in backlog["events"]
    }
    ordered_tasks = sorted(
        backlog["tasks"],
        key=lambda row: int(row["sequence"]),
    )
    tasks = [
        _task_contract_projection(
            task,
            updated_at=updated_at_by_task.get(
                str(task["task_id"]),
                str(task.get("planned_at") or ""),
            ),
        )
        for task in ordered_tasks
    ]
    task_contract_sha256 = {
        str(task["task_id"]): str(task["task_contract_sha256"])
        for task in tasks
    }
    steers: list[dict[str, Any]] = []
    for task in ordered_tasks:
        for task_steer_sequence, steer in enumerate(
            task.get("steer_deltas") or [],
            start=1,
        ):
            exact = cast(dict[str, Any], steer)
            steers.append(
                {
                    "sequence": len(steers) + 1,
                    "task_steer_sequence": task_steer_sequence,
                    "task_id": str(task["task_id"]),
                    "task_sequence": int(task["sequence"]),
                    "delta_id": str(exact["delta_id"]),
                    "text": str(exact["text"]),
                    "delta_sha256": sha256_bytes(
                        str(exact["text"]).encode("utf-8")
                    ),
                    "boundary": str(exact.get("boundary") or "BEFORE_NEXT_HIL"),
                    "boundary_defaulted": bool(exact.get("boundary_defaulted")),
                    "classification": str(exact.get("classification") or ""),
                    "linked_task_id": str(
                        exact.get("linked_task_id") or task["task_id"]
                    ),
                    "recorded_by": str(exact.get("recorded_by") or ""),
                }
            )
    execution_rows: list[dict[str, Any]] = []
    executable_number = int(backlog.get("goal_row_offset") or 0)
    history_number = 0
    previous_executable_task_id: str | None = None
    steer_ids_by_task: dict[str, list[str]] = {}
    for steer in steers:
        steer_ids_by_task.setdefault(str(steer["task_id"]), []).append(
            str(steer["delta_id"])
        )
    for task in ordered_tasks:
        status = str(task["status"])
        executable = status in _EXECUTABLE_HOST_STATUS
        if executable:
            executable_number += 1
            row_number: int | None = executable_number
            projected_history_number: int | None = None
        else:
            history_number += 1
            row_number = None
            projected_history_number = history_number
        metadata = _runtime_row_metadata(
            task,
            previous_executable_task_id=previous_executable_task_id,
            executable=executable,
        )
        task_id = str(task["task_id"])
        execution_rows.append(
            {
                "task_id": task_id,
                "plan_sequence": int(task["sequence"]),
                "projection_lane": "GOAL" if executable else "HISTORY",
                "row_number": row_number,
                "history_number": projected_history_number,
                "lifecycle_status": status,
                "host_status": _EXECUTABLE_HOST_STATUS.get(status),
                "requested_outcome": str(task["requested_outcome"]),
                "task_classification": str(task["task_class"]),
                "panel_role": str(task.get("panel_role") or "STANDARD"),
                **metadata,
                "effective_for_execution": executable,
                "task_contract_sha256": task_contract_sha256[task_id],
                "linked_delta_ids": steer_ids_by_task.get(task_id, []),
                "steer_count": len(steer_ids_by_task.get(task_id, [])),
            }
        )
        if executable:
            previous_executable_task_id = task_id

    row_by_task = {str(row["task_id"]): row for row in execution_rows}
    fts_records: list[dict[str, Any]] = []
    for task in tasks:
        row = row_by_task[str(task["task_id"])]
        searchable = {
            "task_id": task["task_id"],
            "task_class": task["task_class"],
            "requested_outcome": task["requested_outcome"],
            "permitted_paths": task["permitted_paths"],
            "permitted_tools": task["permitted_tools"],
            "acceptance_checks": task["acceptance_checks"],
            "stop_condition": task["stop_condition"],
            "row": row,
        }
        content = json.dumps(
            searchable,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fts_records.append(
            {
                "sequence": len(fts_records) + 1,
                "record_id": f"task:{task['task_id']}",
                "task_id": str(task["task_id"]),
                "source_kind": "TASK_CONTRACT",
                "source_id": str(task["task_id"]),
                "content": content,
                "content_sha256": sha256_bytes(content.encode("utf-8")),
            }
        )
    for steer in steers:
        content = str(steer["text"])
        fts_records.append(
            {
                "sequence": len(fts_records) + 1,
                "record_id": f"steer:{steer['delta_id']}",
                "task_id": str(steer["task_id"]),
                "source_kind": "STEER_DELTA",
                "source_id": str(steer["delta_id"]),
                "content": content,
                "content_sha256": sha256_bytes(content.encode("utf-8")),
            }
        )
    return {
        "tasks": tasks,
        "steers": steers,
        "execution_rows": execution_rows,
        "fts_records": fts_records,
        "events": [
            {
                "event_id": str(event["event_id"]),
                "sequence": int(event["sequence"]),
                "task_id": str(event["task_id"]),
                "event_type": str(event["event_type"]),
                "from_status": event.get("from_status"),
                "to_status": str(event["to_status"]),
                "actor": str(event["actor"]),
                "recorded_at": str(event["recorded_at"]),
                "previous_global_event_sha256": event.get(
                    "previous_global_event_sha256"
                ),
                "previous_task_event_sha256": event.get("previous_task_event_sha256"),
                "event_sha256": str(event["event_sha256"]),
                "details": event.get("details", {}),
            }
            for event in backlog["events"]
        ],
        "planning_mode_events": [
            {
                "event_id": str(event["event_id"]),
                "sequence": int(event["sequence"]),
                "source_chat_lineage_event_id": str(
                    event["source_chat_lineage_event_id"]
                ),
                "session_id": str(event["session_id"]),
                "request_sha256": str(event["request_sha256"]),
                "selected_mode_ids": event["selected_mode_ids"],
                "mode_intersection": str(event["mode_intersection"]),
                "canonical_lanes": event["canonical_lanes"],
                "lifecycle_state": str(event["lifecycle_state"]),
                "pointer_generation": int(event["pointer_generation"]),
                "recorded_at": str(event["recorded_at"]),
                "previous_event_sha256": event.get("previous_event_sha256"),
                "event_sha256": str(event["event_sha256"]),
            }
            for event in backlog["planning_mode_events"]
        ],
    }


def _read_projection_payload(
    connection: sqlite3.Connection,
) -> dict[str, list[dict[str, Any]]]:
    task_columns = (
        "task_id",
        "sequence",
        "plan_id",
        "task_class",
        "requested_outcome",
        "requested_outcome_sha256",
        "permitted_paths_json",
        "permitted_tools_json",
        "acceptance_checks_json",
        "stop_condition",
        "panel_role",
        "plan_group",
        "commit_batch_id",
        "dependencies_json",
        "git_commit_stage",
        "current_version",
        "current_branch",
        "current_status",
        "supersedes_task_id",
        "superseded_by_task_id",
        "last_event_id",
        "last_event_sha256",
        "updated_at",
        "task_contract_sha256",
    )
    steer_columns = (
        "sequence",
        "task_steer_sequence",
        "task_id",
        "task_sequence",
        "delta_id",
        "text",
        "delta_sha256",
        "boundary",
        "boundary_defaulted",
        "classification",
        "linked_task_id",
        "recorded_by",
    )
    row_columns = (
        "task_id",
        "plan_sequence",
        "projection_lane",
        "row_number",
        "history_number",
        "lifecycle_status",
        "host_status",
        "requested_outcome",
        "task_classification",
        "panel_role",
        "plan_group",
        "plan_group_source",
        "commit_batch_id",
        "commit_batch_source",
        "dependencies_json",
        "dependency_source",
        "git_commit_stage",
        "git_commit_stage_source",
        "version_marker",
        "version_marker_source",
        "branch_marker",
        "branch_marker_source",
        "effective_for_execution",
        "task_contract_sha256",
        "linked_delta_ids_json",
        "steer_count",
    )
    fts_columns = (
        "sequence",
        "record_id",
        "task_id",
        "source_kind",
        "source_id",
        "content",
        "content_sha256",
    )
    event_columns = (
        "event_id",
        "sequence",
        "task_id",
        "event_type",
        "from_status",
        "to_status",
        "actor",
        "recorded_at",
        "previous_global_event_sha256",
        "previous_task_event_sha256",
        "event_sha256",
        "details_json",
    )
    mode_columns = (
        "event_id",
        "sequence",
        "source_chat_lineage_event_id",
        "session_id",
        "request_sha256",
        "selected_mode_ids_json",
        "mode_intersection",
        "canonical_lanes_json",
        "lifecycle_state",
        "pointer_generation",
        "recorded_at",
        "previous_event_sha256",
        "event_sha256",
    )
    tasks = [
        dict(zip(task_columns, row, strict=True))
        for row in connection.execute(
            """
            SELECT
                task_id, sequence, plan_id, task_class,
                requested_outcome, requested_outcome_sha256,
                permitted_paths_json, permitted_tools_json,
                acceptance_checks_json, stop_condition, panel_role,
                plan_group, commit_batch_id, dependencies_json,
                git_commit_stage, current_version, current_branch,
                current_status,
                supersedes_task_id, superseded_by_task_id,
                last_event_id, last_event_sha256, updated_at,
                task_contract_sha256
            FROM delta_task
            ORDER BY sequence
            """
        ).fetchall()
    ]
    for task in tasks:
        task["permitted_paths"] = json.loads(
            str(task.pop("permitted_paths_json"))
        )
        task["permitted_tools"] = json.loads(
            str(task.pop("permitted_tools_json"))
        )
        task["acceptance_checks"] = json.loads(
            str(task.pop("acceptance_checks_json"))
        )
        task["dependencies"] = json.loads(
            str(task.pop("dependencies_json"))
        )
    steers = []
    for row in connection.execute(
        """
        SELECT
            sequence, task_steer_sequence, task_id, task_sequence,
            delta_id, text, delta_sha256, boundary,
            boundary_defaulted, classification, linked_task_id, recorded_by
        FROM steer_delta
        ORDER BY sequence
        """
    ).fetchall():
        item = dict(zip(steer_columns, row, strict=True))
        item["boundary_defaulted"] = bool(item["boundary_defaulted"])
        steers.append(item)
    execution_rows = []
    for row in connection.execute(
        """
        SELECT
            task_id, plan_sequence, projection_lane, row_number,
            history_number, lifecycle_status, host_status,
            requested_outcome, task_classification, panel_role,
            plan_group, plan_group_source, commit_batch_id,
            commit_batch_source, dependencies_json, dependency_source,
            git_commit_stage, git_commit_stage_source, version_marker,
            version_marker_source, branch_marker, branch_marker_source,
            effective_for_execution, task_contract_sha256,
            linked_delta_ids_json, steer_count
        FROM plan_execution_row
        ORDER BY plan_sequence
        """
    ).fetchall():
        item = dict(zip(row_columns, row, strict=True))
        item["dependencies"] = json.loads(str(item.pop("dependencies_json")))
        item["linked_delta_ids"] = json.loads(
            str(item.pop("linked_delta_ids_json"))
        )
        item["effective_for_execution"] = bool(
            item["effective_for_execution"]
        )
        execution_rows.append(item)
    fts_records = [
        dict(zip(fts_columns, row, strict=True))
        for row in connection.execute(
            """
            SELECT
                CAST(sequence AS INTEGER), record_id, task_id, source_kind,
                source_id, content, content_sha256
            FROM plan_runtime_fts
            ORDER BY sequence
            """
        ).fetchall()
    ]
    events = []
    for row in connection.execute(
        """
        SELECT
            event_id, sequence, task_id, event_type, from_status,
            to_status, actor, recorded_at, previous_global_event_sha256,
            previous_task_event_sha256, event_sha256, details_json
        FROM delta_event
        ORDER BY sequence
        """
    ).fetchall():
        item = dict(zip(event_columns, row, strict=True))
        item["details"] = json.loads(str(item.pop("details_json")))
        events.append(item)
    planning_mode_events = []
    for row in connection.execute(
        """
        SELECT
            event_id, sequence, source_chat_lineage_event_id, session_id,
            request_sha256, selected_mode_ids_json, mode_intersection,
            canonical_lanes_json, lifecycle_state, pointer_generation,
            recorded_at, previous_event_sha256, event_sha256
        FROM planning_mode_event
        ORDER BY sequence
        """
    ).fetchall():
        item = dict(zip(mode_columns, row, strict=True))
        item["selected_mode_ids"] = json.loads(str(item.pop("selected_mode_ids_json")))
        item["canonical_lanes"] = json.loads(str(item.pop("canonical_lanes_json")))
        planning_mode_events.append(item)
    return {
        "tasks": tasks,
        "steers": steers,
        "execution_rows": execution_rows,
        "fts_records": fts_records,
        "events": events,
        "planning_mode_events": planning_mode_events,
    }


def write_plan_runtime_projection(
    path: Path,
    backlog: dict[str, Any],
) -> None:
    """Atomically rebuild the derived SQLite projection from canonical ledgers."""

    ensure_event_ledger(backlog)
    projection = _projection_payload(backlog)
    projection_content_sha256 = sha256_bytes(canonical_json_bytes(projection))
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE projection_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE delta_task (
                    task_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL UNIQUE,
                    plan_id TEXT NOT NULL,
                    task_class TEXT NOT NULL,
                    requested_outcome TEXT NOT NULL,
                    requested_outcome_sha256 TEXT NOT NULL,
                    permitted_paths_json TEXT NOT NULL,
                    permitted_tools_json TEXT NOT NULL,
                    acceptance_checks_json TEXT NOT NULL,
                    stop_condition TEXT NOT NULL,
                    panel_role TEXT NOT NULL,
                    plan_group TEXT,
                    commit_batch_id TEXT,
                    dependencies_json TEXT NOT NULL,
                    git_commit_stage TEXT,
                    current_version TEXT,
                    current_branch TEXT,
                    current_status TEXT NOT NULL,
                    supersedes_task_id TEXT,
                    superseded_by_task_id TEXT,
                    last_event_id TEXT,
                    last_event_sha256 TEXT,
                    updated_at TEXT NOT NULL,
                    task_contract_sha256 TEXT NOT NULL
                );
                CREATE TABLE steer_delta (
                    sequence INTEGER PRIMARY KEY,
                    task_steer_sequence INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    task_sequence INTEGER NOT NULL,
                    delta_id TEXT NOT NULL UNIQUE,
                    text TEXT NOT NULL,
                    delta_sha256 TEXT NOT NULL,
                    boundary TEXT NOT NULL,
                    boundary_defaulted INTEGER NOT NULL,
                    classification TEXT NOT NULL,
                    linked_task_id TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    UNIQUE (task_id, task_steer_sequence),
                    FOREIGN KEY (task_id) REFERENCES delta_task(task_id),
                    FOREIGN KEY (linked_task_id) REFERENCES delta_task(task_id)
                );
                CREATE TABLE plan_execution_row (
                    task_id TEXT PRIMARY KEY,
                    plan_sequence INTEGER NOT NULL UNIQUE,
                    projection_lane TEXT NOT NULL,
                    row_number INTEGER UNIQUE,
                    history_number INTEGER UNIQUE,
                    lifecycle_status TEXT NOT NULL,
                    host_status TEXT,
                    requested_outcome TEXT NOT NULL,
                    task_classification TEXT NOT NULL,
                    panel_role TEXT NOT NULL,
                    plan_group TEXT NOT NULL,
                    plan_group_source TEXT NOT NULL,
                    commit_batch_id TEXT NOT NULL,
                    commit_batch_source TEXT NOT NULL,
                    dependencies_json TEXT NOT NULL,
                    dependency_source TEXT NOT NULL,
                    git_commit_stage TEXT NOT NULL,
                    git_commit_stage_source TEXT NOT NULL,
                    version_marker TEXT NOT NULL,
                    version_marker_source TEXT NOT NULL,
                    branch_marker TEXT NOT NULL,
                    branch_marker_source TEXT NOT NULL,
                    effective_for_execution INTEGER NOT NULL,
                    task_contract_sha256 TEXT NOT NULL,
                    linked_delta_ids_json TEXT NOT NULL,
                    steer_count INTEGER NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES delta_task(task_id)
                );
                CREATE TABLE delta_event (
                    event_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL UNIQUE,
                    task_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    previous_global_event_sha256 TEXT,
                    previous_task_event_sha256 TEXT,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY (task_id) REFERENCES delta_task(task_id)
                );
                CREATE TABLE planning_mode_event (
                    event_id TEXT PRIMARY KEY,
                    sequence INTEGER NOT NULL UNIQUE,
                    source_chat_lineage_event_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    selected_mode_ids_json TEXT NOT NULL,
                    mode_intersection TEXT NOT NULL,
                    canonical_lanes_json TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    pointer_generation INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    previous_event_sha256 TEXT,
                    event_sha256 TEXT NOT NULL UNIQUE
                );
                CREATE INDEX delta_event_task_idx
                    ON delta_event(task_id, sequence);
                CREATE INDEX steer_delta_task_idx
                    ON steer_delta(task_id, task_steer_sequence);
                CREATE INDEX plan_execution_lane_row_idx
                    ON plan_execution_row(projection_lane, row_number, history_number);
                CREATE VIRTUAL TABLE plan_runtime_fts USING fts5(
                    sequence UNINDEXED,
                    record_id UNINDEXED,
                    task_id UNINDEXED,
                    source_kind UNINDEXED,
                    source_id UNINDEXED,
                    content,
                    content_sha256 UNINDEXED,
                    tokenize = 'unicode61'
                );
                """
            )
            for task in projection["tasks"]:
                connection.execute(
                    """
                    INSERT INTO delta_task (
                        task_id, sequence, plan_id, task_class,
                        requested_outcome, requested_outcome_sha256,
                        permitted_paths_json, permitted_tools_json,
                        acceptance_checks_json, stop_condition, panel_role,
                        plan_group, commit_batch_id, dependencies_json,
                        git_commit_stage, current_version, current_branch,
                        current_status,
                        supersedes_task_id, superseded_by_task_id,
                        last_event_id, last_event_sha256, updated_at,
                        task_contract_sha256
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        task["task_id"],
                        int(task["sequence"]),
                        task["plan_id"],
                        task["task_class"],
                        task["requested_outcome"],
                        task["requested_outcome_sha256"],
                        json.dumps(task["permitted_paths"], separators=(",", ":")),
                        json.dumps(task["permitted_tools"], separators=(",", ":")),
                        json.dumps(task["acceptance_checks"], separators=(",", ":")),
                        task["stop_condition"],
                        task["panel_role"],
                        task.get("plan_group"),
                        task.get("commit_batch_id"),
                        json.dumps(task["dependencies"], separators=(",", ":")),
                        task.get("git_commit_stage"),
                        task.get("current_version"),
                        task.get("current_branch"),
                        task["current_status"],
                        task.get("supersedes_task_id"),
                        task.get("superseded_by_task_id"),
                        task.get("last_event_id"),
                        task.get("last_event_sha256"),
                        task["updated_at"],
                        task["task_contract_sha256"],
                    ),
                )
            for steer in projection["steers"]:
                connection.execute(
                    """
                    INSERT INTO steer_delta (
                        sequence, task_steer_sequence, task_id, task_sequence,
                        delta_id, text, delta_sha256, boundary,
                        boundary_defaulted, classification, linked_task_id,
                        recorded_by
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(steer["sequence"]),
                        int(steer["task_steer_sequence"]),
                        steer["task_id"],
                        int(steer["task_sequence"]),
                        steer["delta_id"],
                        steer["text"],
                        steer["delta_sha256"],
                        steer["boundary"],
                        int(bool(steer["boundary_defaulted"])),
                        steer["classification"],
                        steer["linked_task_id"],
                        steer["recorded_by"],
                    ),
                )
            for row in projection["execution_rows"]:
                connection.execute(
                    """
                    INSERT INTO plan_execution_row (
                        task_id, plan_sequence, projection_lane, row_number,
                        history_number, lifecycle_status, host_status,
                        requested_outcome, task_classification, panel_role,
                        plan_group, plan_group_source, commit_batch_id,
                        commit_batch_source, dependencies_json,
                        dependency_source, git_commit_stage,
                        git_commit_stage_source, version_marker,
                        version_marker_source, branch_marker,
                        branch_marker_source, effective_for_execution,
                        task_contract_sha256, linked_delta_ids_json, steer_count
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        row["task_id"],
                        int(row["plan_sequence"]),
                        row["projection_lane"],
                        row.get("row_number"),
                        row.get("history_number"),
                        row["lifecycle_status"],
                        row.get("host_status"),
                        row["requested_outcome"],
                        row["task_classification"],
                        row["panel_role"],
                        row["plan_group"],
                        row["plan_group_source"],
                        row["commit_batch_id"],
                        row["commit_batch_source"],
                        json.dumps(row["dependencies"], separators=(",", ":")),
                        row["dependency_source"],
                        row["git_commit_stage"],
                        row["git_commit_stage_source"],
                        row["version_marker"],
                        row["version_marker_source"],
                        row["branch_marker"],
                        row["branch_marker_source"],
                        int(bool(row["effective_for_execution"])),
                        row["task_contract_sha256"],
                        json.dumps(row["linked_delta_ids"], separators=(",", ":")),
                        int(row["steer_count"]),
                    ),
                )
            for record in projection["fts_records"]:
                connection.execute(
                    """
                    INSERT INTO plan_runtime_fts (
                        sequence, record_id, task_id, source_kind,
                        source_id, content, content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(record["sequence"]),
                        record["record_id"],
                        record["task_id"],
                        record["source_kind"],
                        record["source_id"],
                        record["content"],
                        record["content_sha256"],
                    ),
                )
            for event in projection["events"]:
                connection.execute(
                    """
                    INSERT INTO delta_event (
                        event_id, sequence, task_id, event_type,
                        from_status, to_status, actor, recorded_at,
                        previous_global_event_sha256,
                        previous_task_event_sha256, event_sha256,
                        details_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        int(event["sequence"]),
                        event["task_id"],
                        event["event_type"],
                        event.get("from_status"),
                        event["to_status"],
                        event["actor"],
                        event["recorded_at"],
                        event.get("previous_global_event_sha256"),
                        event.get("previous_task_event_sha256"),
                        event["event_sha256"],
                        json.dumps(
                            event.get("details", {}),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                )
            for event in projection["planning_mode_events"]:
                connection.execute(
                    """
                    INSERT INTO planning_mode_event (
                        event_id, sequence, source_chat_lineage_event_id,
                        session_id, request_sha256, selected_mode_ids_json,
                        mode_intersection, canonical_lanes_json,
                        lifecycle_state, pointer_generation, recorded_at,
                        previous_event_sha256, event_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["event_id"],
                        int(event["sequence"]),
                        event["source_chat_lineage_event_id"],
                        event["session_id"],
                        event["request_sha256"],
                        json.dumps(
                            event["selected_mode_ids"],
                            separators=(",", ":"),
                        ),
                        event["mode_intersection"],
                        json.dumps(
                            event["canonical_lanes"],
                            separators=(",", ":"),
                        ),
                        event["lifecycle_state"],
                        int(event["pointer_generation"]),
                        event["recorded_at"],
                        event.get("previous_event_sha256"),
                        event["event_sha256"],
                    ),
                )
            metadata = {
                "schema": PLAN_RUNTIME_SCHEMA,
                "backlog_schema": backlog["schema"],
                "event_head_sha256": backlog.get("event_head_sha256") or "",
                "planning_mode_event_head_sha256": backlog.get(
                    "planning_mode_event_head_sha256"
                )
                or "",
                "projection_content_sha256": projection_content_sha256,
                "canonical_plan_sector_mutated": "false",
                "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
                "full_task_contracts_indexed": "true",
                "steer_deltas_indexed": "true",
                "execution_and_history_rows_indexed": "true",
                "fts5_enabled": "true",
                "accepted_pv_payload_copied": "false",
                "raw_pv_model_context_loading": "false",
                "raw_chat_scrollback_model_context_loading": "false",
                "detail_lookup_policy": "EXACT_TASK_ID_THEN_BOUNDED_FTS",
                "memory_sqlite_authority": "SEPARATE_FROM_AI_LEARNING_AND_PROJECT_TRUTH",
            }
            connection.executemany(
                "INSERT INTO projection_meta (key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )
            connection.execute(f"PRAGMA user_version = {PLAN_RUNTIME_USER_VERSION}")
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchall()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            require(
                integrity == [("ok",)] and foreign_keys == [],
                "PLAN_RUNTIME_PROJECTION_INVALID",
                "The derived Plan runtime SQLite projection failed validation.",
                status="FAILED",
                integrity=integrity,
                foreign_key_errors=foreign_keys,
            )
        finally:
            connection.close()
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def plan_runtime_status(
    path: Path,
    backlog: dict[str, Any],
) -> dict[str, Any]:
    """Verify the derived Plan runtime projection without mutating it."""

    ensure_event_ledger(backlog)
    expected_event_head = backlog.get("event_head_sha256") or ""
    expected_mode_head = backlog.get("planning_mode_event_head_sha256") or ""
    expected_projection = _projection_payload(backlog)
    expected_projection_sha256 = sha256_bytes(canonical_json_bytes(expected_projection))
    if not path.is_file():
        return {
            "status": "NOT_BUILT",
            "schema": PLAN_RUNTIME_SCHEMA,
            "path": str(path),
            "event_count": len(backlog["events"]),
            "planning_mode_event_count": len(backlog["planning_mode_events"]),
            "steer_count": len(expected_projection["steers"]),
            "execution_row_count": sum(
                row["projection_lane"] == "GOAL"
                for row in expected_projection["execution_rows"]
            ),
            "history_row_count": sum(
                row["projection_lane"] == "HISTORY"
                for row in expected_projection["execution_rows"]
            ),
            "fts_record_count": len(expected_projection["fts_records"]),
            "expected_event_head_sha256": expected_event_head or None,
            "expected_planning_mode_event_head_sha256": (expected_mode_head or None),
            "expected_projection_content_sha256": expected_projection_sha256,
            "canonical_plan_sector_mutated": False,
            "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
        }
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        metadata = dict(
            connection.execute(
                "SELECT key, value FROM projection_meta ORDER BY key"
            ).fetchall()
        )
        # A derived projection from an older installed plugin is expected during
        # hot upgrade.  Detect it before selecting v2-only columns/tables.  The
        # canonical Plan ledger remains readable and the next governed write
        # atomically rebuilds this disposable index; read-only status must never
        # crash or mutate the legacy projection.
        if (
            user_version != PLAN_RUNTIME_USER_VERSION
            or metadata.get("schema") != PLAN_RUNTIME_SCHEMA
        ):
            return {
                "status": "STALE",
                "reason": "DERIVED_SCHEMA_REBUILD_REQUIRED",
                "schema": PLAN_RUNTIME_SCHEMA,
                "observed_schema": metadata.get("schema"),
                "path": str(path),
                "sqlite_sha256": sha256_file(path),
                "sqlite_user_version": user_version,
                "expected_sqlite_user_version": PLAN_RUNTIME_USER_VERSION,
                "integrity": integrity,
                "foreign_key_errors": foreign_keys,
                "event_count": len(backlog["events"]),
                "planning_mode_event_count": len(
                    backlog["planning_mode_events"]
                ),
                "steer_count": len(expected_projection["steers"]),
                "execution_row_count": sum(
                    row["projection_lane"] == "GOAL"
                    for row in expected_projection["execution_rows"]
                ),
                "history_row_count": sum(
                    row["projection_lane"] == "HISTORY"
                    for row in expected_projection["execution_rows"]
                ),
                "fts_record_count": len(expected_projection["fts_records"]),
                "expected_projection_content_sha256": (
                    expected_projection_sha256
                ),
                "expected_event_head_sha256": expected_event_head or None,
                "expected_planning_mode_event_head_sha256": (
                    expected_mode_head or None
                ),
                "canonical_plan_sector_mutated": False,
                "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
                "rebuild_action": "NEXT_GOVERNED_PLAN_WRITE_ATOMIC_REBUILD",
                "raw_pv_model_context_loading": False,
                "raw_chat_scrollback_model_context_loading": False,
            }
        projection = _read_projection_payload(connection)
        projection_content_sha256 = sha256_bytes(canonical_json_bytes(projection))
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM delta_event").fetchone()[0]
        )
        mode_count = int(
            connection.execute("SELECT COUNT(*) FROM planning_mode_event").fetchone()[0]
        )
        steer_count = int(
            connection.execute("SELECT COUNT(*) FROM steer_delta").fetchone()[0]
        )
        execution_row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM plan_execution_row WHERE projection_lane = 'GOAL'"
            ).fetchone()[0]
        )
        history_row_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM plan_execution_row WHERE projection_lane = 'HISTORY'"
            ).fetchone()[0]
        )
        fts_record_count = int(
            connection.execute("SELECT COUNT(*) FROM plan_runtime_fts").fetchone()[0]
        )
        task_counts = {
            str(status): int(count)
            for status, count in connection.execute(
                """
                SELECT current_status, COUNT(*)
                FROM delta_task
                GROUP BY current_status
                ORDER BY current_status
                """
            ).fetchall()
        }
    finally:
        connection.close()
    fresh = (
        integrity == ["ok"]
        and foreign_keys == []
        and metadata.get("schema") == PLAN_RUNTIME_SCHEMA
        and metadata.get("event_head_sha256") == expected_event_head
        and metadata.get("planning_mode_event_head_sha256") == expected_mode_head
        and metadata.get("projection_content_sha256") == expected_projection_sha256
        and projection_content_sha256 == expected_projection_sha256
        and event_count == len(backlog["events"])
        and mode_count == len(backlog["planning_mode_events"])
        and steer_count == len(expected_projection["steers"])
        and execution_row_count
        == sum(
            row["projection_lane"] == "GOAL"
            for row in expected_projection["execution_rows"]
        )
        and history_row_count
        == sum(
            row["projection_lane"] == "HISTORY"
            for row in expected_projection["execution_rows"]
        )
        and fts_record_count == len(expected_projection["fts_records"])
        and metadata.get("full_task_contracts_indexed") == "true"
        and metadata.get("steer_deltas_indexed") == "true"
        and metadata.get("execution_and_history_rows_indexed") == "true"
        and metadata.get("fts5_enabled") == "true"
        and metadata.get("accepted_pv_payload_copied") == "false"
        and metadata.get("raw_pv_model_context_loading") == "false"
        and metadata.get("raw_chat_scrollback_model_context_loading") == "false"
        and metadata.get("detail_lookup_policy")
        == "EXACT_TASK_ID_THEN_BOUNDED_FTS"
        and metadata.get("memory_sqlite_authority")
        == "SEPARATE_FROM_AI_LEARNING_AND_PROJECT_TRUTH"
        and user_version == PLAN_RUNTIME_USER_VERSION
    )
    expected_counts = Counter(str(task["status"]) for task in backlog.get("tasks", []))
    fresh = fresh and task_counts == dict(sorted(expected_counts.items()))
    return {
        "status": "PASS" if fresh else "STALE",
        "schema": PLAN_RUNTIME_SCHEMA,
        "path": str(path),
        "sqlite_sha256": sha256_file(path),
        "sqlite_user_version": user_version,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "event_count": event_count,
        "planning_mode_event_count": mode_count,
        "steer_count": steer_count,
        "execution_row_count": execution_row_count,
        "history_row_count": history_row_count,
        "fts_record_count": fts_record_count,
        "fts5_enabled": metadata.get("fts5_enabled") == "true",
        "full_task_contracts_indexed": (
            metadata.get("full_task_contracts_indexed") == "true"
        ),
        "steer_deltas_indexed": metadata.get("steer_deltas_indexed") == "true",
        "accepted_pv_payload_copied": False,
        "raw_pv_model_context_loading": False,
        "raw_chat_scrollback_model_context_loading": False,
        "detail_lookup_policy": metadata.get("detail_lookup_policy"),
        "memory_sqlite_authority": metadata.get("memory_sqlite_authority"),
        "task_counts": task_counts,
        "event_head_sha256": metadata.get("event_head_sha256") or None,
        "planning_mode_event_head_sha256": metadata.get(
            "planning_mode_event_head_sha256"
        )
        or None,
        "projection_content_sha256": projection_content_sha256,
        "expected_projection_content_sha256": expected_projection_sha256,
        "expected_event_head_sha256": expected_event_head or None,
        "expected_planning_mode_event_head_sha256": expected_mode_head or None,
        "canonical_plan_sector_mutated": False,
        "projection_role": "DERIVED_CONTROL_PLANE_INDEX",
    }


def query_plan_runtime_projection(
    path: Path,
    *,
    task_id: str | None = None,
    query: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Read one exact Plan row or a bounded FTS slice without loading a PV."""

    exact_task_id = str(task_id or "").strip()
    exact_query = str(query or "").strip()
    require(
        bool(exact_task_id) != bool(exact_query),
        "PLAN_RUNTIME_QUERY_SELECTOR_REQUIRED",
        "Select exactly one exact task ID or one bounded FTS query.",
        status="BLOCKED",
    )
    require(
        1 <= int(limit) <= 20,
        "PLAN_RUNTIME_QUERY_LIMIT_INVALID",
        "Plan runtime retrieval is bounded to at most twenty records.",
        status="BLOCKED",
        limit=limit,
    )
    require(
        path.is_file(),
        "PLAN_RUNTIME_PROJECTION_NOT_BUILT",
        "The live Plan runtime projection is not available.",
        status="BLOCKED",
        path=str(path),
    )
    uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        if exact_task_id:
            row = connection.execute(
                """
                SELECT *
                FROM plan_execution_row
                WHERE task_id = ?
                """,
                (exact_task_id,),
            ).fetchone()
            require(
                row is not None,
                "PLAN_RUNTIME_TASK_NOT_FOUND",
                "The exact Plan task ID is not present in the live runtime index.",
                status="NOT_FOUND",
                task_id=exact_task_id,
            )
            contract = connection.execute(
                """
                SELECT *
                FROM delta_task
                WHERE task_id = ?
                """,
                (exact_task_id,),
            ).fetchone()
            steers = connection.execute(
                """
                SELECT
                    delta_id, text, delta_sha256, boundary,
                    classification, recorded_by, task_steer_sequence
                FROM steer_delta
                WHERE task_id = ?
                ORDER BY task_steer_sequence
                LIMIT ?
                """,
                (exact_task_id, int(limit)),
            ).fetchall()
            return {
                "status": "PASS",
                "schema": "evidence-lane.plan-runtime-query.v1",
                "query_mode": "EXACT_TASK_ID",
                "task_id": exact_task_id,
                "row": dict(row),
                "contract": dict(cast(sqlite3.Row, contract)),
                "steers": [dict(item) for item in steers],
                "steer_result_truncated": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM steer_delta WHERE task_id = ?",
                        (exact_task_id,),
                    ).fetchone()[0]
                )
                > len(steers),
                "accepted_pv_payload_loaded": False,
                "raw_chat_scrollback_loaded": False,
            }
        tokens = re.findall(r"[A-Za-z0-9_.-]+", exact_query)
        require(
            bool(tokens) and len(tokens) <= 16 and len(exact_query) <= 512,
            "PLAN_RUNTIME_FTS_QUERY_INVALID",
            "The Plan FTS query must contain at most sixteen bounded tokens.",
            status="BLOCKED",
        )
        fts_query = " AND ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        hits = connection.execute(
            """
            SELECT
                record_id, task_id, source_kind, source_id,
                snippet(plan_runtime_fts, 5, '[', ']', ' ... ', 32) AS snippet,
                bm25(plan_runtime_fts) AS rank,
                content_sha256
            FROM plan_runtime_fts
            WHERE plan_runtime_fts MATCH ?
            ORDER BY rank, sequence
            LIMIT ?
            """,
            (fts_query, int(limit)),
        ).fetchall()
        return {
            "status": "PASS",
            "schema": "evidence-lane.plan-runtime-query.v1",
            "query_mode": "BOUNDED_FTS5",
            "query_sha256": sha256_bytes(exact_query.encode("utf-8")),
            "tokens": tokens,
            "limit": int(limit),
            "hits": [dict(hit) for hit in hits],
            "accepted_pv_payload_loaded": False,
            "raw_chat_scrollback_loaded": False,
        }
    finally:
        connection.close()
