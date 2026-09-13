"""Original source route choices through exact receipts and owning operations."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sdk import ActionRequest

from .test_code_profile_v4 import call, code_system, create_plan
from .test_tabular_profile_v4 import plan as tabular_plan
from .test_tabular_profile_v4 import tabular_system

__all__ = ['code_system', 'tabular_system']


def registered(system, paths, *, action='source_register', **arguments):
    result = call(system, action, {'sources': [str(system[1].source_root / path) for path in paths], **arguments})
    assert result.status == 'ok', result.error
    return result.result['result']['source_routes']


def enter(system, route, *, action='code_index', arguments=None, index=0, ordinals=(1,)):
    task = PlanStore(system[1]).task('code-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments or {'paths': ['.']},
        'source_route': {'route_id': route, 'occurrence_ordinals': list(ordinals)}}, expected_revision=1)


def finished(system, admission):
    assert admission.status == 'queued', admission.error
    engine, store, _ = system
    # Observe completion of this fixture's owned driver before opening a
    # published Root PV read; a live coordinated commit is not recovery proof.
    with engine._admission:
        assert engine._admission.wait_for(lambda: engine._background_jobs == 0, timeout=35), 'The owned Delta driver did not finish.'
    with store.lane('plan').connection(read_only=True) as connection:
        row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (admission.job_id,)).fetchone())
    assert row['state'] == 'verified', row
    return json.loads(store.lane('plan').read_object(row['result_object']))


def test_route_inheritance_tracks_changed_deleted_new_and_explicit_sources(code_system):
    store = code_system[1]
    first = registered(code_system, ['app.py', 'helper.py'], action='lane_configure_routes',
        overrides={str(store.source_root / 'app.py'): 'artifacts'})
    (store.source_root / 'helper.py').unlink()
    (store.source_root / 'app.py').write_text('print("changed")\n')
    (store.source_root / 'new.py').write_text('print("new")\n')
    second = registered(code_system, ['app.py', 'new.py'], parent_route_id=first['route_id'])
    assert second['inherited_sources'] == [str(store.source_root / 'app.py')]
    assert second['omitted_parent_sources'] == [str(store.source_root / 'helper.py')]
    current = call(code_system, 'source_routes_read', {'route_id': second['route_id']})
    assert current.status == 'ok', current.error
    rows = current.result['result']['routes']
    assert [row['lane_id'] for row in rows] == ['artifacts', 'local_code']
    third = registered(code_system, ['app.py'], action='lane_configure_routes',
        parent_route_id=second['route_id'], overrides={str(store.source_root / 'app.py'): 'local_code'})
    assert third['inherited_sources'] == []
    old = call(code_system, 'source_routes_read', {'route_id': first['route_id']})
    assert old.status == 'ok' and len(old.result['result']['routes']) == 2
    assert old.result['result']['routes'][0]['identity_sha256'] != rows[0]['identity_sha256']
    assert len({first['route_id'], second['route_id'], third['route_id']}) == 3


def test_named_configuration_replay_is_one_consumption_and_never_recaptures(code_system):
    from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
    engine, store, session = code_system
    arguments = {'sources': [str(store.source_root / 'app.py')],
        'overrides': {str(store.source_root / 'app.py'): 'local_code'}}
    request = ActionRequest(request_id=str(uuid4()), action='lane_configure_routes', project_id=store.project_id, arguments=arguments)
    first = PublicActionSDKDispatcher(engine).execute(request, session)
    assert first.status == 'ok', first.error
    (store.source_root / 'app.py').write_text('print("different bytes")\n')
    replay = PublicActionSDKDispatcher(engine).execute(request, session)
    assert replay.status == 'ok' and replay.result == first.result
    conflict = PublicActionSDKDispatcher(engine).execute(request.model_copy(update={
        'arguments': arguments | {'overrides': {str(store.source_root / 'app.py'): 'artifacts'}}}), session)
    assert conflict.error.code == 'SOURCE_ROUTE_REQUEST_CONFLICT'
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM sourceroutes_requests').fetchone()[0] == 1
        assert connection.execute('SELECT count(*) FROM intake_batch').fetchone()[0] == 1
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='source_sdk_action'").fetchone()[0] == 1


def test_code_consumes_directory_route_and_excludes_more_specific_other_lane(code_system):
    store = code_system[1]
    receipt = registered(code_system, ['.', 'helper.py'], action='lane_configure_routes',
        overrides={str(store.source_root / 'helper.py'): 'artifacts'})
    create_plan(code_system)
    result = finished(code_system, enter(code_system, receipt['route_id']))
    assert result['source_route']['route_id'] == receipt['route_id']
    assert result['source_route']['input_file_count'] == 2
    assert result['result']['result']['files'] == 2
    snapshot = result['result']['result']['snapshot_id']
    read = call(code_system, 'code_query', {'snapshot_id': snapshot, 'collection': 'files'})
    assert {row['path'] for row in read.result['result']['rows']} == {'app.py', 'package.json'}
    with store.lane('local_code').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM code_file_version').fetchone()[0] == 2
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM source_occurrence').fetchone()[0] == 2
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='code_file_version'").fetchone() is None


@pytest.mark.parametrize('failure', ['changed', 'wrong_lane', 'wrong_path', 'missing_occurrence'])
def test_delta_rejects_stale_wrong_lane_and_scope_before_job(code_system, failure):
    store = code_system[1]
    overrides = {str(store.source_root / 'app.py'): 'artifacts'} if failure == 'wrong_lane' else {}
    receipt = registered(code_system, ['app.py'], overrides=overrides)
    create_plan(code_system)
    if failure == 'changed':
        (store.source_root / 'app.py').write_text('print("changed")\n')
    response = enter(code_system, receipt['route_id'],
        arguments={'paths': ['helper.py' if failure == 'wrong_path' else 'app.py']},
        ordinals=(2,) if failure == 'missing_occurrence' else (1,))
    expected = {'changed': 'SOURCE_ROUTE_SOURCE_CHANGED', 'wrong_lane': 'SOURCE_ROUTE_LANE_MISMATCH',
        'wrong_path': 'SOURCE_ROUTE_PATH_MISMATCH', 'missing_occurrence': 'SOURCE_ROUTE_OCCURRENCE_MISSING'}
    assert response.error.code == expected[failure]
    assert PlanStore(store).task('code-0', expected_revision=1).state == 'queued'
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_runs'").fetchone() is None


def test_new_directory_file_requires_new_registration(code_system):
    receipt = registered(code_system, ['.'])
    (code_system[1].source_root / 'new.py').write_text('print("new")\n')
    create_plan(code_system)
    admitted = enter(code_system, receipt['route_id'])
    assert admitted.status == 'queued', admitted.error
    store = code_system[1]
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        with store.lane('plan').connection(read_only=True) as connection:
            row = connection.execute('SELECT state,error_code FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone()
        if row['state'] == 'blocked':
            break
        time.sleep(.03)
    assert tuple(row) == ('blocked', 'SOURCE_ROUTE_SOURCE_CHANGED')
    assert not (store.root / 'local_code/local_code_sector_v001.sqlite').exists()


def test_failed_route_publication_rolls_back_registered_batch_schema_and_receipts(code_system, monkeypatch):
    from evidence_lane_plugin.storage import LaneStore
    original = LaneStore.append_receipt
    def reject(self, kind, body, **kwargs):
        if kind == 'source_sdk_action':
            raise LaneError('ROUTE_RECEIPT_FAILURE', 'Injected failure after route publication.')
        return original(self, kind, body, **kwargs)
    monkeypatch.setattr(LaneStore, 'append_receipt', reject)
    store = code_system[1]
    folder = store.lane('sources').folder
    before = {p.relative_to(folder): hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}
    result = call(code_system, 'lane_configure_routes', {'sources': [str(store.source_root / 'app.py')],
        'overrides': {str(store.source_root / 'app.py'): 'local_code'}})
    assert result.error.code == 'ROUTE_RECEIPT_FAILURE'
    assert all(hashlib.sha256((folder / path).read_bytes()).hexdigest() == digest for path, digest in before.items())
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name IN ('intake_batch','sourceroutes_receipts','sourceroutes_requests')").fetchone() is None
        assert connection.execute('SELECT count(*) FROM objects').fetchone()[0] == 0
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='schema_history_files'").fetchone() is None
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='source_sdk_action'").fetchone()[0] == 0
    # Immutable orphan files may survive an aborted publication; registration
    # is the storage visibility boundary and every existing byte is preserved.


def test_directory_member_tampering_fails_against_frozen_merkle(code_system):
    engine, store, _ = code_system
    receipt = registered(code_system, ['.'])
    create_plan(code_system)
    with engine.project_work.mutation(store) as lease, lease.transaction('sources') as connection:
        connection.execute("UPDATE source_member SET sha256=? WHERE member_path='app.py'", ('0' * 64,))
    result = enter(code_system, receipt['route_id'])
    assert result.error.code == 'SOURCE_ROUTE_INTEGRITY'


def test_selected_route_does_not_enable_an_undeclared_consumer(code_system):
    receipt = registered(code_system, ['app.py'])
    create_plan(code_system, ('code_apply',))
    response = enter(code_system, receipt['route_id'], action='code_apply', arguments={
        'snapshot_id': '0' * 64, 'filename': 'app.py', 'expected_sha256': '0' * 64, 'replacement_utf8': ''})
    assert response.error.code == 'SOURCE_ROUTE_CONSUMER_UNSUPPORTED'


def test_same_route_receipt_feeds_separate_workbook_and_data_consumers(tabular_system):
    store = tabular_system[1]
    receipt = registered(tabular_system, ['fixture.xlsx', 'values.json'], action='lane_configure_routes',
        overrides={str(store.source_root / 'fixture.xlsx'): 'data_excel', str(store.source_root / 'values.json'): 'data'})
    tabular_plan(tabular_system, ['spreadsheet_index', 'data_index'])
    snapshots = []
    for index, (action, filename, lane_id) in enumerate([
            ('spreadsheet_index', 'fixture.xlsx', 'data_excel'), ('data_index', 'values.json', 'data')]):
        task = PlanStore(store).task('table-' + str(index), expected_revision=1)
        args = {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
            'action': action, 'arguments': {'filename': filename},
            'source_route': {'route_id': receipt['route_id'], 'occurrence_ordinals': [2 - index]}}
        rejected = call(tabular_system, 'delta_enter', args, expected_revision=1)
        assert rejected.error.code == 'SOURCE_ROUTE_LANE_MISMATCH'
        args['source_route']['occurrence_ordinals'] = [index + 1]
        output = finished(tabular_system, call(tabular_system, 'delta_enter', args, expected_revision=1))
        assert output['source_route']['route_id'] == receipt['route_id']
        assert output['source_route']['lane_id'] == lane_id
        assert output['source_route']['input_file_count'] == 1
        assert output['result']['lane_id'] == lane_id
        snapshots.append(output['result']['result']['snapshot_id'])
    assert snapshots[0] != snapshots[1]
    with store.lane('data_excel').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM sheet_workbook').fetchone()[0] == 1
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='data_table'").fetchone() is None
    with store.lane('data').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM data_table').fetchone()[0] == 1
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sheet_workbook'").fetchone() is None


def test_actual_stdio_configuration_and_read_only_studio(code_system):
    from evidence_lane_plugin.local_transport import LocalEndpoint
    from evidence_lane_plugin.studio_gateway import StudioGateway

    from .test_native_workflow_bindings import native
    engine, store, _ = code_system
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            async with native(engine.root, store.project_id, permissions=('read', 'write')) as session:
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert not tools['lane_configure_routes'].annotations.readOnlyHint
                assert tools['source_routes_read'].annotations.readOnlyHint
                configured = await session.call_tool('lane_configure_routes', {'project_id': store.project_id,
                    'arguments': {'sources': [str(store.source_root / 'app.py')],
                        'overrides': {str(store.source_root / 'app.py'): 'local_code'}}})
                assert not configured.isError and configured.structuredContent['status'] == 'ok', configured
                assert json.loads(configured.content[0].text) == configured.structuredContent
                route_id = configured.structuredContent['result']['result']['source_routes']['route_id']
                read = await session.call_tool('source_routes_read', {'project_id': store.project_id,
                    'arguments': {'route_id': route_id}})
                assert not read.isError and read.structuredContent['status'] == 'ok', read
                return route_id
        route_id = asyncio.run(exercise())
    gateway = StudioGateway(engine)
    _, studio = gateway.exchange(gateway.issue_ticket())
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in store.root.rglob('*') if path.is_file()}
    observed = gateway.command('read', {'project_id': store.project_id, 'action': 'source_routes_read',
        'arguments': {'route_id': route_id}}, studio)
    assert observed['result']['routes'][0]['lane_id'] == 'local_code'
    with pytest.raises(LaneError) as error:
        gateway.command('read', {'project_id': store.project_id, 'action': 'lane_configure_routes',
            'arguments': {}}, studio)
    assert error.value.code == 'NOT_A_STUDIO_QUERY'
    assert {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in store.root.rglob('*') if path.is_file()} == before


def test_optional_git_does_not_read_parent_worktree_identity(tmp_path):
    import subprocess

    from evidence_lane_plugin.git_optional import probe_git_arm
    from evidence_lane_plugin.source_intake import classify_source_intake
    parent = tmp_path / 'git-parent'
    parent.mkdir()
    subprocess.run(['git', '-C', str(parent), 'init', '-b', 'main'], check=True, capture_output=True)
    folder = parent / 'selected-content'
    folder.mkdir()
    (folder / 'code.py').write_bytes(b'print(42)\n')
    observed = probe_git_arm(folder)
    assert observed['state'] == 'PARENT_GIT_WORKTREE_NOT_ADMITTED'
    assert not observed['repository_is_git'] and not observed['history_index_enabled']
    (parent / '.gitignore').write_text('selected-content/\n')
    classified = classify_source_intake([str(folder)], code_mode='local_code')
    assert classified['sources'][0]['source_identity']['member_count'] == 1
    assert classified['sources'][0]['source_identity']['total_bytes'] == len(b'print(42)\n')
    with pytest.raises(LaneError) as error:
        probe_git_arm(folder, requested_mode='REQUIRED')
    assert error.value.code == 'GIT_ARM_EXACT_ROOT_REQUIRED'


def test_route_read_and_execution_budgets_precede_materialization(code_system, monkeypatch):
    from evidence_lane_plugin.source_routing import SourceRouteGuard
    engine, store, _ = code_system
    receipt = registered(code_system, ['.'])
    create_plan(code_system)
    monkeypatch.setattr(SourceRouteGuard, 'MAX_FILES', 1)
    assert enter(code_system, receipt['route_id']).error.code == 'SOURCE_ROUTE_INPUT_BUDGET'
    with engine.project_work.mutation(store) as lease, lease.transaction('sources') as connection:
        connection.execute('UPDATE source_object SET resolved_pointer=?', ('x' * 2_097_153,))
    result = call(code_system, 'source_routes_read', {'route_id': receipt['route_id']})
    assert result.error.code == 'SOURCE_ROUTE_BUDGET'
