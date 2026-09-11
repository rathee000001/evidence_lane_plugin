"""Automatic edit refresh through the real writer, parser and separate lanes."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import Future

import pytest
from evidence_lane_plugin import code_profile
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    SourceCaptureBudget,
    freeze_source_authority,
)
from evidence_lane_plugin.source_routing import load_route
from evidence_lane_plugin.storage import bounded_project_read, project_snapshot

from .test_code_profile_v4 import call, create_plan, execute
from .test_code_profile_v4 import code_system as code_system  # noqa: PLC0414
from .test_code_verification_v4 import admit, wait_run
from .test_source_routing_v4 import enter, finished, registered


def edit_arguments(system, first):
    original = (system[1].source_root / 'helper.py').read_bytes()
    return {'snapshot_id': first['snapshot_id'], 'filename': 'helper.py',
        'expected_sha256': hashlib.sha256(original).hexdigest(),
        'replacement_utf8': 'def greeting(name):\n    return "welcome " + name\n'}


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def terminal_result(system, admission):
    row = wait_run(system, admission)
    assert row['state'] == 'verified', row
    return json.loads(system[1].lane('plan').read_object(row['result_object']))


def export(system, scope, formats, pointer):
    preview = call(system, 'lane_view_preview', {'view_id': 'local_code.relationships', 'scope': scope})
    assert preview.status == 'ok', preview.error
    result = call(system, 'lane_view_refresh', {'view_id': 'local_code.relationships', 'scope': scope,
        'expected_generation': preview.result['generation'], 'contract_digest': preview.result['contract_digest'],
        'source_digest': preview.result['source_digest'], 'formats': formats, 'include_pointer': pointer})
    assert result.status == 'ok', result.error
    return result.result


def test_edit_automatically_refreshes_sources_same_index_and_next_edit(code_system):
    create_plan(code_system, ('code_index', 'code_apply', 'code_apply'))
    first = execute(code_system)
    engine, store, _ = code_system
    old_lane = files(store.lane('sources').folder)
    engine.workers.operations.pop('render_lane_view')
    result = execute(code_system, 'code_apply', edit_arguments(code_system, first), index=1)
    assert result['index_refresh']['result']['refresh']['reused_files'] == 2
    assert result['index_refresh']['result']['refresh']['parser_worker_calls'] == 1
    assert result['view_refresh'] is None and not result['index_refresh_required']
    assert files(store.lane('sources').folder) != old_lane
    assert result['source_refresh']['parent_route_id'] is None
    heads = {row['lane_id']: row['commit_id'] for row in store.lane_catalog()}
    assert heads['sources'] == heads['local_code']
    source = load_route(store, result['source_refresh']['route_id'])
    assert source['routes'][0]['lane_id'] == 'local_code'
    current = call(code_system, 'code_current').result['result']['scopes'][0]['snapshot_id']
    assert current == result['snapshot_id'] != first['snapshot_id']
    next_args = edit_arguments(code_system, result)
    next_args['replacement_utf8'] = 'def greeting(name):\n    return "again " + name\n'
    second = execute(code_system, 'code_apply', next_args, index=2)
    assert second['source_refresh']['parent_route_id'] == result['source_refresh']['route_id']
    assert second['snapshot_id'] != result['snapshot_id']
    assert load_route(store, result['source_refresh']['route_id']) == source


def test_routed_edit_preserves_occurrences_overrides_assertions_and_subset(code_system):
    store = code_system[1]
    root = str(store.source_root)
    route = registered(code_system, ['.', 'app.py', 'package.json'], action='lane_configure_routes',
        overrides={root: 'local_code', str(store.source_root / 'app.py'): 'local_code',
            str(store.source_root / 'package.json'): 'artifacts'},
        source_assertions={root: {'note': 'fixture assertion'}})
    parent = load_route(store, route['route_id'])
    create_plan(code_system, ('code_index', 'code_apply'))
    initial = finished(code_system, enter(code_system, route['route_id']))['result']['result']
    assert initial['files'] == 1
    result = execute(code_system, 'code_apply', edit_arguments(code_system, initial), index=1)
    child = load_route(store, result['source_refresh']['route_id'])
    assert child['parent_route_id'] == route['route_id']
    assert [(row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in child['routes']] == [
        (row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in parent['routes']]
    assert child['routes'][1:] == parent['routes'][1:]
    assert load_route(store, route['route_id']) == parent
    assert result['index_refresh']['result']['files'] == 1
    _, manifest = code_profile._snapshot(store.lane('local_code'), result['snapshot_id'])
    assert manifest['source_route'] == {'route_id': result['source_refresh']['route_id'], 'occurrence_ordinals': [1]}
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT claim_json FROM source_provenance WHERE batch_id=? AND claim_key='note'",
            (child['batch_id'],)).fetchone()[0] == '"fixture assertion"'
    from evidence_lane_plugin.lanes import get_lane
    assert not (store.root / get_lane('artifacts').database_relative_path).exists()


@pytest.mark.parametrize(('formats', 'pointer'), [(['mmd', 'dot'], True), ([], True)])
def test_edit_refreshes_only_existing_view_scope_and_selected_file_roles(code_system, formats, pointer):
    create_plan(code_system, ('code_index', 'code_apply'))
    first = execute(code_system)
    scope = {'query': first['scope_id'], 'node_limit': 40, 'edge_limit': 60}
    initial = export(code_system, scope, formats, pointer)
    before = call(code_system, 'lane_view_read', {'view_id': 'local_code.relationships'}).result
    result = execute(code_system, 'code_apply', edit_arguments(code_system, first), index=1)
    view = call(code_system, 'lane_view_read', {'view_id': 'local_code.relationships'}).result
    assert view['state'] == 'fresh' and view['generation'] == initial['generation'] + 1
    assert {row['role'] for row in view['manifest']['files']} == {*formats, 'pointer'}
    assert view['manifest']['binding']['scope'] == before['manifest']['binding']['scope']
    historical = call(code_system, 'lane_view_read', {'view_id': 'local_code.relationships', 'snapshot_digest': initial['snapshot_digest']})
    assert historical.result['state'] == 'historical'
    assert result['view_refresh']['snapshot_digest'] == view['snapshot_digest']


def test_edit_verifies_large_view_without_public_response_size_limit(code_system):
    from evidence_lane_plugin.artifact_contract import LaneArtifacts
    _, store, _ = code_system
    content = ''.join('def function_' + 'x' * 350 + str(index) + '():\n    return 1\n' for index in range(205))
    (store.source_root / 'helper.py').write_text(content)
    create_plan(code_system, ('code_index', 'code_apply'))
    first = execute(code_system)
    initial = export(code_system, {'node_limit': 200, 'edge_limit': 500}, [], True)
    artifacts = LaneArtifacts(code_system[0], store)
    spec = artifacts._select('local_code.relationships')
    manifest, _ = artifacts._manifest(spec, initial['snapshot_digest'])
    assert len(json.dumps(manifest).encode()) > 131_072
    args = edit_arguments(code_system, first)
    args['replacement_utf8'] = content.replace('return 1', 'return 2')
    result = execute(code_system, 'code_apply', args, index=1)
    assert not result['index_refresh_required']
    current = artifacts._current(spec.view_id)
    actual, _ = artifacts._manifest(spec, current['snapshot_digest'])
    assert actual['binding']['source_head'] == spec.source_head(store)


@pytest.mark.parametrize('failure', ['parser', 'scope_changed', 'budget'])
def test_known_failures_leave_source_and_all_derived_lanes_unchanged(code_system, monkeypatch, failure):
    create_plan(code_system, ('code_index', 'code_apply'))
    first = execute(code_system)
    engine, store, _ = code_system
    args = edit_arguments(code_system, first)
    if failure == 'scope_changed':
        (store.source_root / 'app.py').write_text('print("changed independently")\n')
    elif failure == 'budget':
        args['replacement_utf8'] = 'x' * 262_145
    else:
        original = engine.workers.submit
        def submit(operation, arguments):
            if operation == 'code_parse_content':
                result = Future()
                result.set_result({'status': 'error', 'code': 'fixture_parser_failure'})
                return result
            return original(operation, arguments)
        monkeypatch.setattr(engine.workers, 'submit', submit)
    before_source = files(store.source_root)
    before_code = files(store.lane('local_code').folder)
    before_sources = files(store.lane('sources').folder)
    row = wait_run(code_system, admit(code_system, 'code_apply', args, index=1))
    assert row['state'] == 'blocked'
    assert files(store.source_root) == before_source
    assert files(store.lane('local_code').folder) == before_code
    assert files(store.lane('sources').folder) == before_sources
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM jobs_effects').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['index', 'source_membership', 'view'])
def test_failure_after_confirmed_edit_rolls_back_refresh_and_keeps_effect(code_system, monkeypatch, failure):
    create_plan(code_system, ('code_index', 'code_apply'))
    first = execute(code_system)
    _, store, _ = code_system
    if failure == 'view':
        export(code_system, {}, ['mmd'], True)
    old_view = call(code_system, 'lane_view_read', {'view_id': 'local_code.relationships'}).result
    source_before = files(store.lane('sources').folder)
    database_before = store.lane('sources').database.read_bytes()
    args = edit_arguments(code_system, first)
    if failure == 'index':
        def fail(*args, **kwargs):
            raise LaneError('FIXTURE_INDEX_FAILURE', 'Injected before index publication.')
        monkeypatch.setattr(code_profile, '_insert_file', fail)
    elif failure == 'view':
        from evidence_lane_plugin.artifact_contract import LaneArtifacts
        def fail(*args, **kwargs):
            raise LaneError('FIXTURE_VIEW_FAILURE', 'Injected after index staging.')
        monkeypatch.setattr(LaneArtifacts, 'refresh', fail)
    else:
        original = code_profile.os.replace
        def replace_file(source, destination):
            result = original(source, destination)
            if destination == store.source_root / 'helper.py':
                (store.source_root / 'extra.py').write_text('print("concurrent addition")\n')
            return result
        monkeypatch.setattr(code_profile.os, 'replace', replace_file)
    row = wait_run(code_system, admit(code_system, 'code_apply', args, index=1))
    assert row['state'] == 'blocked'
    assert (store.source_root / 'helper.py').read_text() == args['replacement_utf8']
    assert store.lane('sources').database.read_bytes() == database_before
    after_sources = files(store.lane('sources').folder)
    assert all(after_sources[name] == value for name, value in source_before.items())
    assert call(code_system, 'code_current').result['result']['scopes'][0]['snapshot_id'] == first['snapshot_id']
    new_view = call(code_system, 'lane_view_read', {'view_id': 'local_code.relationships'}).result
    assert new_view['snapshot_digest'] == old_view['snapshot_digest']
    with store.lane('local_code').connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM code_mutation').fetchone()[0] == 1
    with store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT state,evidence_object FROM jobs_effects WHERE job_id=?', (row['job_id'],)).fetchone()
        assert effect['state'] == 'confirmed'
        assert json.loads(store.lane('plan').read_object(effect['evidence_object']))['source_hash_verified']
    assert PlanStore(store).task('code-1', expected_revision=1).state == 'blocked'


def test_staged_owner_reads_require_exact_writer_and_do_not_open_public_snapshot(code_system):
    import time
    engine, store, _ = code_system
    with engine.project_work.mutation(store) as lease:
        with pytest.raises(LaneError) as error, bounded_project_read(store.root, time.monotonic() + 3, writer=lease):
            pass
        assert error.value.code == 'WRITER_NOT_HELD'
        with lease.coordinated_transaction(['sources']):
            with pytest.raises(LaneError) as error, project_snapshot(store.root):
                pass
            assert error.value.code == 'QUERY_DURING_COMMIT'
            with bounded_project_read(store.root, time.monotonic() + 3, writer=lease), store.lane('sources').connection(read_only=True) as connection:
                assert connection.execute('SELECT project_id FROM lane_identity').fetchone()[0] == store.project_id


def test_missing_selected_export_tool_blocks_admission_before_source_effect(code_system, monkeypatch):
    create_plan(code_system, ('code_index', 'code_apply'))
    first = execute(code_system)
    export(code_system, {}, ['mmd'], True)
    engine, store, _ = code_system
    original = engine.registry.tool_router.observer
    def observer(tool, context):
        return {'ready': False, 'reason': 'fixture missing exporter'} if tool == 'LangGraph_Mermaid_engine' else original(tool, context)
    monkeypatch.setattr(engine.registry.tool_router, 'observer', observer)
    before = files(store.root), files(store.source_root)
    result = admit(code_system, 'code_apply', edit_arguments(code_system, first), index=1)
    assert result.status == 'error' and result.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert (files(store.root), files(store.source_root)) == before


@pytest.mark.parametrize('field', ['source_receipt', 'snapshot', 'effect'])
def test_fabricated_completion_binding_cannot_pass_edit_verification(code_system, monkeypatch, field):
    create_plan(code_system, ('code_index', 'code_apply'))
    first = execute(code_system)
    args = edit_arguments(code_system, first)
    original = code_profile._result
    def altered(store, lane_id, action, body, **kwargs):
        if action == 'code_apply' and 'source_refresh' in body:
            if field == 'source_receipt':
                body['source_refresh']['receipt_id'] = 'missing-fixture-receipt'
            elif field == 'snapshot':
                body['snapshot_id'] = first['snapshot_id']
            else:
                body['effect_id'] = 'missing-fixture-effect'
        return original(store, lane_id, action, body, **kwargs)
    monkeypatch.setattr(code_profile, '_result', altered)
    result = wait_run(code_system, admit(code_system, 'code_apply', args, index=1))
    assert result['state'] == 'blocked'
    assert PlanStore(code_system[1]).task('code-1', expected_revision=1).state == 'blocked'


def test_edit_preserves_unrelated_code_scope_and_git_files(code_system):
    store = code_system[1]
    git = store.source_root / '.git'
    git.mkdir()
    (git / 'HEAD').write_text('fixture local Code must not interpret Git\n')
    create_plan(code_system, ('code_index', 'code_index', 'code_apply'))
    first = execute(code_system, arguments={'paths': ['helper.py']})
    other = execute(code_system, arguments={'paths': ['app.py']}, index=1)
    git_before = files(git)
    result = execute(code_system, 'code_apply', edit_arguments(code_system, first), index=2)
    scopes = call(code_system, 'code_current').result['result']['scopes']
    assert {item['snapshot_id'] for item in scopes} == {result['snapshot_id'], other['snapshot_id']}
    assert files(git) == git_before


@pytest.mark.parametrize(('budget', 'code'), [({'max_files': 1}, 'SOURCE_CAPTURE_FILE_BUDGET'),
    ({'max_file_bytes': 2}, 'SOURCE_CAPTURE_BYTE_BUDGET'), ({'max_total_bytes': 6}, 'SOURCE_CAPTURE_BYTE_BUDGET'),
    ({'max_entries': 1}, 'SOURCE_CAPTURE_ENTRY_BUDGET')])
def test_capture_rejects_limits_while_preserving_source_bytes(tmp_path, budget, code):
    (tmp_path / 'a.py').write_bytes(b'alpha')
    (tmp_path / 'b.py').write_bytes(b'beta')
    before = files(tmp_path)
    capture = SourceCaptureBudget(lambda path: None, lambda: None, **budget)
    with pytest.raises(LaneError) as error:
        freeze_source_authority(SourceAuthoritySpec(str(tmp_path), 1, 'local_code'), capture=capture)
    assert error.value.code == code
    assert files(tmp_path) == before
