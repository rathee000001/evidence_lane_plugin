"""Original selected-branch semantics through current SDK, Plan and real local Git."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.enrollment import current_branch_authority
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.git_adapter import restoration_source_identity
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.sdk import ActionRequest

REMOTE = 'https://github.com/example/sync-fixture.git'
BRANCH = 'codex/sync-fixture'


def call(system, action, arguments=None, **kwargs):
    engine, store, session = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, timeout=15)
    return result.stdout.decode().strip()


@pytest.fixture
def git_system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    git(source, 'init', '-b', BRANCH)
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    git(source, 'config', 'core.autocrlf', 'false')
    git(source, 'remote', 'add', 'origin', REMOTE)
    (source / 'app.py').write_bytes(b'print("first")\n')
    git(source, 'add', 'app.py')
    git(source, 'commit', '-m', 'fixture')
    with Engine(tmp_path / 'runtime') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
        yield engine, store, session


def plan(system, count=1, *, paths=None, budget=None):
    engine, store, _ = system
    spec = engine.registry.get('git_sync_selected')
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Git sync fixture', tasks=[TaskDefinition(
            task_id=f'sync-{i}', title='Sync selected branch', requested_outcome='Verify exact scoped source and branch authority',
            profile='code', allowed_actions=['git_sync_selected'], permitted_paths=paths or ['.'],
            permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Git'], acceptance_checks=list(spec.verification_checks),
            **({'budget': budget} if budget is not None else {}))
            for i in range(count)]), lease, actor_id='fixture')


def selection(system, **changes):
    root = system[1].source_root
    head = git(root, 'rev-parse', 'HEAD')
    authority = current_branch_authority(system[1])
    return {'source': str(root), 'branch': BRANCH, 'expected_before_commit': head,
        'expected_commit': head, 'expected_remote_url': REMOTE, 'mode': 'select_local_authority',
        'expected_authority_digest': authority['digest'] if authority else None,
        'replace_branch_authority': True, **changes}


def execute(system, arguments, *, index=0, error=None):
    task = PlanStore(system[1]).task(f'sync-{index}', expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': 'git_sync_selected', 'arguments': arguments}, expected_revision=1)
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + 35
    row = None
    while time.monotonic() < deadline:
        try:
            with system[1].lane('plan').connection(read_only=True) as db:
                row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as observed:
            if observed.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.02)
            continue
        if row['state'] in {'verified', 'blocked'}:
            break
        time.sleep(.02)
    system[0].delta.owned_completion(admitted.job_id).result(
        timeout=max(1, deadline - time.monotonic())
    )
    if error:
        assert row['state'] == 'blocked' and row['error_code'] == error, row
        return admitted.job_id
    assert row['state'] == 'verified', {key: row[key] for key in ('state', 'error_code', 'result_object')}
    return json.loads(system[1].lane('plan').read_object(row['result_object']))['result']


def clone_target(system, tmp_path, *, filename='app.py'):
    source = system[1].source_root
    target = tmp_path / 'upstream'
    subprocess.run(['git', 'clone', '--no-hardlinks', str(source), str(target)], capture_output=True, check=True)
    git(target, 'config', 'user.name', 'Fixture')
    git(target, 'config', 'user.email', 'fixture@example.invalid')
    git(target, 'config', 'core.autocrlf', 'false')
    (target / filename).write_bytes(b'print("second")\n')
    git(target, 'add', filename)
    git(target, 'commit', '-m', 'next fixture')
    engine, store, session = system
    with engine.project_work.mutation(store) as lease:
        ProjectAccess(store).issue(session.client_id, ['read'], [target], writer=lease)
    return target, git(target, 'rev-parse', 'HEAD')


def test_dirty_local_selection_preserves_index_staged_unstaged_and_untracked_bytes(git_system):
    root = git_system[1].source_root
    (root / 'app.py').write_bytes(b'print("staged")\n')
    git(root, 'add', 'app.py')
    (root / 'app.py').write_bytes(b'print("unstaged")\n')
    (root / 'untracked.bin').write_bytes(b'\x00private unchanged\xff')
    before = restoration_source_identity(root)
    plan(git_system)
    result = execute(git_system, selection(git_system))
    assert restoration_source_identity(root) == before
    assert result['result']['fetch_performed'] is False
    assert result['result']['source_bytes_mutated'] is False
    assert not (root / '.git/FETCH_HEAD').exists()
    assert current_branch_authority(git_system[1])['digest'] == result['authority_digest']
    with git_system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


def test_clean_fast_forward_verifies_real_commit_scoped_bytes_and_both_effects(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path)
    plan(git_system, count=2)
    first = execute(git_system, selection(git_system))
    second = execute(git_system, selection(git_system, mode='fast_forward', source=str(target),
        expected_commit=commit, replace_branch_authority=False), index=1)
    assert second['authority_digest'] == first['authority_digest']
    assert second['result']['changed_paths'] == ['app.py']
    assert second['result']['source_reindex_required'] is True
    assert git(git_system[1].source_root, 'rev-parse', 'HEAD') == commit
    with git_system[1].lane('plan').connection(read_only=True) as db:
        rows = db.execute('SELECT state FROM jobs_effects').fetchall()
        assert len(rows) == 2 and all(row[0] == 'confirmed' for row in rows)


@pytest.mark.parametrize('change,error', [
    ({'expected_before_commit': '0' * 40}, 'PROJECT_SYNC_SOURCE_STALE'),
    ({'branch': 'codex/elsewhere'}, 'GIT_BRANCH_MISMATCH'),
    ({'expected_remote_url': 'https://github.com/example/wrong.git'}, 'GIT_REMOTE_IDENTITY_MISMATCH'),
    ({'replace_branch_authority': False}, 'GIT_BRANCH_REPLACEMENT_REQUIRED'),
    ({'expected_authority_digest': '0' * 64}, 'GIT_BRANCH_AUTHORITY_STALE'),
])
def test_exact_selection_rejects_stale_or_unselected_identity_without_effect(git_system, change, error):
    plan(git_system)
    before = restoration_source_identity(git_system[1].source_root)
    execute(git_system, selection(git_system, **change), error=error)
    assert restoration_source_identity(git_system[1].source_root) == before
    assert current_branch_authority(git_system[1]) is None


def test_dirty_sync_does_not_fetch(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path)
    root = git_system[1].source_root
    (root / 'untracked.txt').write_text('preserve')
    plan(git_system)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='WORKTREE_NOT_CLEAN')
    assert not (root / '.git/FETCH_HEAD').exists()


def test_out_of_scope_fast_forward_keeps_checkout_after_recorded_fetch(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path, filename='outside.py')
    plan(git_system, paths=['app.py'])
    root = git_system[1].source_root
    before = restoration_source_identity(root)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='DELTA_PATH_SCOPE')
    assert restoration_source_identity(root) == before
    with git_system[1].lane('plan').connection(read_only=True) as db:
        assert [row[0] for row in db.execute('SELECT state FROM jobs_effects')] == ['confirmed']


def test_interrupted_merge_keeps_uncertain_effect_and_prevents_false_authority(git_system, tmp_path, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    target, commit = clone_target(git_system, tmp_path)
    plan(git_system)
    original = owner.workflow_git

    def interrupted(repository, args, **kwargs):
        if args[0] == 'merge':
            from evidence_lane_plugin.errors import LaneError
            raise LaneError('FIXTURE_INTERRUPTED_MERGE', 'Fixture interruption after durable preparation')
        return original(repository, args, **kwargs)
    monkeypatch.setattr(owner, 'workflow_git', interrupted)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='FIXTURE_INTERRUPTED_MERGE')
    assert current_branch_authority(git_system[1]) is None
    with git_system[1].lane('plan').connection(read_only=True) as db:
        assert [row[0] for row in db.execute('SELECT state FROM jobs_effects ORDER BY created_at')] == ['confirmed', 'prepared']
        assert db.execute('SELECT state FROM jobs_jobs').fetchone()[0] == 'uncertain'


def test_branch_authority_query_is_read_only_before_initialization(git_system):
    store = git_system[1]
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    response = call(git_system, 'git_branch_authority')
    assert response.status == 'ok' and response.result['authority'] is None, response.error
    assert before == {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    assert call(git_system, 'git_sync_selected', selection(git_system)).error.code == 'DELTA_REQUIRED'


def test_config_redirect_is_rejected_before_transport(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path)
    root = git_system[1].source_root
    git(root, 'config', 'url.https://example.invalid/.insteadOf', 'https://github.com/')
    plan(git_system)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='GIT_WORKFLOW_CONFIG_UNSUPPORTED')
    assert not (root / '.git/FETCH_HEAD').exists()


def test_divergent_branch_is_not_merged(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path)
    root = git_system[1].source_root
    (root / 'local.txt').write_text('local independent commit')
    git(root, 'add', 'local.txt')
    git(root, 'commit', '-m', 'diverged fixture')
    before = restoration_source_identity(root)
    plan(git_system)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='PROJECT_SYNC_NOT_FAST_FORWARD')
    assert restoration_source_identity(root) == before


def test_incoming_symlink_does_not_create_a_source_link(git_system, tmp_path):
    target, _ = clone_target(git_system, tmp_path)
    link_target = subprocess.run(['git', '-C', str(target), 'hash-object', '-w', '--stdin'],
        input=b'../outside-secret', capture_output=True, check=True).stdout.decode().strip()
    git(target, 'update-index', '--add', '--cacheinfo', '120000,' + link_target + ',redirect')
    git(target, 'commit', '-m', 'link fixture')
    commit = git(target, 'rev-parse', 'HEAD')
    plan(git_system)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='GIT_SYNC_TREE_UNSUPPORTED')
    assert not (git_system[1].source_root / 'redirect').exists()


def test_ignored_collision_is_preserved_and_merge_effect_requires_reconciliation(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path, filename='private.txt')
    root = git_system[1].source_root
    (root / '.git/info/exclude').write_text('private.txt\n')
    private = root / 'private.txt'
    private.write_bytes(b'preserve ignored user file')
    before = git(root, 'rev-parse', 'HEAD')
    plan(git_system)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='GIT_WORKFLOW_COMMAND_FAILED')
    assert private.read_bytes() == b'preserve ignored user file'
    assert git(root, 'rev-parse', 'HEAD') == before
    with git_system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute("SELECT count(*) FROM jobs_effects WHERE state='prepared'").fetchone()[0] == 1


def test_https_sync_requires_both_live_client_and_project_network_grant(git_system):
    plan(git_system)
    execute(git_system, selection(git_system, source=REMOTE, mode='fast_forward'), error='PERMISSION_DENIED')
    assert not (git_system[1].source_root / '.git/FETCH_HEAD').exists()


def test_local_source_without_project_read_grant_is_denied(git_system, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    plan(git_system)
    execute(git_system, selection(git_system, source=str(outside), mode='fast_forward'), error='PERMISSION_DENIED')


def test_exhausted_operation_budget_blocks_before_fetch_or_branch_publication(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path)
    plan(git_system, budget={'max_tool_calls': 1})
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit),
        error='DELTA_TOOL_BUDGET')
    assert current_branch_authority(git_system[1]) is None
    assert not (git_system[1].source_root / '.git/FETCH_HEAD').exists()


def test_no_post_merge_hook_execution(git_system, tmp_path):
    target, commit = clone_target(git_system, tmp_path)
    root = git_system[1].source_root
    hook = root / '.git/hooks/post-merge'
    hook.write_text('#!/bin/sh\nprintf unsafe > hook-ran.txt\n')
    hook.chmod(0o755)
    plan(git_system)
    execute(git_system, selection(git_system, source=str(target), mode='fast_forward', expected_commit=commit))
    assert not (root / 'hook-ran.txt').exists()


def test_mcp_stdio_runs_selected_branch_through_persistent_backend(git_system):
    import asyncio
    import sys
    from datetime import timedelta

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine, store, _ = git_system
    plan(git_system)
    task = PlanStore(store).task('sync-0', expected_revision=1)
    params = StdioServerParameters(command=sys.executable, args=['-m', 'evidence_lane_plugin.mcp_adapter',
        '--runtime-root', str(engine.root), '--project-id', store.project_id,
        '--permission', 'read', '--permission', 'write', '--permission', 'tools'],
        env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src')})

    async def exercise():
        async with (stdio_client(params) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client):
            await client.initialize()
            direct = await client.call_tool('git_sync_selected', {'project_id': store.project_id,
                'arguments': selection(git_system)})
            assert direct.structuredContent['error']['code'] == 'DELTA_REQUIRED'
            result = await client.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1,
                    'contract_digest': task.contract_digest, 'action': 'git_sync_selected',
                    'arguments': selection(git_system)}})
            body = result.structuredContent
            assert body['status'] == 'queued', body
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with store.lane('plan').connection(read_only=True) as db:
                    row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                if row['state'] in {'verified', 'blocked'}:
                    break
                await asyncio.sleep(.02)
            assert row['state'] == 'verified', row['error_code']
            recorded = await client.call_tool('git_branch_authority', {'project_id': store.project_id, 'arguments': {}})
            assert recorded.structuredContent['result']['authority']['body']['branch'] == BRANCH
            attribution = recorded.structuredContent['tool_execution']
            assert attribution['env_uop']['owner_skill'] == 'run-project-lifecycle'
            assert attribution['native_host_tool_attested'] is False
    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(exercise())
