"""Exact Git branch selection and clean fast-forward under the current Delta.

Original enrollment owns these operations. Source/branch identities and history
live in Sources; Plan owns execution and effects; Receipts records publication.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import Field, field_validator

from .bounded_io import IOBudget
from .errors import LaneError
from .git_adapter import (
    git_credential_provider,
    git_ssh_provider,
    restoration_git,
    restoration_source_identity,
    workflow_git,
)
from .migrations import Migration, apply_migrations, read_compatibility
from .plan_runtime import content_digest
from .projects import ProjectAccess
from .registry import ActionSpec, Contract
from .source_fingerprint import measure_worktree_paths
from .storage import json_text, now, project_snapshot, reject_links
from .tool_routes import ToolRoute

GIT_BRANCH_MIGRATIONS: tuple[Migration, ...] = (Migration('gitbranch', 1, 'Exact selected Git branch and append-only sync history', (
    """CREATE TABLE gitbranch_history (generation INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
    """CREATE TABLE gitbranch_current (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       generation INTEGER NOT NULL REFERENCES gitbranch_history(generation), digest TEXT NOT NULL)""",
    """CREATE TABLE gitbranch_sync (sync_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
)),)

GIT_BRANCH_MIGRATIONS += (Migration('gitbranch', 2,
    'Exact repository enrollment outcomes under the selected project Plan', (
        """CREATE TABLE gitbranch_enrollment (enrollment_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
           body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
    )),)


class GitSyncSelection(Contract):
    source: str = Field(min_length=1, max_length=2000)
    branch: str = Field(min_length=1, max_length=200)
    expected_before_commit: str = Field(pattern=r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
    expected_commit: str = Field(pattern=r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
    remote: str = Field(default='origin', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    expected_remote_url: str = Field(min_length=1, max_length=2000)
    mode: Literal['select_local_authority', 'fast_forward']
    expected_authority_digest: str | None = Field(pattern=r'^[0-9a-f]{64}$')
    replace_branch_authority: bool = False
    authentication: Literal['none', 'host_git_credential_manager', 'host_openssh_agent'] = 'none'

    @field_validator('source', 'branch', 'expected_remote_url')
    @classmethod
    def no_controls(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError('Use an exact selection without control characters')
        return value


class GitEnrollmentSelection(Contract):
    source: str = Field(min_length=1, max_length=2000)
    branch: str = Field(min_length=1, max_length=200)
    expected_commit: str = Field(pattern=r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
    remote: str = Field(default='origin', pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$')
    authentication: Literal['none', 'host_git_credential_manager', 'host_openssh_agent'] = 'none'

    @field_validator('source', 'branch')
    @classmethod
    def no_controls(cls, value):
        return GitSyncSelection.no_controls(value)


class GitEnrollmentResult(Contract):
    project_id: str
    enrollment_id: str
    enrollment_digest: str
    authority_digest: str
    result: dict


class GitSyncResult(Contract):
    project_id: str
    sync_id: str
    sync_digest: str
    authority_digest: str
    result: dict


class GitBranchRead(Contract):
    pass


class GitBranchState(Contract):
    project_id: str
    authority: dict | None
    live_repository_observed: bool = False


def _bounded_source(source: str) -> tuple[str, str]:
    """Exact local, HTTPS or literal SSH selection; never shell/provider syntax."""
    source_path = Path(source)
    if source_path.is_absolute() and source_path.is_dir():
        reject_links(source_path, Path(source_path.anchor))
        return str(source_path.resolve()), 'LOCAL_GIT_SOURCE'
    if _ssh_source(source) is not None:
        return source, 'SSH_GIT_SOURCE'
    parsed = urlparse(source)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or any(c.isspace() for c in source) or '\\' in source):
        raise LaneError('PROJECT_SYNC_SOURCE_INVALID',
            'Select an absolute local Git folder, an exact HTTPS URL without credentials, or a literal SSH user, host and repository path.')
    return source, 'CREDENTIAL_FREE_HTTPS'


def _ssh_source(source):
    # Limiting the literal transport grammar also prevents remote-shell option,
    # expansion and command injection. SSH aliases/config/proxies are separate.
    user = r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}'
    host = r'[A-Za-z0-9][A-Za-z0-9.-]{0,252}'
    path = r'/?[A-Za-z0-9_][A-Za-z0-9_./-]{0,1499}'
    match = re.fullmatch(rf'(?P<user>{user})@(?P<host>{host}):(?P<path>{path})', source)
    if match is None:
        match = re.fullmatch(rf'ssh://(?P<user>{user})@(?P<host>{host})(?::(?P<port>[0-9]{{1,5}}))?/(?P<path>{path})', source)
    if match is None:
        return None
    values = match.groupdict()
    if any(part in {'', '.', '..'} for part in values['path'].lstrip('/').split('/')):
        return None
    port = values.get('port')
    if port is not None and not 1 <= int(port) <= 65535:
        return None
    return values


def selected_remote(repository, remote, expected_url, *, reader=None):
    reader = reader or restoration_git
    expected, _ = _bounded_source(expected_url)
    fetch = reader(repository, ['remote', 'get-url', '--all', remote]).stdout.splitlines()
    push = reader(repository, ['remote', 'get-url', '--push', '--all', remote]).stdout.splitlines()
    if len(fetch) != 1 or len(push) != 1:
        raise LaneError('GIT_REMOTE_AMBIGUOUS', 'Select one fetch URL and the same single push URL.')
    actual, _ = _bounded_source(fetch[0])
    actual_push, _ = _bounded_source(push[0])
    if actual != expected or actual_push != expected:
        raise LaneError('GIT_REMOTE_IDENTITY_MISMATCH', 'The named fetch/push remote differs from the exact selection.')
    parsed = urlparse(expected)
    parts = parsed.path.strip('/').split('/')
    ssh = _ssh_source(expected)
    if ssh:
        parts = ssh['path'].strip('/').split('/')
    network = parsed.scheme == 'https' or ssh is not None
    return {'remote': remote, 'url_digest': content_digest(expected),
        'hostname': ssh['host'] if ssh else parsed.hostname if network else None,
        'owner': parts[-2] if network and len(parts) > 1 else None,
        'name': parts[-1].removesuffix('.git') if network else Path(expected).name,
        'credentials_stored': False}


def branch_identity(repository, branch, remote, expected_url, *, tick=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', branch):
        raise LaneError('GIT_BRANCH_INVALID', 'Select an exact existing local branch name.')
    workflow_git(repository, ['check-ref-format', '--branch', branch], tick=tick)
    current = restoration_git(repository, ['symbolic-ref', '--quiet', '--short', 'HEAD']).stdout.strip()
    if current != branch:
        raise LaneError('GIT_BRANCH_MISMATCH', 'The checkout must already be on the exact selected branch.')
    return {'source_root': str(repository), 'branch': branch,
        'remote_identity': selected_remote(repository, remote, expected_url),
        'worktree': restoration_source_identity(repository, tick=tick)}


def current_branch_authority(store, connection=None):
    lane = store.lane('sources')
    if connection is None:
        if read_compatibility(lane, GIT_BRANCH_MIGRATIONS)[0]['status'] == 'not_initialized':
            return None
        with lane.connection(read_only=True) as db:
            return current_branch_authority(store, db)
    row = connection.execute('SELECT h.* FROM gitbranch_current c JOIN gitbranch_history h ON h.generation=c.generation '
        'AND h.digest=c.digest WHERE c.singleton=1').fetchone()
    head = connection.execute('SELECT * FROM gitbranch_current WHERE singleton=1').fetchone()
    if row is None:
        if head is not None:
            raise LaneError('GIT_BRANCH_AUTHORITY_INTEGRITY', 'The selected branch history reference is invalid.')
        return None
    body = json.loads(row['body_json'])
    if content_digest(body) != row['digest'] or body['project_id'] != store.project_id:
        raise LaneError('GIT_BRANCH_AUTHORITY_INTEGRITY', 'The selected branch record failed its identity check.')
    return {'generation': row['generation'], 'digest': row['digest'], 'body': body}


def _authority_matches(authority, identity):
    return authority and all(authority['body'][key] == identity[key]
        for key in ('source_root', 'branch', 'remote_identity'))


def _confirm(execution, effect, body):
    with execution.lease.transaction('plan'):
        evidence = execution.plan_store.put_object(json_text(body).encode())
        execution.confirm_effect(effect, evidence)



def _source_file_fingerprint(context, paths):
    """Measure this Git operation's exact file/deletion scope after its effect."""
    from .projects import ProjectAccess
    execution = context.execution
    def authorize(path):
        execution.guard.path(str(path))
        ProjectAccess(execution.store).authorize(context.client_id, 'read', path=path)
    return measure_worktree_paths(execution.store.source_root, paths,
        budget=IOBudget(max_file_count=25_000, max_file_bytes=64 * 1024 * 1024,
            max_aggregate_bytes=256 * 1024 * 1024),
        tick=execution._before_more_work, authorize=authorize)


def _publish_file_fingerprint(store, fingerprint, scope):
    if fingerprint is None:
        return None
    object_id = store.lane('sources').put_object(json_text(fingerprint).encode(), limit=16_777_216)
    return {'object_id': object_id, 'receipt_sha256': fingerprint['receipt_sha256'],
        'file_manifest_sha256': fingerprint['file_manifest_sha256'],
        'path_set_sha256': fingerprint['path_set_sha256'], 'path_count': fingerprint['path_count'],
        'scope': scope, 'source_index_refreshed': False}


def _verify_file_fingerprint(context, reference, paths, scope):
    if not isinstance(reference, dict) or reference.get('scope') != scope:
        return False
    lane = context.store.lane('sources')
    with lane.connection(read_only=True) as db:
        row = db.execute('SELECT size_bytes FROM objects WHERE digest=?', (reference.get('object_id'),)).fetchone()
    if row is None or not 0 < row['size_bytes'] <= 16_777_216:
        return False
    recorded = json.loads(lane.read_object(reference['object_id']))
    observed = measure_worktree_paths(context.store.source_root, paths,
        budget=IOBudget(max_file_count=25_000, max_file_bytes=64 * 1024 * 1024,
            max_aggregate_bytes=256 * 1024 * 1024),
        authorize=lambda path: context.source_path(str(path)))
    return recorded == observed and reference == {
        'object_id': reference['object_id'], 'receipt_sha256': observed['receipt_sha256'],
        'file_manifest_sha256': observed['file_manifest_sha256'],
        'path_set_sha256': observed['path_set_sha256'], 'path_count': observed['path_count'],
        'scope': scope, 'source_index_refreshed': False}


def _verify_git_result_binding(context, output, proofs):
    """Bind source evidence to this exact Plan run and confirmed Git effects."""
    body = output.result
    if output.project_id != context.store.project_id or any(body.get(key) != value for key, value in {
            'project_id': context.store.project_id, 'job_id': context.job_id,
            'task_id': context.task_id, 'plan_revision': context.plan_revision}.items()):
        return False
    lane = context.store.lane('plan')
    with lane.connection(read_only=True) as db:
        rows = db.execute('SELECT * FROM jobs_effects WHERE job_id=? ORDER BY rowid LIMIT 3',
            (context.job_id,)).fetchall()
        if (len(rows) != len(proofs) or len(rows) > 2
                or [row['effect_id'] for row in rows] != body.get('effects')
                or {row['effect_key'] for row in rows} != set(proofs)
                or any(row['state'] != 'confirmed' for row in rows)):
            return False
        for row in rows:
            size = db.execute('SELECT size_bytes FROM objects WHERE digest=?', (row['evidence_object'],)).fetchone()
            if size is None or not 0 < size['size_bytes'] <= 8_388_608:
                return False
            observed = json.loads(lane.read_object(row['evidence_object']))
            if not all(observed.get(key) == value for key, value in proofs[row['effect_key']].items()):
                return False
    return True


def _authentication(repository, kind, selection):
    if selection == 'none':
        if kind == 'SSH_GIT_SOURCE':
            raise LaneError('GIT_SSH_AUTHENTICATION_REQUIRED', 'SSH transport requires the explicit host OpenSSH agent route.')
        return None
    if selection == 'host_openssh_agent':
        if kind != 'SSH_GIT_SOURCE':
            raise LaneError('GIT_AUTHENTICATION_SOURCE_MISMATCH', 'Host OpenSSH agent authentication requires an SSH source.')
        return git_ssh_provider(repository)
    if kind != 'CREDENTIAL_FREE_HTTPS':
        raise LaneError('GIT_AUTHENTICATION_SOURCE_MISMATCH', 'Host Git Credential Manager requires an HTTPS source.')
    return git_credential_provider(repository)


def sync_selected_branch(context, request):
    """Select exact local authority without Git writes, or perform one clean FF."""
    execution, store = context.execution, context.execution.store
    repository = store.source_root
    access = ProjectAccess(store)
    for permission in ('read', 'write'):
        access.authorize(context.client_id, permission, path=repository)
    if not execution.guard.task.permitted_paths:
        raise LaneError('DELTA_PATH_SCOPE', 'The current Git task needs explicit source path scope.')
    execution._before_more_work()
    source, kind = _bounded_source(request.source)
    provider = _authentication(repository, kind, request.authentication)
    def source_access():
        context.authorize('read')
        access.authorize(context.client_id, 'read', path=repository)
        access.authorize(context.client_id, 'write', path=repository)
        if kind == 'LOCAL_GIT_SOURCE':
            access.authorize(context.client_id, 'read', path=Path(source))
        else:
            if 'network' not in context.permissions:
                raise LaneError('PERMISSION_DENIED', 'Network Git sync requires this client network permission.')
            context.authorize('network')
            access.authorize(context.client_id, 'network')
    source_access()
    execution.guard.extension_checks.append(source_access)
    tick = execution._before_more_work
    def observe():
        execution.guard.spend_call()
        return branch_identity(repository, request.branch, request.remote, request.expected_remote_url, tick=tick)
    def run(root, args, **kwargs):
        execution.guard.spend_call()
        return workflow_git(root, args, tick=tick, **kwargs)
    before = observe()
    if before['worktree']['head'] != request.expected_before_commit:
        raise LaneError('PROJECT_SYNC_SOURCE_STALE', 'The selected checkout moved from the expected starting commit.')
    authority = current_branch_authority(store)
    if (authority['digest'] if authority else None) != request.expected_authority_digest:
        raise LaneError('GIT_BRANCH_AUTHORITY_STALE', 'Read the current exact branch authority before replacing or using it.')
    if not _authority_matches(authority, before) and not request.replace_branch_authority:
        raise LaneError('GIT_BRANCH_REPLACEMENT_REQUIRED', 'Explicitly select this exact branch and named remote as project authority.')
    effects, changed = [], []
    sync_id = str(uuid4())
    if request.mode == 'select_local_authority':
        if (kind != 'LOCAL_GIT_SOURCE' or Path(source) != repository
                or request.expected_commit != request.expected_before_commit or not request.replace_branch_authority):
            raise LaneError('GIT_LOCAL_AUTHORITY_SELECTION_REQUIRED',
                'Byte-preserving selection requires this exact local checkout, its current commit and explicit branch replacement.')
    else:
        if not before['worktree']['clean']:
            raise LaneError('WORKTREE_NOT_CLEAN', 'Fetch and fast-forward require a clean selected checkout.')
        if kind == 'LOCAL_GIT_SOURCE':
            # Inspect the exact source root and named branch, never a parent repo.
            local = Path(source)
            top = run(local, ['rev-parse', '--show-toplevel']).stdout.strip()
            observed = restoration_git(local, ['rev-parse', '--verify', 'refs/heads/' + request.branch]).stdout.strip()
            if Path(top).resolve() != local or observed != request.expected_commit:
                raise LaneError('PROJECT_SYNC_EXPECTED_COMMIT_MISMATCH', 'The selected source branch differs from the exact target commit.')
        fetch_effect = execution.prepare_effect('git-fetch:' + sync_id, 'Fetch one selected branch and verify its exact commit.')
        effects.append(fetch_effect)
        run(repository, ['fetch', '--no-tags', '--no-recurse-submodules', '--no-auto-maintenance',
            '--', source, 'refs/heads/' + request.branch], https=kind == 'CREDENTIAL_FREE_HTTPS',
            credential_provider=provider if kind == 'CREDENTIAL_FREE_HTTPS' else None,
            ssh_provider=provider if kind == 'SSH_GIT_SOURCE' else None)
        fetched = restoration_git(repository, ['rev-parse', '--verify', 'FETCH_HEAD^{commit}']).stdout.strip()
        after_fetch = observe()
        if before != after_fetch:
            raise LaneError('GIT_FETCH_SOURCE_CHANGED', 'The checkout changed during fetch; reconcile its recorded effect.')
        _confirm(execution, fetch_effect, {'sync_id': sync_id, 'fetched_commit': fetched,
            'checkout_unchanged': True, 'metadata_fetch_completed': True})
        if fetched != request.expected_commit:
            raise LaneError('PROJECT_SYNC_EXPECTED_COMMIT_MISMATCH', 'The fetched branch differs from the exact target commit.')
        ancestor = run(repository, ['merge-base', '--is-ancestor', request.expected_before_commit, fetched], check=False)
        if ancestor.returncode:
            raise LaneError('PROJECT_SYNC_NOT_FAST_FORWARD', 'The selected commit is not a fast-forward of this checkout.')
        changed = [path for path in run(repository,
            ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z', request.expected_before_commit, fetched, '--']
            ).stdout.split('\0') if path]
        if len(changed) > 25000:
            raise LaneError('GIT_SYNC_PATH_BUDGET', 'Select a fast-forward within 25,000 changed paths.')
        for relative in changed:
            path = execution.guard.path(relative)
            access.authorize(context.client_id, 'write', path=path)
        # A changed symlink/gitlink can redirect later path consumers. Admit
        # ordinary file entries only; submodule/LFS materialization is separate.
        tree = run(repository, ['ls-tree', '-r', '-z', '-l', fetched]).stdout
        changed_set = set(changed)
        source_bytes = 0
        for entry in filter(None, tree.split('\0')):
            metadata, relative = entry.split('\t', 1)
            if relative not in changed_set:
                continue
            mode, kind_name, _, size = metadata.split()
            if mode not in {'100644', '100755'} or kind_name != 'blob':
                raise LaneError('GIT_SYNC_TREE_UNSUPPORTED', 'Changed symlinks and submodules require an explicit separate operation.')
            source_bytes += int(size)
            if int(size) > 64 * 1024 * 1024 or source_bytes > 256 * 1024 * 1024:
                raise LaneError('GIT_SYNC_FILE_BUDGET', 'The selected file changes exceed the 64 MiB per-file or 256 MiB total bound.')
        if fetched != request.expected_before_commit:
            merge_effect = execution.prepare_effect('git-fast-forward:' + sync_id, 'Fast-forward the exact branch and verify its clean target commit.')
            effects.append(merge_effect)
            tick()
            access.authorize(context.client_id, 'write', path=repository)
            if observe() != before:
                raise LaneError('PROJECT_SYNC_SOURCE_STALE', 'The checkout changed immediately before fast-forward.')
            run(repository, ['merge', '--ff-only', '--no-edit', '--no-stat', '--no-overwrite-ignore', fetched])
    after = observe()
    if (after['worktree']['head'] != request.expected_commit or
            (request.mode == 'select_local_authority' and before != after) or
            (request.mode == 'fast_forward' and not after['worktree']['clean'])):
        raise LaneError('GIT_SYNC_VERIFY_FAILED', 'The exact resulting source identity was not confirmed.')
    if request.mode == 'fast_forward' and len(effects) == 2:
        _confirm(execution, effects[-1], {'sync_id': sync_id, 'after': after, 'changed_paths': changed})
    fingerprint = _source_file_fingerprint(context, changed) if request.mode == 'fast_forward' else None
    tick()
    apply_migrations(store, GIT_BRANCH_MIGRATIONS, writer=execution.lease)
    lane = store.lane('sources')
    with execution.lease.coordinated_transaction(['sources', 'receipts']):
        with lane.transaction() as connection:
            live = current_branch_authority(store, connection)
        if (live['digest'] if live else None) != request.expected_authority_digest:
            raise LaneError('GIT_BRANCH_AUTHORITY_STALE', 'The branch authority changed before sync publication.')
        if not _authority_matches(live, after) or request.replace_branch_authority:
            generation = (live['generation'] if live else 0) + 1
            body = {'project_id': store.project_id, 'generation': generation,
                'previous_digest': request.expected_authority_digest, 'source_root': str(repository),
                'branch': request.branch, 'remote_identity': after['remote_identity'],
                'actor_id': context.client_id, 'plan_revision': execution.plan_revision,
                'task_id': execution.task_id, 'selected_at': now()}
            digest = content_digest(body)
            with lane.transaction() as db:
                db.execute('INSERT INTO gitbranch_history VALUES(?,?,?)', (generation, digest, json_text(body)))
                db.execute('INSERT INTO gitbranch_current VALUES(1,?,?) ON CONFLICT(singleton) DO UPDATE '
                    'SET generation=excluded.generation,digest=excluded.digest', (generation, digest))
        else:
            digest = live['digest']
        body = {'sync_id': sync_id, 'project_id': store.project_id, 'mode': request.mode,
            'before': before, 'after': after, 'authority_digest': digest,
            'changed_paths': changed, 'effects': effects, 'actor_id': context.client_id,
            'plan_revision': execution.plan_revision, 'task_id': execution.task_id,
            'job_id': execution.claim.job_id, 'recorded_at': now(),
            'fetch_performed': request.mode == 'fast_forward',
            'source_bytes_mutated': before['worktree']['head'] != after['worktree']['head'],
            'remote_write_performed': False, 'merge_commit_created': False,
            'source_reindex_required': before['worktree']['head'] != after['worktree']['head'],
            'source_file_fingerprint': _publish_file_fingerprint(store, fingerprint, 'exact_git_changed_paths'),
            'transport_authentication': request.authentication, 'credential_provider': provider,
            'authentication_observed': False, 'automatic_replay': False}
        sync_digest = content_digest(body)
        with lane.transaction() as db:
            db.execute('INSERT INTO gitbranch_sync VALUES(?,?,?)', (sync_id, sync_digest, json_text(body)))
        store.append_receipt('git_selected_branch_synced', {'sync_id': sync_id, 'sync_digest': sync_digest,
            'authority_digest': digest, 'mode': request.mode})
    return GitSyncResult(project_id=store.project_id, sync_id=sync_id, sync_digest=sync_digest,
        authority_digest=digest, result=body)


def verify_selected_sync(context, request, output):
    lane = context.store.lane('sources')
    with lane.connection(read_only=True) as db:
        row = db.execute('SELECT * FROM gitbranch_sync WHERE sync_id=?', (output.sync_id,)).fetchone()
    observed = branch_identity(context.store.source_root, request.branch, request.remote, request.expected_remote_url)
    authority = current_branch_authority(context.store)
    valid = bool(row and row['digest'] == output.sync_digest == content_digest(output.result)
        and json.loads(row['body_json']) == output.result and observed == output.result['after']
        and authority and authority['digest'] == output.authority_digest)
    valid &= (output.result.get('mode') == request.mode
        and output.result['before']['worktree']['head'] == request.expected_before_commit
        and observed['worktree']['head'] == request.expected_commit)
    proofs = {}
    if request.mode == 'fast_forward':
        changed = [path for path in workflow_git(context.store.source_root,
            ['diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--name-only', '-z',
             request.expected_before_commit, request.expected_commit, '--']).stdout.split('\0') if path]
        valid &= output.result['changed_paths'] == changed
        proofs['git-fetch:' + output.sync_id] = {'sync_id': output.sync_id,
            'fetched_commit': request.expected_commit, 'checkout_unchanged': True, 'metadata_fetch_completed': True}
        if request.expected_commit != request.expected_before_commit:
            proofs['git-fast-forward:' + output.sync_id] = {'sync_id': output.sync_id,
                'after': observed, 'changed_paths': changed}
    else:
        changed = []
        valid &= output.result['changed_paths'] == []
    valid &= _verify_git_result_binding(context, output, proofs)
    valid &= (_verify_file_fingerprint(context, output.result.get('source_file_fingerprint'),
        changed, 'exact_git_changed_paths') if request.mode == 'fast_forward'
        else output.result.get('source_file_fingerprint') is None)
    return [{'check_id': name, 'passed': valid, 'evidence': {'sync_id': output.sync_id,
        'sync_digest': output.sync_digest, 'commit': observed['worktree']['head'],
        'mode': request.mode}} for name in context.requested_checks]


def register_git_sync_actions(engine):
    engine.registry.register(ActionSpec('enroll_project',
        'Clone an exact selected branch into this project\'s empty source folder with journaled effects and preserved partial output.',
        GitEnrollmentSelection, GitEnrollmentResult, enroll_project, permission='write', mutates=True,
        profile='code', workflow='run-project-lifecycle', requires_delta=True,
        tool_routes=(ToolRoute('enroll_project.exact_git', enroll_project, ('Python', 'SQLite_FTS5_BM25', 'Git')),),
        verification_checks=('git_enrollment_identity', 'git_branch_authority_integrity', 'git_enrolled_source_fingerprint'),
        verifier=verify_enrollment))
    engine.registry.register(ActionSpec('git_sync_selected',
        'Select exact local branch authority without source changes, or fetch and apply one scoped clean fast-forward.',
        GitSyncSelection, GitSyncResult, sync_selected_branch, permission='write', mutates=True,
        profile='code', workflow='run-project-lifecycle', requires_delta=True,
        verification_checks=('git_selected_source_identity', 'git_branch_authority_integrity', 'git_changed_source_fingerprint'), verifier=verify_selected_sync,
        tool_routes=(ToolRoute('git_sync_selected.exact_git', sync_selected_branch, ('Python', 'SQLite_FTS5_BM25', 'Git')),)))

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return GitBranchState(project_id=store.project_id, authority=current_branch_authority(store))
    engine.registry.register(ActionSpec('git_branch_authority', 'Read the exact recorded branch and named remote selection without inspecting or changing Git.',
        GitBranchRead, GitBranchState, read, workflow='run-project-lifecycle', queryable_in_delta=True,
        studio_read=True, read_migrations=GIT_BRANCH_MIGRATIONS))


def enroll_project(context, request):
    """Populate an explicitly selected empty source root under the current Delta.

    Local adoption is owned by project_register and git_sync_selected. Cloning
    starts only after external project state and its Plan exist. Uncertain or
    partial output remains in place with its prepared effect; no cleanup/replay.
    """
    execution, store = context.execution, context.execution.store
    repository = store.source_root
    access = ProjectAccess(store)
    source, kind = _bounded_source(request.source)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}', request.branch):
        raise LaneError('GIT_BRANCH_INVALID', 'Select an exact branch name.')
    if not execution.guard.task.permitted_paths:
        raise LaneError('DELTA_PATH_SCOPE', 'Enrollment needs explicit source path scope.')

    def authorize():
        context.authorize('read')
        for permission in ('read', 'write'):
            access.authorize(context.client_id, permission, path=repository)
        if kind == 'LOCAL_GIT_SOURCE':
            access.authorize(context.client_id, 'read', path=Path(source))
        else:
            if 'network' not in context.permissions:
                raise LaneError('PERMISSION_DENIED', 'Network enrollment requires this client network permission.')
            context.authorize('network')
            access.authorize(context.client_id, 'network')

    authorize()
    execution.guard.extension_checks.append(authorize)
    tick = execution._before_more_work

    def run(root, args, **options):
        execution.guard.spend_call()
        return workflow_git(root, args, tick=tick, **options)

    def empty_identity():
        reject_links(repository, Path(repository.anchor))
        if not repository.is_dir() or any(repository.iterdir()):
            raise LaneError('PROJECT_ENROLL_WORKSPACE_EXISTS',
                'Enrollment preserves existing files. Select an empty source folder or adopt the existing local checkout.')
        value = repository.stat()
        return {'source_root': str(repository), 'device': value.st_dev, 'inode': value.st_ino, 'empty': True}

    tick()
    before = empty_identity()
    if current_branch_authority(store) is not None:
        raise LaneError('GIT_BRANCH_REPLACEMENT_REQUIRED', 'This project already has a Git branch authority. Use its explicit source workflow.')
    provider = _authentication(repository, kind, request.authentication)
    # check-ref-format does not need repository discovery or write anything.
    execution.guard.spend_call()
    restoration_git(repository, ['check-ref-format', '--branch', request.branch])
    if kind == 'LOCAL_GIT_SOURCE':
        local = Path(source)
        if local == repository or local.is_relative_to(repository) or repository.is_relative_to(local):
            raise LaneError('PROJECT_ENROLL_SOURCE_OVERLAP', 'The local clone source and destination must be separate roots.')
        top = run(local, ['rev-parse', '--absolute-git-dir']).stdout.strip()
        reject_links(Path(top), Path(Path(top).anchor))
        observed = run(local, ['rev-parse', '--verify', 'refs/heads/' + request.branch + '^{commit}']).stdout.strip()
        if observed != request.expected_commit:
            raise LaneError('PROJECT_ENROLL_EXPECTED_COMMIT_MISMATCH', 'The selected source branch differs from the expected commit.')
    enrollment_id = str(uuid4())
    tick()
    if empty_identity() != before:
        raise LaneError('PROJECT_ENROLL_SOURCE_STALE', 'The empty source folder changed before enrollment.')
    clone_effect = execution.prepare_effect('git-clone:' + enrollment_id,
        'Clone the exact selected branch without checkout; preserve and inspect any partial output.')
    run(repository, ['clone', '--template=', '--no-checkout', '--no-tags', '--single-branch', '--no-local',
        '--branch', request.branch, '--origin', request.remote, '--', source, '.'],
        https=kind == 'CREDENTIAL_FREE_HTTPS', empty_clone=True,
        credential_provider=provider if kind == 'CREDENTIAL_FREE_HTTPS' else None,
        ssh_provider=provider if kind == 'SSH_GIT_SOURCE' else None)
    commit = run(repository, ['rev-parse', '--verify', 'HEAD^{commit}']).stdout.strip()
    remote_identity = selected_remote(repository, request.remote, source)
    _confirm(execution, clone_effect, {'enrollment_id': enrollment_id, 'observed_commit': commit,
        'remote_identity': remote_identity, 'clone_completed': True, 'checkout_performed': False})
    if commit != request.expected_commit:
        raise LaneError('PROJECT_ENROLL_EXPECTED_COMMIT_MISMATCH',
            'The cloned branch moved from the expected commit. Preserve the clone and select explicit recovery.')
    entries = run(repository, ['ls-tree', '-r', '-z', '-l', commit]).stdout.split('\0')
    paths, source_bytes = [], 0
    for entry in filter(None, entries):
        metadata, relative = entry.split('\t', 1)
        mode, object_kind, _, size = metadata.split()
        if mode not in {'100644', '100755'} or object_kind != 'blob':
            raise LaneError('GIT_ENROLL_TREE_UNSUPPORTED', 'Symlink and submodule checkout need separately supported materialization.')
        source_bytes += int(size)
        paths.append(relative)
        if len(paths) > 25000 or int(size) > 64 * 1024 * 1024 or source_bytes > 256 * 1024 * 1024:
            raise LaneError('GIT_ENROLL_FILE_BUDGET', 'Enrollment exceeds 25,000 files, 64 MiB per file or 256 MiB total content.')
        access.authorize(context.client_id, 'write', path=execution.guard.path(relative))
    # The no-checkout clone should contain metadata only. Do not silently admit
    # concurrent user files, even if checkout would not collide with them.
    if [item.name for item in repository.iterdir()] != ['.git']:
        raise LaneError('PROJECT_ENROLL_SOURCE_STALE', 'Source files appeared during clone. Preserve them before recovery.')
    checkout_effect = execution.prepare_effect('git-clone-checkout:' + enrollment_id,
        'Materialize only the scoped ordinary files from the verified exact clone commit.')
    tick()
    # Use the verified SHA for materialization even if an external writer moves
    # the local branch between validation and checkout. Reattach without changing
    # that branch only after confirming it still names the selected commit.
    run(repository, ['checkout', '--no-guess', '--no-overwrite-ignore', '--detach', commit, '--'])
    if run(repository, ['rev-parse', '--verify', 'refs/heads/' + request.branch]).stdout.strip() != commit:
        raise LaneError('GIT_ENROLL_BRANCH_MOVED', 'The local branch moved during checkout; preserve and reconcile the exact materialized commit.')
    run(repository, ['symbolic-ref', 'HEAD', 'refs/heads/' + request.branch])
    execution.guard.spend_call()
    after = branch_identity(repository, request.branch, request.remote, source, tick=tick)
    if after['worktree']['head'] != request.expected_commit or not after['worktree']['clean']:
        raise LaneError('GIT_ENROLL_VERIFY_FAILED', 'The clean exact enrolled checkout was not confirmed.')
    _confirm(execution, checkout_effect, {'enrollment_id': enrollment_id, 'after': after,
        'file_count': len(paths), 'source_bytes': source_bytes})
    fingerprint = _source_file_fingerprint(context, paths)
    tick()
    apply_migrations(store, GIT_BRANCH_MIGRATIONS, writer=execution.lease)
    lane = store.lane('sources')
    with execution.lease.coordinated_transaction(['sources', 'receipts']):
        with lane.transaction() as db:
            if current_branch_authority(store, db) is not None:
                raise LaneError('GIT_BRANCH_AUTHORITY_STALE', 'Another branch authority appeared before enrollment publication.')
            authority = {'project_id': store.project_id, 'generation': 1, 'previous_digest': None,
                'source_root': str(repository), 'branch': request.branch, 'remote_identity': after['remote_identity'],
                'actor_id': context.client_id, 'plan_revision': execution.plan_revision,
                'task_id': execution.task_id, 'selected_at': now()}
            authority_digest = content_digest(authority)
            db.execute('INSERT INTO gitbranch_history VALUES(1,?,?)', (authority_digest, json_text(authority)))
            db.execute('INSERT INTO gitbranch_current VALUES(1,1,?)', (authority_digest,))
            body = {'enrollment_id': enrollment_id, 'project_id': store.project_id,
                'enrollment_mode': {'LOCAL_GIT_SOURCE': 'CLONED_LOCAL_SOURCE',
                    'CREDENTIAL_FREE_HTTPS': 'CLONED_HTTPS_SOURCE', 'SSH_GIT_SOURCE': 'CLONED_SSH_SOURCE'}[kind],
                'source_kind': kind,
                'before': before, 'after': after, 'authority_digest': authority_digest,
                'effects': [clone_effect, checkout_effect], 'actor_id': context.client_id,
                'plan_revision': execution.plan_revision, 'task_id': execution.task_id,
                'job_id': execution.claim.job_id, 'recorded_at': now(), 'file_count': len(paths),
                'source_bytes': source_bytes, 'cloned': True, 'source_bytes_mutated': True,
                'existing_lineage_overwritten': False, 'source_reindex_required': True,
                'source_file_fingerprint': _publish_file_fingerprint(store, fingerprint, 'exact_enrolled_tree_paths'),
                'project_state_copied': False, 'remote_write_performed': False,
                'transport_authentication': request.authentication, 'credential_provider': provider,
                'authentication_observed': False, 'automatic_replay': False,
                'submodules_initialized': False, 'lfs_objects_downloaded': False,
                'network_transfer_bytes_measured': False}
            digest = content_digest(body)
            db.execute('INSERT INTO gitbranch_enrollment VALUES(?,?,?)', (enrollment_id, digest, json_text(body)))
        store.append_receipt('git_repository_enrolled', {'enrollment_id': enrollment_id,
            'enrollment_digest': digest, 'authority_digest': authority_digest})
    return GitEnrollmentResult(project_id=store.project_id, enrollment_id=enrollment_id,
        enrollment_digest=digest, authority_digest=authority_digest, result=body)


def verify_enrollment(context, request, output):
    with context.store.lane('sources').connection(read_only=True) as db:
        row = db.execute('SELECT * FROM gitbranch_enrollment WHERE enrollment_id=?', (output.enrollment_id,)).fetchone()
    observed = branch_identity(context.store.source_root, request.branch, request.remote, request.source)
    authority = current_branch_authority(context.store)
    valid = bool(row and row['digest'] == output.enrollment_digest == content_digest(output.result)
        and json.loads(row['body_json']) == output.result and observed == output.result['after']
        and authority and authority['digest'] == output.authority_digest)
    valid &= observed['worktree']['head'] == request.expected_commit
    valid &= _verify_git_result_binding(context, output, {
        'git-clone:' + output.enrollment_id: {'enrollment_id': output.enrollment_id,
            'observed_commit': request.expected_commit, 'remote_identity': observed['remote_identity'],
            'clone_completed': True, 'checkout_performed': False},
        'git-clone-checkout:' + output.enrollment_id: {'enrollment_id': output.enrollment_id,
            'after': observed, 'file_count': output.result['file_count'], 'source_bytes': output.result['source_bytes']}})
    reference = output.result.get('source_file_fingerprint')
    with context.store.lane('sources').connection(read_only=True) as db:
        size = db.execute('SELECT size_bytes FROM objects WHERE digest=?',
            (reference.get('object_id') if isinstance(reference, dict) else None,)).fetchone()
    if size is None or not 0 < size['size_bytes'] <= 16_777_216:
        valid = False
    else:
        fingerprint = json.loads(context.store.lane('sources').read_object(reference['object_id']))
        paths = [row['path'] for row in fingerprint['entries']]
        # Bind the recorded enrollment scope to the independently observed
        # current index, rather than accepting a self-reported subset.
        index = workflow_git(context.store.source_root, ['ls-files', '-z', '--cached']).stdout
        valid &= sorted(paths) == sorted(set(filter(None, index.split('\0'))))
        valid &= len(paths) == output.result['file_count']
        valid &= _verify_file_fingerprint(context, reference, paths, 'exact_enrolled_tree_paths')
    return [{'check_id': name, 'passed': valid, 'evidence': {'enrollment_id': output.enrollment_id,
        'enrollment_digest': output.enrollment_digest, 'commit': observed['worktree']['head']}}
        for name in context.requested_checks]
