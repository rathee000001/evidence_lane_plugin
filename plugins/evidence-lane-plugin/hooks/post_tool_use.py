"""Read-only ongoing Goal projection for Evidence Lane PostToolUse."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _store_root() -> Path:
    return Path(
        os.environ.get("EVIDENCE_LANE_DATA_ROOT")
        or os.environ.get("PLUGIN_DATA")
        or Path.home() / "EvidenceLanePV"
    ).resolve()


def _load_control():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.codex_turn_control import (
        TurnControlError,
        bind_codex_host_payload,
        current_persistent_change_display,
        gap_receipt,
        persistent_change_system_notice,
        policy_state,
    )

    return (
        TurnControlError,
        bind_codex_host_payload,
        current_persistent_change_display,
        gap_receipt,
        persistent_change_system_notice,
        policy_state,
    )


def _project(payload: dict[str, Any]) -> dict[str, Any]:
    root = _store_root()
    (
        TurnControlError,
        bind_codex_host_payload,
        current_persistent_change_display,
        gap_receipt,
        _,
        policy_state,
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
    if not raw_policy.get("governed_session"):
        return {
            "state": "NOT_PROJECTED",
            "reason": "NO_BOUND_EVIDENCE_LANE_SESSION",
            "read_only_projection": True,
            "tool_input_stored": False,
            "tool_response_stored": False,
            "private_reasoning_stored": False,
        }
    try:
        normalized_payload, host_binding = bind_codex_host_payload(
            root,
            host_payload=payload,
            event_name="PostToolUse",
            allow_alias_claim=True,
        )
    except TurnControlError as exc:
        return gap_receipt(
            root,
            host_payload=payload,
            error=exc,
            policy=raw_policy,
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
    if not policy.get("strict_required"):
        return {
            "state": "NOT_PROJECTED",
            "reason": "SEALED_MODE_PLUS_PLAN_NOT_ACTIVE",
            "project_id": policy.get("project_id"),
            "evidence_session_id": policy.get("evidence_session_id"),
            "read_only_projection": True,
            "tool_input_stored": False,
            "tool_response_stored": False,
            "private_reasoning_stored": False,
        }
    try:
        receipt = current_persistent_change_display(
            root,
            host_payload=normalized_payload,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt
    except TurnControlError as exc:
        receipt = gap_receipt(
            root,
            host_payload=normalized_payload,
            error=exc,
            policy=policy,
        )
        if host_binding is not None:
            receipt["host_binding"] = host_binding
        return receipt


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt = _project(payload)
    except Exception as exc:  # noqa: BLE001 - expose a projection gap, never hide it
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "POST_TOOL_USE_CHANGE_PROJECTION_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed_for_persistent_display_claim": True,
            "source_mutation_authorized": False,
            "tool_input_stored": False,
            "tool_response_stored": False,
            "private_reasoning_stored": False,
        }
    result: dict[str, Any] = {"continue": True}
    display = receipt.get("persistent_change_display")
    if isinstance(display, dict):
        _, _, _, _, persistent_change_system_notice, _ = _load_control()
        notice = persistent_change_system_notice(
            display,
            phase="POST_TOOL_USE",
            turn_receipt=receipt,
        )
        serialized = json.dumps(notice, sort_keys=True, separators=(",", ":"))
        result["systemMessage"] = (
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=" + serialized
        )
        result["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=" + serialized
            ),
        }
    elif receipt.get("state") == "TURN_CONTROL_GAP":
        result["systemMessage"] = (
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY_GAP="
            + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
