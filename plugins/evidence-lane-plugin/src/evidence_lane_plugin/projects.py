"""Selected project roots and project-owned grants; no copied project databases."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .storage import LaneStore, ProjectStore, json_text, now, reject_links

PERMISSIONS = frozenset({"read", "write", "tools", "network", "publish", "approve", "admin"})

ACCESS_MIGRATIONS = (
    Migration(
        "access",
        1,
        "Project grants and revocations",
        (
            """CREATE TABLE access_grants (
        grant_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
        permissions_json TEXT NOT NULL CHECK(json_valid(permissions_json)),
        roots_json TEXT NOT NULL CHECK(json_valid(roots_json)),
        expires_at TEXT, revoked_at TEXT, created_at TEXT NOT NULL)""",
            "CREATE INDEX access_principal ON access_grants(principal_id,revoked_at)",
        ),
    ),
    Migration('access', 2, 'Exact parent binding for scoped remote connection grants', (
        'ALTER TABLE access_grants ADD COLUMN parent_grant_id TEXT REFERENCES access_grants(grant_id)',
        'CREATE INDEX access_parent ON access_grants(parent_grant_id)',
    )),
)


def atomic_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json_text(document) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class ProjectDirectory:
    """Engine-owned root locators; the project SQLite remains its data authority."""

    def __init__(self, runtime_root: Path):
        self.root = Path(os.path.abspath(runtime_root.expanduser()))
        reject_links(self.root, Path(self.root.anchor))
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "projects.json"
        self._lock = threading.RLock()

    def entries(self) -> dict[str, dict]:
        reject_links(self.path, self.root)
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > 2_000_000:
            raise LaneError(
                "PROJECT_DIRECTORY_INVALID", "The project directory exceeded its supported size."
            )
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document["version"] != 1 or not isinstance(document["projects"], dict):
                raise ValueError()
            return document["projects"]
        except (ValueError, KeyError, TypeError):
            raise LaneError(
                "PROJECT_DIRECTORY_INVALID", "The project directory cannot be read safely."
            ) from None

    def register(
        self,
        state_root: Path,
        *,
        source_root: Path | None = None,
        create: bool = False,
        read_only: bool = True,
        display_name: str | None = None,
        sensitivity: str | None = None,
        capture_route: str | None = None,
        initial_lane_ids: tuple[str, ...] | None = None,
        initializer: Callable[[ProjectStore], None] | None = None,
    ) -> dict:
        if create:
            if source_root is None or read_only:
                raise LaneError(
                    "PROJECT_CREATE_CONTRACT",
                    "Creating a project requires a source root and write access.",
                )
            store = ProjectStore.create(state_root, source_root, display_name=display_name,
                sensitivity=sensitivity if sensitivity is not None else 'PRIVATE',
                capture_route=capture_route if capture_route is not None else 'GOVERNED_PROJECT_FULL',
                initial_lane_ids=initial_lane_ids)
            if initializer is not None:
                initializer(store)
        else:
            if initializer is not None:
                raise LaneError('PROJECT_INITIALIZER_CREATE_ONLY', 'Project initialization applies only to a fresh project root.')
            store = ProjectStore(state_root, read_only=True)
            if source_root is not None and source_root.resolve() != store.source_root:
                raise LaneError('PROJECT_SOURCE_MISMATCH', 'Open the selected project with its registered source root.')
            if any(value is not None for value in (display_name, sensitivity, capture_route)):
                if not store.registration['registration_bound']:
                    raise LaneError('PROJECT_REGISTRATION_UNBOUND', 'Preserve this earlier root; registration choices require a newly created project.')
                expected = ProjectStore.registration_body(store.project_id,
                    display_name if display_name is not None else store.registration['display_name'],
                    sensitivity if sensitivity is not None else store.registration['sensitivity'],
                    capture_route if capture_route is not None else store.registration['capture_route'])
                if any(expected[key] != store.registration[key] for key in ('display_name', 'sensitivity', 'capture_route')):
                    raise LaneError('PROJECT_REGISTRATION_CONFLICT', 'This project already has different immutable registration choices.')
        record = {
            "project_id": store.project_id,
            "state_root": str(store.root),
            "source_root": str(store.source_root),
            "read_only": read_only,
            **store.registration,
        }
        with self._lock:
            entries = self.entries()
            previous = entries.get(store.project_id)
            if previous and Path(previous["state_root"]) != store.root:
                raise LaneError(
                    "PROJECT_IDENTITY_COLLISION",
                    "This project identity is already bound to another root.",
                )
            if previous and previous.get('registration_digest') is not None and previous['registration_digest'] != store.registration['registration_digest']:
                raise LaneError('PROJECT_REGISTRATION_CHANGED', 'Preserve the earlier directory binding; this project registration has changed.')
            entries[store.project_id] = record
            atomic_json(self.path, {"version": 1, "projects": entries})
        return record

    def open(self, project_id: str, *, write: bool = False) -> ProjectStore:
        record = self.entries().get(project_id)
        if record is None:
            raise LaneError("PROJECT_NOT_REGISTERED", "Select a registered project.")
        if write and record["read_only"]:
            raise LaneError("READ_ONLY_PROJECT", "The selected project is registered read-only.")
        store = ProjectStore(Path(record["state_root"]), read_only=not write)
        if store.project_id != project_id:
            raise LaneError(
                "PROJECT_BINDING_CHANGED",
                "The selected root no longer matches its registered identity.",
            )
        if record.get('registration_digest') is not None and record['registration_digest'] != store.registration['registration_digest']:
            raise LaneError('PROJECT_REGISTRATION_CHANGED', 'The selected project registration differs from its bound directory identity.')
        if str(store.source_root) != record["source_root"]:
            from .store import validate_source_locator
            validate_source_locator(store, record["source_root"])
        return store

    def record(self, project_id: str) -> dict:
        store = self.open(project_id)
        initial_source_intake = None
        try:
            with store.lane('receipts').connection(read_only=True) as connection:
                if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='receipts'").fetchone():
                    row = connection.execute(
                        "SELECT body_json FROM receipts WHERE kind='project_initial_source_intake' ORDER BY rowid DESC LIMIT 1"
                    ).fetchone()
                    initial_source_intake = json.loads(row[0]) if row else None
        except LaneError as error:
            if error.code != 'LANE_NOT_INITIALIZED':
                raise
        record = {'project_id': store.project_id, 'state_root': str(store.root),
                  'source_root': str(store.source_root),
                  'read_only': self.entries()[project_id]['read_only'], **store.registration}
        if initial_source_intake is not None:
            record['initial_source_intake'] = initial_source_intake
        return record

    def refresh_source_locator(self, project_id: str) -> None:
        """Refresh an advisory path only after validating its authority history."""
        with self._lock:
            store = self.open(project_id)
            entries = self.entries()
            entries[project_id]["source_root"] = str(store.source_root)
            atomic_json(self.path, {"version": 1, "projects": entries})


class ProjectAccess:
    def __init__(self, store: ProjectStore | LaneStore, *, clock: Callable[[], datetime] | None = None):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('receipts')
        self.clock = clock or (lambda: datetime.now(UTC))

    def initialize(self, *, writer=None) -> None:
        apply_migrations(self.store, ACCESS_MIGRATIONS, writer=writer)

    def issue(
        self,
        principal_id: str,
        permissions: Iterable[str],
        roots: Iterable[Path],
        *,
        expires_at: datetime | None = None,
        parent_grant_id: str | None = None,
        writer=None,
    ) -> str:
        selected = frozenset(permissions)
        if (
            not principal_id
            or len(principal_id) > 128
            or not selected
            or not selected <= PERMISSIONS
        ):
            raise LaneError("INVALID_GRANT", "A principal and supported permissions are required.")
        if expires_at is not None and (expires_at.tzinfo is None or expires_at <= self.clock()):
            raise LaneError(
                "INVALID_GRANT_EXPIRY", "Grant expiry must be an aware future timestamp."
            )
        resolved = []
        for path in roots:
            root = Path(os.path.abspath(path.expanduser()))
            reject_links(root, Path(root.anchor))
            if not root.is_dir():
                raise LaneError(
                    "INVALID_GRANT_ROOT", "Grant scope roots must be existing directories."
                )
            resolved.append(str(root.resolve(strict=True)))
        grant_id = str(uuid4())
        with (writer.transaction('receipts') if writer is not None else self.store.transaction()) as connection:
            if parent_grant_id is not None:
                parent = connection.execute('SELECT * FROM access_grants WHERE grant_id=?', (parent_grant_id,)).fetchone()
                if (parent is None or parent['revoked_at'] or parent['parent_grant_id'] is not None
                        or not parent['expires_at'] or expires_at is None
                        or not self.clock() < expires_at <= datetime.fromisoformat(parent['expires_at'])
                        or not selected <= set(json.loads(parent['permissions_json']))
                        or not set(resolved) <= set(json.loads(parent['roots_json']))):
                    raise LaneError('INVALID_PARENT_GRANT', 'A derived connection must remain within one current exact parent grant.')
            connection.execute(
                "INSERT INTO access_grants VALUES(?,?,?,?,?,?,?,?)",
                (
                    grant_id,
                    principal_id,
                    json_text(sorted(selected)),
                    json_text(sorted(set(resolved))),
                    expires_at.isoformat() if expires_at else None,
                    None,
                    now(),
                    parent_grant_id,
                ),
            )
            self.store.append_receipt(
                "grant_issued",
                {
                    "grant_id": grant_id,
                    "principal_id": principal_id,
                    "permissions": sorted(selected),
                    "scope_roots": sorted(set(resolved)),
                    "parent_grant_id": parent_grant_id,
                },
                connection=connection,
            )
        return grant_id

    def revoke(self, grant_id: str, *, writer=None) -> None:
        with (writer.transaction('receipts') if writer is not None else self.store.transaction()) as connection:
            result = connection.execute(
                "UPDATE access_grants SET revoked_at=? WHERE grant_id=? AND revoked_at IS NULL",
                (now(), grant_id),
            )
            if result.rowcount:
                self.store.append_receipt(
                    "grant_revoked", {"grant_id": grant_id}, connection=connection
                )

    def authorize(self, principal_id: str, permission: str, *, path: Path | None = None) -> None:
        if permission not in PERMISSIONS:
            raise LaneError("PERMISSION_DENIED", "The requested permission is not supported.")
        target = Path(os.path.abspath(path.expanduser())) if path is not None else None
        with self.store.connection(read_only=True) as connection:
            if permission != 'read':
                with self.project.lane('chat_lineage').connection(read_only=True) as lineage:
                    if lineage.execute("SELECT 1 FROM sqlite_schema WHERE name='continuation_retired_clients'").fetchone() and lineage.execute(
                        'SELECT 1 FROM continuation_retired_clients WHERE client_id=?', (principal_id,)).fetchone():
                        raise LaneError("SOURCE_CLIENT_CLOSEOUT_ONLY", "This project client transferred its work and now has read-only closeout access.")
            grants = connection.execute(
                "SELECT * FROM access_grants WHERE principal_id=? AND revoked_at IS NULL",
                (principal_id,),
            ).fetchall()
            parents = {row['grant_id']: row for row in connection.execute(
                'SELECT * FROM access_grants WHERE grant_id IN (SELECT parent_grant_id FROM access_grants WHERE principal_id=?)',
                (principal_id,))} if 'parent_grant_id' in {row[1] for row in connection.execute('PRAGMA table_info(access_grants)')} else {}
        current = self.clock()
        for grant in grants:
            if 'parent_grant_id' in grant.keys() and grant['parent_grant_id'] is not None:  # noqa: SIM118 - sqlite3.Row membership checks values, not column names
                parent = parents.get(grant['parent_grant_id'])
                if (parent is None or parent['revoked_at'] or not parent['expires_at']
                        or datetime.fromisoformat(parent['expires_at']) <= current
                        or permission not in json.loads(parent['permissions_json'])):
                    continue
            if permission not in json.loads(grant["permissions_json"]):
                continue
            if grant["expires_at"] and datetime.fromisoformat(grant["expires_at"]) <= current:
                continue
            if target is None:
                return
            for selected_root in json.loads(grant["roots_json"]):
                root = Path(selected_root)
                if target.is_relative_to(root):
                    reject_links(target, Path(root.anchor))
                    if target.resolve(strict=False).is_relative_to(root):
                        return
        raise LaneError(
            "PERMISSION_DENIED", "No current project grant covers this action and path."
        )
