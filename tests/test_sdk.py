from __future__ import annotations

from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.registry import ActionContext, ActionRegistry, ActionSpec, Contract
from evidence_lane_plugin.sdk import ActionRequest, ActionResponse, EvidenceLaneClient, dispatch
from pydantic import ValidationError


class Value(Contract):
    value: str


def test_sdk_dispatch_preserves_request_and_project_binding():
    registry = ActionRegistry()
    registry.register(
        ActionSpec(
            "echo_value", "Return a value.", Value, Value, lambda context, arguments: arguments
        )
    )
    project = str(uuid4())
    context = ActionContext("actual-client", project, frozenset({"read"}))
    request = ActionRequest(action="echo_value", project_id=project, arguments={"value": "hello"})
    response = dispatch(registry, request, context)
    assert response.request_id == request.request_id
    assert response.result == {"value": "hello"}
    wrong = request.model_copy(update={"project_id": str(uuid4())})
    assert dispatch(registry, wrong, context).error.code == "PROJECT_BINDING_MISMATCH"


def test_client_never_retries_an_uncertain_mutation():
    class FailingTransport:
        calls = 0

        def send(self, request):
            self.calls += 1
            raise TimeoutError("outcome unknown")

    transport = FailingTransport()
    with pytest.raises(TimeoutError):
        EvidenceLaneClient(transport).call("change_file")
    assert transport.calls == 1


def test_response_from_another_request_is_rejected():
    class WrongTransport:
        def send(self, request):
            return ActionResponse(
                request_id=str(uuid4()), action=request.action, status="ok", result={}
            )

    with pytest.raises(LaneError) as error:
        EvidenceLaneClient(WrongTransport()).call("engine_health")
    assert error.value.code == "RESPONSE_BINDING_MISMATCH"


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "error"},
        {"status": "queued"},
        {"status": "ok", "error": {"code": "FAILED", "message": "failed"}},
    ],
)
def test_ambiguous_response_states_are_rejected(fields):
    with pytest.raises(ValidationError):
        ActionResponse.model_validate(
            {"request_id": str(uuid4()), "action": "test_action", **fields}
        )


def test_request_rejects_caller_supplied_connection_identity():
    with pytest.raises(ValidationError):
        ActionRequest.model_validate({"action": "engine_health", "client_id": "forged-client"})


def test_request_id_can_be_reused_explicitly_for_later_reconciliation():
    captured = []

    class Transport:
        def send(self, request):
            captured.append(request.request_id)
            return ActionResponse(
                request_id=request.request_id, action=request.action, status="ok", result={}
            )

    request_id = str(uuid4())
    client = EvidenceLaneClient(Transport())
    client.call("request_status", request_id=request_id)
    assert captured == [request_id]
