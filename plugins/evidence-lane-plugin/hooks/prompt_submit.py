"""Authoritative secret-redacted PREPARE adapter for governed Codex turns."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _normalize_user_prompt_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    """Bind one pending visible user input to native ``UserPromptSubmit``.

    Codex dispatches pending ``TurnInput::UserInput`` values through this hook,
    including input submitted through ``turn/steer``. Goal control uses the
    separate ``thread/goal/set`` host route and is not claimed by this adapter.
    Evidence Lane derives prompt/steer kind from sealed turn history;
    caller-provided classification flags are never authority.
    """

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


def _store_root() -> Path:
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        resolve_codex_hook_store_root,
    )

    return resolve_codex_hook_store_root()


def _load_control():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        persistent_change_system_message,
        persistent_change_system_notice,
        policy_state,
        prepare_turn,
        record_non_strict_visible_input,
    )

    return (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        persistent_change_system_message,
        persistent_change_system_notice,
        policy_state,
        prepare_turn,
        record_non_strict_visible_input,
    )


def _record(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    payload = _normalize_user_prompt_dispatch(payload)
    root = _store_root()
    (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        _,
        _,
        policy_state,
        prepare_turn,
        record_non_strict_visible_input,
    ) = _load_control()
    raw_policy = policy_state(
        root,
        host_session_id=str(payload.get("session_id") or "").strip(),
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(
            payload.get("transcript_path")
            or payload.get("agent_transcript_path")
            or ""
        ),
    )
    try:
        normalized_payload, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="UserPromptSubmit",
            allow_alias_claim=True,
        )
    except TurnControlError as exc:
        return (
            gap_receipt(
                root,
                host_payload=payload,
                error=exc,
                policy=raw_policy,
            ),
            not bool(raw_policy.get("strict_required")),
        )
    policy = policy_state(
        root,
        host_session_id=str(normalized_payload.get("session_id") or "").strip(),
        cwd=str(normalized_payload.get("cwd") or ""),
        transcript_path=str(
            normalized_payload.get("transcript_path")
            or normalized_payload.get("agent_transcript_path")
            or ""
        ),
    )
    if not policy.get("governed_session"):
        return (
            {
                "state": "NOT_INDEXED",
                "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
                "raw_prompt_stored": False,
                "private_reasoning_stored": False,
            },
            True,
        )
    if policy.get("runtime_state") != "ACTIVE":
        error = TurnControlError(
            "EVIDENCE_LANE_RUNTIME_DETACHED",
            "The governed Evidence Lane runtime is detached; no prompt bytes were indexed.",
            runtime_state=policy.get("runtime_state"),
        )
        return (
            gap_receipt(
                root,
                host_payload=payload,
                error=error,
                policy=policy,
            ),
            not bool(policy.get("strict_required")),
        )
    if not policy.get("strict_required"):
        try:
            receipt = record_non_strict_visible_input(
                root, host_payload=normalized_payload
            )
            if host_binding is not None:
                receipt["host_binding"] = host_binding
            return receipt, True
        except TurnControlError as exc:
            receipt = gap_receipt(
                root,
                host_payload=normalized_payload,
                error=exc,
                policy=policy,
            )
            if host_binding is not None:
                receipt["host_binding"] = host_binding
            return receipt, True
        except Exception as exc:  # noqa: BLE001 - visible non-strict gap, no raw input
            error = TurnControlError(
                "TURN_CONTROL_NON_STRICT_VISIBLE_INDEX_UNAVAILABLE",
                "The bounded pre-Plan visible-input index is unavailable.",
                error_type=type(exc).__name__,
            )
            receipt = gap_receipt(
                root,
                host_payload=normalized_payload,
                error=error,
                policy=policy,
            )
            if host_binding is not None:
                receipt["host_binding"] = host_binding
            return receipt, True
    try:
        receipt = prepare_turn(root, host_payload=normalized_payload)
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt, True
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized_payload,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt, False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt, should_continue = _record(payload)
    except Exception as exc:  # noqa: BLE001 - missing control code is a visible gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "TURN_CONTROL_MODULE_OR_POLICY_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed": True,
            "source_mutation_authorized": False,
            "raw_prompt_stored": False,
            "private_reasoning_stored": False,
        }
        should_continue = False
    result = {
        "continue": should_continue,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "EVIDENCE_LANE_PROMPT_ENTRY="
                + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
            ),
        },
    }
    display = receipt.get("persistent_change_display")
    if isinstance(display, dict):
        (
            _,
            _,
            _,
            persistent_change_system_message,
            persistent_change_system_notice,
            _,
            _,
            _,
        ) = _load_control()
        notice = persistent_change_system_notice(
            display,
            phase="TURN_PREPARE",
            turn_receipt=receipt,
        )
        serialized = json.dumps(notice, sort_keys=True, separators=(",", ":"))
        result["systemMessage"] = persistent_change_system_message(notice)
        result["hookSpecificOutput"]["additionalContext"] += (
            "\nEVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=" + serialized
        )
    if not should_continue:
        result["stopReason"] = (
            "Governed Evidence Lane PREPARE failed closed before model reasoning or source mutation."
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
