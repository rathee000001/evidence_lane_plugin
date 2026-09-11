import hashlib
import json

from evidence_lane_plugin.agent_learning import LearningStore
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sdk import ActionRequest

from tests import test_delta_entry
from tests.test_delta_entry import call, finished, plan, request
from tests.test_delta_exit import second_task

system = test_delta_entry.system


def learn(system, **arguments):
    return call(system, ActionRequest(action='learning_read', project_id=system[1].project_id, arguments=arguments))


def run_next(system, task_id):
    view = PlanStore(system[1]).task(task_id, expected_revision=1)
    entry = request(system, view)
    entry = entry.model_copy(update={'arguments': {**entry.arguments, 'task_id': task_id}})
    response = call(system, entry)
    assert response.status == 'queued', response.error
    row = finished(system, response.job_id)
    assert row['state'] == 'verified', row
    return row


def test_auto_learning_is_bound_to_verified_exit_and_read_does_not_mutate(system):
    view = plan(system)
    entered = call(system, request(system, view))
    row = finished(system, entered.job_id)
    store = system[1]
    before = hashlib.sha256(store.lane('learning').database.read_bytes()).hexdigest()
    read = learn(system, query='sha256 input')
    assert read.status == 'ok', read.error
    assert len(read.result['lessons']) == 1
    lesson = read.result['lessons'][0]
    assert lesson['state'] == 'active'
    assert lesson['observation']['source_job_id'] == row['job_id']
    assert lesson['observation']['authority'] == 'project_learning'
    assert lesson['observation']['inference_boundary'].startswith('Observed checks')
    assert hashlib.sha256(store.lane('learning').database.read_bytes()).hexdigest() == before
    assert LearningStore(store).verify_history()['verified_events'] == 1


def test_successor_supersedes_learning_version_and_pins_prior_context(system):
    plan(system, additional=[second_task()])
    run_next(system, 'first')
    old = learn(system).result['lessons'][0]
    second = run_next(system, 'second')
    current = learn(system).result['lessons'][0]
    assert current['version'] == 2 and current['version_id'] != old['version_id']
    assert current['lesson_key'] == old['lesson_key']
    historical = learn(system, include_history=True).result['lessons']
    assert {item['state'] for item in historical} == {'active', 'superseded'}
    entry = json.loads(system[1].lane('plan').read_object(second['entry_object']))
    assert entry['learning_context'] == [{'version_id': old['version_id'], 'content_digest': old['content_digest']}]
    assert LearningStore(system[1]).verify_history()['verified_events'] == 3


def test_revocation_preserves_plan_and_suppresses_future_auto_reactivation(system):
    third = second_task().model_copy(update={'task_id': 'third'})
    plan(system, additional=[second_task(), third])
    run_next(system, 'first')
    lesson = learn(system).result['lessons'][0]
    snapshot = PlanStore(system[1]).snapshot().model_dump()
    revoke = ActionRequest(action='learning_revoke', project_id=system[1].project_id, arguments={
        'request_id': '00000000-0000-4000-8000-000000000007', 'version_id': lesson['version_id'],
        'expected_digest': lesson['content_digest'], 'reason': 'This observation should not guide later tasks.'})
    assert call(system, revoke).status == 'ok'
    assert call(system, revoke).status == 'ok'
    assert PlanStore(system[1]).snapshot().model_dump() == snapshot
    assert learn(system).result['lessons'] == []
    run_next(system, 'second')
    assert learn(system).result['lessons'] == []
    history = learn(system, include_history=True).result['lessons']
    assert len(history) == 2 and all(item['state'] == 'revoked' for item in history)
    assert LearningStore(system[1]).verify_history()['verified_events'] == 3


def test_failed_acceptance_never_creates_learned_observation(system):
    view = plan(system)
    entered = call(system, request(system, view, corrupt_output=True))
    assert finished(system, entered.job_id)['state'] == 'blocked'
    assert learn(system).result['lessons'] == []
    with system[1].lane('learning').connection(read_only=True) as connection, system[1].lane('plan').connection(read_only=True), system[1].lane('receipts').connection(read_only=True):
        assert connection.execute('SELECT count(*) FROM learning_versions').fetchone()[0] == 0


def test_learning_failure_rolls_back_entire_verified_exit(system, monkeypatch):
    view = plan(system, additional=[second_task()])
    original = LearningStore.record_exit
    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise LaneError('INJECTED_LEARNING_FAILURE', 'Failure after learning insert')
    monkeypatch.setattr(LearningStore, 'record_exit', fail)
    entered = call(system, request(system, view))
    row = finished(system, entered.job_id)
    assert row['error_code'] == 'INJECTED_LEARNING_FAILURE'
    assert [task.state for task in PlanStore(system[1]).snapshot().tasks] == ['blocked', 'queued']
    with system[1].lane('learning').connection(read_only=True) as connection, system[1].lane('plan').connection(read_only=True) as plan_db, system[1].lane('receipts').connection(read_only=True) as receipts:
        assert connection.execute('SELECT count(*) FROM learning_versions').fetchone()[0] == 0
        assert plan_db.execute('SELECT count(*) FROM delta_exits').fetchone()[0] == 0
        assert receipts.execute("SELECT count(*) FROM receipts WHERE kind='delta_exit_verified'").fetchone()[0] == 0


def test_learning_does_not_cross_project_roots_and_rejects_tampered_scope(system, tmp_path):
    view = plan(system)
    entered = call(system, request(system, view))
    finished(system, entered.job_id)
    engine, store, _, _ = system
    other = engine.directory.register(tmp_path / 'other-state', source_root=store.source_root, create=True, read_only=False)
    other_store = engine.directory.open(other['project_id'])
    before = other_store.lane('learning').database.read_bytes()
    assert LearningStore(other_store).read().lessons == []
    assert other_store.lane('learning').database.read_bytes() == before
    with engine.project_work.mutation(store) as lease, lease.transaction('learning') as connection:
        connection.execute("UPDATE learning_versions SET profile='forged'")
    assert learn(system, profile='forged').error.code == 'LEARNING_INTEGRITY_FAILED'
