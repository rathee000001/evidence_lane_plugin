"""Thin PostToolUse transport adapter for the lifecycle skill."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load_runtime():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.hook_contract import build_hook_transport_envelope
    from evidence_lane_plugin.hook_skill_runtime import (
        consume_post_tool_transport,
        render_persistent_notice,
    )

    return (
        build_hook_transport_envelope,
        consume_post_tool_transport,
        render_persistent_notice,
    )


def _project(payload: dict[str, Any]) -> dict[str, Any]:
    build_transport, consume_transport, _ = _load_runtime()
    transport = build_transport("PostToolUse", payload)
    return consume_transport(payload, transport)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt = _project(payload)
    except Exception as exc:  # noqa: BLE001 - expose the transport gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "HOOK_TRANSPORT_OR_SKILL_TOOL_RECEIPT_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed_for_persistent_display_claim": True,
            "source_mutation_authorized": False,
            "tool_input_stored": False,
            "tool_response_stored": False,
            "private_reasoning_stored": False,
            "hook_runtime_role": (
                "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
            ),
        }
    result: dict[str, Any] = {"continue": True}
    display = receipt.get("persistent_change_display")
    if isinstance(display, dict):
        _, _, render_notice = _load_runtime()
        message, notice = render_notice(
            display,
            phase="POST_TOOL_USE",
            turn_receipt=receipt,
        )
        result["systemMessage"] = message
        result["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION="
                + json.dumps(notice, sort_keys=True, separators=(",", ":"))
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
