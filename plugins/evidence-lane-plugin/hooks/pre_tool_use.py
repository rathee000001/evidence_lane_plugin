"""Fail-closed PREPARE binding guard for governed Codex tool use."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load_control():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        policy_state,
        record_tool_event,
        resolve_codex_hook_store_root,
    )

    return (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        policy_state,
        record_tool_event,
        resolve_codex_hook_store_root,
    )


def _guard(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    (
        TurnControlError,
        bind_codex_host_payload,
        gap_receipt,
        policy_state,
        record_tool_event,
        resolve_codex_hook_store_root,
    ) = _load_control()
    root = resolve_codex_hook_store_root()
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
    if not raw_policy.get("governed_session"):
        return ({"state": "NOT_GOVERNED", "source_mutation_authorized": None}, True)
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="PreToolUse",
            allow_alias_claim=True,
        )
        policy = policy_state(
            root,
            host_session_id=str(normalized.get("session_id") or "").strip(),
            cwd=str(normalized.get("cwd") or ""),
            transcript_path=str(
                normalized.get("transcript_path")
                or normalized.get("agent_transcript_path")
                or ""
            ),
        )
        if not policy.get("strict_required"):
            return ({"state": "NOT_STRICT", "source_mutation_authorized": None}, True)
        receipt = record_tool_event(root, host_payload=normalized, phase="before")
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        receipt["prospective_mutation_guard"] = "PREPARE_AND_EXACT_BINDING_REQUIRED"
        receipt["source_mutation_authorized"] = True
        return receipt, True
    except TurnControlError as exc:
        return (
            gap_receipt(
                root,
                host_payload=payload,
                error=exc,
                policy=raw_policy,
            ),
            False,
        )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt, should_continue = _guard(payload)
    except Exception as exc:  # noqa: BLE001 - visible fail-closed hook gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "PRE_TOOL_USE_GUARD_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "source_mutation_authorized": False,
            "raw_tool_payload_stored": False,
            "private_reasoning_stored": False,
        }
        should_continue = False
    result: dict[str, Any] = {
        "continue": should_continue,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": "EVIDENCE_LANE_PRE_TOOL_GUARD="
            + json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        },
    }
    if not should_continue:
        result["stopReason"] = (
            "Governed Evidence Lane PREPARE or exact binding was absent before tool use."
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
