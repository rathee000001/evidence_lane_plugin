"""Canonical Codex lifecycle-hook transport contract.

The host emits lifecycle signals.  Hooks validate, redact, bound, and seal
those signals; installed skills own PREPARE, native Evidence Lane reads,
classification, Plan refresh, Goal behavior, and HIL sequencing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .package_root import resolve_plugin_root
from .redaction import contains_secret, redact

HOOK_CONTRACT_SCHEMA = "evidence-lane.codex-hook-lifecycle-contract.v1"
HOOK_TRANSPORT_SCHEMA = "evidence-lane.codex-hook-transport-envelope.v1"
HOOK_CAPABILITY_SCHEMA = "evidence-lane.codex-hook-capability-receipt.v1"
HOOK_LOGICAL_ACTION_REGISTRY_SCHEMA = (
    "evidence-lane.hook-logical-action-registry.v1"
)
HOOK_LAUNCH_DIAGNOSTIC_SCHEMA = (
    "evidence-lane.codex-hook-launch-diagnostic.v1"
)
HOOK_CONTRACT_VERSION = 1
MAX_HOOK_TRANSPORT_BYTES = 65_536
MAX_VISIBLE_INPUT_CHARS = 32_768


class HookContractError(ValueError):
    """Raised when a lifecycle hook signal violates the sealed contract."""


@dataclass(frozen=True)
class HookEventContract:
    event_name: str
    ordinal: int
    hook_transport_phase: str
    skill_action_owner: str
    delivery: str
    handler: str


HOOK_EVENTS: tuple[HookEventContract, ...] = (
    HookEventContract(
        "SessionStart",
        1,
        "HOST_ENTRY_SIGNAL",
        "SKILL_BOOT_RESUME_OR_PANEL_REENTRY",
        "REQUIRED_WHEN_HOST_EMITS",
        "session_start.py",
    ),
    HookEventContract(
        "SubagentStart",
        2,
        "SUBAGENT_ENTRY_SIGNAL",
        "SKILL_BOUND_OBSERVATION_ONLY",
        "OPTIONAL_WHEN_HOST_EMITS",
        "subagent_start.py",
    ),
    HookEventContract(
        "UserPromptSubmit",
        3,
        "VISIBLE_INPUT_SIGNAL",
        "SKILL_PREPARE_THEN_NATIVE_READ_SEQUENCE",
        "REQUIRED_WHEN_HOST_EMITS",
        "prompt_submit.py",
    ),
    HookEventContract(
        "PreToolUse",
        4,
        "PROSPECTIVE_TOOL_SIGNAL",
        "SKILL_BOUNDARY_AND_POLICY_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "pre_tool_use.py",
    ),
    HookEventContract(
        "PermissionRequest",
        5,
        "PERMISSION_OBSERVATION_SIGNAL",
        "SKILL_BOUND_OBSERVATION_ONLY",
        "OPTIONAL_WHEN_HOST_EMITS",
        "permission_request.py",
    ),
    HookEventContract(
        "PostToolUse",
        6,
        "VISIBLE_TOOL_RESULT_SIGNAL",
        "SKILL_RECEIPT_AND_PLAN_REFRESH_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "post_tool_use.py",
    ),
    HookEventContract(
        "PreCompact",
        7,
        "COMPACTION_SEAL_SIGNAL",
        "SKILL_CONTINUITY_SEAL_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "lifecycle_boundary.py",
    ),
    HookEventContract(
        "PostCompact",
        8,
        "COMPACTION_REENTRY_SIGNAL",
        "SKILL_REBIND_AND_FULL_PLAN_REENTRY_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "lifecycle_boundary.py",
    ),
    HookEventContract(
        "SubagentStop",
        9,
        "SUBAGENT_EXIT_SIGNAL",
        "SKILL_BOUND_OBSERVATION_ONLY",
        "OPTIONAL_WHEN_HOST_EMITS",
        "subagent_stop.py",
    ),
    HookEventContract(
        "Stop",
        10,
        "VISIBLE_RESPONSE_STOP_SIGNAL",
        "SKILL_IDEMPOTENT_COMMIT_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "stop_response.py",
    ),
    HookEventContract(
        "SessionEnd",
        11,
        "SESSION_END_SIGNAL",
        "SKILL_BEST_EFFORT_BOUNDARY_FLUSH_OWNER",
        "BEST_EFFORT_HOST_CAPABILITY_GATED",
        "lifecycle_boundary.py",
    ),
)

HOOK_EVENT_NAMES = tuple(row.event_name for row in HOOK_EVENTS)
_HOOK_EVENT_BY_NAME = {row.event_name: row for row in HOOK_EVENTS}
HOOK_EVENT_ACTION_HANDLERS = {
    row.event_name: (
        "subhook_validate.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_emit.py",
    )
    for row in HOOK_EVENTS
}
_COMMON_LOGICAL_ACTIONS = (
    "VALIDATE_REDACT_AND_BOUND_VISIBLE_SIGNAL",
    "DEDUPE_AND_SEAL_EVENT",
)
HOOK_EVENT_LOGICAL_ACTIONS = {
    "SessionStart": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_SESSION_ENTRY_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "SubagentStart": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_SUBAGENT_START_OBSERVATION",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "UserPromptSubmit": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_VISIBLE_PROMPT_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "PreToolUse": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_PROSPECTIVE_TOOL_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "PermissionRequest": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_PERMISSION_OBSERVATION",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "PostToolUse": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_VISIBLE_TOOL_RESULT_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "PreCompact": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_PRECOMPACT_BOUNDARY_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "PostCompact": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_POSTCOMPACT_REENTRY_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "SubagentStop": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_SUBAGENT_STOP_OBSERVATION",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "Stop": (
        *_COMMON_LOGICAL_ACTIONS,
        "TRANSPORT_VISIBLE_RESPONSE_STOP_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
    "SessionEnd": (
        *_COMMON_LOGICAL_ACTIONS,
        "BEST_EFFORT_TRANSPORT_SESSION_END_SIGNAL",
        "VERIFY_AND_EMIT_EVENT_RESULT",
    ),
}


def load_hook_logical_action_registry(
    plugin_root: Path | None = None,
) -> dict[str, tuple[str, ...]]:
    """Load the internal logical-action registry kept outside host hooks.json.

    Codex owns the native hooks.json schema and currently accepts only the
    description and hooks fields.  Evidence Lane's numbered SDK sub-actions
    therefore live in a package-owned companion registry that the host never
    parses as hook configuration.
    """

    root = plugin_root or resolve_plugin_root(__file__)
    path = root / "hooks" / "logical-actions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HookContractError("HOOK_LOGICAL_ACTION_REGISTRY_REQUIRED") from exc
    if (
        set(payload) != {"schema", "logicalActions"}
        or payload.get("schema") != HOOK_LOGICAL_ACTION_REGISTRY_SCHEMA
    ):
        raise HookContractError("HOOK_LOGICAL_ACTION_REGISTRY_SCHEMA_MISMATCH")
    actions = payload.get("logicalActions")
    if not isinstance(actions, Mapping):
        raise HookContractError("HOOK_LOGICAL_ACTION_REGISTRY_REQUIRED")
    normalized = {
        str(name): tuple(str(action) for action in values)
        for name, values in actions.items()
        if isinstance(values, list)
    }
    if tuple(normalized) != HOOK_EVENT_NAMES:
        raise HookContractError("HOOK_LOGICAL_ACTION_EVENT_ORDER_MISMATCH")
    if normalized != HOOK_EVENT_LOGICAL_ACTIONS:
        raise HookContractError("HOOK_LOGICAL_ACTION_REGISTRY_MISMATCH")
    return normalized
_FORBIDDEN_PAYLOAD_KEYS = {
    "chain_of_thought",
    "credentials",
    "delta_json",
    "environment",
    "full_plan",
    "hidden_reasoning",
    "plan_rows",
    "private_reasoning",
    "raw_credentials",
}


def lifecycle_hook_contract() -> dict[str, Any]:
    """Return the immutable package contract for the current lifecycle registry."""

    body: dict[str, Any] = {
        "schema": HOOK_CONTRACT_SCHEMA,
        "version": HOOK_CONTRACT_VERSION,
        "events": [
            {
                **asdict(row),
                "logical_action_count": len(
                    HOOK_EVENT_LOGICAL_ACTIONS[row.event_name]
                ),
                "logical_actions": [
                    {
                        "logical_action_number": f"{row.ordinal}.L{action_ordinal}",
                        "event_logical_action_ordinal": action_ordinal,
                        "action": action,
                        "owner": "HOOK_TRANSPORT_ADAPTER",
                        "project_plan_goal_hil_effect": "NONE",
                    }
                    for action_ordinal, action in enumerate(
                        HOOK_EVENT_LOGICAL_ACTIONS[row.event_name], start=1
                    )
                ],
            }
            for row in HOOK_EVENTS
        ],
        "event_order": list(HOOK_EVENT_NAMES),
        "registered_event_count": len(HOOK_EVENTS),
        "event_numbering": "HOOK_1_THROUGH_HOOK_11",
        "nested_action_numbering": "HOOK_EVENT_ORDINAL.ACTION_ORDINAL",
        "handler_cardinality_per_event": "ONE_OR_MORE",
        "hook_count_semantics": "REGISTERED_EVENT_TYPE_COUNT",
        "handler_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "logical_action_count": sum(
            len(actions) for actions in HOOK_EVENT_LOGICAL_ACTIONS.values()
        ),
        "logical_action_count_semantics": (
            "NUMBERED_SERIAL_TRANSPORT_STEPS_INSIDE_HANDLER_ACTIONS"
        ),
        "hook_owner": "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY",
        "skill_owner": (
            "ENTRY_PREPARE_TOOL_BOUNDARIES_COMPACTION_COMMIT_NATIVE_READS_"
            "CLASSIFICATION_PLAN_REFRESH_GOAL_AND_HIL"
        ),
        "skill_runtime_consumer": (
            "evidence_lane_plugin.hook_skill_runtime"
        ),
        "windows_interpreter_resolution": "SEALED_DERIVED_RUNTIME_ONLY",
        "windows_process_window_mode": "HOST_MANAGED_NO_CHILD_WINDOW",
        "windows_command_launcher": "EvidenceLaneHookHost.exe",
        "windows_child_create_no_window": True,
        "windows_path_lookup_allowed": False,
        "session_end_host_timeout_seconds": 3,
        "permission_request_policy": "OBSERVE_ONLY_NEVER_GRANT_OR_DENY",
        "subagent_events_in_scope": True,
        "subagent_event_policy": "BOUND_OBSERVATION_ONLY_NEVER_CONTROL",
        "max_transport_bytes": MAX_HOOK_TRANSPORT_BYTES,
        "max_visible_input_chars": MAX_VISIBLE_INPUT_CHARS,
        "full_plan_allowed_in_hook_payload": False,
        "linked_delta_json_allowed_in_hook_payload": False,
        "private_reasoning_allowed": False,
    }
    body["contract_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def validate_hook_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact event order and one-or-more numbered actions per event."""

    if set(configuration) != {"description", "hooks"}:
        raise HookContractError("HOOK_HOST_NATIVE_SCHEMA_MISMATCH")
    hooks = configuration.get("hooks")
    if not isinstance(hooks, Mapping):
        raise HookContractError("HOOK_CONFIGURATION_MAP_REQUIRED")
    event_names = tuple(str(name) for name in hooks)
    if event_names != HOOK_EVENT_NAMES:
        raise HookContractError("HOOK_EVENT_ORDER_OR_INVENTORY_MISMATCH")
    load_hook_logical_action_registry()
    handler_records: list[dict[str, Any]] = []
    for contract in HOOK_EVENTS:
        groups = hooks.get(contract.event_name)
        if not isinstance(groups, list) or not groups:
            raise HookContractError("ONE_OR_MORE_HOOK_GROUPS_PER_EVENT_REQUIRED")
        event_handlers: list[str] = []
        for group_ordinal, group in enumerate(groups, start=1):
            handlers = group.get("hooks") if isinstance(group, Mapping) else None
            if not isinstance(handlers, list) or not handlers:
                raise HookContractError("ONE_OR_MORE_HANDLER_ACTIONS_REQUIRED")
            for group_action_ordinal, handler in enumerate(handlers, start=1):
                if not isinstance(handler, Mapping) or handler.get("type") != "command":
                    raise HookContractError("COMMAND_HANDLER_REQUIRED")
                command = str(handler.get("command") or "")
                command_windows = str(handler.get("commandWindows") or "")
                handler_match = re.search(
                    r"--handler\s+([A-Za-z0-9_.-]+\.py)(?:\s|$)", command
                )
                handler_name = handler_match.group(1) if handler_match else ""
                if not handler_name or handler_name not in command_windows:
                    raise HookContractError("HOOK_HANDLER_IDENTITY_MISMATCH")
                allowed_handlers = HOOK_EVENT_ACTION_HANDLERS[contract.event_name]
                if not event_handlers and handler_name != allowed_handlers[0]:
                    raise HookContractError("HOOK_PRIMARY_HANDLER_IDENTITY_MISMATCH")
                if handler_name not in allowed_handlers:
                    raise HookContractError("HOOK_HANDLER_NOT_IN_EVENT_ACTION_REGISTRY")
                if (
                    "hooks/invoke_hook.py" not in command
                    or f"--event {contract.event_name}" not in command
                    or "hooks\\EvidenceLaneHookHost.exe" not in command_windows
                    or contract.event_name not in command_windows
                    or not command_windows.startswith(
                        '& "${PLUGIN_ROOT}\\hooks\\EvidenceLaneHookHost.exe" '
                    )
                    or "powershell.exe" in command_windows.casefold()
                    or "invoke_hook.ps1" in command_windows.casefold()
                    or "%SystemRoot%" in command_windows
                    or "%PLUGIN_ROOT%" in command_windows
                    or command_windows.casefold().startswith("python ")
                ):
                    raise HookContractError(
                        "HOOK_DETERMINISTIC_HIDDEN_LAUNCHER_REQUIRED"
                    )
                expected_timeout = 3 if contract.event_name == "SessionEnd" else 10
                if handler.get("timeout") != expected_timeout:
                    raise HookContractError("HOOK_HOST_TIMEOUT_MISMATCH")
                event_handlers.append(handler_name)
                action_ordinal = len(event_handlers)
                handler_records.append(
                    {
                        "hook_number": contract.ordinal,
                        "action_number": f"{contract.ordinal}.{action_ordinal}",
                        "event_name": contract.event_name,
                        "event_action_ordinal": action_ordinal,
                        "group_ordinal": group_ordinal,
                        "group_action_ordinal": group_action_ordinal,
                        "handler": handler_name,
                        "timeout": expected_timeout,
                        "windows_launcher": "EvidenceLaneHookHost.exe",
                        "windows_process_window_mode": "HOST_MANAGED_NO_CHILD_WINDOW",
                        "windows_child_create_no_window": True,
                        "windows_interpreter_resolution": (
                            "SEALED_DERIVED_RUNTIME_ONLY"
                        ),
                    }
                )
        if len(event_handlers) != len(set(event_handlers)):
            raise HookContractError("HOOK_EVENT_HANDLER_ACTION_DUPLICATE")

    lifecycle_contract = lifecycle_hook_contract()
    return {
        "status": "PASS",
        "schema": HOOK_CONTRACT_SCHEMA,
        "contract_sha256": lifecycle_contract["contract_sha256"],
        "event_order": list(event_names),
        "registered_event_count": len(event_names),
        "handler_count": len(handler_records),
        "handler_count_semantics": "TOTAL_NESTED_HANDLER_ACTION_COUNT",
        "event_action_counts": {
            event_name: sum(
                row["event_name"] == event_name for row in handler_records
            )
            for event_name in event_names
        },
        "logical_action_count": lifecycle_contract["logical_action_count"],
        "logical_action_count_semantics": lifecycle_contract[
            "logical_action_count_semantics"
        ],
        "logical_action_inventory": [
            {
                "hook_number": row["ordinal"],
                "event_name": row["event_name"],
                "logical_action_count": row["logical_action_count"],
                "logical_actions": row["logical_actions"],
            }
            for row in lifecycle_contract["events"]
        ],
        "handler_records": handler_records,
        "configuration_sha256": sha256_bytes(
            canonical_json_bytes(dict(configuration))
        ),
    }


def _bounded_visible_input(payload: Mapping[str, Any]) -> str | None:
    for key in ("prompt", "visible_input", "user_prompt"):
        if key not in payload:
            continue
        value = str(redact(str(payload.get(key) or "")))
        if len(value) > MAX_VISIBLE_INPUT_CHARS:
            raise HookContractError("HOOK_VISIBLE_INPUT_BOUND_EXCEEDED")
        if contains_secret(value):
            raise HookContractError("HOOK_VISIBLE_INPUT_REDACTION_FAILED")
        return value
    return None


def build_hook_transport_envelope(
    event_name: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one deterministic, bounded, privacy-safe lifecycle envelope.

    The envelope intentionally contains no behavioral result.  It is the input
    receipt consumed by the installed skill that owns the governed action.
    """

    contract = _HOOK_EVENT_BY_NAME.get(str(event_name))
    if contract is None:
        raise HookContractError("HOOK_EVENT_UNSUPPORTED")
    if not isinstance(payload, Mapping):
        raise HookContractError("HOOK_PAYLOAD_MAP_REQUIRED")
    if any(str(key).casefold() in _FORBIDDEN_PAYLOAD_KEYS for key in payload):
        raise HookContractError("HOOK_FORBIDDEN_PAYLOAD_FIELD")

    visible_input = _bounded_visible_input(payload)
    occurrence_source = str(
        payload.get("hook_event_id")
        or payload.get("event_id")
        or payload.get("turn_id")
        or payload.get("tool_use_id")
        or payload.get("source")
        or contract.event_name
    )
    safe_payload: dict[str, Any] = {
        "source": str(redact(str(payload.get("source") or ""))) or None,
        "permission_mode": (
            str(redact(str(payload.get("permission_mode") or ""))) or None
        ),
        "model": str(redact(str(payload.get("model") or ""))) or None,
        "tool_name": str(redact(str(payload.get("tool_name") or ""))) or None,
        "agent_type": str(redact(str(payload.get("agent_type") or ""))) or None,
        "agent_id_sha256": sha256_bytes(
            str(payload.get("agent_id") or "").encode("utf-8")
        ),
        "visible_input_after_redaction": visible_input,
        "visible_input_sha256": (
            sha256_bytes(visible_input.encode("utf-8"))
            if visible_input is not None
            else None
        ),
        "host_session_id_sha256": sha256_bytes(
            str(payload.get("session_id") or "").encode("utf-8")
        ),
        "turn_id_sha256": sha256_bytes(
            str(payload.get("turn_id") or "").encode("utf-8")
        ),
        "tool_use_id_sha256": sha256_bytes(
            str(payload.get("tool_use_id") or "").encode("utf-8")
        ),
        "cwd_sha256": sha256_bytes(
            str(payload.get("cwd") or "").encode("utf-8")
        ),
        "transcript_path_sha256": sha256_bytes(
            str(
                payload.get("transcript_path")
                or payload.get("agent_transcript_path")
                or ""
            ).encode("utf-8")
        ),
    }
    body: dict[str, Any] = {
        "schema": HOOK_TRANSPORT_SCHEMA,
        "contract_schema": HOOK_CONTRACT_SCHEMA,
        "contract_version": HOOK_CONTRACT_VERSION,
        "event_name": contract.event_name,
        "event_ordinal": contract.ordinal,
        "hook_transport_phase": contract.hook_transport_phase,
        "delivery": contract.delivery,
        "skill_action_owner": contract.skill_action_owner,
        "occurrence_sha256": sha256_bytes(occurrence_source.encode("utf-8")),
        "safe_payload": safe_payload,
        "hook_behavior_executed": False,
        "native_pv_tool_called": False,
        "classification_performed": False,
        "plan_refreshed": False,
        "goal_mutated": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "source_mutated": False,
        "candidate_created": False,
        "full_plan_included": False,
        "linked_delta_json_included": False,
        "raw_payload_stored": False,
        "raw_secret_stored": False,
        "private_reasoning_stored": False,
    }
    encoded = canonical_json_bytes(body)
    if len(encoded) > MAX_HOOK_TRANSPORT_BYTES:
        raise HookContractError("HOOK_TRANSPORT_BOUND_EXCEEDED")
    body["transport_bytes"] = len(encoded)
    body["transport_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def hook_capability_receipt(
    supported_events: Iterable[str],
    *,
    permission_request_supported: bool | None = None,
) -> dict[str, Any]:
    """Project measured host support without fabricating unavailable events."""

    supported = {str(name) for name in supported_events}
    unknown = supported.difference(HOOK_EVENT_NAMES)
    if unknown:
        raise HookContractError("UNKNOWN_HOST_HOOK_CAPABILITY")
    events = [
        {
            "event_name": row.event_name,
            "state": (
                "HOST_CAPABILITY_AVAILABLE"
                if row.event_name in supported
                else "HOST_CAPABILITY_UNAVAILABLE"
            ),
            "delivery": row.delivery,
            "false_success_claimed": False,
        }
        for row in HOOK_EVENTS
    ]
    body: dict[str, Any] = {
        "schema": HOOK_CAPABILITY_SCHEMA,
        "contract_sha256": lifecycle_hook_contract()["contract_sha256"],
        "events": events,
        "permission_request": {
            "state": (
                "HOST_CAPABILITY_AVAILABLE"
                if "PermissionRequest" in supported
                else "HOST_CAPABILITY_UNAVAILABLE"
            ),
            "caller_capability_hint_matched": (
                permission_request_supported
                is None
                or permission_request_supported
                == ("PermissionRequest" in supported)
            ),
            "control_policy": "OBSERVE_ONLY_NEVER_GRANT_OR_DENY",
        },
        "subagent_events_in_scope": True,
        "unsupported_events_relabelled_as_success": False,
    }
    body["capability_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body
