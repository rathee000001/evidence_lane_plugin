"""Thin compaction/session-boundary transport adapter for the lifecycle skill."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


def _load_transport_builder():
    source_root = Path(__file__).resolve().parents[1] / "src"
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
    from evidence_lane_plugin.hook_contract import build_hook_transport_envelope

    return build_hook_transport_envelope


def _load_runtime():
    build_hook_transport_envelope = _load_transport_builder()
    from evidence_lane_plugin.hook_skill_runtime import consume_boundary_transport

    return build_hook_transport_envelope, consume_boundary_transport


def _record(payload: dict[str, Any], event_name: str) -> tuple[dict[str, Any], bool]:
    if event_name == "SessionEnd":
        # Codex intentionally ignores SessionEnd stdout and caps the hook at
        # three seconds.  Seal the privacy-bounded transport here, but do not
        # import or run the durable skill consumer on this best-effort signal.
        # The host's hook/completed notification is the invocation receipt.
        transport = _load_transport_builder()(event_name, payload)
        return (
            {
                "schema": "evidence-lane.codex-session-end-transport.v1",
                "state": "BEST_EFFORT_TRANSPORT_SEALED",
                "event_name": event_name,
                "transport_receipt_sha256": transport[
                    "transport_receipt_sha256"
                ],
                "host_output_consumed": False,
                "durable_skill_consumer_invoked": False,
                "source_mutation_authorized": False,
                "private_reasoning_stored": False,
                "hook_runtime_role": (
                    "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
                ),
            },
            True,
        )
    build_transport, consume_transport = _load_runtime()
    transport = build_transport(event_name, payload)
    return consume_transport(payload, event_name, transport)


def _bounded_notice(receipt: dict[str, Any], event_name: str) -> str:
    receipt_bytes = json.dumps(
        receipt,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    notice = {
        "event_name": event_name,
        "receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest().upper(),
        "state": str(receipt.get("state") or "RECORDED"),
    }
    return "EVIDENCE_LANE_LIFECYCLE_BOUNDARY=" + json.dumps(
        notice,
        sort_keys=True,
        separators=(",", ":"),
    )


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
            "code": "HOOK_TRANSPORT_OR_SKILL_BOUNDARY_UNAVAILABLE",
            "event_name": event_name,
            "error_type": type(exc).__name__,
            "source_mutation_authorized": False,
            "private_reasoning_stored": False,
            "hook_runtime_role": (
                "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
            ),
        }
        should_continue = event_name == "SessionEnd"
    # SessionEnd output is intentionally ignored by Codex.  The empty object
    # avoids implying that the host consumed a receipt or continuation signal.
    if event_name == "SessionEnd":
        print("{}")
        return 0

    # PreCompact/PostCompact accept only the universal command-output fields;
    # hookSpecificOutput is invalid for both native schemas.
    result: dict[str, Any] = {
        "continue": should_continue,
        "systemMessage": _bounded_notice(receipt, event_name),
    }
    if not should_continue:
        result["stopReason"] = (
            "Governed Evidence Lane skill-owned lifecycle boundary failed closed."
        )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
