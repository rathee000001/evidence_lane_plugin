"""Thin UserPromptSubmit transport adapter for the lifecycle skill."""

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
        consume_prompt_transport,
        render_persistent_notice,
    )

    return (
        build_hook_transport_envelope,
        consume_prompt_transport,
        render_persistent_notice,
    )


def _load_behavior_handoff():
    hook_root = Path(__file__).resolve().parent
    if str(hook_root) not in sys.path:
        sys.path.insert(0, str(hook_root))
    from behavior_handoff import attach_consumed_behavior_handoff

    return attach_consumed_behavior_handoff


def _record(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    build_transport, consume_transport, _ = _load_runtime()
    transport = build_transport("UserPromptSubmit", payload)
    receipt, should_continue = consume_transport(payload, transport)
    return (
        _load_behavior_handoff()(
            "UserPromptSubmit",
            transport,
            receipt,
            skill_consumer=consume_transport,
        ),
        should_continue,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt, should_continue = _record(payload)
    except Exception as exc:  # noqa: BLE001 - visible fail-closed transport gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "HOOK_TRANSPORT_OR_SKILL_PREPARE_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed": True,
            "source_mutation_authorized": False,
            "raw_prompt_stored": False,
            "private_reasoning_stored": False,
            "hook_runtime_role": (
                "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
            ),
        }
        should_continue = False
    if receipt.get("state") in {"NOT_INDEXED", "NOT_GOVERNED"}:
        print("{}")
        return 0
    result: dict[str, Any] = {
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
        _, _, render_notice = _load_runtime()
        message, notice = render_notice(
            display,
            phase="TURN_PREPARE",
            turn_receipt=receipt,
        )
        result["systemMessage"] = message
        result["hookSpecificOutput"]["additionalContext"] += (
            "\nEVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY="
            + json.dumps(notice, sort_keys=True, separators=(",", ":"))
        )
    if not should_continue:
        result["stopReason"] = (
            "Governed Evidence Lane skill-owned PREPARE failed closed before "
            "model reasoning or source mutation."
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
