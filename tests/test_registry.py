from __future__ import annotations

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.registry import ActionContext, ActionRegistry, ActionSpec, Contract


class Arguments(Contract):
    count: int


class Result(Contract):
    count: int


def registry(*, mutates=False, permission="read", handler=None):
    result = ActionRegistry()
    result.register(
        ActionSpec(
            "count_items",
            "Count selected items.",
            Arguments,
            Result,
            handler or (lambda context, args: {"count": args.count + 1}),
            permission=permission,
            mutates=mutates,
        )
    )
    return result


def context(*permissions, project="project-a"):
    return ActionContext("connection-a", project, frozenset(permissions))


def test_schema_and_execution_share_the_same_contract():
    actions = registry()
    schema = actions.schemas()[0]
    assert schema["inputSchema"]["additionalProperties"] is False
    assert schema["outputSchema"]["properties"]["count"]["type"] == "integer"
    assert actions.execute("count_items", {"count": 2}, context("read")) == {"count": 3}


@pytest.mark.parametrize("payload", [{"count": "2"}, {"count": 2, "project_id": "other"}, {}])
def test_invalid_or_injected_arguments_never_reach_handler(payload):
    called = []
    actions = registry(handler=lambda *args: called.append(args))
    with pytest.raises(LaneError) as error:
        actions.execute("count_items", payload, context("read"))
    assert error.value.code == "INVALID_ARGUMENTS"
    assert not called


def test_mutation_cannot_register_as_read_only():
    with pytest.raises(LaneError, match="read permission"):
        registry(mutates=True)


def test_permission_is_connection_owned_and_checked_before_handler():
    actions = registry(mutates=True, permission="write")
    with pytest.raises(LaneError) as error:
        actions.execute("count_items", {"count": 1}, context("read"))
    assert error.value.code == "PERMISSION_DENIED"


def test_project_is_required_unless_action_declares_otherwise():
    with pytest.raises(LaneError) as error:
        registry().execute("count_items", {"count": 1}, context("read", project=None))
    assert error.value.code == "PROJECT_REQUIRED"


def test_aliases_duplicates_and_late_registrations_are_rejected():
    actions = registry()
    with pytest.raises(LaneError) as error:
        actions.get("COUNT_ITEMS")
    assert error.value.code == "UNKNOWN_ACTION"
    with pytest.raises(LaneError) as error:
        actions.register(actions.get("count_items"))
    assert error.value.code == "DUPLICATE_ACTION"
    actions.freeze()
    with pytest.raises(LaneError) as error:
        actions.register(actions.get("count_items"))
    assert error.value.code == "REGISTRY_FROZEN"


def test_invalid_results_are_not_returned_as_success():
    with pytest.raises(LaneError) as error:
        registry(handler=lambda *_: {"count": "wrong"}).execute(
            "count_items", {"count": 1}, context("read")
        )
    assert error.value.code == "INVALID_RESULT"


def test_validation_error_does_not_echo_input():
    secret = "private-input-that-must-not-be-echoed"
    with pytest.raises(LaneError) as error:
        registry().execute("count_items", {"count": secret}, context("read"))
    assert secret not in str(error.value.public())
