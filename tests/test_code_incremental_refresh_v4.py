"""Retained hash/tool-bound reuse at the existing Code refresh owner."""
from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from .test_code_profile_v4 import call, create_plan, execute, git_source
from .test_code_profile_v4 import code_system as code_system  # noqa: PLC0414
from .test_code_verification_v4 import admit, wait_run


def worker_calls(system):
    return system[0].workers.status()['succeeded_operations']


def snapshot(system, snapshot_id):
    from evidence_lane_plugin.code_profile import _snapshot
    return _snapshot(system[1].lane('local_code'), snapshot_id)[1]


def test_same_version_grammar_byte_change_invalidates_parser_identity(tmp_path, monkeypatch):
    from evidence_lane_plugin.code_profile import parser_contract
    from evidence_lane_plugin.hashing import canonical_json_bytes
    from evidence_lane_plugin.shared_tool_assets import ASSET_SCHEMA
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(tmp_path))
    grammar = tmp_path / 'toolchains/grammars'
    grammar.mkdir(parents=True)
    data = grammar / 'identity-fixture.bin'
    def publish(raw):
        data.write_bytes(raw)
        body = {'schema': ASSET_SCHEMA, 'status': 'verified', 'assets': [{
            'asset_id': 'parser_grammars', 'status': 'verified', 'path': 'toolchains/grammars',
            'version': '1.14.3', 'languages': ['identity_fixture_only'],
            'files': [{'path': data.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}]}
        body['receipt_sha256'] = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
        (tmp_path / 'toolchains/asset-installation.v4.json').write_text(json.dumps(body), encoding='utf-8')
    publish(b'first identity fixture, not a grammar')
    first = parser_contract(True)
    plain = parser_contract(False)
    publish(b'second identity fixture, not a grammar')
    assert parser_contract(True) != first and parser_contract(False) == plain


def test_unchanged_refresh_reuses_versions_without_parser_workers_and_read_is_immutable(code_system):
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    store = code_system[1]
    frozen = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in store.lane('local_code').folder.rglob('*') if p.is_file()}
    count = worker_calls(code_system)
    second = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert worker_calls(code_system) == count
    assert second['refresh']['reused_files'] == 3 and second['refresh']['parser_worker_calls'] == 0
    assert second['changes'] == {'added': 0, 'modified': 0, 'deleted': 0, 'unchanged': 3}
    assert second['snapshot_id'] == first['snapshot_id'] and second['generation'] == first['generation']
    assert second['refresh']['publication'] == 'reused_current_snapshot'
    assert frozen == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in store.lane('local_code').folder.rglob('*') if p.is_file()}
    with store.lane('local_code').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM code_file_version').fetchone()[0] == 3
        assert connection.execute('SELECT count(*) FROM code_snapshot_file').fetchone()[0] == 3
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in store.root.rglob('*') if p.is_file()}
    assert call(code_system, 'code_read', {'snapshot_id': second['snapshot_id'], 'filename': 'app.py'}).status == 'ok'
    from evidence_lane_plugin.studio_gateway import StudioGateway
    gateway = StudioGateway(code_system[0])
    _, session = gateway.exchange(gateway.issue_ticket())
    result = gateway.command('read', {'action': 'code_query', 'project_id': store.project_id,
        'arguments': {'snapshot_id': second['snapshot_id'], 'query': 'greeting'}}, session)
    assert result['result']['rows']
    assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in store.root.rglob('*') if p.is_file()}


def test_changed_new_removed_files_reparse_only_changed_bytes_and_rebuild_edges(code_system):
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    source = code_system[1].source_root
    (source / 'helper.py').unlink()
    (source / 'new.py').write_text('def replacement(): return 2\n')
    (source / 'app.py').write_text('from new import replacement\n')
    count = worker_calls(code_system)
    second = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert worker_calls(code_system) - count == 2
    assert second['refresh']['reused_files'] == 1 and second['refresh']['parser_worker_calls'] == 2
    assert second['changes'] == {'added': 1, 'deleted': 1, 'modified': 1, 'unchanged': 1}
    impact = call(code_system, 'code_impact', {'snapshot_id': second['snapshot_id'], 'paths': ['new.py']})
    assert impact.result['result']['files'] == ['app.py', 'new.py']
    assert call(code_system, 'code_read', {'snapshot_id': first['snapshot_id'], 'filename': 'helper.py'}).status == 'ok'


def test_parser_identity_change_forces_reparse_with_explicit_reason(code_system, monkeypatch):
    from evidence_lane_plugin import code_profile
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    original = code_profile.parser_contract
    monkeypatch.setattr(code_profile, 'parser_contract', lambda syntax: hashlib.sha256(('changed:' + original(syntax)).encode()).hexdigest())
    count = worker_calls(code_system)
    second = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert worker_calls(code_system) - count == 3
    assert second['refresh']['reused_files'] == 0 and second['refresh']['parser_contract_changed'] is True
    with code_system[1].lane('local_code').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM code_file_version').fetchone()[0] == 6


def test_refresh_metrics_must_agree_with_snapshot_and_worker_receipts(code_system, monkeypatch):
    from evidence_lane_plugin import code_profile
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    original = code_profile._result
    def incorrect(store, lane_id, operation, body, **kwargs):
        if operation == 'code_refresh':
            body = {**body, 'refresh': {**body['refresh'], 'parser_worker_calls': 3}}
        return original(store, lane_id, operation, body, **kwargs)
    monkeypatch.setattr(code_profile, '_result', incorrect)
    row = wait_run(code_system, admit(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1))
    assert row['state'] == 'blocked' and row['error_code'] == 'DELTA_ACCEPTANCE_FAILED'
    with code_system[1].lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='delta_exit_verified'").fetchone()[0] == 1


def test_current_content_policy_applies_before_reusing_unchanged_bytes(code_system, monkeypatch):
    from evidence_lane_plugin import source_policy
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    monkeypatch.setattr(source_policy, 'known_environment_secrets', lambda: (b'"hello "',))
    count = worker_calls(code_system)
    second = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert worker_calls(code_system) == count
    assert second['refresh']['reused_files'] == 2 and second['refresh']['policy_excluded_files'] == 1
    assert second['changes'] == {'added': 0, 'modified': 0, 'deleted': 1, 'unchanged': 2}
    manifest = snapshot(code_system, second['snapshot_id'])
    assert {item['path'] for item in manifest['files']} == {'app.py', 'package.json'}
    assert manifest['exclusions'] == [{'path': 'helper.py', 'reason': 'CONFIGURED_SECRET_VALUE_EXCLUDED'}]


def test_unchanged_refresh_keeps_published_natural_views_fresh(code_system):
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    args = {'view_id': 'local_code.relationships', 'scope': {'query': first['scope_id']}}
    preview = call(code_system, 'lane_view_preview', args).result
    published = call(code_system, 'lane_view_refresh', {**args, 'scope': preview['scope'],
        'expected_generation': preview['generation'], 'contract_digest': preview['contract_digest'],
        'source_digest': preview['source_digest'], 'formats': ['mmd', 'dot'], 'include_pointer': True})
    assert published.status == 'ok', published.error
    from pathlib import Path
    before = {row['path']: Path(row['path']).read_bytes() for row in published.result['files']}
    count = worker_calls(code_system)
    second = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1)
    assert second['snapshot_id'] == first['snapshot_id'] and worker_calls(code_system) == count
    read = call(code_system, 'lane_view_read', {'view_id': args['view_id'], 'snapshot_digest': published.result['snapshot_digest']})
    assert read.status == 'ok' and read.result['state'] == 'fresh', read
    assert before == {name: Path(name).read_bytes() for name in before}


@pytest.mark.parametrize('tamper', ['facts', 'fts', 'encoding', 'size', 'parser_state'])
def test_corrupt_reuse_fails_before_new_snapshot_publication(code_system, tamper):
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    engine, store, _ = code_system
    with engine.project_work.mutation(store) as lease, lease.transaction('local_code') as connection:
        if tamper == 'facts':
            connection.execute("UPDATE code_symbol SET payload_json='{}'")
        elif tamper == 'fts':
            connection.execute("UPDATE code_chunk_fts SET text_content='tampered'")
        else:
            column, value = {'encoding': ('encoding', 'wrong'), 'size': ('size_bytes', 0), 'parser_state': ('parser_state', 'wrong')}[tamper]
            connection.execute('UPDATE code_file_version SET ' + column + '=?', (value,))
    count = worker_calls(code_system)
    failed = wait_run(code_system, admit(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1))
    assert failed['state'] == 'blocked' and failed['error_code'] == 'CODE_REUSE_INTEGRITY'
    assert worker_calls(code_system) == count
    assert call(code_system, 'code_current').result['result']['scopes'][0]['snapshot_id'] == first['snapshot_id']


@pytest.mark.parametrize('change', ['reused_bytes', 'excluded_bytes', 'new_member'])
def test_complete_selected_identity_rechecked_before_publication(code_system, monkeypatch, change):
    from evidence_lane_plugin import code_profile
    source = code_system[1].source_root
    # Valid PEM markers exercise content exclusion without using credentials.
    (source / 'excluded.txt').write_text('-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----\n')
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    original, count = code_profile._enumerate, 0
    def changed(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            filename = {'reused_bytes': 'helper.py', 'excluded_bytes': 'excluded.txt', 'new_member': 'late.py'}[change]
            (source / filename).write_text('different = True\n')
        return original(*args, **kwargs)
    monkeypatch.setattr(code_profile, '_enumerate', changed)
    failed = wait_run(code_system, admit(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}, index=1))
    assert failed['state'] == 'blocked' and failed['error_code'] == 'CODE_SOURCE_CHANGED'
    assert call(code_system, 'code_current').result['result']['scopes'][0]['snapshot_id'] == first['snapshot_id']


def test_same_lane_other_scope_and_git_baseline_remain_exact(code_system):
    source = code_system[1].source_root
    git = git_source(code_system)
    create_plan(code_system, ('code_index_git', 'code_index', 'code_index', 'code_refresh'))
    execute(code_system, 'code_index_git', {'paths': ['.'], 'git_snapshot_id': git}, index=0)
    first = execute(code_system, arguments={'paths': ['app.py']}, index=1)
    other = execute(code_system, arguments={'paths': ['helper.py']}, index=2)
    store = code_system[1]
    folders = (store.lane('github_code').folder, store.lane('sources').folder, source / '.git')
    frozen = {p: hashlib.sha256(p.read_bytes()).hexdigest() for folder in folders for p in folder.rglob('*') if p.is_file()}
    (source / 'app.py').write_text('from helper import greeting\nvalue = greeting("changed")\n')
    second = execute(code_system, 'code_refresh', {'paths': ['app.py'], 'expected_snapshot': first['snapshot_id']}, index=3)
    current = call(code_system, 'code_current').result['result']['scopes']
    assert {row['snapshot_id'] for row in current} == {other['snapshot_id'], second['snapshot_id']}
    assert frozen == {p: hashlib.sha256(p.read_bytes()).hexdigest() for folder in folders for p in folder.rglob('*') if p.is_file()}


def test_stdio_refresh_exposes_measured_reuse(code_system):
    from evidence_lane_plugin.local_transport import LocalEndpoint

    from .test_native_workflow_bindings import native
    create_plan(code_system, ('code_index', 'code_refresh'))
    first = execute(code_system)
    engine, store, _ = code_system
    from evidence_lane_plugin.plan_runtime import PlanStore
    task = PlanStore(store).task('code-1', expected_revision=1)
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            async with native(engine.root, store.project_id, permissions=('read', 'write')) as session:
                response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': 'code-1', 'plan_revision': 1,
                    'contract_digest': task.contract_digest, 'action': 'code_refresh',
                    'arguments': {'paths': ['.'], 'expected_snapshot': first['snapshot_id']}}})
                data = response.structuredContent
                from evidence_lane_plugin.sdk import ActionResponse
                return await asyncio.to_thread(wait_run, code_system, ActionResponse.model_validate(data))
        row = asyncio.run(exercise())
    assert row['state'] == 'verified', row
    output = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
    assert output['refresh']['reused_files'] == 3 and output['refresh']['parser_worker_calls'] == 0
