"""Bounded native Hook input admission and redaction."""

from __future__ import annotations

import json
from uuid import uuid4

from pydantic import ValidationError

from .capture_routing import HookEnvelope, normalize_hook, seal_observer_identity
from .errors import LaneError
from .redaction import redact

MAX_HOOK_INPUT_BYTES = 262_144
HOOK_COMMON_FIELDS = frozenset({"hook_event_name", "session_id", "turn_id", "model"})
HOOK_EVENT_FIELDS = {
    "SessionStart": {"source"},
    "SubagentStart": {"agent_id", "agent_type"},
    "SubagentStop": {"agent_id", "agent_type"},
    "SessionEnd": {"reason"},
    "UserPromptSubmit": {"prompt"},
    "PreToolUse": {"tool_name", "tool_use_id", "tool_input"},
    "PermissionRequest": {"tool_name", "tool_input"},
    "PostToolUse": {"tool_name", "tool_use_id", "tool_input", "tool_response"},
    "PreCompact": {"trigger"},
    "PostCompact": {"trigger"},
    "Stop": {"last_assistant_message", "stop_hook_active"},
    "Interrupt": set(),
}
SUBAGENT_EVENTS = frozenset({"SubagentStart", "SubagentStop"})


def admit_hook(raw: bytes, expected_event: str) -> HookEnvelope:
    if expected_event not in HOOK_EVENT_FIELDS:
        raise LaneError("HOOK_EVENT_UNSUPPORTED", "Select a documented packaged Hook event.")
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise LaneError("HOOK_INPUT_BUDGET", "The native Hook payload exceeds its capture budget.")
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get("hook_event_name") != expected_event:
            raise ValueError
        allowed = HOOK_COMMON_FIELDS | HOOK_EVENT_FIELDS[expected_event]
        selected = {key: value[key] for key in allowed if key in value}
        if expected_event in SUBAGENT_EVENTS:
            selected = seal_observer_identity(selected)
        envelope = HookEnvelope(event_id=str(uuid4()), event=redact(selected))
        normalize_hook(envelope)
        return envelope
    except (ValueError, KeyError, TypeError, ValidationError, RecursionError):
        raise LaneError(
            "HOOK_INPUT_INVALID",
            "The native Hook input does not match this event contract.",
        ) from None


__all__ = ["HOOK_COMMON_FIELDS", "HOOK_EVENT_FIELDS", "MAX_HOOK_INPUT_BYTES", "admit_hook"]
