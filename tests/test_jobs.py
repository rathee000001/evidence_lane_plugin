from __future__ import annotations

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease


@pytest.fixture
def state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    store = ProjectStore.create(tmp_path / "state", source)
    queue = JobQueue(store)
    with WriterLease(store, "engine") as lease:
        queue.initialize(lease)
    return store, queue


def admitted(store, queue, lease):
    request = ActionRequest(action="run_work", project_id=store.project_id)
    job = queue.enqueue(request, "client", lease)
    return request, job


def test_large_request_requires_explicit_bounded_queue_budget(state):
    store, queue = state
    request = ActionRequest(action='run_work', project_id=store.project_id,
                            arguments={'source': 'x' * 1_100_000})
    with WriterLease(store, 'engine') as lease:
        with pytest.raises(LaneError) as rejected:
            queue.enqueue(request, 'client', lease)
        assert rejected.value.code == 'JOB_TOO_LARGE'
        for invalid in (True, 0, 67_174_401):
            with pytest.raises(LaneError) as rejected:
                queue.enqueue(request, 'client', lease, max_request_bytes=invalid)
            assert rejected.value.code == 'INVALID_JOB_BUDGET'
        job_id = queue.enqueue(request, 'client', lease, max_request_bytes=2_000_000)
        claim = queue.claim(job_id, lease)
        assert claim.request.arguments == request.arguments


def test_request_reconciliation_does_not_enqueue_or_replay_twice(state):
    store, queue = state
    with WriterLease(store, "engine") as lease:
        request, job = admitted(store, queue, lease)
        assert queue.enqueue(request, "client", lease) == job
        with pytest.raises(LaneError) as error:
            queue.enqueue(request.model_copy(update={"arguments": {"changed": True}}), "client", lease)
        assert error.value.code == "REQUEST_ID_CONFLICT"
        claim = queue.claim(job, lease)
        queue.complete(job, {"answer": 42}, lease, execution_id=claim.execution_id)
        assert queue.enqueue(request, "client", lease) == job
        with pytest.raises(LaneError):
            claim = queue.claim(job, lease)
    assert queue.get(job)["result"] == {"answer": 42}
    assert len(queue.list()) == 1


def test_checkpoint_resumes_only_when_explicitly_claimed(state):
    store, queue = state
    checkpoint = queue.store.put_object(b'{"next_phase":"validate"}')
    with WriterLease(store, "engine") as lease:
        _, job = admitted(store, queue, lease)
        claim = queue.claim(job, lease)
        assert queue.checkpoint(job, lease, execution_id=claim.execution_id, phase="read", object_digest=checkpoint,
                                resumable=True, pause=True) == "checkpointed"
    with WriterLease(store, "next-engine") as lease:
        assert queue.reconcile_interrupted(lease) == []
        claim = queue.claim(job, lease)
        queue.complete(job, {}, lease, execution_id=claim.execution_id)


def test_cancellation_prevents_further_effects_and_success(state):
    store, queue = state
    checkpoint = queue.store.put_object(b"checkpoint")
    with WriterLease(store, "engine") as lease:
        _, job = admitted(store, queue, lease)
        claim = queue.claim(job, lease)
        queue.request_cancel(job, "User changed the plan", lease)
        with pytest.raises(LaneError):
            queue.prepare_effect(job, "publish", "Publish result", lease, execution_id=claim.execution_id)
        with pytest.raises(LaneError):
            queue.complete(job, {}, lease, execution_id=claim.execution_id)
        assert queue.checkpoint(job, lease, execution_id=claim.execution_id, phase="stopped", object_digest=checkpoint,
                                resumable=False) == "cancelled"


@pytest.mark.parametrize("effect_state,expected", [(None, "checkpointed"), ("prepared", "uncertain"), ("confirmed", "uncertain")])
def test_interrupted_execution_never_blindly_replays_effects(state, effect_state, expected):
    store, queue = state
    evidence = queue.store.put_object(b"checkpoint or external receipt")
    with WriterLease(store, "previous-engine") as lease:
        _, job = admitted(store, queue, lease)
        claim = queue.claim(job, lease)
        queue.checkpoint(job, lease, execution_id=claim.execution_id, phase="working", object_digest=evidence, resumable=True)
        if effect_state:
            effect = queue.prepare_effect(job, "send-once", "External effect", lease, execution_id=claim.execution_id)
            if effect_state == "confirmed":
                queue.confirm_effect(job, effect, evidence, lease, execution_id=claim.execution_id)
    with WriterLease(store, "new-engine") as lease:
        outcomes = queue.reconcile_interrupted(lease)
        assert outcomes == [{"job_id": job, "state": expected, "automatic_replay": False}]
        if effect_state:
            with pytest.raises(LaneError):
                claim = queue.claim(job, lease)


def test_pending_effect_requires_evidence_and_cannot_be_replayed(state):
    store, queue = state
    evidence = queue.store.put_object(b"service receipt")
    with WriterLease(store, "engine") as lease:
        _, job = admitted(store, queue, lease)
        claim = queue.claim(job, lease)
        effect = queue.prepare_effect(job, "publish", "Publish once", lease, execution_id=claim.execution_id)
        with pytest.raises(LaneError) as error:
            queue.complete(job, {}, lease, execution_id=claim.execution_id)
        assert error.value.code == "EFFECT_OUTCOME_UNCERTAIN"
        with pytest.raises(LaneError):
            queue.prepare_effect(job, "publish", "Retry", lease, execution_id=claim.execution_id)
        queue.confirm_effect(job, effect, evidence, lease, execution_id=claim.execution_id)
        queue.complete(job, {}, lease, execution_id=claim.execution_id)
    assert queue.get(job)["effects"][0]["evidence_object"] == evidence


def test_failure_after_effect_and_reconciliation_do_not_report_success(state):
    store, queue = state
    evidence = queue.store.put_object(b"verified absent from external service")
    with WriterLease(store, "engine") as lease:
        _, job = admitted(store, queue, lease)
        claim = queue.claim(job, lease)
        effect = queue.prepare_effect(job, "publish", "Publish once", lease, execution_id=claim.execution_id)
        queue.fail(job, "CONNECTION_LOST", lease, execution_id=claim.execution_id)
        assert queue.get(job)["state"] == "uncertain"
        queue.reconcile_effect(job, effect, "absent", evidence, lease)
    assert queue.get(job)["state"] == "failed"
    assert queue.get(job)["error_code"] == "RECONCILED_REPLAN_REQUIRED"


def test_job_cannot_use_another_projects_lease(state, tmp_path):
    store, queue = state
    other_source = tmp_path / "other-source"
    other_source.mkdir()
    other = ProjectStore.create(tmp_path / "other-state", other_source)
    with WriterLease(other, "engine") as lease, pytest.raises(LaneError) as error:
        admitted(store, queue, lease)
    assert error.value.code == "WRITER_PROJECT_MISMATCH"
