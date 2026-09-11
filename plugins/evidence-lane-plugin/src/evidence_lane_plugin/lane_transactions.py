"""Separate lane commits, durable undo, and one atomic project evidence head coordinator publication.

Readers pin the root in rollback-journal mode. Writers keep it exclusive while
changing lane databases. The durable prepared journal closes the crash gap:
readers refuse unpublished bytes until recovery restores every enlisted lane.
This is working-state crash recovery, not an accepted-PV or historical rollback.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from .errors import LaneError
from .lanes import get_lane
from .locking import RuntimeLock
from .storage import (
    LANE_APPLICATION_ID,
    STORAGE_LAYOUT,
    _commit_scope,
    _read_scope,
    _snapshot_scope,
    json_text,
    now,
    reject_links,
)


def file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _sync_directory(path: Path):
    # Windows file handles are flushed separately; directory fsync is POSIX-only.
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _copy_new(source: Path, target: Path):
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    _sync_directory(target.parent)


def _root_head(connection) -> dict:
    return dict(connection.execute('SELECT * FROM root_pv_head WHERE singleton=1').fetchone())


def _heads(connection) -> list[dict]:
    return [dict(row) for row in connection.execute('SELECT * FROM root_lane_heads ORDER BY lane_id')]


def _pending(connection):
    return connection.execute("SELECT * FROM root_transaction_journal WHERE phase='prepared' ORDER BY created_at").fetchall()


def _checkpoint(fault, phase):
    if fault is not None:
        fault(phase)


@contextmanager
def _writer_lock(project, writer):
    if project.read_only or _read_scope.get() is not None or _snapshot_scope.get() is not None:
        raise LaneError('READ_ONLY_PROJECT', 'A published query cannot acquire project write ownership.')
    with ExitStack() as stack:
        if writer is None:
            try:
                stack.enter_context(RuntimeLock(project.root / 'writer.lock'))
            except LaneError as error:
                if error.code == 'RUNTIME_IN_USE':
                    raise LaneError('PROJECT_WRITER_BUSY', 'Another writer owns this project.') from None
                raise
        elif writer.store.root != project.root or writer.lock.stream is None:
            raise LaneError('WRITER_NOT_HELD', 'The supplied writer must own this exact project.')
        try:
            stack.enter_context(RuntimeLock(project.root / 'publication.lock'))
        except LaneError as error:
            if error.code == 'RUNTIME_IN_USE':
                raise LaneError('PROJECT_COMMIT_BUSY', 'A coordinated commit is already using this writer.') from None
            raise
        yield


class LaneCommit:
    def __init__(self, project, root, lanes, connections, commit_id):
        self.project = project
        self.root = root
        self.lanes = lanes
        self.connections = connections
        self.commit_id = commit_id
        self.published_head = None
        self.phase = 'writing'
        self._savepoint = 0

    def connection(self, lane_id):
        if lane_id is None:
            return self.root
        canonical = get_lane(lane_id).canonical_lane_id
        if canonical not in self.connections:
            raise LaneError('LANE_NOT_ENLISTED', 'Declare every participating lane before the coordinated write.')
        return self.connections[canonical]

    def owns(self, connection):
        return connection is self.root or any(connection is item for item in self.connections.values())

    @contextmanager
    def savepoint(self):
        self._savepoint += 1
        name = 'lane_nested_' + str(self._savepoint)
        connections = [self.root, *self.connections.values()]
        for connection in connections:
            connection.managed = False
            connection.execute('SAVEPOINT ' + name)
            connection.managed = True
        try:
            yield
        except BaseException:
            for connection in connections:
                connection.managed = False
                connection.execute('ROLLBACK TO ' + name)
                connection.managed = True
            raise
        finally:
            for connection in reversed(connections):
                connection.managed = False
                connection.execute('RELEASE ' + name)
                connection.managed = True


def _check_lane_files(lane, connection):
    from .migrations import verify_schema_history_files
    verify_schema_history_files(lane, connection)
    identity = connection.execute('SELECT project_id,lane_id,kind,storage_layout FROM lane_identity WHERE singleton=1').fetchone()
    expected = (lane.project_id, lane.lane_id, lane.definition.kind, STORAGE_LAYOUT)
    if identity is None or tuple(identity) != expected or connection.execute('PRAGMA application_id').fetchone()[0] != LANE_APPLICATION_ID:
        raise LaneError('LANE_IDENTITY_MISMATCH', 'The write changed its bound lane or project identity.')
    if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
        raise LaneError('LANE_FOREIGN_KEY_FAILED', 'A lane contains invalid local references.')
    if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise LaneError('LANE_INTEGRITY_FAILED', 'A lane failed SQLite integrity verification.')
    # Registration is the visibility boundary. Orphan files left by an aborted
    # operation cannot be read through the registered-content API.
    for row in connection.execute('SELECT digest,size_bytes FROM objects'):
        path = lane.object_path(row['digest'])
        if not path.is_file() or path.stat().st_size != row['size_bytes'] or file_digest(path) != row['digest']:
            raise LaneError('OBJECT_INTEGRITY_FAILED', 'A published lane object differs from its registered bytes.')


def _validate_undo(project, entry):
    """Validate every recovery input before replacing any live database."""
    try:
        commit_id = entry['commit_id']
        if str(UUID(commit_id)) != commit_id or len(entry['body_json']) > 1_000_000:
            raise ValueError('journal identity or size')
        body = json.loads(entry['body_json'])
        if body['project_id'] != project.project_id or body['layout'] != STORAGE_LAYOUT:
            raise ValueError('project identity')
        records = body['lanes']
        if not isinstance(records, list) or not 1 <= len(records) <= 27:
            raise ValueError('lane count')
        seen = set()
        verified = []
        for item in records:
            definition = get_lane(item['lane_id'])
            lane_id = definition.canonical_lane_id
            relative = '.transactions/' + commit_id + '/' + lane_id + '.before.sqlite'
            if lane_id in seen or lane_id != item['lane_id'] or item['backup'] != relative:
                raise ValueError('backup binding')
            if item['database'] != definition.database_relative_path:
                raise ValueError('database binding')
            seen.add(lane_id)
            backup = project.root / relative
            target = project.root / definition.database_relative_path
            reject_links(backup, project.root)
            reject_links(target, project.root)
            if not backup.is_file() or file_digest(backup) != item['before_sha256']:
                raise ValueError('backup hash')
            previous = next((head for head in body['before_heads'] if head['lane_id'] == lane_id), None)
            backup_head = hashlib.sha256(json_text({'lane_id': lane_id, 'database_sha256': item['before_sha256']}).encode()).hexdigest()
            if previous is not None and previous['head_digest'] != backup_head:
                raise ValueError('backup differs from previous lane head')
            with closing(sqlite3.connect(backup.as_uri() + '?mode=ro&immutable=1', uri=True)) as connection:
                connection.execute('PRAGMA query_only=ON')
                connection.execute('PRAGMA trusted_schema=OFF')
                identity = connection.execute('SELECT project_id,lane_id,kind,storage_layout FROM lane_identity WHERE singleton=1').fetchone()
                if identity != (project.project_id, lane_id, definition.kind, STORAGE_LAYOUT):
                    raise ValueError('backup lane identity')
                if connection.execute('PRAGMA application_id').fetchone()[0] != LANE_APPLICATION_ID:
                    raise ValueError('backup application')
                if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('backup integrity')
            verified.append((item, backup, target))
        return body, verified
    except (ValueError, KeyError, TypeError, sqlite3.DatabaseError, OSError):
        raise LaneError('RECOVERY_EVIDENCE_INVALID', 'Preserve the interrupted state; its recovery evidence is invalid.') from None


def _restore(project, root, entry, fault=None):
    body, verified = _validate_undo(project, entry)
    if _root_head(root) != body['before_root'] or _heads(root) != body['before_heads']:
        raise LaneError('RECOVERY_HEAD_CHANGED', 'The root advanced beyond this unpublished commit; preserve both states.')
    # Open/close first so SQLite itself resolves hot rollback journals. Never
    # delete a journal by guessing whether its contents are safe to discard.
    for _, _, target in verified:
        for suffix in ('-wal', '-shm', '-journal'):
            reject_links(Path(str(target) + suffix), project.root)
        with closing(sqlite3.connect(target.as_uri() + '?mode=rw', uri=True, isolation_level=None)) as connection:
            connection.execute('PRAGMA busy_timeout=1000')
            if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise LaneError('UNSUPPORTED_JOURNAL_MODE', 'Recovery requires separate DELETE-mode databases.')
            connection.execute('BEGIN EXCLUSIVE')
            connection.rollback()
    for item, backup, target in verified:
        # Unique scratch names make a crash during restore retryable without
        # overwriting or trusting a previous partial copy.
        scratch = target.parent / ('.restore-' + str(uuid4()) + '.sqlite')
        reject_links(scratch, project.root)
        _copy_new(backup, scratch)
        if file_digest(scratch) != item['before_sha256']:
            raise LaneError('RECOVERY_COPY_FAILED', 'Recovery copy verification failed; preserve the pending journal.')
        os.replace(scratch, target)
        _sync_directory(target.parent)
        _checkpoint(fault, 'restored:' + item['lane_id'])
    root.execute("UPDATE root_transaction_journal SET phase='aborted',updated_at=? WHERE commit_id=? AND phase='prepared'",
                 (now(), entry['commit_id']))


@contextmanager
def coordinated_transaction(project, lanes, *, writer=None, expected_revision=None, fault=None):
    selected = sorted({get_lane(lane).canonical_lane_id for lane in lanes} | {'receipts'})
    active = _commit_scope.get()
    if active is not None:
        if active.project.root != project.root:
            raise LaneError('COMMIT_PROJECT_SCOPE', 'A coordinated write belongs to one project.')
        if expected_revision is not None and expected_revision != _root_head(active.root)['revision']:
            raise LaneError('STALE_ROOT_REVISION', 'The selected project evidence head coordinator revision changed.')
        for lane_id in selected:
            active.connection(lane_id)
        with active.savepoint():
            yield active
        return
    with _writer_lock(project, writer), ExitStack() as stack:
        with project.connection(read_only=True) as observed:
            if expected_revision is not None and _root_head(observed)['revision'] != expected_revision:
                raise LaneError('STALE_ROOT_REVISION', 'The selected project evidence head coordinator revision changed.')
            if writer is not None:
                writer.check_commit(observed)
        # Initialization adds only empty lane identity/catalog state, never a
        # published business head. A collision is preserved for explicit recovery.
        for lane_id in selected:
            project._initialize_lane(get_lane(lane_id))
        lane_stores = {lane_id: project.lane(lane_id) for lane_id in selected}
        root = stack.enter_context(project._raw_connection(read_only=False))
        root.execute('BEGIN EXCLUSIVE')
        if root.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise LaneError('UNSUPPORTED_JOURNAL_MODE', 'project evidence head coordinator publication requires DELETE journal mode.')
        if _pending(root):
            raise LaneError('PROJECT_RECOVERY_REQUIRED', 'Recover the interrupted lane commit before writing.')
        if writer is not None:
            writer.check_commit(root)
        before_root = _root_head(root)
        if expected_revision is not None and before_root['revision'] != expected_revision:
            raise LaneError('STALE_ROOT_REVISION', 'The selected project evidence head coordinator revision changed.')
        before_heads = _heads(root)
        for lane in lane_stores.values():
            lane._verify_published_head(root)
        commit_id = str(uuid4())
        folder = project.root / '.transactions' / commit_id
        reject_links(folder, project.root)
        folder.mkdir(parents=True, exist_ok=False)
        _sync_directory(folder.parent)
        lane_stack = stack.enter_context(ExitStack())
        connections = {}
        records = []
        for lane_id, lane in lane_stores.items():
            connection = lane_stack.enter_context(lane._raw_connection(read_only=False))
            if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
                raise LaneError('UNSUPPORTED_JOURNAL_MODE', 'Lane publication requires DELETE journal mode.')
            connection.execute('BEGIN IMMEDIATE')
            backup = folder / (lane_id + '.before.sqlite')
            _copy_new(lane.database, backup)
            records.append({'lane_id': lane_id, 'database': lane.definition.database_relative_path,
                            'backup': backup.relative_to(project.root).as_posix(),
                            'before_sha256': file_digest(backup)})
            connections[lane_id] = connection
        body = {'project_id': project.project_id, 'layout': STORAGE_LAYOUT,
                'before_root': before_root, 'before_heads': before_heads, 'lanes': records,
                'writer_fence': writer.fence if writer is not None else None}
        _validate_undo(project, {'commit_id': commit_id, 'body_json': json_text(body)})
        root.execute('INSERT INTO root_transaction_journal VALUES(?,?,?,?,?)',
                     (commit_id, 'prepared', json_text(body), now(), now()))
        root.commit()  # From here onward a crash must be reconciled explicitly.
        commit = LaneCommit(project, root, lane_stores, connections, commit_id)
        token = None
        published = False
        try:
            _checkpoint(fault, 'prepared')
            root.execute('BEGIN EXCLUSIVE')
            for connection in (root, *connections.values()):
                connection.managed = True
            token = _commit_scope.set(commit)
            yield commit
            if writer is not None:
                writer.check_commit(root)
            from .sqlite_execution import optimize_owned_sqlite_lane, verify_committed_sqlite_lane
            sqlite_execution = {}
            for lane_id, connection in connections.items():
                sqlite_execution[lane_id] = {'optimization': optimize_owned_sqlite_lane(commit, lane_id)}
                _check_lane_files(lane_stores[lane_id], connection)
            _checkpoint(fault, 'before_lane_commits')
            for lane_id, connection in connections.items():
                connection.managed = False
                connection.commit()
                _checkpoint(fault, 'committed:' + lane_id)
            commit.phase = 'validating_lanes'
            for lane_id in connections:
                sqlite_execution[lane_id]['validation'] = verify_committed_sqlite_lane(commit, lane_id)
                _checkpoint(fault, 'validated:' + lane_id)
            body['sqlite_execution'] = sqlite_execution
            commit.phase = 'publishing'
            root.managed = False
            for item in records:
                lane_id = item['lane_id']
                database_hash = file_digest(lane_stores[lane_id].database)
                if database_hash != sqlite_execution[lane_id]['validation']['database_sha256']:
                    raise LaneError('SQLITE_COMMITTED_LANE_CHANGED', 'Publish only the exact SQLite bytes validated for this lane.')
                head = hashlib.sha256(json_text({'lane_id': lane_id, 'database_sha256': database_hash}).encode()).hexdigest()
                previous = next((row for row in before_heads if row['lane_id'] == lane_id), None)
                revision = previous['revision'] + 1 if previous else 1
                root.execute('INSERT OR REPLACE INTO root_lane_heads VALUES(?,?,?,?)',
                             (lane_id, revision, head, commit_id))
                item['after_sha256'] = database_hash
            new_heads = _heads(root)
            revision = before_root['revision'] + 1
            digest = hashlib.sha256(json_text({'project_id': project.project_id, 'revision': revision,
                                               'previous_digest': before_root['head_digest'], 'lanes': new_heads}).encode()).hexdigest()
            root.execute('UPDATE root_pv_head SET revision=?,head_digest=?,commit_id=? WHERE singleton=1',
                         (revision, digest, commit_id))
            body['published_heads'] = new_heads
            root.execute("UPDATE root_transaction_journal SET phase='published',body_json=?,updated_at=? WHERE commit_id=?",
                         (json_text(body), now(), commit_id))
            _checkpoint(fault, 'before_root_publish')
            if writer is not None:
                writer.check_commit(root)
            root.managed = False
            root.commit()  # Heads and journal become visible in one root commit.
            published = True
            commit.phase = 'published'
            commit.published_head = {'revision': revision, 'head_digest': digest, 'commit_id': commit_id}
            _checkpoint(fault, 'published')
        except BaseException:
            # A durable published marker wins even if the caller loses its reply.
            # Never turn a post-publication error into a historical rollback.
            for connection in (root, *connections.values()):
                connection.managed = False
                connection.rollback()
            lane_stack.close()
            if not published:
                root.execute('BEGIN EXCLUSIVE')
                entry = root.execute('SELECT * FROM root_transaction_journal WHERE commit_id=?', (commit_id,)).fetchone()
                if entry['phase'] == 'prepared':
                    _restore(project, root, entry)
                    root.commit()
            raise
        finally:
            if not published:
                commit.phase = 'aborted'
            if token is not None:
                _commit_scope.reset(token)
            root.managed = False
            for connection in connections.values():
                connection.managed = False


def recover_transactions(project, *, writer=None, fault=None) -> list[dict]:
    if _commit_scope.get() is not None:
        raise LaneError('RECOVERY_DURING_COMMIT', 'Recover only after the active commit has stopped.')
    with _writer_lock(project, writer), project._raw_connection(read_only=False) as root:
        root.execute('BEGIN EXCLUSIVE')
        if writer is not None:
            writer.check_commit(root)
        entries = _pending(root)
        if len(entries) > 1:
            raise LaneError('RECOVERY_JOURNAL_CONFLICT', 'Multiple unpublished commits require diagnostic reconciliation.')
        recovered = []
        for entry in entries:
            _restore(project, root, entry, fault)
            recovered.append({'commit_id': entry['commit_id'], 'phase': 'aborted', 'root_revision': _root_head(root)['revision']})
        root.commit()
        return recovered
