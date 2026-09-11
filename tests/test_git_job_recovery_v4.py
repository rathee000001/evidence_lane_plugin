"""Uncertain effects observed against real isolated Git repositories."""
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.git_adapter import workflow_git
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.migrations import apply_migrations
from evidence_lane_plugin.plan_runtime import content_digest
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.remote_git import PUSH_MIGRATIONS
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import json_text
from evidence_lane_plugin.writers import WriterLease

from tests import test_job_recovery_v4 as base
from tests import test_remote_git_v4 as push_base
from tests.test_git_sync_v4 import BRANCH, git

system, call = base.system, base.call
push_system = push_base.system


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def repository(system, tmp_path):
    source = system[1].source_root
    seed = tmp_path / 'seed'
    seed.mkdir()
    git(seed, 'init', '-b', BRANCH)
    git(seed, 'config', 'user.name', 'Recovery fixture')
    git(seed, 'config', 'user.email', 'fixture@example.invalid')
    git(seed, 'config', 'core.autocrlf', 'false')
    (seed / 'content.txt').write_bytes(b'before\n')
    git(seed, 'add', 'content.txt')
    git(seed, 'commit', '-m', 'before')
    before = git(seed, 'rev-parse', 'HEAD')
    (seed / 'content.txt').write_bytes(b'after\n')
    git(seed, 'commit', '-am', 'after')
    target = git(seed, 'rev-parse', 'HEAD')
    return source, seed, before, target


def admit(system, action, arguments, operation, *, identity=None, push=None):
    engine, project, client = system
    queue = JobQueue(project)
    with WriterLease(project, engine.instance_id) as lease:
        job = queue.enqueue(ActionRequest(action=action, project_id=project.project_id,
            expected_revision=1, arguments=arguments), client.client_id, lease)
        claim = queue.claim(job, lease)
        effect = queue.prepare_effect(job, operation + ':' + (identity or str(uuid4())),
            'Observe admitted Git outcome', lease, execution_id=claim.execution_id)
        if push:
            apply_migrations(project, PUSH_MIGRATIONS, writer=lease)
            with lease.transaction('receipts') as connection:
                previous = None
                for sequence, state in enumerate(('prepared', 'consumed'), 1):
                    body = {**push, 'project_id': project.project_id, 'sequence': sequence, 'state': state,
                        'previous_digest': previous, 'execution_job_id': job, 'executed_by': client.client_id}
                    digest = content_digest(body)
                    connection.execute('INSERT INTO gitpush_events VALUES(?,?,?,?)',
                                       (identity, sequence, digest, json_text(body)))
                    if sequence == 1:
                        arguments['prepared_digest'] = digest
                    previous = digest
                connection.execute('INSERT INTO gitpush_current VALUES(?,?,?)', (identity, 2, previous))
            # The test seeds exactly the persisted request/receipt boundary, not
            # a mock execution controller or a user-supplied recovery verdict.
            with lease.transaction('plan') as connection:
                row = queue._row(connection, job)
                request = json.loads(row['request_json'])
                request['arguments'] = arguments
                encoded = json_text(request)
                connection.execute('UPDATE jobs_jobs SET request_json=?,request_digest=? WHERE job_id=?',
                                   (encoded, hashlib.sha256(encoded.encode()).hexdigest(), job))
    with WriterLease(project, engine.instance_id) as lease:
        queue.reconcile_interrupted(lease)
    return job, effect


def recover(system, job, effect):
    inspected = call(system, 'job_recovery_inspect', {'job_id': job})
    assert inspected.status == 'ok', inspected
    row = next(row for row in inspected.result['effects'] if row['effect_id'] == effect)
    assert row['observation_route'] == 'git_readback'
    return call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': row['effect_digest'], 'plan_revision': 1, 'observation_route': 'git_readback'}, 1)


@pytest.mark.parametrize('operation,state,outcome', [
    ('git-fetch', 'after', 'confirmed'), ('git-fetch', 'wrong_fetch', None),
    ('git-fast-forward', 'after', 'confirmed'), ('git-fast-forward', 'before', 'absent'),
    ('git-fast-forward', 'dirty', None),
    ('git-clone', 'after', 'confirmed'), ('git-clone', 'empty', 'absent'),
    ('git-clone', 'partial', None), ('git-clone-checkout', 'after', 'confirmed'),
    ('git-clone-checkout', 'metadata', 'absent'), ('git-clone-checkout', 'detached', None),
])
def test_git_effect_readback_preserves_source_and_metadata(system, tmp_path, operation, state, outcome):
    root, seed, before, target = repository(system, tmp_path)
    if state != 'empty':
        git(root, 'clone', '--no-checkout', '--', str(seed), '.')
        git(root, 'config', 'core.autocrlf', 'false')
        if state not in {'metadata', 'partial'}:
            git(root, 'reset', '--hard', before if state == 'before' else target)
        if state == 'partial':
            git(root, 'update-ref', 'refs/heads/' + BRANCH, before)
        if state == 'detached':
            git(root, 'checkout', '--detach', target)
        if state == 'dirty':
            (root / 'content.txt').write_bytes(b'new user bytes\n')
        if operation == 'git-fetch':
            git(root, 'fetch', '--', str(seed), before if state == 'wrong_fetch' else target)
    action = 'git_sync_selected' if operation in {'git-fetch', 'git-fast-forward'} else 'enroll_project'
    arguments = {'source': str(seed), 'branch': BRANCH, 'expected_commit': target}
    if action == 'git_sync_selected':
        arguments.update(expected_remote_url=str(seed), expected_before_commit=before,
                         mode='fast_forward', expected_authority_digest=None)
    job, effect = admit(system, action, arguments, operation)
    original = tree_bytes(root)
    response = recover(system, job, effect)
    if outcome is None:
        assert response.error.code == 'JOB_EFFECT_SOURCE_CONFLICT', response
        assert JobQueue(system[1]).get(job)['state'] == 'uncertain'
    else:
        assert response.status == 'ok', response.error
        assert response.result['outcome'] == outcome and response.result['job_state'] == 'failed'
        proof = json.loads(system[1].lane('plan').read_object(response.result['evidence_object']))
        assert proof['basis'] == 'observed_git_state' and not proof['historical_actor_inferred']
        assert not proof['git_writes_performed']
    assert tree_bytes(root) == original


@pytest.mark.parametrize('state,outcome', [('after', 'confirmed'), ('before', 'absent'),
                                        ('missing', 'absent'), ('conflict', None), ('no_read_grant', None),
                                        ('no_network_grant', None)])
def test_push_readback_is_read_only_and_binds_consumed_request(system, tmp_path, state, outcome):
    from evidence_lane_plugin.enrollment import selected_remote

    root, seed, before, target = repository(system, tmp_path)
    remote = tmp_path / 'remote.git'
    git(root, 'clone', '--bare', str(seed), str(remote))
    git(root, 'clone', str(seed), '.')
    git(root, 'remote', 'set-url', 'origin', str(remote))
    if state == 'before':
        git(remote, 'update-ref', 'refs/heads/' + BRANCH, before)
    if state == 'missing':
        git(remote, 'update-ref', '-d', 'refs/heads/' + BRANCH)
    if state == 'conflict':
        (seed / 'content.txt').write_bytes(b'other\n')
        git(seed, 'commit', '-am', 'other')
        git(remote, 'fetch', str(seed), BRANCH + ':' + BRANCH)
    engine, project, client = system
    if state != 'no_read_grant':
        with WriterLease(project, engine.instance_id) as lease:
            ProjectAccess(project).issue(client.client_id, ['read'], [remote], writer=lease)
    identity = str(uuid4())
    remote_url = 'https://example.invalid/exact.git' if state == 'no_network_grant' else str(remote)
    if state == 'no_network_grant':
        git(root, 'remote', 'set-url', 'origin', remote_url)
    body = {'action_id': identity, 'source_root': str(root), 'branch': BRANCH, 'remote': 'origin',
        'commit': target, 'remote_commit_before': None if state == 'missing' else before,
        'remote_identity': selected_remote(root, 'origin', remote_url),
        'authentication': 'none', 'credential_provider': None}
    args = {'action_id': identity, 'prepared_digest': '0' * 64, 'expected_remote_url': remote_url}
    job, effect = admit(system, 'remote_git_execute_push', args, 'git-push', identity=identity, push=body)
    original = tree_bytes(root), tree_bytes(remote)
    response = recover(system, job, effect)
    if outcome is None:
        assert response.status == 'error', response
        assert response.error.code == ('JOB_EFFECT_SOURCE_CONFLICT' if state == 'conflict' else 'PERMISSION_DENIED'), response.error
        assert JobQueue(project).get(job)['state'] == 'uncertain'
    else:
        assert response.status == 'ok', response
        assert response.result['outcome'] == outcome
    assert (tree_bytes(root), tree_bytes(remote)) == original


def test_git_readback_deadline_and_redirecting_config_fail_closed(system, tmp_path):
    root, seed, before, target = repository(system, tmp_path)
    git(root, 'clone', str(seed), '.')
    with pytest.raises(LaneError) as error:
        workflow_git(root, ['rev-parse', 'HEAD'], deadline=time.monotonic() - 1)
    assert error.value.code == 'GIT_WORKFLOW_DEADLINE'
    git(root, 'config', 'url.https://example.invalid/.insteadOf', str(seed))
    job, effect = admit(system, 'git_sync_selected', {'source': str(seed), 'branch': BRANCH,
        'expected_commit': target, 'expected_before_commit': before, 'expected_remote_url': str(seed),
        'mode': 'fast_forward', 'expected_authority_digest': None}, 'git-fast-forward')
    original = tree_bytes(root)
    response = recover(system, job, effect)
    assert response.error.code == 'GIT_WORKFLOW_CONFIG_UNSUPPORTED', response
    assert tree_bytes(root) == original and JobQueue(system[1]).get(job)['state'] == 'uncertain'


def test_real_delta_push_lost_confirmation_recovers_without_replay(push_system, monkeypatch):
    import evidence_lane_plugin.remote_git as owner

    push_base.setup(push_system, paths=['.'])
    arguments = push_base.prepare_args(push_system)
    prepared = push_base.run(push_system, 'remote_git_prepare_push', arguments, 1)
    request = {'action_id': prepared['action_id'], 'prepared_digest': prepared['digest'],
               'expected_remote_url': arguments['expected_remote_url']}

    def lose_confirmation(*args, **kwargs):
        raise LaneError('INJECTED_PUSH_CONFIRMATION_LOSS', 'Injected after the real push and ref readback.')

    monkeypatch.setattr(owner, '_confirm', lose_confirmation)
    job = push_base.run(push_system, 'remote_git_execute_push', request, 2, error='INJECTED_PUSH_CONFIRMATION_LOSS')
    queue = JobQueue(push_system[1])
    current = queue.get(job)
    assert current['state'] == 'uncertain' and len(current['effects']) == 1
    root = push_system[1].source_root
    original = tree_bytes(root)
    response = recover(push_system, job, current['effects'][0]['effect_id'])
    assert response.status == 'ok', response.error
    assert response.result['outcome'] == 'confirmed' and response.result['job_state'] == 'failed'
    assert owner.push_record(push_system[1], request['action_id'])['body']['state'] == 'consumed'
    assert tree_bytes(root) == original
