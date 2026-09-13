"""Current production registry through real local Git and one-use push effects."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.enrollment import current_branch_authority
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.git_adapter import restoration_source_identity
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.remote_git import push_record

from tests.test_git_sync_v4 import BRANCH, call, git


def run(system, action, arguments, index, *, error=None):
    task = PlanStore(system[1]).task(f'push-{index}', expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments}, expected_revision=1)
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + 40
    row = None
    while time.monotonic() < deadline:
        try:
            with system[1].lane('plan').connection(read_only=True) as db:
                row = dict(db.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as observed:
            # The durable prepared marker is observable between its commit and
            # the coordinator's next exclusive lock. Retry this read only; a
            # persistent recovery error cannot satisfy the terminal assertions.
            if observed.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.02)
            continue
        if row['state'] in {'verified', 'blocked'}:
            break
        time.sleep(.02)
    assert row is not None
    system[0].delta.owned_completion(admitted.job_id).result(timeout=40)
    if error:
        assert row['state'] == 'blocked' and row['error_code'] == error, row
        return admitted.job_id
    assert row['state'] == 'verified', row
    return json.loads(system[1].lane('plan').read_object(row['result_object']))['result']


@pytest.fixture
def system(tmp_path):
    source, remote = tmp_path / 'source', tmp_path / 'remote.git'
    source.mkdir()
    git(source, 'init', '-b', BRANCH)
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    git(source, 'config', 'core.autocrlf', 'false')
    (source / 'app.py').write_bytes(b'print("committed")\n')
    git(source, 'add', 'app.py')
    git(source, 'commit', '-m', 'fixture')
    subprocess.run(['git', 'init', '--bare', str(remote)], capture_output=True, check=True)
    git(source, 'remote', 'add', 'origin', str(remote))
    with Engine(tmp_path / 'engine') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin', 'publish'])]))
        with engine.project_work.mutation(store) as lease:
            ProjectAccess(store).issue(session.client_id, ['read', 'publish'], [remote], writer=lease)
        yield engine, store, session


def setup(system, *, branches=None, paths=None, budget=None):
    actions = branches or ['git_sync_selected', 'remote_git_prepare_push', 'remote_git_execute_push', 'remote_git_execute_push']
    engine, store, _ = system
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Prepared push fixture', tasks=[TaskDefinition(
            task_id=f'push-{i}', title=action, requested_outcome='Verify exact one-use branch delivery',
            profile='code', allowed_actions=[action], permitted_paths=paths if i else ['.'],
            permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Git'],
            acceptance_checks=list(engine.registry.get(action).verification_checks),
            **({'budget': budget} if budget is not None and i else {}))
            for i, action in enumerate(actions)]), lease, actor_id='fixture')
    root = store.source_root
    remote = git(root, 'remote', 'get-url', 'origin')
    head = git(root, 'rev-parse', 'HEAD')
    run(system, 'git_sync_selected', {'source': str(root), 'branch': BRANCH, 'expected_before_commit': head,
        'expected_commit': head, 'expected_remote_url': remote, 'mode': 'select_local_authority',
        'expected_authority_digest': None, 'replace_branch_authority': True}, 0)


def prepare_args(system, **changes):
    root = system[1].source_root
    return {'branch': BRANCH, 'expected_remote_url': git(root, 'remote', 'get-url', 'origin'),
        'expected_commit': git(root, 'rev-parse', 'HEAD'), 'expected_remote_commit': None,
        'expected_authority_digest': current_branch_authority(system[1])['digest'], **changes}


def execute_args(prepared, system):
    return {'action_id': prepared['action_id'], 'prepared_digest': prepared['digest'],
        'expected_remote_url': git(system[1].source_root, 'remote', 'get-url', 'origin')}


def test_exact_committed_push_preserves_dirty_bytes_and_consumes_once(system):
    setup(system, paths=['.'])
    root = system[1].source_root
    (root / 'app.py').write_bytes(b'print("staged")\n')
    git(root, 'add', 'app.py')
    (root / 'app.py').write_bytes(b'print("unstaged")\n')
    (root / 'untracked.bin').write_bytes(b'\xffuntouched\x00')
    before = restoration_source_identity(root)
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    assert prepared['action']['remote_write_performed'] is False
    args = execute_args(prepared, system)
    delivered = run(system, 'remote_git_execute_push', args, 2)
    assert delivered['action']['state'] == 'confirmed'
    assert delivered['action']['remote_commit_after'] == before['head']
    assert restoration_source_identity(root) == before
    assert 'safe_output' not in delivered['action']['output_security']
    remote = Path(args['expected_remote_url'])
    assert git(remote, 'show', BRANCH + ':app.py') == 'print("committed")'
    with system[1].lane('plan').connection(read_only=True) as db:
        assert [r[0] for r in db.execute('SELECT state FROM jobs_effects')] == ['confirmed']
    run(system, 'remote_git_execute_push', args, 3, error='REMOTE_ACTION_ALREADY_CONSUMED')


@pytest.mark.parametrize('change,error', [
    ({'branch': 'main'}, 'REMOTE_PROTECTED_OR_NON_TEST_BRANCH_BLOCKED'),
    ({'branch': 'release/next'}, 'REMOTE_PROTECTED_OR_NON_TEST_BRANCH_BLOCKED'),
    ({'expected_commit': '0' * 40}, 'REMOTE_ACTION_SOURCE_STALE'),
    ({'expected_remote_commit': '0' * 40}, 'REMOTE_ACTION_REMOTE_STALE'),
    ({'expected_authority_digest': '0' * 64}, 'REMOTE_TEST_BRANCH_NOT_EXACT_PROJECT_AUTHORITY'),
])
def test_stale_preparation_and_protected_branches_have_no_effect(system, change, error):
    setup(system, paths=['.'])
    run(system, 'remote_git_prepare_push', prepare_args(system, **change), 1, error=error)
    with system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


def test_local_ref_move_after_prepare_is_rejected(system):
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    root = system[1].source_root
    (root / 'next.txt').write_text('next')
    git(root, 'add', 'next.txt')
    git(root, 'commit', '-m', 'moved')
    run(system, 'remote_git_execute_push', execute_args(prepared, system), 2, error='REMOTE_ACTION_SOURCE_STALE')
    assert push_record(system[1], prepared['action_id'])['body']['state'] == 'invalidated'


def test_remote_moved_after_prepare_is_rejected(system):
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    root = system[1].source_root
    git(root, 'push', 'origin', 'HEAD:refs/heads/' + BRANCH)
    run(system, 'remote_git_execute_push', execute_args(prepared, system), 2, error='REMOTE_ACTION_REMOTE_STALE')


def test_interruption_after_push_consumes_action_and_retains_unconfirmed_effect(system, monkeypatch):
    import evidence_lane_plugin.remote_git as owner
    from evidence_lane_plugin.errors import LaneError
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    original = owner.workflow_git
    def interrupt(repository, arguments, **kwargs):
        result = original(repository, arguments, **kwargs)
        if arguments[0] == 'push':
            raise LaneError('FIXTURE_PUSH_INTERRUPTED', 'Stopped after actual local push, before remote readback.')
        return result
    monkeypatch.setattr(owner, 'workflow_git', interrupt)
    job = run(system, 'remote_git_execute_push', execute_args(prepared, system), 2, error='FIXTURE_PUSH_INTERRUPTED')
    assert push_record(system[1], prepared['action_id'])['body']['state'] == 'consumed'
    remote = Path(execute_args(prepared, system)['expected_remote_url'])
    assert git(remote, 'rev-parse', 'refs/heads/' + BRANCH) == prepared['action']['commit']
    with system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT state FROM jobs_jobs WHERE job_id=?', (job,)).fetchone()[0] == 'uncertain'
        assert db.execute('SELECT state FROM jobs_effects WHERE job_id=?', (job,)).fetchone()[0] == 'prepared'


def test_publish_path_scope_is_enforced_before_push_preparation(system):
    setup(system, paths=['other.txt'])
    run(system, 'remote_git_prepare_push', prepare_args(system), 1, error='DELTA_PATH_SCOPE')


def test_read_before_initialization_does_not_create_lane_tables(system):
    store = system[1]
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    result = call(system, 'remote_git_action_read', {'action_id': str(uuid4())})
    assert result.status == 'ok' and result.result['record'] is None, result.error
    assert before == {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}


def test_local_push_does_not_run_source_or_destination_hooks(system, tmp_path):
    setup(system, paths=['.'])
    root = system[1].source_root
    remote = Path(git(root, 'remote', 'get-url', 'origin'))
    marker = tmp_path / 'hook-ran'
    for path in (root / '.git/hooks/pre-push', remote / 'hooks/pre-receive'):
        path.write_text('#!/bin/sh\nprintf bad > "' + marker.as_posix() + '"\nexit 17\n')
        path.chmod(0o755)
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    run(system, 'remote_git_execute_push', execute_args(prepared, system), 2)
    assert not marker.exists()


def test_successful_existing_branch_fast_forward(system):
    root = system[1].source_root
    old = git(root, 'rev-parse', 'HEAD')
    git(root, 'push', 'origin', 'HEAD:refs/heads/' + BRANCH)
    (root / 'app.py').write_text('next committed version')
    git(root, 'add', 'app.py')
    git(root, 'commit', '-m', 'next')
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system, expected_remote_commit=old), 1)
    assert prepared['action']['published_paths'] == ['app.py']
    result = run(system, 'remote_git_execute_push', execute_args(prepared, system), 2)
    assert result['action']['remote_commit_after'] == git(root, 'rev-parse', 'HEAD')


def test_already_current_branch_is_confirmed_without_claiming_remote_change(system):
    root = system[1].source_root
    head = git(root, 'rev-parse', 'HEAD')
    git(root, 'push', 'origin', 'HEAD:refs/heads/' + BRANCH)
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system, expected_remote_commit=head), 1)
    result = run(system, 'remote_git_execute_push', execute_args(prepared, system), 2)
    assert result['action']['remote_ref_readback_verified']
    assert result['action']['remote_write_attempted']
    assert not result['action']['remote_write_performed']
    assert not result['action']['remote_ref_change_observed']


def test_local_destination_requires_current_project_publish_grant(system):
    setup(system, paths=['.'])
    engine, store, session = system
    with store.lane('receipts').connection(read_only=True) as db:
        grants = db.execute('SELECT grant_id,roots_json FROM access_grants WHERE principal_id=?', (session.client_id,)).fetchall()
    remote = Path(git(store.source_root, 'remote', 'get-url', 'origin'))
    with engine.project_work.mutation(store) as lease:
        for row in grants:
            if str(remote) in json.loads(row['roots_json']):
                ProjectAccess(store).revoke(row['grant_id'], writer=lease)
    run(system, 'remote_git_prepare_push', prepare_args(system), 1, error='PERMISSION_DENIED')


def test_website_marker_requires_current_v4_plan_binding(system):
    from evidence_lane_plugin.website_plan_projection import WEBSITE_DELTA_LEDGER_PATH
    root = system[1].source_root
    marker = root / WEBSITE_DELTA_LEDGER_PATH
    marker.parent.mkdir(parents=True)
    marker.write_text('export const ledger = [];')
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'website fixture')
    setup(system, paths=['.'])
    run(system, 'remote_git_prepare_push', prepare_args(system), 1, error='REMOTE_WEBSITE_PLAN_PROJECTION_STALE')


def test_correct_committed_website_binding_is_checked_again_at_execution(system):
    from evidence_lane_plugin.plan_runtime import content_digest
    from evidence_lane_plugin.website_plan_projection import (
        WEBSITE_DELTA_LEDGER_PATH,
        WEBSITE_PLAN_PROJECTION_PATH,
    )
    root = system[1].source_root
    setup(system, paths=['.'])
    snapshot = PlanStore(system[1]).snapshot()
    body = {'schema': 'evidence-lane.website-plan-binding.v4', 'project_id': system[1].project_id,
        'plan_revision': 1, 'document_digest': snapshot.document_digest}
    for name, content in [(WEBSITE_DELTA_LEDGER_PATH, 'export const ledger=[];'),
            (WEBSITE_PLAN_PROJECTION_PATH, json.dumps({**body, 'digest': content_digest(body)}))]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'current website binding')
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    assert prepared['action']['website_plan_gate']['status'] == 'verified'
    result = run(system, 'remote_git_execute_push', execute_args(prepared, system), 2)
    assert result['action']['state'] == 'confirmed'


def test_tampered_prepared_digest_is_not_consumed(system):
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    arguments = {**execute_args(prepared, system), 'prepared_digest': '0' * 64}
    run(system, 'remote_git_execute_push', arguments, 2, error='REMOTE_ACTION_STATE_CHANGED')
    assert push_record(system[1], prepared['action_id'])['body']['state'] == 'prepared'


def test_https_push_requires_network_grant_before_contact(system):
    setup(system, paths=['.'])
    run(system, 'remote_git_prepare_push', prepare_args(system,
        expected_remote_url='https://example.invalid/repo.git'), 1, error='PERMISSION_DENIED')


def test_push_read_through_stdio_reaches_persistent_engine_with_current_flash(system):
    import asyncio
    import sys
    from datetime import timedelta

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    setup(system, paths=['.'])
    prepared = run(system, 'remote_git_prepare_push', prepare_args(system), 1)
    engine, store, _ = system
    params = StdioServerParameters(command=sys.executable, args=['-m', 'evidence_lane_plugin.mcp_adapter',
        '--runtime-root', str(engine.root), '--project-id', store.project_id, '--permission', 'read'],
        env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src')})
    async def exercise():
        async with (stdio_client(params) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as client):
            await client.initialize()
            result = await client.call_tool('remote_git_action_read', {'project_id': store.project_id,
                'arguments': {'action_id': prepared['action_id']}})
            assert result.structuredContent['status'] == 'ok', result.structuredContent
            assert result.structuredContent['result']['record']['digest'] == prepared['digest']
            assert result.structuredContent['result']['live_remote_observed'] is False
            attribution = result.structuredContent['tool_execution']
            assert attribution['env_uop']['owner_skill'] == 'run-project-lifecycle'
            assert attribution['native_host_tool_attested'] is False
    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(exercise())
