"""Skill-owned consumers for sealed Codex lifecycle-hook envelopes.

Hook command files are deliberately thin transport adapters.  They parse the
host signal, seal it with :mod:`evidence_lane_plugin.hook_contract`, and hand
the exact envelope to this module.  This module owns PREPARE, COMMIT, durable
event recording, binding checks, and current-change projection on behalf of
the installed Evidence Lane lifecycle skill.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .codex_turn_control import (
    TurnControlError,
    bind_codex_host_payload,
    commit_turn,
    current_persistent_change_display,
    gap_receipt,
    persistent_change_system_message,
    persistent_change_system_notice,
    policy_state,
    prepare_goal_continuation_turn,
    prepare_turn,
    record_lifecycle_boundary_event,
    record_non_strict_visible_input,
    record_tool_event,
    resolve_codex_hook_store_root,
    session_start_control,
)
from .hook_contract import build_hook_transport_envelope

SKILL_RUNTIME_OWNER = "INSTALLED_EVIDENCE_LANE_CODE_LIFECYCLE_SKILL"
HOOK_RUNTIME_ROLE = "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"


def _validate_transport(
    event_name: str,
    payload: Mapping[str, Any],
    transport: Mapping[str, Any],
) -> None:
    expected = build_hook_transport_envelope(event_name, payload)
    if dict(transport) != expected:
        raise TurnControlError(
            "HOOK_TRANSPORT_ENVELOPE_MISMATCH",
            "The skill action refused a hook envelope that did not match the host signal.",
            event_name=event_name,
        )


def _owned_receipt(
    receipt: dict[str, Any],
    *,
    transport: Mapping[str, Any],
    action: str,
) -> dict[str, Any]:
    receipt["hook_transport_envelope"] = dict(transport)
    receipt["hook_runtime_role"] = HOOK_RUNTIME_ROLE
    receipt["skill_action_owner"] = SKILL_RUNTIME_OWNER
    receipt["skill_action"] = action
    receipt["skill_action_executed"] = True
    receipt["hook_behavior_executed"] = False
    return receipt


def _raw_policy(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    return policy_state(
        root,
        host_session_id=str(payload.get("session_id") or "").strip(),
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(
            payload.get("transcript_path")
            or payload.get("agent_transcript_path")
            or ""
        ),
    )


def normalize_user_prompt_dispatch(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize host input without trusting caller-provided prompt kind."""

    normalized = dict(payload)
    supplied_source = str(normalized.get("source") or "").strip().lower()
    ignored_input_kind_claim = bool(
        normalized.get("is_goal") is not None
        or normalized.get("is_steer") is not None
        or supplied_source
    )
    normalized.pop("source", None)
    normalized.pop("is_goal", None)
    normalized.pop("is_steer", None)
    normalized["evidence_lane_capture_dispatch"] = {
        "surface": "PENDING_VISIBLE_USER_INPUT",
        "host_route": "inspect_pending_input(TurnInput::UserInput)",
        "native_hook_event": "UserPromptSubmit",
        "adapter_invocation_observed": True,
        "host_payload_hook_event_name": str(
            normalized.get("hook_event_name") or ""
        ),
        "pre_reasoning_hook_contract": (
            "USERPROMPTSUBMIT_RUNS_DURING_PENDING_INPUT_INSPECTION"
        ),
        "installed_host_dispatch_independently_proven": False,
        "independent_host_proof_owner": "INSTALLED_HOST_ACCEPTANCE_CORRELATION",
        "input_kind_derived_from_sealed_state": True,
        "caller_input_kind_authority": False,
        "caller_input_kind_claim_present": ignored_input_kind_claim,
    }
    return normalized


def consume_session_start_transport(
    payload: dict[str, Any],
    transport: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    _validate_transport("SessionStart", payload, transport)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, payload)
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="SessionStart",
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=raw_policy,
        )
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="SESSION_START_BIND_OR_REENTRY",
            ),
            not bool(raw_policy.get("strict_required")),
        )
    policy = _raw_policy(root, normalized)
    if not policy.get("governed_session"):
        return (
            _owned_receipt(
                {
                    "state": "NO_BOUND_EVIDENCE_LANE_SESSION",
                    "strict_required": False,
                    "scrollback_authority": False,
                    "transcript_authority": False,
                },
                transport=transport,
                action="SESSION_START_BIND_OR_REENTRY",
            ),
            True,
        )
    if not policy.get("strict_required"):
        return (
            _owned_receipt(
                {
                    "state": "TURN_CONTROL_NOT_REQUIRED_YET",
                    "reason": "SEALED_MODE_PLUS_PLAN_NOT_ACTIVE",
                    "project_id": policy.get("project_id"),
                    "evidence_session_id": policy.get("evidence_session_id"),
                    "scrollback_authority": False,
                    "transcript_authority": False,
                },
                transport=transport,
                action="SESSION_START_BIND_OR_REENTRY",
            ),
            True,
        )
    try:
        receipt = session_start_control(root, host_payload=normalized)
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="SESSION_START_BIND_OR_REENTRY",
            ),
            True,
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="SESSION_START_BIND_OR_REENTRY",
            ),
            False,
        )


def consume_prompt_transport(
    payload: dict[str, Any],
    transport: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    _validate_transport("UserPromptSubmit", payload, transport)
    normalized_input = normalize_user_prompt_dispatch(payload)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, normalized_input)
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=normalized_input,
            event_name="UserPromptSubmit",
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized_input,
            error=exc,
            policy=raw_policy,
        )
        return (
            _owned_receipt(receipt, transport=transport, action="PREPARE"),
            not bool(raw_policy.get("strict_required")),
        )
    policy = _raw_policy(root, normalized)
    if not policy.get("governed_session"):
        return (
            _owned_receipt(
                {
                    "state": "NOT_INDEXED",
                    "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
                    "raw_prompt_stored": False,
                    "private_reasoning_stored": False,
                },
                transport=transport,
                action="PREPARE",
            ),
            True,
        )
    if policy.get("runtime_state") != "ACTIVE":
        error = TurnControlError(
            "EVIDENCE_LANE_RUNTIME_DETACHED",
            "The governed Evidence Lane runtime is detached; no prompt bytes were indexed.",
            runtime_state=policy.get("runtime_state"),
        )
        receipt = gap_receipt(
            root,
            host_payload=normalized,
            error=error,
            policy=policy,
        )
        return (
            _owned_receipt(receipt, transport=transport, action="PREPARE"),
            not bool(policy.get("strict_required")),
        )
    if not policy.get("strict_required"):
        try:
            receipt = record_non_strict_visible_input(
                root,
                host_payload=normalized,
            )
        except TurnControlError as exc:
            receipt = gap_receipt(
                root,
                host_payload=normalized,
                error=exc,
                policy=policy,
            )
        except Exception as exc:  # noqa: BLE001 - visible bounded gap
            error = TurnControlError(
                "TURN_CONTROL_NON_STRICT_VISIBLE_INDEX_UNAVAILABLE",
                "The bounded pre-Plan visible-input index is unavailable.",
                error_type=type(exc).__name__,
            )
            receipt = gap_receipt(
                root,
                host_payload=normalized,
                error=error,
                policy=policy,
            )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return (
            _owned_receipt(receipt, transport=transport, action="PREPARE"),
            True,
        )
    try:
        receipt = prepare_turn(root, host_payload=normalized)
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return (
            _owned_receipt(receipt, transport=transport, action="PREPARE"),
            True,
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return (
            _owned_receipt(receipt, transport=transport, action="PREPARE"),
            False,
        )


def consume_pre_tool_transport(
    payload: dict[str, Any],
    transport: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    _validate_transport("PreToolUse", payload, transport)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, payload)
    if not raw_policy.get("governed_session"):
        return (
            _owned_receipt(
                {"state": "NOT_GOVERNED", "source_mutation_authorized": None},
                transport=transport,
                action="PROSPECTIVE_TOOL_BOUNDARY",
            ),
            True,
        )
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="PreToolUse",
        )
        policy = _raw_policy(root, normalized)
        if not policy.get("strict_required"):
            return (
                _owned_receipt(
                    {
                        "state": "NOT_STRICT",
                        "source_mutation_authorized": None,
                    },
                    transport=transport,
                    action="PROSPECTIVE_TOOL_BOUNDARY",
                ),
                True,
            )
        goal_continuation_entry = None
        try:
            receipt = record_tool_event(
                root,
                host_payload=normalized,
                phase="before",
            )
        except TurnControlError as exc:
            if exc.code != "TURN_CONTROL_PREFLIGHT_REQUIRED":
                raise
            goal_continuation_entry = prepare_goal_continuation_turn(
                root,
                host_payload=normalized,
            )
            receipt = record_tool_event(
                root,
                host_payload=normalized,
                phase="before",
            )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        if goal_continuation_entry is not None:
            receipt["goal_continuation_entry"] = goal_continuation_entry
        receipt["prospective_mutation_guard"] = (
            "USERPROMPTSUBMIT_PREPARE_OR_EXACT_NATIVE_TASK_GOAL_BINDING_REQUIRED"
        )
        receipt["source_mutation_authorized"] = True
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="PROSPECTIVE_TOOL_BOUNDARY",
            ),
            True,
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=raw_policy,
        )
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="PROSPECTIVE_TOOL_BOUNDARY",
            ),
            False,
        )


def consume_post_tool_transport(
    payload: dict[str, Any],
    transport: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_transport("PostToolUse", payload, transport)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, payload)
    if not raw_policy.get("governed_session"):
        return _owned_receipt(
            {
                "state": "NOT_PROJECTED",
                "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
                "read_only_projection": True,
                "tool_input_stored": False,
                "tool_response_stored": False,
                "private_reasoning_stored": False,
            },
            transport=transport,
            action="TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
        )
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="PostToolUse",
        )
    except TurnControlError as exc:
        return _owned_receipt(
            gap_receipt(
                root,
                host_payload=payload,
                error=exc,
                policy=raw_policy,
            ),
            transport=transport,
            action="TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
        )
    policy = _raw_policy(root, normalized)
    if not policy.get("strict_required"):
        return _owned_receipt(
            {
                "state": "NOT_PROJECTED",
                "reason": "SEALED_MODE_PLUS_PLAN_NOT_ACTIVE",
                "project_id": policy.get("project_id"),
                "evidence_session_id": policy.get("evidence_session_id"),
                "read_only_projection": True,
                "tool_input_stored": False,
                "tool_response_stored": False,
                "private_reasoning_stored": False,
            },
            transport=transport,
            action="TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
        )
    try:
        tool_event = None
        if normalized.get("tool_use_id") and normalized.get("tool_name"):
            try:
                tool_event = record_tool_event(
                    root,
                    host_payload=normalized,
                    phase="after",
                )
            except TurnControlError as exc:
                tool_event = {
                    "state": "NOT_RECORDED_NO_PREPARED_TURN",
                    "code": exc.code,
                    "raw_tool_payload_stored": False,
                    "private_reasoning_stored": False,
                }
        receipt = current_persistent_change_display(
            root,
            host_payload=normalized,
        )
        if tool_event is not None:
            receipt["tool_event"] = tool_event
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return _owned_receipt(
            receipt,
            transport=transport,
            action="TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return _owned_receipt(
            receipt,
            transport=transport,
            action="TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
        )


_OPTIONAL_OBSERVER_EVENTS = {
    "PermissionRequest",
    "SubagentStart",
    "SubagentStop",
}


def consume_optional_observer_transport(
    payload: dict[str, Any],
    event_name: str,
    transport: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify an optional host event without controlling or mutating it."""

    if event_name not in _OPTIONAL_OBSERVER_EVENTS:
        raise TurnControlError(
            "HOOK_OPTIONAL_OBSERVER_EVENT_UNSUPPORTED",
            "The optional hook observer received an unsupported event.",
            event_name=event_name,
        )
    _validate_transport(event_name, payload, transport)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, payload)
    if not raw_policy.get("governed_session"):
        return _owned_receipt(
            {
                "state": "NOT_GOVERNED",
                "event_name": event_name,
                "host_control_emitted": False,
                "source_mutation_authorized": False,
            },
            transport=transport,
            action="BOUND_OPTIONAL_EVENT_OBSERVATION",
        )
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name=event_name,
        )
    except TurnControlError as exc:
        return _owned_receipt(
            gap_receipt(
                root,
                host_payload=payload,
                error=exc,
                policy=raw_policy,
            ),
            transport=transport,
            action="BOUND_OPTIONAL_EVENT_OBSERVATION",
        )
    policy = _raw_policy(root, normalized)
    exact_binding = policy.get("binding_match") == "EXACT_HOST_SESSION"
    if not exact_binding:
        return _owned_receipt(
            {
                "state": "OPTIONAL_EVENT_REJECTED_WRONG_TASK",
                "event_name": event_name,
                "binding_match": policy.get("binding_match"),
                "host_control_emitted": False,
                "source_mutation_authorized": False,
                "cross_task_disclosure": False,
            },
            transport=transport,
            action="BOUND_OPTIONAL_EVENT_OBSERVATION",
        )
    safe_transport = dict(transport.get("safe_payload") or {})
    receipt: dict[str, Any] = {
        "state": "BOUND_OPTIONAL_EVENT_OBSERVED",
        "event_name": event_name,
        "project_id": policy.get("project_id"),
        "evidence_session_id": policy.get("evidence_session_id"),
        "agent_id_sha256": safe_transport.get("agent_id_sha256"),
        "agent_type": safe_transport.get("agent_type"),
        "permission_decision_emitted": False,
        "subagent_control_emitted": False,
        "continuation_control_emitted": False,
        "source_mutation_authorized": False,
        "plan_mutated": False,
        "goal_mutated": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "raw_payload_stored": False,
        "private_reasoning_stored": False,
        "cross_task_disclosure": False,
    }
    if host_binding is not None:
        receipt["host_binding"] = host_binding
    return _owned_receipt(
        receipt,
        transport=transport,
        action="BOUND_OPTIONAL_EVENT_OBSERVATION",
    )


def consume_boundary_transport(
    payload: dict[str, Any],
    event_name: str,
    transport: Mapping[str, Any],
) -> tuple[dict[str, Any], bool]:
    _validate_transport(event_name, payload, transport)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, payload)
    if not raw_policy.get("governed_session"):
        return (
            _owned_receipt(
                {"state": "NOT_GOVERNED", "event_name": event_name},
                transport=transport,
                action="COMPACTION_OR_SESSION_BOUNDARY",
            ),
            True,
        )
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name=event_name,
        )
        receipt = record_lifecycle_boundary_event(
            root,
            host_payload=normalized,
            event_name=event_name,
        )
        if event_name == "PostCompact":
            receipt["persistent_change_display"] = current_persistent_change_display(
                root,
                host_payload=normalized,
            )["persistent_change_display"]
            receipt["rehydrated_from_durable_authority"] = True
            receipt["skill_behavior_reentry"] = {
                "state": "SKILL_REENTRY_REQUIRED",
                "trigger": "POSTCOMPACT",
                "goal_presence_required": False,
                "hook_scope": "LIFECYCLE_SIGNAL_ONLY",
                "native_behavior_performed_by_hook": False,
                "host_behavior_performed_by_hook": False,
                "full_plan_rows_embedded_by_hook": False,
            }
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="COMPACTION_OR_SESSION_BOUNDARY",
            ),
            True,
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=raw_policy,
        )
        return (
            _owned_receipt(
                receipt,
                transport=transport,
                action="COMPACTION_OR_SESSION_BOUNDARY",
            ),
            event_name == "SessionEnd",
        )


def consume_stop_transport(
    payload: dict[str, Any],
    transport: Mapping[str, Any],
) -> dict[str, Any]:
    _validate_transport("Stop", payload, transport)
    root = resolve_codex_hook_store_root()
    raw_policy = _raw_policy(root, payload)
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="Stop",
        )
    except TurnControlError as exc:
        return _owned_receipt(
            gap_receipt(
                root,
                host_payload=payload,
                error=exc,
                policy=raw_policy,
            ),
            transport=transport,
            action="COMMIT",
        )
    policy = _raw_policy(root, normalized)
    if not policy.get("governed_session"):
        return _owned_receipt(
            {
                "state": "NOT_INDEXED",
                "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
                "private_reasoning_stored": False,
            },
            transport=transport,
            action="COMMIT",
        )
    if not policy.get("strict_required"):
        return _owned_receipt(
            {
                "state": "TURN_CONTROL_NOT_REQUIRED_YET",
                "reason": "SEALED_MODE_PLUS_PLAN_NOT_ACTIVE",
                "project_id": policy.get("project_id"),
                "evidence_session_id": policy.get("evidence_session_id"),
                "private_reasoning_stored": False,
            },
            transport=transport,
            action="COMMIT",
        )
    try:
        receipt = commit_turn(root, host_payload=normalized)
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return _owned_receipt(
            receipt,
            transport=transport,
            action="COMMIT",
        )
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return _owned_receipt(
            receipt,
            transport=transport,
            action="COMMIT",
        )


def render_persistent_notice(
    display: Mapping[str, Any],
    *,
    phase: str,
    turn_receipt: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Render a skill-owned current-change notice for a thin hook adapter."""

    notice = persistent_change_system_notice(
        dict(display),
        phase=phase,
        turn_receipt=dict(turn_receipt),
    )
    return persistent_change_system_message(notice), notice
