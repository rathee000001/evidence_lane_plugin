"""Code workflow boundaries over real workers, persistence and stdio MCP."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hashing import canonical_json_bytes
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.migrations import apply_migrations
from evidence_lane_plugin.plan_runtime import PlanStore, TaskBudget
from evidence_lane_plugin.shared_tool_assets import resolve_shared_asset
from evidence_lane_plugin.storage import ProjectStore

from .test_code_profile_v4 import call, create_plan, execute, git_source
from .test_code_profile_v4 import (
    code_system as code_system,  # noqa: PLC0414 - pytest fixture re-export
)
from .test_native_workflow_bindings import native


def admit(system, action, arguments, *, index=0):
    task = PlanStore(system[1]).task('code-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments}, expected_revision=1)


def wait_run(system, admitted):
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            with system[1].lane('plan').connection(read_only=True) as db:
                row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            # A concurrent read can observe the durable prepare marker during
            # the writer's rollback. Wait for that same job; never recover or
            # replay it here. A persistent marker still fails this deadline.
            time.sleep(.02)
            continue
        if row['state'] in {'verified', 'blocked'}:
            return row
        time.sleep(.02)
    pytest.fail('Code worker did not reach its bounded terminal observation')


def test_insufficient_calls_fail_before_code_snapshot_publication(code_system):
    create_plan(code_system, budget=TaskBudget(max_tool_calls=4))
    row = wait_run(code_system, admit(code_system, 'code_index', {'paths': ['.']}))
    assert row['state'] == 'blocked' and row['error_code'] == 'DELTA_TOOL_BUDGET'
    assert call(code_system, 'code_current').result['result']['scopes'] == []
    assert code_system[0].workers.status()['succeeded_operations'] == 0


@pytest.mark.parametrize('paths', [['../outside.py'], ['helper.py:secret'], ['C:/outside.py']])
def test_code_delta_rejects_paths_outside_its_contract(code_system, paths):
    create_plan(code_system)
    response = admit(code_system, 'code_index', {'paths': paths})
    assert response.status == 'error' and response.error.code == 'DELTA_PATH_SCOPE'
    assert call(code_system, 'code_current').result['result']['scopes'] == []


def test_source_change_while_parsing_does_not_publish_a_current_snapshot(code_system, monkeypatch):
    from evidence_lane_plugin import code_profile
    original = code_profile._enumerate
    count = 0
    def changing(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            (code_system[1].source_root / 'added.py').write_text('def added(): pass\n')
        return original(*args, **kwargs)
    monkeypatch.setattr(code_profile, '_enumerate', changing)
    create_plan(code_system)
    row = wait_run(code_system, admit(code_system, 'code_index', {'paths': ['.']}))
    assert row['state'] == 'blocked' and row['error_code'] == 'CODE_SOURCE_CHANGED'
    assert code_system[0].workers.status()['succeeded_operations'] == 3
    assert call(code_system, 'code_current').result['result']['scopes'] == []


def test_code_receipt_failure_rolls_back_current_snapshot(code_system, monkeypatch):
    original = ProjectStore.append_receipt
    def failing(self, kind, *args, **kwargs):
        if kind == 'code_snapshot':
            raise RuntimeError('Injected receipt failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ProjectStore, 'append_receipt', failing)
    create_plan(code_system)
    row = wait_run(code_system, admit(code_system, 'code_index', {'paths': ['.']}))
    assert row['state'] == 'blocked'
    assert call(code_system, 'code_current').result['result']['scopes'] == []


def test_refresh_preserves_deletion_of_an_explicit_file_scope(code_system):
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system, arguments={'paths': ['helper.py']})
    (code_system[1].source_root / 'helper.py').unlink()
    second = execute(code_system, 'code_refresh', {'paths': ['helper.py'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert second['files'] == 0 and second['changes']['deleted'] == 1
    assert second['scope_id'] == first['scope_id']
    old = call(code_system, 'code_read', {'snapshot_id': first['snapshot_id'], 'filename': 'helper.py'})
    assert old.status == 'ok' and 'greeting' in old.result['result']['text']


def test_refresh_reports_added_deleted_and_renamed_members(code_system):
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    source = code_system[1].source_root
    (source / 'helper.py').rename(source / 'renamed.py')
    (source / 'package.json').unlink()
    (source / 'new.py').write_text('def new(): pass\n')
    second = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert second['changes'] == {'added': 2, 'deleted': 2, 'modified': 0, 'unchanged': 1}
    page = call(code_system, 'code_query', {'snapshot_id': second['snapshot_id'], 'collection': 'changes'})
    assert page.status == 'ok' and len(page.result['result']['rows']) == 5


def test_namespace_import_retains_two_targets_on_one_source_line(code_system):
    source = code_system[1].source_root
    (source / 'package').mkdir()
    for name in ('first', 'second'):
        (source / 'package' / (name + '.py')).write_text('def value(): return 1\n')
    (source / 'imports.py').write_text('from package import first, second\n')
    create_plan(code_system)
    indexed = execute(code_system)
    result = call(code_system, 'code_impact', {'snapshot_id': indexed['snapshot_id'], 'paths': ['imports.py'], 'direction': 'dependencies'})
    assert result.status == 'ok', result.error
    assert result.result['result']['files'] == ['imports.py', 'package/first.py', 'package/second.py']
    with code_system[1].lane('local_code').connection(read_only=True) as db:
        rows = db.execute("SELECT to_path FROM code_import_edge WHERE from_path='imports.py' ORDER BY to_path").fetchall()
    assert [row[0] for row in rows] == ['package/first.py', 'package/second.py']


def test_import_edge_upgrade_preserves_existing_rows_and_schema_history(code_system):
    from evidence_lane_plugin.code_profile_schema import code_migrations
    engine, store, _ = code_system
    migrations = code_migrations('local_code')
    with engine.project_work.mutation(store) as lease:
        apply_migrations(store, migrations[:2], writer=lease)
        lane = store.lane('local_code')
        with lease.transaction('local_code') as db:
            obj = lane.put_object(b'{}')
            db.execute('INSERT INTO code_repo VALUES(?,?,?,?)', ('fixture-repo', store.project_id, str(store.source_root), 'fixture'))
            db.execute('INSERT INTO code_snapshot VALUES(?,?,?,?,?,?,?,?)', ('fixture-snapshot', 'fixture-repo', 'scope', 1, None, obj, 'parser', 'fixture'))
            db.execute('INSERT INTO code_import_edge VALUES(?,?,?,?,?,?)', ('fixture-snapshot', 'app.py', 'helper', 'helper.py', 1, 'unique_static_path'))
            history = [tuple(row) for row in db.execute('SELECT * FROM schema_migrations ORDER BY owner,version')]
        apply_migrations(store, migrations, writer=lease)
    with lane.connection(read_only=True) as db:
        row = db.execute('SELECT * FROM code_import_edge').fetchone()
        assert tuple(row)[:6] == ('fixture-snapshot', 'app.py', 'helper', 'helper.py', 1, 'unique_static_path')
        assert json.loads(row['edge_key']) == list(tuple(row)[1:6])
        assert [tuple(row) for row in db.execute('SELECT * FROM schema_migrations WHERE version<=2 ORDER BY owner,version')] == history
        assert db.execute("SELECT 1 FROM sqlite_schema WHERE name='code_import_edge_previous'").fetchone() is None


@pytest.mark.parametrize('failure', ['stale_snapshot', 'changed_source'])
def test_stale_edit_preserves_source(code_system, failure):
    create_plan(code_system, ('code_index', 'code_apply'), budget=TaskBudget(max_tool_calls=6))
    first = execute(code_system)
    source = code_system[1].source_root / 'helper.py'
    before = source.read_bytes()
    expected = hashlib.sha256(before).hexdigest()
    if failure == 'stale_snapshot':
        expected = '0' * 64
    elif failure == 'changed_source':
        source.write_bytes(before + b'# external edit\n')
    retained = source.read_bytes()
    row = wait_run(code_system, admit(code_system, 'code_apply', {'snapshot_id': first['snapshot_id'],
        'filename': 'helper.py', 'expected_sha256': expected, 'replacement_utf8': 'def changed(): pass\n'}, index=1))
    assert row['state'] == 'blocked'
    assert row['error_code'] == ('CODE_EDIT_SNAPSHOT_MISMATCH' if failure == 'stale_snapshot' else 'CODE_EDIT_SOURCE_CHANGED')
    assert source.read_bytes() == retained
    with code_system[1].lane('local_code').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM code_mutation').fetchone()[0] == 0


@pytest.mark.parametrize(('sql', 'collection', 'query'), [
    ("UPDATE code_chunk_fts SET text_content='fabricated greeting'", 'text', 'greeting'),
    ("UPDATE code_symbol SET payload_json='{}'", 'symbols', None),
    ("UPDATE code_change SET after_sha256='" + '0' * 64 + "'", 'changes', None),
])
def test_queried_index_corruption_is_rejected(code_system, sql, collection, query):
    create_plan(code_system)
    first = execute(code_system)
    engine, store, _ = code_system
    with engine.project_work.mutation(store) as lease, lease.transaction('local_code') as db:
        db.execute(sql)
    result = call(code_system, 'code_query', {'snapshot_id': first['snapshot_id'], 'collection': collection,
        **({'query': query} if query else {})})
    assert result.status == 'error' and result.error.code == 'CODE_QUERY_INTEGRITY'


def test_impact_and_graph_use_immutable_facts_when_derived_edges_are_altered(code_system):
    create_plan(code_system)
    first = execute(code_system)
    engine, store, _ = code_system
    with engine.project_work.mutation(store) as lease, lease.transaction('local_code') as db:
        db.execute("UPDATE code_import_edge SET to_path='package.json'")
    result = call(code_system, 'code_impact', {'snapshot_id': first['snapshot_id'], 'paths': ['helper.py']})
    assert result.result['result']['files'] == ['app.py', 'helper.py']
    bad = call(code_system, 'code_impact', {'snapshot_id': first['snapshot_id'], 'paths': ['helper.py', 'app.py'], 'max_files': 1})
    assert bad.status == 'error'
    graph = call(code_system, 'lane_view_preview', {'view_id': 'local_code.relationships', 'scope': {'query': first['scope_id']}})
    assert graph.status == 'ok' and any(edge['kind'] == 'IMPORTS' for edge in graph.result['graph']['edges'])


def test_code_graph_publication_binds_all_formats_and_detects_tampering(code_system):
    create_plan(code_system)
    first = execute(code_system)
    args = {'view_id': 'local_code.relationships', 'scope': {'query': first['scope_id']}}
    preview = call(code_system, 'lane_view_preview', args).result
    published = call(code_system, 'lane_view_refresh', {**args, 'scope': preview['scope'],
        'expected_generation': preview['generation'], 'contract_digest': preview['contract_digest'],
        'source_digest': preview['source_digest'], 'formats': ['mmd', 'dot'], 'include_pointer': True})
    assert published.status == 'ok', published.error
    assert {row['role'] for row in published.result['files']} == {'mmd', 'dot', 'pointer'}
    read_args = {'view_id': args['view_id'], 'snapshot_digest': published.result['snapshot_digest']}
    read = call(code_system, 'lane_view_read', {**read_args, 'include_content': True})
    assert read.status == 'ok', read.error
    assert json.loads(read.result['contents']['pointer'])['snapshot_binding']['source_digest'] == preview['source_digest']
    path = Path(published.result['files'][0]['path'])
    path.write_bytes(path.read_bytes() + b'\nchanged')
    assert call(code_system, 'lane_view_read', read_args).error.code == 'VIEW_ARTIFACT_CHANGED'


def test_changed_git_checkpoint_cannot_be_indexed_as_the_old_sources_reference(code_system):
    history = git_source(code_system)
    (code_system[1].source_root / 'helper.py').write_text('def dirty(): pass\n')
    create_plan(code_system, ('code_index_git',))
    row = wait_run(code_system, admit(code_system, 'code_index_git', {'paths': ['.'], 'git_snapshot_id': history}))
    assert row['state'] == 'blocked' and row['error_code'] == 'CODE_GIT_CHECKPOINT_CHANGED'
    assert call(code_system, 'code_current', {'lane_id': 'github_code'}).result['result']['scopes'] == []


def test_shared_asset_inventory_rejects_extra_model_files(tmp_path, monkeypatch):
    root = tmp_path / 'shared'
    folder = root / 'toolchains/models/fixture'
    folder.mkdir(parents=True)
    content = b'{}'
    (folder / 'config.json').write_bytes(content)
    manifest = {'schema': 'evidence-lane.shared-tool-assets.v4', 'status': 'verified', 'assets': [
        {'asset_id': 'embedding_snapshot', 'status': 'verified', 'path': 'toolchains/models/fixture', 'files': [
            {'path': 'config.json', 'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}]}]}
    manifest['receipt_sha256'] = hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()
    (root / 'toolchains/asset-installation.v4.json').write_text(json.dumps(manifest))
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(root))
    assert resolve_shared_asset('embedding_snapshot')[0] == folder
    (folder / 'model.safetensors').write_bytes(b'unrecorded')
    with pytest.raises(LaneError, match='file set'):
        resolve_shared_asset('embedding_snapshot')


def test_stdio_code_intake_query_edit_and_refresh_use_the_real_engine(code_system):
    engine, store, _ = code_system
    create_plan(code_system, ('code_index', 'code_apply', 'code_refresh'))
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            async def query(action, arguments):
                response = await session.call_tool(action, {'project_id': store.project_id, 'arguments': arguments})
                body = response.structuredContent
                assert body['status'] == 'ok', body
                return body['result']['result']
            async def delta(index, action, arguments):
                task = PlanStore(store).task('code-' + str(index), expected_revision=1)
                response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                                  'action': action, 'arguments': arguments}})
                body = response.structuredContent
                assert body['status'] == 'queued', body
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    with store.lane('plan').connection(read_only=True) as db:
                        row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                    if row['state'] in {'verified', 'blocked'}:
                        break
                    await asyncio.sleep(.03)
                assert row['state'] == 'verified', row
                return json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
            first = await delta(0, 'code_index', {'paths': ['.']})
            found = await query('code_query', {'snapshot_id': first['snapshot_id'], 'query': 'greeting'})
            assert len(found['rows']) == 2
            before = (store.source_root / 'helper.py').read_bytes()
            edited = await delta(1, 'code_apply', {'snapshot_id': first['snapshot_id'], 'filename': 'helper.py',
                'expected_sha256': hashlib.sha256(before).hexdigest(), 'replacement_utf8': 'def welcome(name): return name\n'})
            second = await delta(2, 'code_refresh', {'paths': ['.'], 'expected_snapshot': edited['snapshot_id']})
            assert edited['index_refresh']['result']['changes']['modified'] == 1
            assert second['snapshot_id'] == edited['snapshot_id']
            assert (await query('code_query', {'snapshot_id': second['snapshot_id'], 'query': 'welcome'}))['rows'][0]['path'] == 'helper.py'
    with LocalEndpoint(engine):
        asyncio.run(run())
