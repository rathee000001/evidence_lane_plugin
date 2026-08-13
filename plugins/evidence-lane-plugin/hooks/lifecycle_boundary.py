"""Compaction and best-effort session-boundary transport receipts.

This hook signals that the skill must re-enter after PostCompact.  It never
performs native PV reads, embeds Plan rows, or requests host behavior.
"""

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
        current_persistent_change_display,
        gap_receipt,
        policy_state,
        record_lifecycle_boundary_event,
        resolve_codex_hook_store_root,
    )

    return (
        TurnControlError,
        bind_codex_host_payload,
        current_persistent_change_display,
        gap_receipt,
        policy_state,
        record_lifecycle_boundary_event,
        resolve_codex_hook_store_root,
    )


def _record(payload: dict[str, Any], event_name: str) -> tuple[dict[str, Any], bool]:
    (
        TurnControlError,
        bind_codex_host_payload,
        current_persistent_change_display,
        gap_receipt,
        policy_state,
        record_lifecycle_boundary_event,
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
        return ({"state": "NOT_GOVERNED", "event_name": event_name}, True)
    try:
        normalized, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name=event_name,
            allow_alias_claim=True,
        )
        receipt = record_lifecycle_boundary_event(
            root,
            host_payload=normalized,
            event_name=event_name,
        )
        if event_name == "PostCompact":
            receipt["persistent_change_display"] = current_persistent_change_display(
                root, host_payload=normalized
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
        return receipt, True
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=raw_policy,
        )
        return receipt, event_name == "SessionEnd"


def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt, should_continue = _record(payload, event_name)
    except Exception as exc:  # noqa: BLE001 - visible boundary gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "LIFECYCLE_BOUNDARY_UNAVAILABLE",
            "event_name": event_name,
            "error_type": type(exc).__name__,
            "source_mutation_authorized": False,
            "private_reasoning_stored": False,
        }
        should_continue = event_name == "SessionEnd"
    result: dict[str, Any] = {
        "continue": should_continue,
        "hookSpecificOutput": {
            "hookEventName": event_name,
            "additionalContext": "EVIDENCE_LANE_LIFECYCLE_BOUNDARY="
            + json.dumps(receipt, sort_keys=True, separators=(",", ":")),
        },
    }
    if not should_continue:
        result["stopReason"] = (
            "Governed Evidence Lane lifecycle boundary failed closed."
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
