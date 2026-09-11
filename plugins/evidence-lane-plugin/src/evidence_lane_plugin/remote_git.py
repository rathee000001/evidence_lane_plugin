"""Original prepared-push owner, bound to current Plan, Sources and Receipts.

Consume before mutation, journal the effect, and confirm exact remote readback.
No automatic replay, protected-branch push, force update or main merge.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, field_validator

from .enrollment import (
    _authentication,
    _authority_matches,
    _bounded_source,
    _confirm,
    branch_identity,
    current_branch_authority,
)
from .errors import LaneError
from .git_adapter import resolve_git_executable, workflow_git
from .github_automation_governance import inspect_agent_output
from .migrations import Migration, apply_migrations, read_compatibility
from .plan_runtime import content_digest
from .projects import ProjectAccess
from .registry import ActionSpec, Contract
from .storage import json_text, now, project_snapshot
from .tool_routes import ToolRoute
from .website_plan_projection import require_current_website_plan_for_push

PUSH_MIGRATIONS = (Migration('gitpush', 1, 'One-use prepared pushes and append-only outcome history', (
    """CREATE TABLE gitpush_events (action_id TEXT NOT NULL, sequence INTEGER NOT NULL,
       digest TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       PRIMARY KEY(action_id,sequence))""",
    """CREATE TABLE gitpush_current (action_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
       digest TEXT NOT NULL, FOREIGN KEY(action_id,sequence) REFERENCES gitpush_events(action_id,sequence))""",
)),)


class PushPrepare(Contract):
    branch: str = Field(min_length=1, max_length=200)
    remote: str = Field(default='origin', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    expected_remote_url: str = Field(min_length=1, max_length=2000)
    expected_commit: str = Field(pattern=r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
    expected_remote_commit: str | None = Field(pattern=r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
    expected_authority_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    authentication: Literal['none', 'host_git_credential_manager', 'host_openssh_agent'] = 'none'

    @field_validator('branch', 'expected_remote_url')
    @classmethod
    def no_controls(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Use an exact selection without control characters')
        return value


class PushExecute(Contract):
    action_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    prepared_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    expected_remote_url: str = Field(min_length=1, max_length=2000)


class PushRead(Contract):
    action_id: str = Field(pattern=r'^[0-9a-f-]{36}$')


class PushResult(Contract):
    project_id: str
    action_id: str
    digest: str
    action: dict


class PushState(Contract):
    project_id: str
    record: dict | None
    live_remote_observed: bool = False


def push_record(store, action_id, connection=None):
    lane = store.lane('receipts')
    if connection is None:
        if read_compatibility(lane, PUSH_MIGRATIONS)[0]['status'] == 'not_initialized':
            return None
        with lane.connection(read_only=True) as db:
            return push_record(store, action_id, db)
    head = connection.execute('SELECT * FROM gitpush_current WHERE action_id=?', (action_id,)).fetchone()
    if head is None:
        return None
    rows = connection.execute('SELECT * FROM gitpush_events WHERE action_id=? ORDER BY sequence LIMIT 4', (action_id,)).fetchall()
    if len(rows) > 3:
        raise LaneError('REMOTE_ACTION_INTEGRITY', 'A one-use push cannot have more than three state transitions.')
    previous = None
    for sequence, row in enumerate(rows, start=1):
        body = json.loads(row['body_json'])
        if (sequence != row['sequence'] or body.get('sequence') != sequence or body.get('action_id') != action_id
                or body.get('project_id') != store.project_id or body.get('previous_digest') != previous
                or row['digest'] != content_digest(body)):
            raise LaneError('REMOTE_ACTION_INTEGRITY', 'The prepared push event chain failed verification.')
        previous = row['digest']
    if not rows or head['digest'] != previous or head['sequence'] != len(rows):
        raise LaneError('REMOTE_ACTION_INTEGRITY', 'The prepared push current reference failed verification.')
    return {'digest': previous, 'body': body}


def append_push(context, body, previous=None):
    execution, store = context.execution, context.execution.store
    execution._before_more_work()
    apply_migrations(store, PUSH_MIGRATIONS, writer=execution.lease)
    with execution.lease.transaction('receipts') as db:
        current = push_record(store, body['action_id'], db)
        if (current['digest'] if current else None) != previous:
            raise LaneError('REMOTE_ACTION_STATE_CHANGED', 'Read the exact push state before another transition.')
        event = {**body, 'sequence': current['body']['sequence'] + 1 if current else 1,
            'previous_digest': previous, 'recorded_at': now()}
        digest = content_digest(event)
        db.execute('INSERT INTO gitpush_events VALUES(?,?,?,?)',
            (event['action_id'], event['sequence'], digest, json_text(event)))
        db.execute('INSERT INTO gitpush_current VALUES(?,?,?) ON CONFLICT(action_id) DO UPDATE '
            'SET sequence=excluded.sequence,digest=excluded.digest', (event['action_id'], event['sequence'], digest))
        store.append_receipt('remote_git_push_' + event['state'],
            {'action_id': event['action_id'], 'digest': digest}, connection=db)
    return {'digest': digest, 'body': event}


class RemoteGitController:
    def __init__(self, context):
        self.context = context
        self.execution = context.execution
        self.store = self.execution.store
        self.repository = self.store.source_root
        self.access = ProjectAccess(self.store)

    @staticmethod
    def _require_automatic_test_branch(branch):
        protected = {'main', 'master', 'develop', 'development', 'production', 'prod', 'release', 'stable'}
        if (branch in protected or branch.startswith(('release/', 'hotfix/'))
                or not branch.startswith(('agent/', 'test/', 'tests/', 'feature/', 'fix/', 'chore/', 'codex/'))):
            raise LaneError('REMOTE_PROTECTED_OR_NON_TEST_BRANCH_BLOCKED',
                'Push requires the exact registered non-protected development branch.')

    def transport(self, url, authentication, expected_provider=None):
        source, kind = _bounded_source(url)
        local = kind == 'LOCAL_GIT_SOURCE'
        if local and authentication != 'none':
            raise LaneError('GIT_CREDENTIAL_PROVIDER_INCOMPATIBLE', 'Local Git does not use network credentials.')
        def check_access():
            for permission in ('read', 'publish'):
                if permission not in self.context.permissions:
                    raise LaneError('PERMISSION_DENIED', 'This client lacks the required Git read or publish permission.')
                self.context.authorize(permission)
                self.access.authorize(self.context.client_id, permission, path=self.repository)
                if local:
                    self.access.authorize(self.context.client_id, permission, path=Path(source))
            if not local:
                if 'network' not in self.context.permissions:
                    raise LaneError('PERMISSION_DENIED', 'Network Git requires this client network permission.')
                self.context.authorize('network')
                self.access.authorize(self.context.client_id, 'network')
        check_access()
        self.execution.guard.extension_checks.append(check_access)
        self.source, self.local, self.https, self.ssh = source, local, kind == 'CREDENTIAL_FREE_HTTPS', kind == 'SSH_GIT_SOURCE'
        self.provider = _authentication(self.repository, kind, authentication)
        if expected_provider is not None and self.provider != expected_provider:
            raise LaneError('GIT_CREDENTIAL_PROVIDER_CHANGED', 'The prepared Git credential provider changed.')
        if local and self.run(['rev-parse', '--is-bare-repository'], repository=Path(source)).stdout.strip() != 'true':
            raise LaneError('REMOTE_LOCAL_BARE_REQUIRED', 'A local push destination must be an explicitly granted bare Git repository.')

    def run(self, arguments, *, repository=None, check=True, transport=False):
        self.execution.guard.spend_call()
        return workflow_git(repository or self.repository, arguments, check=check,
            tick=self.execution._before_more_work, https=self.https if transport else False,
            credential_provider=self.provider if transport and self.https else None,
            ssh_provider=self.provider if transport and self.ssh else None)

    def observe(self, branch, remote, url):
        self.execution.guard.spend_call()
        return branch_identity(self.repository, branch, remote, url, tick=self.execution._before_more_work)

    def remote_head(self, branch):
        result = self.run(['ls-remote', '--refs', '--heads', '--', self.source, 'refs/heads/' + branch], transport=True)
        rows = result.stdout.splitlines()
        if not rows:
            return None
        if (len(rows) != 1 or len(rows[0].split('\t')) != 2
                or rows[0].split('\t')[1] != 'refs/heads/' + branch
                or not re.fullmatch(r'[0-9a-f]{40}(?:[0-9a-f]{24})?', rows[0].split('\t')[0])):
            raise LaneError('REMOTE_REF_READBACK_INVALID', 'The exact remote ref readback is invalid or ambiguous.')
        return rows[0].split('\t')[0]

    def published_paths(self, commit, remote_commit):
        if remote_commit is not None:
            if self.run(['cat-file', '-e', remote_commit + '^{commit}'], check=False).returncode:
                raise LaneError('REMOTE_COMMIT_NOT_LOCAL', 'Sync the exact remote commit before preparing its fast-forward.')
            if self.run(['merge-base', '--is-ancestor', remote_commit, commit], check=False).returncode:
                raise LaneError('REMOTE_PUSH_NOT_FAST_FORWARD', 'The selected push must fast-forward the remote branch.')
            arguments = ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', remote_commit, commit, '--']
        else:
            arguments = ['ls-tree', '-r', '--name-only', '-z', commit]
        paths = list(filter(None, self.run(arguments).stdout.split('\0')))
        if len(paths) > 25000:
            raise LaneError('REMOTE_PUSH_PATH_BUDGET', 'Select a push within 25,000 changed paths.')
        for relative in paths:
            path = self.execution.guard.path(relative)
            self.access.authorize(self.context.client_id, 'publish', path=path)
        if not self.execution.guard.task.permitted_paths:
            raise LaneError('DELTA_PATH_SCOPE', 'The push task needs explicit source path scope.')
        return paths

    def prepare_push(self, request):
        self._require_automatic_test_branch(request.branch)
        self.transport(request.expected_remote_url, request.authentication)
        before = self.observe(request.branch, request.remote, request.expected_remote_url)
        authority = current_branch_authority(self.store)
        if not _authority_matches(authority, before) or authority['digest'] != request.expected_authority_digest:
            raise LaneError('REMOTE_TEST_BRANCH_NOT_EXACT_PROJECT_AUTHORITY', 'Select this exact branch and remote as project authority first.')
        if before['worktree']['head'] != request.expected_commit:
            raise LaneError('REMOTE_ACTION_SOURCE_STALE', 'The local commit differs from the requested push.')
        remote_commit = self.remote_head(request.branch)
        if remote_commit != request.expected_remote_commit:
            raise LaneError('REMOTE_ACTION_REMOTE_STALE', 'The remote branch differs from the expected pre-push commit.')
        paths = self.published_paths(request.expected_commit, remote_commit)
        website = require_current_website_plan_for_push(self.store, request.expected_commit, tick=self.execution._before_more_work)
        body = {'action_id': str(uuid4()), 'project_id': self.store.project_id, 'state': 'prepared',
            'branch': request.branch, 'remote': request.remote, 'remote_identity': before['remote_identity'],
            'source_root': str(self.repository), 'commit': request.expected_commit, 'tree': before['worktree']['tree'],
            'remote_commit_before': remote_commit, 'authority_digest': authority['digest'],
            'prepared_by': self.context.client_id, 'plan_revision': self.execution.plan_revision,
            'prepared_task_id': self.execution.task_id, 'prepared_job_id': self.execution.claim.job_id,
            'authentication': request.authentication, 'credential_provider': self.provider,
            'published_paths': paths, 'website_plan_gate': website, 'force_push': False,
            'main_merge_authorized': False, 'automatic_replay': False,
            'credential_values_stored': False, 'remote_write_performed': False}
        if self.observe(request.branch, request.remote, request.expected_remote_url) != before:
            raise LaneError('REMOTE_ACTION_SOURCE_STALE', 'The source changed during push preparation.')
        record = append_push(self.context, body)
        return PushResult(project_id=self.store.project_id, action_id=body['action_id'], digest=record['digest'], action=record['body'])

    def execute_push(self, request):
        record = push_record(self.store, request.action_id)
        if record is None:
            raise LaneError('REMOTE_ACTION_NOT_FOUND', 'Select an existing prepared Git push.')
        action = record['body']
        if action['state'] != 'prepared':
            raise LaneError('REMOTE_ACTION_ALREADY_CONSUMED', 'The push is consumed; inspect its evidence and reconcile it.')
        if record['digest'] != request.prepared_digest:
            raise LaneError('REMOTE_ACTION_STATE_CHANGED', 'Use the exact prepared push digest.')
        if action['prepared_by'] != self.context.client_id or action['plan_revision'] != self.execution.plan_revision:
            raise LaneError('REMOTE_ACTION_PLAN_OR_ACTOR_STALE', 'The prepared push belongs to another client or Plan revision.')
        self._require_automatic_test_branch(action['branch'])
        self.transport(request.expected_remote_url, action['authentication'], action['credential_provider'])
        before = self.observe(action['branch'], action['remote'], request.expected_remote_url)
        authority = current_branch_authority(self.store)
        if (not _authority_matches(authority, before) or authority['digest'] != action['authority_digest']
                or before['remote_identity'] != action['remote_identity'] or str(self.repository) != action['source_root']
                or before['worktree']['head'] != action['commit'] or before['worktree']['tree'] != action['tree']):
            append_push(self.context, {**action, 'state': 'invalidated', 'invalidation_reason': 'source_or_authority_changed'}, record['digest'])
            raise LaneError('REMOTE_ACTION_SOURCE_STALE', 'The branch, remote, authority, commit or tree changed after preparation.')
        if self.remote_head(action['branch']) != action['remote_commit_before']:
            raise LaneError('REMOTE_ACTION_REMOTE_STALE', 'The remote branch moved after preparation.')
        if self.published_paths(action['commit'], action['remote_commit_before']) != action['published_paths']:
            raise LaneError('REMOTE_ACTION_SOURCE_STALE', 'The committed path selection changed.')
        if require_current_website_plan_for_push(self.store, action['commit'], tick=self.execution._before_more_work) != action['website_plan_gate']:
            raise LaneError('REMOTE_WEBSITE_PLAN_PROJECTION_STALE', 'The website Plan gate changed after preparation.')
        consumed = append_push(self.context, {**action, 'state': 'consumed', 'executed_by': self.context.client_id,
            'execution_task_id': self.execution.task_id, 'execution_job_id': self.execution.claim.job_id}, record['digest'])
        # A crash even before effect preparation leaves consumption in place.
        effect = self.execution.prepare_effect('git-push:' + request.action_id, 'Push one pinned commit and verify the exact remote branch readback.')
        receiver = []
        if self.local:
            # A local Git transport clears the source command's -c settings.
            # Use our fixed receiver command, never repository receive-pack text.
            command = [Path(resolve_git_executable(self.repository)).as_posix(),
                '-c', 'core.hooksPath=' + os.devnull, '-c', 'core.fsmonitor=false', 'receive-pack']
            receiver = ['--receive-pack=' + shlex.join(command)]
        result = self.run(['push', '--porcelain', '--no-verify', '--no-follow-tags', '--recurse-submodules=no', *receiver,
            '--', self.source, action['commit'] + ':refs/heads/' + action['branch']], transport=True, check=False)
        inspection = inspect_agent_output(result.stdout + '\n' + result.stderr)
        # Keep original advisory inspection metadata, excluding provider prose:
        # regex redaction cannot prove absence of previously unknown secrets.
        inspection.pop('safe_output', None)
        inspection.pop('safe_infrastructure_error', None)
        observed = self.remote_head(action['branch'])
        after = self.observe(action['branch'], action['remote'], request.expected_remote_url)
        if observed != action['commit'] or after != before:
            raise LaneError('REMOTE_PUSH_OUTCOME_UNCERTAIN', 'The pinned remote ref and unchanged local bytes were not both confirmed.')
        _confirm(self.execution, effect, {'action_id': request.action_id, 'remote_commit': observed,
            'remote_identity': action['remote_identity'], 'local_before': before, 'local_after': after,
            'git_returncode': result.returncode, 'output_security': inspection})
        final = append_push(self.context, {**consumed['body'], 'state': 'confirmed', 'effect_id': effect,
            'remote_commit_after': observed, 'local_before': before, 'local_after': after,
            'git_returncode': result.returncode, 'output_security': inspection,
            'remote_write_attempted': True,
            'remote_write_performed': result.returncode == 0 and observed != action['remote_commit_before'],
            'remote_ref_change_observed': observed != action['remote_commit_before'],
            'remote_ref_readback_verified': True,
            'remote_preflight_is_atomic_compare_and_swap': False,
            'push_process_reported_success': result.returncode == 0}, consumed['digest'])
        return PushResult(project_id=self.store.project_id, action_id=request.action_id,
            digest=final['digest'], action=final['body'])


def verify_push(context, request, output):
    record = push_record(context.store, output.action_id)
    valid = bool(record and record['digest'] == output.digest and record['body'] == output.action)
    if isinstance(request, PushExecute):
        action = output.action
        from .git_adapter import restoration_source_identity
        valid = valid and action['state'] == 'confirmed' and action['remote_commit_after'] == action['commit']
        valid = valid and restoration_source_identity(context.store.source_root) == action['local_after']['worktree']
        with context.store.lane('plan').connection(read_only=True) as db:
            effect = db.execute('SELECT state FROM jobs_effects WHERE effect_id=?', (action['effect_id'],)).fetchone()
        valid = valid and bool(effect and effect[0] == 'confirmed')
    else:
        valid = valid and output.action['state'] == 'prepared'
    return [{'check_id': name, 'passed': bool(valid), 'evidence': {'action_id': output.action_id,
        'digest': output.digest, 'state': output.action['state']}} for name in context.requested_checks]


def register_remote_git_actions(engine):
    for name, model, method, checks in (
        ('remote_git_prepare_push', PushPrepare, 'prepare_push', ('git_push_preparation_integrity',)),
        ('remote_git_execute_push', PushExecute, 'execute_push', ('git_push_remote_readback', 'git_push_local_preservation')),
    ):
        def handler(context, request, method=method):
            return getattr(RemoteGitController(context), method)(request)
        engine.registry.register(ActionSpec(name, 'Prepare or execute one pinned, one-use development-branch Git push with remote readback.',
            model, PushResult, handler, permission='publish', mutates=True, profile='code', workflow='lifecycle',
            requires_delta=True, required_tools=('Python', 'SQLite_FTS5_BM25', 'Git'),
            verification_checks=checks, verifier=verify_push,
            tool_routes=(ToolRoute(name + '.exact_git', handler, ('Python', 'SQLite_FTS5_BM25', 'Git')),)))
    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return PushState(project_id=store.project_id, record=push_record(store, request.action_id))
    engine.registry.register(ActionSpec('remote_git_action_read', 'Read one recorded push state and integrity without contacting the remote.',
        PushRead, PushState, read, workflow='lifecycle', queryable_in_delta=True, studio_read=True, read_migrations=PUSH_MIGRATIONS))
