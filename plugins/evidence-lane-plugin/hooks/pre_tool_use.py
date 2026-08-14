"""Thin PreToolUse transport adapter for the lifecycle skill."""

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
    from evidence_lane_plugin.hook_skill_runtime import consume_pre_tool_transport

    return build_hook_transport_envelope, consume_pre_tool_transport


def _guard(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    build_transport, consume_transport = _load_runtime()
    transport = build_transport("PreToolUse", payload)
    return consume_transport(payload, transport)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        receipt, should_continue = _guard(payload)
    except Exception as exc:  # noqa: BLE001 - visible fail-closed transport gap
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "HOOK_TRANSPORT_OR_SKILL_TOOL_GUARD_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "source_mutation_authorized": False,
            "raw_tool_payload_stored": False,
            "private_reasoning_stored": False,
            "hook_runtime_role": (
                "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
            ),
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
            "Governed Evidence Lane skill-owned PREPARE or exact binding was "
            "absent before tool use."
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
