from __future__ import annotations

import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanRead, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import EvidenceLaneClient
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease
from pydantic import ValidationError


@pytest.fixture
def plan(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "state", source)
    return PlanStore(store)


def definition(task_id, **kwargs):
    return TaskDefinition(task_id=task_id, title="Task " + task_id, requested_outcome="Verified " + task_id,
                          acceptance_checks=["Representative operation passes."], **kwargs)


def create(plan, *, tasks=None):
    request = PlanCreate(title="Actual project work", tasks=tasks or [definition("first"), definition("second")])
    with WriterLease(plan.store, "test-engine") as lease:
        plan.create(request, lease, actor_id="test-client")
    return request


def verification(plan, task_id, lease, *, revision=1, **overrides):
    body = {"project_id": plan.store.project_id, "plan_revision": revision, "task_id": task_id,
            "contract_digest": plan.task(task_id, expected_revision=revision).contract_digest, "status": "passed"}
    body.update(overrides)
    with lease.transaction("receipts") as connection:
        return plan.project.append_receipt("delta_exit_verified", body, connection=connection)


def test_read_of_uninitialized_plan_is_readonly_and_does_not_create_schema(plan):
    before = plan.store.database.read_bytes()
    assert plan.snapshot().state == "no_plan"
    assert plan.store.database.read_bytes() == before
    with pytest.raises(LaneError) as error:
        plan.task("missing", expected_revision=1)
    assert error.value.code == "PLAN_NOT_CREATED"


def test_plan_creation_normalizes_linear_dependencies_and_preserves_full_contract(plan):
    request = create(plan, tasks=[definition("one", permitted_paths=["src"], permitted_tools=["Git"],
                                           allowed_actions=["project_status"], commit_batch_id="batch-1"),
                                  definition("two"), definition("independent", dependencies=[])])
    snapshot = PlanStore(ProjectStore(plan.store.root, read_only=True)).snapshot()
    assert snapshot.revision == snapshot.current_revision == 1
    assert snapshot.counts == {"queued": 3}
    assert snapshot.tasks[0].definition.commit_batch_id == "batch-1"
    assert snapshot.tasks[0].definition.permitted_paths == ["src"]
    assert snapshot.tasks[1].definition.dependencies == ["one"]
    assert snapshot.tasks[2].definition.dependencies == []
    with WriterLease(plan.store, "test-engine") as lease:
        assert plan.create(request, lease, actor_id="test-client") == snapshot
        with pytest.raises(LaneError) as error:
            plan.create(request.model_copy(update={"title": "Changed"}), lease, actor_id="test-client")
        assert error.value.code == "PLAN_REQUEST_CONFLICT"
        with pytest.raises(LaneError) as error:
            plan.create(PlanCreate(title="Second plan", tasks=[definition("one")]), lease, actor_id="test-client")
        assert error.value.code == "PLAN_ALREADY_CREATED"
    assert plan.verify_history()["events_verified"] == 1


@pytest.mark.parametrize("tasks", [
    [definition("same"), definition("same")],
    [definition("one", dependencies=["one"])],
    [definition("one", dependencies=["missing"])],
    [definition("one", dependencies=["two"]), definition("two", dependencies=["one"])],
])
def test_invalid_plan_topology_is_rejected_before_persistence(plan, tasks):
    with pytest.raises(ValidationError):
        PlanCreate(title="Invalid topology", tasks=tasks)
    assert plan.snapshot().state == "no_plan"


def test_one_active_task_dependency_gate_and_verified_completion(plan):
    create(plan)
    with WriterLease(plan.store, "test-engine") as lease:
        with pytest.raises(LaneError) as error:
            plan.transition("second", "active", lease, expected_revision=1, actor_id="test-client")
        assert error.value.code == "PLAN_DEPENDENCY_INCOMPLETE"
        event_id = str(uuid4())
        first_event = plan.transition("first", "active", lease, expected_revision=1, actor_id="test-client", event_id=event_id)
        assert plan.transition("first", "active", lease, expected_revision=1, actor_id="test-client", event_id=event_id) == first_event
        with pytest.raises(LaneError) as error:
            plan.transition("second", "active", lease, expected_revision=1, actor_id="test-client")
        assert error.value.code == "PLAN_TASK_ALREADY_ACTIVE"
        with pytest.raises(LaneError) as error:
            plan.transition("first", "completed", lease, expected_revision=1, actor_id="test-client")
        assert error.value.code == "DELTA_VERIFICATION_REQUIRED"
        # A wrong contract receipt cannot be promoted to completion.
        receipt = verification(plan, "first", lease, contract_digest="0" * 64)
        with pytest.raises(LaneError):
            plan.transition("first", "completed", lease, expected_revision=1, actor_id="test-client", evidence_receipt=receipt)
        receipt = verification(plan, "first", lease)
        plan.transition("first", "completed", lease, expected_revision=1, actor_id="test-client", evidence_receipt=receipt)
        plan.transition("second", "active", lease, expected_revision=1, actor_id="test-client")
        with pytest.raises(LaneError):
            plan.transition("first", "active", lease, expected_revision=1, actor_id="test-client")
    assert plan.snapshot().counts == {"completed": 1, "active": 1}
    assert plan.verify_history()["events_verified"] == 4


def test_stale_revision_and_event_id_rebinding_never_mutate_plan(plan):
    create(plan)
    with WriterLease(plan.store, "test-engine") as lease:
        event_id = str(uuid4())
        plan.transition("first", "active", lease, expected_revision=1, actor_id="test-client", event_id=event_id)
        before = plan.snapshot()
        with pytest.raises(LaneError) as error:
            plan.transition("first", "blocked", lease, expected_revision=2, actor_id="test-client")
        assert error.value.code == "STALE_PLAN_REVISION"
        with pytest.raises(LaneError) as error:
            plan.transition("first", "blocked", lease, expected_revision=1, actor_id="test-client", event_id=event_id)
        assert error.value.code == "PLAN_EVENT_CONFLICT"
        assert plan.snapshot() == before


def test_failed_transaction_retains_plan_and_event_head(plan, monkeypatch):
    create(plan)
    before = plan.snapshot()
    original = plan._event

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("Simulated failure after event insertion")

    monkeypatch.setattr(plan, "_event", fail)
    with WriterLease(plan.store, "test-engine") as lease, pytest.raises(OSError):
        plan.transition("first", "active", lease, expected_revision=1, actor_id="test-client")
    assert plan.snapshot() == before
    assert plan.verify_history()["events_verified"] == 1


def test_bounded_pagination_reports_whole_plan_count_without_mutation(plan):
    create(plan, tasks=[definition(f"task-{i}") for i in range(205)])
    before = plan.store.database.read_bytes()
    page = plan.snapshot(PlanRead(offset=100))
    assert page.total_tasks == 205 and len(page.tasks) == 100 and page.truncated
    assert page.tasks[0].position == 101
    last = plan.snapshot(PlanRead(offset=200))
    assert len(last.tasks) == 5 and not last.truncated
    assert plan.store.database.read_bytes() == before


def test_tampered_event_and_contract_are_detected(plan):
    create(plan)
    with plan.store.transaction() as connection:
        connection.execute("UPDATE plan_events SET payload_json='{}' WHERE sequence=1")
    with pytest.raises(LaneError) as error:
        plan.verify_history()
    assert error.value.code == "PLAN_HISTORY_INTEGRITY"
    with plan.store.transaction() as connection:
        definition_json = connection.execute("SELECT definition_json FROM plan_tasks WHERE task_id='first'").fetchone()[0]
        changed = json.loads(definition_json)
        changed["requested_outcome"] = "Changed behind the revision"
        connection.execute("UPDATE plan_tasks SET definition_json=? WHERE task_id='first'", (json.dumps(changed),))
    with pytest.raises(LaneError) as error:
        plan.task("first", expected_revision=1)
    assert error.value.code == "PLAN_CONTRACT_INTEGRITY"


def test_native_sdk_plan_route_uses_selected_sqlite_and_readonly_grants(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with Engine(tmp_path / "runtime") as engine, LocalEndpoint(engine) as endpoint:
        record = engine.directory.register(tmp_path / "state", source_root=source, create=True, read_only=False)
        project_id = record["project_id"]
        with LocalTransport(engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=project_id, permissions=["read", "write"])])) as transport:
            client = EvidenceLaneClient(transport)
            request = PlanCreate(title="SDK Plan", tasks=[definition("native-one")])
            response = client.call("plan_create", project_id=project_id, arguments=request.model_dump(mode="json"))
            assert response.status == "ok" and response.result["revision"] == 1
            assert endpoint.studio.snapshot(project_id)["project"]["plan"]["plan_id"] == request.plan_id
        with LocalTransport(engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=project_id)])) as transport:
            client = EvidenceLaneClient(transport)
            assert client.call("plan_read", project_id=project_id).result["tasks"][0]["definition"]["task_id"] == "native-one"
            assert client.call("plan_create", project_id=project_id, arguments=request.model_dump(mode="json")).status == "error"
        assert PlanStore(ProjectStore(tmp_path / "state", read_only=True)).snapshot().total_tasks == 1
        assert "plan_set_status" not in [item["name"] for item in engine.registry.schemas()]


@pytest.mark.parametrize('field', ['document_json', 'title', 'request_id'])
def test_corrupt_revision_metadata_is_rejected_without_rewriting_it(plan, field):
    create(plan)
    with plan.store.transaction() as connection:
        connection.execute(f'UPDATE plan_revisions SET {field}=? WHERE revision=1', ('{}' if field == 'document_json' else 'changed',))
    before = plan.store.database.read_bytes()
    with pytest.raises(LaneError) as error:
        plan.snapshot()
    assert error.value.code == 'PLAN_DOCUMENT_INTEGRITY'
    assert plan.store.database.read_bytes() == before


def test_missing_dependency_index_cannot_activate_a_dependent_task(plan):
    create(plan)
    with plan.store.transaction() as connection:
        connection.execute("DELETE FROM plan_dependencies WHERE task_id='second'")
    before = plan.store.database.read_bytes()
    with pytest.raises(LaneError) as error:
        plan.snapshot()
    assert error.value.code == 'PLAN_DEPENDENCY_INTEGRITY'
    with WriterLease(plan.store, 'test-engine') as lease, pytest.raises(LaneError) as error:
        plan.transition('second', 'active', lease, expected_revision=1, actor_id='test-client')
    assert error.value.code == 'PLAN_DEPENDENCY_INTEGRITY'
    assert plan.store.database.read_bytes() == before


def test_contract_identity_must_match_its_task_row(plan):
    from evidence_lane_plugin.plan_runtime import content_digest
    create(plan)
    with plan.store.transaction() as connection:
        document = json.loads(connection.execute("SELECT definition_json FROM plan_tasks WHERE task_id='first'").fetchone()[0])
        document['task_id'] = 'another-task'
        connection.execute("UPDATE plan_tasks SET definition_json=?,contract_digest=? WHERE task_id='first'",
                           (json.dumps(document), content_digest(document)))
    with pytest.raises(LaneError) as error:
        plan.task('first', expected_revision=1)
    assert error.value.code == 'PLAN_CONTRACT_INTEGRITY'
