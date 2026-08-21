"""Fail-closed hook-to-skill behavior handoff receipts.

The hook adapter owns transport only.  After the installed lifecycle skill
consumer returns, the adapter issues and immediately consumes a sealed receipt
that proves which exact transport the skill handled.  This module validates
ownership and binding; it never performs the skill-owned behavior itself.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BEHAVIOR_HANDOFF_SCHEMA = "evidence-lane.codex-hook-behavior-handoff.v1"
BEHAVIOR_HANDOFF_CONSUMER = "EVIDENCE_LANE_HOOK_TRANSPORT_ADAPTER"
SKILL_RUNTIME_OWNER = "INSTALLED_EVIDENCE_LANE_CODE_LIFECYCLE_SKILL"
HOOK_RUNTIME_ROLE = "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"

EVENT_SKILL_ACTIONS = {
    "SessionStart": "SESSION_START_BIND_OR_REENTRY",
    "UserPromptSubmit": "PREPARE",
    "PreToolUse": "PROSPECTIVE_TOOL_BOUNDARY",
    "PostToolUse": "TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
    "PreCompact": "COMPACTION_OR_SESSION_BOUNDARY",
    "PostCompact": "COMPACTION_OR_SESSION_BOUNDARY",
    "Stop": "COMMIT",
}
EVENT_SKILL_CONSUMERS = {
    "SessionStart": "consume_session_start_transport",
    "UserPromptSubmit": "consume_prompt_transport",
    "PreToolUse": "consume_pre_tool_transport",
    "PostToolUse": "consume_post_tool_transport",
    "PreCompact": "consume_boundary_transport",
    "PostCompact": "consume_boundary_transport",
    "Stop": "consume_stop_transport",
}


class BehaviorHandoffError(RuntimeError):
    """A skill-result handoff did not match its exact hook transport."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


def _is_sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9A-F]{64}", value) is not None


def _event_isolation_binding() -> dict[str, Any]:
    names = {
        "correlation_id": "EVIDENCE_LANE_HOOK_EVENT_CORRELATION_ID",
        "owner_sha256": "EVIDENCE_LANE_HOOK_EVENT_OWNER_SHA256",
        "occurrence_input_sha256": "EVIDENCE_LANE_HOOK_EVENT_INPUT_SHA256",
        "policy_sha256": "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256",
    }
    values = {
        key: os.environ.get(env_name, "").strip()
        for key, env_name in names.items()
    }
    present = {key for key, value in values.items() if value}
    if not present:
        return {
            "state": "DIRECT_SOURCE_OR_TEST_ADAPTER",
            "event_isolation_bound": False,
        }
    if present != set(names):
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_ISOLATION_BINDING_PARTIAL",
            "The installed event-isolation binding is incomplete.",
        )
    if (
        re.fullmatch(r"hook_[0-9a-f]{40}", values["correlation_id"]) is None
        or not _is_sha256(values["owner_sha256"].upper())
        or not _is_sha256(values["occurrence_input_sha256"].upper())
        or not _is_sha256(values["policy_sha256"].upper())
    ):
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_ISOLATION_BINDING_INVALID",
            "The installed event-isolation binding is malformed.",
        )
    return {
        "state": "EXACT_EVENT_ISOLATION_BOUND",
        "event_isolation_bound": True,
        "correlation_id": values["correlation_id"],
        "owner_sha256": values["owner_sha256"].upper(),
        "occurrence_input_sha256": values["occurrence_input_sha256"].upper(),
        "policy_sha256": values["policy_sha256"].upper(),
    }


def _verified_skill_consumer(
    event_name: str,
    skill_consumer: object,
) -> dict[str, Any]:
    expected_name = EVENT_SKILL_CONSUMERS.get(event_name)
    if expected_name is None:
        _expected_action(event_name)
        raise AssertionError("unreachable")
    expected_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "evidence_lane_plugin"
        / "hook_skill_runtime.py"
    ).resolve()
    try:
        source_file = (
            inspect.getsourcefile(skill_consumer)
            if callable(skill_consumer)
            else None
        )
    except (TypeError, OSError):
        source_file = None
    actual_path = Path(source_file).resolve() if source_file else None
    if (
        not callable(skill_consumer)
        or getattr(skill_consumer, "__module__", "")
        != "evidence_lane_plugin.hook_skill_runtime"
        or getattr(skill_consumer, "__name__", "") != expected_name
        or actual_path != expected_path
        or not expected_path.is_file()
    ):
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_SKILL_CONSUMER_INVALID",
            "The executed consumer is not the exact installed lifecycle skill route.",
        )
    return {
        "module": "evidence_lane_plugin.hook_skill_runtime",
        "function": expected_name,
        "source_path_role": "PLUGIN_ROOT/src/evidence_lane_plugin/hook_skill_runtime.py",
        "source_sha256": hashlib.sha256(expected_path.read_bytes())
        .hexdigest()
        .upper(),
    }


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_NOT_CANONICAL_JSON",
            "The handoff input is not deterministic JSON.",
        ) from exc


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest().upper()


def _expected_action(event_name: str) -> str:
    try:
        return EVENT_SKILL_ACTIONS[event_name]
    except KeyError as exc:
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_EVENT_UNSUPPORTED",
            "This event has no durable skill-action handoff contract.",
        ) from exc


def _verified_bindings(
    event_name: str,
    transport: Mapping[str, Any],
    skill_receipt: Mapping[str, Any],
) -> tuple[str, str, str]:
    expected_action = _expected_action(event_name)
    transport_receipt_sha256 = str(
        transport.get("transport_receipt_sha256") or ""
    )
    if transport.get("event_name") != event_name or not _is_sha256(
        transport_receipt_sha256.upper()
    ):
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_TRANSPORT_INVALID",
            "The transport event or transport receipt identity is invalid.",
        )
    if dict(skill_receipt.get("hook_transport_envelope") or {}) != dict(
        transport
    ):
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_TRANSPORT_MISMATCH",
            "The skill receipt is not bound to the exact hook transport.",
        )
    checks = {
        "skill_action_owner": (
            skill_receipt.get("skill_action_owner") == SKILL_RUNTIME_OWNER
        ),
        "skill_action": skill_receipt.get("skill_action") == expected_action,
        "skill_action_executed": (
            skill_receipt.get("skill_action_executed") is True
        ),
        "hook_behavior_executed": (
            skill_receipt.get("hook_behavior_executed") is False
        ),
        "hook_runtime_role": (
            skill_receipt.get("hook_runtime_role") == HOOK_RUNTIME_ROLE
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_SKILL_RECEIPT_INVALID",
            "The skill receipt failed fields: " + ",".join(failed),
        )
    state = str(skill_receipt.get("state") or "").strip()
    if not state:
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_RESULT_STATE_MISSING",
            "The executed skill action returned no bounded result state.",
        )
    return expected_action, transport_receipt_sha256, state


def issue_behavior_handoff_receipt(
    event_name: str,
    transport: Mapping[str, Any],
    skill_receipt: Mapping[str, Any],
    *,
    skill_consumer: object,
) -> dict[str, Any]:
    """Issue a sealed receipt only after the owning skill action returned."""

    expected_action, transport_sha256, result_state = _verified_bindings(
        event_name,
        transport,
        skill_receipt,
    )
    consumer_identity = _verified_skill_consumer(event_name, skill_consumer)
    isolation_binding = _event_isolation_binding()
    issued: dict[str, Any] = {
        "schema": BEHAVIOR_HANDOFF_SCHEMA,
        "status": "ISSUED_AFTER_SKILL_ACTION",
        "event_name": event_name,
        "transport_receipt_sha256": transport_sha256,
        "skill_receipt_sha256": _sha256(skill_receipt),
        "skill_action_owner": SKILL_RUNTIME_OWNER,
        "skill_action": expected_action,
        "skill_action_executed": True,
        "skill_consumer_identity": consumer_identity,
        "skill_result_state": result_state,
        "hook_behavior_executed": False,
        "behavior_success_inferred": False,
        "host_memory_imported": False,
        "learning_candidate_created": False,
        "learning_hil_invoked": False,
        "event_isolation_binding": isolation_binding,
        "raw_host_payload_stored": False,
        "private_reasoning_stored": False,
    }
    issued["issued_receipt_sha256"] = _sha256(issued)
    return issued


def consume_behavior_handoff_receipt(
    issued: Mapping[str, Any],
    *,
    event_name: str,
    transport: Mapping[str, Any],
    skill_receipt: Mapping[str, Any],
    skill_consumer: object,
) -> dict[str, Any]:
    """Verify an issued receipt and return its bounded consumed projection."""

    expected = issue_behavior_handoff_receipt(
        event_name,
        transport,
        skill_receipt,
        skill_consumer=skill_consumer,
    )
    if dict(issued) != expected:
        raise BehaviorHandoffError(
            "BEHAVIOR_HANDOFF_ISSUED_RECEIPT_MISMATCH",
            "The issued handoff receipt was changed before consumption.",
        )
    consumed: dict[str, Any] = {
        "schema": BEHAVIOR_HANDOFF_SCHEMA,
        "status": "PASS_CONSUMED",
        "event_name": event_name,
        "transport_receipt_sha256": expected["transport_receipt_sha256"],
        "skill_receipt_sha256": expected["skill_receipt_sha256"],
        "issued_receipt_sha256": expected["issued_receipt_sha256"],
        "skill_action_owner": SKILL_RUNTIME_OWNER,
        "skill_action": expected["skill_action"],
        "skill_action_executed": True,
        "skill_consumer_identity": expected["skill_consumer_identity"],
        "skill_result_state": expected["skill_result_state"],
        "hook_behavior_executed": False,
        "behavior_success_inferred": False,
        "host_memory_imported": False,
        "learning_candidate_created": False,
        "learning_hil_invoked": False,
        "event_isolation_binding": expected["event_isolation_binding"],
        "handoff_consumer": BEHAVIOR_HANDOFF_CONSUMER,
        "raw_host_payload_stored": False,
        "private_reasoning_stored": False,
    }
    consumed["consumed_receipt_sha256"] = _sha256(consumed)
    return consumed


def attach_consumed_behavior_handoff(
    event_name: str,
    transport: Mapping[str, Any],
    skill_receipt: Mapping[str, Any],
    *,
    skill_consumer: object,
) -> dict[str, Any]:
    """Issue, verify, consume, and attach one exact behavior handoff."""

    issued = issue_behavior_handoff_receipt(
        event_name,
        transport,
        skill_receipt,
        skill_consumer=skill_consumer,
    )
    consumed = consume_behavior_handoff_receipt(
        issued,
        event_name=event_name,
        transport=transport,
        skill_receipt=skill_receipt,
        skill_consumer=skill_consumer,
    )
    result = dict(skill_receipt)
    result["behavior_handoff_receipt"] = consumed
    return result
