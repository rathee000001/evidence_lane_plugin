import hashlib
import time

from evidence_lane_plugin.plan_runtime import PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest

from tests.test_delta_entry import call, finished, plan, request, system
from tests.test_plan_replacement import apply, project, record_steer, replacement

__all__ = ['project', 'system']


def query(system, action, arguments=None, **overrides):
    payload = {'task_id': 'first', 'plan_revision': 1, 'action': action, 'arguments': arguments or {}}
    payload.update(overrides)
    return call(system, ActionRequest(action='delta_query', project_id=system[1].project_id,
                                    expected_revision=1, arguments=payload))


def database_digest(store):
    # The OS-owned lock is unreadable while the worker holds the project writer.
    # Check every persistent lane/root/artifact byte, excluding only that lock.
    return {str(path.relative_to(store.root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in store.root.rglob('*') if path.is_file() and path != store.root / 'writer.lock'}


def test_queries_remain_readonly_while_an_owned_worker_holds_writer(system):
    engine, store, _, _ = system
    view = plan(system)
    gate = store.source_root / 'release'
    entered = call(system, request(system, view, gate=str(gate)))
    try:
        deadline = time.monotonic() + 5
        while engine.workers.status()['submitted'] < 1:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        before = database_digest(store)
        status = query(system, 'delta_status', {'job_id': entered.job_id})
        assert status.status == 'ok', status.error
        assert status.result['result']['state'] == 'running'
        page = query(system, 'plan_read', {'limit': 1})
        assert page.status == 'ok' and page.result['result']['tasks'][0]['state'] == 'active'
        missed = query(system, 'lineage_read', {'query': 'missing words'})
        assert missed.status == 'ok' and missed.result['result']['events'] == []
        assert not missed.result['refresh_performed']
        assert database_digest(store) == before
        assert PlanStore(store).snapshot().counts == {'active': 1}
    finally:
        gate.touch()
    finished(system, entered.job_id)


def test_query_rejects_mutations_and_wrong_revision_without_changes(system):
    plan(system)
    before = database_digest(system[1])
    assert query(system, 'lineage_record', {'kind': 'prompt', 'payload': {'text': 'forbidden'}}).error.code == 'NOT_A_DELTA_QUERY'
    assert query(system, 'delta_query').error.code == 'NOT_A_DELTA_QUERY'
    assert query(system, 'plan_read', {'revision': 2}).error.code == 'QUERY_REVISION_MISMATCH'
    assert query(system, 'plan_read', plan_revision=2).error.code == 'DELTA_REVISION_REQUIRED'
    assert database_digest(system[1]) == before


def test_result_budget_is_explicit_and_never_silently_truncates(system):
    plan(system)
    assert call(system, ActionRequest(action='lineage_record', project_id=system[1].project_id,
        arguments={'kind': 'prompt', 'payload': {'text': 'visible ' * 500}})).status == 'ok'
    before = database_digest(system[1])
    assert query(system, 'lineage_read', max_bytes=1024).error.code == 'QUERY_OUTPUT_BUDGET'
    page = query(system, 'lineage_read')
    assert page.status == 'ok' and len(page.result['result']['events']) == 1
    assert database_digest(system[1]) == before


def test_query_discards_page_when_a_real_revision_change_crosses_its_reads(project, monkeypatch):
    engine, current, _actor, client = project
    steer = record_steer(project, affected=['two'])
    replace_request = replacement(project, steer)
    original = engine.registry.execute

    def cross_revision(name, payload, context):
        result = original(name, payload, context)
        if name == 'plan_read':
            assert result['revision'] == 1
            assert apply(project, replace_request).revision == 2
        return result

    monkeypatch.setattr(engine.registry, 'execute', cross_revision)
    response = client.call('delta_query', project_id=current.store.project_id, expected_revision=1,
        arguments={'task_id': 'two', 'plan_revision': 1, 'action': 'plan_read', 'arguments': {'limit': 1}})
    assert response.error.code == 'STALE_PLAN_REVISION'
    assert response.result is None
    assert current.snapshot().revision == 2


def test_query_cannot_return_another_tasks_run_under_the_selected_task(system):
    second = TaskDefinition(task_id='second', title='Next task', requested_outcome='Next result')
    view = plan(system, additional=[second])
    entered = call(system, request(system, view))
    assert finished(system, entered.job_id)['state'] == 'verified'
    before = database_digest(system[1])
    response = query(system, 'delta_status', {'job_id': entered.job_id}, task_id='second')
    assert response.error.code == 'QUERY_JOB_BINDING_MISMATCH'
    assert response.result is None
    assert database_digest(system[1]) == before
