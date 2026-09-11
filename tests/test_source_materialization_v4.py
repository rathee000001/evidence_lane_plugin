"""Source groups execute actual Plan jobs; failures never become coverage."""
from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore

from tests.test_code_profile_v4 import call, code_system
from tests.test_source_preparation_v4 import prepare
from tests.test_source_routing_v4 import registered

__all__ = ['code_system']


def setup(system, *, group_size=1, transform=None):
    route = registered(system, ['.'])
    response = prepare(system, route, lane_options={'local_code': {'max_files': group_size}})
    assert response.status == 'ok', response.error
    prepared = response.result['result']
    tasks = [dict(row) for row in prepared['tasks']]
    if transform:
        transform(tasks)
    response = call(system, 'plan_create', {'title': 'Owning source group', 'tasks': tasks})
    assert response.status == 'ok', response.error
    return prepared, {'preparation_id': prepared['preparation_id'], 'plan_revision': 1,
        'plan_document_digest': PlanStore(system[1]).snapshot().document_digest}


def finished(system, response):
    assert response.status == 'ok', response.error
    run_id = response.result['result']['run_id']
    system[0].source_materialization._drivers[run_id].result(timeout=100)
    # Direct owning read remains available during drain; ordinary SDK admission
    # is intentionally unavailable then. All other cases use the public SDK.
    if system[0].phase == 'running':
        read = call(system, 'source_materialization_read', {'run_id': run_id})
        assert read.status == 'ok', read.error
        return read.result['result']
    owner = system[0].source_materialization
    return owner._status(system[1], owner._row(system[1], run_id))


def start(system, request, **kwargs):
    return call(system, 'source_materialize', request, expected_revision=request['plan_revision'], **kwargs)


def test_group_runs_all_children_and_checks_current_original_bytes(code_system):
    prepared, request = setup(code_system)
    admission = start(code_system, request)
    result = finished(code_system, admission)
    assert result['state'] == 'completed', result
    assert result['outcome']['file_count'] == result['outcome']['task_count'] == 3
    assert result['outcome']['coverage_owner_calls'] == 4
    assert result['outcome']['source_selection_rechecked'] and result['outcome']['stored_original_bytes_verified']
    assert {row['path']: row['sha256'] for row in result['outcome']['files']} == {
        row['path']: row['sha256'] for row in prepared['files']}
    assert all(row['owned_request'] and row['state'] == 'verified' for row in result['jobs'])
    assert PlanStore(code_system[1]).snapshot().counts == {'completed': 3}
    assert PlanStore(code_system[1]).verify_history()['events_verified'] > 0
    count = code_system[0].workers.status()['submitted']
    replay = start(code_system, request, request_id=admission.request_id)
    assert replay.status == 'ok' and replay.result['result']['state'] == 'completed'
    assert code_system[0].workers.status()['submitted'] == count
    conflict = start(code_system, dict(request, max_seconds=100), request_id=admission.request_id)
    assert conflict.error.code == 'SOURCE_GROUP_REQUEST_CONFLICT'


def test_group_never_executes_the_unselected_successor(code_system):
    def suffix(tasks):
        tasks.append(dict(tasks[-1], task_id='later-task', plan_group='different-group'))
    _, request = setup(code_system, transform=suffix)
    result = finished(code_system, start(code_system, request))
    assert result['state'] == 'completed', result
    assert len(result['jobs']) == 3
    snapshot = PlanStore(code_system[1]).snapshot()
    assert snapshot.counts == {'completed': 3, 'active': 1}
    assert snapshot.tasks[-1].definition.task_id == 'later-task'


@pytest.mark.parametrize('case,code', [
    ('contract', 'SOURCE_GROUP_TASK_CHANGED'), ('order', 'SOURCE_GROUP_ORDER'),
    ('dependency', 'SOURCE_GROUP_ORDER'), ('document', 'SOURCE_GROUP_PLAN_CHANGED'),
    ('stale_source', 'SOURCE_ROUTE_SOURCE_CHANGED'), ('revision', 'STALE_PLAN_REVISION'),
])
def test_invalid_group_admission_starts_no_worker(code_system, case, code):
    def change(tasks):
        if case == 'contract':
            tasks[0]['title'] = 'An unapproved changed contract'
        if case == 'order':
            tasks.reverse()
        if case == 'dependency':
            tasks[1]['dependencies'] = []
    _, request = setup(code_system, transform=change)
    if case == 'document':
        request['plan_document_digest'] = '0' * 64
    if case == 'revision':
        request['plan_revision'] = 2
    if case == 'stale_source':
        (code_system[1].source_root / 'new.py').write_text('NEW = True')
    response = start(code_system, request)
    assert response.error.code == code, response
    assert code_system[0].workers.status()['submitted'] == 0
    assert PlanStore(code_system[1]).snapshot().counts == {'queued': 3}


def test_read_only_client_cannot_start_a_source_group(code_system):
    _, request = setup(code_system)
    engine, store, _ = code_system
    _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id, permissions=['read'])]))
    response = start((engine, store, session), request)
    # The client router refuses to select this project for a write action.
    assert response.error.code == 'PROJECT_NOT_SELECTED'
    assert engine.workers.status()['submitted'] == 0


def test_source_change_between_tasks_stops_the_remaining_group(code_system, monkeypatch):
    _, request = setup(code_system)
    engine, store, _ = code_system
    original = engine.registry.execute
    calls = 0
    def execute(action, arguments, context):
        nonlocal calls
        if action == 'delta_enter_planned':
            calls += 1
            if calls == 2:
                (store.source_root / 'helper.py').write_text('def changed(): pass\n')
        return original(action, arguments, context)
    monkeypatch.setattr(engine.registry, 'execute', execute)
    result = finished(code_system, start(code_system, request))
    assert result['state'] == 'blocked' and result['outcome']['error_code'] == 'SOURCE_ROUTE_SOURCE_CHANGED', result
    assert len(result['jobs']) == 1 and result['jobs'][0]['state'] == 'verified'
    assert not result['outcome']['materialized']


def test_semantic_steer_after_first_exit_prevents_the_next_admission(code_system, monkeypatch):
    prepared, request = setup(code_system)
    engine = code_system[0]
    original = engine.registry.execute
    count = 0
    def execute(action, arguments, context):
        nonlocal count
        if action == 'delta_enter_planned':
            count += 1
            if count == 2:
                captured = call(code_system, 'lineage_record', {'kind': 'prompt', 'payload': {'text': 'Change the remaining source work.'}})
                assert captured.status == 'ok', captured.error
                steer = call(code_system, 'steer_submit', {'source_event_id': captured.result['event_id'],
                    'source_cursor': captured.result['cursor'], 'expected_revision': 1, 'intent': 'semantic',
                    'rationale': 'Change the selected source work', 'affected_task_ids': [prepared['tasks'][1]['task_id']]})
                assert steer.status == 'ok' and steer.result['state'] == 'ready', steer
        return original(action, arguments, context)
    monkeypatch.setattr(engine.registry, 'execute', execute)
    result = finished(code_system, start(code_system, request))
    assert result['state'] == 'blocked', result
    assert result['outcome']['error_code'] in {'DELTA_TASK_NOT_RUNNABLE', 'PLAN_STEER_PENDING'}
    assert len(result['jobs']) == 1
    assert PlanStore(code_system[1]).snapshot().counts == {'completed': 1, 'blocked': 1, 'queued': 1}


@pytest.mark.parametrize('case,code', [('membership', 'SOURCE_ROUTE_SOURCE_CHANGED'),
    ('budget', 'SOURCE_GROUP_COVERAGE_BUDGET'), ('selector', 'SOURCE_GROUP_SELECTOR_CHANGED')])
def test_all_verified_jobs_do_not_replace_final_coverage(code_system, monkeypatch, case, code):
    _, request = setup(code_system)
    engine, store, _ = code_system
    original = engine.source_materialization._coverage
    def coverage(*args):
        if case == 'membership':
            (store.source_root / 'late.py').write_text('LATE = True')
        return original(*args)
    monkeypatch.setattr(engine.source_materialization, '_coverage', coverage)
    if case == 'budget':
        request['max_coverage_calls'] = 1
    if case == 'selector':
        invoke = engine.registry.execute
        def execute(action, arguments, context):
            value = invoke(action, arguments, context)
            if action == 'code_current':
                value['result']['scopes'] = []
            return value
        monkeypatch.setattr(engine.registry, 'execute', execute)
    result = finished(code_system, start(code_system, request))
    assert result['state'] == 'blocked' and result['outcome']['error_code'] == code, result
    assert len(result['jobs']) == 3 and all(row['state'] == 'verified' for row in result['jobs'])
    assert PlanStore(store).snapshot().counts == {'completed': 3}
    assert not result['outcome']['materialized']


def test_drain_joins_the_current_child_and_blocks_remaining_source_work(code_system, monkeypatch):
    _, request = setup(code_system)
    engine = code_system[0]
    completion = engine.delta.owned_completion
    class JoinAndDrain:
        def __init__(self, future):
            self.future = future
        def result(self):
            self.future.result(timeout=40)
            engine.begin_drain()
    monkeypatch.setattr(engine.delta, 'owned_completion', lambda job_id: JoinAndDrain(completion(job_id)))
    result = finished(code_system, start(code_system, request))
    assert result['state'] == 'blocked' and result['outcome']['error_code'] == 'ENGINE_DRAINING', result
    assert len(result['jobs']) == 1
    assert all(future.done() for future in engine.delta._drivers.values())


def test_active_replay_competition_and_read_do_not_start_another_group(code_system, monkeypatch):
    _, request = setup(code_system)
    engine = code_system[0]
    entered, release = threading.Event(), threading.Event()
    run = engine.source_materialization._run
    def held(*args):
        entered.set()
        assert release.wait(30)
        return run(*args)
    monkeypatch.setattr(engine.source_materialization, '_run', held)
    admission = start(code_system, request)
    try:
        assert entered.wait(10)
        replay = start(code_system, request, request_id=admission.request_id)
        assert replay.status == 'ok' and replay.result['result']['driver_status'] == 'owned'
        assert start(code_system, request).error.code == 'SOURCE_GROUP_ACTIVE'
        read = call(code_system, 'source_materialization_read', {'run_id': admission.request_id})
        assert read.status == 'ok' and read.result['result']['jobs'] == []
        reconcile = call(code_system, 'source_materialization_reconcile', {'run_id': admission.request_id})
        assert reconcile.error.code == 'SOURCE_GROUP_OWNED'
    finally:
        release.set()
    assert finished(code_system, admission)['state'] == 'completed'


def test_unowned_group_requires_explicit_reconciliation_without_execution(code_system, monkeypatch):
    _, request = setup(code_system)
    engine = code_system[0]
    # A returned owner without a terminal publication simulates a lost group
    # driver. No task is run or fabricated as successfully completed.
    monkeypatch.setattr(engine.source_materialization, '_run', lambda *args: None)
    admission = start(code_system, request)
    read = finished(code_system, admission)
    assert read['state'] == 'active' and read['driver_status'] == 'owner_unavailable'
    assert start(code_system, request, request_id=admission.request_id).result['result']['driver_status'] == 'owner_unavailable'
    result = call(code_system, 'source_materialization_reconcile', {'run_id': admission.request_id})
    assert result.status == 'ok' and result.result['result']['state'] == 'blocked'
    assert result.result['result']['outcome']['error_code'] == 'SOURCE_GROUP_OWNER_UNAVAILABLE'
    assert engine.workers.status()['submitted'] == 0


def test_failed_driver_dispatch_has_one_blocked_receipt(code_system, monkeypatch):
    _, request = setup(code_system)
    def fail(target):
        raise RuntimeError('Fixture driver launch failure')
    monkeypatch.setattr(code_system[0], 'start_job', fail)
    response = start(code_system, request, request_id=str(uuid4()))
    assert response.error.code == 'SOURCE_GROUP_DISPATCH_FAILED'
    read = call(code_system, 'source_materialization_read', {'run_id': response.request_id})
    assert read.result['result']['state'] == 'blocked'
    assert code_system[0].workers.status()['submitted'] == 0


def test_owned_engine_completion_contains_exceptions_and_cannot_be_cancelled(tmp_path):
    with Engine(tmp_path / 'runtime') as engine:
        release = threading.Event()
        def target():
            assert release.wait(10)
            raise ValueError('Attributed child failure')
        with engine.admit():
            future = engine.start_job(target)
            assert not future.cancel()
        release.set()
        with pytest.raises(ValueError, match='Attributed child failure'):
            future.result(timeout=10)
        assert engine._background_jobs == 0
        with pytest.raises(LaneError) as error:
            engine.delta.owned_completion(str(uuid4()))
        assert error.value.code == 'DELTA_OWNER_UNAVAILABLE'
