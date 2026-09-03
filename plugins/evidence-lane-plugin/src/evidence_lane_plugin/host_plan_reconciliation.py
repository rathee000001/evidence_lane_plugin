"""Dynamic, Store-owned host Plan reconciliation and review-only rewrites.

This module deliberately contains no public MCP route.  ``ProjectStore`` owns
the lock and calls the file transaction below; ``SessionManager`` is only the
host-facing coordinator.  The pure builders are also used for dry-run review.
"""

from __future__ import annotations

import copy
import json
import os
import re
import sqlite3
from collections.abc import Iterable
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
from .plan_runtime import (
    append_delta_event,
    ensure_event_ledger,
    write_plan_runtime_projection,
)
from .timeutil import utc_now

HOST_PLAN_SNAPSHOT_SCHEMA = "evidence-lane.host-plan-snapshot.v2"
HOST_PLAN_RECONCILIATION_SCHEMA = "evidence-lane.host-plan-reconciliation.v2"
HOST_PLAN_DRY_RUN_SCHEMA = "evidence-lane.host-plan-downstream-dry-run.v2"
HOST_PLAN_JOURNAL_SCHEMA = "evidence-lane.host-plan-reconciliation-journal.v1"

_SHA256_RE = re.compile(r"[A-F0-9]{64}")
_TASK_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_HOST_STATUS_TO_LIFECYCLE = {
    "completed": "DONE",
    "in_progress": "ACTIVE",
    "pending": "QUEUED",
}
_LIFECYCLE_TO_HOST_STATUS = {
    "DONE": "completed",
    "ACCEPTED": "completed",
    "ACTIVE": "in_progress",
    "QUEUED": "pending",
}
_EXECUTABLE_STATUSES = frozenset({"DONE", "ACCEPTED", "ACTIVE", "QUEUED"})
_HISTORY_STATUSES = frozenset({"SUPERSEDED", "DROPPED"})
UNIVERSAL_DELTA_IMPLEMENTATION_PURGE_LAW = (
    "Same-row universal Delta law: implement or refresh only this row's current "
    "owner; atomically reuse unchanged content-addressed evidence; directly purge "
    "every superseded executable, schema, generated artifact, test, fixture, and "
    "source-derived documentation consumer exposed by this row; preserve immutable "
    "external evidence only as non-executable history."
)
_UNIVERSAL_DELTA_ACCEPTANCE_CHECKS = [
    "CURRENT_ROW_OWNER_ONLY",
    "UNCHANGED_CONTENT_ADDRESSED_EVIDENCE_REUSED",
    "SUPERSEDED_EXECUTABLE_CONSUMERS_DIRECTLY_PURGED",
    "IMMUTABLE_EXTERNAL_EVIDENCE_PRESERVED_NON_EXECUTABLE",
]


def _require_sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        _SHA256_RE.fullmatch(exact) is not None,
        "HOST_PLAN_SHA256_INVALID",
        "Every host Plan identity must be one exact SHA-256.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _require_task_id(value: Any, *, field: str) -> str:
    exact = str(value or "").strip()
    require(
        _TASK_ID_RE.fullmatch(exact) is not None,
        "HOST_PLAN_TASK_ID_INVALID",
        "Every dynamic host Plan row must use one bounded stable task identity.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _canonical_backlog_sha256(backlog: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(backlog))


def _projection_membership_sha256(task_ids: list[str]) -> str:
    return sha256_bytes(canonical_json_bytes(task_ids))


def _normalize_string_list(value: Any, *, field: str) -> list[str]:
    require(
        isinstance(value, list)
        and all(isinstance(item, str) and item.strip() for item in value),
        "HOST_PLAN_STRING_LIST_INVALID",
        "A dynamic host Plan string-list field is invalid.",
        status="BLOCKED",
        field=field,
    )
    return [item.strip() for item in value]


def create_dynamic_host_snapshot(
    *,
    source_authority_id: str,
    anchor_task_id: str,
    rows: list[dict[str, Any]],
    captured_at: str | None = None,
    host_row_offset: int = 0,
) -> dict[str, Any]:
    """Validate and hash one complete, dynamically sized native host list."""

    exact_source = str(source_authority_id or "").strip()
    exact_anchor = _require_task_id(anchor_task_id, field="anchor_task_id")
    require(
        exact_source == "PLAN_LANE" or exact_source.startswith("PLAN_LANE:"),
        "HOST_PLAN_SOURCE_AUTHORITY_INVALID",
        "The canonical Plan Lane alone may own ordered work; the host list is projection-only.",
        status="BLOCKED",
        source_authority_id=exact_source or None,
    )
    require(
        bool(rows) and isinstance(host_row_offset, int) and host_row_offset >= 0,
        "HOST_PLAN_SNAPSHOT_EMPTY",
        "A dynamic host Plan snapshot requires rows and a nonnegative retained-row offset.",
        status="BLOCKED",
    )
    normalized: list[dict[str, Any]] = []
    seen_host_ids: set[str] = set()
    seen_task_ids: set[str] = set()
    for position, raw in enumerate(rows, start=1):
        require(
            isinstance(raw, dict),
            "HOST_PLAN_ROW_INVALID",
            "Every dynamic host Plan row must be one structured contract.",
            status="BLOCKED",
            position=position,
        )
        host_row_id = _require_task_id(
            raw.get("host_row_id"), field=f"rows[{position}].host_row_id"
        )
        task_id = _require_task_id(
            raw.get("task_id"), field=f"rows[{position}].task_id"
        )
        display_text = str(raw.get("display_text") or "").strip()
        canonical_outcome = str(
            raw.get("canonical_requested_outcome") or display_text
        ).strip()
        host_status = str(raw.get("status") or "").strip()
        panel_role = str(raw.get("panel_role") or "STANDARD").strip().upper()
        phase = str(raw.get("phase") or "UNASSIGNED").strip().upper()
        raw_supersedes = list(raw.get("supersedes_task_ids") or [])
        singular_supersedes = str(raw.get("supersedes_task_id") or "").strip()
        if singular_supersedes:
            require(
                not raw_supersedes or raw_supersedes == [singular_supersedes],
                "HOST_PLAN_REPLACEMENT_SHAPE_AMBIGUOUS",
                "A row may use singular or grouped replacement identity, not both.",
                status="BLOCKED",
                position=position,
            )
            if not raw_supersedes:
                raw_supersedes = [singular_supersedes]
        supersedes_task_ids = [
            _require_task_id(
                item,
                field=f"rows[{position}].supersedes_task_ids",
            )
            for item in raw_supersedes
        ]
        path_scope = _normalize_string_list(
            list(raw.get("path_scope") or []), field=f"rows[{position}].path_scope"
        )
        workflow_gates = _normalize_string_list(
            list(raw.get("workflow_gates") or []),
            field=f"rows[{position}].workflow_gates",
        )
        acceptance_checks = _normalize_string_list(
            list(raw.get("acceptance_checks") or ["NO_INFERRED_HIL"]),
            field=f"rows[{position}].acceptance_checks",
        )
        require(
            bool(display_text)
            and bool(canonical_outcome)
            and host_status in _HOST_STATUS_TO_LIFECYCLE
            and panel_role in {"STANDARD", "HIL_GATE", "PHYSICALLY_FINAL_HIL"}
            and host_row_id not in seen_host_ids
            and task_id not in seen_task_ids
            and task_id not in supersedes_task_ids
            and len(supersedes_task_ids) == len(set(supersedes_task_ids)),
            "HOST_PLAN_ROW_INVALID",
            "A dynamic host Plan row has an invalid identity, state, role, or text.",
            status="BLOCKED",
            position=position,
        )
        seen_host_ids.add(host_row_id)
        seen_task_ids.add(task_id)
        text_sha256 = sha256_bytes(display_text.encode("utf-8"))
        normalized.append(
            {
                "host_row_number": host_row_offset + position,
                "host_row_id": host_row_id,
                "task_id": task_id,
                "display_text": display_text,
                "display_text_sha256": text_sha256,
                "canonical_requested_outcome": canonical_outcome,
                "status": host_status,
                "panel_role": panel_role,
                "phase": phase,
                "path_scope": path_scope,
                "workflow_gates": workflow_gates,
                "acceptance_checks": acceptance_checks,
                "task_class": str(raw.get("task_class") or "modify_code").strip(),
                "preserve_existing_identity": bool(
                    raw.get("preserve_existing_identity")
                ),
                "preserve_canonical_contract": bool(
                    raw.get("preserve_canonical_contract")
                ),
                "supersedes_task_id": (
                    supersedes_task_ids[0]
                    if len(supersedes_task_ids) == 1
                    else None
                ),
                "supersedes_task_ids": supersedes_task_ids,
                "state_travel_route": bool(raw.get("state_travel_route")),
            }
        )
    active_rows = [row for row in normalized if row["status"] == "in_progress"]
    current_hils = [row for row in normalized if row["panel_role"] == "HIL_GATE"]
    physical_hils = [
        row for row in normalized if row["panel_role"] == "PHYSICALLY_FINAL_HIL"
    ]
    replacement_ids = [
        str(task_id)
        for row in normalized
        for task_id in row["supersedes_task_ids"]
    ]
    physical_final_valid = not physical_hils or physical_hils == [normalized[-1]]
    require(
        len(active_rows) == 1
        and bool(current_hils)
        and physical_final_valid
        and active_rows[0]["task_id"] == exact_anchor
        and active_rows[0]["preserve_existing_identity"] is True
        and all(
            row["preserve_existing_identity"] is True
            and row["preserve_canonical_contract"] is True
            for row in [*current_hils, *physical_hils]
        )
        and len(replacement_ids) == len(set(replacement_ids)),
        "HOST_PLAN_SNAPSHOT_INVARIANTS_INVALID",
        "The host Plan must have one active row, at least one HIL, an optional physically-final HIL only at the end, and unique replacements.",
        status="BLOCKED",
        active_count=len(active_rows),
        hil_count=len(current_hils),
        physical_final_count=len(physical_hils),
    )
    body = {
        "schema": HOST_PLAN_SNAPSHOT_SCHEMA,
        "source_authority_id": exact_source,
        "canonical_authority": "PLAN_LANE",
        "host_projection_only": True,
        "bootstrap_full_projection_allowed": True,
        "bootstrap_projection_mode": "FULL_NATIVE_BOOTSTRAP_LIST_ONLY",
        "post_activation_projection_mode": "COMPACT_NATIVE_PLAN_WINDOW",
        "post_activation_projection_contract": "ONE_CONTINUITY_HEADER_PLUS_EXACTLY_NINE_PLAN_ROWS",
        "native_changes_behavior": "ORDINARY_CODEX_CHANGES_UNMODIFIED",
        "goal_projection": {
            "authority": "PLAN_LANE",
            "summary": (
                "Pursue the current canonical Plan boundary to the next configured "
                "HIL and complete only when the final effective Plan row completes; "
                "that final row may be ordinary or HIL."
            ),
            "duplicates_plan_rows": False,
        },
        "anchor_task_id": exact_anchor,
        "captured_at": captured_at or utc_now(),
        "row_count": len(normalized),
        "retained_host_row_count": host_row_offset,
        "host_authority_total": host_row_offset + len(normalized),
        "active_host_row_id": active_rows[0]["host_row_id"],
        "active_task_id": active_rows[0]["task_id"],
        "hil_mappings": [
            {
                "host_row_id": row["host_row_id"],
                "task_id": row["task_id"],
                "panel_role": row["panel_role"],
            }
            for row in [*current_hils, *physical_hils]
        ],
        "physical_final_task_id": (
            physical_hils[0]["task_id"] if physical_hils else None
        ),
        "rows": normalized,
    }
    return {
        **body,
        "snapshot_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def validate_dynamic_host_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Read back one snapshot through the same canonical validator."""

    require(
        isinstance(snapshot, dict)
        and snapshot.get("schema") == HOST_PLAN_SNAPSHOT_SCHEMA,
        "HOST_PLAN_SNAPSHOT_SCHEMA_MISMATCH",
        "The dynamic host Plan snapshot schema is invalid.",
        status="MISMATCH",
    )
    rebuilt = create_dynamic_host_snapshot(
        source_authority_id=str(snapshot.get("source_authority_id") or ""),
        anchor_task_id=str(snapshot.get("anchor_task_id") or ""),
        rows=[dict(row) for row in snapshot.get("rows") or []],
        captured_at=str(snapshot.get("captured_at") or ""),
        host_row_offset=int(snapshot.get("retained_host_row_count") or 0),
    )
    require(
        rebuilt == snapshot,
        "HOST_PLAN_SNAPSHOT_HASH_MISMATCH",
        "The dynamic host Plan snapshot does not match its canonical bytes.",
        status="MISMATCH",
    )
    return rebuilt


def _host_projection(row: dict[str, Any], snapshot_sha256: str) -> dict[str, Any]:
    return {
        "schema": "evidence-lane.host-step-row.v2",
        "host_snapshot_sha256": snapshot_sha256,
        "host_row_number": row["host_row_number"],
        "host_row_id": row["host_row_id"],
        "host_display_text": row["display_text"],
        "host_display_text_sha256": row["display_text_sha256"],
        "host_status": row["status"],
        "canonical_task_id": row["task_id"],
        "canonical_contract_separate": bool(row["preserve_canonical_contract"]),
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }


def _replacement_receipt(
    *, old_task_id: str, new_task_id: str, recorded_at: str
) -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.plan-replacement-link.v1",
        "old_task_id": old_task_id,
        "new_task_id": new_task_id,
        "recorded_at": recorded_at,
        "old_row_retained_as_history": True,
        "new_row_effective_for_execution": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _drop_receipt(
    *, task_id: str, reason_sha256: str, recorded_at: str
) -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.plan-drop-receipt.v1",
        "task_id": task_id,
        "reason_sha256": _require_sha256(reason_sha256, field="drop.reason_sha256"),
        "recorded_at": recorded_at,
        "intentionally_removed_from_executable_plan": True,
        "history_preserved": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _task_supersedes(task: dict[str, Any]) -> list[str]:
    grouped = [str(item) for item in task.get("supersedes_task_ids") or []]
    singular = str(task.get("supersedes_task_id") or "")
    if singular and singular not in grouped:
        grouped.append(singular)
    return grouped


def _supersession_link_issues(backlog: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = {str(task["task_id"]): task for task in backlog.get("tasks", [])}
    issues: list[dict[str, Any]] = []
    for task_id, task in sorted(tasks.items()):
        if task.get("status") != "SUPERSEDED":
            continue
        successor_id = str(task.get("superseded_by_task_id") or "")
        successor = tasks.get(successor_id)
        if (
            not successor_id
            or successor is None
            or task_id not in _task_supersedes(successor)
        ):
            issues.append(
                {
                    "direction": "OLD_TO_SUCCESSOR",
                    "task_id": task_id,
                    "superseded_by_task_id": successor_id or None,
                    "successor_exists": successor is not None,
                    "successor_supersedes_task_ids": (
                        _task_supersedes(successor)
                        if successor is not None
                        else None
                    ),
                }
            )
    for task_id, task in sorted(tasks.items()):
        for old_task_id in _task_supersedes(task):
            old = tasks.get(old_task_id)
            if old is None or str(old.get("superseded_by_task_id") or "") != task_id:
                issues.append(
                    {
                        "direction": "SUCCESSOR_TO_OLD",
                        "task_id": task_id,
                        "supersedes_task_id": old_task_id,
                        "old_exists": old is not None,
                        "old_superseded_by_task_id": (
                            old.get("superseded_by_task_id")
                            if old is not None
                            else None
                        ),
                    }
                )
    return issues


def _drop_receipt_issues(backlog: dict[str, Any]) -> list[str]:
    events_by_task: dict[str, list[dict[str, Any]]] = {}
    for event in backlog.get("events", []):
        events_by_task.setdefault(str(event.get("task_id") or ""), []).append(event)
    issues: list[str] = []
    for task in backlog.get("tasks", []):
        if task.get("status") != "DROPPED":
            continue
        task_id = str(task["task_id"])
        explicit = _SHA256_RE.fullmatch(
            str(task.get("drop_receipt_sha256") or "").upper()
        ) is not None
        legacy_events = [
            event
            for event in events_by_task.get(task_id, [])
            if event.get("event_type") == "DROPPED"
            and _SHA256_RE.fullmatch(
                str(dict(event.get("details") or {}).get("reason_sha256") or "").upper()
            )
            is not None
            and _SHA256_RE.fullmatch(str(event.get("event_sha256") or "").upper())
            is not None
        ]
        if not explicit and not legacy_events:
            issues.append(task_id)
    return issues


def _repair_legacy_supersession_links(
    backlog: dict[str, Any],
    *,
    actor: str,
    plan_id: str,
    recorded_at: str,
) -> list[dict[str, Any]]:
    """Amend legacy many-to-one links without changing lifecycle or order."""

    tasks = {str(task["task_id"]): task for task in backlog.get("tasks", [])}
    prior_by_task: dict[str, dict[str, Any]] = {}

    def mark(task: dict[str, Any]) -> None:
        task_id = str(task["task_id"])
        prior_by_task.setdefault(
            task_id,
            {
                "supersedes_task_id": task.get("supersedes_task_id"),
                "supersedes_task_ids": _task_supersedes(task),
                "superseded_by_task_id": task.get("superseded_by_task_id"),
            },
        )

    for old_task_id, old in sorted(tasks.items()):
        if old.get("status") != "SUPERSEDED":
            continue
        successor_id = str(old.get("superseded_by_task_id") or "")
        successor = tasks.get(successor_id)
        if not successor_id or successor is None:
            continue
        old_forward = _task_supersedes(old)
        if successor_id in old_forward:
            # A two-node cycle is not a chain.  Preserve the old bytes in the
            # amendment receipt and retain only the actual old -> successor edge.
            mark(old)
            old_forward = [item for item in old_forward if item != successor_id]
            old["supersedes_task_ids"] = old_forward
            old["supersedes_task_id"] = (
                old_forward[0] if len(old_forward) == 1 else None
            )
        successor_forward = _task_supersedes(successor)
        if old_task_id not in successor_forward:
            mark(successor)
            successor_forward.append(old_task_id)
            successor["supersedes_task_ids"] = successor_forward
            if len(successor_forward) == 1:
                successor["supersedes_task_id"] = successor_forward[0]

    receipts: list[dict[str, Any]] = []
    for task_id, prior in sorted(prior_by_task.items()):
        task = tasks[task_id]
        current = {
            "supersedes_task_id": task.get("supersedes_task_id"),
            "supersedes_task_ids": _task_supersedes(task),
            "superseded_by_task_id": task.get("superseded_by_task_id"),
        }
        body = {
            "schema": "evidence-lane.plan-supersession-link-repair.v1",
            "task_id": task_id,
            "prior": prior,
            "prior_sha256": sha256_bytes(canonical_json_bytes(prior)),
            "current": current,
            "current_sha256": sha256_bytes(canonical_json_bytes(current)),
            "lifecycle_status_unchanged": str(task["status"]),
            "recorded_at": recorded_at,
        }
        receipt = {
            **body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(body)),
        }
        task.setdefault("task_contract_amendments", []).append(receipt)
        append_delta_event(
            backlog,
            task_id=task_id,
            event_type="TASK_CONTRACT_AMENDED",
            to_status=str(task["status"]),
            actor=actor,
            event_id=f"{plan_id}__{task_id}__supersession_link_repaired",
            recorded_at=recorded_at,
            assume_initialized=True,
            details={
                "repair_receipt_sha256": receipt["receipt_sha256"],
                "lifecycle_status_unchanged": True,
                "history_preserved": True,
            },
        )
        receipts.append(receipt)
    return receipts


def build_dynamic_reconciliation_candidate(
    backlog: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    project_id: str,
    actor: str,
    plan_id: str,
    drop_contracts: list[dict[str, str]] | None = None,
    recorded_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build, but do not persist, one complete replacement of the active suffix."""

    exact_snapshot = validate_dynamic_host_snapshot(snapshot)
    exact_actor = str(actor or "").strip()
    exact_plan_id = _require_task_id(plan_id, field="plan_id")
    require(
        bool(
            backlog.get("schema") == "evidence-lane.linear-task-backlog.v1"
            and backlog.get("project_id") == project_id
            and exact_actor
        ),
        "HOST_PLAN_BACKLOG_IDENTITY_MISMATCH",
        "The host Plan candidate does not match the canonical project backlog.",
        status="MISMATCH",
    )
    candidate = copy.deepcopy(backlog)
    ensure_event_ledger(candidate)
    ordered = sorted(candidate["tasks"], key=lambda item: int(item["sequence"]))
    by_id = {str(item["task_id"]): item for item in ordered}
    require(
        len(by_id) == len(ordered)
        and exact_snapshot["anchor_task_id"] in by_id
        and not any(
            str(plan.get("plan_id")) == exact_plan_id
            for plan in candidate.get("plans", [])
        ),
        "HOST_PLAN_RECONCILIATION_ANCHOR_MISMATCH",
        "The Plan anchor, task identities, or operation ID changed.",
        status="MISMATCH",
    )
    anchor = by_id[str(exact_snapshot["anchor_task_id"])]
    require(
        anchor.get("status") == "ACTIVE",
        "HOST_PLAN_RECONCILIATION_ACTIVE_ANCHOR_REQUIRED",
        "Dynamic reconciliation must start from the sole active executable row.",
        status="MISMATCH",
    )
    active_tasks = [item for item in ordered if item.get("status") == "ACTIVE"]
    require(
        active_tasks == [anchor],
        "HOST_PLAN_RECONCILIATION_SOLE_ACTIVE_MISMATCH",
        "The canonical Plan does not have the expected sole active row.",
        status="MISMATCH",
    )
    anchor_index = ordered.index(anchor)
    prefix = ordered[:anchor_index]
    suffix = ordered[anchor_index:]
    suffix_executable = [
        item for item in suffix if item.get("status") in _EXECUTABLE_STATUSES
    ]
    desired_rows = list(exact_snapshot["rows"])
    desired_ids = [str(row["task_id"]) for row in desired_rows]
    preserved_ids = {
        str(row["task_id"])
        for row in desired_rows
        if row["preserve_existing_identity"]
    }
    replacement_by_old = {
        str(old_task_id): str(row["task_id"])
        for row in desired_rows
        for old_task_id in row["supersedes_task_ids"]
    }
    drops = list(drop_contracts or [])
    drop_by_task: dict[str, dict[str, Any]] = {}
    now = recorded_at or utc_now()
    for raw_drop in drops:
        require(
            isinstance(raw_drop, dict)
            and set(raw_drop) == {"task_id", "reason_sha256"},
            "HOST_PLAN_DROP_CONTRACT_INVALID",
            "Every dropped row requires one exact task and reason digest.",
            status="BLOCKED",
        )
        task_id = _require_task_id(raw_drop["task_id"], field="drop.task_id")
        require(
            task_id not in drop_by_task,
            "HOST_PLAN_DROP_CONTRACT_DUPLICATE",
            "A Plan row cannot be dropped twice in one reconciliation.",
            status="BLOCKED",
            task_id=task_id,
        )
        drop_by_task[task_id] = _drop_receipt(
            task_id=task_id,
            reason_sha256=raw_drop["reason_sha256"],
            recorded_at=now,
        )
    current_suffix_ids = {str(item["task_id"]) for item in suffix_executable}
    require(
        preserved_ids <= current_suffix_ids
        and set(replacement_by_old) <= current_suffix_ids
        and not preserved_ids.intersection(replacement_by_old)
        and set(drop_by_task) <= current_suffix_ids,
        "HOST_PLAN_RECONCILIATION_MEMBERSHIP_INVALID",
        "A preserved, replaced, or dropped row is outside the active suffix.",
        status="MISMATCH",
    )
    accounted_old = preserved_ids | set(replacement_by_old) | set(drop_by_task)
    require(
        accounted_old == current_suffix_ids,
        "HOST_PLAN_RECONCILIATION_OBSOLETE_EXECUTABLE_ROWS",
        "Every prior executable suffix row must be preserved, replaced, or explicitly dropped.",
        status="MISMATCH",
        unaccounted=sorted(current_suffix_ids - accounted_old),
        multiply_accounted=sorted(accounted_old - current_suffix_ids),
    )
    require(
        set(desired_ids).isdisjoint(set(by_id) - preserved_ids),
        "HOST_PLAN_RECONCILIATION_TASK_ID_CONFLICT",
        "A new host Plan task identity already exists outside its preserved anchor.",
        status="MISMATCH",
    )

    replacement_receipts: list[dict[str, Any]] = []
    new_by_id: dict[str, dict[str, Any]] = {}
    for row in desired_rows:
        task_id = str(row["task_id"])
        desired_status = _HOST_STATUS_TO_LIFECYCLE[str(row["status"])]
        if row["preserve_existing_identity"]:
            task = by_id[task_id]
            require(
                task.get("status") == desired_status
                and str(task.get("panel_role") or "STANDARD").upper()
                == str(row["panel_role"]),
                "HOST_PLAN_PRESERVED_STATUS_MISMATCH",
                "A preserved Plan identity changed lifecycle state or HIL role outside its owner.",
                status="MISMATCH",
                task_id=task_id,
            )
            prior_contract = {
                key: copy.deepcopy(task.get(key))
                for key in (
                    "requested_outcome",
                    "acceptance_checks",
                    "panel_role",
                    "plan_group",
                    "dependencies",
                )
            }
            if not row["preserve_canonical_contract"]:
                task["requested_outcome"] = row["canonical_requested_outcome"]
                task["acceptance_checks"] = list(row["acceptance_checks"])
                task["plan_group"] = "DYNAMIC_HOST_PLAN"
            task["workflow_phase"] = row["phase"]
            task["workflow_gates"] = list(row["workflow_gates"])
            task["permitted_paths"] = list(row["path_scope"])
            task["host_step_projection"] = _host_projection(
                row, str(exact_snapshot["snapshot_sha256"])
            )
            amendment_body = {
                "schema": "evidence-lane.host-plan-contract-amendment.v2",
                "task_id": task_id,
                "prior_contract": prior_contract,
                "prior_contract_sha256": sha256_bytes(
                    canonical_json_bytes(prior_contract)
                ),
                "host_display_text_sha256": row["display_text_sha256"],
                "canonical_contract_preserved": bool(
                    row["preserve_canonical_contract"]
                ),
                "recorded_at": now,
            }
            amendment = {
                **amendment_body,
                "receipt_sha256": sha256_bytes(canonical_json_bytes(amendment_body)),
            }
            task.setdefault("task_contract_amendments", []).append(amendment)
            append_delta_event(
                candidate,
                task_id=task_id,
                event_type="TASK_CONTRACT_AMENDED",
                to_status=desired_status,
                actor=exact_actor,
                event_id=f"{exact_plan_id}__{task_id}__host_projection_rebound",
                recorded_at=now,
                assume_initialized=True,
                details={
                    "host_snapshot_sha256": exact_snapshot["snapshot_sha256"],
                    "amendment_receipt_sha256": amendment["receipt_sha256"],
                    "candidate_created": False,
                    "hil_inferred": False,
                    "pointer_moved": False,
                },
            )
            new_by_id[task_id] = task
            continue
        old_task_ids = [str(item) for item in row["supersedes_task_ids"]]
        row_replacement_receipts: list[dict[str, Any]] = []
        for old_task_id in old_task_ids:
            require(
                old_task_id in by_id
                and by_id[old_task_id].get("status") in {"QUEUED", "DONE"},
                "HOST_PLAN_REPLACEMENT_TARGET_INVALID",
                "A replacement row must supersede one queued or completed prior row.",
                status="MISMATCH",
                task_id=task_id,
                supersedes_task_id=old_task_id,
            )
            replacement = _replacement_receipt(
                old_task_id=old_task_id,
                new_task_id=task_id,
                recorded_at=now,
            )
            replacement_receipts.append(replacement)
            row_replacement_receipts.append(replacement)
        task = {
            "task_id": task_id,
            "plan_id": exact_plan_id,
            "task_class": row["task_class"],
            "requested_outcome": row["canonical_requested_outcome"],
            "permitted_paths": list(row["path_scope"]),
            "permitted_tools": [],
            "acceptance_checks": list(row["acceptance_checks"]),
            "stop_condition": (
                "Stop on source, identity, dependency, receipt, HIL, or pointer drift."
            ),
            "panel_role": row["panel_role"],
            "plan_group": "DYNAMIC_HOST_PLAN",
            "commit_batch_id": "DYNAMIC_UNTIL_EXPLICIT_COMMIT_OWNER",
            "dependencies": [],
            "git_commit_stage": "NONE",
            "workflow_phase": row["phase"],
            "workflow_gates": list(row["workflow_gates"]),
            "host_step_projection": _host_projection(
                row, str(exact_snapshot["snapshot_sha256"])
            ),
            "supersedes_task_id": (
                old_task_ids[0] if len(old_task_ids) == 1 else None
            ),
            "supersedes_task_ids": old_task_ids,
            "replacement_receipt_sha256s": [
                receipt["receipt_sha256"] for receipt in row_replacement_receipts
            ],
            "status": desired_status,
            "planned_at": now,
            "history": [],
        }
        candidate["tasks"].append(task)
        append_delta_event(
            candidate,
            task_id=task_id,
            event_type="ADDED",
            to_status=desired_status,
            actor=exact_actor,
            event_id=f"{exact_plan_id}__{task_id}__added",
            recorded_at=now,
            assume_initialized=True,
            allow_legacy_initial=desired_status == "DONE",
            details={
                "host_snapshot_sha256": exact_snapshot["snapshot_sha256"],
                "supersedes_task_ids": old_task_ids,
                "replacement_receipt_sha256s": [
                    receipt["receipt_sha256"]
                    for receipt in row_replacement_receipts
                ],
            },
        )
        for old_task_id, replacement in zip(
            old_task_ids, row_replacement_receipts, strict=True
        ):
            old = by_id[old_task_id]
            append_delta_event(
                candidate,
                task_id=old_task_id,
                event_type="PLAN_DYNAMIC_RECONCILIATION_SUPERSEDED",
                to_status="SUPERSEDED",
                actor=exact_actor,
                event_id=(
                    f"{exact_plan_id}__{old_task_id}__superseded_by__{task_id}"
                ),
                recorded_at=now,
                assume_initialized=True,
                details={
                    "replacement_task_id": task_id,
                    "replacement_receipt_sha256": replacement["receipt_sha256"],
                    "history_preserved": True,
                },
            )
            old["superseded_by_task_id"] = task_id
            old["replacement_receipt_sha256"] = replacement["receipt_sha256"]
        new_by_id[task_id] = task

    drop_receipts: list[dict[str, Any]] = []
    for task_id, receipt in drop_by_task.items():
        task = by_id[task_id]
        append_delta_event(
            candidate,
            task_id=task_id,
            event_type="DROPPED",
            to_status="DROPPED",
            actor=exact_actor,
            event_id=f"{exact_plan_id}__{task_id}__dropped",
            recorded_at=now,
            assume_initialized=True,
            details={
                "drop_receipt_sha256": receipt["receipt_sha256"],
                "reason_sha256": receipt["reason_sha256"],
                "history_preserved": True,
            },
        )
        task["drop_receipt_sha256"] = receipt["receipt_sha256"]
        drop_receipts.append(receipt)

    # Historical suffix rows remain history and are placed before the fresh
    # active block; superseded old rows stay adjacent to their replacement.
    paired_old_ids = set(replacement_by_old)
    historical_suffix = [
        item
        for item in suffix
        if str(item["task_id"]) not in preserved_ids | paired_old_ids
        and str(item["task_id"]) not in drop_by_task
        and item.get("status") in _HISTORY_STATUSES
    ]
    dropped_rows = [by_id[task_id] for task_id in drop_by_task]
    desired_block: list[dict[str, Any]] = []
    previous_effective = next(
        (
            str(item["task_id"])
            for item in reversed(prefix)
            if item.get("status") in _EXECUTABLE_STATUSES
        ),
        "",
    )
    for row in desired_rows:
        task_id = str(row["task_id"])
        for old_task_id in row["supersedes_task_ids"]:
            desired_block.append(by_id[old_task_id])
        task = new_by_id[task_id]
        task["dependencies"] = [previous_effective] if previous_effective else []
        desired_block.append(task)
        previous_effective = task_id
    reordered = prefix + historical_suffix + dropped_rows + desired_block
    require(
        len(reordered) == len(candidate["tasks"])
        and len({str(item["task_id"]) for item in reordered}) == len(reordered)
        and str(reordered[-1]["task_id"]) == desired_ids[-1],
        "HOST_PLAN_RECONCILIATION_ROW_SET_MISMATCH",
        "Dynamic reconciliation lost a row or the final effective Plan boundary.",
        status="FAIL",
    )
    for sequence, task in enumerate(reordered, start=1):
        task["sequence"] = sequence
    candidate["tasks"] = reordered
    membership_sha256 = _projection_membership_sha256(desired_ids)
    candidate.setdefault("plans", []).append(
        {
            "plan_id": exact_plan_id,
            "planned_by": exact_actor,
            "planned_at": now,
            "input_sha256": exact_snapshot["snapshot_sha256"],
            "host_snapshot_sha256": exact_snapshot["snapshot_sha256"],
            "host_row_count": exact_snapshot["host_authority_total"],
            "dynamic_membership_count": len(desired_rows),
            "retained_host_row_count": exact_snapshot["retained_host_row_count"],
            "task_ids": desired_ids,
            "membership_sha256": membership_sha256,
            "membership_authority": "PLAN_MANIFEST_NOT_TASK_ID_PREFIX_OR_PLAN_ID",
            "replaced_task_ids": list(replacement_by_old),
            "dropped_task_ids": list(drop_by_task),
        }
    )
    history_linkage_repair_receipts = _repair_legacy_supersession_links(
        candidate,
        actor=exact_actor,
        plan_id=exact_plan_id,
        recorded_at=now,
    )
    ensure_event_ledger(candidate)
    active_after = [
        item for item in reordered if item.get("status") == "ACTIVE"
    ]
    effective_after = [
        item for item in reordered if item.get("status") in _EXECUTABLE_STATUSES
    ]
    effective_host_tasks = [
        item
        for item in effective_after
        if dict(item.get("host_step_projection") or {}).get("host_row_number")
        is not None
    ]
    effective_host_tasks.sort(
        key=lambda item: int(
            dict(item.get("host_step_projection") or {})["host_row_number"]
        )
    )
    effective_host_rows = [
        int(dict(item.get("host_step_projection") or {})["host_row_number"])
        for item in effective_host_tasks
    ]
    require(
        [str(item["task_id"]) for item in active_after]
        == [str(exact_snapshot["active_task_id"])]
        and [str(item["task_id"]) for item in effective_after[-len(desired_ids) :]]
        == desired_ids
        and all(
            by_id[old_id].get("status") == "SUPERSEDED"
            and by_id[old_id].get("superseded_by_task_id") == new_id
            and old_id in _task_supersedes(new_by_id[new_id])
            for old_id, new_id in replacement_by_old.items()
        )
        and effective_host_rows
        == list(range(1, int(exact_snapshot["host_authority_total"]) + 1)),
        "HOST_PLAN_RECONCILIATION_POSTCONDITION_FAILED",
        "The candidate lost its sole active row, manifest order, or reciprocal replacement links.",
        status="FAIL",
    )
    table: list[dict[str, Any]] = []
    snapshot_row_by_task = {
        str(item["task_id"]): item for item in desired_rows
    }
    row_number = int(candidate.get("goal_row_offset") or 0)
    for task in reordered:
        if task.get("status") not in _EXECUTABLE_STATUSES:
            continue
        row_number += 1
        projection = dict(task.get("host_step_projection") or {})
        if task["task_id"] not in desired_ids:
            continue
        snapshot_row = snapshot_row_by_task[str(task["task_id"])]
        table.append(
            {
                "canonical_row": row_number,
                "host_row_number": projection.get("host_row_number"),
                "host_row_id": projection.get("host_row_id"),
                "task_id": task["task_id"],
                "status": task["status"],
                "panel_role": task.get("panel_role") or "STANDARD",
                "phase": task.get("workflow_phase") or "PRESERVED_ANCHOR",
                "display_text": projection.get("host_display_text"),
                "canonical_requested_outcome": task["requested_outcome"],
                "canonical_contract_separate": bool(
                    projection.get("canonical_contract_separate")
                ),
                "supersedes_task_id": task.get("supersedes_task_id"),
                "supersedes_task_ids": list(snapshot_row["supersedes_task_ids"]),
                "historical_supersedes_task_ids": _task_supersedes(task),
                "dependencies": list(task.get("dependencies") or []),
                "workflow_gates": list(task.get("workflow_gates") or []),
                "path_scope": list(task.get("permitted_paths") or []),
            }
        )
    full_host_body = {
        "schema": "evidence-lane.host-update-plan-contract.v2",
        "canonical_authority": "PLAN_LANE",
        "projection_mode": "FULL_DYNAMIC_PRE_PLUGIN_SWITCH",
        "available_immediately_after_plan_promotion": True,
        "independent_of_plugin_install_or_restart": True,
        "row_count": len(effective_host_tasks),
        "steps": [
            {
                "step": str(
                    dict(task.get("host_step_projection") or {}).get(
                        "host_display_text"
                    )
                    or task["requested_outcome"]
                ),
                "status": str(
                    dict(task.get("host_step_projection") or {}).get("host_status")
                    or _LIFECYCLE_TO_HOST_STATUS[str(task["status"])]
                ),
            }
            for task in effective_host_tasks
        ],
        "native_changes_behavior": "ORDINARY_CODEX_CHANGES_UNMODIFIED",
        "goal_projection": exact_snapshot["goal_projection"],
        "post_install_switch": (
            "NATIVE_ONE_CONTINUITY_HEADER_PLUS_EXACTLY_NINE_PLAN_ROWS"
        ),
    }
    full_host_projection = {
        **full_host_body,
        "projection_sha256": sha256_bytes(canonical_json_bytes(full_host_body)),
    }
    supersession_issues = _supersession_link_issues(candidate)
    drop_issues = _drop_receipt_issues(candidate)
    receipt_body = {
        "schema": HOST_PLAN_DRY_RUN_SCHEMA,
        "status": (
            "DRY_RUN_PASS"
            if not supersession_issues and not drop_issues
            else "DRY_RUN_REQUIRES_HISTORY_LINKAGE_REPAIR"
        ),
        "project_id": project_id,
        "plan_id": exact_plan_id,
        "host_snapshot_sha256": exact_snapshot["snapshot_sha256"],
        "source_backlog_sha256": _canonical_backlog_sha256(backlog),
        "candidate_backlog_sha256": _canonical_backlog_sha256(candidate),
        "host_row_count": exact_snapshot["host_authority_total"],
        "dynamic_membership_count": len(desired_ids),
        "retained_host_row_count": exact_snapshot["retained_host_row_count"],
        "membership_sha256": membership_sha256,
        "membership_task_ids": desired_ids,
        "canonical_authority": "PLAN_LANE",
        "host_projection_only": True,
        "bootstrap_projection_mode": exact_snapshot["bootstrap_projection_mode"],
        "post_activation_projection_mode": exact_snapshot[
            "post_activation_projection_mode"
        ],
        "post_activation_projection_contract": (
            "ONE_CONTINUITY_HEADER_PLUS_EXACTLY_NINE_PLAN_ROWS"
        ),
        "native_changes_behavior": "ORDINARY_CODEX_CHANGES_UNMODIFIED",
        "goal_projection": exact_snapshot["goal_projection"],
        "current_full_host_projection_contract": full_host_projection,
        "replacement_count": len(replacement_receipts),
        "new_insertion_count": sum(
            not row["preserve_existing_identity"]
            and not row["supersedes_task_ids"]
            for row in desired_rows
        ),
        "replacement_receipt_sha256s": [
            item["receipt_sha256"] for item in replacement_receipts
        ],
        "drop_count": len(drop_receipts),
        "drop_receipt_sha256s": [
            item["receipt_sha256"] for item in drop_receipts
        ],
        "history_linkage_repair_count": len(history_linkage_repair_receipts),
        "history_linkage_repair_receipt_sha256s": [
            item["receipt_sha256"] for item in history_linkage_repair_receipts
        ],
        "supersession_link_issues": supersession_issues,
        "drop_receipt_issues": drop_issues,
        "state_travel_executable": any(
            bool(row["state_travel_route"]) for row in desired_rows
        ),
        "active_task_id": exact_snapshot["active_task_id"],
        "hil_mappings": exact_snapshot["hil_mappings"],
        "physical_final_task_id": exact_snapshot["physical_final_task_id"],
        "candidate_created": False,
        "hil_invoked": False,
        "pointer_moved": False,
        "recorded_at": now,
        "proposed_rows": table,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    return candidate, receipt


_TASK35_REWRITE_ROWS: tuple[dict[str, Any], ...] = (
    {
        "slug": "active-executable-correction",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Continue the sole active Task35 executable correction from exact current source, Plan, Goal, dirty-byte, candidate, pointer, and installed-runtime identities; refresh the dynamic native host projection without State Travel or inferred HIL.",
        "gates": ["SOURCE_IDENTITY", "PLAN_MEMBERSHIP", "NO_STATE_TRAVEL"],
        "paths": [],
        "anchor": "ACTIVE",
    },
    {
        "slug": "exact-version-identity",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Correct every executable package, manifest, runtime, tunnel, installation, receipt, SDK/MCP, skill, hook, ENV/UOP, and generated-surface field that claims the loaded plugin identity so it binds the exact cachebuster version while product prose may retain the truthful base release.",
        "gates": ["EXACT_CACHEBUSTER", "FULL_STAGED_SEMANTIC_FINGERPRINT"],
        "paths": ["plugins/evidence-lane-plugin"],
    },
    {
        "slug": "versioned-tunnel-lifecycle",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Implement the capability-derived hidden tunnel lifecycle. Rebind every local install to the exact plugin package, but reuse the encrypted key, runtime, prewarm, profile, and scheduled task when the MCP/schema, skill, hook, AI-toolchain, dependency-lock, transport, and prewarm compatibility fingerprint is unchanged. Rotate to a new retained tunnel runtime only when that capability fingerprint changes; keep one active host-wide project-neutral route for explicit project IDs and preserve FastMCP/native transport parity.",
        "gates": ["TUNNEL_CAPABILITY_FINGERPRINT", "EXACT_PLUGIN_REBIND", "COMPATIBLE_RUNTIME_REUSE", "CHANGED_CAPABILITY_ROTATION"],
        "paths": ["plugins/evidence-lane-plugin/scripts/windows_tunnel", "plugins/evidence-lane-plugin/tunnel"],
    },
    {
        "slug": "toolchain-prewarm",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Execute the full conditional AI-toolchain dependency audit and prewarm contract across the executable plugin, including every declared primary/fallback role; prove each condition-true dependency runs or fails visibly rather than remaining metadata-only.",
        "gates": ["TOOLCHAIN_SET_PARITY", "PREWARM_RECEIPT"],
        "paths": ["plugins/evidence-lane-plugin/toolchains", "plugins/evidence-lane-plugin/requirements.toolchain.lock"],
    },
    {
        "slug": "env-uop-orchestration",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Correct ENV selection and independent UOP governance so lane, authority, action, tool, formula, host, budget, privacy, HIL, and fallback orchestration is executable system-wide and dynamically selected per exact project and Delta row.",
        "gates": ["ENV_UOP_INDEPENDENCE", "CONDITION_TRUE_ROUTE_EXECUTED"],
        "paths": ["plugins/evidence-lane-plugin/env", "plugins/evidence-lane-plugin/uop"],
    },
    {
        "slug": "lane-authority-workflows",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Rebuild and verify all fired sector-lane and permanent authority workflows from current schema: SQLite/CAS/FTS, MMD/DOT, tool-route evidence, manifests, receipts, changed-hash reuse, accepted-history boundaries, and dynamic first-fire versus reuse behavior.",
        "gates": ["SECTOR_REGISTRY_PARITY", "AUTHORITY_REGISTRY_PARITY", "ATOMIC_REFRESH_REUSE"],
        "paths": ["plugins/evidence-lane-plugin/authorities"],
    },
    {
        "slug": "github-app-backend",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Complete the GitHub App backend for access requests, repository entitlement, governed feature delivery, exact-head checks, non-force fast-forward, and plugin update routing while keeping maintainer local-slot operations separate from the user workflow.",
        "gates": ["GITHUB_APP_AUTH", "EXACT_HEAD", "NON_FORCE_ONLY"],
        "paths": ["plugins/evidence-lane-plugin/apps", "plugins/evidence-lane-plugin/src/evidence_lane_plugin"],
    },
    {
        "slug": "dynamic-plan-authority",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Land the dynamic Plan Store/session-owned reconciliation route: manifest-counted membership, exact host display projection, canonical HIL separation, reciprocal supersession, explicit drops, lock/journal recovery, and derived row counts/order.",
        "gates": ["PLAN_STORE_LOCK", "PLAN_JOURNAL", "DYNAMIC_MEMBERSHIP"],
        "paths": ["plugins/evidence-lane-plugin/src/evidence_lane_plugin/host_plan_reconciliation.py", "plugins/evidence-lane-plugin/tests"],
    },
    {
        "slug": "current-root-refresh",
        "phase": "EXECUTABLE_FOUNDATION",
        "text": "Run the bounded current-root atomic refresh/reuse proof for Plan, Goal, Goal metrics, Learning, Canon, Memory, Universe, ChatLineage, sectors, authorities, session, candidate, pointer, and source bindings without Project Overlay refresh or HIL movement.",
        "gates": ["ROOT_HASH_READBACK", "NO_POINTER_MOVEMENT"],
        "paths": [],
    },
    {
        "slug": "local-package-restart",
        "phase": "LOCAL_INSTALL",
        "text": "Before the PV13 executable commit, validate, cachebust, package, and update only the local-testing selector through Plugin Creator. Compare the generated tunnel capability fingerprint: atomically rebind and reuse a compatible tunnel, or rotate only for a real capability change. Prompt visibly for the Runtime key only for first registration or a missing/invalid encrypted credential. Treat the sealed install receipt as the restart boundary, then stop only for the user's physical Codex close/reopen; no restart helper or programmatic process control is allowed.",
        "gates": ["PLUGIN_CREATOR", "LOCAL_SELECTOR_ONLY", "TUNNEL_COMPATIBILITY_DECISION", "FIRST_REGISTRATION_KEY_BOUNDARY", "USER_PHYSICAL_RESTART"],
        "paths": ["plugins/evidence-lane-plugin"],
    },
    {
        "slug": "post-restart-native-activation",
        "phase": "LOCAL_INSTALL",
        "text": "After restart, prove exact local package/tunnel identity through runtime doctor, locked Flash, one resume/boot, activation, and the full action/skill/hook/tool catalog; only then switch the already-available full dynamic host projection to the plugin's exact native 1+9 Plan window. Local installation is never a prerequisite for the current full-list host update.",
        "gates": ["DOCTOR_FLASH_ONE_BOOT_ACTIVATION", "POST_INSTALL_NATIVE_ONE_PLUS_NINE_SWITCH"],
        "paths": [],
    },
    {
        "slug": "installed-e2e-poc",
        "phase": "INSTALLED_PROOF",
        "text": "Run installed end-to-end PoC proofs for Git and non-Git intake, all fired lane/authority routes, full-history Git only under its explicit lane condition, refresh/reuse/rollback, multi-project and multi-version isolation, package hot reattach, Canon, Learning, and host-visible fail-closed behavior.",
        "gates": ["INSTALLED_BYTES_ONLY", "POC_PROOF_SET"],
        "paths": [],
    },
    {
        "slug": "bounded-defect-correction",
        "phase": "INSTALLED_PROOF",
        "text": "Apply one bounded source-owner correction batch for concrete installed PoC failures; rerun only affected selectors, regenerate affected consumers, and directly purge superseded executable/generated routes without another full-suite loop.",
        "gates": ["TARGETED_CLOSURE", "NO_FULL_SUITE_LOOP"],
        "paths": ["plugins/evidence-lane-plugin"],
    },
    {
        "slug": "intermediate-executable-commit",
        "phase": "INTERMEDIATE_DELIVERY",
        "text": "Freeze the executable correction tree, run the complete staged byte-plus-semantic fingerprint, and create the bounded intermediate source/fingerprint commit pair without staging GitHub documents, GitHub Pages, Vercel app, or public-adapter files.",
        "gates": ["STAGED_FINGERPRINT", "PUBLICATION_PATHS_UNCHANGED"],
        "paths": ["plugins/evidence-lane-plugin"],
    },
    {
        "slug": "intermediate-executable-ci",
        "phase": "INTERMEDIATE_DELIVERY",
        "text": "Require the intermediate exact head to pass code, package, security, CodeQL, and GitHub-App backend checks. GitHub docs/Pages and Vercel/public-preview workflows are legitimately not triggered because their maintained path scopes are unchanged; do not disable or falsely waive any required executable check.",
        "gates": ["CODE_CI", "PACKAGE_CI", "SECURITY", "CODEQL", "GITHUB_APP_CI", "PATH_SCOPED_PUBLICATION_NOT_TRIGGERED"],
        "paths": [],
    },
    {
        "slug": "goal-metric-reconciliation",
        "phase": "INTERMEDIATE_DELIVERY",
        "text": "Before PV13 HIL, reconcile this exact Goal's restart-spanning metrics without changing Goal status: retain the earlier measured multi-day segment that was lost when Plan mode was entered, add the current native Goal segment, keep unavailable token/time fields nullable with missing counts, and seal formula-derived totals linked to this Plan and Goal rather than replacing either authority.",
        "gates": ["GOAL_IDENTITY_MATCH", "RESET_AWARE_SEGMENTS", "NO_DOUBLE_COUNT", "GOAL_REMAINS_ACTIVE"],
        "paths": [],
    },
    {
        "slug": "late-github-docs",
        "phase": "LATE_PUBLICATION",
        "text": "Rewrite every maintained GitHub document page-by-page from current backend truth with human workflow story, exact source binding, distinct diagrams, and no maintainer local-install dump in the customer workflow; changing these maintained docs paths must trigger their docs/Pages checks.",
        "gates": ["GITHUB_DOCS_PATH_TRIGGER", "DOC_SOURCE_HASH_PARITY"],
        "paths": ["plugins/evidence-lane-plugin/docs"],
    },
    {
        "slug": "late-github-pages",
        "phase": "LATE_PUBLICATION",
        "text": "Rebuild GitHub Pages page-by-page as full-canvas responsive presentation from the exact maintained docs allowlist: current navigation only, no 404/stale pages, accessible tables, rendered flowcharts, no raw template or Mermaid source, and exact production Pages readback.",
        "gates": ["GITHUB_PAGES_PATH_TRIGGER", "PAGE_BY_PAGE_BROWSER_READBACK"],
        "paths": ["plugins/evidence-lane-plugin/github-pages", "plugins/evidence-lane-plugin/scripts/prepare_github_pages.py"],
    },
    {
        "slug": "late-vercel-preview",
        "phase": "LATE_PUBLICATION",
        "text": "Update and verify Vercel/public-adapter pages only in this late batch through the live page-by-page dual-Chrome workflow; these app/public-adapter path changes must trigger Vercel/public-preview checks and append the exact Plan Delta ledger evidence.",
        "gates": ["VERCEL_PUBLIC_PATH_TRIGGER", "DUAL_CHROME_READBACK", "DELTA_LEDGER_APPEND"],
        "paths": ["apps", "plugins/evidence-lane-plugin/remote_adapter"],
    },
    {
        "slug": "late-public-readback",
        "phase": "LATE_PUBLICATION",
        "text": "Reconcile GitHub documents, built GitHub Pages, Vercel preview, navigation, diagrams, installation/onboarding story, and public Delta ledger against one exact backend snapshot with no older-history or machine-dump fallback.",
        "gates": ["CROSS_SURFACE_HASH_JOIN", "NO_STALE_FALLBACK"],
        "paths": [],
    },
    {
        "slug": "final-full-regression",
        "phase": "FINAL_DELIVERY",
        "text": "Run the one planned final full deterministic regression, Plugin Creator and all Skill Creator checks, Codex Security verification, executable/package/docs/publication fixed-point audits, and complete staged-tree byte-plus-semantic fingerprint; close only bounded concrete failures.",
        "gates": ["ONE_FINAL_FULL_REGRESSION", "PLUGIN_CREATOR", "SKILL_CREATOR", "CODEX_SECURITY", "FULL_FINGERPRINT"],
        "paths": [],
    },
    {
        "slug": "final-all-checks-delivery",
        "phase": "FINAL_DELIVERY",
        "text": "Create the final bounded source/fingerprint delivery, require every configured code/package/security/CodeQL/GitHub-App/GitHub-docs/Pages/Vercel/public-preview check required by the now-changed paths to pass, and prove the exact feature head; no commit loop.",
        "gates": ["ALL_CHANGED_PATH_CHECKS_GREEN", "EXACT_FEATURE_HEAD", "NO_COMMIT_LOOP"],
        "paths": [],
    },
    {
        "slug": "main-fast-forward",
        "phase": "FINAL_DELIVERY",
        "text": "Use one authorized non-force GitHub App fast-forward of the exact green feature head to main and verify exact main commit/tree identity; no source, Plan, HIL, candidate, or pointer mutation during promotion.",
        "gates": ["GITHUB_APP", "NON_FORCE_FAST_FORWARD", "EXACT_MAIN_READBACK"],
        "paths": [],
    },
    {
        "slug": "main-marketplace-upgrade",
        "phase": "FINAL_INSTALL",
        "text": "Build and validate the exact-main package, upgrade the persistent main marketplace slot through its governed route, keep the proven local selector active, materialize the matching main-version tunnel disabled until selected, and verify two-slot package/runtime/action/skill/hook/toolchain and versioned-tunnel parity with restart survival and clean failback.",
        "gates": ["EXACT_MAIN_PACKAGE", "MAIN_SLOT_UPGRADE", "LOCAL_STAYS_ACTIVE", "TWO_SLOT_PARITY", "VERSIONED_TUNNEL_PARITY", "RESTART_SURVIVAL"],
        "paths": [],
    },
    {
        "slug": "slot-tunnel-parity",
        "phase": "FINAL_INSTALL",
        "text": "Verify local and main slot package/runtime/action/skill/hook/toolchain identities, versioned tunnel enablement/disablement and prior-version retention, restart survival, project routing, and clean failback without changing selected work or Project/PV state.",
        "gates": ["TWO_SLOT_PARITY", "VERSIONED_TUNNEL_PARITY", "RESTART_SURVIVAL"],
        "paths": [],
    },
    {
        "slug": "current-pv13-hil",
        "phase": "CURRENT_HIL",
        "text": "Present the preserved current PV13 Project and separate consolidated Learning HIL from the exact executable-plugin, tunnel, toolchain, GitHub-App, local-install, installed-PoC, bounded-correction, intermediate-commit, and mandatory CI evidence; publication, final main delivery, and PV14 remain explicitly deferred. Stop for independent human decisions and infer nothing.",
        "gates": ["EXPLICIT_PROJECT_HIL", "EXPLICIT_LEARNING_HIL"],
        "paths": [],
        "anchor": "CURRENT_HIL",
    },
    {
        "slug": "post-hil-closure",
        "phase": "POST_HIL",
        "text": "Apply only the exact current Project and Learning decisions, then verify pointer, accepted storage/Overlay only when authorized, Learning state, every lane/authority, ENV/UOP, ChatLineage, Git/source bindings, and direct-purge closure.",
        "gates": ["DECISION_TOKEN_MATCH", "POST_DECISION_READBACK"],
        "paths": [],
    },
    {
        "slug": "physical-final-hil",
        "phase": "FINAL_HIL",
        "text": "At the physically final boundary, reconcile the complete canonical Plan from actual outcomes, run the final installed readback required by its canonical contract, present exact receipts and six choices, and stop if any earlier HIL remains unresolved.",
        "gates": ["PHYSICALLY_FINAL", "ALL_PREDECESSORS_VERIFIED"],
        "paths": [],
        "anchor": "PHYSICAL_FINAL_HIL",
    },
)


def _ordered_task35_rewrite_rows() -> list[dict[str, Any]]:
    by_slug = {str(row["slug"]): dict(row) for row in _TASK35_REWRITE_ROWS}
    atomic_publication_insert = {
        "slug": "post-hil-publication-plan-insertion",
        "phase": "POST_HIL",
        "text": "After the explicit PV13 decisions and verified closure, use the normal Evidence Lane Plan DB atomic insertion route through evi-plan to create the exact page-by-page GitHub documents, GitHub Pages, Vercel, cross-surface readback, final CI/main/install, and remaining Plan through PV14. Derive the later terminal row and any later HIL from that canonical insertion. Do not invoke Codex Plan mode, duplicate Goal rows, replace native Changes, or infer another HIL.",
        "gates": ["EVI_PLAN_ATOMIC_INSERT", "NO_CODEX_PLAN_MODE", "PLAN_LANE_ONLY"],
        "paths": [],
    }
    ordered_slugs = [
        "active-executable-correction",
        "exact-version-identity",
        "versioned-tunnel-lifecycle",
        "toolchain-prewarm",
        "env-uop-orchestration",
        "lane-authority-workflows",
        "github-app-backend",
        "dynamic-plan-authority",
        "current-root-refresh",
        "local-package-restart",
        "post-restart-native-activation",
        "installed-e2e-poc",
        "bounded-defect-correction",
        "intermediate-executable-commit",
        "intermediate-executable-ci",
        "goal-metric-reconciliation",
        "current-pv13-hil",
        "post-hil-closure",
    ]
    result = [by_slug[slug] for slug in ordered_slugs]
    result.append(atomic_publication_insert)
    return result


def build_task35_downstream_dry_run(
    backlog: dict[str, Any],
    *,
    project_id: str,
    source_authority_id: str,
    actor: str,
    captured_at: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Build the reviewed no-State-Travel Task35 future without writing it."""

    ensure_event_ledger(backlog)
    ordered = sorted(backlog["tasks"], key=lambda item: int(item["sequence"]))
    effective = [
        item for item in ordered if item.get("status") in _EXECUTABLE_STATUSES
    ]
    active = [item for item in effective if item.get("status") == "ACTIVE"]
    require(
        len(active) == 1,
        "TASK35_DRY_RUN_ANCHOR_MISMATCH",
        "Task35 dry-run requires exactly one active executable row.",
        status="MISMATCH",
    )
    active_index = effective.index(active[0])
    suffix = effective[active_index:]
    prior_physical_final = [
        item
        for item in effective
        if str(item.get("panel_role") or "").upper() == "PHYSICALLY_FINAL_HIL"
    ]
    prior_terminal_shape = (
        len(prior_physical_final) == 1
        and effective[-1] == prior_physical_final[0]
    )
    refreshed_dynamic_shape = (
        not prior_physical_final
        and str(effective[-1].get("task_id") or "")
        == "T35-NEXT-POST_HIL_PUBLICATION_PLAN_INSERTION"
        and all(
            str(dict(item.get("host_step_projection") or {}).get("schema") or "")
            == "evidence-lane.host-step-row.v2"
            for item in suffix
        )
    )
    require(
        prior_terminal_shape or refreshed_dynamic_shape,
        "TASK35_DRY_RUN_TERMINAL_SHAPE_MISMATCH",
        "Task35 reconciliation requires either the original physical-final suffix or its exact current dynamic replacement.",
        status="MISMATCH",
    )
    current_hils = [
        item
        for item in suffix
        if str(item.get("panel_role") or "").upper() == "HIL_GATE"
    ]
    require(
        len(current_hils) == 1,
        "TASK35_DRY_RUN_SUFFIX_SHAPE_CHANGED",
        "The reviewed Task35 rewrite requires one preserved current HIL.",
        status="MISMATCH",
        observed_suffix_count=len(suffix),
        current_hil_count=len(current_hils),
    )
    current_hil_index = suffix.index(current_hils[0])
    pre_hil_sources = suffix[1:current_hil_index]
    post_hil_sources = suffix[current_hil_index + 1 :]
    rewrite_specs = _ordered_task35_rewrite_rows()
    current_hil_spec_index = next(
        index
        for index, row in enumerate(rewrite_specs)
        if row.get("anchor") == "CURRENT_HIL"
    )
    pre_hil_specs = rewrite_specs[1:current_hil_spec_index]
    post_hil_specs = rewrite_specs[current_hil_spec_index + 1 :]
    require(
        bool(pre_hil_specs)
        and bool(post_hil_specs)
        and bool(pre_hil_sources)
        and bool(post_hil_sources),
        "TASK35_DRY_RUN_DYNAMIC_GROUP_EMPTY",
        "The current Plan and proposed mutation must both retain bounded pre- and post-HIL work.",
        status="MISMATCH",
    )

    def distribute(
        sources: list[dict[str, Any]], slots: int
    ) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = [[] for _ in range(slots)]
        for index, source in enumerate(sources):
            groups[min(index, slots - 1)].append(source)
        return groups

    source_groups = [
        [],
        *distribute(pre_hil_sources, len(pre_hil_specs)),
        [],
        *distribute(post_hil_sources, len(post_hil_specs)),
    ]
    require(
        len(source_groups) == len(rewrite_specs),
        "TASK35_DRY_RUN_DYNAMIC_GROUP_COUNT_MISMATCH",
        "The derived Task35 source groups do not match the proposed rows.",
        status="FAIL",
    )
    preserved = {
        "ACTIVE": active[0],
        "CURRENT_HIL": current_hils[0],
    }
    rows: list[dict[str, Any]] = []
    for position, (spec, source_group) in enumerate(
        zip(rewrite_specs, source_groups, strict=True), start=1
    ):
        anchor_role = str(spec.get("anchor") or "")
        if anchor_role:
            source_task = preserved[anchor_role]
            task_id = str(source_task["task_id"])
            supersedes_task_ids: list[str] = []
            preserve_existing = True
            preserve_canonical = anchor_role == "CURRENT_HIL"
            status = "in_progress" if anchor_role == "ACTIVE" else "pending"
            panel_role = str(source_task.get("panel_role") or "STANDARD")
        else:
            task_id = "T35-NEXT-" + str(spec["slug"]).upper().replace("-", "_")
            existing_match = (
                len(source_group) == 1
                and str(source_group[0]["task_id"]) == task_id
            )
            supersedes_task_ids = (
                []
                if existing_match
                else [str(item["task_id"]) for item in source_group]
            )
            preserve_existing = existing_match
            preserve_canonical = False
            status = (
                _LIFECYCLE_TO_HOST_STATUS[str(source_group[0]["status"])]
                if existing_match
                else "pending"
            )
            panel_role = "STANDARD"
        is_hil = panel_role in {"HIL_GATE", "PHYSICALLY_FINAL_HIL"}
        display_text = str(spec["text"])
        if not is_hil:
            display_text = f"{display_text} {UNIVERSAL_DELTA_IMPLEMENTATION_PURGE_LAW}"
        acceptance_checks = [
            "NO_INFERRED_HIL",
            "NO_POINTER_MOVEMENT_WITHOUT_OWNING_HIL",
            "SUPERSEDED_HISTORY_HAS_RECIPROCAL_REPLACEMENT",
        ]
        if not is_hil:
            acceptance_checks.extend(_UNIVERSAL_DELTA_ACCEPTANCE_CHECKS)
        rows.append(
            {
                "host_row_id": f"T35-DYNAMIC-{position:03d}",
                "task_id": task_id,
                "display_text": display_text,
                "canonical_requested_outcome": (
                    str(preserved[anchor_role]["requested_outcome"])
                    if preserve_canonical
                    else display_text
                ),
                "status": status,
                "panel_role": panel_role,
                "phase": str(spec["phase"]),
                "path_scope": list(spec["paths"]),
                "workflow_gates": list(spec["gates"]),
                "acceptance_checks": acceptance_checks,
                "task_class": "modify_code",
                "preserve_existing_identity": preserve_existing,
                "preserve_canonical_contract": preserve_canonical,
                "supersedes_task_ids": supersedes_task_ids,
                "state_travel_route": False,
            }
        )
    exact_time = captured_at or utc_now()
    retained_host_rows = sorted(
        int(dict(item.get("host_step_projection") or {})["host_row_number"])
        for item in effective[:active_index]
        if dict(item.get("host_step_projection") or {}).get("host_row_number")
        is not None
    )
    require(
        not retained_host_rows
        or retained_host_rows == list(range(1, retained_host_rows[-1] + 1)),
        "TASK35_RETAINED_HOST_ROWS_NOT_CONTIGUOUS",
        "Retained effective host rows must be unique and contiguous before dynamic append.",
        status="MISMATCH",
    )
    retained_host_row_count = retained_host_rows[-1] if retained_host_rows else 0
    snapshot = create_dynamic_host_snapshot(
        source_authority_id=source_authority_id,
        anchor_task_id=str(active[0]["task_id"]),
        rows=rows,
        captured_at=exact_time,
        host_row_offset=retained_host_row_count,
    )
    plan_id = f"TASK35_DYNAMIC_{snapshot['snapshot_sha256'][:16]}"
    candidate, receipt = build_dynamic_reconciliation_candidate(
        backlog,
        snapshot=snapshot,
        project_id=project_id,
        actor=actor,
        plan_id=plan_id,
        drop_contracts=[],
        recorded_at=exact_time,
    )
    publication_rows = [
        row
        for row in receipt["proposed_rows"]
        if row["phase"] == "LATE_PUBLICATION"
    ]
    intermediate_rows = [
        row
        for row in receipt["proposed_rows"]
        if row["phase"] == "INTERMEDIATE_DELIVERY"
    ]
    public_roots = {
        "plugins/evidence-lane-plugin/docs",
        "plugins/evidence-lane-plugin/github-pages",
        "plugins/evidence-lane-plugin/remote_adapter",
        "apps",
    }
    require(
        receipt["state_travel_executable"] is False
        and not publication_rows
        and all(
            not public_roots.intersection(set(row["path_scope"]))
            for row in intermediate_rows
        )
        and receipt["proposed_rows"][-1]["task_id"]
        == "T35-NEXT-POST_HIL_PUBLICATION_PLAN_INSERTION"
        and receipt["proposed_rows"][-1]["panel_role"] == "STANDARD",
        "TASK35_DRY_RUN_PUBLICATION_BOUNDARY_INVALID",
        "The intermediate delivery or late publication boundary drifted.",
        status="FAIL",
    )
    receipt["intermediate_publication_paths_changed"] = False
    receipt["intermediate_required_checks"] = [
        "CODE_CI",
        "PACKAGE_CI",
        "SECURITY",
        "CODEQL",
        "GITHUB_APP_CI",
    ]
    receipt["intermediate_path_scoped_not_triggered"] = [
        "GITHUB_DOCS_PUBLICATION",
        "GITHUB_PAGES",
        "VERCEL_PAGES",
    ]
    receipt["deferred_publication_workflows"] = [
        "GITHUB_DOCS_PUBLICATION",
        "GITHUB_PAGES",
        "VERCEL_PAGES",
    ]
    receipt["continuation_append_requirements"] = [
        "NORMAL_EVI_PLAN_PLAN_DB_ATOMIC_INSERT",
        "NO_CODEX_PLAN_MODE",
        "PAGE_BY_PAGE_GITHUB_DOCS",
        "PAGE_BY_PAGE_GITHUB_PAGES",
        "PAGE_BY_PAGE_VERCEL",
        "LATER_FULL_FINAL_CI_AND_MAIN_DELIVERY",
    ]
    receipt["current_list_terminal_action"] = receipt["proposed_rows"][-1][
        "task_id"
    ]
    receipt["current_list_completion_rule"] = (
        "GOAL_COMPLETES_ONLY_WHEN_FINAL_EFFECTIVE_PLAN_ROW_COMPLETES;"
        "THE_FINAL_ROW_MAY_BE_ORDINARY_OR_HIL_AND_THIS_CONTINUATION_MAY_APPEND_LATER_ROWS"
    )
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return snapshot, candidate, receipt


def _validate_staged_runtime(
    runtime_path: Path,
    *,
    snapshot: dict[str, Any],
    expected_task_ids: list[str],
) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{runtime_path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        rows = connection.execute(
            """
            SELECT task_id,row_number,lifecycle_status,host_status,
                   effective_for_execution,panel_role,requested_outcome
            FROM plan_execution_row
            WHERE task_id IN (SELECT value FROM json_each(?))
            ORDER BY row_number
            """,
            (json.dumps(expected_task_ids),),
        ).fetchall()
    finally:
        connection.close()
    require(
        integrity == "ok"
        and not violations
        and len(rows) == len(expected_task_ids)
        and [str(row["task_id"]) for row in rows] == expected_task_ids
        and all(bool(row["effective_for_execution"]) for row in rows)
        and sum(str(row["lifecycle_status"]) == "ACTIVE" for row in rows) == 1
        and str(rows[-1]["panel_role"])
        == str(snapshot["rows"][-1]["panel_role"]),
        "HOST_PLAN_STAGED_RUNTIME_INVALID",
        "The staged Plan runtime failed membership, integrity, active-row, or final-row checks.",
        status="FAIL",
    )
    return {
        "integrity_check": integrity,
        "foreign_key_violation_count": len(violations),
        "membership_count": len(rows),
        "first_row_number": int(rows[0]["row_number"]),
        "last_row_number": int(rows[-1]["row_number"]),
        "snapshot_sha256": snapshot["snapshot_sha256"],
    }


def _snapshot_protected_hashes(
    project_root: Path,
    protected_paths: Iterable[str],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in protected_paths:
        exact = str(relative).replace("\\", "/").strip("/")
        path = (project_root / exact).resolve()
        try:
            path.relative_to(project_root)
        except ValueError as exc:
            raise EvidenceLaneError(
                "HOST_PLAN_PROTECTED_PATH_ESCAPE",
                "A protected reconciliation path escaped the project root.",
                status="BLOCKED",
                details={"path": exact},
            ) from exc
        require(
            path.is_file(),
            "HOST_PLAN_PROTECTED_PATH_MISSING",
            "A protected reconciliation authority is missing.",
            status="MISMATCH",
            path=exact,
        )
        result[exact] = sha256_file(path)
    return result


def _journal_path(project_root: Path, operation_id: str) -> Path:
    return (
        project_root
        / "sectors"
        / "plan"
        / "plan_normalization"
        / "dynamic_host_reconciliation"
        / f"{operation_id}.json"
    )


def _require_atomic_replace_ready(paths: Iterable[Path]) -> None:
    """Fail before staging when Windows has an incompatible live file handle."""

    if os.name != "nt":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    invalid_handle = ctypes.c_void_p(-1).value
    locked: list[str] = []
    for path in paths:
        handle = create_file(
            str(path),
            0x80000000,  # GENERIC_READ
            0,  # exclusive sharing proves rename readiness before mutation
            None,
            3,  # OPEN_EXISTING
            0x80,  # FILE_ATTRIBUTE_NORMAL
            None,
        )
        if handle == invalid_handle:
            locked.append(str(path))
            continue
        close_handle(handle)
    require(
        not locked,
        "HOST_PLAN_ATOMIC_TARGET_LOCKED",
        "A canonical Plan target has a live incompatible handle; no file was staged or promoted.",
        status="BLOCKED",
        locked_paths=locked,
    )


def _recover_transaction(
    journal_path: Path,
    *,
    project_root: Path,
) -> dict[str, Any]:
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    require(
        journal.get("schema") == HOST_PLAN_JOURNAL_SCHEMA,
        "HOST_PLAN_JOURNAL_SCHEMA_MISMATCH",
        "The host Plan transaction journal is invalid.",
        status="MISMATCH",
    )
    protected = _snapshot_protected_hashes(
        project_root, list(journal["protected_file_sha256s"])
    )
    require(
        protected == journal["protected_file_sha256s"],
        "HOST_PLAN_JOURNAL_PROTECTED_AUTHORITY_CHANGED",
        "A protected authority changed during host Plan crash recovery.",
        status="MISMATCH",
    )
    backlog_target = Path(journal["backlog_target"])
    runtime_target = Path(journal["runtime_target"])
    backlog_stage = Path(journal["backlog_stage"])
    runtime_stage = Path(journal["runtime_stage"])
    backlog_matches = (
        backlog_target.is_file()
        and sha256_file(backlog_target) == journal["backlog_sha256"]
    )
    runtime_matches = (
        runtime_target.is_file()
        and sha256_file(runtime_target) == journal["runtime_sha256"]
    )
    if not backlog_matches:
        require(
            backlog_stage.is_file()
            and sha256_file(backlog_stage) == journal["backlog_sha256"],
            "HOST_PLAN_JOURNAL_BACKLOG_STAGE_MISSING",
            "Crash recovery cannot find the exact staged Plan backlog.",
            status="FAIL",
        )
        os.replace(backlog_stage, backlog_target)
        backlog_matches = True
        atomic_write_json(journal_path, {**journal, "journal_state": "BACKLOG_PROMOTED"})
    if not runtime_matches:
        require(
            runtime_stage.is_file()
            and sha256_file(runtime_stage) == journal["runtime_sha256"],
            "HOST_PLAN_JOURNAL_RUNTIME_STAGE_MISSING",
            "Crash recovery cannot find the exact staged Plan runtime.",
            status="FAIL",
        )
        os.replace(runtime_stage, runtime_target)
        runtime_matches = True
    committed = {
        **journal,
        "journal_state": "COMMITTED",
        "recovered": True,
        "committed_at": utc_now(),
    }
    atomic_write_json(journal_path, committed)
    require(
        backlog_matches and runtime_matches,
        "HOST_PLAN_JOURNAL_RECOVERY_FAILED",
        "The host Plan transaction did not recover both canonical targets.",
        status="FAIL",
    )
    return committed


def reconcile_dynamic_host_plan_files(
    project_root: str | Path,
    *,
    snapshot: dict[str, Any],
    project_id: str,
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
    """Build or atomically promote one Store-locked dynamic Plan transaction."""

    root = Path(project_root).resolve()
    exact_snapshot = validate_dynamic_host_snapshot(snapshot)
    operation_id = f"hostplan_{exact_snapshot['snapshot_sha256'][:24].lower()}"
    journal_path = _journal_path(root, operation_id)
    if not dry_run and journal_path.is_file():
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        require(
            journal.get("snapshot_sha256") == exact_snapshot["snapshot_sha256"],
            "HOST_PLAN_JOURNAL_REPLAY_CONFLICT",
            "The transaction ID already binds another host snapshot.",
            status="MISMATCH",
        )
        if journal.get("journal_state") != "COMMITTED":
            return _recover_transaction(journal_path, project_root=root)
        require(
            Path(journal["backlog_target"]).is_file()
            and sha256_file(journal["backlog_target"])
            == journal["backlog_sha256"]
            and Path(journal["runtime_target"]).is_file()
            and sha256_file(journal["runtime_target"])
            == journal["runtime_sha256"]
            and _snapshot_protected_hashes(
                root, list(journal["protected_file_sha256s"])
            )
            == journal["protected_file_sha256s"],
            "HOST_PLAN_COMMITTED_REPLAY_DRIFT",
            "A committed Plan transaction no longer matches its targets or protected authorities.",
            status="MISMATCH",
        )
        return {**journal, "idempotent_replay": True}
    backlog_path = root / "sectors" / "plan" / "task_backlog.json"
    runtime_path = root / "sectors" / "plan" / "plan_runtime_projection.sqlite"
    require(
        backlog_path.is_file() and runtime_path.is_file(),
        "HOST_PLAN_AUTHORITY_MISSING",
        "The project root lacks canonical Plan backlog/runtime authority.",
        status="MISMATCH",
    )
    normalized_protected_paths = {
        str(path).replace("\\", "/").strip("/") for path in protected_paths
    }
    require(
        {"active_pointer.json", "ai_learning/agent-learning.sqlite"}
        <= normalized_protected_paths,
        "HOST_PLAN_REQUIRED_PROTECTED_AUTHORITIES_MISSING",
        "Dynamic Plan reconciliation must bind the current pointer and Learning authority at transaction start.",
        status="BLOCKED",
        missing=sorted(
            {"active_pointer.json", "ai_learning/agent-learning.sqlite"}
            - normalized_protected_paths
        ),
    )
    backlog = json.loads(backlog_path.read_text(encoding="utf-8"))
    source_backlog_sha256 = _canonical_backlog_sha256(backlog)
    require(
        source_backlog_sha256
        == _require_sha256(expected_backlog_sha256, field="expected_backlog_sha256"),
        "HOST_PLAN_BACKLOG_CHANGED",
        "The canonical Plan changed before dynamic reconciliation.",
        status="MISMATCH",
        observed_backlog_sha256=source_backlog_sha256,
    )
    source_runtime_sha256 = sha256_file(runtime_path)
    require(
        source_runtime_sha256
        == _require_sha256(expected_runtime_sha256, field="expected_runtime_sha256"),
        "HOST_PLAN_RUNTIME_CHANGED",
        "The canonical Plan runtime changed before dynamic reconciliation.",
        status="MISMATCH",
        observed_runtime_sha256=source_runtime_sha256,
    )
    protected = _snapshot_protected_hashes(root, protected_paths)
    if expected_protected_file_sha256s is not None:
        expected = {
            str(path).replace("\\", "/").strip("/"): _require_sha256(
                digest, field=f"protected.{path}"
            )
            for path, digest in expected_protected_file_sha256s.items()
        }
        require(
            protected == expected,
            "HOST_PLAN_PROTECTED_AUTHORITY_CHANGED",
            "A protected authority changed before dynamic reconciliation.",
            status="MISMATCH",
        )
    plan_id = f"HOST_DYNAMIC_{exact_snapshot['snapshot_sha256'][:20]}"
    candidate, dry_receipt = build_dynamic_reconciliation_candidate(
        backlog,
        snapshot=exact_snapshot,
        project_id=project_id,
        actor=actor,
        plan_id=plan_id,
        drop_contracts=drop_contracts,
        recorded_at=recorded_at,
    )
    dry_receipt["source_runtime_sha256"] = source_runtime_sha256
    dry_receipt["protected_file_sha256s_rebound_at_start"] = protected
    dry_body = {
        key: value for key, value in dry_receipt.items() if key != "receipt_sha256"
    }
    dry_receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(dry_body))
    if dry_run:
        return dry_receipt
    require(
        not dry_receipt["supersession_link_issues"]
        and not dry_receipt["drop_receipt_issues"],
        "HOST_PLAN_HISTORY_LINKAGE_REPAIR_REQUIRED",
        "The canonical history requires bounded reciprocal supersession or drop-receipt repair before promotion.",
        status="MISMATCH",
        supersession_link_issues=dry_receipt["supersession_link_issues"],
        drop_receipt_issues=dry_receipt["drop_receipt_issues"],
    )
    _require_atomic_replace_ready([backlog_path, runtime_path])
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    backlog_stage = journal_path.with_suffix(".backlog.stage.json")
    runtime_stage = journal_path.with_suffix(".runtime.stage.sqlite")
    atomic_write_bytes(backlog_stage, canonical_json_bytes(candidate))
    write_plan_runtime_projection(runtime_stage, candidate)
    stage_readback = _validate_staged_runtime(
        runtime_stage,
        snapshot=exact_snapshot,
        expected_task_ids=list(dry_receipt["membership_task_ids"]),
    )
    require(
        _snapshot_protected_hashes(root, protected) == protected,
        "HOST_PLAN_PROTECTED_AUTHORITY_CHANGED_DURING_STAGE",
        "A protected authority changed while staging the Plan transaction.",
        status="MISMATCH",
    )
    journal = {
        "schema": HOST_PLAN_JOURNAL_SCHEMA,
        "operation_id": operation_id,
        "journal_state": "PREPARED",
        "project_id": project_id,
        "snapshot_sha256": exact_snapshot["snapshot_sha256"],
        "source_backlog_sha256": source_backlog_sha256,
        "source_runtime_sha256": source_runtime_sha256,
        "backlog_target": str(backlog_path),
        "runtime_target": str(runtime_path),
        "backlog_stage": str(backlog_stage),
        "runtime_stage": str(runtime_stage),
        "backlog_sha256": sha256_file(backlog_stage),
        "runtime_sha256": sha256_file(runtime_stage),
        "protected_file_sha256s": protected,
        "stage_readback": stage_readback,
        "dry_run_receipt": dry_receipt,
        "candidate_created": False,
        "hil_invoked": False,
        "pointer_moved": False,
        "prepared_at": utc_now(),
    }
    atomic_write_json(journal_path, journal)
    if _fault_after_phase == "PREPARED":
        raise RuntimeError("INJECTED_HOST_PLAN_FAILURE_AFTER_PREPARED")
    os.replace(backlog_stage, backlog_path)
    atomic_write_json(journal_path, {**journal, "journal_state": "BACKLOG_PROMOTED"})
    if _fault_after_phase == "BACKLOG_PROMOTED":
        raise RuntimeError("INJECTED_HOST_PLAN_FAILURE_AFTER_BACKLOG_PROMOTED")
    os.replace(runtime_stage, runtime_path)
    require(
        _snapshot_protected_hashes(root, protected) == protected,
        "HOST_PLAN_PROTECTED_AUTHORITY_CHANGED_DURING_PROMOTION",
        "A protected authority changed during Plan promotion.",
        status="MISMATCH",
    )
    committed = {
        **journal,
        "journal_state": "COMMITTED",
        "recovered": False,
        "committed_at": utc_now(),
    }
    atomic_write_json(journal_path, committed)
    return committed


__all__ = [
    "HOST_PLAN_DRY_RUN_SCHEMA",
    "HOST_PLAN_JOURNAL_SCHEMA",
    "HOST_PLAN_RECONCILIATION_SCHEMA",
    "HOST_PLAN_SNAPSHOT_SCHEMA",
    "build_dynamic_reconciliation_candidate",
    "build_task35_downstream_dry_run",
    "create_dynamic_host_snapshot",
    "reconcile_dynamic_host_plan_files",
    "validate_dynamic_host_snapshot",
]
