"""Shared output-inert adapter for optional Codex lifecycle observations."""

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
        consume_optional_observer_transport,
    )

    return build_hook_transport_envelope, consume_optional_observer_transport


def _load_behavior_handoff():
    hook_root = Path(__file__).resolve().parent
    if str(hook_root) not in sys.path:
        sys.path.insert(0, str(hook_root))
    from behavior_handoff import attach_consumed_behavior_handoff

    return attach_consumed_behavior_handoff


def run(event_name: str) -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        build_transport, consume_transport = _load_runtime()
        transport = build_transport(event_name, payload)
        receipt = consume_transport(payload, event_name, transport)
        receipt = _load_behavior_handoff()(
            event_name,
            transport,
            receipt,
            skill_consumer=consume_transport,
        )
    except Exception as exc:  # noqa: BLE001 - bounded observation gap only
        receipt = {
            "schema": "evidence-lane.codex-turn-control-gap.v1",
            "state": "OPTIONAL_EVENT_OBSERVATION_GAP",
            "event_name": event_name,
            "error_type": type(exc).__name__,
            "host_control_emitted": False,
            "source_mutation_authorized": False,
            "raw_payload_stored": False,
            "private_reasoning_stored": False,
        }
    if receipt.get("state") == "NOT_GOVERNED":
        print("{}")
        return 0
    result: dict[str, Any] = {
        "systemMessage": (
            "EVIDENCE_LANE_OPTIONAL_EVENT_OBSERVATION="
            + json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        )
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0
