"""Pointer-neutral Canon observation and host-exit continuity packets.

These packets bridge already-committed visible ChatLineage events into later
governed consumers.  They are deliberately not Project Truth PVs, Canon input
acceptance, Agent Learning acceptance, or host-entry consumption receipts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

from .constants import LINEAGE_SCHEMA, POINTER_SCHEMA
from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .project_authority import resolved_chat_lineage_root
from .project_root_binding import validate_project_root_binding
from .redaction import contains_secret

OBSERVED_EXPERIENCE_SCHEMA = "evidence-lane.canon-observed-experience-packet.v1"
HOST_EXIT_CONTINUITY_SCHEMA = "evidence-lane.host-exit-continuity-packet.v1"

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_OBSERVABLE_EVENT_TYPES = {"task.steer", "turn.control_commit"}
_HEADLESS_PROFILES = {"HEADLESS_API", "DIRECT_CLI_API"}


def _sha256(value: Any, *, field: str) -> str:
    exact = str(value or "").strip().upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "CANON_RUNTIME_SHA256_INVALID",
        "A Canon runtime-continuity field is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _load_json(path: Path) -> dict[str, Any]:
    require(
        path.is_file(),
        "CANON_RUNTIME_AUTHORITY_FILE_REQUIRED",
        "A required project-scoped authority file is missing.",
        status="MISMATCH",
        path=str(path),
    )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        require(
            False,
            "CANON_RUNTIME_AUTHORITY_JSON_INVALID",
            "A required project-scoped authority file is unreadable or invalid JSON.",
            status="MISMATCH",
            path=str(path),
            error_type=type(exc).__name__,
        )
        raise AssertionError("unreachable") from exc
    require(
        isinstance(value, dict),
        "CANON_RUNTIME_AUTHORITY_JSON_INVALID",
        "A required project-scoped authority file is not one JSON object.",
        status="MISMATCH",
        path=str(path),
    )
    return cast(dict[str, Any], value)


def _pointer_snapshot(
    project_root: str | Path,
    *,
    project_id: str,
    expected_accepted_pv: str,
    expected_pointer_generation: int,
) -> dict[str, Any]:
    root = validate_project_root_binding(
        project_root,
        project_id=project_id,
        error_code="CANON_RUNTIME_CROSS_PROJECT_ROUTE_DENIED",
    )
    project = _load_json(root / "project.json")
    pointer = _load_json(root / "active_pointer.json")
    require(
        project.get("project_id") == project_id
        and pointer.get("schema") == POINTER_SCHEMA
        and pointer.get("project_id") == project_id,
        "CANON_RUNTIME_CROSS_PROJECT_ROUTE_DENIED",
        "Project configuration and accepted pointer do not share one project identity.",
        status="BLOCKED",
        project_id=project_id,
    )
    accepted_pv = str(pointer.get("accepted_pv") or "")
    generation = int(pointer.get("generation") or 0)
    require(
        accepted_pv == expected_accepted_pv
        and generation == expected_pointer_generation,
        "CANON_RUNTIME_POINTER_STALE",
        "The accepted Project Truth pointer changed before runtime packet sealing.",
        status="STALE",
        expected_accepted_pv=expected_accepted_pv,
        actual_accepted_pv=accepted_pv,
        expected_pointer_generation=expected_pointer_generation,
        actual_pointer_generation=generation,
    )
    manifest_sha256 = _sha256(
        pointer.get("accepted_manifest_sha256"),
        field="accepted_manifest_sha256",
    )
    return {
        "project_id": project_id,
        "accepted_pv": accepted_pv,
        "accepted_manifest_sha256": manifest_sha256,
        "pointer_generation": generation,
        "prior_generation": pointer.get("prior_generation"),
    }


def _validate_lineage_event(
    event: dict[str, Any],
    *,
    allowed_event_types: set[str],
    evidence_session_id: str,
    runtime_task_id: str,
) -> dict[str, Any]:
    require(
        event.get("schema") == LINEAGE_SCHEMA,
        "CANON_RUNTIME_LINEAGE_SCHEMA_INVALID",
        "Runtime packets require one native ChatLineage event.",
        status="MISMATCH",
    )
    event_type = str(event.get("event_type") or "")
    require(
        event_type in allowed_event_types,
        "CANON_RUNTIME_EVENT_NOT_COMMITTED_VISIBLE_EVIDENCE",
        "Only an allowlisted visible committed event can produce a runtime packet.",
        status="BLOCKED",
        event_type=event_type,
        allowed_event_types=sorted(allowed_event_types),
    )
    require(
        event.get("session_id") == evidence_session_id
        and event.get("task_id") == runtime_task_id,
        "CANON_RUNTIME_EVENT_BINDING_MISMATCH",
        "The visible event does not match the exact governed session and runtime task.",
        status="MISMATCH",
    )
    claimed_event_sha256 = _sha256(event.get("event_sha256"), field="event_sha256")
    actual_event_sha256 = sha256_bytes(
        canonical_json_bytes(
            {key: value for key, value in event.items() if key != "event_sha256"}
        )
    )
    require(
        claimed_event_sha256 == actual_event_sha256,
        "CANON_RUNTIME_LINEAGE_EVENT_HASH_MISMATCH",
        "The visible ChatLineage event failed its self-hash verification.",
        status="MISMATCH",
    )
    visible_payload = event.get("visible_payload")
    require(
        isinstance(visible_payload, dict)
        and event.get("private_reasoning_stored") is False
        and not contains_secret(visible_payload),
        "CANON_RUNTIME_VISIBLE_EVENT_PRIVACY_INVALID",
        "Runtime packets require a secret-safe visible payload with no private reasoning.",
        status="BLOCKED",
    )
    visible_payload = cast(dict[str, Any], visible_payload)
    require(
        _sha256(
            event.get("visible_payload_sha256"),
            field="visible_payload_sha256",
        )
        == sha256_bytes(canonical_json_bytes(visible_payload)),
        "CANON_RUNTIME_VISIBLE_PAYLOAD_HASH_MISMATCH",
        "The visible payload does not match its ChatLineage hash.",
        status="MISMATCH",
    )
    return event


def _active_plan_binding(
    value: dict[str, Any], *, project_id: str, plan_task_id: str
) -> dict[str, Any]:
    row = int(value.get("position") or value.get("number") or 0)
    exact_task_id = str(value.get("task_id") or "")
    lifecycle_status = str(value.get("lifecycle_status") or "").upper()
    status = str(value.get("status") or "").lower()
    require(
        row > 0
        and exact_task_id == plan_task_id
        and lifecycle_status == "ACTIVE"
        and status == "in_progress",
        "CANON_RUNTIME_ACTIVE_PLAN_BINDING_INVALID",
        "Runtime packets require the sole current canonical active Plan row.",
        status="MISMATCH",
        project_id=project_id,
        plan_task_id=plan_task_id,
    )
    result = {
        "row": row,
        "task_id": exact_task_id,
        "status": "in_progress",
        "lifecycle_status": "ACTIVE",
    }
    for key in (
        "canonical_plan_sha256",
        "goal_projection_sha256",
        "event_head_sha256",
    ):
        if value.get(key):
            result[key] = _sha256(value.get(key), field=f"active_plan.{key}")
    return result


def _plan_steer_classification(
    event: dict[str, Any],
    *,
    input_kind: str | None,
    active_plan: dict[str, Any],
) -> dict[str, Any]:
    event_type = str(event["event_type"])
    payload = cast(dict[str, Any], event["visible_payload"])
    before = payload.get("before_plan")
    after = payload.get("after_plan")
    linked_delta_ids = payload.get("linked_delta_ids")
    plan_change_proven = False
    before_sha256: str | None = None
    after_sha256: str | None = None
    exact_delta_ids: list[str] = []
    if (
        event_type == "task.steer"
        and isinstance(before, dict)
        and isinstance(after, dict)
        and isinstance(linked_delta_ids, list)
        and linked_delta_ids
        and all(isinstance(item, str) and item.strip() for item in linked_delta_ids)
    ):
        before_candidate = str(before.get("canonical_plan_sha256") or "").upper()
        after_candidate = str(after.get("canonical_plan_sha256") or "").upper()
        if (
            _SHA256_RE.fullmatch(before_candidate)
            and _SHA256_RE.fullmatch(after_candidate)
            and before_candidate != after_candidate
            and after_candidate == active_plan.get("canonical_plan_sha256")
            and int(after.get("active_row") or active_plan["row"]) == active_plan["row"]
            and str(after.get("active_task_id") or active_plan["task_id"])
            == active_plan["task_id"]
        ):
            plan_change_proven = True
            before_sha256 = before_candidate
            after_sha256 = after_candidate
            exact_delta_ids = sorted({str(item).strip() for item in linked_delta_ids})
    if plan_change_proven:
        classification = "PLAN_CHANGING_STEER"
        future_learning_eligibility = "CANDIDATE_EVIDENCE_ONLY_NOT_ACCEPTED"
    elif event_type == "task.steer":
        classification = "ORDINARY_OR_MINOR_STEER"
        future_learning_eligibility = "EXCLUDED_NO_PLAN_CHANGE_PROOF"
    elif str(input_kind or "").strip().lower() == "steer":
        classification = "VISIBLE_STEER_COMMIT_NO_PLAN_CHANGE_PROOF"
        future_learning_eligibility = "EXCLUDED_NO_PLAN_CHANGE_PROOF"
    elif str(input_kind or "").strip().lower() == "goal":
        classification = "VISIBLE_GOAL_COMMIT"
        future_learning_eligibility = "NOT_CLASSIFIED_AS_PLAN_STEER"
    else:
        classification = "ORDINARY_VISIBLE_REQUEST_COMMIT"
        future_learning_eligibility = "NOT_CLASSIFIED_AS_PLAN_STEER"
    return {
        "classification": classification,
        "plan_change_proven": plan_change_proven,
        "before_canonical_plan_sha256": before_sha256,
        "after_canonical_plan_sha256": after_sha256,
        "linked_delta_ids": exact_delta_ids,
        "linked_delta_ids_sha256": sha256_bytes(canonical_json_bytes(exact_delta_ids)),
        "future_learning_candidate_eligibility": future_learning_eligibility,
        "learning_accepted": False,
    }


def _write_immutable_packet(
    path: Path,
    packet: dict[str, Any],
    *,
    hash_field: str,
    conflict_code: str,
) -> str:
    if path.exists():
        existing = _load_json(path)
        require(
            existing == packet,
            conflict_code,
            "An immutable runtime packet locator already contains different bytes.",
            status="BLOCKED",
            path=str(path),
        )
        claimed = _sha256(existing.get(hash_field), field=hash_field)
        actual = sha256_bytes(
            canonical_json_bytes(
                {key: value for key, value in existing.items() if key != hash_field}
            )
        )
        require(
            claimed == actual,
            conflict_code,
            "An existing immutable runtime packet failed hash verification.",
            status="MISMATCH",
            path=str(path),
        )
        return "SEALED_IDEMPOTENT_REUSE"
    atomic_write_json(path, packet)
    return "SEALED"


def validate_observed_experience_packet(
    value: dict[str, Any], *, expected_project_id: str | None = None
) -> dict[str, Any]:
    require(
        value.get("schema") == OBSERVED_EXPERIENCE_SCHEMA,
        "CANON_OBSERVED_EXPERIENCE_SCHEMA_INVALID",
        "The observed-experience packet schema is unsupported.",
        status="MISMATCH",
    )
    if expected_project_id is not None:
        require(
            value.get("project_id") == expected_project_id,
            "CANON_RUNTIME_CROSS_PROJECT_ROUTE_DENIED",
            "The observed-experience packet belongs to another project.",
            status="BLOCKED",
        )
    claimed = _sha256(value.get("packet_sha256"), field="packet_sha256")
    actual = sha256_bytes(
        canonical_json_bytes(
            {key: item for key, item in value.items() if key != "packet_sha256"}
        )
    )
    classification = cast(dict[str, Any], value.get("classification") or {})
    authority_effects = cast(dict[str, Any], value.get("authority_effects") or {})
    require(
        claimed == actual
        and value.get("pointer_moved") is False
        and value.get("candidate_created") is False
        and value.get("hil_inferred") is False
        and value.get("private_reasoning_stored") is False
        and authority_effects
        == {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "chat_lineage": "HASHED_VISIBLE_SOURCE_EVENT_ONLY",
        }
        and classification.get("learning_accepted") is False,
        "CANON_OBSERVED_EXPERIENCE_BOUNDARY_INVALID",
        "The observed-experience packet crossed an authority or pointer boundary.",
        status="FAIL",
    )
    return value


def seal_observed_experience_packet(
    project_root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    runtime_task_id: str,
    plan_task_id: str,
    active_plan: dict[str, Any],
    event: dict[str, Any],
    expected_accepted_pv: str,
    expected_pointer_generation: int,
    input_kind: str | None = None,
) -> dict[str, Any]:
    """Seal one typed observation from a verified visible committed event."""

    root = Path(project_root).resolve()
    pointer_before = _pointer_snapshot(
        root,
        project_id=project_id,
        expected_accepted_pv=expected_accepted_pv,
        expected_pointer_generation=expected_pointer_generation,
    )
    exact_event = _validate_lineage_event(
        event,
        allowed_event_types=_OBSERVABLE_EVENT_TYPES,
        evidence_session_id=evidence_session_id,
        runtime_task_id=runtime_task_id,
    )
    plan = _active_plan_binding(
        active_plan,
        project_id=project_id,
        plan_task_id=plan_task_id,
    )
    classification = _plan_steer_classification(
        exact_event,
        input_kind=input_kind,
        active_plan=plan,
    )
    source_event_sha256 = _sha256(
        exact_event.get("event_sha256"), field="source_event_sha256"
    )
    identity = {
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "runtime_task_id": runtime_task_id,
        "plan_task_id": plan_task_id,
        "source_event_sha256": source_event_sha256,
        "classification": classification["classification"],
        "accepted_pv": pointer_before["accepted_pv"],
        "pointer_generation": pointer_before["pointer_generation"],
    }
    packet_id = "obs_" + sha256_bytes(canonical_json_bytes(identity))[:30].lower()
    packet = {
        "schema": OBSERVED_EXPERIENCE_SCHEMA,
        "packet_id": packet_id,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "runtime_task_id": runtime_task_id,
        "plan_task_id": plan_task_id,
        "active_plan": plan,
        "source_event": {
            "event_id": exact_event.get("event_id"),
            "event_type": exact_event.get("event_type"),
            "event_sha256": source_event_sha256,
            "visible_payload_sha256": exact_event.get("visible_payload_sha256"),
            "occurred_at": exact_event.get("occurred_at"),
            "raw_visible_payload_copied": False,
        },
        "classification": classification,
        "pointer": pointer_before,
        "authority_effects": {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "chat_lineage": "HASHED_VISIBLE_SOURCE_EVENT_ONLY",
        },
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "private_reasoning_stored": False,
        "sealed_at": exact_event.get("occurred_at"),
    }
    packet["packet_sha256"] = sha256_bytes(canonical_json_bytes(packet))
    validate_observed_experience_packet(packet, expected_project_id=project_id)
    path = (
        resolved_chat_lineage_root(root) / "observed-experience" / f"{packet_id}.json"
    )
    state = _write_immutable_packet(
        path,
        packet,
        hash_field="packet_sha256",
        conflict_code="CANON_OBSERVED_EXPERIENCE_IMMUTABILITY_CONFLICT",
    )
    pointer_after = _pointer_snapshot(
        root,
        project_id=project_id,
        expected_accepted_pv=expected_accepted_pv,
        expected_pointer_generation=expected_pointer_generation,
    )
    require(
        pointer_after == pointer_before,
        "CANON_RUNTIME_POINTER_CHANGED_DURING_SEAL",
        "The Project Truth pointer changed while sealing observed experience.",
        status="STALE",
    )
    return {
        "status": "PASS",
        "state": state,
        "packet": packet,
        "packet_path": str(path),
        "pointer_before": pointer_before,
        "pointer_after": pointer_after,
    }


def validate_host_exit_continuity_packet(
    value: dict[str, Any], *, expected_project_id: str | None = None
) -> dict[str, Any]:
    require(
        value.get("schema") == HOST_EXIT_CONTINUITY_SCHEMA,
        "HOST_EXIT_CONTINUITY_SCHEMA_INVALID",
        "The host-exit continuity packet schema is unsupported.",
        status="MISMATCH",
    )
    if expected_project_id is not None:
        require(
            value.get("project_id") == expected_project_id,
            "CANON_RUNTIME_CROSS_PROJECT_ROUTE_DENIED",
            "The host-exit packet belongs to another project.",
            status="BLOCKED",
        )
    claimed = _sha256(value.get("packet_sha256"), field="packet_sha256")
    actual = sha256_bytes(
        canonical_json_bytes(
            {key: item for key, item in value.items() if key != "packet_sha256"}
        )
    )
    authority_effects = cast(dict[str, Any], value.get("authority_effects") or {})
    persistence = cast(dict[str, Any], value.get("persistence") or {})
    require(
        claimed == actual
        and value.get("pointer_moved") is False
        and value.get("candidate_promoted") is False
        and value.get("hil_inferred") is False
        and value.get("private_reasoning_stored") is False
        and authority_effects
        == {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "host_entry": "PENDING_LATER_EXACT_CONSUMER",
        }
        and persistence.get("continuity_claimed") is False,
        "HOST_EXIT_CONTINUITY_BOUNDARY_INVALID",
        "The host-exit packet crossed an authority, HIL, or pointer boundary.",
        status="FAIL",
    )
    return value


def seal_host_exit_continuity_packet(
    project_root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    runtime_task_id: str,
    plan_task_id: str,
    active_plan: dict[str, Any],
    event: dict[str, Any],
    exit_slip: dict[str, Any],
    persistence_route: dict[str, Any],
    expected_accepted_pv: str,
    expected_pointer_generation: int,
) -> dict[str, Any]:
    """Seal an opaque host-exit locator when local continuity is insufficient."""

    root = Path(project_root).resolve()
    pointer_before = _pointer_snapshot(
        root,
        project_id=project_id,
        expected_accepted_pv=expected_accepted_pv,
        expected_pointer_generation=expected_pointer_generation,
    )
    exact_event = _validate_lineage_event(
        event,
        allowed_event_types={"turn.lifecycle_exit_slip"},
        evidence_session_id=evidence_session_id,
        runtime_task_id=runtime_task_id,
    )
    plan = _active_plan_binding(
        active_plan,
        project_id=project_id,
        plan_task_id=plan_task_id,
    )
    exit_slip_sha256 = _sha256(
        exit_slip.get("exit_slip_sha256"), field="exit_slip_sha256"
    )
    require(
        exit_slip_sha256
        == sha256_bytes(
            canonical_json_bytes(
                {
                    key: value
                    for key, value in exit_slip.items()
                    if key != "exit_slip_sha256"
                }
            )
        )
        and exact_event.get("visible_payload") == exit_slip
        and exit_slip.get("project_id") == project_id
        and exit_slip.get("evidence_session_id") == evidence_session_id
        and exit_slip.get("task_id") == runtime_task_id
        and exit_slip.get("plan_task_id") == plan_task_id
        and int(exit_slip.get("active_row") or 0) == plan["row"],
        "HOST_EXIT_CONTINUITY_EXIT_SLIP_MISMATCH",
        "The host-exit source event does not match its exact sealed Exit Slip.",
        status="MISMATCH",
    )
    server_filesystem = str(persistence_route.get("server_filesystem") or "")
    durable_required = persistence_route.get("durable_required") is True
    primary_runtime_authority = str(
        persistence_route.get("primary_runtime_authority") or ""
    )
    interaction_profile = str(
        persistence_route.get("interaction_profile") or "HOST_SURFACE_UNSPECIFIED"
    ).upper()
    durable_local = (
        server_filesystem == "DURABLE"
        and not durable_required
        and primary_runtime_authority
        in {"LOCAL_DURABLE_SQLITE", "DURABLE_MOUNT_SQLITE"}
    )
    if durable_local:
        pointer_after = _pointer_snapshot(
            root,
            project_id=project_id,
            expected_accepted_pv=expected_accepted_pv,
            expected_pointer_generation=expected_pointer_generation,
        )
        require(
            pointer_after == pointer_before,
            "CANON_RUNTIME_POINTER_CHANGED_DURING_SEAL",
            "The Project Truth pointer changed while evaluating durable-local exit.",
            status="STALE",
        )
        return {
            "status": "PASS",
            "state": "NOT_REQUIRED_DURABLE_LOCAL_AUTHORITY",
            "interaction_profile": "DURABLE_LOCAL",
            "packet": None,
            "packet_path": None,
            "pointer_before": pointer_before,
            "pointer_after": pointer_after,
        }
    require(
        durable_required
        and server_filesystem == "EPHEMERAL_OR_UNAVAILABLE"
        and persistence_route.get("mode") == "configured_durable_connector"
        and primary_runtime_authority == "CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
        "HOST_EXIT_CONTINUITY_ROUTE_INVALID",
        "An insufficiently durable host requires the configured durable-connector route.",
        status="BLOCKED",
        persistence_route=persistence_route,
    )
    route_class = (
        "STATELESS_HEADLESS"
        if interaction_profile in _HEADLESS_PROFILES
        else "INTERACTIVE_EPHEMERAL"
    )
    source_event_sha256 = _sha256(
        exact_event.get("event_sha256"), field="source_event_sha256"
    )
    identity = {
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "runtime_task_id": runtime_task_id,
        "plan_task_id": plan_task_id,
        "exit_slip_sha256": exit_slip_sha256,
        "source_event_sha256": source_event_sha256,
        "pointer_generation": pointer_before["pointer_generation"],
        "route_class": route_class,
    }
    packet_id = "hexit_" + sha256_bytes(canonical_json_bytes(identity))[:28].lower()
    opaque_locator = f"evi+host-exit://{packet_id}"
    locator_sha256 = sha256_bytes(opaque_locator.encode("utf-8"))
    packet = {
        "schema": HOST_EXIT_CONTINUITY_SCHEMA,
        "packet_id": packet_id,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "runtime_task_id": runtime_task_id,
        "plan_task_id": plan_task_id,
        "active_plan": plan,
        "route": {
            "interaction_profile": route_class,
            "measured_server_filesystem": server_filesystem,
            "primary_runtime_authority": primary_runtime_authority,
            "storage_connector_required": True,
            "google_drive_primary_runtime": False,
        },
        "pointer": pointer_before,
        "source_exit": {
            "exit_slip_sha256": exit_slip_sha256,
            "lineage_event_sha256": source_event_sha256,
            "reason": exit_slip.get("reason"),
            "lineage_head_sha256": source_event_sha256,
        },
        "opaque_locator": opaque_locator,
        "opaque_locator_sha256": locator_sha256,
        "idempotency_key": sha256_bytes(
            canonical_json_bytes(
                {
                    "project_id": project_id,
                    "packet_id": packet_id,
                    "locator_sha256": locator_sha256,
                }
            )
        ),
        "persistence": {
            "state": "AWAITING_LATER_DURABLE_CONNECTOR_PERSISTENCE",
            "durable_persisted": False,
            "storage_receipt_sha256": None,
            "exit_complete": False,
            "continuity_claimed": False,
            "consumer_owner": "INDEPENDENT_HOST_ENTRY_CONTINUITY_ROW",
        },
        "authority_effects": {
            "project_truth": "NONE",
            "canon_input": "NONE",
            "agent_learning": "NONE",
            "host_entry": "PENDING_LATER_EXACT_CONSUMER",
        },
        "candidate_promoted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "private_reasoning_stored": False,
        "sealed_at": exact_event.get("occurred_at"),
    }
    packet["packet_sha256"] = sha256_bytes(canonical_json_bytes(packet))
    validate_host_exit_continuity_packet(packet, expected_project_id=project_id)
    path = (
        resolved_chat_lineage_root(root) / "host-exit-continuity" / f"{packet_id}.json"
    )
    state = _write_immutable_packet(
        path,
        packet,
        hash_field="packet_sha256",
        conflict_code="HOST_EXIT_CONTINUITY_IMMUTABILITY_CONFLICT",
    )
    pointer_after = _pointer_snapshot(
        root,
        project_id=project_id,
        expected_accepted_pv=expected_accepted_pv,
        expected_pointer_generation=expected_pointer_generation,
    )
    require(
        pointer_after == pointer_before,
        "CANON_RUNTIME_POINTER_CHANGED_DURING_SEAL",
        "The Project Truth pointer changed while sealing host-exit continuity.",
        status="STALE",
    )
    return {
        "status": "PASS",
        "state": state,
        "interaction_profile": route_class,
        "packet": packet,
        "packet_path": str(path),
        "pointer_before": pointer_before,
        "pointer_after": pointer_after,
    }
