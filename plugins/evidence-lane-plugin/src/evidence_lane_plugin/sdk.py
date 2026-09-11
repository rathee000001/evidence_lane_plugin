"""Typed public envelopes and a transport-independent client."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, Protocol, Self
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from . import PROTOCOL_VERSION
from .errors import LaneError
from .registry import ActionContext, ActionRegistry, Contract

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class ActionRequest(Contract):
    protocol_version: Literal[4] = PROTOCOL_VERSION
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    project_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    expected_revision: int | None = Field(default=None, ge=1)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class PublicError(Contract):
    code: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)


class EvidenceReference(Contract):
    kind: Literal["receipt", "object", "source", "test", "commit", "installation", "user_decision"]
    identifier: str
    project_id: str | None = None
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ActionResponse(Contract):
    protocol_version: Literal[4] = PROTOCOL_VERSION
    request_id: str = Field(pattern=UUID_PATTERN)
    action: str
    status: Literal["ok", "error", "queued", "waiting"]
    result: dict[str, JsonValue] | None = None
    error: PublicError | None = None
    job_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    plan_revision: int | None = Field(default=None, ge=1)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    tool_execution: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def consistent_status(self) -> Self:
        if self.status == "error":
            if self.error is None or self.result is not None:
                raise ValueError("Error responses require an error and no successful result")
        elif self.error is not None:
            raise ValueError("Non-error responses cannot contain an error")
        if self.status == "queued" and self.job_id is None:
            raise ValueError("Queued responses require an addressable job")
        return self


class JobCheckpoint(Contract):
    job_id: str = Field(pattern=UUID_PATTERN)
    plan_revision: int | None = Field(default=None, ge=1)
    phase: str
    checkpoint_object: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effect_state: Literal["none", "prepared", "confirmed", "uncertain"]
    resumable: bool


class CancellationRequest(Contract):
    job_id: str = Field(pattern=UUID_PATTERN)
    reason: str = Field(min_length=1, max_length=1000)


class Transport(Protocol):
    def send(self, request: ActionRequest) -> ActionResponse: ...


class EvidenceLaneClient:
    """Never retry a mutation automatically after a transport failure."""

    def __init__(self, transport: Transport):
        self.transport = transport

    def call(
        self,
        action: str,
        *,
        project_id: str | None = None,
        arguments: dict[str, Any] | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> ActionResponse:
        values = {
            "action": action,
            "project_id": project_id,
            "arguments": arguments or {},
            "expected_revision": expected_revision,
        }
        if request_id is not None:
            values["request_id"] = request_id
        request = ActionRequest.model_validate(values)
        response = self.transport.send(request)
        if response.request_id != request.request_id or response.action != request.action:
            raise LaneError(
                "RESPONSE_BINDING_MISMATCH", "The response does not belong to this request."
            )
        return response


def dispatch(
    registry: ActionRegistry, request: ActionRequest, context: ActionContext
) -> ActionResponse:
    """Shared dispatch for engine execution; connection and grant checks precede this call."""
    try:
        if request.project_id != context.project_id:
            raise LaneError(
                "PROJECT_BINDING_MISMATCH", "The request does not match its connection context."
            )
        context = replace(context, request_id=request.request_id, expected_revision=request.expected_revision)
        result, tool_execution = registry.execute_attributed(request.action, request.arguments, context)
        return ActionResponse(
            request_id=request.request_id, action=request.action,
            status="queued" if registry.get(request.action).queued else "ok", result=result,
            tool_execution=tool_execution,
            job_id=result.get("job_id") if registry.get(request.action).queued else None,
            plan_revision=result.get("plan_revision") if registry.get(request.action).queued else None,
        )
    except LaneError as error:
        return ActionResponse(
            request_id=request.request_id,
            action=request.action,
            status="error",
            error=PublicError.model_validate(error.public()),
        )
