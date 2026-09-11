import json

from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.plan_runtime import PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest

from tests.test_delta_entry import call, finished, plan, request, system

__all__ = ['system']


def second_task():
    return TaskDefinition(task_id='second', title='Continue', requested_outcome='Next verified digest', profile='fixture',
        allowed_actions=['fixture_hash'], permitted_tools=['hashlib'], permitted_paths=['.'],
        acceptance_checks=['sha256_matches_input', 'byte_count_matches_input'])


def test_verified_exit_completes_exact_task_and_selects_successor_without_running_it(system):
    view = plan(system, additional=[second_task()])
    response = call(system, request(system, view))
    row = finished(system, response.job_id)
    assert row['state'] == 'verified', row
    engine, store, _, _ = system
    tasks = PlanStore(store).snapshot().tasks
    assert [task.state for task in tasks] == ['completed', 'active']
    assert engine.workers.status()['submitted'] == 1
    with store.lane('plan').connection(read_only=True) as connection, store.lane('receipts').connection(read_only=True) as receipts:
        exit_row = dict(connection.execute('SELECT * FROM delta_exits WHERE job_id=?', (row['job_id'],)).fetchone())
        receipt = receipts.execute('SELECT * FROM receipts WHERE receipt_id=?', (exit_row['receipt_id'],)).fetchone()
        body = json.loads(receipt['body_json'])
    assert exit_row['next_task_id'] == 'second'
    assert body['status'] == 'passed' and body['contract_digest'] == view.contract_digest
    checks = json.loads(store.lane('plan').read_object(exit_row['verification_object']))
    assert all(check['passed'] for check in checks['checks'])
    assert checks['usage']['tool_calls'] == 3
    assert not checks['native_host_tools_attested']
    assert JobQueue(store).get(row['job_id'])['state'] == 'succeeded'
    PlanStore(store).verify_history()


def test_typed_but_incorrect_result_fails_real_acceptance_and_preserves_next_row(system):
    view = plan(system, additional=[second_task()])
    response = call(system, request(system, view, corrupt_output=True))
    row = finished(system, response.job_id)
    assert row['error_code'] == 'DELTA_ACCEPTANCE_FAILED', row
    store = system[1]
    assert [task.state for task in PlanStore(store).snapshot().tasks] == ['blocked', 'queued']
    with store.lane('plan').connection(read_only=True) as connection, store.lane('receipts').connection(read_only=True) as receipts:
        assert connection.execute('SELECT count(*) FROM delta_exits').fetchone()[0] == 0
        failed = receipts.execute("SELECT body_json FROM receipts WHERE kind='delta_verification_failed'").fetchone()
    details = json.loads(store.lane('plan').read_object(json.loads(failed[0])['verification_object']))
    assert any(not check['passed'] for check in details['checks'])


def test_unknown_or_missing_acceptance_check_rejects_before_worker(system):
    view = plan(system, acceptance_checks=['unregistered-check'])
    response = call(system, request(system, view))
    assert response.error.code == 'DELTA_VERIFIER_UNAVAILABLE'
    assert system[0].workers.status()['submitted'] == 0
    assert PlanStore(system[1]).snapshot().counts == {'queued': 1}


def test_exit_failure_after_plan_change_rolls_back_receipt_job_and_successor(system, monkeypatch):
    view = plan(system, additional=[second_task()])
    original = PlanStore.transition
    def fail(self, task_id, state, *args, **kwargs):
        result = original(self, task_id, state, *args, **kwargs)
        if task_id == 'second' and state == 'active':
            raise LaneError('INJECTED_EXIT_FAILURE', 'Failure after successor activation')
        return result
    monkeypatch.setattr(PlanStore, 'transition', fail)
    response = call(system, request(system, view))
    row = finished(system, response.job_id)
    assert row['error_code'] == 'INJECTED_EXIT_FAILURE', row
    store = system[1]
    assert [task.state for task in PlanStore(store).snapshot().tasks] == ['blocked', 'queued']
    assert JobQueue(store).get(row['job_id'])['state'] == 'checkpointed'
    with store.lane('plan').connection(read_only=True) as connection, store.lane('receipts').connection(read_only=True) as receipts:
        assert receipts.execute("SELECT count(*) FROM receipts WHERE kind='delta_exit_verified'").fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM delta_exits').fetchone()[0] == 0
    PlanStore(store).verify_history()


def test_semantic_steer_at_verifier_boundary_prevents_old_contract_completion(system, monkeypatch):
    view = plan(system, additional=[second_task()])
    engine, store, _, _ = system
    original = engine.delta_exit.finish
    def steer(execution, request, context, result_object):
        source = call(system, ActionRequest(action='lineage_record', project_id=store.project_id,
            arguments={'kind': 'prompt', 'payload': {'text': 'Change the pending work'}}))
        response = call(system, ActionRequest(action='steer_submit', project_id=store.project_id, arguments={
            'source_event_id': source.result['event_id'], 'source_cursor': source.result['cursor'], 'expected_revision': 1,
            'intent': 'semantic', 'rationale': 'Changed requirements', 'affected_task_ids': ['first']}))
        assert response.status == 'ok'
        return original(execution, request, context, result_object)
    monkeypatch.setattr(engine.delta_exit, 'finish', steer)
    response = call(system, request(system, view))
    row = finished(system, response.job_id)
    assert row['error_code'] == 'JOB_CHECKPOINT_REQUIRED', row
    assert [task.state for task in PlanStore(store).snapshot().tasks] == ['blocked', 'queued']
    with store.lane('plan').connection(read_only=True) as connection, store.lane('receipts').connection(read_only=True) as _receipts:
        assert connection.execute('SELECT count(*) FROM delta_exits').fetchone()[0] == 0
