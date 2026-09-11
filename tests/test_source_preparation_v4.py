"""Complete source selection preparation through the real SDK and lane owners."""
from __future__ import annotations

import json
import subprocess
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import ProjectStore

from tests.test_code_profile_v4 import call, code_system
from tests.test_source_routing_v4 import finished, registered

__all__ = ['code_system']


def prepare(system, route, **values):
    return call(system, 'source_prepare_tasks', {'selection': {
        'route_id': route['route_id'], 'occurrence_ordinals': [1]}, **values})


def counts(store):
    with store.lane('sources').connection(read_only=True) as connection:
        return {name: connection.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0]
            for name in ('intake_batch', 'source_object', 'sourceroutes_receipts', 'sourceroutes_preparations')}


def test_directory_preparation_becomes_a_normal_plan_bound_verified_code_task(code_system):
    engine, store, _ = code_system
    route = registered(code_system, ['.'])
    result = prepare(code_system, route)
    assert result.status == 'ok', result.error
    body = result.result['result']
    assert body['file_count'] == 3 and body['task_count'] == 1
    assert body['files_omitted'] == 0 and not body['materialized'] and not body['plan_changed']
    assert PlanStore(store).snapshot().state == 'no_plan'
    assert engine.workers.status()['submitted'] == 0
    task = body['tasks'][0]
    assert task['operation']['action'] == 'code_index'
    assert task['permitted_paths'] == ['app.py', 'helper.py', 'package.json']
    assert task['operation']['arguments']['paths'] == task['permitted_paths']
    assert task['operation']['source_route']['occurrence_ordinals'] == [1, 2, 3]
    assert all(row['parents'][0]['member_path'] == row['path'] for row in body['files'])
    created = call(code_system, 'plan_create', {'title': 'Prepared sources', 'tasks': body['tasks']})
    assert created.status == 'ok', created.error
    view = PlanStore(store).task(task['task_id'], expected_revision=1)
    output = finished(code_system, call(code_system, 'delta_enter_planned', {
        'task_id': task['task_id'], 'plan_revision': 1, 'contract_digest': view.contract_digest}, expected_revision=1))
    assert output['result']['result']['files'] == 3
    assert PlanStore(store).snapshot().counts == {'completed': 1}
    assert store.lane('sources').database != store.lane('local_code').database
    assert PlanStore(store).verify_history()['events_verified'] > 0


def test_automatic_members_keep_specialty_routes_and_exact_overrides(code_system):
    store = code_system[1]
    # Classification and preparation do not parse or claim format validity.
    for name, content in {'rows.csv': b'x,y\n1,2\n', 'evidence.md': b'Evidence text',
                          'model.bim': b'{}', 'image.png': b'not parsed yet'}.items():
        (store.source_root / name).write_bytes(content)
    (store.source_root / '.env').write_text('EXCLUDED=value')
    route = registered(code_system, ['.'])
    result = prepare(code_system, route, overrides={'evidence.md': 'research'})
    assert result.status == 'ok', result.error
    body = result.result['result']
    assert body['file_count'] == 7
    assert {row['path']: row['lane_id'] for row in body['files']} == {
        'app.py': 'local_code', 'helper.py': 'local_code', 'package.json': 'local_code',
        'rows.csv': 'data', 'evidence.md': 'research', 'model.bim': 'power_bi', 'image.png': 'images_ocr'}
    assert '.env' not in json.dumps(body)
    assert all(task['operation']['action'] != 'code_index' or len(task['permitted_paths']) == 3 for task in body['tasks'])
    assert not body['tool_readiness_verified'] and not body['materialized']
    assert counts(store)['sourceroutes_preparations'] == 1


def test_specific_registered_file_wins_over_directory_and_overlap_is_not_duplicated(code_system):
    store = code_system[1]
    route = registered(code_system, ['.', 'app.py'], overrides={str(store.source_root / 'app.py'): 'artifacts'})
    result = call(code_system, 'source_prepare_tasks', {'selection': {
        'route_id': route['route_id'], 'occurrence_ordinals': [1, 2]}})
    assert result.status == 'ok', result.error
    body = result.result['result']
    assert body['file_count'] == 3
    row = next(row for row in body['files'] if row['path'] == 'app.py')
    assert row['lane_id'] == 'artifacts' and len(row['parents']) == 2
    assert row['routing_basis'] == 'registered_file_occurrence'


@pytest.mark.parametrize('case,code', [
    ('stale', 'SOURCE_ROUTE_SOURCE_CHANGED'), ('missing_ordinal', 'SOURCE_ROUTE_OCCURRENCE_MISSING'),
    ('unknown_override', 'SOURCE_PREPARE_OVERRIDE_SCOPE'), ('excluded_override', 'SOURCE_PREPARE_OVERRIDE_SCOPE'),
    ('path_option', 'SOURCE_PREPARE_OPTIONS_SCOPE'), ('unknown_lane_option', 'SOURCE_PREPARE_OPTIONS_SCOPE'),
    ('invalid_option', 'SOURCE_PREPARE_OPERATION_REQUIRED'), ('small_capture', 'SOURCE_CAPTURE_FILE_BUDGET'),
    ('small_parser', 'SOURCE_PREPARE_PARSER_BUDGET'),
])
def test_invalid_preparation_leaves_no_partial_source_batches(code_system, case, code):
    store = code_system[1]
    (store.source_root / '.env').write_text('EXCLUDED=value')
    route = registered(code_system, ['.'])
    before = counts(store)
    values = {'selection': {'route_id': route['route_id'], 'occurrence_ordinals': [1]}}
    if case == 'stale':
        (store.source_root / 'app.py').write_text('changed')
    elif case == 'missing_ordinal':
        values['selection']['occurrence_ordinals'] = [2]
    elif case in {'unknown_override', 'excluded_override'}:
        values['overrides'] = {'.env' if case == 'excluded_override' else 'missing.py': 'custom'}
    elif case == 'path_option':
        values['lane_options'] = {'local_code': {'paths': ['.']}}
    elif case == 'unknown_lane_option':
        values['lane_options'] = {'research': {}}
    elif case == 'invalid_option':
        values['lane_options'] = {'local_code': {'max_files': 999999}}
    elif case == 'small_parser':
        values['lane_options'] = {'local_code': {'max_file_bytes': 1}}
    else:
        values['max_files'] = 2
    result = call(code_system, 'source_prepare_tasks', values)
    assert result.status == 'error' and result.error.code == code, result
    assert counts(store) == before
    assert PlanStore(store).snapshot().state == 'no_plan'


def test_preparation_replay_is_immutable_and_conflicting_reuse_is_rejected(code_system):
    engine, store, session = code_system
    route = registered(code_system, ['.'])
    request = ActionRequest(action='source_prepare_tasks', project_id=store.project_id, request_id=str(uuid4()),
        arguments={'selection': {'route_id': route['route_id'], 'occurrence_ordinals': [1]}})
    first = PublicActionSDKDispatcher(engine).execute(request, session)
    assert first.status == 'ok', first.error
    before = counts(store)
    (store.source_root / 'app.py').write_text('changed after historical preparation')
    replay = PublicActionSDKDispatcher(engine).execute(request, session)
    assert replay.result == first.result and counts(store) == before
    conflict = PublicActionSDKDispatcher(engine).execute(request.model_copy(update={
        'arguments': request.arguments | {'max_files': 20}}), session)
    assert conflict.error.code == 'SOURCE_PREPARATION_REQUEST_CONFLICT'
    from evidence_lane_plugin.source_preparation import read_preparation
    assert read_preparation(ProjectStore(store.root, read_only=True),
        first.result['result']['preparation_id']) == first.result['result']


def test_failure_after_child_publication_rolls_back_sources_and_receipts(code_system, monkeypatch):
    import evidence_lane_plugin.source_preparation as module
    store = code_system[1]
    route = registered(code_system, ['.'])
    before = counts(store)
    original = module.publish_batch_route

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise LaneError('FIXTURE_PREPARE_FAIL', 'Injected failure after child route publication.')

    monkeypatch.setattr(module, 'publish_batch_route', fail)
    result = prepare(code_system, route)
    assert result.error.code == 'FIXTURE_PREPARE_FAIL'
    assert counts(store) == before
    monkeypatch.setattr(module, 'publish_batch_route', original)
    assert prepare(code_system, route).status == 'ok'


def test_member_change_during_compile_prevents_preparation_commit(code_system, monkeypatch):
    import evidence_lane_plugin.source_preparation as module
    store = code_system[1]
    route = registered(code_system, ['.'])
    before = counts(store)
    original = module._compile

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (store.source_root / 'new.py').write_text('print(1)')
        return result

    monkeypatch.setattr(module, '_compile', mutate)
    result = prepare(code_system, route)
    assert result.error.code == 'SOURCE_ROUTE_SOURCE_CHANGED'
    assert counts(store) == before


def test_more_than_one_source_batch_is_complete_and_code_groups_remain_bounded(code_system):
    store = code_system[1]
    for index in range(260):
        (store.source_root / f'file_{index:03}.py').write_text('pass\n')
    route = registered(code_system, ['.'])
    result = prepare(code_system, route)
    assert result.status == 'ok', result.error
    body = result.result['result']
    assert body['file_count'] == 263 and body['task_count'] == 9
    assert len({row['batch_id'] for row in body['files']}) == 2
    assert len({row['path'] for row in body['files']}) == 263
    assert sum(len(task['operation']['arguments']['paths']) for task in body['tasks']) == 263
    assert all(len(task['operation']['source_route']['occurrence_ordinals']) <= 32 for task in body['tasks'])


def test_stale_prepared_bytes_are_rejected_at_normal_delta_entry(code_system):
    store = code_system[1]
    result = prepare(code_system, registered(code_system, ['.']))
    assert result.status == 'ok', result.error
    assert call(code_system, 'plan_create', {'title': 'Prepared', 'tasks': result.result['result']['tasks']}).status == 'ok'
    view = PlanStore(store).snapshot().tasks[0]
    (store.source_root / 'app.py').write_text('changed')
    admission = call(code_system, 'delta_enter_planned', {'task_id': view.definition.task_id, 'plan_revision': 1,
        'contract_digest': view.contract_digest}, expected_revision=1)
    assert admission.error.code == 'SOURCE_ROUTE_SOURCE_CHANGED'
    assert code_system[0].workers.status()['submitted'] == 0


def test_code_grouping_honors_the_owning_parser_file_limit(code_system):
    result = prepare(code_system, registered(code_system, ['.']), lane_options={'local_code': {'max_files': 2}})
    assert result.status == 'ok', result.error
    assert sorted(len(task['operation']['arguments']['paths']) for task in result.result['result']['tasks']) == [1, 2]


def test_github_preparation_preserves_checkpoint_and_its_declared_verifier_scope(code_system):
    store = code_system[1]
    for arguments in (['init', '-b', 'main'], ['config', 'user.name', 'Fixture'],
        ['config', 'user.email', 'fixture@example.invalid'], ['config', 'core.autocrlf', 'false'],
        ['add', '.'], ['commit', '-m', 'Source preparation checkpoint']):
        subprocess.run(['git', '-C', str(store.source_root), *arguments], check=True, capture_output=True)
    route = registered(code_system, ['.'], code_mode='github_code')
    missing = prepare(code_system, route)
    assert missing.error.code == 'SOURCE_PREPARE_OPERATION_REQUIRED'
    checkpoint = call(code_system, 'source_git_history', {'batch_id': route['batch_id'], 'occurrence_ordinal': 1})
    assert checkpoint.status == 'ok', checkpoint.error
    result = prepare(code_system, route, lane_options={'github_code': {
        'git_snapshot_id': checkpoint.result['result']['snapshot_id']}})
    assert result.status == 'ok', result.error
    tasks = result.result['result']['tasks']
    assert len(tasks) == 1 and tasks[0]['permitted_paths'] == ['app.py', 'helper.py', 'package.json', '.']
    assert tasks[0]['operation']['action'] == 'code_index_git'
    assert call(code_system, 'plan_create', {'title': 'Git preparation', 'tasks': tasks}).status == 'ok'
    view = PlanStore(store).task(tasks[0]['task_id'], expected_revision=1)
    output = finished(code_system, call(code_system, 'delta_enter_planned', {'task_id': view.definition.task_id,
        'plan_revision': 1, 'contract_digest': view.contract_digest}, expected_revision=1))
    assert output['result']['result']['files'] == 3


def test_every_retained_sector_has_an_explicit_initial_index_owner(code_system):
    from evidence_lane_plugin.lanes import SECTOR_LANE_IDS
    specs = code_system[0].registry.materialization_actions()
    assert len(specs) == 16
    assert {lane for spec in specs for lane in spec.source_lanes} == set(SECTOR_LANE_IDS)
    assert all(spec.requires_delta and spec.verifier and spec.workflow == 'source-intake' for spec in specs)
    assert {spec.name for spec in specs if spec.materialization.auxiliary_paths} == {'code_index_git'}
