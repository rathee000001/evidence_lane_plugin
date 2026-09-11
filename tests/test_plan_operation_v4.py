"""Plan-owned operation execution through the actual Delta service and workers."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.plan_runtime import (
    PlanRead,
    PlanStore,
    TaskDefinition,
    TaskOperation,
    content_digest,
)
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import ProjectStore, json_text
from pydantic import ValidationError

from tests.test_code_profile_v4 import call as code_call
from tests.test_code_profile_v4 import code_system, create_plan
from tests.test_delta_entry import call, finished, plan, request, system
from tests.test_plan_replacement import replacement
from tests.test_source_routing_v4 import finished as code_finished
from tests.test_source_routing_v4 import registered

__all__ = ['code_system', 'system']


def planned(system, view, *, revision=1, **kwargs):
    return ActionRequest(action='delta_enter_planned', project_id=system[1].project_id,
        expected_revision=revision, arguments={'task_id': view.definition.task_id, 'plan_revision': revision,
            'contract_digest': view.contract_digest}, **kwargs)


def public_steer(system, *, task_id='first', intent='semantic'):
    captured = call(system, ActionRequest(action='lineage_record', project_id=system[1].project_id,
        arguments={'kind': 'prompt', 'payload': {'text': 'Change the remaining bounded task.'}}))
    assert captured.status == 'ok', captured.error
    return ActionRequest(action='steer_submit', project_id=system[1].project_id, arguments={
        'request_id': str(uuid4()), 'source_event_id': captured.result['event_id'],
        'source_cursor': captured.result['cursor'], 'expected_revision': 1,
        'intent': intent, 'rationale': 'Explicit remaining-work change', 'affected_task_ids': [task_id]})


def test_bound_operation_survives_store_reopen_and_runs_once_with_real_verification(system):
    view = plan(system, operation={'action': 'fixture_hash', 'arguments': {'text': 'stored evidence'}})
    reopened = PlanStore(ProjectStore(system[1].root, read_only=True)).task('first', expected_revision=1)
    assert reopened == view
    submitted = planned(system, reopened)
    response = call(system, submitted)
    assert response.status == 'queued', response.error
    row = finished(system, response.job_id)
    assert row['state'] == 'verified', row
    output = json.loads(system[1].lane('plan').read_object(row['result_object']))
    assert output['result']['bytes'] == len(b'stored evidence')
    assert output['workers'][0]['worker_pid'] > 0
    assert output['contract_digest'] == view.contract_digest
    repeated = call(system, submitted)
    assert repeated.job_id == response.job_id
    assert system[0].workers.status()['submitted'] == 1
    assert PlanStore(system[1]).snapshot().counts == {'completed': 1}
    with system[1].lane('plan').connection(read_only=True) as connection:
        entry = connection.execute('SELECT entry_object FROM delta_runs WHERE job_id=?', (response.job_id,)).fetchone()[0]
        assert connection.execute('SELECT COUNT(*) FROM delta_exits').fetchone()[0] == 1
    admitted = json.loads(system[1].lane('plan').read_object(entry))
    assert admitted['actor_id'] == system[3].client_id
    assert admitted['entry']['arguments'] == {'text': 'stored evidence'}


@pytest.mark.parametrize('change', ['argument', 'action', 'route'])
def test_direct_delta_cannot_override_bound_operation_before_job_creation(system, change):
    view = plan(system, operation={'action': 'fixture_hash', 'arguments': {'text': 'bound'}})
    submitted = request(system, view, text='bound')
    values = dict(submitted.arguments)
    if change == 'argument':
        values['arguments'] = {'text': 'changed'}
    elif change == 'action':
        values['action'], values['arguments'] = 'code_index', {'paths': ['.']}
    else:
        values['source_route'] = {'route_id': 'a' * 64, 'occurrence_ordinals': [1]}
    response = call(system, submitted.model_copy(update={'arguments': values}))
    assert response.error.code == 'PLAN_OPERATION_CHANGED'
    assert PlanStore(system[1]).task('first', expected_revision=1).state == 'queued'
    with system[1].lane('plan').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_runs'").fetchone() is None
    assert system[0].workers.status()['submitted'] == 0


def test_direct_entry_accepts_explicit_schema_defaults_of_the_same_bound_operation(system):
    view = plan(system, operation={'action': 'fixture_hash', 'arguments': {'text': 'same'}})
    submitted = request(system, view, text='same', gate=None, repeat=1, wrong_worker=False,
        fail_worker=False, early_checkpoint=False, corrupt_output=False)
    response = call(system, submitted)
    assert response.status == 'queued', response.error
    assert finished(system, response.job_id)['state'] == 'verified'


def test_planned_entry_requires_a_binding_and_the_exact_revision_and_digest(system):
    view = plan(system)
    assert call(system, planned(system, view)).error.code == 'PLAN_OPERATION_REQUIRED'
    assert call(system, planned(system, view, revision=2)).error.code == 'STALE_PLAN_REVISION'
    incorrect = planned(system, view)
    incorrect = incorrect.model_copy(update={'arguments': dict(incorrect.arguments, contract_digest='a' * 64)})
    assert call(system, incorrect).error.code == 'DELTA_CONTRACT_CHANGED'
    # An unbound historical task can still use its original direct entry.
    response = call(system, request(system, view, text='historical'))
    assert finished(system, response.job_id)['state'] == 'verified'


def test_plan_operation_contract_rejects_out_of_scope_and_oversized_arguments():
    base = {'task_id': 'bound', 'title': 'Bound', 'requested_outcome': 'Verify evidence',
        'allowed_actions': ['fixture_hash']}
    with pytest.raises(ValidationError, match='allowed task action'):
        TaskDefinition(**base, operation=TaskOperation(action='code_index'))
    with pytest.raises(ValidationError, match='input byte budget'):
        TaskDefinition(**base, budget={'max_input_bytes': 10},
            operation=TaskOperation(action='fixture_hash', arguments={'text': 'ééééé'}))
    with pytest.raises(ValidationError):
        TaskOperation(action='fixture_hash', source_route={'route_id': 'a' * 64, 'occurrence_ordinals': [1, 1]})


def test_unbound_contract_keeps_legacy_bytes_while_binding_changes_its_digest():
    # The exact pre-operation serialized contract remains a readable historical input.
    raw = '{"task_id":"old","title":"Old","requested_outcome":"Read history","profile":"core",' \
        '"permitted_paths":[],"permitted_tools":[],"allowed_actions":["fixture_hash"],"acceptance_checks":[],' \
        '"stop_condition":"Stop on failed verification or unavailable required capability.","dependencies":[],' \
        '"plan_group":null,"commit_batch_id":null,"budget":{"max_seconds":300,"max_tool_calls":64,' \
        '"max_input_bytes":1048576,"max_output_bytes":4194304}}'
    original = json.loads(raw)
    task = TaskDefinition.model_validate_json(raw)
    assert json_text(task.model_dump(mode='json')) == json_text(original)
    assert content_digest(task.model_dump(mode='json')) == content_digest(original)
    bound = TaskDefinition.model_validate(dict(original, operation={'action': 'fixture_hash', 'arguments': {'text': 'a'}}))
    changed = TaskDefinition.model_validate(dict(original, operation={'action': 'fixture_hash', 'arguments': {'text': 'b'}}))
    assert len({content_digest(row.model_dump(mode='json')) for row in (task, bound, changed)}) == 3


@pytest.mark.parametrize('failure', ['no_permission', 'tool_scope', 'input_invalid', 'verifier_failed'])
def test_planned_entry_keeps_the_real_authorization_tool_schema_and_verifier_gates(system, failure):
    changes = {'permitted_tools': []} if failure == 'tool_scope' else {}
    arguments = {'text': 'verify', 'corrupt_output': failure == 'verifier_failed'}
    if failure == 'input_invalid':
        arguments['repeat'] = 99
    view = plan(system, operation={'action': 'fixture_hash', 'arguments': arguments}, **changes)
    if failure == 'no_permission':
        from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
        _, readonly = system[0].clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=system[1].project_id, permissions=['read'])]))
        restricted = (*system[:3], readonly)
        response = call(restricted, planned(system, view))
        assert response.status == 'error'
        assert system[0].workers.status()['submitted'] == 0
        return
    response = call(system, planned(system, view))
    if failure == 'verifier_failed':
        row = finished(system, response.job_id)
        assert row['state'] == 'blocked'
        assert PlanStore(system[1]).task('first', expected_revision=1).state != 'completed'
    else:
        assert response.status == 'error'
        assert response.error.code == {'tool_scope': 'DELTA_TOOL_SCOPE', 'input_invalid': 'INVALID_ARGUMENTS'}[failure]
        assert system[0].workers.status()['submitted'] == 0


def test_semantic_steer_rebinds_remaining_operation_and_preserves_completed_contract(system):
    second = TaskDefinition(task_id='second', title='Second', requested_outcome='Second verified input',
        profile='fixture', allowed_actions=['fixture_hash'], permitted_tools=['hashlib'], permitted_paths=['.'],
        acceptance_checks=['sha256_matches_input', 'byte_count_matches_input'],
        operation=TaskOperation(action='fixture_hash', arguments={'text': 'before'}))
    first = plan(system, operation={'action': 'fixture_hash', 'arguments': {'text': 'complete'}}, additional=[second])
    response = call(system, planned(system, first))
    assert finished(system, response.job_id)['state'] == 'verified'
    current_plan = PlanStore(system[1])
    old_second = current_plan.task('second', expected_revision=1)
    project = (system[0], current_plan, system[3].client_id, None)
    from evidence_lane_plugin.steering import SteerIntent
    captured = call(system, ActionRequest(action='lineage_record', project_id=system[1].project_id,
        arguments={'kind': 'prompt', 'payload': {'text': 'Change the second input to after.'}}))
    assert captured.status == 'ok', captured.error
    steer = SteerIntent(source_event_id=captured.result['event_id'], source_cursor=captured.result['cursor'],
        expected_revision=1, intent='semantic', rationale='Explicitly rebind the remaining input', affected_task_ids=['second'])
    submitted = call(system, ActionRequest(action='steer_submit', project_id=system[1].project_id, arguments=steer.model_dump(mode='json')))
    assert submitted.status == 'ok', submitted.error
    assert submitted.result['state'] == 'ready' and submitted.result['plan_changed']
    assert not submitted.result['plan_revision_changed']
    checkpoint = json.loads(system[1].lane('plan').read_object(submitted.result['checkpoint_object']))
    assert checkpoint['task_id'] == 'second' and not checkpoint['job_admitted']
    tasks = [row.definition for row in current_plan.snapshot().tasks]
    tasks[1] = tasks[1].model_copy(update={'operation': TaskOperation(action='fixture_hash', arguments={'text': 'after'})})
    refreshed = call(system, ActionRequest(action='plan_refresh', project_id=system[1].project_id,
        arguments=replacement(project, steer, tasks=tasks, resume_after_change=True).model_dump(mode='json')))
    assert refreshed.status == 'ok', refreshed.error
    updated = current_plan.snapshot()
    assert updated.revision == 2
    assert updated.tasks[0].contract_digest == first.contract_digest and updated.tasks[0].state == 'completed'
    assert current_plan.snapshot(PlanRead(revision=1)).tasks[1].definition.operation.arguments == {'text': 'before'}
    assert call(system, planned(system, old_second)).error.code == 'STALE_PLAN_REVISION'
    response = call(system, planned(system, updated.tasks[1], revision=2))
    assert response.status == 'queued', response.error
    row = finished(system, response.job_id)
    assert row['state'] == 'verified', row
    assert current_plan.snapshot().counts == {'completed': 2}
    assert current_plan.verify_history()['events_verified'] > 0


@pytest.mark.parametrize('changed', [False, True])
def test_plan_bound_source_route_is_consumed_by_actual_code_owner(code_system, changed):
    route = registered(code_system, ['app.py'])
    create_plan(code_system, operation={'action': 'code_index', 'arguments': {'paths': ['app.py']},
        'source_route': {'route_id': route['route_id'], 'occurrence_ordinals': [1]}})
    store = code_system[1]
    view = PlanStore(store).task('code-0', expected_revision=1)
    if changed:
        (store.source_root / 'app.py').write_text('print("changed after source registration")\n')
    response = code_call(code_system, 'delta_enter_planned', {'task_id': 'code-0', 'plan_revision': 1,
        'contract_digest': view.contract_digest}, expected_revision=1, request_id=str(uuid4()))
    if changed:
        assert response.error.code == 'SOURCE_ROUTE_SOURCE_CHANGED'
        assert PlanStore(store).task('code-0', expected_revision=1).state == 'queued'
    else:
        result = code_finished(code_system, response)
        assert result['source_route']['route_id'] == route['route_id']
        assert result['result']['result']['files'] == 1
        assert store.lane('local_code').database != store.lane('plan').database


@pytest.mark.parametrize('state', ['queued', 'uncertain'])
def test_idle_checkpoint_never_crosses_an_admitted_or_uncertain_job(system, state):
    from evidence_lane_plugin.jobs import JobQueue
    view = plan(system, operation={'action': 'fixture_hash', 'arguments': {'text': 'planned'}})
    engine, store, _, session = system
    queue = JobQueue(store)
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).transition('first', 'active', lease, expected_revision=1, actor_id=session.client_id)
        queue.initialize(lease)
        job = queue.enqueue(ActionRequest(action='fixture_hash', project_id=store.project_id, expected_revision=1), session.client_id, lease)
        if state == 'uncertain':
            claimed = queue.claim(job, lease)
            queue.prepare_effect(job, 'selected-effect', 'A fixture effect with unknown outcome', lease, execution_id=claimed.execution_id)
            queue.fail(job, 'FIXTURE_UNKNOWN_EFFECT', lease, execution_id=claimed.execution_id)
    assert not engine.project_work.status()
    response = call(system, public_steer(system))
    assert response.status == 'ok', response.error
    assert response.result['state'] == 'pending' and not response.result['plan_changed']
    assert response.result['checkpoint_object'] is None
    current = PlanStore(store).task('first', expected_revision=1)
    assert current.state == 'active' and current.contract_digest == view.contract_digest
    assert queue.get(job)['state'] == state


def test_failed_idle_checkpoint_rolls_back_steer_classification_and_plan_state(system, monkeypatch):
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.steering import Steering
    plan(system, operation={'action': 'fixture_hash', 'arguments': {'text': 'planned'}})
    engine, store, _, session = system
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).transition('first', 'active', lease, expected_revision=1, actor_id=session.client_id)
    submitted = public_steer(system)
    original = ProjectStore.append_receipt
    def fail(self, kind, *args, **kwargs):
        if kind == 'plan_idle_checkpoint':
            raise LaneError('FIXTURE_CHECKPOINT_FAILURE', 'Injected atomic publication failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ProjectStore, 'append_receipt', fail)
    response = call(system, submitted)
    assert response.error.code == 'FIXTURE_CHECKPOINT_FAILURE'
    assert PlanStore(store).task('first', expected_revision=1).state == 'active'
    assert not Steering(store).pending()
    monkeypatch.setattr(ProjectStore, 'append_receipt', original)
    retried = call(system, submitted)
    assert retried.status == 'ok', retried.error
    assert retried.result['state'] == 'ready' and retried.result['plan_changed']
    assert PlanStore(store).task('first', expected_revision=1).state == 'blocked'
    repeated = call(system, submitted)
    assert repeated.status == 'ok' and not repeated.result['plan_changed']
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM receipts WHERE kind='plan_idle_checkpoint'").fetchone()[0] == 1
