"""Internal authenticated dispatch shared by the imported native entrypoints.

The v3 SDK's independent authority namespaces remain action profiles.
Locked Flash and project sessions use their registered workflows. Formula,
accepted-PV and HIL orchestration have no dispatch route.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import LaneError
from .registry import ActionContext
from .sdk import ActionRequest, ActionResponse, PublicError, dispatch

if TYPE_CHECKING:
    from .connections import Session
    from .engine import Engine

def dispatch_authenticated(engine: Engine, request: ActionRequest, context: ActionContext) -> ActionResponse:
    """Both transports authorize the exact principal/project before this call."""
    try:
        if request.action in {"engine_health", "runtime_status"} and engine.phase in {"running", "draining"}:
            return dispatch(engine.registry, request, context)
        with engine.admit(), engine.project_work.authorized(context, engine.registry.get(request.action).permission):
            return dispatch(engine.registry, request, context)
    except LaneError as error:
        return ActionResponse(request_id=request.request_id, action=request.action, status="error",
                              error=PublicError.model_validate(error.public()))

class PublicActionSDKDispatcher:
    def __init__(self, engine: Engine):
        self.engine = engine

    def execute(self, request: ActionRequest, session: Session) -> ActionResponse:
        try:
            spec = self.engine.registry.get(request.action)
            context = self.engine.clients.context(session, request.project_id, spec.permission)
            return dispatch_authenticated(self.engine, request, context)
        except LaneError as error:
            return ActionResponse(request_id=request.request_id, action=request.action,
                                  status="error", error=PublicError.model_validate(error.public()))
