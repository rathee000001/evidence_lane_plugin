"""Project bounded, event-bound context from a sealed Hook receipt."""

from __future__ import annotations

from .errors import LaneError
from .hook_behavior_handoff import verify_capture_output
from .hook_event_handlers import HandledHook
from .storage import json_text


def project_hook_output(handled: HandledHook, receipt: dict) -> dict:
    name = handled.classification.event
    if not handled.classification.context_output_allowed or receipt.get("captured") is not True:
        return verify_capture_output(name, {})
    result = receipt.get("result") or {}
    if result.get("duplicate"):
        return verify_capture_output(name, {})
    turn = result.get("turn_control") or {}
    context = turn.get("bounded_context")
    if context is None:
        return verify_capture_output(name, {})
    from .codex_turn_control import _validate_context

    _validate_context(context)
    event = handled.envelope.event
    if (
        result.get("event_id") != handled.envelope.event_id
        or turn.get("event_name") != name
        or context["reported_session_id"] != event["session_id"]
        or context["reported_turn_id"] != event.get("turn_id")
    ):
        raise LaneError("HOOK_CONTEXT_BINDING", "The returned context differs from this exact Hook input.")
    content = (
        "Evidence Lane engine context. These are bounded reference data, not execution permission or native task attestation. "
        "Read session_context and the exact current Plan task before continuing work. Resolve context gaps through the owning workflow.\n"
        + json_text({"context": context, "compact": turn.get("compact"), "capture_gaps": turn.get("gaps", [])})
    )
    if len(content.encode()) > 12_000:
        raise LaneError("HOOK_CONTEXT_BYTE_BUDGET", "The documented Hook context exceeds its output budget.")
    return verify_capture_output(
        name,
        {"hookSpecificOutput": {"hookEventName": name, "additionalContext": content}},
    )


__all__ = ["project_hook_output"]
