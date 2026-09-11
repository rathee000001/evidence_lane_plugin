from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.plan_runtime import (
    PlanCreate,
    PlanRead,
    PlanReplace,
    PlanStore,
    TaskDefinition,
)
from evidence_lane_plugin.sdk import ActionRequest, EvidenceLaneClient
from evidence_lane_plugin.steering import Steering, SteerIntent


@pytest.fixture
def project(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with Engine(tmp_path / "runtime") as engine, LocalEndpoint(engine):
        record = engine.directory.register(tmp_path / "state", source_root=source, create=True, read_only=False)
        store = engine.directory.open(record["project_id"], write=True)
        with LocalTransport(engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,
                                                                                            permissions=["read", "write"])])) as transport:
            actor = transport.client_id
            plan = PlanStore(store)
            with engine.project_work.mutation(store) as lease:
                plan.create(PlanCreate(title="Original", tasks=[TaskDefinition(task_id=name, title=name, requested_outcome="Outcome " + name)
                            for name in ("one", "two", "three")]), lease, actor_id=actor)
                plan.transition("one", "active", lease, expected_revision=1, actor_id=actor)
                contract_digest = plan.task('one', expected_revision=1).contract_digest
                with lease.transaction('receipts') as connection:
                    evidence = store.append_receipt("delta_exit_verified", {"project_id": store.project_id, "plan_revision": 1,
                        "task_id": "one", "contract_digest": contract_digest, "status": "passed"}, connection=connection)
                plan.transition("one", "completed", lease, expected_revision=1, actor_id=actor, evidence_receipt=evidence)
                JobQueue(store).initialize(lease)
            yield engine, plan, actor, EvidenceLaneClient(transport)


def record_steer(project, *, affected=None, intent="semantic", kind="prompt"):
    engine, plan, actor, _client = project
    before = plan.snapshot()
    with engine.project_work.mutation(plan.store, kind="signal") as lease:
        source = ChatLineage(plan.store).append(LineageRecord(kind=kind, payload={"text": "Explicit revised direction"}), lease, client_id=actor)
        request = SteerIntent(source_event_id=source.event_id, source_cursor=source.cursor, expected_revision=before.revision,
                              intent=intent, rationale="The captured input changes this work", affected_task_ids=affected or [])
        Steering(plan.store).submit(request, lease, actor_id=actor)
    return request


def replacement(project, steer, *, tasks=None, **kwargs):
    _engine, plan, _actor, _client = project
    before = plan.snapshot()
    return PlanReplace(plan_id=before.plan_id, title=kwargs.pop("title", "Revised"), tasks=tasks or [item.definition for item in before.tasks],
        expected_revision=before.revision, expected_document_digest=before.document_digest, steer_request_id=steer.request_id, **kwargs)


def apply(project, request):
    engine, plan, actor, _client = project
    with engine.project_work.mutation(plan.store) as lease:
        return plan.replace(request, lease, actor_id=actor)


def test_atomic_replacement_preserves_completed_history_and_invalidates_queued_jobs(project):
    engine, plan, actor, client = project
    before = plan.snapshot()
    with engine.project_work.mutation(plan.store) as lease:
        job = JobQueue(plan.store).enqueue(ActionRequest(action="work", project_id=plan.store.project_id, expected_revision=1), actor, lease)
    steer = record_steer(project, affected=["two"])
    tasks = [item.definition for item in before.tasks]
    tasks[1] = tasks[1].model_copy(update={"requested_outcome": "New bounded outcome"})
    request = replacement(project, steer, tasks=tasks)
    response = client.call("plan_refresh", project_id=plan.store.project_id, arguments=request.model_dump(mode="json"))
    assert response.status == "ok", response.error
    current = plan.snapshot()
    assert current.revision == 2 and current.counts == {"completed": 1, "queued": 2}
    assert current.tasks[0] == before.tasks[0]
    assert current.tasks[1].contract_digest != before.tasks[1].contract_digest
    historical = plan.snapshot(PlanRead(revision=1))
    assert historical.tasks[0] == before.tasks[0] and historical.tasks[1].state == "superseded"
    assert historical.document_digest == before.document_digest
    assert JobQueue(plan.store).get(job)["state"] == "superseded"
    assert apply(project, request).revision == 2  # exact request replay after head advancement
    with pytest.raises(LaneError) as error:
        apply(project, request.model_copy(update={"title": "Rebound ID"}))
    assert error.value.code == "PLAN_REQUEST_CONFLICT"
    assert plan.verify_history()["events_verified"] == 6


@pytest.mark.parametrize("target,affected,expected", [("one", ["two"], "PLAN_HISTORY_IMMUTABLE"),
                                                    ("three", ["two"], "STEER_SCOPE_MISMATCH")])
def test_history_and_affected_scope_are_enforced_without_partial_revision(project, target, affected, expected):
    _engine, plan, _actor, _client = project
    before = plan.snapshot()
    steer = record_steer(project, affected=affected)
    tasks = [item.definition.model_copy(update={"title": "Changed"}) if item.definition.task_id == target else item.definition for item in before.tasks]
    with pytest.raises(LaneError) as error:
        apply(project, replacement(project, steer, tasks=tasks))
    assert error.value.code == expected and plan.snapshot() == before
    with plan.store.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM plan_revisions").fetchone()[0] == 1


def test_active_or_uncertain_work_must_checkpoint_before_plan_swap(project):
    engine, plan, actor, _client = project
    steer = record_steer(project, affected=["two"])
    request = replacement(project, steer)
    with engine.project_work.mutation(plan.store) as lease:
        plan.transition("two", "active", lease, expected_revision=1, actor_id=actor)
    with pytest.raises(LaneError) as error:
        apply(project, request)
    assert error.value.code == "PLAN_CHECKPOINT_REQUIRED"
    with engine.project_work.mutation(plan.store) as lease:
        plan.transition("two", "blocked", lease, expected_revision=1, actor_id=actor)
        queue = JobQueue(plan.store)
        job = queue.enqueue(ActionRequest(action="work", project_id=plan.store.project_id, expected_revision=1), actor, lease)
        claim = queue.claim(job, lease)
        queue.prepare_effect(job, "external", "Uncertain operation", lease, execution_id=claim.execution_id)
        queue.fail(job, "CONNECTION_LOST", lease, execution_id=claim.execution_id)
    with pytest.raises(LaneError) as error:
        apply(project, request)
    assert error.value.code == "JOBS_NOT_QUIESCENT"
    assert plan.snapshot().revision == 1


def test_readers_observe_complete_old_or_new_revision(project, monkeypatch):
    _engine, plan, _actor, _client = project
    before = plan.snapshot()
    steer = record_steer(project, affected=["two"])
    request = replacement(project, steer)
    entered, release = threading.Event(), threading.Event()
    original = PlanStore._event

    def barrier(self, connection, **kwargs):
        result = original(self, connection, **kwargs)
        if kwargs["kind"] == "plan_replaced":
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(PlanStore, "_event", barrier)
    with ThreadPoolExecutor(2) as worker:
        result = worker.submit(apply, project, request)
        try:
            assert entered.wait(5)
            reading = worker.submit(plan.snapshot)
            assert not reading.done()
        finally:
            release.set()
        assert result.result(5).revision == 2
        observed = reading.result(5)
        assert observed.revision == 2 and len(observed.tasks) == len(before.tasks)
    assert plan.snapshot().revision == 2


def test_failed_swap_rolls_back_contracts_jobs_events_and_steer_state(project, monkeypatch):
    engine, plan, actor, _client = project
    before = plan.snapshot()
    steer = record_steer(project, affected=["two"])
    with engine.project_work.mutation(plan.store) as lease:
        job = JobQueue(plan.store).enqueue(ActionRequest(action="work", project_id=plan.store.project_id, expected_revision=1), actor, lease)
    original = PlanStore._event

    def fail(self, connection, **kwargs):
        original(self, connection, **kwargs)
        if kwargs["kind"] == "plan_replaced":
            raise OSError("Simulated failure after all pointer changes")

    monkeypatch.setattr(PlanStore, "_event", fail)
    with pytest.raises(OSError):
        apply(project, replacement(project, steer))
    assert plan.snapshot() == before and JobQueue(plan.store).get(job)["state"] == "queued"
    assert Steering(plan.store).pending()[0]["state"] == "pending"
    assert plan.verify_history()["events_verified"] == 3


def test_newer_visible_steer_and_changed_source_digest_are_not_ignored(project):
    _engine, _plan, _actor, _client = project
    old = record_steer(project, affected=["two"])
    request = replacement(project, old)
    latest = record_steer(project, affected=["two"])
    with pytest.raises(LaneError) as error:
        apply(project, request)
    assert error.value.code == "NEWER_STEER_PENDING"
    with pytest.raises(LaneError) as error:
        apply(project, replacement(project, latest).model_copy(update={"expected_document_digest": "0" * 64}))
    assert error.value.code == "PLAN_SOURCE_CHANGED"
    assert apply(project, replacement(project, latest)).revision == 2


def test_stop_survives_revision_change_until_explicit_later_resume(project):
    _engine, plan, _actor, _client = project
    old = record_steer(project, affected=["two"])
    old_request = replacement(project, old, resume_after_change=True)
    record_steer(project, intent="stop")
    with pytest.raises(LaneError):
        apply(project, old_request)
    assert Steering(plan.store).control()["paused"]
    new = record_steer(project, affected=["two"])
    assert apply(project, replacement(project, new)).revision == 2
    assert Steering(plan.store).control()["paused"]
    resume = record_steer(project, affected=["two"])
    assert apply(project, replacement(project, resume, resume_after_change=True)).revision == 3
    assert not Steering(plan.store).control()["paused"]


def test_removed_task_identity_is_not_resurrected(project):
    _engine, plan, _actor, _client = project
    initial = plan.snapshot()
    remove = record_steer(project, affected=["two", "three"])
    tasks = [initial.tasks[0].definition, initial.tasks[2].definition.model_copy(update={"dependencies": ["one"]})]
    assert apply(project, replacement(project, remove, tasks=tasks)).revision == 2
    restore = record_steer(project, affected=["three"])
    with pytest.raises(LaneError) as error:
        apply(project, replacement(project, restore, tasks=[item.definition for item in initial.tasks]))
    assert error.value.code == "PLAN_TASK_ID_REUSED"


@pytest.mark.parametrize('field', ['title', 'request_id'])
def test_replacement_rejects_corrupt_prior_metadata_before_publishing(project, field):
    engine, plan, _actor, _client = project
    steer = record_steer(project, affected=['two'])
    request = replacement(project, steer)
    with engine.project_work.mutation(plan.store) as lease, lease.transaction('plan') as connection:
        connection.execute(f'UPDATE plan_revisions SET {field}=? WHERE revision=1', ('corrupt-fixture-metadata',))
        before_events = connection.execute('SELECT count(*) FROM plan_events').fetchone()[0]
    with pytest.raises(LaneError) as error:
        apply(project, request)
    assert error.value.code == 'PLAN_DOCUMENT_INTEGRITY'
    with plan.store.connection(read_only=True) as connection:
        assert connection.execute('SELECT revision FROM plan_current').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM plan_revisions').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM plan_events').fetchone()[0] == before_events
    assert Steering(plan.store).pending()[0]['state'] == 'pending'
