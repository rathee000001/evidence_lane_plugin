"""Exact, replay-safe Codex host Plan artifact rehydration receipts.

The native Plan Lane remains the only row/status authority.  This module turns
its current executable projection into the exact payload a host ``update_plan``
action must receive, but it never claims that the host rendered or accepted the
artifact.  Visibility and explicit host Plan acceptance are separate observed
facts; Sources/icon presence, a backlog readback, or an empty host receipt are
not substitutes for either fact.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .store import ProjectStore
from .timeutil import utc_now

_TRIGGERS = {
    "TASK_CLASSIFICATION_TRANSITION",
    "SESSION_START_COLD",
    "SESSION_START_WARM",
    "HOT_REATTACH",
    "PRECOMPACT",
    "POSTCOMPACT",
    "STATE_TRAVEL_DESTINATION_ENTRY",
    "GOAL_ACTIVE_TURN",
    "NO_GOAL_TURN",
    "PANEL_LOSS",
    "STALE_ARTIFACT",
    "EXPLICIT_HOST_OBSERVATION",
}
_OBSERVATION_STATES = {
    "UNCONFIRMED",
    "MISSING",
    "STALE",
    "VISIBLE_UNACCEPTED",
    "VISIBLE_ACCEPTED",
}
_HOST_CAPABILITIES = {"SUPPORTED", "HOST_CAPABILITY_UNAVAILABLE"}
_VISIBLE_STATES = {"VISIBLE_UNACCEPTED", "VISIBLE_ACCEPTED"}


def _session_record(
    store: ProjectStore,
    *,
    project_id: str,
    evidence_session_id: str,
    host_task_id: str,
) -> dict[str, Any]:
    require(
        evidence_session_id.startswith("session_")
        and evidence_session_id.replace("_", "").isalnum(),
        "HOST_PLAN_SESSION_ID_INVALID",
        "Host Plan rehydration requires one exact Evidence Lane session ID.",
        status="BLOCKED",
    )
    project_root = store.project_root(project_id)
    session_path = (project_root / "sessions" / f"{evidence_session_id}.json").resolve()
    session_path.relative_to(project_root)
    require(
        session_path.is_file(),
        "HOST_PLAN_SESSION_REQUIRED",
        "Host Plan rehydration requires the exact governed session.",
        status="BLOCKED",
        project_id=project_id,
        evidence_session_id=evidence_session_id,
    )
    session = cast(dict[str, Any], json.loads(session_path.read_text(encoding="utf-8")))
    require(
        session.get("project_id") == project_id
        and session.get("session_id") == evidence_session_id,
        "HOST_PLAN_PROJECT_SESSION_BINDING_MISMATCH",
        "The governed session does not belong to the requested project.",
        status="MISMATCH",
    )
    current_host_task_id = str(
        cast(dict[str, Any], session.get("metadata") or {}).get(
            "current_host_session_id"
        )
        or ""
    ).strip()
    require(
        bool(host_task_id)
        and bool(current_host_task_id)
        and host_task_id == current_host_task_id,
        "HOST_PLAN_EXACT_TASK_BINDING_REQUIRED",
        "Host Plan rehydration requires the exact currently bound Codex task ID.",
        status="MISMATCH",
    )
    return session


def _exact_projection(
    store: ProjectStore,
    *,
    project_id: str,
) -> dict[str, Any]:
    backlog = store.backlog_status(project_id)
    goal = cast(dict[str, Any], backlog.get("goal_projection") or {})
    rows = [
        dict(row)
        for row in goal.get("rows") or []
        if isinstance(row, dict)
    ]
    require(
        goal.get("canonical_authority") == "PLAN_LANE" and bool(rows),
        "HOST_PLAN_CANONICAL_AUTHORITY_REQUIRED",
        "A non-empty canonical Plan Lane projection is required.",
        status="BLOCKED",
    )
    row_start = int(goal.get("row_start") or 0)
    row_end = int(goal.get("row_end") or 0)
    require(
        len(rows) == int(goal.get("task_count") or -1)
        and [int(row.get("number") or 0) for row in rows]
        == list(range(row_start, row_end + 1)),
        "HOST_PLAN_ROWS_NOT_CONTIGUOUS",
        "The current executable Plan rows are not complete and contiguous.",
        status="MISMATCH",
    )
    active = [row for row in rows if row.get("status") == "in_progress"]
    require(
        len(active) == 1 and active[0].get("lifecycle_status") == "ACTIVE",
        "HOST_PLAN_SOLE_ACTIVE_ROW_REQUIRED",
        "The host Plan requires exactly one native ACTIVE row.",
        status="MISMATCH",
        active_count=len(active),
    )
    physical_final_rows = [
        row for row in rows if row.get("panel_role") == "PHYSICALLY_FINAL_HIL"
    ]
    require(
        len(physical_final_rows) == 1
        and physical_final_rows[0].get("task_id") == rows[-1].get("task_id")
        and physical_final_rows[0].get("number") == row_end,
        "HOST_PLAN_PHYSICALLY_FINAL_HIL_INVALID",
        "Exactly one PHYSICALLY_FINAL_HIL row must be physically last.",
        status="MISMATCH",
    )
    items = []
    for row in rows:
        label = str(row.get("visible_label") or "")
        require(
            label.startswith(f"Row {row['number']} / {row['task_id']} ")
            and str(row.get("step") or "") in label
            and "steer_deltas" not in label,
            "HOST_PLAN_VISIBLE_LABEL_INVALID",
            "Every host Plan item must use the exact metadata-rich visible label.",
            status="MISMATCH",
            row=row.get("number"),
            task_id=row.get("task_id"),
        )
        items.append({"step": label, "status": str(row["status"])})
    projection_body = {
        "schema": "evidence-lane.host-plan-exact-projection.v1",
        "project_id": project_id,
        "canonical_authority": "PLAN_LANE",
        "canonical_plan_sha256": goal.get("canonical_plan_sha256"),
        "executable_projection_sha256": goal.get("projection_sha256"),
        "visible_label_contract": goal.get("visible_label_contract"),
        "visible_label_metadata_schema": goal.get(
            "visible_label_metadata_schema"
        ),
        "row_start": row_start,
        "row_end": row_end,
        "item_count": len(items),
        "sole_active_row": active[0]["number"],
        "sole_active_task_id": active[0]["task_id"],
        "physically_final_hil_row": rows[-1]["number"],
        "physically_final_hil_task_id": rows[-1]["task_id"],
        "items": items,
    }
    return {
        **projection_body,
        "projection_sha256": sha256_bytes(canonical_json_bytes(projection_body)),
    }


def _normalized_observation(
    raw: dict[str, Any] | None,
    *,
    project_id: str,
    host_task_id: str,
    projection: dict[str, Any],
) -> dict[str, Any]:
    observation = dict(raw or {})
    state = str(observation.get("state") or "UNCONFIRMED").strip().upper()
    require(
        state in _OBSERVATION_STATES,
        "HOST_PLAN_OBSERVATION_STATE_INVALID",
        "The host Plan artifact observation state is unsupported.",
        status="BLOCKED",
        state=state,
    )
    observed_project_id = str(observation.get("project_id") or project_id)
    observed_host_task_id = str(observation.get("host_task_id") or host_task_id)
    require(
        observed_project_id == project_id and observed_host_task_id == host_task_id,
        "HOST_PLAN_OBSERVATION_BINDING_MISMATCH",
        "A host Plan observation cannot cross project or task boundaries.",
        status="MISMATCH",
    )
    update_receipt = observation.get("host_update_plan_receipt")
    update_receipt_nonempty = isinstance(update_receipt, dict) and bool(update_receipt)
    visible = state in _VISIBLE_STATES
    if visible:
        require(
            observation.get("surface") == "CODEX_RIGHT_SIDE_PLAN"
            and bool(str(observation.get("artifact_id") or "").strip())
            and observation.get("projection_sha256")
            == projection["projection_sha256"]
            and observation.get("item_count") == projection["item_count"],
            "HOST_PLAN_VISIBLE_ARTIFACT_PROOF_INVALID",
            "Visible Plan proof must bind the right-side Plan artifact to the exact projection.",
            status="MISMATCH",
        )
    accepted = state == "VISIBLE_ACCEPTED"
    if accepted:
        require(
            bool(str(observation.get("explicit_acceptance_event_id") or "").strip())
            and observation.get("acceptance_control") == "ACCEPT",
            "HOST_PLAN_EXPLICIT_ACCEPTANCE_PROOF_REQUIRED",
            "Host Plan acceptance requires one explicit Accept-control event.",
            status="MISMATCH",
        )
    return {
        "state": state,
        "project_id": project_id,
        "host_task_id": host_task_id,
        "observation_event_id": (
            str(observation.get("observation_event_id") or "").strip() or None
        ),
        "surface": observation.get("surface") if visible else None,
        "artifact_id": observation.get("artifact_id") if visible else None,
        "projection_sha256": (
            observation.get("projection_sha256") if visible else None
        ),
        "item_count": observation.get("item_count") if visible else None,
        "explicit_acceptance_event_id": (
            observation.get("explicit_acceptance_event_id") if accepted else None
        ),
        "acceptance_control": (
            observation.get("acceptance_control") if accepted else None
        ),
        "host_update_plan_receipt_nonempty": update_receipt_nonempty,
        "empty_update_plan_receipt_is_visibility_proof": False,
        "sources_presence_is_visibility_proof": False,
        "icon_presence_is_visibility_proof": False,
        "native_backlog_readback_is_visibility_proof": False,
        "artifact_visibility_proven": visible,
        "explicit_host_plan_acceptance_proven": accepted,
    }


def prepare_host_plan_rehydration(
    store_root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    host_task_id: str,
    trigger: str,
    trigger_event_id: str,
    host_capability: str = "SUPPORTED",
    observed_artifact: dict[str, Any] | None = None,
    host_goal_active: bool | None = None,
) -> dict[str, Any]:
    """Seal one exact host Plan rehydration request or verified no-op.

    The returned projection is suitable for the host ``update_plan`` action.
    The function itself never invokes that action and never fabricates host UI
    visibility or acceptance.
    """

    exact_trigger = str(trigger or "").strip().upper()
    exact_event_id = str(trigger_event_id or "").strip()
    exact_capability = str(host_capability or "").strip().upper()
    require(
        exact_trigger in _TRIGGERS and bool(exact_event_id),
        "HOST_PLAN_REHYDRATION_TRIGGER_INVALID",
        "Host Plan rehydration requires one supported trigger and event identity.",
        status="BLOCKED",
    )
    require(
        exact_capability in _HOST_CAPABILITIES,
        "HOST_PLAN_CAPABILITY_STATUS_INVALID",
        "The host Plan capability status is unsupported.",
        status="BLOCKED",
    )
    store = ProjectStore(store_root)
    session = _session_record(
        store,
        project_id=project_id,
        evidence_session_id=evidence_session_id,
        host_task_id=host_task_id,
    )
    pointer_before = store.pointer(project_id).as_dict()
    projection = _exact_projection(store, project_id=project_id)
    observation = _normalized_observation(
        observed_artifact,
        project_id=project_id,
        host_task_id=host_task_id,
        projection=projection,
    )
    visible_current = bool(observation["artifact_visibility_proven"])
    capability_available = exact_capability == "SUPPORTED"
    if not capability_available:
        status = "BLOCKED"
        action = "FAIL_CLOSED_HOST_CAPABILITY_UNAVAILABLE"
    elif visible_current:
        status = "PASS"
        action = "NO_UPDATE_PLAN_CURRENT_ARTIFACT_VISIBLE"
    else:
        status = "PASS"
        action = "CALL_HOST_UPDATE_PLAN_EXACTLY_ONCE_FOR_THIS_TRIGGER"
    metadata = cast(dict[str, Any], session.get("metadata") or {})
    core = {
        "schema": "evidence-lane.host-plan-rehydration-receipt.v1",
        "status": status,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "host_task_id": host_task_id,
        "host_task_id_sha256": sha256_bytes(host_task_id.encode("utf-8")),
        "active_runtime_task_id": cast(dict[str, Any], session.get("task") or {}).get(
            "task_id"
        ),
        "active_plan_task_id": metadata.get("active_backlog_task_id"),
        "trigger": exact_trigger,
        "trigger_event_id": exact_event_id,
        "host_goal_active": host_goal_active,
        "host_goal_presence_changes_projection": False,
        "host_capability": exact_capability,
        "host_capability_failure_behavior": "FAIL_CLOSED",
        "plan_authority": "PLAN_LANE",
        "projection": projection,
        "observation": observation,
        "action": action,
        "host_plan_tool": "update_plan",
        "native_runtime_invoked_host_update_plan": False,
        "host_action_receipt_required": action.startswith("CALL_HOST_UPDATE_PLAN"),
        "host_artifact_visibility_status": (
            "CONFIRMED_VISIBLE" if visible_current else "UNCONFIRMED"
        ),
        "host_plan_acceptance_status": (
            "EXPLICITLY_ACCEPTED"
            if observation["explicit_host_plan_acceptance_proven"]
            else "PENDING_EXPLICIT_HOST_ACCEPTANCE"
        ),
        "host_plan_acceptance_is_evidence_lane_hil": False,
        "candidate_created": False,
        "pending_hil_mutated": False,
        "pointer_moved": False,
        "source_authority_mutated": False,
        "git_mutated": False,
        "plan_lane_mutated": False,
        "sealed_at": utc_now(),
    }
    receipt = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    project_root = store.project_root(project_id)
    request_identity = {
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "host_task_id": host_task_id,
        "trigger": exact_trigger,
        "trigger_event_id": exact_event_id,
        "projection_sha256": projection["projection_sha256"],
        "observation": observation,
        "host_capability": exact_capability,
        "host_goal_active": host_goal_active,
    }
    request_sha256 = sha256_bytes(canonical_json_bytes(request_identity))
    receipt_path = (
        project_root
        / "receipts"
        / "host-plan-rehydration"
        / sha256_bytes(host_task_id.encode("utf-8"))[:24].lower()
        / f"{request_sha256.lower()}.json"
    )
    if receipt_path.is_file():
        existing = cast(
            dict[str, Any], json.loads(receipt_path.read_text(encoding="utf-8"))
        )
        claimed = str(existing.get("receipt_sha256") or "")
        require(
            claimed
            == sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in existing.items()
                        if key != "receipt_sha256"
                    }
                )
            )
            and existing.get("project_id") == project_id
            and cast(dict[str, Any], existing.get("projection") or {}).get(
                "projection_sha256"
            )
            == projection["projection_sha256"],
            "HOST_PLAN_REHYDRATION_RECEIPT_CONFLICT",
            "The existing host Plan rehydration receipt failed identity or SHA-256 verification.",
            status="MISMATCH",
        )
        return {
            "state": "REHYDRATION_RECEIPT_IDEMPOTENT_REUSE",
            "request_sha256": request_sha256,
            "receipt_path": str(receipt_path),
            "receipt": existing,
        }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(receipt_path, receipt)
    pointer_after = store.pointer(project_id).as_dict()
    require(
        pointer_before == pointer_after,
        "HOST_PLAN_REHYDRATION_POINTER_CHANGED",
        "Preparing a host Plan projection must not move the accepted pointer.",
        status="MISMATCH",
    )
    return {
        "state": "REHYDRATION_RECEIPT_SEALED",
        "request_sha256": request_sha256,
        "receipt_path": str(receipt_path),
        "receipt": receipt,
    }


def validate_host_plan_rehydration_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate the self-hash and non-promoting boundary of one receipt."""

    exact = dict(receipt)
    claimed = str(exact.pop("receipt_sha256", ""))
    require(
        receipt.get("schema") == "evidence-lane.host-plan-rehydration-receipt.v1"
        and claimed == sha256_bytes(canonical_json_bytes(exact))
        and receipt.get("candidate_created") is False
        and receipt.get("pending_hil_mutated") is False
        and receipt.get("pointer_moved") is False
        and receipt.get("plan_lane_mutated") is False,
        "HOST_PLAN_REHYDRATION_RECEIPT_INVALID",
        "The host Plan rehydration receipt failed integrity or boundary validation.",
        status="MISMATCH",
    )
    return receipt
