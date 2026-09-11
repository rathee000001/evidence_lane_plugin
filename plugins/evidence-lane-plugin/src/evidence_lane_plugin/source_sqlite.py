"""Read-only forensic intake for direct and ZIP-embedded SQLite authorities."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import tempfile
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import EvidenceLaneError, LaneError, require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .source_authority import _connect as _connect_registry
from .source_authority import (
    _open_source_archive,
    initialize_source_authority_registry,
    source_authority_write,
)
from .sqlite_execution import SQLiteInspectionBudget, private_sqlite_bundle, private_sqlite_source
from .storage import LaneStore, ProjectStore
from .timeutil import utc_now

SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
SQLITE_HEADER = b"SQLite format 3\x00"
MAX_SCHEMA_OBJECTS = 4096
MAX_FOREIGN_KEYS = 4096


@dataclass
class SQLiteBatchBudget:
    max_assets: int = 512
    max_total_input_bytes: int = 256 * 1024 * 1024
    max_metadata_bytes: int = 8 * 1024 * 1024
    max_batch_vm_steps: int = 40_000_000
    timeout_seconds: float = 15
    vm_steps: int = 0
    metadata_calls: int = 0

    def __post_init__(self):
        limits = ((self.max_assets, 1, 512), (self.max_total_input_bytes, 1, 4 * 1024**3),
            (self.max_metadata_bytes, 1024, 32 * 1024 * 1024), (self.max_batch_vm_steps, 1000, 200_000_000))
        require(all(type(value) is int and lower <= value <= upper for value, lower, upper in limits)
            and type(self.timeout_seconds) in {int, float} and math.isfinite(self.timeout_seconds)
            and 0 < self.timeout_seconds <= 60,
            'SOURCE_SQLITE_BATCH_BUDGET_INVALID', 'SQLite batch budgets are invalid.', status='BLOCKED')
        self.deadline = time.monotonic() + self.timeout_seconds

    def check(self):
        require(self.vm_steps < self.max_batch_vm_steps and time.monotonic() < self.deadline,
            'SOURCE_SQLITE_BATCH_EXECUTION_BUDGET', 'The complete SQLite batch exceeded its execution budget.', status='BLOCKED')

    def progress(self):
        self.vm_steps += 1
        return int(self.vm_steps >= self.max_batch_vm_steps
            or (self.vm_steps % 1000 == 0 and time.monotonic() >= self.deadline))

    def admit(self, assets):
        self.check()
        require(len(assets) <= self.max_assets, 'SOURCE_SQLITE_BATCH_ASSET_BUDGET',
            'Select fewer SQLite occurrences for this batch.', status='BLOCKED')
        observed = 0
        for asset in assets:
            values = [size for size, _ in asset.expected_members.values()]
            if asset.source_kind == 'zip':
                values.append(asset.container_size_bytes)
            require(all(type(value) is int and value >= 0 for value in values),
                'SOURCE_SQLITE_AUTHORITY_INCOMPLETE', 'Complete registered input sizes are required.', status='BLOCKED')
            observed += sum(values)
            require(observed <= self.max_total_input_bytes, 'SOURCE_SQLITE_BATCH_INPUT_BUDGET',
                'The complete SQLite batch exceeds its registered input-byte budget.', status='BLOCKED')
        return observed

    def contract(self):
        return {key: getattr(self, key) for key in ('max_assets', 'max_total_input_bytes',
            'max_metadata_bytes', 'max_batch_vm_steps', 'timeout_seconds')}


@dataclass(frozen=True, slots=True)
class RegisteredSQLiteAsset:
    ordinal: int
    object_id: str
    source_pointer: str
    source_kind: str
    member_path: str
    size_bytes: int | None
    byte_sha256: str | None
    policy_state: str
    policy_reason: str
    sidecars: tuple[tuple[str, int, str], ...] = ()
    container_sha256: str | None = None
    container_size_bytes: int | None = None

    @property
    def key(self) -> tuple[str, str]:
        return self.object_id, self.member_path

    @property
    def expected_members(self) -> dict[str, tuple[int, str]]:
        return {'': (int(self.size_bytes or 0), str(self.byte_sha256)),
            **{suffix: (size, digest) for suffix, size, digest in self.sidecars}}

    @property
    def source_members(self) -> list[dict[str, Any]]:
        return [{'role': suffix or 'main', 'bytes': size, 'sha256': digest.lower()}
            for suffix, (size, digest) in self.expected_members.items()]

    @property
    def state_sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.source_members)).lower()





def _registered_assets(path: ProjectStore | LaneStore, batch_id: str) -> list[RegisteredSQLiteAsset]:
    with _connect_registry(path) as connection:
        member_rows = list(
            connection.execute(
                """SELECT o.ordinal, s.object_id, s.source_pointer, s.kind,
                m.member_path, m.size_bytes, m.sha256, m.policy_state, m.policy_reason,
                s.byte_sha256 AS container_sha256, s.size_bytes AS container_size_bytes
                FROM source_occurrence o
                JOIN source_object s USING(object_id)
                JOIN source_member m USING(object_id)
                WHERE o.batch_id=? AND (
                    lower(m.member_path) GLOB '*.db'
                    OR lower(m.member_path) GLOB '*.sqlite'
                    OR lower(m.member_path) GLOB '*.sqlite3'
                )
                ORDER BY o.ordinal, m.member_path COLLATE NOCASE, m.member_path""",
                (batch_id,),
            ).fetchmany(513)
        )
        direct_rows = list(
            connection.execute(
                """SELECT o.ordinal, s.object_id, s.source_pointer, s.kind,
                '<direct-file>' AS member_path, s.size_bytes, s.byte_sha256 AS sha256,
                'INCLUDED' AS policy_state, 'POLICY_APPROVED' AS policy_reason,
                NULL AS container_sha256, NULL AS container_size_bytes
                FROM source_occurrence o JOIN source_object s USING(object_id)
                WHERE o.batch_id=? AND s.kind='file' AND (
                    lower(s.source_pointer) GLOB '*.db'
                    OR lower(s.source_pointer) GLOB '*.sqlite'
                    OR lower(s.source_pointer) GLOB '*.sqlite3'
                ) ORDER BY o.ordinal""",
                (batch_id,),
            ).fetchmany(513)
        )
    assets = [
        RegisteredSQLiteAsset(
            ordinal=int(row["ordinal"]),
            object_id=str(row["object_id"]),
            source_pointer=str(row["source_pointer"]),
            source_kind=str(row["kind"]),
            member_path=str(row["member_path"]),
            size_bytes=(
                int(row["size_bytes"]) if row["size_bytes"] is not None else None
            ),
            byte_sha256=(str(row["sha256"]) if row["sha256"] else None),
            policy_state=str(row["policy_state"]),
            policy_reason=str(row["policy_reason"]),
            container_sha256=str(row['container_sha256']) if row['container_sha256'] else None,
            container_size_bytes=int(row['container_size_bytes']) if row['container_size_bytes'] is not None else None,
        )
        for row in [*member_rows, *direct_rows]
    ]
    require(len(assets) <= 512, 'SOURCE_SQLITE_ASSET_BUDGET',
        'Select at most 512 registered SQLite occurrences per inspection.', status='BLOCKED')
    with _connect_registry(path) as connection:
        result = []
        for asset in assets:
            sidecars = []
            for suffix in ('-wal', '-journal'):
                if asset.member_path == '<direct-file>':
                    rows = connection.execute('''SELECT DISTINCT s.size_bytes, s.byte_sha256 AS sha256
                        FROM source_occurrence o JOIN source_object s USING(object_id)
                        WHERE o.batch_id=? AND s.kind='file' AND s.resolved_pointer=
                        (SELECT resolved_pointer FROM source_object WHERE object_id=?) || ?''',
                        (batch_id, asset.object_id, suffix)).fetchmany(2)
                else:
                    rows = connection.execute('''SELECT size_bytes, sha256 FROM source_member
                        WHERE object_id=? AND member_path=? AND policy_state='INCLUDED' ''',
                        (asset.object_id, asset.member_path + suffix)).fetchmany(2)
                require(len(rows) <= 1, 'SOURCE_SQLITE_AMBIGUOUS_SIDECAR',
                    'A SQLite sidecar has conflicting registered identities.', status='MISMATCH')
                if rows and rows[0]['size_bytes'] is not None and rows[0]['sha256']:
                    sidecars.append((suffix, int(rows[0]['size_bytes']), str(rows[0]['sha256'])))
            result.append(replace(asset, sidecars=tuple(sidecars)))
        return result


def _archive_skip_objects(path: ProjectStore | LaneStore, batch_id: str) -> set[str]:
    with _connect_registry(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                """SELECT archive_object_id FROM source_archive_receipt
                WHERE batch_id=? AND skip_status=
                'SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART'""",
                (batch_id,),
            )
        }


def _direct_path(asset: RegisteredSQLiteAsset) -> Path:
    source = Path(asset.source_pointer)
    if asset.member_path == "<direct-file>":
        return source.absolute()
    return (source / PurePosixPath(asset.member_path)).absolute()


@contextmanager
def _open_direct(asset: RegisteredSQLiteAsset, *, max_bytes: int = 768 * 1024 * 1024
                 , observation: dict | None = None
                 ) -> Iterator[tuple[sqlite3.Connection, str]]:
    try:
        with private_sqlite_source(_direct_path(asset), max_bytes=max_bytes,
                expected_sha256=str(asset.byte_sha256).lower(), expected_members=asset.expected_members) as (target, identity):
            require(identity['source_state_sha256'] == asset.state_sha256,
                'SOURCE_SQLITE_STATE_MISMATCH', 'The private source state differs from registration.', status='STALE')
            if observation is not None:
                observation['temporary_image_created'] = True
                observation['private_recovery'] = identity['private_recovery']
            connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=0.25)
            try:
                yield connection, 'sqlite_private_registered_image_mode_ro_query_only'
            finally:
                connection.close()
    except ValueError as error:
        code = str(error)
        if code == 'SQLITE_SOURCE_HEADER_INVALID':
            code = 'SOURCE_SQLITE_HEADER_INVALID'
        raise EvidenceLaneError(code, 'The registered SQLite source image could not be verified.',
            status='MISMATCH') from error


@contextmanager
def _open_embedded(asset: RegisteredSQLiteAsset, *, max_embedded_member_bytes: int
                   , observation: dict | None = None
                   ) -> Iterator[tuple[sqlite3.Connection, str]]:
    expected = asset.expected_members
    require(sum(size for size, _ in expected.values()) <= max_embedded_member_bytes,
        'SOURCE_SQLITE_EMBEDDED_SIZE_LIMIT', 'The complete SQLite member set exceeds its byte budget.', status='BLOCKED')
    archive_path = Path(asset.source_pointer).absolute()
    parts: dict[str, bytes] = {}
    with _open_source_archive(archive_path, expected_sha256=asset.container_sha256,
            expected_size=asset.container_size_bytes) as archive:
        for suffix in ('', '-wal', '-journal'):
            matches = [info for info in archive.infolist() if not info.is_dir()
                and info.filename.replace('\\', '/') == asset.member_path + suffix]
            if suffix not in expected:
                require(not matches, 'SOURCE_SQLITE_UNREGISTERED_SIDECAR',
                    'An adjacent archive sidecar is not registered for inspection.', status='BLOCKED')
                continue
            require(len(matches) == 1, 'SOURCE_SQLITE_ARCHIVE_MEMBER_MISMATCH',
                'A registered SQLite member is missing or duplicated.', status='STALE')
            member = matches[0]
            size, digest = expected[suffix]
            require(member.file_size == size and size <= max_embedded_member_bytes,
                'SOURCE_SQLITE_ARCHIVE_MEMBER_CHANGED', 'A registered member changed size.', status='STALE')
            with archive.open(member) as stream:
                payload = stream.read(size + 1)
            require(len(payload) == size and sha256_bytes(payload).lower() == digest.lower(),
                'SOURCE_SQLITE_ARCHIVE_MEMBER_CHANGED', 'A registered member changed bytes.', status='STALE')
            parts[suffix] = payload
        main = parts['']
        journal = parts.get('-journal')
        needs_recovery = bool(journal and journal[0])
        require(len(main) >= 100 and (main.startswith(SQLITE_HEADER) or needs_recovery), 'SOURCE_SQLITE_HEADER_INVALID',
            'A registered SQLite image has an invalid header.', status='MISMATCH')
        if len(parts) == 1 and main[18:20] == b'\x01\x01':
            connection = sqlite3.connect(':memory:')
            try:
                connection.deserialize(main)
                yield connection, 'sqlite_in_memory_deserialize_query_only'
            finally:
                connection.close()
            return
        with private_sqlite_bundle(parts, max_bytes=max_embedded_member_bytes) as (target, recovery):
            if observation is not None:
                observation['temporary_image_created'] = True
                observation['private_recovery'] = recovery
            connection = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=0.25)
            try:
                yield connection, 'sqlite_private_registered_archive_mode_ro_query_only'
            finally:
                connection.close()


def _inspect_connection(
    connection: sqlite3.Connection,
    asset: RegisteredSQLiteAsset,
    *,
    inspection_mode: str,
    exact_count_max_database_bytes: int,
    batch_budget: SQLiteBatchBudget | None = None,
) -> dict[str, Any]:
    budget = SQLiteInspectionBudget()
    try:
        budget.configure(connection)
        if batch_budget is not None:
            batch_budget.check()
            batch_budget.metadata_calls += 1
            def progress():
                individual, total = budget._progress(), batch_budget.progress()
                return int(individual or total)
            connection.set_progress_handler(progress, 1)
        result = _inspect_connection_metadata(connection, asset, inspection_mode=inspection_mode,
            exact_count_max_database_bytes=exact_count_max_database_bytes, budget=budget)
        budget.check()
        if batch_budget is not None:
            batch_budget.check()
        return result
    except sqlite3.DatabaseError:
        if batch_budget is not None:
            batch_budget.check()
        budget.check()
        raise


def _inspect_connection_metadata(
    connection: sqlite3.Connection,
    asset: RegisteredSQLiteAsset,
    *,
    inspection_mode: str,
    exact_count_max_database_bytes: int,
    budget: SQLiteInspectionBudget,
) -> dict[str, Any]:
    byte_sha256 = asset.byte_sha256
    require(
        byte_sha256 is not None,
        "SOURCE_SQLITE_AUTHORITY_INCOMPLETE",
        "A SQLite inspection requires registered byte authority.",
        status="BLOCKED",
    )
    assert byte_sha256 is not None
    connection.row_factory = sqlite3.Row
    integrity = [
        str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchmany(101)
    ]
    integrity_truncated = len(integrity) > 100
    if integrity_truncated:
        integrity = integrity[:100]
    foreign_key_errors = [
        dict(row)
        for row in connection.execute("PRAGMA foreign_key_check").fetchmany(1001)
    ]
    foreign_key_errors_truncated = len(foreign_key_errors) > 1000
    if foreign_key_errors_truncated:
        foreign_key_errors = foreign_key_errors[:1000]
    schema_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
        ).fetchmany(MAX_SCHEMA_OBJECTS + 1)
    ]
    require(len(schema_rows) <= MAX_SCHEMA_OBJECTS, 'SOURCE_SQLITE_SCHEMA_BUDGET',
        'SQLite schema exceeds the inspection object budget.', status='BLOCKED')
    schema_objects = [
        {
            "object_type": str(row["type"]),
            "object_name": str(row["name"]),
            "table_name": str(row["tbl_name"]),
            "sql_sha256": (
                sha256_bytes(str(row["sql"]).encode("utf-8"))
                if row["sql"] is not None
                else None
            ),
        }
        for row in schema_rows
    ]
    schema_sha256 = sha256_bytes(canonical_json_bytes(schema_objects))
    table_stats: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    fts_tables: list[str] = []
    for row in schema_rows:
        budget.check()
        if row["type"] != "table":
            continue
        table_name = str(row["name"])
        quoted = table_name.replace('"', '""')
        sql = str(row.get("sql") or "")
        virtual = sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
        fts = virtual and "USING FTS" in sql.upper()
        if fts:
            fts_tables.append(table_name)
        fk_rows = connection.execute(
            f'PRAGMA foreign_key_list("{quoted}")'  # nosec B608
        ).fetchmany(MAX_FOREIGN_KEYS - len(foreign_keys) + 1)
        require(len(foreign_keys) + len(fk_rows) <= MAX_FOREIGN_KEYS,
            'SOURCE_SQLITE_FOREIGN_KEY_BUDGET',
            'SQLite relationships exceed the inspection budget.', status='BLOCKED')
        for fk in fk_rows:
            payload = dict(fk)
            foreign_keys.append(
                {
                    "from_table": table_name,
                    "foreign_key_id": int(payload["id"]),
                    "sequence_id": int(payload["seq"]),
                    "to_table": payload.get("table"),
                    "from_column": payload.get("from"),
                    "to_column": payload.get("to"),
                    "on_update": payload.get("on_update"),
                    "on_delete": payload.get("on_delete"),
                    "match_rule": payload.get("match"),
                }
            )
        if virtual:
            row_count = None
            count_state = "VIRTUAL_TABLE_NOT_COUNTED"
        elif sum(size for size, _ in asset.expected_members.values()) > exact_count_max_database_bytes:
            row_count = None
            count_state = "DEFERRED_TO_DELTA075_SIZE_BOUND"
        else:
            try:
                row_count = int(
                    connection.execute(  # nosec B608
                        f'SELECT COUNT(*) FROM "{quoted}"'
                    ).fetchone()[0]
                )
                count_state = "EXACT"
            except sqlite3.DatabaseError as error:
                budget.check()
                if getattr(error, 'sqlite_errorcode', None) == sqlite3.SQLITE_INTERRUPT:
                    raise
                row_count = None
                count_state = f"UNREADABLE:{type(error).__name__}"
        table_stats.append(
            {
                "table_name": table_name,
                "row_count": row_count,
                "count_state": count_state,
            }
        )
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    encoding = str(connection.execute("PRAGMA encoding").fetchone()[0])
    passed = integrity == ["ok"] and not foreign_key_errors
    receipt = {
        "schema": "evidence-lane.source-sqlite-inspection.v1",
        "canonical_asset_id": f"sqlite_{byte_sha256[:32].lower()}",
        "byte_sha256": byte_sha256,
        "size_bytes": asset.size_bytes,
        "inspection_mode": inspection_mode,
        "status": "PASS" if passed else "FAIL",
        "integrity": integrity,
        "integrity_truncated": integrity_truncated,
        "foreign_key_error_count": len(foreign_key_errors),
        "foreign_key_errors_truncated": foreign_key_errors_truncated,
        "user_version": user_version,
        "application_id": application_id,
        "page_count": page_count,
        "page_size": page_size,
        "encoding": encoding,
        "schema_sha256": schema_sha256,
        "schema_object_count": len(schema_objects),
        "table_count": len(table_stats),
        "fts_table_count": len(fts_tables),
        "exact_count_table_count": sum(
            row["count_state"] == "EXACT" for row in table_stats
        ),
        "deferred_count_table_count": sum(
            row["count_state"] == "DEFERRED_TO_DELTA075_SIZE_BOUND"
            for row in table_stats
        ),
        "imported_sql_executed": False,
        "source_bytes_mutated": False,
        "source_payload_persisted": False,
        "inspection_limits": {
            "max_schema_objects": MAX_SCHEMA_OBJECTS,
            "max_foreign_keys": MAX_FOREIGN_KEYS,
            "max_vm_steps": budget.max_vm_steps,
            "timeout_seconds": budget.timeout_seconds,
        },
    }
    return {
        "receipt": receipt,
        "schema_objects": schema_objects,
        "table_stats": table_stats,
        "foreign_keys": foreign_keys,
        "foreign_key_errors": foreign_key_errors,
    }


def _inspect_asset(asset: RegisteredSQLiteAsset, *, max_embedded_member_bytes: int,
                   exact_count_max_database_bytes: int, reuse: dict[str, Any] | None = None,
                   observation: dict | None = None, batch_budget: SQLiteBatchBudget | None = None) -> dict[str, Any]:
    require(asset.byte_sha256 is not None and asset.size_bytes is not None,
        'SOURCE_SQLITE_AUTHORITY_INCOMPLETE', 'A SQLite asset requires registered bytes and size.', status='BLOCKED')
    manager = (_open_embedded(asset, max_embedded_member_bytes=max_embedded_member_bytes, observation=observation)
        if asset.source_kind == 'zip' else _open_direct(asset, max_bytes=max_embedded_member_bytes, observation=observation))
    with manager as (connection, mode):
        # Each alias is verified even when metadata work can reuse a measured state.
        if reuse is not None:
            result = reuse
        else:
            result = _inspect_connection(connection, asset, inspection_mode=mode,
                exact_count_max_database_bytes=exact_count_max_database_bytes, batch_budget=batch_budget)
    return result


def _collect_sqlite_inspections(inspectable: list[RegisteredSQLiteAsset], *,
        max_embedded_member_bytes: int, exact_count_max_database_bytes: int,
        batch_budget: SQLiteBatchBudget) -> dict[str, Any]:
    input_bytes = batch_budget.admit(inspectable)
    canonical_indices: dict[str, int] = {}
    inspections: dict[str, dict[str, Any]] = {}
    occurrence_keys = [''] * len(inspectable)
    metadata_bytes = 0
    archive_payloads_staged = False
    for index, asset in sorted(enumerate(inspectable), key=lambda row: (row[1].source_kind == 'zip', row[1].ordinal, row[1].member_path.casefold())):
        batch_budget.check()
        state_key = asset.state_sha256
        observation: dict[str, Any] = {}
        try:
            inspection = _inspect_asset(asset, max_embedded_member_bytes=max_embedded_member_bytes,
                exact_count_max_database_bytes=exact_count_max_database_bytes, reuse=inspections.get(state_key),
                observation=observation, batch_budget=batch_budget)
            key = state_key
        except (EvidenceLaneError, OSError, sqlite3.DatabaseError, zipfile.BadZipFile, ValueError) as error:
            if isinstance(error, EvidenceLaneError) and error.code == 'SOURCE_SQLITE_BATCH_EXECUTION_BUDGET':
                raise
            code, status = ((error.code, error.status) if isinstance(error, EvidenceLaneError)
                else (str(error), 'MISMATCH') if isinstance(error, ValueError) else (type(error).__name__, 'FAIL'))
            # A stale alias must not inherit PASS from an intact canonical occurrence.
            key = sha256_bytes(canonical_json_bytes([state_key, asset.key, code])).lower()
            inspection = {'receipt': {'byte_sha256': asset.byte_sha256, 'size_bytes': asset.size_bytes,
                'inspection_mode': 'failed_before_read_only_inspection', 'status': status, 'error_code': code,
                'schema_sha256': None, 'source_bytes_mutated': False, 'source_payload_persisted': False,
                'imported_sql_executed': False}, 'schema_objects': [], 'table_stats': [],
                'foreign_keys': [], 'foreign_key_errors': []}
        archive_payloads_staged |= asset.source_kind == 'zip' and bool(observation.get('temporary_image_created'))
        if key not in inspections:
            receipt = dict(inspection['receipt'])
            receipt.pop('canonical_asset_id', None)
            receipt.update(schema='evidence-lane.source-sqlite-inspection.v2',
                source_state_sha256=state_key, source_members=asset.source_members,
                source_observation_atomic=False, shared_memory_copied=False,
                registered_members_verified=not bool(receipt.get('error_code')),
                exact_count_max_database_bytes=exact_count_max_database_bytes,
                source_set_limit_bytes=max_embedded_member_bytes,
                temporary_source_image=bool(observation.get('temporary_image_created')),
                private_recovery=observation.get('private_recovery', {'status': 'NOT_REQUIRED'}))
            receipt['canonical_asset_id'] = 'sqlite_v4_' + sha256_bytes(canonical_json_bytes(receipt)).lower()
            inspections[key] = {**inspection, 'receipt': receipt}
            metadata_bytes += len(canonical_json_bytes(inspections[key]))
            require(metadata_bytes <= batch_budget.max_metadata_bytes, 'SOURCE_SQLITE_BATCH_METADATA_BUDGET',
                'The complete SQLite batch exceeds its metadata budget.', status='BLOCKED')
            canonical_indices[key] = index
        occurrence_keys[index] = key
        batch_budget.check()
    return {'inspections': inspections, 'canonical_indices': canonical_indices,
        'occurrence_keys': occurrence_keys, 'archive_payloads_staged': archive_payloads_staged,
        'input_bytes': input_bytes, 'vm_steps': batch_budget.vm_steps, 'metadata_calls': batch_budget.metadata_calls}


def _batch_programs() -> dict[str, str]:
    return {name: sha256_file(Path(__file__).with_name(name)).lower() for name in (
        '_source_sqlite_batch_worker.py', 'source_sqlite.py', 'source_authority.py',
        'sqlite_execution.py', '_sqlite_recovery_worker.py', 'bounded_io.py', '_bounded_process_child.py')}


def _batch_asset(value: dict) -> RegisteredSQLiteAsset:
    require(isinstance(value, dict) and set(value) == set(RegisteredSQLiteAsset.__dataclass_fields__),
        'SOURCE_SQLITE_BATCH_REQUEST_INVALID', 'The registered asset descriptor is invalid.', status='BLOCKED')
    asset = RegisteredSQLiteAsset(**{**value, 'sidecars': tuple(tuple(row) for row in value['sidecars'])})
    require(type(asset.ordinal) is int and asset.ordinal > 0 and asset.policy_state == 'INCLUDED'
        and asset.source_kind in {'directory', 'zip', 'file'} and Path(asset.source_pointer).is_absolute()
        and type(asset.size_bytes) is int and asset.size_bytes >= 0
        and isinstance(asset.byte_sha256, str) and re.fullmatch(r'[0-9a-fA-F]{64}', asset.byte_sha256) is not None
        and len(asset.sidecars) <= 2,
        'SOURCE_SQLITE_BATCH_REQUEST_INVALID', 'The asset lacks bounded registered identity.', status='BLOCKED')
    if asset.source_kind == 'file':
        require(asset.member_path == '<direct-file>', 'SOURCE_SQLITE_BATCH_REQUEST_INVALID',
            'A direct asset has an invalid member locator.', status='BLOCKED')
    else:
        member = PurePosixPath(asset.member_path)
        require(not member.is_absolute() and bool(member.parts) and '..' not in member.parts
            and '\x00' not in asset.member_path and '\\' not in asset.member_path,
            'SOURCE_SQLITE_BATCH_REQUEST_INVALID', 'The registered member locator is unsafe.', status='BLOCKED')
    for suffix, size, digest in asset.sidecars:
        require(suffix in {'-wal', '-journal'} and type(size) is int and size >= 0
            and isinstance(digest, str) and re.fullmatch(r'[0-9a-fA-F]{64}', digest) is not None,
            'SOURCE_SQLITE_BATCH_REQUEST_INVALID', 'The sidecar identity is invalid.', status='BLOCKED')
    require(len({row[0] for row in asset.sidecars}) == len(asset.sidecars),
        'SOURCE_SQLITE_BATCH_REQUEST_INVALID', 'The sidecar identities are ambiguous.', status='BLOCKED')
    return asset


def _validate_batch_result(result: dict, assets: list[RegisteredSQLiteAsset], budget: SQLiteBatchBudget) -> None:
    require(isinstance(result, dict) and set(result) == {'inspections', 'canonical_indices', 'occurrence_keys',
        'archive_payloads_staged', 'input_bytes', 'vm_steps', 'metadata_calls'},
        'SOURCE_SQLITE_BATCH_RESULT_INVALID', 'The batch worker result shape is invalid.', status='MISMATCH')
    inspections, indices, occurrences = result['inspections'], result['canonical_indices'], result['occurrence_keys']
    require(isinstance(inspections, dict) and isinstance(indices, dict) and set(inspections) == set(indices)
        and isinstance(occurrences, list) and len(occurrences) == len(assets)
        and all(key in inspections for key in occurrences)
        and result['input_bytes'] == budget.admit(assets)
        and type(result['vm_steps']) is int and 0 <= result['vm_steps'] < budget.max_batch_vm_steps
        and type(result['metadata_calls']) is int and 0 <= result['metadata_calls'] <= len(assets),
        'SOURCE_SQLITE_BATCH_RESULT_INVALID', 'The batch result does not cover the registered occurrences.', status='MISMATCH')
    for key, inspection in inspections.items():
        index = indices[key]
        require(type(index) is int and 0 <= index < len(assets), 'SOURCE_SQLITE_BATCH_RESULT_INVALID',
            'The canonical occurrence is outside its request.', status='MISMATCH')
        asset = assets[index]
        receipt = inspection['receipt']
        core = {name: value for name, value in receipt.items() if name != 'canonical_asset_id'}
        require(receipt['source_state_sha256'] == asset.state_sha256 and receipt['source_members'] == asset.source_members
            and receipt['byte_sha256'] == asset.byte_sha256 and receipt['size_bytes'] == asset.size_bytes
            and receipt['canonical_asset_id'] == 'sqlite_v4_' + sha256_bytes(canonical_json_bytes(core)).lower()
            and occurrences[index] == key,
            'SOURCE_SQLITE_BATCH_RESULT_MISMATCH', 'The canonical result changed its registered source identity.', status='MISMATCH')
    for asset, key in zip(assets, occurrences, strict=True):
        require(inspections[key]['receipt']['source_state_sha256'] == asset.state_sha256,
            'SOURCE_SQLITE_BATCH_RESULT_MISMATCH', 'An occurrence was linked to a different source state.', status='MISMATCH')


def _run_sqlite_batch(assets: list[RegisteredSQLiteAsset], *, max_embedded_member_bytes: int,
        exact_count_max_database_bytes: int, batch_budget: SQLiteBatchBudget) -> tuple[dict, dict]:
    from .bounded_io import run_owned_bounded_process

    input_bytes = batch_budget.admit(assets)
    programs = _batch_programs()
    program_digest = sha256_bytes(canonical_json_bytes(programs)).lower()
    descriptors = [{**asdict(asset), 'source_pointer': str(Path(asset.source_pointer).expanduser().absolute())}
        for asset in assets]
    request = {'schema': 'evidence-lane.sqlite-batch-request.v4', 'assets': descriptors,
        'image_limits': {'max_embedded_member_bytes': max_embedded_member_bytes,
            'exact_count_max_database_bytes': exact_count_max_database_bytes},
        'batch_limits': batch_budget.contract(), 'programs': programs}
    encoded = canonical_json_bytes(request)
    require(len(encoded) <= 8 * 1024 * 1024, 'SOURCE_SQLITE_BATCH_REQUEST_BUDGET',
        'The batch descriptors exceed the internal request budget.', status='BLOCKED')
    request_digest = sha256_bytes(encoded).lower()
    with tempfile.TemporaryDirectory(prefix='evidence-lane-source-sqlite-') as directory:
        (Path(directory) / 'request.json').write_bytes(encoded)
        environment = {key: value for key, value in os.environ.items() if key.upper() in {
            'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
        environment.update(TEMP=directory, TMP=directory)
        result = run_owned_bounded_process([sys.executable, '-I', '-B',
            str(Path(__file__).with_name('_source_sqlite_batch_worker.py')), request_digest],
            cwd=directory, env=environment, timeout_seconds=batch_budget.timeout_seconds,
            max_stdout_bytes=batch_budget.max_metadata_bytes + 65_536, max_stderr_bytes=65_536)
        require(len(result.stdout) <= batch_budget.max_metadata_bytes + 65_536,
            'SOURCE_SQLITE_BATCH_METADATA_BUDGET', 'The worker response exceeds its budget.', status='BLOCKED')
        try:
            response = json.loads(result.stdout)
            if result.returncode != 0:
                code = response.get('error_code', 'SOURCE_SQLITE_BATCH_WORKER_FAILED')
                if not isinstance(code, str) or not re.fullmatch(r'SOURCE_SQLITE_[A-Z_]{1,70}', code):
                    code = 'SOURCE_SQLITE_BATCH_WORKER_FAILED'
                raise LaneError(code, 'The complete SQLite batch could not be inspected within its contract.')
            require(response['status'] == 'ok' and response['request_sha256'] == request_digest
                and response['program_sha256'] == program_digest and _batch_programs() == programs,
                'SOURCE_SQLITE_BATCH_BINDING_MISMATCH', 'The worker request or executable binding changed.', status='MISMATCH')
            collected = response['result']
            require(len(canonical_json_bytes(collected)) <= batch_budget.max_metadata_bytes,
                'SOURCE_SQLITE_BATCH_METADATA_BUDGET', 'The complete metadata result exceeds its budget.', status='BLOCKED')
            _validate_batch_result(collected, assets, batch_budget)
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            raise LaneError('SOURCE_SQLITE_BATCH_RESULT_INVALID', 'The worker returned an invalid complete result.') from error
    return collected, {'request_sha256': request_digest, 'program_sha256': program_digest,
        'owned_worker_joined': True, 'authority_database_path_supplied': False, 'project_writer_supplied': False,
        'input_bytes': input_bytes, 'input_budget_basis': 'per_occurrence_container_plus_expanded_sqlite_set',
        'vm_steps': collected['vm_steps'], 'metadata_calls': collected['metadata_calls'],
        'limits': batch_budget.contract()}


@source_authority_write
def inspect_registered_sqlite_assets(
    registry_path: ProjectStore | LaneStore,
    batch_id: str,
    *,
    max_embedded_member_bytes: int = 768 * 1024 * 1024,
    exact_count_max_database_bytes: int = 32 * 1024 * 1024,
    max_assets: int = 512,
    max_total_input_bytes: int = 256 * 1024 * 1024,
    max_metadata_bytes: int = 8 * 1024 * 1024,
    max_batch_vm_steps: int = 40_000_000,
    timeout_seconds: float = 15,
) -> dict[str, Any]:
    """Inspect every registered SQLite occurrence, deduplicated by frozen bytes."""

    require(type(max_embedded_member_bytes) is int and 1 <= max_embedded_member_bytes <= 768 * 1024 * 1024
        and type(exact_count_max_database_bytes) is int and 1 <= exact_count_max_database_bytes <= 32 * 1024 * 1024,
        'SOURCE_SQLITE_INSPECTION_BUDGET_INVALID', 'SQLite inspection byte budgets are invalid.', status='BLOCKED')
    batch_budget = SQLiteBatchBudget(max_assets, max_total_input_bytes, max_metadata_bytes,
        max_batch_vm_steps, timeout_seconds)
    target = initialize_source_authority_registry(registry_path)
    with _connect_registry(target) as connection:
        require(
            connection.execute(
                "SELECT 1 FROM intake_batch WHERE batch_id=?", (batch_id,)
            ).fetchone()
            is not None,
            "SOURCE_AUTHORITY_BATCH_MISSING",
            "The requested source authority batch is not registered.",
            status="MISMATCH",
            batch_id=batch_id,
        )
    assets = _registered_assets(target, batch_id)
    require(len(assets) <= batch_budget.max_assets, 'SOURCE_SQLITE_BATCH_ASSET_BUDGET',
        'Select fewer SQLite occurrences for this batch.', status='BLOCKED')
    skip_archive_objects = _archive_skip_objects(target, batch_id)
    inspectable = [
        asset
        for asset in assets
        if asset.policy_state == "INCLUDED"
        and asset.byte_sha256
        and not (
            asset.source_kind == "zip" and asset.object_id in skip_archive_objects
        )
    ]
    by_sha = {asset.byte_sha256 for asset in inspectable}
    collected, worker_receipt = _run_sqlite_batch(inspectable,
        max_embedded_member_bytes=max_embedded_member_bytes,
        exact_count_max_database_bytes=exact_count_max_database_bytes, batch_budget=batch_budget)
    inspections = collected['inspections']
    canonical_by_sha = {key: inspectable[index] for key, index in collected['canonical_indices'].items()}
    occurrence_keys = {asset.key: key for asset, key in zip(inspectable, collected['occurrence_keys'], strict=True)}
    archive_payloads_staged = collected['archive_payloads_staged']
    receipt_rows: dict[str, dict[str, Any]] = {}
    for sha, inspection in inspections.items():
        receipt = dict(inspection["receipt"])
        canonical_receipt_sha = sha256_bytes(canonical_json_bytes(receipt))
        receipt_rows[sha] = {
            **receipt,
            "receipt_sha256": canonical_receipt_sha,
        }
    nonpass_findings: list[dict[str, Any]] = []
    for sha in sorted(receipt_rows):
        receipt = receipt_rows[sha]
        if receipt["status"] == "PASS":
            continue
        canonical = canonical_by_sha[sha]
        if receipt.get("error_code"):
            error_code = str(receipt["error_code"])
        elif int(receipt.get("foreign_key_error_count") or 0):
            error_code = "SOURCE_SQLITE_FOREIGN_KEY_CHECK_FAILED"
        else:
            error_code = "SOURCE_SQLITE_INTEGRITY_CHECK_FAILED"
        nonpass_findings.append(
            {
                "canonical_asset_id": receipt["canonical_asset_id"],
                "status": receipt["status"],
                "error_code": error_code,
                "source_ordinal": canonical.ordinal,
                "member_path": canonical.member_path,
            }
        )
    asset_rows: list[dict[str, Any]] = []
    for asset in assets:
        asset_receipt: dict[str, Any] | None = receipt_rows.get(
            occurrence_keys.get(asset.key, asset.state_sha256)
        )
        state: str
        canonical_asset_id: str | None
        schema_sha: str | None
        asset_receipt_sha: str | None
        if asset.policy_state != "INCLUDED" or not asset.byte_sha256:
            state = "POLICY_EXCLUDED_NOT_INSPECTED"
            canonical_asset_id = None
            schema_sha = None
            asset_receipt_sha = None
        elif asset.source_kind == "zip" and asset.object_id in skip_archive_objects:
            state = "SKIPPED_EXACT_EXTRACTED_COUNTERPART"
            canonical_asset_id = (
                str(asset_receipt["canonical_asset_id"])
                if asset_receipt is not None
                else None
            )
            schema_sha = (
                str(asset_receipt["schema_sha256"])
                if asset_receipt is not None
                and asset_receipt["schema_sha256"] is not None
                else None
            )
            asset_receipt_sha = (
                str(asset_receipt["receipt_sha256"])
                if asset_receipt is not None
                else None
            )
        else:
            require(
                asset_receipt is not None,
                "SOURCE_SQLITE_RECEIPT_MISSING",
                "An inspectable SQLite occurrence lacks its canonical receipt.",
                status="FAIL",
                object_id=asset.object_id,
                member_path=asset.member_path,
            )
            assert asset_receipt is not None
            canonical = canonical_by_sha[occurrence_keys[asset.key]]
            state = (
                "CANONICAL_" + str(asset_receipt["status"])
                if asset.key == canonical.key
                else "DEDUP_REUSE_" + str(asset_receipt["status"])
            )
            canonical_asset_id = str(asset_receipt["canonical_asset_id"])
            schema_sha = (
                str(asset_receipt["schema_sha256"])
                if asset_receipt["schema_sha256"] is not None
                else None
            )
            asset_receipt_sha = str(asset_receipt["receipt_sha256"])
        asset_rows.append(
            {
                "object_id": asset.object_id,
                "member_path": asset.member_path,
                "inspection_state": state,
                "schema_sha256": schema_sha,
                "byte_sha256": asset.byte_sha256,
                "size_bytes": asset.size_bytes,
                "canonical_asset_id": canonical_asset_id,
                "inspection_receipt_sha256": asset_receipt_sha,
                "inspection_id": (canonical_asset_id or "excluded-v4") + ':' + state,
            }
        )
    append_status = "IDEMPOTENT_REUSE"
    with _connect_registry(target, write=True) as connection:
        for sha in sorted(inspections):
            inspection = inspections[sha]
            receipt = receipt_rows[sha]
            existing = connection.execute(
                "SELECT receipt_sha256 FROM source_sqlite_receipt WHERE canonical_asset_id=?",
                (receipt["canonical_asset_id"],),
            ).fetchone()
            require(
                existing is None
                or existing["receipt_sha256"] == receipt["receipt_sha256"],
                "SOURCE_SQLITE_RECEIPT_MISMATCH",
                "An immutable SQLite receipt resolved to different evidence.",
                status="MISMATCH",
                canonical_asset_id=receipt["canonical_asset_id"],
            )
            if existing is None:
                append_status = "APPENDED"
                connection.execute(
                    """INSERT INTO source_sqlite_receipt VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        receipt["canonical_asset_id"],
                        receipt["byte_sha256"],
                        receipt["size_bytes"],
                        receipt["inspection_mode"],
                        receipt["status"],
                        canonical_json_bytes(receipt.get("integrity", [])).decode(
                            "utf-8"
                        ).strip(),
                        receipt.get("foreign_key_error_count", 0),
                        receipt.get("user_version"),
                        receipt.get("page_count"),
                        receipt.get("page_size"),
                        receipt.get("schema_sha256"),
                        receipt.get("schema_object_count", 0),
                        receipt.get("table_count", 0),
                        receipt.get("fts_table_count", 0),
                        canonical_json_bytes(receipt).decode("utf-8").strip(),
                        receipt["receipt_sha256"],
                        utc_now(),
                    ),
                )
                canonical = canonical_by_sha[sha]
                for row in inspection["schema_objects"]:
                    connection.execute(
                        "INSERT INTO source_sqlite_schema_object VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            canonical.object_id,
                            canonical.member_path,
                            row["object_type"],
                            row["object_name"],
                            row["sql_sha256"],
                            receipt["canonical_asset_id"],
                        ),
                    )
                for row in inspection["table_stats"]:
                    connection.execute(
                        "INSERT INTO source_sqlite_table_stat VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            canonical.object_id,
                            canonical.member_path,
                            row["table_name"],
                            row["row_count"],
                            row["count_state"],
                            receipt["canonical_asset_id"],
                        ),
                    )
                for row in inspection["foreign_keys"]:
                    connection.execute(
                        "INSERT INTO source_sqlite_foreign_key VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            receipt["canonical_asset_id"],
                            row["from_table"],
                            row["foreign_key_id"],
                            row["sequence_id"],
                            row["to_table"],
                            row["from_column"],
                            row["to_column"],
                            row["on_update"],
                            row["on_delete"],
                            row["match_rule"],
                        ),
                    )
        for row in asset_rows:
            existing = connection.execute(
                """SELECT * FROM source_sqlite_asset
                WHERE object_id=? AND member_path=? AND inspection_id=?""",
                (row["object_id"], row["member_path"], row["inspection_id"]),
            ).fetchone()
            comparable = {
                key: row[key]
                for key in (
                    "inspection_state",
                    "schema_sha256",
                    "byte_sha256",
                    "size_bytes",
                    "canonical_asset_id",
                    "inspection_receipt_sha256",
                )
            }
            require(
                existing is None
                or all(existing[key] == value for key, value in comparable.items()),
                "SOURCE_SQLITE_ASSET_RECEIPT_MISMATCH",
                "An immutable SQLite occurrence receipt resolved to different evidence.",
                status="MISMATCH",
                object_id=row["object_id"],
                member_path=row["member_path"],
            )
            if existing is None:
                append_status = "APPENDED"
                connection.execute(
                    """INSERT INTO source_sqlite_asset(
                    object_id, member_path, inspection_state, schema_sha256,
                    byte_sha256, size_bytes, canonical_asset_id,
                    inspection_receipt_sha256, inspection_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["object_id"],
                        row["member_path"],
                        row["inspection_state"],
                        row["schema_sha256"],
                        row["byte_sha256"],
                        row["size_bytes"],
                        row["canonical_asset_id"],
                        row["inspection_receipt_sha256"],
                        row["inspection_id"],
                    ),
                )
        receipt_set = sorted(
            {
                str(row["receipt_sha256"])
                for row in receipt_rows.values()
            }
        )
        asset_set_sha = sha256_bytes(canonical_json_bytes(asset_rows))
        receipt_set_sha = sha256_bytes(canonical_json_bytes(receipt_set))
        event = {
            "schema": "evidence-lane.source-sqlite-intake.summary.v1",
            "batch_id": batch_id,
            "asset_occurrence_count": len(assets),
            "included_asset_count": sum(
                row.policy_state == "INCLUDED" for row in assets
            ),
            "policy_excluded_asset_count": sum(
                row.policy_state != "INCLUDED" for row in assets
            ),
            "skip_exact_counterpart_asset_count": sum(
                row["inspection_state"]
                == "SKIPPED_EXACT_EXTRACTED_COUNTERPART"
                for row in asset_rows
            ),
            "batch_worker": worker_receipt,
            "unique_byte_authority_count": len(by_sha),
            "canonical_inspection_count": len(inspections),
            "canonical_pass_count": sum(
                row["receipt"]["status"] == "PASS"
                for row in inspections.values()
            ),
            "canonical_failure_count": len(nonpass_findings),
            "asset_set_sha256": asset_set_sha,
            "receipt_set_sha256": receipt_set_sha,
            "max_embedded_member_bytes": max_embedded_member_bytes,
            "exact_count_max_database_bytes": exact_count_max_database_bytes,
            "imported_sql_executed": False,
            "source_bytes_mutated": False,
            "source_payloads_extracted_to_filesystem": archive_payloads_staged,
            "unique_source_state_count": len({asset.state_sha256 for asset in inspectable}),
            "candidate_created": False,
            "pointer_moved": False,
        }
        event_sha = sha256_bytes(canonical_json_bytes(event))
        event_id = f"registry_{event_sha[:32].lower()}"
        if connection.execute(
            "SELECT 1 FROM registry_event WHERE event_id=?", (event_id,)
        ).fetchone() is None:
            append_status = "APPENDED"
            connection.execute(
                "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    batch_id,
                    "source.sqlite.intake.completed",
                    canonical_json_bytes(event).decode("utf-8").strip(),
                    event_sha,
                    utc_now(),
                ),
            )
    return {
        "status": "PASS" if not nonpass_findings else "MISMATCH",
        "append_status": append_status,
        **event,
        "event_sha256": event_sha,
        "failure_samples": nonpass_findings[:100],
        "failure_samples_truncated": len(nonpass_findings) > 100,
    }
