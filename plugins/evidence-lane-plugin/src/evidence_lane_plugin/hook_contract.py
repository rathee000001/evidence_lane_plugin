"""Canonical Codex lifecycle-hook transport contract.

The host emits lifecycle signals.  Hooks validate, redact, bound, and seal
those signals; installed skills own PREPARE, native Evidence Lane reads,
classification, Plan refresh, Goal behavior, and HIL sequencing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .redaction import contains_secret, redact

HOOK_CONTRACT_SCHEMA = "evidence-lane.codex-hook-lifecycle-contract.v1"
HOOK_TRANSPORT_SCHEMA = "evidence-lane.codex-hook-transport-envelope.v1"
HOOK_CAPABILITY_SCHEMA = "evidence-lane.codex-hook-capability-receipt.v1"
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
        "UserPromptSubmit",
        2,
        "VISIBLE_INPUT_SIGNAL",
        "SKILL_PREPARE_THEN_NATIVE_READ_SEQUENCE",
        "REQUIRED_WHEN_HOST_EMITS",
        "prompt_submit.py",
    ),
    HookEventContract(
        "PreToolUse",
        3,
        "PROSPECTIVE_TOOL_SIGNAL",
        "SKILL_BOUNDARY_AND_POLICY_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "pre_tool_use.py",
    ),
    HookEventContract(
        "PostToolUse",
        4,
        "VISIBLE_TOOL_RESULT_SIGNAL",
        "SKILL_RECEIPT_AND_PLAN_REFRESH_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "post_tool_use.py",
    ),
    HookEventContract(
        "PreCompact",
        5,
        "COMPACTION_SEAL_SIGNAL",
        "SKILL_CONTINUITY_SEAL_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "lifecycle_boundary.py",
    ),
    HookEventContract(
        "PostCompact",
        6,
        "COMPACTION_REENTRY_SIGNAL",
        "SKILL_REBIND_AND_FULL_PLAN_REENTRY_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "lifecycle_boundary.py",
    ),
    HookEventContract(
        "Stop",
        7,
        "VISIBLE_RESPONSE_STOP_SIGNAL",
        "SKILL_IDEMPOTENT_COMMIT_OWNER",
        "REQUIRED_WHEN_HOST_EMITS",
        "stop_response.py",
    ),
    HookEventContract(
        "SessionEnd",
        8,
        "SESSION_END_SIGNAL",
        "SKILL_BEST_EFFORT_BOUNDARY_FLUSH_OWNER",
        "BEST_EFFORT_HOST_CAPABILITY_GATED",
        "lifecycle_boundary.py",
    ),
)

HOOK_EVENT_NAMES = tuple(row.event_name for row in HOOK_EVENTS)
_HOOK_EVENT_BY_NAME = {row.event_name: row for row in HOOK_EVENTS}
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
    """Return the immutable package contract for the eight lifecycle signals."""

    body: dict[str, Any] = {
        "schema": HOOK_CONTRACT_SCHEMA,
        "version": HOOK_CONTRACT_VERSION,
        "events": [asdict(row) for row in HOOK_EVENTS],
        "event_order": list(HOOK_EVENT_NAMES),
        "registered_event_count": len(HOOK_EVENTS),
        "hook_owner": "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY",
        "skill_owner": (
            "ENTRY_PREPARE_TOOL_BOUNDARIES_COMPACTION_COMMIT_NATIVE_READS_"
            "CLASSIFICATION_PLAN_REFRESH_GOAL_AND_HIL"
        ),
        "skill_runtime_consumer": (
            "evidence_lane_plugin.hook_skill_runtime"
        ),
        "windows_interpreter_resolution": "SEALED_DERIVED_RUNTIME_ONLY",
        "windows_process_window_mode": "HIDDEN",
        "windows_path_lookup_allowed": False,
        "session_end_host_timeout_seconds": 3,
        "permission_request_policy": (
            "CONDITIONAL_ONLY_AFTER_EXPLICIT_HOST_CAPABILITY_PROOF"
        ),
        "subagent_events_in_scope": False,
        "max_transport_bytes": MAX_HOOK_TRANSPORT_BYTES,
        "max_visible_input_chars": MAX_VISIBLE_INPUT_CHARS,
        "full_plan_allowed_in_hook_payload": False,
        "linked_delta_json_allowed_in_hook_payload": False,
        "private_reasoning_allowed": False,
    }
    body["contract_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def validate_hook_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Validate exact event order, one handler per event, and forbidden surfaces."""

    hooks = configuration.get("hooks")
    if not isinstance(hooks, Mapping):
        raise HookContractError("HOOK_CONFIGURATION_MAP_REQUIRED")
    event_names = tuple(str(name) for name in hooks)
    if event_names != HOOK_EVENT_NAMES:
        raise HookContractError("HOOK_EVENT_ORDER_OR_INVENTORY_MISMATCH")
    if "PermissionRequest" in hooks:
        raise HookContractError("PERMISSION_REQUEST_CAPABILITY_NOT_PROVEN")
    if any("subagent" in name.casefold() for name in event_names):
        raise HookContractError("SUBAGENT_HOOK_EVENT_OUT_OF_SCOPE")

    handler_records: list[dict[str, Any]] = []
    for contract in HOOK_EVENTS:
        groups = hooks.get(contract.event_name)
        if not isinstance(groups, list) or len(groups) != 1:
            raise HookContractError("ONE_HOOK_GROUP_PER_EVENT_REQUIRED")
        group = groups[0]
        handlers = group.get("hooks") if isinstance(group, Mapping) else None
        if not isinstance(handlers, list) or len(handlers) != 1:
            raise HookContractError("ONE_HANDLER_PER_EVENT_REQUIRED")
        handler = handlers[0]
        if not isinstance(handler, Mapping) or handler.get("type") != "command":
            raise HookContractError("COMMAND_HANDLER_REQUIRED")
        command = str(handler.get("command") or "")
        command_windows = str(handler.get("commandWindows") or "")
        if contract.handler not in command or contract.handler not in command_windows:
            raise HookContractError("HOOK_HANDLER_IDENTITY_MISMATCH")
        if (
            "hooks/invoke_hook.py" not in command
            or f"--event {contract.event_name}" not in command
            or "hooks\\invoke_hook.ps1" not in command_windows
            or contract.event_name not in command_windows
            or "-WindowStyle Hidden" not in command_windows
            or not command_windows.startswith(
                '& "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
            )
            or '"${PLUGIN_ROOT}\\hooks\\invoke_hook.ps1"' not in command_windows
            or "%SystemRoot%" in command_windows
            or "%PLUGIN_ROOT%" in command_windows
            or command_windows.casefold().startswith("python ")
        ):
            raise HookContractError("HOOK_DETERMINISTIC_HIDDEN_LAUNCHER_REQUIRED")
        expected_timeout = 3 if contract.event_name == "SessionEnd" else 10
        if handler.get("timeout") != expected_timeout:
            raise HookContractError("HOOK_HOST_TIMEOUT_MISMATCH")
        handler_records.append(
            {
                "event_name": contract.event_name,
                "handler": contract.handler,
                "timeout": expected_timeout,
                "windows_launcher": "invoke_hook.ps1",
                "windows_process_window_mode": "HIDDEN",
                "windows_interpreter_resolution": (
                    "SEALED_DERIVED_RUNTIME_ONLY"
                ),
            }
        )

    lifecycle_contract = lifecycle_hook_contract()
    return {
        "status": "PASS",
        "schema": HOOK_CONTRACT_SCHEMA,
        "contract_sha256": lifecycle_contract["contract_sha256"],
        "event_order": list(event_names),
        "handler_count": len(handler_records),
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
    permission_request_supported: bool = False,
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
                "HOST_CAPABILITY_AVAILABLE_NOT_REGISTERED"
                if permission_request_supported
                else "HOST_CAPABILITY_UNAVAILABLE"
            ),
            "registration_requires_explicit_contract_change": True,
        },
        "subagent_events_in_scope": False,
        "unsupported_events_relabelled_as_success": False,
    }
    body["capability_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body
