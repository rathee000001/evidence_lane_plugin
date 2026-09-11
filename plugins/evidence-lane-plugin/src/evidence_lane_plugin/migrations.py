"""Versioned migrations with transactional application and table ownership."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby

from .errors import LaneError
from .lanes import LaneRegistryError, lane_for_schema_owner, owns_schema_object
from .storage import LaneStore, ProjectStore, json_text, now, reject_links


@dataclass(frozen=True)
class Migration:
    owner: str
    version: int
    description: str
    statements: tuple[str, ...]

    @property
    def digest(self) -> str:
        value = [self.owner, self.version, self.description, self.statements]
        return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def _authorizer(owner: str):
    owned_schema_change = False
    table_actions = {
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_CREATE_VTABLE,
        sqlite3.SQLITE_DROP_VTABLE,
    }
    index_actions = {
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_DROP_TRIGGER,
    }

    def authorize(action, first, second, database, trigger):
        nonlocal owned_schema_change
        internal_temp_schema_read = (
            action == sqlite3.SQLITE_READ
            and database == "temp"
            and first in {"sqlite_temp_master", "sqlite_temp_schema"}
        )
        internal_temp_schema_maintenance = (
            owned_schema_change
            and action in {sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
            and database == "temp"
            and first in {"sqlite_temp_master", "sqlite_temp_schema"}
            and trigger is None
        )
        if internal_temp_schema_read or internal_temp_schema_maintenance:
            return sqlite3.SQLITE_OK
        if database not in {None, "main"}:
            return sqlite3.SQLITE_DENY
        if action in {
            sqlite3.SQLITE_ATTACH,
            sqlite3.SQLITE_DETACH,
            sqlite3.SQLITE_PRAGMA,
            sqlite3.SQLITE_TRANSACTION,
            sqlite3.SQLITE_SAVEPOINT,
            sqlite3.SQLITE_CREATE_TRIGGER,
            sqlite3.SQLITE_DROP_TRIGGER,
        }:
            return sqlite3.SQLITE_DENY
        if action in table_actions and not owns_schema_object(owner, first or ''):
            return sqlite3.SQLITE_DENY
        owned_index = owns_schema_object(owner, first or '') and owns_schema_object(owner, second or '')
        automatic_index = (
            action == sqlite3.SQLITE_CREATE_INDEX
            and (first or "").startswith('sqlite_autoindex_' + (second or '') + '_')
            and owns_schema_object(owner, second or '')
        )
        if action in index_actions and not owned_index and not automatic_index:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_ALTER_TABLE and not owns_schema_object(owner, second or ''):
            return sqlite3.SQLITE_DENY
        if action in {
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_DROP_INDEX,
            sqlite3.SQLITE_ALTER_TABLE,
        }:
            owned_schema_change = True
        internal_schema_metadata = (
            owned_schema_change
            and action in {sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
            and first in {"sqlite_stat1", "sqlite_stat4", "sqlite_sequence"}
            and trigger is None
        )
        if (
            action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
            and first not in {"sqlite_master", "sqlite_schema"}
            and not internal_schema_metadata
            and not owns_schema_object(owner, first or '')
        ):
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and second == "load_extension":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorize


def _migration_lane(store, owner):
    if owner == 'writer':
        if isinstance(store, LaneStore):
            raise LaneError('SCHEMA_LANE_MISMATCH', 'Writer fencing belongs only to project evidence head coordinator.')
        return None
    if owner == 'views':
        if not isinstance(store, LaneStore):
            raise LaneError('EXPLICIT_LANE_REQUIRED', 'View schemas require their selected lane.')
        return store.lane_id
    if isinstance(store, LaneStore):
        from .lanes import is_named_custom_lane
        if is_named_custom_lane(store.lane_id) and owner in store.definition.schema_owners:
            return store.lane_id
    try:
        lane_id = lane_for_schema_owner(owner).canonical_lane_id
    except LaneRegistryError:
        raise LaneError('INVALID_SCHEMA_OWNER', 'Select a registered schema owner.') from None
    if isinstance(store, LaneStore) and store.lane_id != lane_id:
        raise LaneError('SCHEMA_LANE_MISMATCH', 'This schema belongs to another lane database.')
    return lane_id


def apply_migrations(store: ProjectStore | LaneStore, migrations: Sequence[Migration], *, writer=None) -> list[dict]:
    """Resolve declared owners before opening one coordinated migration commit."""
    if store.read_only:
        raise LaneError('READ_ONLY_PROJECT', 'Read-only access cannot apply migrations.')
    groups: dict[str | None, list[Migration]] = {}
    for migration in migrations:
        groups.setdefault(_migration_lane(store, migration.owner), []).append(migration)
    if not groups:
        return []
    project = store.project if isinstance(store, LaneStore) else store
    lanes = sorted(lane_id for lane_id in groups if lane_id is not None)
    if writer is not None and (writer.store.root != project.root or writer.store.project_id != project.project_id):
        raise LaneError('WRITER_PROJECT_MISMATCH', 'The migration writer belongs to another project.')
    applied = []
    with project.coordinated_transaction(lanes, writer=writer):
        for lane_id, group in groups.items():
            target = project.lane(lane_id) if lane_id is not None else project
            applied.extend(_apply_migrations(target, group))
    return applied


def _record_history(store, connection, migration):
    if not isinstance(store, LaneStore):
        return
    content = json_text({'schema': 'evidence-lane.schema-history.v4',
        'project_id': store.project_id, 'lane_id': store.lane_id,
        'owner': migration.owner, 'version': migration.version,
        'migration_digest': migration.digest, 'description': migration.description,
        'statements': migration.statements}).encode()
    digest = hashlib.sha256(content).hexdigest()
    filename = f'{migration.owner}.{migration.version}.{digest}.json'
    path = store.schema_history / filename
    reject_links(path, store.root)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if path.read_bytes() != content:
            raise LaneError('SCHEMA_HISTORY_INTEGRITY', 'The immutable migration file changed.') from None
    else:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    prior = connection.execute('SELECT filename,digest FROM schema_history_files WHERE owner=? AND version=?',
                               (migration.owner, migration.version)).fetchone()
    if prior is not None and tuple(prior) != (filename, digest):
        raise LaneError('SCHEMA_HISTORY_INTEGRITY', 'The migration file reference differs from its schema.')
    connection.execute('INSERT OR IGNORE INTO schema_history_files VALUES(?,?,?,?)',
                       (migration.owner, migration.version, filename, digest))


def verify_schema_history_files(store, connection):
    if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='schema_history_files'").fetchone():
        return
    for row in connection.execute('SELECT * FROM schema_history_files'):
        if (not re.fullmatch(r'[a-z][a-z0-9]{0,31}', row['owner']) or row['version'] < 1
                or not re.fullmatch(r'[0-9a-f]{64}', row['digest'])
                or row['filename'] != f"{row['owner']}.{row['version']}.{row['digest']}.json"):
            raise LaneError('SCHEMA_HISTORY_INTEGRITY', 'The migration file reference is invalid.')
        path = store.schema_history / row['filename']
        reject_links(path, store.root)
        if not path.is_file() or path.stat().st_size > 4_194_304:
            raise LaneError('SCHEMA_HISTORY_INTEGRITY', 'The registered migration file is missing or too large.')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['digest']:
            raise LaneError('SCHEMA_HISTORY_INTEGRITY', 'The registered migration file changed.')


def _apply_migrations(store, migrations: Sequence[Migration]) -> list[dict]:
    groups: dict[str, list[Migration]] = {}
    for owner, entries in groupby(
        sorted(migrations, key=lambda m: (m.owner, m.version)), lambda m: m.owner
    ):
        group = list(entries)
        if not re.fullmatch(r"[a-z][a-z0-9]{0,31}", owner) or owner in {"sqlite", "schema"}:
            raise LaneError(
                "INVALID_SCHEMA_OWNER", "A canonical profile/authority owner is required."
            )
        if [m.version for m in group] != list(range(1, len(group) + 1)):
            raise LaneError(
                "MIGRATION_SEQUENCE_INVALID",
                "Provide every owner migration in order from version one.",
            )
        if any(not m.description.strip() or not m.statements for m in group):
            raise LaneError(
                "INVALID_MIGRATION", "Every migration needs a description and statements."
            )
        groups[owner] = group
    applied = []
    with store.transaction() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            owner TEXT NOT NULL, version INTEGER NOT NULL, digest TEXT NOT NULL,
            description TEXT NOT NULL, applied_at TEXT NOT NULL,
            PRIMARY KEY(owner,version))""")
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_ownership (
            object_name TEXT PRIMARY KEY, owner TEXT NOT NULL, object_type TEXT NOT NULL)""")
        if isinstance(store, LaneStore):
            connection.execute('''CREATE TABLE IF NOT EXISTS schema_history_files (
                owner TEXT NOT NULL, version INTEGER NOT NULL, filename TEXT NOT NULL,
                digest TEXT NOT NULL, PRIMARY KEY(owner,version),
                FOREIGN KEY(owner,version) REFERENCES schema_migrations(owner,version))''')
        for owner, group in groups.items():
            history = {
                r["version"]: r["digest"]
                for r in connection.execute(
                    "SELECT version,digest FROM schema_migrations WHERE owner=?", (owner,)
                )
            }
            if history and max(history) > len(group):
                raise LaneError(
                    "SCHEMA_NEWER_THAN_ENGINE",
                    "An installed owner schema is newer than this engine.",
                )
            for migration in group:
                if migration.version in history:
                    if history[migration.version] != migration.digest:
                        raise LaneError(
                            "MIGRATION_DRIFT",
                            "An applied migration differs from the supplied version.",
                        )
                    _record_history(store, connection, migration)
                    continue
                before_objects = {
                    r[0] for r in connection.execute("SELECT name FROM sqlite_schema")
                }
                try:
                    for statement_index, statement in enumerate(migration.statements, start=1):
                        # A fresh callback scopes SQLite's internal sqlite_stat*
                        # cleanup permission to one already-authorized DROP.
                        connection.set_authorizer(_authorizer(owner))
                        connection.execute(statement)
                except sqlite3.DatabaseError as error:
                    raise LaneError(
                        "MIGRATION_FAILED",
                        "The migration failed; no schema changes were committed.",
                        details={
                            "owner": owner,
                            "version": migration.version,
                            "statement_index": statement_index,
                            "sqlite_error_code": getattr(error, "sqlite_errorcode", None),
                            "sqlite_error_name": getattr(error, "sqlite_errorname", None),
                        },
                    ) from None
                finally:
                    connection.set_authorizer(None)
                after_objects = {r[0] for r in connection.execute("SELECT name FROM sqlite_schema")}
                unexpected = {
                    name
                    for name in after_objects - before_objects
                    if not owns_schema_object(owner, name)
                    and not (name.startswith('sqlite_autoindex_') and owns_schema_object(owner, name[len('sqlite_autoindex_'):]))
                }
                if unexpected:
                    raise LaneError(
                        "SCHEMA_OWNER_CONFLICT", "The migration created objects outside its owner."
                    )
                connection.execute(
                    "DELETE FROM schema_ownership WHERE owner=? AND object_name NOT IN "
                    "(SELECT name FROM sqlite_schema)",
                    (owner,),
                )
                for item in connection.execute(
                    "SELECT name,type FROM sqlite_schema"
                ).fetchall():
                    if not owns_schema_object(owner, item['name']):
                        continue
                    existing = connection.execute(
                        "SELECT owner FROM schema_ownership WHERE object_name=?", (item["name"],)
                    ).fetchone()
                    if existing and existing["owner"] != owner:
                        raise LaneError(
                            "SCHEMA_OWNER_CONFLICT", "A schema object belongs to another owner."
                        )
                    connection.execute(
                        "INSERT OR IGNORE INTO schema_ownership VALUES(?,?,?)",
                        (item["name"], owner, item["type"]),
                    )
                connection.execute(
                    "INSERT INTO schema_migrations VALUES(?,?,?,?,?)",
                    (owner, migration.version, migration.digest, migration.description, now()),
                )
                _record_history(store, connection, migration)
                applied.append(
                    {"owner": owner, "version": migration.version, "digest": migration.digest}
                )
        if applied:
            store.append_receipt("schema_migration", {"applied": applied}, connection=connection)
    return applied


def read_compatibility(store: ProjectStore | LaneStore, migrations: Sequence[Migration]) -> list[dict]:
    groups: dict[str | None, list[Migration]] = {}
    for migration in migrations:
        groups.setdefault(_migration_lane(store, migration.owner), []).append(migration)
    project = store.project if isinstance(store, LaneStore) else store
    result: list[dict] = []
    # The owning root connection pins a published snapshot for ordinary reads,
    # or the exact coordinator connection for an internal staged-lane check.
    with project.connection(read_only=True):
        for lane_id, group in groups.items():
            try:
                target = project.lane(lane_id) if lane_id is not None else project
            except LaneError as error:
                if error.code != 'LANE_NOT_INITIALIZED':
                    raise
                result.extend({'owner': owner, 'status': 'not_initialized', 'migrations': []}
                              for owner in sorted({item.owner for item in group}))
                continue
            result.extend(_read_compatibility(target, group))
    return result


def _read_compatibility(store, migrations: Sequence[Migration]) -> list[dict]:
    """Validate only requested owners in place; never initialize or upgrade a target."""
    owners: dict[str, list[Migration]] = {}
    for migration in migrations:
        owners.setdefault(migration.owner, []).append(migration)
    result: list[dict] = []
    with store.connection(read_only=True) as connection:
        if not connection.in_transaction:
            connection.execute("BEGIN")
        if isinstance(store, LaneStore):
            verify_schema_history_files(store, connection)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        for owner, group in sorted(owners.items()):
            group = sorted(group, key=lambda item: item.version)
            if [item.version for item in group] != list(range(1, len(group) + 1)):
                raise LaneError("QUERY_SCHEMA_CONTRACT", "A query must declare the complete owner migration history.")
            objects = {row[0]: row[1] for row in connection.execute(
                "SELECT name,type FROM sqlite_schema") if owns_schema_object(owner, row[0])}
            history = [dict(row) for row in connection.execute(
                "SELECT version,digest FROM schema_migrations WHERE owner=? ORDER BY version", (owner,))] if "schema_migrations" in tables else []
            expected = [{"version": item.version, "digest": item.digest} for item in group]
            if not history and not objects:
                result.append({"owner": owner, "status": "not_initialized", "migrations": []})
                continue
            if not history or "schema_ownership" not in tables:
                raise LaneError("QUERY_SCHEMA_METADATA_MISSING", "The selected owner lacks version or ownership evidence.")
            if history[-1]["version"] > len(group):
                raise LaneError("SCHEMA_NEWER_THAN_ENGINE", "The selected owner requires a newer compatible engine.")
            if history != expected:
                raise LaneError("QUERY_SCHEMA_INCOMPATIBLE", "The owner migration history differs; query cannot migrate it.")
            if isinstance(store, LaneStore):
                recorded = ([row[0] for row in connection.execute(
                    'SELECT version FROM schema_history_files WHERE owner=? ORDER BY version', (owner,))]
                    if 'schema_history_files' in tables else [])
                if recorded != [item.version for item in group]:
                    raise LaneError('QUERY_SCHEMA_HISTORY_MISSING', 'The owner lacks its complete lane-owned migration files.')
            ownership = {row[0]: row[1] for row in connection.execute(
                "SELECT object_name,object_type FROM schema_ownership WHERE owner=?", (owner,))}
            if objects != ownership or not objects:
                raise LaneError("QUERY_SCHEMA_OWNERSHIP", "The queried owner objects differ from their schema ownership records.")
            result.append({"owner": owner, "status": "compatible", "migrations": history})
    return result
