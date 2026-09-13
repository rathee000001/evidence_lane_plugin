"""Explicit offline migration of a hash-pinned v4 prototype into a fresh root.

Historical v3 lane databases are not accepted here. Unmapped tables, ambiguous
object ownership, active writers, triggers and views fail before target creation.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from .errors import LaneError
from .lanes import SCHEMA_OWNER_LANES, get_lane
from .migrations import Migration, _record_history, read_compatibility
from .storage import (
    APPLICATION_ID,
    LEGACY_DATABASE_NAME,
    MAX_OBJECT_BYTES,
    ProjectStore,
    json_text,
    now,
    reject_links,
)

MAX_DATABASE_BYTES = 256 * 1024 * 1024
MARKER = '.lane-migration.json'


def _digest(path: Path, limit: int = MAX_DATABASE_BYTES) -> str:
    digest = hashlib.sha256()
    remaining = limit
    with path.open('rb') as stream:
        while chunk := stream.read(min(1024 * 1024, remaining + 1)):
            remaining -= len(chunk)
            if remaining < 0:
                raise LaneError('MIGRATION_SOURCE_TOO_LARGE', 'A selected source exceeds its migration budget.')
            digest.update(chunk)
    return digest.hexdigest()


def _quoted(name: str) -> str:
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        raise LaneError('MIGRATION_SCHEMA_UNSUPPORTED', 'Migration requires canonical schema object names.')
    return '"' + name + '"'


def migrate_shared_project(source_root: Path, target_root: Path, *, expected_source_sha256: str,
                           object_lanes: dict[str, tuple[str, ...]],
                           table_lanes: dict[str, str] | None = None,
                           migration_definitions: Sequence[Migration] = ()) -> dict:
    """Preserve source bytes; copy only explicitly attributed data into separate lanes."""
    source_root = Path(os.path.abspath(source_root.expanduser()))
    reject_links(source_root, Path(source_root.anchor))
    source_root = source_root.resolve(strict=True)
    database = source_root / LEGACY_DATABASE_NAME
    reject_links(database, source_root)
    if not database.is_file() or database.stat().st_size > MAX_DATABASE_BYTES:
        raise LaneError('MIGRATION_SOURCE_INVALID', 'Select a bounded, existing v4 prototype database.')
    if not re.fullmatch(r'[0-9a-f]{64}', expected_source_sha256) or _digest(database) != expected_source_sha256:
        raise LaneError('MIGRATION_SOURCE_HASH_MISMATCH', 'The source database differs from the explicitly selected hash.')
    for suffix in ('-wal', '-journal'):
        sidecar = database.with_name(database.name + suffix)
        reject_links(sidecar, source_root)
        if sidecar.exists() and sidecar.stat().st_size:
            raise LaneError('MIGRATION_SOURCE_NOT_QUIESCENT', 'Checkpoint and close the prototype before offline migration.')
    target_root = Path(os.path.abspath(target_root.expanduser()))
    if target_root.is_relative_to(source_root) or source_root.is_relative_to(target_root):
        raise LaneError('MIGRATION_TARGET_INVALID', 'Use a fresh root separate from the prototype state.')
    source = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute('PRAGMA query_only=ON')
        source.execute('PRAGMA trusted_schema=OFF')
        source.execute('BEGIN')
        if source.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise LaneError('MIGRATION_SOURCE_INVALID', 'The source database failed its integrity check.')
        if source.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            raise LaneError('MIGRATION_SOURCE_INVALID', 'This route only accepts the v4 shared-database prototype.')
        metadata_row = source.execute('SELECT * FROM project WHERE singleton=1').fetchone()
        metadata = dict(metadata_row) if metadata_row else None
        if metadata is None or metadata['format_version'] != 4 or 'storage_layout' in metadata:
            raise LaneError('MIGRATION_SOURCE_INVALID', 'This route only accepts the v4 shared-database prototype.')
        objects = {row['digest']: dict(row) for row in source.execute('SELECT * FROM objects')}
        if set(objects) != set(object_lanes) or any(not v for v in object_lanes.values()):
            raise LaneError('MIGRATION_OBJECT_OWNERSHIP_REQUIRED', 'Explicitly assign every registered object to its consuming lanes.')
        selected_objects = {key: tuple(sorted({get_lane(lane).canonical_lane_id for lane in values}))
                            for key, values in object_lanes.items()}
        for digest, row in objects.items():
            if not re.fullmatch(r'[0-9a-f]{64}', digest) or not 0 <= row['size_bytes'] <= MAX_OBJECT_BYTES:
                raise LaneError('MIGRATION_OBJECT_INVALID', 'A source object exceeds its contract.')
            path = source_root / 'objects' / digest[:2] / digest[2:]
            reject_links(path, source_root)
            if not path.is_file() or path.stat().st_size != row['size_bytes'] or _digest(path, MAX_OBJECT_BYTES) != digest:
                raise LaneError('MIGRATION_OBJECT_INVALID', 'A source object differs from its registered hash or size.')
        schema = [dict(row) for row in source.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")]
        if any(row['type'] in {'view', 'trigger'} for row in schema):
            raise LaneError('MIGRATION_SCHEMA_UNSUPPORTED', 'Views and triggers require an explicit owning-workflow migration.')
        shadows = {row['name'] for row in source.execute('PRAGMA table_list') if row['type'] == 'shadow'}
        tables = {row['name']: row for row in schema if row['type'] == 'table' and row['name'] not in shadows}
        ownership = {row['object_name']: row['owner'] for row in source.execute('SELECT * FROM schema_ownership')} if 'schema_ownership' in tables else {}
        overrides = table_lanes or {}
        if set(overrides) - set(tables):
            raise LaneError('MIGRATION_TABLE_OWNERSHIP_INVALID', 'A table ownership override names an absent table.')
        assignments = {}
        skip = {'project', 'objects', 'schema_migrations', 'schema_ownership'}
        for name in sorted(set(tables) - skip):
            _quoted(name)
            owner = ownership.get(name)
            if name == 'receipts':
                lane_id = 'receipts'
            elif owner == 'writer':
                if name != 'writer_lease' or source.execute('SELECT 1 FROM writer_lease WHERE owner_id IS NOT NULL LIMIT 1').fetchone():
                    raise LaneError('MIGRATION_SOURCE_NOT_QUIESCENT', 'Release the prototype writer before migration.')
                lane_id = '_root'
            elif owner in SCHEMA_OWNER_LANES:
                lane_id = SCHEMA_OWNER_LANES[owner]
            elif name in overrides:
                lane_id = get_lane(overrides[name]).canonical_lane_id
            else:
                raise LaneError('MIGRATION_TABLE_OWNERSHIP_REQUIRED', f'Assign an explicit lane to table {name!r}.')
            if name in overrides and get_lane(overrides[name]).canonical_lane_id != lane_id:
                raise LaneError('MIGRATION_TABLE_OWNERSHIP_INVALID', 'An override cannot replace registered schema ownership.')
            sql = tables[name]['sql'] or ''
            if not re.match(r'CREATE\s+(?:VIRTUAL\s+)?TABLE\s', sql, re.IGNORECASE):
                raise LaneError('MIGRATION_SCHEMA_UNSUPPORTED', 'Unsupported schema builder.')
            if re.match(r'CREATE\s+VIRTUAL', sql, re.IGNORECASE) and not re.search(r'\bUSING\s+fts5\s*\(', sql, re.IGNORECASE):
                raise LaneError('MIGRATION_SCHEMA_UNSUPPORTED', 'Only FTS5 virtual tables are accepted in this migration.')
            assignments[name] = lane_id
        for name, lane_id in assignments.items():
            for fk in source.execute(f'PRAGMA foreign_key_list({_quoted(name)})'):
                if fk['table'] != 'objects' and assignments.get(fk['table']) != lane_id:
                    raise LaneError('MIGRATION_CROSS_LANE_FOREIGN_KEY', 'Cross-lane foreign keys require a workflow-specific reference migration.')
        owner_targets = {owner: ('_root' if owner == 'writer' else SCHEMA_OWNER_LANES.get(owner)) for owner in set(ownership.values())}
        if any(value is None for value in owner_targets.values()):
            raise LaneError('MIGRATION_SCHEMA_UNSUPPORTED', 'A shared schema mechanism needs a workflow-specific migration.')
        definitions = _admit_history(source, tables, owner_targets, migration_definitions)
        copy_fields = _admit_copy_fields(source, assignments)
        return _copy(source, source_root, target_root, database, metadata, objects, selected_objects,
                     tables, schema, assignments, ownership, owner_targets, expected_source_sha256, definitions, copy_fields)
    except sqlite3.DatabaseError:
        raise LaneError('MIGRATION_SOURCE_INVALID', 'The source schema cannot be read as a supported v4 prototype.') from None
    finally:
        source.close()


def _admit_history(source, tables, owner_targets, migrations):
    history = [dict(row) for row in source.execute('SELECT * FROM schema_migrations ORDER BY owner,version')] if 'schema_migrations' in tables else []
    selected = {}
    for migration in migrations:
        if (not isinstance(migration, Migration) or type(migration.version) is not int or migration.version < 1
                or not migration.description or not migration.statements
                or any(not isinstance(statement, str) or not statement.strip() for statement in migration.statements)
                or (migration.owner, migration.version) in selected):
            raise LaneError('MIGRATION_HISTORY_INVALID', 'Provide distinct owning migration definitions.')
        selected[(migration.owner, migration.version)] = migration
    if len(history) != len(selected) or set(selected) != {(row['owner'], row['version']) for row in history}:
        raise LaneError('MIGRATION_HISTORY_REQUIRED', 'Provide the exact complete migration definitions for every recorded source schema before creating a target.')
    versions = defaultdict(list)
    for row in history:
        migration = selected[(row['owner'], row['version'])]
        if (row['owner'] not in owner_targets or row['digest'] != migration.digest
                or row['description'] != migration.description or type(row['version']) is not int or row['version'] < 1):
            raise LaneError('MIGRATION_HISTORY_INVALID', 'A supplied definition differs from the hash-pinned source history.')
        versions[row['owner']].append(row['version'])
    if any(value != list(range(1, len(value) + 1)) for value in versions.values()):
        raise LaneError('MIGRATION_HISTORY_INVALID', 'Provide a contiguous complete history for each owning schema.')
    return selected


def _admit_copy_fields(source, assignments):
    table_modes = {row['name']: row['wr'] for row in source.execute('PRAGMA table_list')}
    result = {}
    for name in assignments:
        columns = [row['name'] for row in source.execute(f'PRAGMA table_info({_quoted(name)})')]
        fields = [_quoted(column) for column in columns]
        if not table_modes[name]:
            # FTS and ordinary rowid tables can have gaps or explicit negative
            # identities. Re-insertion must not renumber their existing locators.
            aliases = [alias for alias in ('rowid', 'oid', '_rowid_') if alias not in {column.lower() for column in columns}]
            if not aliases:
                raise LaneError('MIGRATION_ROW_IDENTITY_UNAVAILABLE', 'All implicit row identity aliases are shadowed; use an owning-workflow migration.')
            fields.insert(0, '"' + aliases[0] + '"')
        result[name] = fields
    return result


def _copy(source, source_root, target_root, database, metadata, objects, object_lanes,
          tables, schema, assignments, ownership, owner_targets, expected_digest, definitions, copy_fields):
    project = ProjectStore.create(target_root, Path(metadata['source_root']), project_id=metadata['project_id'])
    marker = project.root / MARKER
    report = {'schema': 'evidence-lane.separate-lane-migration.v4', 'status': 'incomplete',
              'source_database_sha256': expected_digest, 'project_id': project.project_id,
              'native_session_trust_transferred': False, 'root_publication_pending': True,
              'schema_compatibility_scope': 'exact supplied source definitions; current business workflow compatibility is separate',
              'table_counts': {}, 'object_counts': {}, 'created_at': now()}
    marker.write_text(json_text(report), encoding='utf-8')
    try:
        lane_ids = sorted((set(assignments.values()) | {lane for values in object_lanes.values() for lane in values}) - {'_root'})
        stores = {lane: project.lane(lane, create=True) for lane in lane_ids}
        stores['_root'] = project
        for digest, lanes in object_lanes.items():
            path = source_root / 'objects' / digest[:2] / digest[2:]
            reject_links(path, source_root)
            with path.open('rb') as stream:
                content = stream.read(MAX_OBJECT_BYTES + 1)
            if len(content) != objects[digest]['size_bytes'] or hashlib.sha256(content).hexdigest() != digest:
                raise LaneError('MIGRATION_SOURCE_CHANGED', 'A selected source object changed during migration.')
            for lane in lanes:
                if stores[lane].put_object(content) != digest:
                    raise LaneError('MIGRATION_OBJECT_INVALID', 'Target object verification failed.')
                report['object_counts'][lane] = report['object_counts'].get(lane, 0) + 1
        grouped = defaultdict(list)
        for name, lane in assignments.items():
            grouped[lane].append(name)
        for lane, names in sorted(grouped.items()):
            with stores[lane].transaction() as connection:
                connection.execute('PRAGMA defer_foreign_keys=ON')
                for name in names:
                    existing = connection.execute('SELECT name FROM sqlite_schema WHERE name=?', (name,)).fetchone()
                    if not existing:
                        connection.execute(tables[name]['sql'])
                for name in names:
                    fields = ','.join(copy_fields[name])
                    query = f'INSERT INTO {_quoted(name)}({fields}) VALUES({",".join("?" for _ in copy_fields[name])})'
                    cursor = source.execute(f'SELECT {fields} FROM {_quoted(name)}')
                    count = 0
                    while rows := cursor.fetchmany(500):
                        connection.executemany(query, [tuple(row) for row in rows])
                        count += len(rows)
                    report['table_counts'][name] = {'lane': lane, 'rows': count}
                for item in schema:
                    if (item['type'] == 'index' and item['sql'] and item['tbl_name'] in names
                            and not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (item['name'],)).fetchone()):
                        connection.execute(item['sql'])
                if lane == '_root':
                    connection.execute('CREATE TABLE IF NOT EXISTS schema_migrations(owner TEXT,version INTEGER,digest TEXT,description TEXT,applied_at TEXT,PRIMARY KEY(owner,version))')
                    connection.execute('CREATE TABLE IF NOT EXISTS schema_ownership(object_name TEXT PRIMARY KEY,owner TEXT,object_type TEXT)')
                else:
                    connection.execute('''CREATE TABLE IF NOT EXISTS schema_history_files (
                        owner TEXT NOT NULL, version INTEGER NOT NULL, filename TEXT NOT NULL,
                        digest TEXT NOT NULL, document_json TEXT NOT NULL CHECK(json_valid(document_json)),
                        PRIMARY KEY(owner,version),
                        FOREIGN KEY(owner,version) REFERENCES schema_migrations(owner,version))''')
                for owner, selected in owner_targets.items():
                    if selected == lane and 'schema_migrations' in tables:
                        history = source.execute('SELECT * FROM schema_migrations WHERE owner=?', (owner,)).fetchall()
                        connection.executemany('INSERT INTO schema_migrations VALUES(?,?,?,?,?)', [tuple(row) for row in history])
                        owned = source.execute('SELECT * FROM schema_ownership WHERE owner=?', (owner,)).fetchall()
                        connection.executemany('INSERT INTO schema_ownership VALUES(?,?,?)', [tuple(row) for row in owned])
                        for row in history:
                            _record_history(stores[lane], connection, definitions[(owner, row['version'])])
                if connection.execute('PRAGMA foreign_key_check').fetchone():
                    raise LaneError('MIGRATION_REFERENCE_INVALID', 'A copied record lacks its lane-owned reference.')
        if _digest(database) != expected_digest:
            raise LaneError('MIGRATION_SOURCE_CHANGED', 'The source database changed during migration.')
        for suffix in ('-wal', '-journal'):
            sidecar = database.with_name(database.name + suffix)
            reject_links(sidecar, source_root)
            if sidecar.exists() and sidecar.stat().st_size:
                raise LaneError('MIGRATION_SOURCE_CHANGED', 'The source resumed writing during offline migration.')
        for digest in objects:
            path = source_root / 'objects' / digest[:2] / digest[2:]
            reject_links(path, source_root)
            if _digest(path, MAX_OBJECT_BYTES) != digest:
                raise LaneError('MIGRATION_SOURCE_CHANGED', 'A source object changed during migration.')
        report['schema_compatibility'] = read_compatibility(project, tuple(definitions.values()))
        if any(row['status'] != 'compatible' for row in report['schema_compatibility']):
            raise LaneError('MIGRATION_TARGET_HISTORY_INVALID', 'Every imported owner must have compatible registered history.')
        with project.connection(read_only=True) as connection:
            for lane_id in lane_ids:
                project.lane(lane_id)._verify_published_head(connection)
        report['status'] = 'complete'
        report['root_publication_pending'] = False
        report['copied_data_root'] = project.pv_head()
        report['completed_at'] = now()
        report['publication_receipt_id'] = project.append_receipt('separate_lane_migration', report)
        report['published_root'] = project.pv_head()
        marker.write_text(json_text(report), encoding='utf-8')
        return report
    except BaseException as error:
        report['status'] = 'failed'
        marker.write_text(json_text(report), encoding='utf-8')
        if isinstance(error, sqlite3.DatabaseError):
            raise LaneError('MIGRATION_TARGET_COPY_FAILED', 'A target schema copy failed; its incomplete root is preserved.') from error
        raise
