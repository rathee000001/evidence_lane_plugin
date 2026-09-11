"""Explicit source restoration and administrative recovery ownership.

The old master store's Plan, storage and project-directory responsibilities now
live in their v4 owners. Git restoration preserves a fresh independent checkout;
it never restores an accepted PV or rewinds project authority history.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from .errors import LaneError
from .git_adapter import restoration_git, restoration_selection, restoration_source_identity
from .migrations import Migration, apply_migrations, read_compatibility
from .plan_runtime import content_digest
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, json_text, now, project_snapshot, reject_links

DIGEST = r'^[0-9a-f]{64}$'
RESTORE_MIGRATIONS = (Migration('restoration', 1, 'Source restoration requests, history and Plan refresh boundary', (
    """CREATE TABLE restoration_requests (request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
       input_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       state TEXT NOT NULL CHECK(state IN ('pending','published','abandoned')),
       result_json TEXT CHECK(json_valid(result_json)))""",
    """CREATE TABLE restoration_history (generation INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
    """CREATE TABLE restoration_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       generation INTEGER NOT NULL REFERENCES restoration_history(generation),
       restore_digest TEXT NOT NULL, required_after_revision INTEGER NOT NULL,
       cleared_by_revision INTEGER)""",
    "CREATE UNIQUE INDEX restoration_one_pending ON restoration_requests(state) WHERE state='pending'",
)), Migration('restoration', 2, 'Source reindex evidence after an explicit source binding change', (
    """CREATE TABLE restoration_reindex (generation INTEGER PRIMARY KEY REFERENCES restoration_history(generation),
       digest TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
)))


class GitRestoreSelection(Contract):
    branch: str = Field(min_length=1, max_length=200)
    commit: str = Field(pattern=r'^[0-9a-f]{40}(?:[0-9a-f]{24})?$')
    destination_root: str = Field(min_length=1, max_length=1000)


class GitRestorePreview(Contract):
    project_id: str
    preview_digest: str
    source_root: str
    destination_root: str
    source_identity: dict
    selection: dict
    plan_boundary: dict
    generation: int
    source_workspace_reset: bool = False
    source_code_reindexed: bool = False
    external_filters_enabled: bool = False
    submodules_initialized: bool = False
    lfs_objects_downloaded: bool = False


class GitRestoreExecute(GitRestoreSelection):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    expected_preview_digest: str = Field(pattern=DIGEST)


class RestorationReindex(Contract):
    restore_digest: str = Field(pattern=DIGEST)
    max_files: int = Field(default=5000, ge=1, le=25000)
    max_total_bytes: int = Field(default=67108864, ge=8388608, le=268435456)


class RestorationReindexed(Contract):
    project_id: str
    generation: int
    restore_digest: str
    reindex_digest: str
    source_root: str
    batch_id: str
    graph_id: str
    file_count: int
    parsed_file_count: int
    coverage_states: dict[str, int]
    scope: str = 'registered_source_members_and_source_graph_extraction'
    plan_refresh_required: bool = True
    all_language_semantics_verified: bool = False
    source_bytes_mutated: bool = False


class GitRestored(Contract):
    project_id: str
    request_id: str
    generation: int
    restore_digest: str
    previous_source_root: str
    source_root: str
    commit: str
    tree: str
    plan_refresh_required: bool = True
    clients_must_reconnect: bool = True
    source_workspace_reset: bool = False
    project_history_rewound: bool = False
    source_code_reindexed: bool = False
    native_task_attestation: str = 'not_provided'


class RestorationRead(Contract):
    limit: int = Field(default=10, ge=1, le=50)


class RestorationState(Contract):
    project_id: str
    source_root: str
    control: dict | None
    requests: list[dict]
    history: list[dict]
    source_reindex: dict | None = None


class RestoreAbandon(Contract):
    request_id: str = Field(pattern=UUID_PATTERN)
    expected_preview_digest: str = Field(pattern=DIGEST)


class RestoreAbandoned(Contract):
    request_id: str
    state: str = 'abandoned'
    files_removed: bool = False


def has_table(connection, name):
    return connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (name,)).fetchone() is not None


def recovery_operation(callback):
    try:
        return callback()
    except (OSError, sqlite3.DatabaseError, ValueError, KeyError, TypeError):
        raise LaneError('RECOVERY_IO_OR_DATA_FAILURE',
            'A selected recovery file or record could not be validated. Inspect the recorded attempt and preserve its output before retrying.') from None


def assert_quiescent(connection):
    if has_table(connection, 'jobs_jobs') and connection.execute(
            "SELECT 1 FROM jobs_jobs WHERE state IN ('queued','running','uncertain') LIMIT 1").fetchone():
        raise LaneError('RECOVERY_JOBS_NOT_QUIESCENT', 'Finish or checkpoint outstanding jobs before this recovery action.')
    if has_table(connection, 'jobs_effects') and connection.execute(
            "SELECT 1 FROM jobs_effects WHERE state='prepared' LIMIT 1").fetchone():
        raise LaneError('RECOVERY_EFFECT_UNCERTAIN', 'Reconcile prepared external effects before recovery.')
    if has_table(connection, 'plan_current') and connection.execute(
            "SELECT 1 FROM plan_tasks WHERE revision=(SELECT revision FROM plan_current) AND state='active'").fetchone():
        raise LaneError('RECOVERY_PLAN_CHECKPOINT_REQUIRED', 'Checkpoint the active Plan task before recovery.')
    if has_table(connection, 'steer_requests') and connection.execute(
            "SELECT 1 FROM steer_requests WHERE state IN ('pending','checkpointing','ready') LIMIT 1").fetchone():
        raise LaneError('RECOVERY_STEER_PENDING', 'Resolve pending steers before recovery.')


def restoration_control(connection):
    if not has_table(connection, 'restoration_control'):
        return None
    row = connection.execute('SELECT * FROM restoration_control WHERE singleton=1').fetchone()
    return dict(row) if row else None


def require_restoration_plan(connection, supplied_digest, revision):
    """Called by the Plan owner inside its revision transaction."""
    control = restoration_control(connection)
    if control and control['cleared_by_revision'] is None:
        if supplied_digest != control['restore_digest'] or revision <= control['required_after_revision']:
            raise LaneError('SOURCE_RESTORE_PLAN_REQUIRED', 'Bind the new Plan to the exact current source restoration digest.')
        require_restoration_reindex(connection, control)
        connection.execute('UPDATE restoration_control SET cleared_by_revision=? WHERE singleton=1', (revision,))
    elif supplied_digest is not None:
        raise LaneError('SOURCE_RESTORE_PLAN_MISMATCH', 'There is no pending source restoration for this Plan change.')


def require_restoration_execution_ready(connection):
    control = restoration_control(connection)
    if control and control['cleared_by_revision'] is None:
        raise LaneError('SOURCE_RESTORE_PLAN_REQUIRED', 'Refresh the Plan against the restored source before executing work.')
    if control:
        require_restoration_reindex(connection, control)


def require_restoration_reindex(connection, control):
    row = connection.execute('SELECT * FROM restoration_reindex WHERE generation=?',
        (control['generation'],)).fetchone() if has_table(connection, 'restoration_reindex') else None
    if row is None:
        raise LaneError('SOURCE_RESTORE_REINDEX_REQUIRED', 'Reindex the changed source with restoration_reindex before refreshing the Plan.')
    if len(row['body_json'].encode()) > 65536:
        raise LaneError('RESTORATION_REINDEX_INTEGRITY', 'The source reindex record exceeds its bounded identity.')
    body = json.loads(row['body_json'])
    if (content_digest(body) != row['digest'] or body.get('generation') != control['generation']
            or body.get('restore_digest') != control['restore_digest']):
        raise LaneError('RESTORATION_REINDEX_INTEGRITY', 'Source reindex evidence differs from this restoration.')
    graph = connection.execute('SELECT * FROM source_graph_snapshot WHERE graph_id=? AND batch_id=?',
        (body.get('graph_id'), body.get('batch_id'))).fetchone() if has_table(connection, 'source_graph_snapshot') else None
    if (graph is None or graph['graph_root_sha256'] != body.get('graph_root_sha256')
            or graph['coverage_sha256'] != body.get('coverage_sha256') or graph['status'] != 'PASS'):
        raise LaneError('RESTORATION_REINDEX_INTEGRITY', 'The source reindex record lacks its exact complete graph snapshot.')
    return body, row['digest']


def validate_source_locator(store, registered_root):
    """Follow a verified SQLite source chain when the directory hint is stale."""
    store = store if isinstance(store, LaneStore) else store.lane('sources')
    with store.connection(read_only=True) as connection:
        if not has_table(connection, 'restoration_history'):
            raise LaneError('PROJECT_BINDING_CHANGED', 'The source root differs without a restoration record.')
        rows = connection.execute('SELECT * FROM restoration_history ORDER BY generation LIMIT 1001').fetchall()
    if len(rows) > 1000:
        raise LaneError('RESTORATION_HISTORY_BUDGET', 'Refresh the registered source locator before extending its history.')
    previous_digest = cursor = None
    matched = False
    for index, row in enumerate(rows, 1):
        body = json.loads(row['body_json'])
        if (row['generation'] != index or content_digest(body) != row['digest']
                or body['project_id'] != store.project_id or body['generation'] != index
                or body['previous_digest'] != previous_digest
                or (cursor is not None and cursor != body['previous_source_root'])):
            raise LaneError('RESTORATION_HISTORY_INTEGRITY', 'Source restoration history failed validation.')
        matched = matched or body['previous_source_root'] == registered_root or body['source_root'] == registered_root
        cursor, previous_digest = body['source_root'], row['digest']
    if not matched or cursor != str(store.source_root):
        raise LaneError('PROJECT_BINDING_CHANGED', 'The source locator does not match verified restoration history.')


class SourceRestoration:
    def __init__(self, engine, store):
        self.engine = engine
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('sources')

    def _destination(self, value, *, allow_existing=False):
        path = Path(value).expanduser()
        if not path.is_absolute() or '..' in path.parts:
            raise LaneError('RESTORE_ABSOLUTE_PATH_REQUIRED', 'Choose an absolute fresh workspace path.')
        path = Path(os.path.abspath(path))
        reject_links(path, Path(path.anchor))
        roots = [self.engine.root, self.store.source_root, self.store.root]
        for entry in self.engine.directory.entries().values():
            selected = self.engine.directory.open(entry['project_id'])
            roots.extend([selected.source_root, selected.root])
        if any(path.is_relative_to(root) or root.is_relative_to(path) for root in roots):
            raise LaneError('RESTORE_ROOT_OVERLAP', 'The fresh workspace must be separate from registered source, state and runtime roots.')
        if not path.parent.is_dir() or (path.exists() and not allow_existing):
            raise LaneError('RESTORE_FRESH_ROOT_REQUIRED', 'Choose an absent workspace beneath an existing parent directory.')
        return path

    def _boundary(self):
        with project_snapshot(self.project.root):
            self.project.assert_current_binding()
            with self.project.lane('plan').connection(read_only=True) as connection:
                assert_quiescent(connection)
                row = connection.execute('SELECT * FROM plan_current').fetchone() if has_table(connection, 'plan_current') else None
            with self.project.lane('chat_lineage').connection(read_only=True) as connection:
                lineage = connection.execute('SELECT cursor FROM lineage_events ORDER BY sequence DESC LIMIT 1').fetchone() if has_table(connection, 'lineage_events') else None
            with self.store.connection(read_only=True) as connection:
                control = restoration_control(connection)
            return {'revision': row['revision'] if row else 0, 'event_head': row['event_head'] if row else None,
                    'lineage_head': lineage[0] if lineage else None,
                    'restore_generation': control['generation'] if control else 0}

    def preview(self, request, *, allow_existing=False, tick=None):
        selection = GitRestoreSelection.model_validate(request.model_dump(include={'branch','commit','destination_root'}))
        destination = self._destination(selection.destination_root, allow_existing=allow_existing)
        before = self._boundary()
        source = restoration_source_identity(self.store.source_root, tick=tick)
        selected = restoration_selection(self.store.source_root, selection.branch, selection.commit, tick=tick)
        if self._boundary() != before or restoration_source_identity(self.store.source_root, tick=tick) != source:
            raise LaneError('RESTORE_SOURCE_CHANGED', 'The source or Plan changed while preparing restoration.')
        body = {'project_id': self.store.project_id, 'source_root': str(self.store.source_root),
                'destination_root': str(destination), 'source_identity': source, 'selection': selected,
                'plan_boundary': before, 'generation': before['restore_generation']}
        return GitRestorePreview(**body, preview_digest=content_digest(body))

    @staticmethod
    def _heartbeat(lease):
        previous = [time.monotonic()]
        def tick():
            if time.monotonic() - previous[0] >= 5:
                lease.heartbeat()
                previous[0] = time.monotonic()
        return tick

    def execute(self, request, lease, *, actor_id, reconcile=False, authorize=None):
        request = GitRestoreExecute.model_validate(request.model_dump())
        lease.check()
        input_digest = content_digest(request.model_dump())
        apply_migrations(self.store, RESTORE_MIGRATIONS, writer=lease)
        with self.store.connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM restoration_requests WHERE request_id=?', (request.request_id,)).fetchone()
        if row:
            if row['input_digest'] != input_digest or (row['actor_id'] != actor_id and not reconcile):
                raise LaneError('RESTORE_REQUEST_CONFLICT', 'This request belongs to another actor or restoration.')
            if row['state'] == 'published':
                return GitRestored.model_validate_json(row['result_json'])
            if row['state'] == 'abandoned' or not reconcile:
                raise LaneError('RESTORE_RECONCILIATION_REQUIRED', 'Inspect this recorded attempt and explicitly reconcile its fresh workspace or abandon it.')
        elif reconcile:
            raise LaneError('RESTORE_REQUEST_MISSING', 'There is no recorded restoration attempt to reconcile.')
        heartbeat = self._heartbeat(lease)
        def tick():
            if authorize:
                authorize()
            heartbeat()
        preview = self.preview(request, allow_existing=bool(row), tick=tick)
        if preview.preview_digest != request.expected_preview_digest:
            raise LaneError('RESTORE_PREVIEW_CHANGED', 'Refresh the source restoration preview before proceeding.')
        destination = Path(preview.destination_root)
        if not row:
            with lease.transaction('sources') as connection:
                if connection.execute("SELECT 1 FROM restoration_requests WHERE state='pending'").fetchone():
                    raise LaneError('RESTORE_PENDING', 'Reconcile or abandon the existing restoration attempt first.')
                connection.execute('INSERT INTO restoration_requests VALUES(?,?,?,?,?,NULL)',
                    (request.request_id, actor_id, input_digest, json_text({'request':request.model_dump(), 'preview':preview.model_dump()}), 'pending'))
                self.store.append_receipt('git_restore_started', {'request_id':request.request_id,
                    'preview_digest':preview.preview_digest, 'destination_root':str(destination)}, connection=connection)
            lease.heartbeat()
            restoration_git(self.store.source_root, ['clone', '--local', '--no-hardlinks', '--no-checkout', '--template=',
                                                    '--', str(self.store.source_root), str(destination)])
            lease.heartbeat()
            restoration_git(destination, ['checkout', '-B', request.branch, request.commit, '--'])
        lease.heartbeat()
        restored = restoration_source_identity(destination, tick=tick)
        if restored['head'] != request.commit or restored['tree'] != preview.selection['tree'] or not restored['clean']:
            raise LaneError('RESTORED_WORKSPACE_CHANGED', 'The fresh workspace must have the exact selected commit, tree and clean Git state.')
        if self.preview(request, allow_existing=True, tick=tick).preview_digest != preview.preview_digest:
            raise LaneError('RESTORE_SOURCE_CHANGED', 'The original source or Plan changed before restoration could be applied.')
        generation = preview.generation + 1
        tick()
        with lease.coordinated_transaction(['sources', 'plan', 'receipts']) as commit:
            connection = commit.connection('sources')
            plan = commit.connection('plan')
            receipts = commit.connection('receipts')
            assert_quiescent(plan)
            prior_clients = [row[0] for row in receipts.execute('SELECT DISTINCT principal_id FROM access_grants WHERE revoked_at IS NULL LIMIT 257')] if has_table(receipts, 'access_grants') else []
            if len(prior_clients) > 256:
                raise LaneError('RESTORATION_CLIENT_BUDGET', 'The selected project exceeds its bounded client-revocation inventory.')
            old = connection.execute('SELECT digest FROM restoration_history ORDER BY generation DESC LIMIT 1').fetchone()
            body = {'project_id':self.store.project_id, 'request_id':request.request_id,
                    'generation':generation, 'previous_digest':old[0] if old else None,
                    'previous_source_root':str(self.store.source_root), 'source_root':str(destination),
                    'preview':preview.model_dump(), 'restored_identity':restored,
                    'actor_id':actor_id, 'created_at':now()}
            digest = content_digest(body)
            result = GitRestored(project_id=self.store.project_id, request_id=request.request_id, generation=generation,
                restore_digest=digest, previous_source_root=str(self.store.source_root), source_root=str(destination),
                commit=request.commit, tree=restored['tree'])
            connection.execute('INSERT INTO restoration_history VALUES(?,?,?)', (generation,digest,json_text(body)))
            connection.execute('INSERT OR REPLACE INTO restoration_control VALUES(1,?,?,?,NULL)',
                               (generation,digest,preview.plan_boundary['revision']))
            commit.connection(None).execute('UPDATE project SET source_root=? WHERE singleton=1', (str(destination),))
            if has_table(receipts, 'access_grants'):
                receipts.execute('UPDATE access_grants SET revoked_at=? WHERE revoked_at IS NULL', (now(),))
            if has_table(plan, 'jobs_jobs'):
                plan.execute("UPDATE jobs_jobs SET state='superseded',resumable=0,updated_at=? WHERE state='checkpointed'", (now(),))
            from .database_recovery import close_recovery_session
            close_recovery_session(self.project, receipts, digest, reason='source_binding_changed')
            connection.execute("UPDATE restoration_requests SET state='published',result_json=? WHERE request_id=?",
                               (result.model_dump_json(),request.request_id))
            self.store.append_receipt('git_source_restored', result.model_dump(), connection=connection)
        # The database commit is authoritative; a stale directory hint remains
        # readable only through its verified source-history chain.
        for client_id in prior_clients:
            self.engine.capture.detach_project(client_id, self.project.project_id)
        self.engine.directory.refresh_source_locator(self.store.project_id)
        return result

    def reindex(self, request, lease, *, actor_id, authorize=None):
        """Index the exact restored root; preserve all older registered sources."""
        from .source_authority import verify_source_batch_unchanged
        from .source_graph import build_registered_source_graph
        from .source_intake import classify_source_intake
        apply_migrations(self.store, RESTORE_MIGRATIONS, writer=lease)
        with self.store.connection(read_only=True) as connection:
            control = restoration_control(connection)
            if not control or control['restore_digest'] != request.restore_digest:
                raise LaneError('RESTORATION_REINDEX_MISMATCH', 'Select the exact current source restoration digest.')
            history = connection.execute('SELECT body_json,digest FROM restoration_history WHERE generation=?', (control['generation'],)).fetchone()
            body = json.loads(history['body_json'])
            if content_digest(body) != history['digest']:
                raise LaneError('RESTORATION_HISTORY_INTEGRITY', 'The source restoration record changed.')
            prior = connection.execute('SELECT * FROM restoration_reindex WHERE generation=?', (control['generation'],)).fetchone()
        expected = body['restored_identity']
        identity = restoration_source_identity(self.store.source_root)
        if not identity['clean'] or any(identity[key] != expected[key] for key in ('head', 'tree')):
            raise LaneError('RESTORATION_REINDEX_SOURCE_CHANGED', 'The restored checkout must still have its exact clean commit and tree before reindexing.')
        if authorize:
            authorize()
        if prior:
            with self.store.connection(read_only=True) as connection:
                recorded, digest = require_restoration_reindex(connection, control)
            verify_source_batch_unchanged(self.store, recorded['batch_id'])
            return RestorationReindexed(**recorded['result'], reindex_digest=digest)
        lease.heartbeat()
        with lease.transaction('sources') as connection:
            intake = classify_source_intake([str(self.store.source_root)], code_mode='local_code',
                registered_repository_path=self.store.source_root, authority_mode='GOVERNED_CONTENT_REGISTRY',
                authority_registry_path=self.project, writer=lease)
            batch_id = intake['source_authority']['batch_id']
            graph = build_registered_source_graph(self.project, batch_id, max_files=request.max_files,
                max_total_bytes=request.max_total_bytes, max_nodes=100000, max_edges=200000, writer=lease)
            if graph['status'] != 'PASS' or graph['omitted_registered_member_count']:
                raise LaneError('RESTORATION_REINDEX_INCOMPLETE', 'Resolve source extraction gaps before publishing the restored source index.',
                    details={'status':graph['status'], 'gap_states':graph['gap_states']})
            verify_source_batch_unchanged(self.store, batch_id)
            if restoration_source_identity(self.store.source_root) != identity:
                raise LaneError('RESTORATION_REINDEX_SOURCE_CHANGED', 'The restored source changed during reindexing.')
            if authorize:
                authorize()
            result = {'project_id':self.project.project_id, 'generation':control['generation'],
                'restore_digest':request.restore_digest, 'source_root':str(self.store.source_root),
                'batch_id':batch_id, 'graph_id':graph['graph_id'], 'file_count':graph['file_count'],
                'parsed_file_count':graph['parsed_file_count'], 'coverage_states':graph['coverage_states']}
            record = {'project_id':self.project.project_id, 'generation':control['generation'],
                'restore_digest':request.restore_digest, 'batch_id':batch_id, 'graph_id':graph['graph_id'],
                'graph_root_sha256':graph['graph_root_sha256'], 'coverage_sha256':graph['coverage_sha256'],
                'source_identity':identity, 'actor_id':actor_id, 'created_at':now(), 'result':result}
            digest = content_digest(record)
            connection.execute('INSERT INTO restoration_reindex VALUES(?,?,?)', (control['generation'], digest, json_text(record)))
            self.store.append_receipt('restored_sources_reindexed', {'reindex_digest':digest, **result}, connection=connection)
        return RestorationReindexed(**result, reindex_digest=digest)

    def abandon(self, request, lease, *, actor_id):
        with lease.transaction('sources') as connection:
            if not has_table(connection,'restoration_requests'):
                raise LaneError('RESTORE_REQUEST_MISSING', 'There is no restoration attempt to abandon.')
            row = connection.execute('SELECT * FROM restoration_requests WHERE request_id=?',(request.request_id,)).fetchone()
            if not row or json.loads(row['body_json'])['preview']['preview_digest'] != request.expected_preview_digest:
                raise LaneError('RESTORE_REQUEST_CONFLICT', 'Select the exact recorded restoration preview.')
            if row['state'] == 'published':
                raise LaneError('RESTORE_ALREADY_PUBLISHED', 'A published source change remains immutable history.')
            connection.execute("UPDATE restoration_requests SET state='abandoned' WHERE request_id=?",(request.request_id,))
            self.store.append_receipt('git_restore_abandoned', {'request_id':request.request_id,
                'actor_id':actor_id,'files_removed':False},connection=connection)
        return RestoreAbandoned(request_id=request.request_id)

    def read(self, request):
        read_compatibility(self.store, RESTORE_MIGRATIONS)
        with self.store.connection(read_only=True) as connection:
            control = restoration_control(connection)
            if not has_table(connection,'restoration_requests'):
                return RestorationState(project_id=self.store.project_id,source_root=str(self.store.source_root),control=None,requests=[],history=[])
            requests = [{**dict(row), 'body':json.loads(row['body_json'])} for row in connection.execute(
                'SELECT * FROM restoration_requests ORDER BY rowid DESC LIMIT ?', (request.limit,))]
            history = []
            for row in connection.execute('SELECT * FROM restoration_history ORDER BY generation DESC LIMIT ?', (request.limit,)):
                body = json.loads(row['body_json'])
                if content_digest(body) != row['digest'] or body['project_id'] != self.store.project_id:
                    raise LaneError('RESTORATION_HISTORY_INTEGRITY', 'A source restoration record differs from its digest.')
                history.append({'generation':row['generation'],'digest':row['digest'],'body':body})
        for item in requests:
            item.pop('body_json')
        reindex = None
        if control:
            with self.store.connection(read_only=True) as connection:
                row = connection.execute('SELECT 1 FROM restoration_reindex WHERE generation=?',
                    (control['generation'],)).fetchone() if has_table(connection, 'restoration_reindex') else None
                if row:
                    body, digest = require_restoration_reindex(connection, control)
                    reindex = {'required':False, 'reindex_digest':digest, **body['result']}
                else:
                    reindex = {'required':True, 'restore_digest':control['restore_digest']}
        result = RestorationState(project_id=self.store.project_id,source_root=str(self.store.source_root),control=control,requests=requests,history=history,source_reindex=reindex)
        if len(result.model_dump_json().encode()) > 262144:
            raise LaneError('RESTORATION_READ_BUDGET', 'Select fewer restoration records.')
        return result


def register_restoration_actions(engine):
    def authorize_source(context, store, permission):
        from .projects import ProjectAccess
        if context.authorize:
            context.authorize(permission)
        ProjectAccess(store).authorize(context.client_id, 'read', path=store.source_root)

    def preview(context, request):
        store = engine.directory.open(context.project_id)
        authorize_source(context, store, 'read')
        return recovery_operation(lambda:SourceRestoration(engine, store).preview(request))

    engine.registry.register(ActionSpec('git_restore_preview', 'Inspect an exact local Git commit and fresh workspace restoration.',
        GitRestoreSelection, GitRestorePreview,
        preview,
        profile='recovery',studio_read=True,required_tools=('Git',), workflow='recover'))
    def execute(reconcile=False):
        def handler(context,request):
            store = engine.directory.open(context.project_id,write=True)
            authorize_source(context, store, 'admin')
            with engine.project_work.mutation(store) as lease:
                return recovery_operation(lambda:SourceRestoration(engine,store).execute(request,lease,actor_id=context.client_id,reconcile=reconcile,
                    authorize=lambda: authorize_source(context, store, 'admin')))
        return handler
    engine.registry.register(ActionSpec('git_restore', 'Restore a fresh local Git workspace and require a new source-bound Plan revision.',
        GitRestoreExecute, GitRestored, execute(), permission='admin',mutates=True,profile='recovery',required_tools=('Git',), workflow='recover'))
    engine.registry.register(ActionSpec('git_restore_reconcile', 'Verify a recorded fresh workspace and finish its source change without rerunning clone.',
        GitRestoreExecute, GitRestored, execute(True),permission='admin',mutates=True,profile='recovery',required_tools=('Git',), workflow='recover'))
    def abandon(context,request):
        store = engine.directory.open(context.project_id,write=True)
        with engine.project_work.mutation(store) as lease:
            return SourceRestoration(engine,store).abandon(request,lease,actor_id=context.client_id)
    engine.registry.register(ActionSpec('git_restore_abandon','Abandon a pending restoration record while preserving all files.',
        RestoreAbandon,RestoreAbandoned,abandon,permission='admin',mutates=True,profile='recovery', workflow='recover'))
    engine.registry.register(ActionSpec('restoration_read','Read source restoration history and the Plan refresh boundary.',
        RestorationRead,RestorationState,
        lambda context,request:SourceRestoration(engine,engine.directory.open(context.project_id)).read(request),
        profile='recovery',queryable_in_delta=True, workflow='recover'))

    def reindex(context, request):
        store = engine.directory.open(context.project_id, write=True)
        authorize_source(context, store, 'write')
        with engine.project_work.mutation(store) as lease:
            return recovery_operation(lambda:SourceRestoration(engine, store).reindex(request, lease, actor_id=context.client_id,
                authorize=lambda: authorize_source(context, store, 'write')))
    engine.registry.register(ActionSpec('restoration_reindex', 'Reindex the exact restored source root and retain its file and extraction coverage before Plan refresh.',
        RestorationReindex, RestorationReindexed, reindex, permission='write', mutates=True,
        profile='recovery', required_tools=('Git',), workflow='recover'))
