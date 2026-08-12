"""Append-only Delta lifecycle law and derived Plan runtime projection."""

from __future__ import annotations

import json
import os
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
PLAN_RUNTIME_SCHEMA = "evidence-lane.plan-runtime-projection.v1"
PLAN_RUNTIME_USER_VERSION = 1

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


def _projection_payload(backlog: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    updated_at_by_task = {
        str(event["task_id"]): str(event["recorded_at"]) for event in backlog["events"]
    }
    return {
        "tasks": [
            {
                "task_id": str(task["task_id"]),
                "sequence": int(task["sequence"]),
                "plan_id": str(task["plan_id"]),
                "task_class": str(task["task_class"]),
                "requested_outcome_sha256": sha256_bytes(
                    str(task["requested_outcome"]).encode("utf-8")
                ),
                "current_status": str(task["status"]),
                "supersedes_task_id": task.get("supersedes_task_id"),
                "superseded_by_task_id": task.get("superseded_by_task_id"),
                "last_event_id": task.get("last_event_id"),
                "last_event_sha256": task.get("last_event_sha256"),
                "updated_at": updated_at_by_task[str(task["task_id"])],
            }
            for task in sorted(
                backlog["tasks"],
                key=lambda row: int(row["sequence"]),
            )
        ],
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
        "requested_outcome_sha256",
        "current_status",
        "supersedes_task_id",
        "superseded_by_task_id",
        "last_event_id",
        "last_event_sha256",
        "updated_at",
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
                requested_outcome_sha256, current_status,
                supersedes_task_id, superseded_by_task_id,
                last_event_id, last_event_sha256, updated_at
            FROM delta_task
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
                    requested_outcome_sha256 TEXT NOT NULL,
                    current_status TEXT NOT NULL,
                    supersedes_task_id TEXT,
                    superseded_by_task_id TEXT,
                    last_event_id TEXT,
                    last_event_sha256 TEXT,
                    updated_at TEXT NOT NULL
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
                """
            )
            for task in projection["tasks"]:
                connection.execute(
                    """
                    INSERT INTO delta_task (
                        task_id, sequence, plan_id, task_class,
                        requested_outcome_sha256, current_status,
                        supersedes_task_id, superseded_by_task_id,
                        last_event_id, last_event_sha256, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task["task_id"],
                        int(task["sequence"]),
                        task["plan_id"],
                        task["task_class"],
                        task["requested_outcome_sha256"],
                        task["current_status"],
                        task.get("supersedes_task_id"),
                        task.get("superseded_by_task_id"),
                        task.get("last_event_id"),
                        task.get("last_event_sha256"),
                        task["updated_at"],
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
        metadata = dict(
            connection.execute(
                "SELECT key, value FROM projection_meta ORDER BY key"
            ).fetchall()
        )
        projection = _read_projection_payload(connection)
        projection_content_sha256 = sha256_bytes(canonical_json_bytes(projection))
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM delta_event").fetchone()[0]
        )
        mode_count = int(
            connection.execute("SELECT COUNT(*) FROM planning_mode_event").fetchone()[0]
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
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
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
