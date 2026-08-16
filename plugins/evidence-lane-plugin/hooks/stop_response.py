"""Thin Stop transport adapter for the lifecycle skill-owned COMMIT."""

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
        consume_stop_transport,
    )

    return (
        build_hook_transport_envelope,
        consume_stop_transport,
    )


def _load_behavior_handoff():
    hook_root = Path(__file__).resolve().parent
    if str(hook_root) not in sys.path:
        sys.path.insert(0, str(hook_root))
    from behavior_handoff import attach_consumed_behavior_handoff

    return attach_consumed_behavior_handoff


def _load_stop_output():
    hook_root = Path(__file__).resolve().parent
    if str(hook_root) not in sys.path:
        sys.path.insert(0, str(hook_root))
    from event_isolation import stop_output

    return stop_output


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    build_transport, consume_transport = _load_runtime()
    transport = build_transport("Stop", payload)
    receipt = consume_transport(payload, transport)
    return _load_behavior_handoff()(
        "Stop",
        transport,
        receipt,
        skill_consumer=consume_transport,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    handoff_failed = False
    try:
        contract_output = _load_stop_output()()
        receipt = _record(payload)
    except Exception as exc:  # noqa: BLE001 - Stop exposes the gap
        handoff_failed = True
        contract_output = {}
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "TURN_CONTROL_GAP",
            "code": "HOOK_TRANSPORT_OR_SKILL_COMMIT_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "fail_closed": True,
            "automatic_commit_inferred": False,
            "private_reasoning_stored": False,
            "hook_runtime_role": (
                "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
            ),
        }
    # Stop may commit a bounded receipt internally, but its host output must
    # always be inert.  Any non-empty Stop output can block or recursively
    # continue a Codex turn depending on host interpretation.
    del receipt
    print(json.dumps(contract_output, sort_keys=True, separators=(",", ":")))
    return 1 if handoff_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
