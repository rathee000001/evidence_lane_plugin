"""Declare capture-only lifecycle boundaries for supported Hook events."""

from __future__ import annotations

from .errors import LaneError
from .hook_classification import HookClassification

SESSION_BOUNDARIES = frozenset({"SessionStart", "SessionEnd"})
RESPONSE_BOUNDARIES = frozenset({"Stop", "Interrupt"})


def verify_capture_boundary(classification: HookClassification) -> dict[str, object]:
    if classification.event not in SESSION_BOUNDARIES | RESPONSE_BOUNDARIES | {
        "UserPromptSubmit", "PreToolUse", "PermissionRequest", "PostToolUse",
        "PreCompact", "PostCompact", "SubagentStart", "SubagentStop",
    }:
        raise LaneError("HOOK_BOUNDARY_UNSUPPORTED", "The Hook event has no declared capture boundary.")
    return {
        "event": classification.event,
        "session_boundary": classification.event in SESSION_BOUNDARIES,
        "response_boundary": classification.event in RESPONSE_BOUNDARIES,
        "starts_lifecycle_work": False,
        "changes_goal_or_plan": False,
        "controls_host_or_subagent": False,
    }


__all__ = ["RESPONSE_BOUNDARIES", "SESSION_BOUNDARIES", "verify_capture_boundary"]
