"""Explicit event-specific native Hook handler classes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .capture_routing import HookEnvelope
from .errors import LaneError
from .hook_classification import HookClassification


@dataclass(frozen=True)
class HandledHook:
    envelope: HookEnvelope
    classification: HookClassification
    handler_id: str
    capture_only: bool = True


class NativeHookHandler:
    event_name: ClassVar[str]
    handler_id: ClassVar[str]

    @classmethod
    def handle(cls, envelope: HookEnvelope, classification: HookClassification) -> HandledHook:
        if classification.event != cls.event_name or envelope.event.get("hook_event_name") != cls.event_name:
            raise LaneError("HOOK_HANDLER_BINDING", "The event-specific Hook handler received another event.")
        return HandledHook(envelope=envelope, classification=classification, handler_id=cls.handler_id)


class SessionStartHandler(NativeHookHandler):
    event_name = "SessionStart"; handler_id = "session-start.capture"


class SubagentStartHandler(NativeHookHandler):
    event_name = "SubagentStart"; handler_id = "subagent-start.observe"


class UserPromptSubmitHandler(NativeHookHandler):
    event_name = "UserPromptSubmit"; handler_id = "user-prompt.capture"


class PreToolUseHandler(NativeHookHandler):
    event_name = "PreToolUse"; handler_id = "pre-tool.capture"


class PermissionRequestHandler(NativeHookHandler):
    event_name = "PermissionRequest"; handler_id = "permission-request.capture"


class PostToolUseHandler(NativeHookHandler):
    event_name = "PostToolUse"; handler_id = "post-tool.capture"


class PreCompactHandler(NativeHookHandler):
    event_name = "PreCompact"; handler_id = "pre-compact.capture"


class PostCompactHandler(NativeHookHandler):
    event_name = "PostCompact"; handler_id = "post-compact.capture"


class SubagentStopHandler(NativeHookHandler):
    event_name = "SubagentStop"; handler_id = "subagent-stop.observe"


class StopHandler(NativeHookHandler):
    event_name = "Stop"; handler_id = "stop.capture"


class SessionEndHandler(NativeHookHandler):
    event_name = "SessionEnd"; handler_id = "session-end.capture"


class InterruptHandler(NativeHookHandler):
    event_name = "Interrupt"; handler_id = "interrupt.capture"


HOOK_HANDLER_CLASSES = (
    SessionStartHandler, SubagentStartHandler, UserPromptSubmitHandler,
    PreToolUseHandler, PermissionRequestHandler, PostToolUseHandler,
    PreCompactHandler, PostCompactHandler, SubagentStopHandler,
    StopHandler, SessionEndHandler, InterruptHandler,
)
_BY_EVENT = {handler.event_name: handler for handler in HOOK_HANDLER_CLASSES}


def handler_class_for_event(event: str) -> type[NativeHookHandler]:
    try:
        return _BY_EVENT[event]
    except KeyError:
        raise LaneError("HOOK_EVENT_UNSUPPORTED", "Select a documented packaged Hook event.") from None


__all__ = ["HOOK_HANDLER_CLASSES", "HandledHook", "NativeHookHandler", "handler_class_for_event"]
