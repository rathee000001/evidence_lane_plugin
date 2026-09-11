from __future__ import annotations

from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.steering import Steering, SteerIntent
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease
from pydantic import ValidationError


@pytest.fixture
def steering(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "state", source)
    plan = PlanStore(store)
    with WriterLease(store, "engine") as lease:
        plan.create(PlanCreate(title="Work", tasks=[TaskDefinition(task_id="one", title="One", requested_outcome="First outcome"),
                         TaskDefinition(task_id="two", title="Two", requested_outcome="Second outcome")]), lease, actor_id="client")
        plan.transition("one", "active", lease, expected_revision=1, actor_id="client")
        captured = ChatLineage(store).append(LineageRecord(kind="prompt", payload={"text": "Explain the change before proceeding"}),
                                            lease, client_id="client")
    return Steering(store), captured


def intent(captured, **kwargs):
    return SteerIntent(source_event_id=captured.event_id, source_cursor=captured.cursor, expected_revision=1,
                       intent=kwargs.pop("intent", "informational"), rationale="Declared interpretation of visible user input", **kwargs)


def test_information_query_has_no_database_effect(steering):
    service, captured = steering
    def project_bytes():
        return {path.relative_to(service.project.root): path.read_bytes()
                for path in service.project.root.rglob('*') if path.is_file()}

    before = project_bytes()
    snapshot = service.plan.snapshot()
    result = service.preview(intent(captured), actor_id="client")
    assert result.disposition == "read_only" and not result.plan_changed and not result.checkpoint_required
    assert service.plan.snapshot() == snapshot
    assert service.pending() == []
    assert project_bytes() == before
    with pytest.raises(ValidationError):
        intent(captured, affected_task_ids=["one"])


def test_semantic_steer_queues_exact_source_and_preserves_plan_until_checkpoint(steering):
    service, captured = steering
    request = intent(captured, intent="semantic", affected_task_ids=["one", "two"])
    assert service.preview(request, actor_id="client").checkpoint_required
    before = service.plan.snapshot()
    with WriterLease(service.store, "engine") as lease:
        result = service.submit(request, lease, actor_id="client")
        assert result.state == "pending" and not result.plan_changed
        assert service.submit(request, lease, actor_id="client").duplicate
        with pytest.raises(LaneError) as error:
            service.submit(request.model_copy(update={"rationale": "Different change"}), lease, actor_id="client")
        assert error.value.code == "STEER_REQUEST_CONFLICT"
    assert service.plan.snapshot() == before
    assert service.pending()[0]["source_cursor"] == captured.cursor


def test_stop_requires_safe_stop_and_includes_active_task(steering):
    service, captured = steering
    result = service.preview(intent(captured, intent="stop"), actor_id="client")
    assert result.disposition == "requires_safe_stop" and result.affected_task_ids == ["one"]
    assert result.checkpoint_required and not result.plan_changed


def test_steer_cannot_substitute_source_client_or_revision(steering):
    service, captured = steering
    request = intent(captured, intent="semantic", affected_task_ids=["one"])
    for changed, actor, code in [
        ({"source_event_id": str(uuid4())}, "client", "STEER_SOURCE_MISMATCH"),
        ({"source_cursor": "0" * 64}, "client", "STEER_SOURCE_MISMATCH"),
        ({}, "different-client", "STEER_SOURCE_MISMATCH"),
        ({"expected_revision": 2}, "client", "STALE_PLAN_REVISION"),
    ]:
        with pytest.raises(LaneError) as error:
            service.preview(request.model_copy(update=changed), actor_id=actor)
        assert error.value.code == code
    assert service.pending() == []


def test_informational_submit_rejected_and_terminal_history_not_rewritten(steering):
    service, captured = steering
    with WriterLease(service.store, "engine") as lease:
        with pytest.raises(LaneError) as error:
            service.submit(intent(captured), lease, actor_id="client")
        assert error.value.code == "INFORMATIONAL_QUERY_READ_ONLY"
        service.plan.transition("two", "cancelled", lease, expected_revision=1, actor_id="client")
        with pytest.raises(LaneError) as error:
            service.submit(intent(captured, intent="semantic", affected_task_ids=["two"]), lease, actor_id="client")
        assert error.value.code == "STEER_HISTORY_IMMUTABLE"
    assert service.pending() == []
