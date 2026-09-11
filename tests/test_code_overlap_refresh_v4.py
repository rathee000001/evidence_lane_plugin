"""Actual overlapping Local Code scopes, source routes and atomic edit refresh."""
from __future__ import annotations

import hashlib
import json
import os

import pytest
from evidence_lane_plugin import code_profile
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.source_routing import SourceMutationRefresh, load_route

from .test_code_edit_refresh_v4 import edit_arguments, files
from .test_code_profile_v4 import call, create_plan
from .test_code_profile_v4 import code_system as code_system  # noqa: PLC0414
from .test_code_verification_v4 import admit, wait_run
from .test_source_routing_v4 import enter, registered


def run(system, action='code_index', arguments=None, *, index=0, route=None, ordinals=(1,)):
    arguments = arguments or {'paths': ['.']}
    admission = (enter(system, route, action=action, arguments=arguments, index=index, ordinals=ordinals)
        if route else admit(system, action, arguments, index=index))
    row = wait_run(system, admission)
    assert row['state'] == 'verified', row
    return json.loads(system[1].lane('plan').read_object(row['result_object']))['result']['result']


def heads(system):
    return {row['scope_id']: row['snapshot_id'] for row in call(system, 'code_current').result['result']['scopes']}


def two_scopes(system, *, actions=None):
    create_plan(system, actions or ('code_index', 'code_index', 'code_apply'))
    primary = run(system, arguments={'paths': ['helper.py']})
    outer = run(system, arguments={'paths': ['.']}, index=1)
    return primary, outer


def test_edit_refreshes_overlapping_scopes_reuses_parser_and_supports_next_edit(code_system):
    primary, outer = two_scopes(code_system, actions=('code_index', 'code_index', 'code_index', 'code_apply', 'code_apply'))
    unrelated = run(code_system, arguments={'paths': ['app.py']}, index=2)
    worker_count = code_system[0].workers.status()['succeeded_operations']
    changed = run(code_system, 'code_apply', edit_arguments(code_system, primary), index=3)
    assert code_system[0].workers.status()['succeeded_operations'] == worker_count + 1
    assert len(changed['scope_refreshes']) == 2
    assert {tuple(row['parser_worker_indices']) for row in changed['scope_refreshes']} == {(0,)}
    current = heads(code_system)
    assert current[unrelated['scope_id']] == unrelated['snapshot_id']
    assert current[primary['scope_id']] != primary['snapshot_id']
    assert current[outer['scope_id']] != outer['snapshot_id']
    old = call(code_system, 'code_read', {'snapshot_id': outer['snapshot_id'], 'filename': 'helper.py'})
    assert 'hello' in old.result['result']['text']
    second_arguments = edit_arguments(code_system, {'snapshot_id': current[outer['scope_id']]})
    second_arguments['replacement_utf8'] = 'def greeting(name):\n    return "again " + name\n'
    second = run(code_system, 'code_apply', second_arguments, index=4)
    assert len(second['scope_refreshes']) == 2
    for scope in second['scope_refreshes']:
        parent = next(item for item in changed['scope_refreshes'] if item['scope_id'] == scope['scope_id'])
        assert scope['source_refresh']['parent_route_id'] == parent['source_refresh']['route_id']
    assert heads(code_system)[unrelated['scope_id']] == unrelated['snapshot_id']


def test_unselected_changed_parent_source_renews_route_without_reparsing_other_file(code_system):
    source = registered(code_system, ['app.py', 'helper.py'])
    create_plan(code_system, ('code_index', 'code_index', 'code_apply'))
    primary = run(code_system, arguments={'paths': ['helper.py']})
    other = run(code_system, arguments={'paths': ['app.py']}, index=1, route=source['route_id'])
    changed = run(code_system, 'code_apply', edit_arguments(code_system, primary), index=2)
    refreshed = next(item for item in changed['scope_refreshes'] if item['scope_id'] == other['scope_id'])
    assert refreshed['parser_worker_indices'] == []
    assert refreshed['index_refresh']['result']['refresh']['parser_worker_calls'] == 0
    assert refreshed['index_refresh']['result']['refresh']['reused_files'] == 1
    _, manifest = code_profile._snapshot(code_system[1].lane('local_code'), refreshed['index_refresh']['result']['snapshot_id'])
    assert manifest['source_route']['occurrence_ordinals'] == [1]
    assert manifest['source_route']['route_id'] != source['route_id']
    child = load_route(code_system[1], manifest['source_route']['route_id'])
    assert child['routes'][0] == load_route(code_system[1], source['route_id'])['routes'][0]
    assert child['routes'][1]['byte_sha256'].lower() == changed['after_sha256']


def test_shared_parent_route_is_published_once_for_both_scopes(code_system):
    source = registered(code_system, ['.'])
    create_plan(code_system, ('code_index', 'code_index', 'code_apply'))
    primary = run(code_system, arguments={'paths': ['helper.py']}, route=source['route_id'])
    run(code_system, arguments={'paths': ['.']}, index=1, route=source['route_id'])
    changed = run(code_system, 'code_apply', edit_arguments(code_system, primary), index=2)
    assert changed['scope_refreshes'][0]['source_refresh'] == changed['scope_refreshes'][1]['source_refresh']
    with code_system[1].lane('sources').connection(read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM sourceroutes_requests WHERE action='code_apply'").fetchone()[0] == 1


def test_manifest_metadata_cannot_understate_actual_read_bytes(code_system):
    create_plan(code_system)
    first = run(code_system)
    engine, store, _ = code_system
    lane = store.lane('local_code')
    with engine.project_work.mutation(store) as lease, lease.transaction('local_code') as db:
        old = db.execute('SELECT o.digest,o.size_bytes FROM objects o JOIN code_snapshot s ON s.manifest_object=o.digest WHERE s.snapshot_id=?',
            (first['snapshot_id'],)).fetchone()
        db.execute('UPDATE objects SET size_bytes=1 WHERE digest=(SELECT manifest_object FROM code_snapshot WHERE snapshot_id=?)',
            (first['snapshot_id'],))
        try:
            with pytest.raises(LaneError) as error:
                code_profile._snapshot(lane, first['snapshot_id'])
            assert error.value.code == 'CODE_SNAPSHOT_INTEGRITY'
        finally:
            db.execute('UPDATE objects SET size_bytes=? WHERE digest=?', (old['size_bytes'], old['digest']))


@pytest.mark.parametrize('failure', ['secondary_drift', 'secondary_limit'])
def test_secondary_preflight_failure_precedes_parser_and_file_effect(code_system, failure):
    create_plan(code_system, ('code_index', 'code_index', 'code_apply'))
    primary = run(code_system, arguments={'paths': ['helper.py']})
    limit = len((code_system[1].source_root / 'helper.py').read_bytes())
    secondary = {'paths': ['.']} if failure == 'secondary_drift' else {'paths': ['helper.py', 'package.json'], 'max_file_bytes': limit}
    run(code_system, arguments=secondary, index=1)
    if failure == 'secondary_drift':
        (code_system[1].source_root / 'app.py').write_text('print("outside edit")\n')
    arguments = edit_arguments(code_system, primary)
    if failure == 'secondary_limit':
        arguments['replacement_utf8'] += '# expanded replacement\n'
    before, current = files(code_system[1].source_root), heads(code_system)
    workers = code_system[0].workers.status()['succeeded_operations']
    result = wait_run(code_system, admit(code_system, 'code_apply', arguments, index=2))
    assert result['state'] == 'blocked'
    assert result['error_code'] == ('CODE_EDIT_SOURCE_CHANGED' if failure == 'secondary_drift' else 'CODE_TOTAL_BYTE_BUDGET')
    assert files(code_system[1].source_root) == before and heads(code_system) == current
    assert code_system[0].workers.status()['succeeded_operations'] == workers
    with code_system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT COUNT(*) FROM jobs_effects WHERE job_id=?', (result['job_id'],)).fetchone()[0] == 0


def test_edit_does_not_expand_task_path_grant_for_an_overlapping_scope(code_system):
    engine, store, _ = code_system
    actions = ('code_index', 'code_index', 'code_apply')
    tasks = [TaskDefinition(task_id='code-' + str(index), title=action, requested_outcome='Verify exact scoped edit',
        profile='code', allowed_actions=[action], permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser'],
        permitted_paths=['helper.py'] if action == 'code_apply' else ['.'],
        acceptance_checks=list(engine.registry.get(action).verification_checks)) for index, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Overlap grants', tasks=tasks), lease, actor_id='fixture')
    primary = run(code_system, arguments={'paths': ['helper.py']})
    run(code_system, arguments={'paths': ['.']}, index=1)
    before, current = files(store.source_root), heads(code_system)
    result = wait_run(code_system, admit(code_system, 'code_apply', edit_arguments(code_system, primary), index=2))
    assert result['state'] == 'blocked' and result['error_code'] == 'DELTA_PATH_SCOPE'
    assert files(store.source_root) == before and heads(code_system) == current


def test_second_source_publication_failure_preserves_effect_and_old_scope_selectors(code_system, monkeypatch):
    primary, _ = two_scopes(code_system)
    current = heads(code_system)
    original, count = SourceMutationRefresh.publish, 0
    def publish(self, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise LaneError('FIXTURE_SECOND_SOURCE_FAILURE', 'Injected later source publication failure.')
        return original(self, **kwargs)
    monkeypatch.setattr(SourceMutationRefresh, 'publish', publish)
    arguments = edit_arguments(code_system, primary)
    result = wait_run(code_system, admit(code_system, 'code_apply', arguments, index=2))
    assert result['state'] == 'blocked' and result['error_code'] == 'FIXTURE_SECOND_SOURCE_FAILURE'
    assert heads(code_system) == current
    assert (code_system[1].source_root / 'helper.py').read_text() == arguments['replacement_utf8']
    with code_system[1].lane('plan').connection(read_only=True) as db:
        effects = db.execute('SELECT state FROM jobs_effects WHERE job_id=?', (result['job_id'],)).fetchall()
        assert [row[0] for row in effects] == ['confirmed']
    with code_system[1].lane('sources').connection(read_only=True) as db:
        exists = db.execute("SELECT 1 FROM sqlite_schema WHERE name='sourceroutes_requests'").fetchone()
        assert not exists or db.execute('SELECT COUNT(*) FROM sourceroutes_requests').fetchone()[0] == 0


@pytest.mark.parametrize('corruption', ['omitted_scope', 'wrong_parent', 'worker_reference', 'publication_receipt'])
def test_incomplete_or_misbound_scope_result_cannot_complete_delta(code_system, monkeypatch, corruption):
    primary, outer = two_scopes(code_system)
    original = code_profile._result
    def altered(store, lane_id, action, body, **kwargs):
        if action == 'code_apply' and 'scope_refreshes' in body:
            if corruption == 'omitted_scope':
                body['scope_refreshes'].pop()
            elif corruption == 'wrong_parent':
                body['scope_refreshes'][1]['previous_snapshot'] = primary['snapshot_id']
            elif corruption == 'worker_reference':
                body['scope_refreshes'][1]['parser_worker_indices'] = []
            else:
                body['scope_refresh_receipt'] = outer['snapshot_id']
        return original(store, lane_id, action, body, **kwargs)
    monkeypatch.setattr(code_profile, '_result', altered)
    result = wait_run(code_system, admit(code_system, 'code_apply', edit_arguments(code_system, primary), index=2))
    assert result['state'] == 'blocked' and result['error_code'] == 'DELTA_ACCEPTANCE_FAILED'
    assert PlanStore(code_system[1]).task('code-2', expected_revision=1).state == 'blocked'


@pytest.fixture(autouse=True)
def grammar_assets(monkeypatch):
    # Workers inherit the environment when the engine starts. Configure the
    # explicit isolated asset root before creating any engine fixture.
    root = os.environ.get('EVI_CODE_QUALIFICATION_ASSETS')
    if not root:
        return
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', root)
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('HF_HUB_DISABLE_TELEMETRY', '1')


@pytest.mark.skipif(not os.environ.get('EVI_CODE_QUALIFICATION_ASSETS'), reason='Requires explicitly staged grammar assets; absence is not execution proof.')
def test_mixed_real_parser_scopes_keep_their_own_syntax_facts(code_system):
    path = code_system[1].source_root / 'api.ts'
    path.write_text('export function oldName(): number { return 1; }\n')
    create_plan(code_system, ('code_index', 'code_index_syntax', 'code_apply'))
    primary = run(code_system, arguments={'paths': ['api.ts']})
    native = run(code_system, 'code_index_syntax', {'paths': ['.']}, index=1)
    workers = code_system[0].workers.status()['succeeded_operations']
    changed = run(code_system, 'code_apply', {'snapshot_id': primary['snapshot_id'], 'filename': 'api.ts',
        'expected_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'replacement_utf8': 'export function newName(): number { return 2; }\n'}, index=2)
    assert code_system[0].workers.status()['succeeded_operations'] == workers + 2
    assert {tuple(row['parser_worker_indices']) for row in changed['scope_refreshes']} == {(0,), (1,)}
    current = heads(code_system)
    for previous, syntax in ((primary, False), (native, True)):
        _, manifest = code_profile._snapshot(code_system[1].lane('local_code'), current[previous['scope_id']])
        assert manifest['syntax_requested'] is syntax
    query = call(code_system, 'code_query', {'snapshot_id': current[native['scope_id']], 'collection': 'symbols', 'query': 'newName'})
    assert query.status == 'ok' and query.result['result']['rows'][0]['fact']['parser'] == 'tree-sitter-language-pack'


@pytest.mark.skipif(not os.environ.get('EVI_CODE_QUALIFICATION_ASSETS'), reason='Requires explicitly staged grammar assets; absence is not execution proof.')
def test_unavailable_secondary_parser_blocks_admission_before_file_write(code_system, monkeypatch):
    create_plan(code_system, ('code_index', 'code_index_syntax', 'code_apply'))
    primary = run(code_system, arguments={'paths': ['helper.py']})
    run(code_system, 'code_index_syntax', {'paths': ['.']}, index=1)
    observer = code_system[0].registry.tool_router.observer
    monkeypatch.setattr(code_system[0].registry.tool_router, 'observer', lambda tool, context:
        {'ready': False, 'reason': 'fixture unavailable secondary grammar'} if tool == 'TreeSitter_LanguagePack' else observer(tool, context))
    before, current = files(code_system[1].source_root), heads(code_system)
    result = admit(code_system, 'code_apply', edit_arguments(code_system, primary), index=2)
    assert result.status == 'error' and result.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert files(code_system[1].source_root) == before and heads(code_system) == current
