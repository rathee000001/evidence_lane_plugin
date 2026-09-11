"""SQLite execution engine selection and post-build verification.

The core speed law is one explicit transaction for bulk writes.  APSW is the
preferred Codex runtime adapter because it exposes the complete SQLite API;
stdlib sqlite3 remains the deterministic compatibility fallback for source
development before the hidden runtime is installed.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import sqlite3
import stat
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .errors import EvidenceLaneError, LaneError
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


def _capture_sqlite_files(path: Path, max_bytes: int, *,
                          expected_members: dict[str, tuple[int, str]] | None = None) -> tuple[dict[str, bytes | None], dict]:
    """Read the bounded source set without opening it with SQLite."""
    from .storage import reject_links

    parts: dict[str, bytes | None] = {}
    identities: dict[str, tuple | None] = {}
    consumed = 0
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    # Windows path-stat may still expose creation time as ctime while fstat
    # exposes change time. Compare the shared identity fields across APIs and
    # retain full, separate before/after comparisons within each API.
    descriptor_fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns')
    for suffix in ('', '-wal', '-journal', '-shm'):
        if suffix == '-shm' and expected_members is not None:
            # A registered source reader neither requires nor reads the derived index.
            parts[suffix], identities[suffix] = None, None
            continue
        member = path.with_name(path.name + suffix)
        reject_links(member, Path(member.anchor))
        try:
            before = member.stat()
        except FileNotFoundError:
            if expected_members is not None and suffix in expected_members:
                raise ValueError('SOURCE_SQLITE_REGISTERED_MEMBER_MISSING') from None
            if not suffix:
                raise
            parts[suffix], identities[suffix] = None, None
            continue
        if expected_members is not None and suffix not in expected_members:
            raise ValueError('SOURCE_SQLITE_UNREGISTERED_SIDECAR')
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('SQLITE_SOURCE_REGULAR_FILE_REQUIRED')
        if before.st_size > max_bytes - consumed:
            raise ValueError('DATA_SOURCE_BYTE_BUDGET')
        if expected_members is not None and before.st_size != expected_members[suffix][0]:
            raise ValueError('SOURCE_SQLITE_REGISTERED_MEMBER_CHANGED')
        descriptor = os.open(member, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if tuple(getattr(before, field) for field in descriptor_fields) != tuple(getattr(opened, field) for field in descriptor_fields):
                raise ValueError('DATA_SOURCE_CHANGED_DURING_INSPECTION')
            content = stream.read(max_bytes - consumed + 1)
            ended = os.fstat(stream.fileno())
        reject_links(member, Path(member.anchor))
        after = member.stat()
        identity = tuple(getattr(before, field) for field in fields)
        if (identity != tuple(getattr(after, field) for field in fields)
                or tuple(getattr(opened, field) for field in fields) != tuple(getattr(ended, field) for field in fields)
                or len(content) != before.st_size):
            raise ValueError('DATA_SOURCE_CHANGED_DURING_INSPECTION')
        consumed += len(content)
        if consumed > max_bytes:
            raise ValueError('DATA_SOURCE_BYTE_BUDGET')
        if expected_members is not None and sha256_bytes(content).lower() != expected_members[suffix][1].lower():
            raise ValueError('SOURCE_SQLITE_REGISTERED_MEMBER_CHANGED')
        parts[suffix], identities[suffix] = content, (identity, tuple(getattr(opened, field) for field in fields))
    return parts, identities


@contextmanager
def private_sqlite_image(content: bytes) -> Iterator[Path]:
    """An immutable caller-owned byte image in a disposable private directory."""
    if type(content) is not bytes or not content.startswith(b'SQLite format 3\x00'):
        raise ValueError('SQLITE_SOURCE_HEADER_INVALID')
    if not 100 <= len(content) <= 8_388_608:
        raise ValueError('DATA_SOURCE_BYTE_BUDGET')
    with tempfile.TemporaryDirectory(prefix='evidence-lane-sqlite-') as directory:
        target = Path(directory) / 'inspection.sqlite'
        target.write_bytes(content)
        yield target


@contextmanager
def private_sqlite_bundle(parts: Mapping[str, bytes | None], *, max_bytes: int) -> Iterator[tuple[Path, dict]]:
    """Stage caller-owned bytes and recover only this disposable private image."""
    from .bounded_io import run_owned_bounded_process

    if (type(max_bytes) is not int or not 1 <= max_bytes <= 768 * 1024 * 1024
            or set(parts) - {'', '-wal', '-journal', '-shm'}
            or any(raw is not None and type(raw) is not bytes for raw in parts.values())
            or sum(len(raw) for raw in parts.values() if raw is not None) > max_bytes):
        raise ValueError('SQLITE_RECOVERY_BYTE_BUDGET')
    main, journal = parts.get(''), parts.get('-journal')
    needs_recovery = bool(journal and journal[0])
    if main is None or len(main) < 100 or (not needs_recovery and not main.startswith(b'SQLite format 3\x00')):
        raise ValueError('SQLITE_SOURCE_HEADER_INVALID')
    if needs_recovery and parts.get('-wal') is not None:
        raise ValueError('SQLITE_RECOVERY_CONFLICTING_JOURNALS')
    with tempfile.TemporaryDirectory(prefix='evidence-lane-sqlite-') as directory:
        target = Path(directory) / 'inspection.sqlite'
        for suffix, raw in parts.items():
            if suffix != '-shm' and raw is not None:
                target.with_name(target.name + suffix).write_bytes(raw)
        recovery: dict[str, Any] = {'status': 'NOT_REQUIRED'}
        if needs_recovery:
            assert journal is not None
            worker = Path(__file__).with_name('_sqlite_recovery_worker.py')
            worker_sha256 = sha256_file(worker).lower()
            request = {'main_sha256': sha256_bytes(main).lower(),
                'journal_sha256': sha256_bytes(journal).lower(), 'max_bytes': max_bytes}
            environment = {key: value for key, value in os.environ.items() if key.upper() in {
                'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
            environment.update(TEMP=directory, TMP=directory)
            result = run_owned_bounded_process([sys.executable, '-I', '-S', '-B', str(worker), json.dumps(request)],
                cwd=directory, env=environment, timeout_seconds=15,
                max_stdout_bytes=16_384, max_stderr_bytes=16_384)
            try:
                recovery = json.loads(result.stdout)
            except (ValueError, UnicodeError):
                raise ValueError('SQLITE_RECOVERY_RESULT_INVALID') from None
            if not isinstance(recovery, dict) or result.returncode != 0 or recovery.get('status') != 'ROLLED_BACK_PRIVATE_COPY':
                code = recovery.get('error_code', 'SQLITE_RECOVERY_FAILED') if isinstance(recovery, dict) else 'SQLITE_RECOVERY_RESULT_INVALID'
                if not isinstance(code, str) or not code.startswith('SQLITE_'):
                    code = 'SQLITE_RECOVERY_RESULT_INVALID'
                raise ValueError(code)
            if (not target.is_file() or target.stat().st_size > max_bytes
                    or target.stat().st_size != recovery.get('recovered_bytes')
                    or sha256_file(target).lower() != recovery.get('recovered_sha256')
                    or recovery.get('source_paths_received') is not False
                    or recovery.get('external_journal_reference_followed') is not False):
                raise ValueError('SQLITE_RECOVERY_RESULT_MISMATCH')
            if sha256_file(worker).lower() != worker_sha256:
                raise ValueError('SQLITE_RECOVERY_WORKER_CHANGED')
            recovery['worker_sha256'] = worker_sha256
            recovery['owned_worker_joined'] = True
        yield target, recovery


@contextmanager
def private_sqlite_source(path: Path, *, max_bytes: int,
                          expected_sha256: str | None = None,
                          expected_members: dict[str, tuple[int, str]] | None = None) -> Iterator[tuple[Path, dict]]:
    """Capture stable main/WAL bytes; SQLite touches only the private copy.

    The shared-memory file is checked for source stability but is never copied:
    SQLite rebuilds that transient index beside the private WAL. Its bytes are
    not part of the durable main/WAL/journal identity.
    """
    if type(max_bytes) is not int or not 1 <= max_bytes <= 768 * 1024 * 1024:
        raise ValueError('SQLITE_SOURCE_BYTE_BUDGET_INVALID')
    source = path.absolute()
    before = _capture_sqlite_files(source, max_bytes, expected_members=expected_members)
    parts = before[0]
    main = parts['']
    assert main is not None
    journal = parts.get('-journal')
    if len(main) < 100 or (not main.startswith(b'SQLite format 3\x00') and not (journal and journal[0])):
        raise ValueError('SQLITE_SOURCE_HEADER_INVALID')
    source_sha = sha256_bytes(main).lower()
    if expected_sha256 is not None and source_sha != expected_sha256:
        raise ValueError('DATA_SOURCE_HASH_MISMATCH')
    members = [{'role': suffix or 'main', 'bytes': len(raw), 'sha256': sha256_bytes(raw).lower()}
        for suffix, raw in parts.items() if suffix != '-shm' and raw is not None]
    identity = {'source_sha256': source_sha, 'source_members': members,
        'source_state_sha256': sha256_bytes(canonical_json_bytes(members)).lower(),
        'wal_present': bool(parts['-wal']), 'rollback_journal_present': bool(parts['-journal']),
        'shared_memory_copied': False, 'source_hash_verified_before_and_after': True,
        'source_observation_atomic': False, 'source_set_limit_bytes': max_bytes,
        'source_observation_bytes': sum(len(raw) for raw in parts.values() if raw is not None)}
    try:
        with private_sqlite_bundle(parts, max_bytes=max_bytes) as (target, recovery):
            identity['private_recovery'] = recovery
            yield target, identity
    finally:
        if _capture_sqlite_files(source, max_bytes, expected_members=expected_members) != before:
            raise ValueError('DATA_SOURCE_CHANGED_DURING_INSPECTION')


class SQLiteInspectionBudget:
    """Cumulative limits for one owned read connection, never a writer grant.

    A worker's process deadline remains the outer bound for blocking OS I/O.
    The progress handler bounds SQLite VM work; callers also check between
    statements so many tiny queries cannot evade the elapsed-time limit.
    """

    def __init__(self, *, max_vm_steps: int = 20_000_000, timeout_seconds: float = 15):
        if (type(max_vm_steps) is not int or not 1000 <= max_vm_steps <= 20_000_000
                or type(timeout_seconds) not in {int, float}
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 15):
            raise ValueError('SQLITE_INSPECTION_BUDGET_INVALID')
        self.max_vm_steps = max_vm_steps
        self.timeout_seconds = timeout_seconds
        self.deadline = time.monotonic() + timeout_seconds
        self.vm_steps = 0
        self.exhausted = False

    def _progress(self) -> int:
        # SQLite can reset its callback interval at statement boundaries. An
        # interval of 1000 would miss a sequence of short metadata statements.
        # Count every instruction, but sample the clock once per 1000 to avoid
        # an OS clock call on each instruction. check() covers statement gaps.
        self.vm_steps += 1
        self.exhausted = (self.exhausted or self.vm_steps >= self.max_vm_steps
            or (self.vm_steps % 1000 == 0 and time.monotonic() >= self.deadline))
        return int(self.exhausted)

    def check(self) -> None:
        self.exhausted = self.exhausted or time.monotonic() >= self.deadline
        if self.exhausted:
            raise EvidenceLaneError('SQLITE_INSPECTION_EXECUTION_BUDGET',
                'SQLite inspection exceeded its cumulative execution budget.', status='BLOCKED')

    def configure(self, connection: sqlite3.Connection) -> None:
        self.check()
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('PRAGMA busy_timeout=250')
        connection.enable_load_extension(False)
        if hasattr(connection, 'setconfig'):
            connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.set_progress_handler(self._progress, 1)


class SQLiteExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["evidence-lane.sqlite-execution.v1"]
    status: Literal["PASS"]
    engine: Literal["APSW", "STDLIB_SQLITE3"]
    engine_version: str
    sqlite_version: str
    database_path: str
    database_sha256: str
    integrity_check: Literal["ok"]
    journal_mode: str
    page_count: int
    page_size: int
    explicit_bulk_transaction_required: Literal[True]
    apsw_full_api_available: bool
    sqlite_session_available: bool
    sqlite_rbu_available: bool
    sqlite_backup_available: bool
    sqlite_fts5_available: bool
    durable_authority: Literal[True]
    receipt_sha256: str


def apsw_available() -> bool:
    return importlib.util.find_spec("apsw") is not None


def _owned_sqlite_lane(commit: Any, lane_id: str, phase: str) -> tuple[Any, sqlite3.Connection]:
    from .lanes import get_lane
    from .storage import _commit_scope

    if _commit_scope.get() is not commit or commit is None or commit.phase != phase:
        raise LaneError('SQLITE_COMMIT_OWNER_REQUIRED', 'SQLite maintenance requires the active owning lane commit.')
    canonical = get_lane(lane_id).canonical_lane_id
    connection = commit.connection(canonical)
    if canonical not in commit.lanes or not commit.root.in_transaction:
        raise LaneError('SQLITE_COMMIT_OWNER_REQUIRED', 'The lane must belong to the current unpublished commit.')
    return commit.lanes[canonical], connection


def optimize_owned_sqlite_lane(commit: Any, lane_id: str) -> dict[str, Any]:
    """Optimize on the already owned write connection, before its only commit."""
    lane, connection = _owned_sqlite_lane(commit, lane_id, 'writing')
    if not connection.in_transaction:
        raise LaneError('SQLITE_OWNING_TRANSACTION_REQUIRED', 'Optimize only inside the existing lane transaction.')
    budget = SQLiteInspectionBudget()
    previous_limit = int(connection.execute('PRAGMA analysis_limit').fetchone()[0])
    before_changes = connection.total_changes
    connection.set_progress_handler(budget._progress, 1)
    try:
        connection.execute('PRAGMA analysis_limit=1000')
        connection.execute('PRAGMA optimize').fetchall()
        budget.check()
    except sqlite3.DatabaseError:
        budget.check()
        raise
    finally:
        connection.set_progress_handler(None, 0)
        connection.execute(f'PRAGMA analysis_limit={previous_limit}')
    if not connection.in_transaction:
        raise LaneError('SQLITE_OWNING_TRANSACTION_LOST', 'Optimization changed its owning transaction boundary.')
    return {'project_id': lane.project_id, 'lane_id': lane.lane_id, 'commit_id': commit.commit_id,
        'engine': 'STDLIB_SQLITE3', 'sqlite_version': sqlite3.sqlite_version,
        'operation': 'PRAGMA optimize', 'existing_write_connection': True,
        'transaction_committed_by_helper': False, 'analysis_limit': 1000,
        'changes': connection.total_changes - before_changes, 'vm_steps': budget.vm_steps}


@contextmanager
def _committed_lane_reader(path: Path, budget: SQLiteInspectionBudget) -> Iterator[tuple[Any, dict[str, Any]]]:
    """Open a committed internal lane with native read-only SQLite flags."""
    if apsw_available():
        import apsw  # type: ignore[import-not-found]

        connection = apsw.Connection(path.as_uri() + '?mode=ro',
            flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI)
        try:
            connection.set_busy_timeout(250)
            connection.enable_load_extension(False)
            connection.config(apsw.SQLITE_DBCONFIG_DEFENSIVE, True)
            for category, value in ((apsw.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024),
                    (apsw.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024), (apsw.SQLITE_LIMIT_COLUMN, 256),
                    (apsw.SQLITE_LIMIT_ATTACHED, 0)):
                connection.limit(category, value)
            connection.set_progress_handler(budget._progress, 1)
            connection.execute('PRAGMA trusted_schema=OFF')
            connection.execute('PRAGMA query_only=ON')
            if not connection.readonly('main'):
                raise LaneError('SQLITE_NATIVE_READ_ONLY_REQUIRED', 'The committed lane reader must be read-only.')
            yield connection, {'engine': 'APSW', 'engine_version': apsw.apswversion(),
                'sqlite_version': apsw.sqlitelibversion(), 'native_read_only': True,
                'sqlite_session_available': hasattr(apsw, 'Session'),
                'sqlite_rbu_available': hasattr(apsw, 'RBU'),
                'sqlite_backup_available': hasattr(apsw.Connection, 'backup')}
        except apsw.Error:
            budget.check()
            raise
        finally:
            connection.close()
    else:
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0.25)
        try:
            budget.configure(connection)
            yield connection, {'engine': 'STDLIB_SQLITE3', 'engine_version': sys.version.split()[0],
                'sqlite_version': sqlite3.sqlite_version, 'native_read_only': True,
                'sqlite_session_available': False, 'sqlite_rbu_available': False,
                'sqlite_backup_available': hasattr(sqlite3.Connection, 'backup')}
        except sqlite3.DatabaseError:
            budget.check()
            raise
        finally:
            connection.close()


def verify_committed_sqlite_lane(commit: Any, lane_id: str) -> dict[str, Any]:
    """Validate actual committed bytes without opening another write connection."""
    from .storage import LANE_APPLICATION_ID, STORAGE_LAYOUT, reject_links

    lane, writer = _owned_sqlite_lane(commit, lane_id, 'validating_lanes')
    if writer.in_transaction:
        raise LaneError('SQLITE_LANE_NOT_COMMITTED', 'Read the lane only after its owning connection commits.')
    path = lane.database
    reject_links(path, lane.project.root)
    before = sha256_file(path).lower()
    budget = SQLiteInspectionBudget()
    with _committed_lane_reader(path, budget) as (reader, engine):
        identity = reader.execute('SELECT project_id,lane_id,kind,storage_layout FROM lane_identity WHERE singleton=1').fetchone()
        expected = (lane.project_id, lane.lane_id, lane.definition.kind, STORAGE_LAYOUT)
        if identity is None or tuple(identity) != expected or reader.execute('PRAGMA application_id').fetchone()[0] != LANE_APPLICATION_ID:
            raise LaneError('LANE_IDENTITY_MISMATCH', 'The committed database changed its owning lane identity.')
        integrity = reader.execute('PRAGMA integrity_check(1)').fetchone()
        if integrity is None or tuple(integrity) != ('ok',):
            raise LaneError('LANE_SQLITE_INTEGRITY_FAILED', 'The committed lane failed SQLite integrity validation.')
        if reader.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise LaneError('LANE_FOREIGN_KEY_FAILED', 'The committed lane contains invalid local references.')
        journal_mode = str(reader.execute('PRAGMA journal_mode').fetchone()[0])
        if journal_mode != 'delete':
            raise LaneError('UNSUPPORTED_JOURNAL_MODE', 'Lane publication requires DELETE journal mode.')
        page_count = int(reader.execute('PRAGMA page_count').fetchone()[0])
        page_size = int(reader.execute('PRAGMA page_size').fetchone()[0])
        options: set[str] = set()
        for row in reader.execute('PRAGMA compile_options'):
            if len(options) >= 512:
                raise LaneError('SQLITE_ENGINE_METADATA_BUDGET', 'The engine exceeded its capability metadata bound.')
            options.add(str(row[0]))
        budget.check()
    reject_links(path, lane.project.root)
    if sha256_file(path).lower() != before:
        raise LaneError('SQLITE_COMMITTED_LANE_CHANGED', 'The lane bytes changed during pre-publication validation.')
    core = {'schema': 'evidence-lane.sqlite-lane-commit-validation.v4', 'status': 'PASS',
        'phase': 'COMMITTED_UNPUBLISHED', 'project_id': lane.project_id, 'lane_id': lane.lane_id,
        'commit_id': commit.commit_id, 'database_sha256': before, **engine,
        'integrity_check': 'ok', 'foreign_key_check': 'ok', 'journal_mode': journal_mode,
        'page_count': page_count, 'page_size': page_size, 'database_bytes_mutated_by_validation': False,
        'sqlite_fts5_available': 'ENABLE_FTS5' in options, 'vm_steps': budget.vm_steps,
        'max_vm_steps': budget.max_vm_steps, 'timeout_seconds': budget.timeout_seconds}
    return {**core, 'receipt_sha256': sha256_bytes(canonical_json_bytes(core)).lower()}


def verify_and_optimize_sqlite_authority(
    database_path: str | Path,
) -> SQLiteExecutionReceipt:
    """Reject the superseded arbitrary-path writer until its legacy consumer purge."""
    raise LaneError('SQLITE_COMMIT_OWNER_REQUIRED', 'Use the current coordinated lane commit for SQLite maintenance.')


__all__ = [
    "SQLiteExecutionReceipt",
    "apsw_available",
    "verify_and_optimize_sqlite_authority",
]
