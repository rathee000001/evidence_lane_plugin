"""Explicit per-lane SQLite and file storage with a small root PV catalog.

The root stores project identity and coordination references. Business records,
schema history and addressed files belong to the selected authority or sector.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from .errors import LaneError
from .lanes import LANE_REGISTRY, LaneDefinition, get_lane

if TYPE_CHECKING:
    from .lane_transactions import LaneCommit

DATABASE_NAME = 'root-pv.sqlite3'
LEGACY_DATABASE_NAME = 'project.sqlite3'
APPLICATION_ID = 0x45564C34
LANE_APPLICATION_ID = 0x45564C4E
FORMAT_VERSION = 4
STORAGE_LAYOUT = 'separate_lanes_v1'
MAX_OBJECT_BYTES = 64 * 1024 * 1024
_read_scope: ContextVar[tuple[Path, float] | None] = ContextVar('project_read_scope', default=None)
_snapshot_scope: ContextVar[tuple[Path, _PinnedReadConnection] | None] = ContextVar('root_pv_snapshot', default=None)
_commit_scope: ContextVar[LaneCommit | None] = ContextVar('coordinated_lane_commit', default=None)
_sqlite_wait_scope = ContextVar('sqlite_lock_wait_ms', default=10000)


@contextmanager
def sqlite_lock_wait(milliseconds: int):
    """Bound each SQLite lock wait for deferrable engine maintenance."""
    if not 1 <= milliseconds <= 10000:
        raise ValueError('SQLite lock waits must be between 1 and 10000 milliseconds')
    token = _sqlite_wait_scope.set(min(milliseconds, _sqlite_wait_scope.get()))
    try:
        yield
    finally:
        _sqlite_wait_scope.reset(token)

CORE_SCHEMA = """
CREATE TABLE project (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL UNIQUE,
    source_root TEXT NOT NULL, format_version INTEGER NOT NULL,
    storage_layout TEXT NOT NULL, created_at TEXT NOT NULL,
    registration_digest TEXT NOT NULL CHECK(length(registration_digest)=64));
CREATE TABLE project_registration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    body_json TEXT NOT NULL CHECK(json_valid(body_json)));
CREATE TABLE root_lane_catalog (
    lane_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('authority','sector')),
    database_path TEXT NOT NULL UNIQUE, initialized_at TEXT NOT NULL);
CREATE TABLE root_lane_heads (
    lane_id TEXT PRIMARY KEY REFERENCES root_lane_catalog(lane_id),
    revision INTEGER NOT NULL, head_digest TEXT NOT NULL, commit_id TEXT NOT NULL);
CREATE TABLE root_pv_head (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL,
    head_digest TEXT NOT NULL, commit_id TEXT);
CREATE TABLE root_transaction_journal (
    commit_id TEXT PRIMARY KEY, phase TEXT NOT NULL, body_json TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
INSERT INTO root_pv_head VALUES(1,0,'',NULL);
"""

LANE_SCHEMA = """
CREATE TABLE lane_identity (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), project_id TEXT NOT NULL,
    lane_id TEXT NOT NULL, kind TEXT NOT NULL, storage_layout TEXT NOT NULL,
    created_at TEXT NOT NULL);
CREATE TABLE objects (
    digest TEXT PRIMARY KEY CHECK(length(digest)=64),
    size_bytes INTEGER NOT NULL CHECK(size_bytes>=0), created_at TEXT NOT NULL);
CREATE TABLE schema_migrations (
    owner TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL,
    description TEXT NOT NULL, applied_at TEXT NOT NULL, PRIMARY KEY(owner,version));
CREATE TABLE schema_ownership (
    object_name TEXT PRIMARY KEY, owner TEXT NOT NULL, object_type TEXT NOT NULL);
"""
RECEIPTS_SCHEMA = """
CREATE TABLE receipts (
    receipt_id TEXT PRIMARY KEY, kind TEXT NOT NULL,
    body_json TEXT NOT NULL CHECK(json_valid(body_json)), created_at TEXT NOT NULL);
CREATE INDEX receipts_kind_time ON receipts(kind,created_at);
"""


@contextmanager
def bounded_project_read(root: Path, deadline: float, *, writer=None):
    root = Path(root).resolve()
    existing = _read_scope.get()
    if existing is not None:
        if existing[0] != root:
            raise LaneError('QUERY_PROJECT_SCOPE', 'A query cannot open another project implicitly.')
        deadline = min(existing[1], deadline)
    token = _read_scope.set((root, deadline))
    try:
        if writer is not None:
            active = _commit_scope.get()
            if (active is None or active.project.root != root or writer.store.root != root
                    or writer.lock.stream is None):
                raise LaneError('WRITER_NOT_HELD', 'A staged owner read requires the exact active project writer and commit.')
            writer.check()
        with (writer.store.connection(read_only=True) if writer is not None else project_snapshot(root)):
            yield
    finally:
        _read_scope.reset(token)


@contextmanager
def project_snapshot(root: Path):
    """Pin one published project evidence head coordinator while any participating lane is being read.

    Rollback journal mode is required: this shared root lock prevents the
    coordinator's exclusive publication lock until the entire query finishes.
    """
    existing = _snapshot_scope.get()
    if existing is not None:
        if existing[0] != root:
            raise LaneError('QUERY_PROJECT_SCOPE', 'A snapshot belongs to one selected project.')
        scope = _read_scope.get()
        connection = existing[1]
        previous_deadline = connection.deadline
        connection.deadline = min(previous_deadline, scope[1]) if scope else previous_deadline
        try:
            if time.monotonic() >= connection.deadline:
                raise LaneError('QUERY_TIMEOUT', 'The query exceeded its selected time budget.')
            yield connection
        except sqlite3.OperationalError:
            if time.monotonic() >= connection.deadline:
                raise LaneError('QUERY_TIMEOUT', 'The query exceeded its selected time budget.') from None
            raise
        finally:
            connection.deadline = previous_deadline
        return
    if _commit_scope.get() is not None:
        raise LaneError('QUERY_DURING_COMMIT', 'Finish the write before opening a published snapshot.')
    scope = _read_scope.get()
    deadline = scope[1] if scope else time.monotonic() + 10
    if time.monotonic() >= deadline:
        raise LaneError('QUERY_TIMEOUT', 'The query exceeded its selected time budget.')
    database = root / DATABASE_NAME
    reject_links(database, root)
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True,
                                 timeout=max(0.001, min(deadline - time.monotonic(), _sqlite_wait_scope.get() / 1000)), isolation_level=None,
                                 factory=_PinnedReadConnection)
    connection.row_factory = sqlite3.Row
    connection.deadline = deadline
    connection.set_progress_handler(None, 1000)
    token = None
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN')
        connection.execute('SELECT revision FROM root_pv_head WHERE singleton=1').fetchone()
        if connection.execute('PRAGMA journal_mode').fetchone()[0] != 'delete':
            raise LaneError('UNSUPPORTED_JOURNAL_MODE', 'project evidence head coordinator snapshots require DELETE journal mode.')
        if connection.execute("SELECT 1 FROM root_transaction_journal WHERE phase='prepared' LIMIT 1").fetchone():
            raise LaneError('PROJECT_RECOVERY_REQUIRED', 'An interrupted lane commit requires explicit recovery.')
        connection.managed = True
        connection.root_only = True
        connection.set_authorizer(None)
        token = _snapshot_scope.set((root, connection))
        yield connection
    except sqlite3.OperationalError:
        if time.monotonic() >= deadline:
            raise LaneError('QUERY_TIMEOUT', 'The query exceeded its selected time budget.') from None
        raise
    finally:
        if token is not None:
            _snapshot_scope.reset(token)
        connection.close()


class _StorageConnection(sqlite3.Connection):
    root_only = False
    managed = False

    def set_authorizer(self, callback):
        def bounded(action, first, second, database, trigger):
            if self.root_only and first == 'project_registration' and action in {
                    sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE, sqlite3.SQLITE_DROP_TABLE}:
                return sqlite3.SQLITE_DENY
            if self.managed and action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT}:
                return sqlite3.SQLITE_DENY
            if self.managed and self.root_only and (first or '').startswith('root_') and action in {
                sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
            }:
                return sqlite3.SQLITE_DENY
            if database not in {None, 'main', 'temp'} or action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
                return sqlite3.SQLITE_DENY
            if self.root_only:
                table_actions = {sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_CREATE_VTABLE,
                                 sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_DROP_VTABLE,
                                 sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
                if action in table_actions and first not in {'project', 'project_registration', 'schema_migrations', 'schema_ownership', 'sqlite_master', 'sqlite_schema'} and not (first or '').startswith(('root_', 'writer_', 'sqlite_')):
                    return sqlite3.SQLITE_DENY
            return callback(action, first, second, database, trigger) if callback else sqlite3.SQLITE_OK
        super().set_authorizer(bounded)


class _QueryConnection(_StorageConnection):
    deadline: float

    def set_progress_handler(self, callback, n):
        super().set_progress_handler(
            lambda: int(time.monotonic() >= self.deadline or (callback is not None and callback())),
            min(n, 1000) if n > 0 else 1000)


class _PinnedReadConnection(_QueryConnection):
    def execute(self, sql, parameters=()):
        # Owner query code may request BEGIN even when the enclosing snapshot
        # already pins this exact root. It cannot commit or release that pin.
        if self.managed and sql.strip().rstrip(';').upper() in {'BEGIN', 'BEGIN DEFERRED', 'BEGIN TRANSACTION'}:
            return super().execute('SELECT 1 WHERE 0')
        return super().execute(sql, parameters)


def now() -> str:
    return datetime.now(UTC).isoformat(timespec='microseconds')


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def reject_links(path: Path, root: Path) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise LaneError('PATH_OUTSIDE_ROOT', 'The path is outside its selected root.') from None
    cursor = root
    for component in (None, *relative.parts):
        if component is not None:
            cursor /= component
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise LaneError('LINKED_STORAGE_PATH', 'Project storage cannot cross linked paths.')


def _new_database(path: Path, statements: str, insert: tuple[str, tuple], application_id: int,
                  *, additional_inserts: tuple[tuple[str, tuple], ...] = ()) -> None:
    # Exclusive creation preserves collisions. A partial file is diagnostic state,
    # never silently overwritten or admitted as an initialized database.
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    connection = sqlite3.connect(path)
    try:
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute('PRAGMA synchronous=FULL')
        connection.executescript(statements)
        connection.execute(f'PRAGMA application_id={application_id}')
        connection.execute(*insert)
        for statement, parameters in additional_inserts:
            connection.execute(statement, parameters)
        connection.commit()
    finally:
        connection.close()


class _SqliteStorage:
    root: Path
    database: Path
    files: Path
    project_id: str
    lane_id: str | None
    read_only: bool

    def require_transaction(self, connection) -> None:
        active = _commit_scope.get()
        if (active is None or active.project.root != self.root
                or active.connection(self.lane_id) is not connection):
            raise LaneError('LANE_TRANSACTION_MISMATCH', 'Use the owning lane connection in this coordinated commit.')

    @contextmanager
    def connection(self, *, read_only: bool | None = None) -> Iterator[sqlite3.Connection]:
        readonly = self.read_only if read_only is None else read_only
        scope = _read_scope.get()
        if scope is not None and (self.root != scope[0] or not readonly):
            raise LaneError('QUERY_PROJECT_SCOPE', 'A query only permits reads within its selected project.')
        if self.read_only and not readonly:
            raise LaneError('READ_ONLY_PROJECT', 'This project connection cannot write.')
        active = _commit_scope.get()
        if active is not None:
            if active.project.root != self.root:
                raise LaneError('COMMIT_PROJECT_SCOPE', 'A coordinated write belongs to one project.')
            if not readonly or self.lane_id is None or self.lane_id in active.connections:
                yield active.connection(self.lane_id)
            else:
                # Root's exclusive lock already pins every unmodified lane.
                # Explicit reference reads do not enlist that authority for
                # writing or advance its published head.
                with self._raw_connection(read_only=True) as connection:
                    cast(LaneStore, self)._verify_published_head(active.root)
                    yield connection
            return
        if not readonly and self.lane_id is not None:
            raise LaneError('COORDINATED_TRANSACTION_REQUIRED', 'Write this lane through a coordinated transaction.')
        if readonly:
            with project_snapshot(self.root) as root:
                if self.lane_id is None:
                    yield root
                    return
                with self._raw_connection(read_only=True) as connection:
                    cast(LaneStore, self)._verify_published_head(root)
                    yield connection
        else:
            with self._raw_connection(read_only=False) as connection:
                if connection.execute("SELECT 1 FROM root_transaction_journal WHERE phase='prepared' LIMIT 1").fetchone():
                    raise LaneError('PROJECT_RECOVERY_REQUIRED', 'Recover the interrupted lane commit before writing.')
                yield connection

    @contextmanager
    def _raw_connection(self, *, read_only: bool | None = None) -> Iterator[sqlite3.Connection]:
        readonly = self.read_only if read_only is None else read_only
        scope = _read_scope.get()
        if scope is not None:
            if self.root != scope[0] or not readonly:
                raise LaneError("QUERY_PROJECT_SCOPE", "A query only permits read access to its selected target.")
            if time.monotonic() >= scope[1]:
                raise LaneError("QUERY_TIMEOUT", "The query exceeded its selected time budget.")
        if self.read_only and not readonly:
            raise LaneError("READ_ONLY_PROJECT", "This project connection cannot write.")
        if not readonly and (self.root / '.backup-sealed').exists():
            raise LaneError('SEALED_BACKUP_READ_ONLY', 'Restore this sealed backup into a fresh state root before writing.')
        reject_links(self.database, self.root)
        for suffix in ('-journal', '-wal', '-shm'):
            reject_links(Path(str(self.database) + suffix), self.root)
        uri = self.database.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
        connection = sqlite3.connect(uri, uri=True, timeout=10, isolation_level=None,
                                     factory=_QueryConnection if scope is not None else _StorageConnection)
        connection.row_factory = sqlite3.Row
        if scope is not None:
            cast(_QueryConnection, connection).deadline = scope[1]
            connection.set_progress_handler(None, 1000)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            busy_ms = max(1, min(10000, int((scope[1] - time.monotonic()) * 1000))) if scope else 10000
            busy_ms = min(busy_ms, _sqlite_wait_scope.get())
            connection.execute(f"PRAGMA busy_timeout={busy_ms}")
            if readonly:
                connection.execute("PRAGMA query_only=ON")
            else:
                connection.execute("PRAGMA synchronous=FULL")
            connection.root_only = self.lane_id is None
            connection.set_authorizer(None)
            if self.lane_id is not None:
                try:
                    identity = connection.execute('SELECT * FROM lane_identity WHERE singleton=1').fetchone()
                    valid = (connection.execute('PRAGMA application_id').fetchone()[0] == LANE_APPLICATION_ID
                             and identity is not None and identity['project_id'] == self.project_id
                             and identity['lane_id'] == self.lane_id and identity['storage_layout'] == STORAGE_LAYOUT
                             and identity['kind'] == cast(LaneStore, self).definition.kind)
                except sqlite3.DatabaseError:
                    valid = False
                if not valid:
                    raise LaneError('LANE_IDENTITY_MISMATCH', 'The selected database belongs to another lane or project.')
            yield connection
        except sqlite3.OperationalError:
            if scope is not None and time.monotonic() >= scope[1]:
                raise LaneError("QUERY_TIMEOUT", "The query exceeded its selected time budget.") from None
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = _commit_scope.get()
        if active is not None:
            if active.project.root != self.root:
                raise LaneError('COMMIT_PROJECT_SCOPE', 'A coordinated write belongs to one project.')
            with active.savepoint():
                yield active.connection(self.lane_id)
            return
        if self.lane_id is not None:
            with cast(LaneStore, self).project.coordinated_transaction([self.lane_id]) as commit:
                yield commit.connection(self.lane_id)
            return
        with self.connection(read_only=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                if self.lane_id is None:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
                    unexpected = {name for name in tables if name not in {'project', 'project_registration', 'schema_migrations', 'schema_ownership'}
                                  and not name.startswith(('root_', 'writer_', 'sqlite_'))}
                    if unexpected:
                        raise LaneError('ROOT_BUSINESS_SCHEMA_FORBIDDEN', 'project evidence head coordinator stores coordination references only; select the owning lane.')
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def object_path(self, digest: str) -> Path:
        scope = _read_scope.get()
        if scope is not None:
            if self.root != scope[0]:
                raise LaneError("QUERY_PROJECT_SCOPE", "A query cannot read another project's content implicitly.")
            if time.monotonic() >= scope[1]:
                raise LaneError("QUERY_TIMEOUT", "The query exceeded its selected time budget.")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise LaneError("INVALID_DIGEST", "A lowercase SHA-256 content address is required.")
        path = self.files / digest[:2] / digest[2:]
        reject_links(path, self.root)
        return path

    def put_object(self, content: bytes, *, limit: int = MAX_OBJECT_BYTES) -> str:
        if _read_scope.get() is not None:
            raise LaneError("QUERY_PROJECT_SCOPE", "Queries cannot publish content objects.")
        if self.read_only:
            raise LaneError("READ_ONLY_PROJECT", "This project connection cannot write.")
        if not 0 <= len(content) <= min(limit, MAX_OBJECT_BYTES):
            raise LaneError("OBJECT_TOO_LARGE", "The content exceeds its storage budget.")
        digest = hashlib.sha256(content).hexdigest()
        target = self.object_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        reject_links(target, self.root)
        try:
            descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            existing = self.read_object(digest, require_registered=False)
            if existing != content:
                raise LaneError(
                    "OBJECT_INTEGRITY_FAILED", "Existing content differs from its address."
                )
        else:
            # A failed write is never registered and remains an explicit integrity error.
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO objects VALUES(?,?,?)", (digest, len(content), now())
            )
        return digest

    def read_object(self, digest: str, *, require_registered: bool = True) -> bytes:
        path = self.object_path(digest)
        if require_registered:
            with self.connection(read_only=True) as connection:
                registered = connection.execute(
                    "SELECT size_bytes FROM objects WHERE digest=?", (digest,)
                ).fetchone()
            if registered is None:
                raise LaneError(
                    "OBJECT_NOT_REGISTERED",
                    "The content address is not registered in this project.",
                )
        try:
            size = path.stat().st_size
            if size > MAX_OBJECT_BYTES:
                raise LaneError(
                    "OBJECT_TOO_LARGE", "Stored content exceeds its supported byte budget."
                )
            with path.open("rb") as stream:
                content = stream.read(MAX_OBJECT_BYTES + 1)
        except FileNotFoundError:
            raise LaneError("OBJECT_MISSING", "The addressed content is missing.") from None
        if len(content) > MAX_OBJECT_BYTES or hashlib.sha256(content).hexdigest() != digest:
            raise LaneError("OBJECT_INTEGRITY_FAILED", "Stored content does not match its address.")
        if require_registered and registered["size_bytes"] != len(content):
            raise LaneError(
                "OBJECT_INTEGRITY_FAILED", "Stored content size differs from its registry."
            )
        return content

    def append_receipt(self, kind: str, body: dict[str, Any], *, connection=None) -> str:
        if _read_scope.get() is not None:
            raise LaneError("QUERY_PROJECT_SCOPE", "Queries cannot record project receipts.")
        if self.read_only:
            raise LaneError("READ_ONLY_PROJECT", "This project connection cannot write.")
        encoded = json_text(body)
        if not kind or len(encoded.encode("utf-8")) > 1_000_000:
            raise LaneError("INVALID_RECEIPT", "A bounded receipt and kind are required.")
        receipt_id = str(uuid4())
        values = (receipt_id, kind, encoded, now())
        if connection is not None:
            connection.execute("INSERT INTO receipts VALUES(?,?,?,?)", values)
        else:
            with self.transaction() as transaction:
                transaction.execute("INSERT INTO receipts VALUES(?,?,?,?)", values)
        return receipt_id


class ProjectStore(_SqliteStorage):
    """Selected project root; business storage requires an explicit lane."""
    lane_id = None

    def __init__(self, root: Path, *, read_only: bool = False):
        lexical = Path(os.path.abspath(root.expanduser()))
        reject_links(lexical, Path(lexical.anchor))
        self.root = lexical.resolve(strict=True)
        self.database = self.root / DATABASE_NAME
        self.read_only = read_only
        migration_marker = self.root / '.lane-migration.json'
        reject_links(migration_marker, self.root)
        if migration_marker.exists():
            try:
                if migration_marker.stat().st_size > 1_000_000:
                    raise ValueError('marker limit')
                migration = json.loads(migration_marker.read_text(encoding='utf-8'))
                if migration.get('status') != 'complete':
                    raise ValueError('unfinished migration')
            except (OSError, ValueError, AttributeError):
                raise LaneError('INCOMPLETE_LANE_MIGRATION',
                                'This migration did not complete; preserve its files and use explicit recovery.') from None
        if not self.database.exists() and (self.root / LEGACY_DATABASE_NAME).exists():
            raise LaneError('SHARED_PROJECT_MIGRATION_REQUIRED',
                            'The shared-database prototype requires an explicit migration into a fresh state root.')
        reject_links(self.database, self.root)
        bound_scope = _snapshot_scope.get() is not None or _commit_scope.get() is not None
        binding_connection = self.connection(read_only=True) if bound_scope else self._raw_connection(read_only=read_only)
        with binding_connection as connection:
            try:
                application_id = connection.execute('PRAGMA application_id').fetchone()[0]
                metadata = connection.execute('SELECT * FROM project WHERE singleton=1').fetchone()
            except sqlite3.DatabaseError:
                raise LaneError('UNRECOGNIZED_PROJECT', 'This is not a separate-lane v4 project root.') from None
            if application_id != APPLICATION_ID or metadata is None:
                raise LaneError('UNRECOGNIZED_PROJECT', 'The root PV identity is invalid.')
            if metadata['format_version'] != FORMAT_VERSION or metadata['storage_layout'] != STORAGE_LAYOUT:
                raise LaneError('UNSUPPORTED_PROJECT_VERSION', 'The project layout requires a compatible engine.')
            try:
                UUID(metadata['project_id'])
            except ValueError:
                raise LaneError('INVALID_PROJECT_IDENTITY', 'The stored project identity is invalid.') from None
            self.project_id = metadata['project_id']
            self.source_root = Path(metadata['source_root'])
            # Root identity must remain readable so an explicit writer can
            # recover an interrupted commit. Use this same binding connection;
            # opening a published project snapshot here would prevent recovery.
            # Lane/business reads still require the ordinary snapshot boundary.
            self.registration = self._read_registration(binding_connection=connection)

    @staticmethod
    def registration_body(project_id, display_name, sensitivity, capture_route):
        """Immutable registration choices, separate from lane business records."""
        if (not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 160
                or any(ord(c) < 32 or ord(c) == 127 for c in display_name)):
            raise LaneError('PROJECT_DISPLAY_NAME_INVALID', 'Use a project display name of at most 160 characters without controls.')
        sensitivity = sensitivity.strip().upper() if isinstance(sensitivity, str) else ''
        capture_route = capture_route.strip().upper().replace('-', '_') if isinstance(capture_route, str) else ''
        if sensitivity not in {'PUBLIC', 'INTERNAL', 'PRIVATE', 'CONFIDENTIAL', 'RESTRICTED'}:
            raise LaneError('PROJECT_SENSITIVITY_INVALID', 'Select a supported project sensitivity label.')
        if capture_route not in {'GOVERNED_PROJECT_FULL', 'ENV_BUILDER_SPARSE'}:
            raise LaneError('CAPTURE_ROUTE_INVALID', 'Select full visible capture or sparse governed capture.')
        return {'schema': 'evidence-lane.project-registration.v4', 'project_id': project_id,
                'display_name': display_name.strip(), 'sensitivity': sensitivity, 'capture_route': capture_route,
                'sensitivity_enforcement': 'metadata_label_only', 'archive_encryption_claimed': False}

    def _read_registration(self, *, binding_connection=None):
        with nullcontext(binding_connection) if binding_connection is not None else self.connection(read_only=True) as connection:
            identity = connection.execute('SELECT * FROM project WHERE singleton=1').fetchone()
            if 'registration_digest' not in identity.keys():  # noqa: SIM118 -- sqlite3.Row membership tests values, not column names.
                # Earlier v4 roots captured full visible lineage. A read must not
                # fabricate a user selection or rewrite these existing bytes.
                return {'display_name': self.source_root.name or self.project_id, 'sensitivity': None,
                        'capture_route': 'GOVERNED_PROJECT_FULL', 'registration_digest': None,
                        'registration_bound': False, 'sensitivity_enforcement': 'not_recorded'}
            try:
                row = connection.execute('SELECT body_json FROM project_registration WHERE singleton=1').fetchone()
                if row is None or len(row[0].encode()) > 4096:
                    raise ValueError('missing or oversized registration')
                body = json.loads(row[0])
                expected = self.registration_body(self.project_id, body['display_name'], body['sensitivity'], body['capture_route'])
                digest = hashlib.sha256(json_text(body).encode()).hexdigest()
                if body != expected or digest != identity['registration_digest']:
                    raise ValueError('registration differs')
            except (sqlite3.DatabaseError, ValueError, KeyError, TypeError, LaneError):
                raise LaneError('PROJECT_REGISTRATION_INTEGRITY', 'The immutable project registration differs from its root identity.') from None
        return {**{key: body[key] for key in ('display_name', 'sensitivity', 'capture_route', 'sensitivity_enforcement')},
                'registration_digest': digest, 'registration_bound': True}

    @classmethod
    def create(cls, root: Path, source_root: Path, *, project_id: str | None = None,
               display_name: str | None = None, sensitivity: str = 'PRIVATE',
               capture_route: str = 'GOVERNED_PROJECT_FULL') -> ProjectStore:
        if _read_scope.get() is not None:
            raise LaneError('QUERY_PROJECT_SCOPE', 'Queries cannot create project storage.')
        root = Path(os.path.abspath(root.expanduser()))
        source_root = source_root.expanduser().resolve(strict=True)
        if not source_root.is_dir():
            raise LaneError('SOURCE_ROOT_REQUIRED', 'Select a source directory.')
        if root.is_relative_to(source_root) or source_root.is_relative_to(root):
            raise LaneError('EXTERNAL_STATE_REQUIRED', 'Select a state directory separate from the source tree.')
        reject_links(root, Path(root.anchor))
        if root.exists() and any(root.iterdir()):
            raise LaneError('PROJECT_ALREADY_EXISTS', 'Create project storage only in a fresh empty root.')
        identity = str(UUID(project_id)) if project_id else str(uuid4())
        registration = cls.registration_body(identity, display_name if display_name is not None else source_root.name or identity,
                                             sensitivity, capture_route)
        registration_json = json_text(registration)
        registration_digest = hashlib.sha256(registration_json.encode()).hexdigest()
        root.mkdir(parents=True, exist_ok=True)
        try:
            _new_database(root / DATABASE_NAME, CORE_SCHEMA,
                          ('INSERT INTO project VALUES(1,?,?,?,?,?,?)',
                           (identity, str(source_root), FORMAT_VERSION, STORAGE_LAYOUT, now(), registration_digest)), APPLICATION_ID,
                          additional_inserts=(('INSERT INTO project_registration VALUES(1,?)', (registration_json,)),))
        except FileExistsError:
            raise LaneError('PROJECT_ALREADY_EXISTS', 'Open the existing project instead of replacing it.') from None
        project = cls(root)
        # Authorities are always present, even before their first business
        # migration. Reading an empty authority never needs to create storage.
        for definition in LANE_REGISTRY.values():
            if definition.kind == 'authority':
                project._initialize_lane(definition)
        return project

    def assert_current_binding(self) -> None:
        with self.connection(read_only=True) as connection:
            row = connection.execute('SELECT project_id,source_root FROM project WHERE singleton=1').fetchone()
            if row is None or row['project_id'] != self.project_id or row['source_root'] != str(self.source_root):
                raise LaneError('SOURCE_BINDING_CHANGED', 'Reopen the project after its source binding changes.')
        if self._read_registration() != self.registration:
            raise LaneError('PROJECT_REGISTRATION_CHANGED', 'The immutable project registration changed after this store was opened.')

    def lane(self, lane_id: str, *, create: bool = False) -> LaneStore:
        definition = get_lane(lane_id)
        if create:
            active = _commit_scope.get()
            if active is not None:
                active.connection(definition.canonical_lane_id)
            else:
                self._initialize_lane(definition)
        return LaneStore(self, definition)

    def coordinated_transaction(self, lanes, *, writer=None, expected_revision=None, fault=None):
        from .lane_transactions import coordinated_transaction
        return coordinated_transaction(self, lanes, writer=writer,
                                       expected_revision=expected_revision, fault=fault)

    def recover_transactions(self, *, writer=None, fault=None):
        from .lane_transactions import recover_transactions
        return recover_transactions(self, writer=writer, fault=fault)

    def pv_head(self) -> dict:
        with self.connection(read_only=True) as connection:
            return dict(connection.execute('SELECT * FROM root_pv_head WHERE singleton=1').fetchone())

    def _initialize_lane(self, definition: LaneDefinition) -> None:
        if self.read_only or _read_scope.get() is not None:
            raise LaneError('READ_ONLY_PROJECT', 'Read-only project access cannot initialize lanes.')
        self.assert_current_binding()
        lane_id = definition.canonical_lane_id
        with self.transaction() as root_connection:
            found = root_connection.execute('SELECT * FROM root_lane_catalog WHERE lane_id=?', (lane_id,)).fetchone()
            if found:
                if found['database_path'] != definition.database_relative_path or found['kind'] != definition.kind:
                    raise LaneError('LANE_BINDING_MISMATCH', 'The lane catalog differs from its canonical layout.')
                return
            folder = self.root / definition.folder
            database = self.root / definition.database_relative_path
            reject_links(database, self.root)
            folder.mkdir(parents=True, exist_ok=True)
            if database.exists():
                raise LaneError('UNREGISTERED_LANE_DATABASE', 'Preserve the unregistered database and use explicit recovery.')
            _new_database(database, LANE_SCHEMA + (RECEIPTS_SCHEMA if lane_id == 'receipts' else ''),
                          ('INSERT INTO lane_identity VALUES(1,?,?,?,?,?)',
                           (self.project_id, lane_id, definition.kind, STORAGE_LAYOUT, now())), LANE_APPLICATION_ID)
            for relative in (definition.files_relative_path, definition.schema_history_relative_path):
                path = self.root / relative
                reject_links(path, self.root)
                path.mkdir(parents=True, exist_ok=True)
            root_connection.execute('INSERT INTO root_lane_catalog VALUES(?,?,?,?)',
                                    (lane_id, definition.kind, definition.database_relative_path, now()))

    def lane_catalog(self) -> list[dict]:
        with self.connection(read_only=True) as connection:
            return [dict(row) for row in connection.execute(
                'SELECT c.*,h.revision,h.head_digest,h.commit_id FROM root_lane_catalog c '
                'LEFT JOIN root_lane_heads h USING(lane_id) ORDER BY c.lane_id')]

    def object_path(self, digest: str) -> Path:
        raise LaneError('EXPLICIT_LANE_REQUIRED', 'Select the owning lane before accessing project content.')

    def append_receipt(self, kind: str, body: dict[str, Any], *, connection=None) -> str:
        if connection is not None:
            active = _commit_scope.get()
            if active is None or not active.owns(connection) or active.project.root != self.root:
                raise LaneError('RECEIPTS_TRANSACTION_REQUIRED', 'Enlist Receipts in the same coordinated transaction.')
            return self.lane('receipts').append_receipt(kind, body, connection=active.connection('receipts'))
        return self.lane('receipts', create=True).append_receipt(kind, body)


class LaneStore(_SqliteStorage):
    """One canonical lane database and file root bound to one project identity."""
    def __init__(self, project: ProjectStore, definition: LaneDefinition):
        self.project = project
        self.root = project.root
        self.project_id = project.project_id
        self.source_root = project.source_root
        self.read_only = project.read_only
        self.definition = definition
        self.lane_id = definition.canonical_lane_id
        self.database = self.root / definition.database_relative_path
        self.files = self.root / definition.files_relative_path
        self.folder = self.root / definition.folder
        self.schema_history = self.root / definition.schema_history_relative_path
        with project.connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM root_lane_catalog WHERE lane_id=?', (self.lane_id,)).fetchone()
        if row is None:
            raise LaneError('LANE_NOT_INITIALIZED', 'The selected lane has no initialized database.')
        if row['database_path'] != definition.database_relative_path or row['kind'] != definition.kind:
            raise LaneError('LANE_BINDING_MISMATCH', 'The lane catalog differs from its canonical layout.')
        with self.connection(read_only=True) as connection:
            try:
                application_id = connection.execute('PRAGMA application_id').fetchone()[0]
                identity = connection.execute('SELECT * FROM lane_identity WHERE singleton=1').fetchone()
            except sqlite3.DatabaseError:
                raise LaneError('LANE_IDENTITY_MISMATCH', 'The selected lane database identity is invalid.') from None
        if application_id != LANE_APPLICATION_ID or identity is None or any(identity[k] != value for k, value in
             {'project_id': self.project_id, 'lane_id': self.lane_id, 'kind': definition.kind, 'storage_layout': STORAGE_LAYOUT}.items()):
            raise LaneError('LANE_IDENTITY_MISMATCH', 'The selected database belongs to another lane or project.')

    def assert_current_binding(self) -> None:
        self.project.assert_current_binding()

    def _verify_published_head(self, root_connection):
        row = root_connection.execute('SELECT head_digest FROM root_lane_heads WHERE lane_id=?', (self.lane_id,)).fetchone()
        if row is not None:
            with self.database.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            head = hashlib.sha256(json_text({'lane_id': self.lane_id, 'database_sha256': digest}).encode()).hexdigest()
            if head != row['head_digest']:
                raise LaneError('LANE_HEAD_MISMATCH', 'The lane database differs from its published project evidence head coordinator head.')

    def append_receipt(self, kind: str, body: dict[str, Any], *, connection=None) -> str:
        if self.lane_id != 'receipts':
            if connection is not None:
                return self.project.append_receipt(kind, {**body, 'owner_lane': self.lane_id}, connection=connection)
            return self.project.append_receipt(kind, {**body, 'owner_lane': self.lane_id})
        return super().append_receipt(kind, body, connection=connection)
