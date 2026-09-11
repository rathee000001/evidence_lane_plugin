"""Read uncertain Git outcomes through exact, bounded operation-owned argv.

An observation establishes current state, never historical causal attribution.
No fetch, checkout, ref update, push, credential setup or replay occurs here.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .enrollment import (
    GitEnrollmentSelection,
    GitSyncSelection,
    _authentication,
    _bounded_source,
    selected_remote,
)
from .errors import LaneError
from .git_adapter import workflow_git
from .projects import ProjectAccess
from .remote_git import PushExecute, push_record
from .sdk import UUID_PATTERN
from .storage import reject_links

COMMIT = r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$'


def observe_git_effect(project, context, job, effect, *, deadline):
    repository = project.source_root
    access = ProjectAccess(project)
    if deadline is None or time.monotonic() >= deadline:
        raise LaneError('GIT_WORKFLOW_DEADLINE', 'The Git recovery observation deadline expired.')
    if len(job['request_json'].encode()) > 65_536:
        raise LaneError('JOB_EFFECT_REQUEST_BUDGET', 'Git recovery requires a bounded admitted request.')
    arguments = json.loads(job['request_json'])['arguments']
    models = {'git_sync_selected': GitSyncSelection, 'enroll_project': GitEnrollmentSelection,
              'remote_git_execute_push': PushExecute}
    if job['action'] not in models:
        raise LaneError('JOB_EFFECT_OBSERVATION_REQUIRED', 'This effect has no registered Git observation owner.')
    request = models[job['action']].model_validate(arguments)
    operation, _, identity = effect['effect_key'].partition(':')
    allowed = {'git_sync_selected': {'git-fetch', 'git-fast-forward'},
               'enroll_project': {'git-clone', 'git-clone-checkout'}, 'remote_git_execute_push': {'git-push'}}
    if operation not in allowed[job['action']] or not re.fullmatch(UUID_PATTERN, identity):
        raise LaneError('JOB_EFFECT_REQUEST_MISMATCH', 'The effect does not belong to its admitted Git operation.')

    def authorize():
        if 'read' not in context.permissions:
            raise LaneError('PERMISSION_DENIED', 'Git recovery requires current client read permission.')
        context.authorize('read')
        reject_links(repository, Path(repository.anchor))
        access.authorize(context.client_id, 'read', path=repository)

    authorize()
    def run(root, argv, **kwargs):
        authorize()
        return workflow_git(root, argv, deadline=deadline, max_stdout_bytes=65_536, **kwargs)

    def read(argv, **kwargs):
        return run(repository, argv, **kwargs).stdout.strip()

    def proof(outcome, **observations):
        return {'outcome': outcome, 'basis': 'observed_git_state', 'operation': operation,
            'source_currentness': 'observed_during_reconciliation', 'historical_actor_inferred': False,
            'git_writes_performed': False, **observations}

    # The clone's admitted before-state is an empty folder. Any partial metadata
    # or unrelated source is preserved; absence is never inferred from a Git error.
    if operation == 'git-clone' and not any(repository.iterdir()):
        return proof('absent', empty_source_root=True)
    reject_links(repository / '.git', repository)
    top = read(['rev-parse', '--show-toplevel'])
    if Path(top).resolve() != repository.resolve():
        raise LaneError('GIT_ROOT_REQUIRED', 'Observe the exact selected Git worktree root.')
    if operation == 'git-push':
        record = push_record(project, request.action_id)
        if record is None:
            raise LaneError('JOB_EFFECT_PREPARATION_REQUIRED', 'The exact consumed push record is required.')
        body = record['body']
        if (identity != request.action_id or body.get('state') not in {'consumed', 'confirmed'}
                or body.get('execution_job_id') != job['job_id'] or body.get('executed_by') != job['client_id']
                or str(repository) != body.get('source_root')):
            raise LaneError('JOB_EFFECT_REQUEST_MISMATCH', 'The push consumption does not belong to this job and source.')
        # A confirmed record has the consumed record as parent; in both cases
        # check that the chain contains the exact request's prepared identity.
        with project.lane('receipts').connection(read_only=True) as connection:
            prepared = connection.execute('SELECT digest FROM gitpush_events WHERE action_id=? AND sequence=1',
                                          (identity,)).fetchone()
        if prepared is None or prepared[0] != request.prepared_digest:
            raise LaneError('JOB_EFFECT_REQUEST_MISMATCH', 'The admitted push preparation changed.')
        branch, remote, expected = body['branch'], body['remote'], request.expected_remote_url
        target, before = body['commit'], body['remote_commit_before']
    else:
        branch, remote = request.branch, request.remote
        expected = request.expected_remote_url if job['action'] == 'git_sync_selected' else request.source
        target = request.expected_commit
        before = request.expected_before_commit if job['action'] == 'git_sync_selected' else None
        if job['action'] == 'git_sync_selected' and request.mode != 'fast_forward':
            raise LaneError('JOB_EFFECT_REQUEST_MISMATCH', 'The admitted sync has no matching remote mutation.')
    if not re.fullmatch(COMMIT, target) or (before is not None and not re.fullmatch(COMMIT, before)):
        raise LaneError('JOB_EFFECT_PREPARATION_INVALID', 'The Git preparation lacks exact commit identities.')
    read(['check-ref-format', '--branch', branch])
    remote_identity = selected_remote(repository, remote, expected, reader=run)
    if operation == 'git-push':
        if remote_identity != body['remote_identity']:
            raise LaneError('GIT_REMOTE_IDENTITY_MISMATCH', 'The consumed push remote changed.')
        source, kind = _bounded_source(expected)
        if kind == 'LOCAL_GIT_SOURCE':
            access.authorize(context.client_id, 'read', path=Path(source))
        else:
            if 'network' not in context.permissions:
                raise LaneError('PERMISSION_DENIED', 'Remote Git readback requires current network permission.')
            context.authorize('network')
            access.authorize(context.client_id, 'network')
        provider = _authentication(repository, kind, body['authentication'])
        if provider != body['credential_provider']:
            raise LaneError('GIT_CREDENTIAL_PROVIDER_CHANGED', 'The recorded transport provider changed.')
        observed = read(['ls-remote', '--refs', '--heads', '--', source, 'refs/heads/' + branch],
            https=kind == 'CREDENTIAL_FREE_HTTPS',
            credential_provider=provider if kind == 'CREDENTIAL_FREE_HTTPS' else None,
            ssh_provider=provider if kind == 'SSH_GIT_SOURCE' else None)
        rows = observed.splitlines()
        if not rows:
            commit = None
        elif len(rows) == 1 and len(parts := rows[0].split()) == 2 and parts[1] == 'refs/heads/' + branch and re.fullmatch(COMMIT, parts[0]):
            commit = parts[0]
        else:
            raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'The exact remote ref readback was ambiguous.')
        if commit not in {before, target}:
            raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'The remote branch matches neither prepared outcome.')
        return proof('confirmed' if commit == target else 'absent', remote_commit=commit,
            before_commit=before, target_commit=target, remote_identity=remote_identity, push_record_digest=record['digest'])

    head = read(['rev-parse', '--verify', 'HEAD^{commit}'])
    if operation == 'git-fetch':
        fetched = read(['rev-parse', '--verify', 'FETCH_HEAD^{commit}'])
        if fetched != target:
            raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'FETCH_HEAD does not identify the admitted fetched commit.')
        return proof('confirmed', fetched_commit=fetched, head=head, remote_identity=remote_identity)
    branch_head = read(['rev-parse', '--verify', 'refs/heads/' + branch + '^{commit}'])
    symbolic = read(['symbolic-ref', '--quiet', 'HEAD'], check=False)
    if operation == 'git-clone':
        if head != target or branch_head != target or symbolic != 'refs/heads/' + branch:
            raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'The clone metadata differs from the selected commit and branch.')
        read(['fsck', '--connectivity-only', '--no-reflogs', '--no-dangling', target])
        return proof('confirmed', head=head, branch=branch, remote_identity=remote_identity,
                     checkout_state_inferred=False)
    if symbolic != 'refs/heads/' + branch or head != branch_head:
        raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'The selected branch and checkout are not coherent.')
    # --no-optional-locks (via the environment) prevents status from refreshing
    # the index. No external diff, fsmonitor or text conversion is enabled.
    status = read(['status', '--porcelain=v2', '-z', '--untracked-files=all'])
    if not status and head in {target, before}:
        return proof('confirmed' if head == target else 'absent', head=head, branch=branch,
                     clean=True, remote_identity=remote_identity)
    if (operation == 'git-clone-checkout' and head == target
            and [item.name for item in repository.iterdir()] == ['.git'] and not read(['ls-files', '-z'])):
        return proof('absent', head=head, branch=branch, metadata_only=True, remote_identity=remote_identity)
    raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'The current checkout does not match a complete prepared outcome; preserve its bytes.')
