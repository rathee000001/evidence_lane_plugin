"""Literal SSH identity, actual agent authentication and current Git workflows."""
from __future__ import annotations

import hashlib
import shlex
from pathlib import Path

import pytest
from evidence_lane_plugin.bounded_io import BoundedProcessResult
from evidence_lane_plugin.enrollment import _bounded_source
from evidence_lane_plugin.errors import LaneError

from tests.test_git_sync_v4 import BRANCH, git


@pytest.mark.parametrize('url', ['git@example.invalid:owner/repo.git',
    'ssh://user@example.invalid:2222/owner/repo.git', 'ssh://git@127.0.0.1/fixture.git'])
def test_literal_ssh_source_selection(url):
    assert _bounded_source(url) == (url, 'SSH_GIT_SOURCE')


@pytest.mark.parametrize('url', ['git@host:-bad', 'git@host:a/../b', 'git@host:a//b',
    'git@host:a;echo_bad', 'git@host:$(bad)', 'git@host:`bad`', 'git@host:~/.ssh/id_rsa',
    'ssh://-bad@host/a', 'ssh://user:password@host/a', 'ssh://git@host:70000/a',
    'ssh://git@host:0/a', 'ssh://git@host/a%20b', 'ssh://git@host/a#b', 'ssh://git@host/a?b',
    'git@host:a\nb', 'git@host:a\\b'])
def test_ssh_sources_reject_shell_credentials_and_ambiguous_paths(url):
    with pytest.raises(LaneError) as error:
        _bounded_source(url)
    assert error.value.code == 'PROJECT_SYNC_SOURCE_INVALID'


def test_ssh_command_is_fixed_quoted_and_cannot_use_inherited_helpers(tmp_path, monkeypatch):
    import evidence_lane_plugin.git_adapter as owner
    git(tmp_path, 'init')
    provider = {'provider': 'host_openssh_agent', 'executable': str(tmp_path.parent / "host's ssh/ssh.exe"),
        'sha256': 'a' * 64, 'known_hosts': str(tmp_path.parent / 'host keys/known_hosts'),
        'known_hosts_sha256': 'b' * 64, 'agent_endpoint': r'\\.\pipe\fixture',
        'default_windows_agent': False, 'credential_values_read': False, 'authentication_observed': False}
    monkeypatch.setattr(owner, 'git_ssh_provider', lambda repo: provider)
    monkeypatch.setenv('GIT_SSH_COMMAND', 'untrusted')
    monkeypatch.setenv('SSH_ASKPASS', 'untrusted')
    calls = []
    def capture(command, **kwargs):
        calls.append((command, kwargs))
        return BoundedProcessResult(0, b'', b'')
    monkeypatch.setattr(owner, 'run_owned_bounded_process', capture)
    owner.workflow_git(tmp_path, ['ls-remote', '--', 'git@host:owner/repo'], ssh_provider=provider)
    command, options = calls[-1]
    env = options['env']
    ssh = shlex.split(env['GIT_SSH_COMMAND'])
    assert ssh[:4] == [Path(provider['executable']).as_posix(), '-F', 'none', '-T']
    assert 'StrictHostKeyChecking=yes' in ssh and 'IdentityFile=none' in ssh
    assert 'UserKnownHostsFile="' + Path(provider['known_hosts']).as_posix() + '"' in ssh
    assert 'SSH_ASKPASS' not in env and env['SSH_ASKPASS_REQUIRE'] == 'never'
    assert env['SSH_AUTH_SOCK'] == provider['agent_endpoint'] and env['GIT_SSH_VARIANT'] == 'ssh'
    assert 'protocol.ssh.allow=always' in command
    with pytest.raises(LaneError) as error:
        owner.workflow_git(tmp_path, ['ls-remote'], ssh_provider={**provider, 'known_hosts_sha256': 'c' * 64})
    assert error.value.code == 'GIT_SSH_PROVIDER_CHANGED' and len(calls) == 1
    owner.workflow_git(tmp_path, ['status'])
    assert 'GIT_SSH_COMMAND' not in calls[-1][1]['env'] and 'protocol.ssh.allow=never' in calls[-1][0]


@pytest.fixture
def ssh_system(tmp_path, monkeypatch):
    paramiko = pytest.importorskip('paramiko')
    import evidence_lane_plugin.git_adapter as adapter

    from tests.git_ssh_fixture import GitSSHServer, MemoryAgent
    repository = tmp_path / 'upstream'
    repository.mkdir()
    git(repository, 'init', '-b', BRANCH)
    git(repository, 'config', 'user.name', 'Fixture')
    git(repository, 'config', 'user.email', 'fixture@example.invalid')
    (repository / 'app.py').write_bytes(b'print("SSH fixture")\n')
    git(repository, 'add', 'app.py')
    git(repository, 'commit', '-m', 'fixture')
    remote = tmp_path / 'remote.git'
    git(repository, 'clone', '--bare', str(repository), str(remote))
    key = paramiko.RSAKey.generate(2048)
    agent = MemoryAgent(tmp_path, key)
    server = GitSSHServer(remote, key)
    server.worktree = repository
    fixture_home = tmp_path / 'host profile'
    (fixture_home / '.ssh').mkdir(parents=True)
    known_hosts = fixture_home / '.ssh/known_hosts'
    known_hosts.write_text(f'[127.0.0.1]:{server.port} {server.host_key.get_name()} {server.host_key.get_base64()}\n')
    monkeypatch.setattr(adapter.Path, 'home', classmethod(lambda cls: fixture_home))
    monkeypatch.setenv('SSH_AUTH_SOCK', agent.endpoint)
    process = adapter.run_owned_bounded_process
    def record_failure(*args, **kwargs):
        result = process(*args, **kwargs)
        if result.returncode:
            print('Isolated fixture Git failure:', result.stderr.decode(errors='replace'))
        return result
    monkeypatch.setattr(adapter, 'run_owned_bounded_process', record_failure)
    try:
        yield server, agent, known_hosts
    finally:
        server.close()
        agent.close()


def test_actual_openssh_authenticates_only_with_fixture_agent_and_reads_exact_ref(ssh_system, tmp_path):
    from evidence_lane_plugin.git_adapter import git_ssh_provider, workflow_git
    server, agent, hosts = ssh_system
    local = tmp_path / 'client'
    local.mkdir()
    git(local, 'init')
    provider = git_ssh_provider(local)
    before = hashlib.sha256(hosts.read_bytes()).hexdigest()
    result = workflow_git(local, ['ls-remote', '--refs', '--heads', '--', server.url,
        'refs/heads/' + BRANCH], ssh_provider=provider)
    assert result.stdout.strip() == git(server.repository, 'rev-parse', 'HEAD') + '\trefs/heads/' + BRANCH
    assert agent.signatures > 0 and server.commands == ["git-upload-pack '/fixture.git'"]
    assert hashlib.sha256(hosts.read_bytes()).hexdigest() == before
    assert not provider['credential_values_read'] and not provider['authentication_observed']


@pytest.mark.parametrize('kind', ['unknown', 'changed'])
def test_actual_host_key_is_rejected_without_authentication_or_host_file_write(ssh_system, tmp_path, kind):
    from evidence_lane_plugin.git_adapter import git_ssh_provider, workflow_git
    server, agent, hosts = ssh_system
    if kind == 'unknown':
        hosts.write_text('unrelated.invalid ssh-rsa ' + server.host_key.get_base64() + '\n')
    else:
        import paramiko
        hosts.write_text(f'[127.0.0.1]:{server.port} ssh-rsa ' + paramiko.RSAKey.generate(2048).get_base64() + '\n')
    local = tmp_path / 'client'
    local.mkdir()
    git(local, 'init')
    before = hosts.read_bytes()
    with pytest.raises(LaneError) as error:
        workflow_git(local, ['ls-remote', '--', server.url], ssh_provider=git_ssh_provider(local))
    assert error.value.code == 'GIT_WORKFLOW_COMMAND_FAILED'
    assert agent.signatures == 0 and not server.commands and hosts.read_bytes() == before


def make_project(tmp_path, actions, *, network=True):
    from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
    from evidence_lane_plugin.engine import Engine
    from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
    root = tmp_path / 'enrolled'
    root.mkdir()
    engine = Engine(tmp_path / 'engine')
    try:
        engine.start()
        row = engine.directory.register(tmp_path / 'state', source_root=root, create=True, read_only=False)
        store = engine.directory.open(row['project_id'], write=True)
        permissions = ['read', 'write', 'tools', 'admin', 'publish'] + (['network'] if network else [])
        _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id, permissions=permissions)]))
        with engine.project_work.mutation(store) as lease:
            PlanStore(store).create(PlanCreate(title='Actual SSH Git workflow', tasks=[TaskDefinition(
                task_id=f'push-{i}', title=action, requested_outcome='Verify attributed exact SSH Git operation',
                profile='code', allowed_actions=[action], permitted_paths=['.'],
                permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Git'],
                acceptance_checks=list(engine.registry.get(action).verification_checks))
                for i, action in enumerate(actions)]), lease, actor_id='fixture')
        return engine, store, client
    except BaseException:
        engine.stop()
        raise


def clone_arguments(server, **changes):
    return {'source': server.url, 'branch': BRANCH, 'expected_commit': git(server.repository, 'rev-parse', 'HEAD'),
        'authentication': 'host_openssh_agent', **changes}


def test_real_plan_enrollment_fast_forward_dirty_committed_push_and_replay(ssh_system, tmp_path):
    from evidence_lane_plugin.enrollment import current_branch_authority
    from evidence_lane_plugin.git_adapter import restoration_source_identity

    from tests.test_remote_git_v4 import execute_args, prepare_args, run
    server, agent, hosts = ssh_system
    actions = ['enroll_project', 'git_sync_selected', 'remote_git_prepare_push', 'remote_git_execute_push', 'remote_git_execute_push']
    system = make_project(tmp_path, actions)
    try:
        root = system[1].source_root
        hosts_before = hosts.read_bytes()
        enrolled = run(system, 'enroll_project', clone_arguments(server), 0)
        assert enrolled['result']['source_kind'] == 'SSH_GIT_SOURCE'
        first = git(root, 'rev-parse', 'HEAD')
        (server.worktree / 'app.py').write_bytes(b'print("second SSH commit")\n')
        git(server.worktree, 'add', 'app.py')
        git(server.worktree, 'commit', '-m', 'second')
        second = git(server.worktree, 'rev-parse', 'HEAD')
        git(server.worktree, 'push', str(server.repository), BRANCH)
        synced = run(system, 'git_sync_selected', {'source': server.url, 'branch': BRANCH,
            'expected_before_commit': first, 'expected_commit': second, 'expected_remote_url': server.url,
            'mode': 'fast_forward', 'expected_authority_digest': current_branch_authority(system[1])['digest'],
            'authentication': 'host_openssh_agent'}, 1)
        assert synced['result']['after']['worktree']['head'] == second
        git(root, 'config', 'user.name', 'Fixture')
        git(root, 'config', 'user.email', 'fixture@example.invalid')
        (root / 'app.py').write_bytes(b'print("third committed SSH update")\n')
        git(root, 'add', 'app.py')
        git(root, 'commit', '-m', 'third')
        third = git(root, 'rev-parse', 'HEAD')
        (root / 'app.py').write_bytes(b'print("staged")\n')
        git(root, 'add', 'app.py')
        (root / 'app.py').write_bytes(b'print("unstaged")\n')
        (root / 'untracked').write_bytes(b'\x00keep')
        before = restoration_source_identity(root)
        prepared = run(system, 'remote_git_prepare_push', prepare_args(system,
            expected_remote_commit=second, authentication='host_openssh_agent'), 2)
        delivered = run(system, 'remote_git_execute_push', execute_args(prepared, system), 3)
        assert delivered['action']['state'] == 'confirmed' and delivered['action']['remote_write_performed']
        assert delivered['action']['remote_commit_after'] == third
        assert git(server.repository, 'rev-parse', BRANCH) == third
        assert git(server.repository, 'show', BRANCH + ':app.py') == 'print("third committed SSH update")'
        assert restoration_source_identity(root) == before and hosts.read_bytes() == hosts_before
        assert agent.signatures >= 6
        with system[1].lane('plan').connection(read_only=True) as db:
            assert [row[0] for row in db.execute('SELECT state FROM jobs_effects')] == ['confirmed'] * 5
        signatures = agent.signatures
        run(system, 'remote_git_execute_push', execute_args(prepared, system), 4, error='REMOTE_ACTION_ALREADY_CONSUMED')
        assert agent.signatures == signatures
    finally:
        assert system[0].stop()


def test_changed_host_database_blocks_prepared_push_before_another_connection(ssh_system, tmp_path):
    from evidence_lane_plugin.remote_git import push_record

    from tests.test_remote_git_v4 import execute_args, prepare_args, run
    server, agent, hosts = ssh_system
    system = make_project(tmp_path, ['enroll_project', 'remote_git_prepare_push', 'remote_git_execute_push'])
    try:
        run(system, 'enroll_project', clone_arguments(server), 0)
        prepared = run(system, 'remote_git_prepare_push', prepare_args(system,
            expected_remote_commit=git(server.repository, 'rev-parse', 'HEAD'), authentication='host_openssh_agent'), 1)
        signatures = agent.signatures
        hosts.write_text(hosts.read_text() + '# external change\n')
        run(system, 'remote_git_execute_push', execute_args(prepared, system), 2, error='GIT_CREDENTIAL_PROVIDER_CHANGED')
        assert agent.signatures == signatures
        assert push_record(system[1], prepared['action_id'])['body']['state'] == 'prepared'
    finally:
        assert system[0].stop()


@pytest.mark.parametrize('network,authentication,error', [(False, 'host_openssh_agent', 'PERMISSION_DENIED'),
    (True, 'none', 'GIT_SSH_AUTHENTICATION_REQUIRED'),
    (True, 'host_git_credential_manager', 'GIT_AUTHENTICATION_SOURCE_MISMATCH')])
def test_ssh_enrollment_requires_live_grant_and_matching_explicit_provider(ssh_system, tmp_path, network, authentication, error):
    from tests.test_remote_git_v4 import run
    server, agent, _ = ssh_system
    system = make_project(tmp_path, ['enroll_project'], network=network)
    try:
        run(system, 'enroll_project', clone_arguments(server, authentication=authentication), 0, error=error)
        assert agent.signatures == 0 and not server.commands and not any(system[1].source_root.iterdir())
        with system[1].lane('plan').connection(read_only=True) as db:
            assert db.execute('SELECT COUNT(*) FROM jobs_effects').fetchone()[0] == 0
    finally:
        assert system[0].stop()


def test_host_key_database_change_during_clone_retains_uncertain_effect_and_metadata(ssh_system, tmp_path, monkeypatch):
    import evidence_lane_plugin.git_adapter as adapter

    from tests.test_remote_git_v4 import run
    server, _, hosts = ssh_system
    system = make_project(tmp_path, ['enroll_project'])
    process = adapter.run_owned_bounded_process
    def change_after_clone(command, **kwargs):
        result = process(command, **kwargs)
        if 'clone' in command:
            assert result.returncode == 0
            hosts.write_text(hosts.read_text() + '# changed during operation\n')
        return result
    monkeypatch.setattr(adapter, 'run_owned_bounded_process', change_after_clone)
    try:
        run(system, 'enroll_project', clone_arguments(server), 0, error='GIT_SSH_PROVIDER_CHANGED')
        assert (system[1].source_root / '.git').is_dir()
        assert not (system[1].source_root / 'app.py').exists()
        with system[1].lane('plan').connection(read_only=True) as db:
            assert [row[0] for row in db.execute('SELECT state FROM jobs_effects')] == ['prepared']
    finally:
        assert system[0].stop()


def test_host_ssh_does_not_trust_project_owned_known_hosts(ssh_system, monkeypatch):
    import evidence_lane_plugin.git_adapter as adapter
    _, _, hosts = ssh_system
    with pytest.raises(LaneError) as error:
        adapter.git_ssh_provider(hosts.parent.parent)
    assert error.value.code == 'GIT_SSH_HOST_KEYS_REQUIRED'


def test_repeated_actual_clones_preserve_transport_and_agent_lifecycle(ssh_system, tmp_path):
    from evidence_lane_plugin.git_adapter import git_ssh_provider, workflow_git
    server, agent, _ = ssh_system
    for index in range(12):
        local = tmp_path / f'clone-{index}'
        local.mkdir()
        workflow_git(local, ['clone', '--template=', '--no-checkout', '--no-tags', '--single-branch',
            '--branch', BRANCH, '--', server.url, '.'], empty_clone=True, ssh_provider=git_ssh_provider(local))
        assert git(local, 'rev-parse', 'HEAD') == git(server.repository, 'rev-parse', 'HEAD')
    for connection in server.connections:
        connection.join(5)
    assert agent.signatures == 12 and len(server.completions) == 12
    assert all(row['returncode'] == 0 and row['peer_disconnected']
        and row['transport_thread_ended'] and row['stream_threads_ended'] for row in server.completions)
