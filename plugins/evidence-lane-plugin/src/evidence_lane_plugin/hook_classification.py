"""Classify admitted native Hook events before event-specific handling."""

from __future__ import annotations

from dataclasses import dataclass

from .capture_routing import HOOK_EVENT_ORDER, HookEnvelope
from .errors import LaneError

EVENT_CLASSES = {
    "SessionStart": "session_boundary",
    "SessionEnd": "session_boundary",
    "UserPromptSubmit": "prompt_capture",
    "PreToolUse": "tool_boundary",
    "PermissionRequest": "tool_boundary",
    "PostToolUse": "tool_boundary",
    "PreCompact": "context_boundary",
    "PostCompact": "context_boundary",
    "SubagentStart": "observer_boundary",
    "SubagentStop": "observer_boundary",
    "Stop": "response_boundary",
    "Interrupt": "response_boundary",
}


@dataclass(frozen=True)
class HookClassification:
    event: str
    event_class: str
    ordinal: int
    context_output_allowed: bool
    terminal_timeout: bool


def classify_hook(envelope: HookEnvelope, expected_event: str) -> HookClassification:
    observed = envelope.event.get("hook_event_name")
    if observed != expected_event or expected_event not in EVENT_CLASSES:
        raise LaneError("HOOK_CLASSIFICATION_INVALID", "The admitted Hook event cannot be classified for this handler.")
    return HookClassification(
        event=expected_event,
        event_class=EVENT_CLASSES[expected_event],
        ordinal=HOOK_EVENT_ORDER.index(expected_event) + 1,
        context_output_allowed=expected_event in {"SessionStart", "UserPromptSubmit"},
        terminal_timeout=expected_event in {"Interrupt", "SessionEnd"},
    )


__all__ = ["EVENT_CLASSES", "HookClassification", "classify_hook"]
