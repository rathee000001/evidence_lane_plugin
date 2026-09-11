"""Repository enrollment through the real registry, current Plan and local Git."""
from __future__ import annotations

import subprocess

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.enrollment import current_branch_authority
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.git_adapter import restoration_source_identity
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.projects import ProjectAccess

from tests.test_git_sync_v4 import BRANCH, call, git
from tests.test_remote_git_v4 import run


@pytest.fixture
def system(tmp_path):
    source, upstream = tmp_path / 'empty-source', tmp_path / 'upstream'
    source.mkdir()
    upstream.mkdir()
    git(upstream, 'init', '-b', BRANCH)
    git(upstream, 'config', 'user.name', 'Fixture')
    git(upstream, 'config', 'user.email', 'fixture@example.invalid')
    git(upstream, 'config', 'core.autocrlf', 'false')
    (upstream / 'app.py').write_bytes(b'print("enrollment")\n')
    git(upstream, 'add', 'app.py')
    git(upstream, 'commit', '-m', 'enrollment fixture')
    with Engine(tmp_path / 'engine') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
        with engine.project_work.mutation(store) as lease:
            ProjectAccess(store).issue(session.client_id, ['read'], [upstream], writer=lease)
        yield engine, store, session


def plan(system, *, paths=None, budget=None, count=1):
    engine, store, _ = system
    spec = engine.registry.get('enroll_project')
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Exact enrollment', tasks=[TaskDefinition(
            task_id=f'push-{i}', title='Enroll selected repository', requested_outcome='Verify the exact enrolled commit and scoped files',
            profile='code', allowed_actions=['enroll_project'], permitted_paths=paths or ['.'],
            permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Git'], acceptance_checks=list(spec.verification_checks),
            **({'budget': budget} if budget is not None else {})) for i in range(count)]), lease, actor_id='fixture')


def args(system, **changes):
    upstream = system[1].source_root.parent / 'upstream'
    return {'source': str(upstream), 'branch': BRANCH,
        'expected_commit': git(upstream, 'rev-parse', 'HEAD'), **changes}


def effects(system):
    with system[1].lane('plan').connection(read_only=True) as db:
        return [dict(row) for row in db.execute('SELECT * FROM jobs_effects ORDER BY rowid')]


def test_real_clone_preserves_source_and_publishes_exact_independent_checkout(system):
    upstream = system[1].source_root.parent / 'upstream'
    before = restoration_source_identity(upstream)
    plan(system, count=2)
    result = run(system, 'enroll_project', args(system), 0)
    body = result['result']
    assert body['after']['worktree']['clean'] and body['after']['worktree']['head'] == args(system)['expected_commit']
    assert (system[1].source_root / 'app.py').read_bytes() == (upstream / 'app.py').read_bytes()
    assert restoration_source_identity(upstream) == before
    assert not (system[1].source_root / '.git/objects/info/alternates').exists()
    assert body['source_reindex_required'] and not body['remote_write_performed'] and not body['project_state_copied']
    assert current_branch_authority(system[1])['digest'] == result['authority_digest']
    assert [row['state'] for row in effects(system)] == ['confirmed', 'confirmed']
    run(system, 'enroll_project', args(system), 1, error='PROJECT_ENROLL_WORKSPACE_EXISTS')
    assert len(effects(system)) == 2


@pytest.mark.parametrize('kind', ['file', 'directory', 'git'])
def test_existing_destination_bytes_are_never_replaced(system, kind):
    root = system[1].source_root
    if kind == 'file':
        (root / 'preserve.bin').write_bytes(b'\xffprivate\x00')
    elif kind == 'directory':
        (root / 'preserve').mkdir()
    else:
        git(root, 'init')
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    plan(system)
    run(system, 'enroll_project', args(system), 0, error='PROJECT_ENROLL_WORKSPACE_EXISTS')
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert not effects(system) and current_branch_authority(system[1]) is None


def test_stale_local_commit_has_no_effect(system):
    plan(system)
    run(system, 'enroll_project', args(system, expected_commit='0' * 40), 0,
        error='PROJECT_ENROLL_EXPECTED_COMMIT_MISMATCH')
    assert not effects(system) and list(system[1].source_root.iterdir()) == []


def test_out_of_scope_checkout_keeps_confirmed_metadata_without_files(system):
    plan(system, paths=['other.py'])
    run(system, 'enroll_project', args(system), 0, error='DELTA_PATH_SCOPE')
    assert [p.name for p in system[1].source_root.iterdir()] == ['.git']
    assert [row['state'] for row in effects(system)] == ['confirmed']
    assert current_branch_authority(system[1]) is None


def test_interruption_after_clone_retains_uncertain_output_without_authority(system, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    original = owner.workflow_git
    def interrupt(root, arguments, **kwargs):
        result = original(root, arguments, **kwargs)
        if arguments[0] == 'clone':
            raise LaneError('TEST_INTERRUPTED_AFTER_CLONE', 'Fixture interruption after actual owned Git clone')
        return result
    monkeypatch.setattr(owner, 'workflow_git', interrupt)
    plan(system)
    job_id = run(system, 'enroll_project', args(system), 0, error='TEST_INTERRUPTED_AFTER_CLONE')
    assert (system[1].source_root / '.git').is_dir()
    assert not (system[1].source_root / 'app.py').exists()
    assert [row['state'] for row in effects(system)] == ['prepared']
    assert current_branch_authority(system[1]) is None
    with system[1].lane('plan').connection(read_only=True) as db:
        assert db.execute('SELECT state FROM jobs_jobs WHERE job_id=?', (job_id,)).fetchone()[0] == 'uncertain'


def test_source_branch_moves_during_clone_preserves_metadata_and_rejects_checkout(system, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    original = owner.workflow_git
    def moved(root, arguments, **kwargs):
        if arguments[0] == 'clone':
            upstream = root.parent / 'upstream'
            (upstream / 'new.py').write_text('changed')
            git(upstream, 'add', 'new.py')
            git(upstream, 'commit', '-m', 'moved fixture')
        return original(root, arguments, **kwargs)
    monkeypatch.setattr(owner, 'workflow_git', moved)
    plan(system)
    run(system, 'enroll_project', args(system), 0, error='PROJECT_ENROLL_EXPECTED_COMMIT_MISMATCH')
    assert [row['state'] for row in effects(system)] == ['confirmed']
    assert [p.name for p in system[1].source_root.iterdir()] == ['.git']
    assert current_branch_authority(system[1]) is None


def test_symlink_tree_is_not_materialized(system):
    upstream = system[1].source_root.parent / 'upstream'
    blob = subprocess.run(['git', '-C', str(upstream), 'hash-object', '-w', '--stdin'],
        input=b'../outside', capture_output=True, check=True).stdout.decode().strip()
    git(upstream, 'update-index', '--add', '--cacheinfo', f'120000,{blob},link')
    git(upstream, 'commit', '-m', 'symlink fixture')
    plan(system)
    run(system, 'enroll_project', args(system), 0, error='GIT_ENROLL_TREE_UNSUPPORTED')
    assert not (system[1].source_root / 'link').exists()
    assert [row['state'] for row in effects(system)] == ['confirmed']


def test_concurrent_destination_file_after_clone_is_preserved(system, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    original = owner.workflow_git
    def concurrent(root, arguments, **kwargs):
        result = original(root, arguments, **kwargs)
        if arguments[0] == 'clone':
            (root / 'app.py').write_bytes(b'user-file-preserved')
        return result
    monkeypatch.setattr(owner, 'workflow_git', concurrent)
    plan(system)
    run(system, 'enroll_project', args(system), 0, error='PROJECT_ENROLL_SOURCE_STALE')
    assert (system[1].source_root / 'app.py').read_bytes() == b'user-file-preserved'
    assert current_branch_authority(system[1]) is None


@pytest.mark.parametrize('change,error', [
    ({'source': 'https://github.com/example/fixture.git'}, 'PERMISSION_DENIED'),
    ({'authentication': 'host_git_credential_manager'}, 'GIT_AUTHENTICATION_SOURCE_MISMATCH'),
    ({'source': 'https://token@github.com/example/fixture.git'}, 'PROJECT_SYNC_SOURCE_INVALID'),
])
def test_transport_grants_and_authentication_are_explicit(system, change, error):
    plan(system)
    run(system, 'enroll_project', args(system, **change), 0, error=error)
    assert not effects(system) and list(system[1].source_root.iterdir()) == []


def test_low_budget_has_no_clone_effect(system):
    plan(system, budget={'max_tool_calls': 1})
    run(system, 'enroll_project', args(system), 0, error='DELTA_TOOL_BUDGET')
    assert not effects(system) and list(system[1].source_root.iterdir()) == []


def test_sdk_direct_enrollment_requires_delta(system):
    result = call(system, 'enroll_project', args(system))
    assert result.status == 'error' and result.error.code == 'DELTA_REQUIRED'
    assert list(system[1].source_root.iterdir()) == []


def test_branch_race_cannot_materialize_unverified_files(system, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    upstream = system[1].source_root.parent / 'upstream'
    (upstream / 'outside.py').write_text('old outside scope')
    git(upstream, 'add', 'outside.py')
    git(upstream, 'commit', '-m', 'old out of scope fixture')
    previous = git(upstream, 'rev-parse', 'HEAD')
    git(upstream, 'rm', 'outside.py')
    git(upstream, 'commit', '-m', 'selected fixture')
    original = owner.workflow_git
    def race(root, arguments, **kwargs):
        if arguments[0] == 'checkout':
            git(root, 'update-ref', 'refs/heads/' + BRANCH, previous)
        return original(root, arguments, **kwargs)
    monkeypatch.setattr(owner, 'workflow_git', race)
    plan(system, paths=['app.py'])
    run(system, 'enroll_project', args(system), 0, error='GIT_ENROLL_BRANCH_MOVED')
    assert not (system[1].source_root / 'outside.py').exists()
    assert git(system[1].source_root, 'rev-parse', 'HEAD') == args(system)['expected_commit']
    assert [row['state'] for row in effects(system)] == ['confirmed', 'prepared']
    assert current_branch_authority(system[1]) is None


def test_bare_local_source_is_supported_with_exact_separate_grant(system, tmp_path):
    upstream, bare = system[1].source_root.parent / 'upstream', tmp_path / 'fixture.git'
    subprocess.run(['git', 'clone', '--bare', '--no-hardlinks', str(upstream), str(bare)],
        capture_output=True, check=True, timeout=15)
    with system[0].project_work.mutation(system[1]) as lease:
        ProjectAccess(system[1]).issue(system[2].client_id, ['read'], [bare], writer=lease)
    plan(system)
    result = run(system, 'enroll_project', args(system, source=str(bare)), 0)
    assert result['result']['after']['worktree']['head'] == git(bare, 'rev-parse', 'HEAD')
    assert result['result']['after']['worktree']['clean']


def test_ungranted_local_source_does_not_start_clone(system, tmp_path):
    other = tmp_path / 'not-granted'
    other.mkdir()
    plan(system)
    run(system, 'enroll_project', args(system, source=str(other)), 0, error='PERMISSION_DENIED')
    assert not effects(system) and not list(system[1].source_root.iterdir())


def test_https_enrollment_passes_bound_provider_without_claiming_server_authentication(system, monkeypatch):
    import evidence_lane_plugin.enrollment as owner
    engine, store, _ = system
    _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,
        permissions=['read', 'write', 'tools', 'admin', 'network'])]))
    selected = engine, store, session
    plan(selected)
    remote = 'https://github.com/example/enrollment-fixture.git'
    provider = {'provider': 'host_git_credential_manager', 'executable': 'captured-fixture-only',
        'sha256': 'a' * 64, 'credential_values_read': False, 'authentication_observed': False}
    original, captured = owner.workflow_git, []
    monkeypatch.setattr(owner, 'git_credential_provider', lambda root: provider)
    def transport(root, arguments, **kwargs):
        if arguments[0] != 'clone':
            return original(root, arguments, **kwargs)
        captured.append(kwargs.copy())
        # Exercise an actual local clone while capturing the HTTPS adapter
        # contract. This is explicitly not a server or GCM authentication test.
        arguments = [str(root.parent / 'upstream') if item == remote else item for item in arguments]
        result = original(root, arguments, **{**kwargs, 'https': False, 'credential_provider': None})
        git(root, 'remote', 'set-url', 'origin', remote)
        return result
    monkeypatch.setattr(owner, 'workflow_git', transport)
    result = run(selected, 'enroll_project', args(selected, source=remote,
        authentication='host_git_credential_manager'), 0)
    assert len(captured) == 1 and captured[0]['https'] is True and captured[0]['credential_provider'] == provider
    assert result['result']['credential_provider'] == provider
    assert result['result']['authentication_observed'] is False
    assert result['result']['enrollment_mode'] == 'CLONED_HTTPS_SOURCE'
