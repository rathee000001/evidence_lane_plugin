"""Validate the only bounded behavior handoff a Hook may return to Codex."""

from __future__ import annotations

from .errors import LaneError

FORBIDDEN_OUTPUT_KEYS = frozenset({
    "permissionDecision", "permissionDecisionReason", "continue", "decision", "approve", "deny"
})


def verify_capture_output(event: str, output: dict) -> dict:
    hook_output = output.get("hookSpecificOutput") if isinstance(output, dict) else None
    if FORBIDDEN_OUTPUT_KEYS.intersection(output):
        raise LaneError("HOOK_CONTROL_OUTPUT", "Evidence Lane Hooks cannot control the host.")
    if hook_output is not None:
        if not isinstance(hook_output, dict) or FORBIDDEN_OUTPUT_KEYS.intersection(hook_output):
            raise LaneError("HOOK_CONTROL_OUTPUT", "Evidence Lane Hooks cannot return permission or continuation decisions.")
        if event not in {"SessionStart", "UserPromptSubmit"}:
            raise LaneError("HOOK_CONTEXT_EVENT", "This Hook event cannot return additional context.")
        if hook_output.get("hookEventName") != event or not isinstance(hook_output.get("additionalContext"), str):
            raise LaneError("HOOK_CONTEXT_SHAPE", "The Hook context output is not bound to this event.")
    return output


__all__ = ["FORBIDDEN_OUTPUT_KEYS", "verify_capture_output"]
