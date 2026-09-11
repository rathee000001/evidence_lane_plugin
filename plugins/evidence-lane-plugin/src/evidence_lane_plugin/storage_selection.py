"""Primary storage policy, its Receipts ledger and attributed runtime evidence.

Selection never copies state or changes a transport. Boot/Resume must satisfy
the saved policy on the connected backend. Disk lifetime remains an explicit
operator declaration; the remote restart probe measures actual recovery.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .plan_runtime import content_digest
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import json_text, now, project_snapshot, reject_links

DIGEST = r'^[0-9a-f]{64}$'
STORAGE_HISTORY_MAX_BYTES = 4_194_304
StorageMode = Literal['AUTO', 'LOCAL_SQLITE', 'CONFIGURED_DURABLE_CONNECTOR']
StorageClass = Literal['persistent_operator_declared', 'ephemeral', 'unverified']


class LocalStorageDeclaration(Contract):
    state_root: str = Field(min_length=1, max_length=1024)
    volume_id: str = Field(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9._-]+$')
    storage_class: StorageClass

    @field_validator('state_root')
    @classmethod
    def absolute_root(cls, value):
        path = Path(value)
        if not path.is_absolute() or '..' in path.parts or any(c in value for c in '\r\n\x00'):
            raise ValueError('Declare an absolute state root without traversal')
        return str(path)


class LocalStoragePolicy(Contract):
    format_version: Literal[1] = 1
    declarations: list[LocalStorageDeclaration] = Field(default_factory=list, max_length=32)

    @model_validator(mode='after')
    def disjoint_roots(self):
        roots = [Path(row.state_root) for row in self.declarations]
        if any(a.is_relative_to(b) or b.is_relative_to(a)
               for i, a in enumerate(roots) for b in roots[i + 1:]):
            raise ValueError('Declare disjoint state roots so each project has one storage policy')
        return self

    @classmethod
    def load(cls, runtime_root):
        """Owner configuration read once at engine construction, never tool input."""
        path = runtime_root / 'storage-policy.json'
        reject_links(path, runtime_root)
        if not path.exists():
            return cls()
        if not path.is_file() or path.stat().st_size > 65536:
            raise LaneError('STORAGE_POLICY_INVALID', 'The owner storage policy exceeds its file budget.')
        try:
            return cls.model_validate_json(path.read_bytes())
        except ValueError:
            raise LaneError('STORAGE_POLICY_INVALID', 'The owner storage policy is invalid.') from None


class StorageBackend(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    engine_instance_id: str = Field(pattern=UUID_PATTERN)
    connection: Literal['local_api', 'remote_api', 'unavailable']
    evidence_basis: str
    storage_class: StorageClass = 'unverified'
    volume_id: str | None = None
    policy_digest: str | None = Field(default=None, pattern=DIGEST)
    connector_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    restart_recovery_verified: bool = False
    restart_verified_at: str | None = None
    observed_at: str
    physical_durability_attested: Literal[False] = False


def local_storage_evidence(engine, project_id):
    project = engine.directory.open(project_id)
    root = project.root.resolve()
    selected = None
    for declaration in engine.local_storage_policy.declarations:
        path = Path(declaration.state_root)
        reject_links(path, Path(path.anchor))
        if root.is_relative_to(path.resolve()):
            selected = declaration
            break
    return StorageBackend(project_id=project_id, engine_instance_id=engine.instance_id,
        connection='local_api', evidence_basis='engine_owner_configuration_at_startup',
        storage_class=selected.storage_class if selected else 'unverified',
        volume_id=selected.volume_id if selected else None,
        policy_digest=content_digest(engine.local_storage_policy.model_dump(mode='json')), observed_at=now())


class StorageInspect(Contract):
    history_limit: int = Field(default=4096, ge=1, le=4096)


class StorageSelect(Contract):
    mode: StorageMode
    connector_id: str | None = Field(default=None, max_length=128, pattern=r'^[A-Za-z0-9._-]+$')
    expected_event_digest: str | None = Field(default=None, pattern=DIGEST)
    reason: str = Field(min_length=1, max_length=1000)
    confirmation: str = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def exact_selection(self):
        if (self.mode == 'CONFIGURED_DURABLE_CONNECTOR') != (self.connector_id is not None):
            raise ValueError('Only CONFIGURED_DURABLE_CONNECTOR requires a connector ID')
        if not self.reason.strip():
            raise ValueError('Supply a storage selection reason')
        return self


class StorageSelectionResult(Contract):
    project_id: str
    mode: StorageMode = 'AUTO'
    connector_id: str | None = None
    selection_recorded: bool = False
    event_digest: str | None = None
    history_count: int = 0
    selected_by: str | None = None
    selected_at: str | None = None
    reason: str | None = None
    backend: StorageBackend
    policy_satisfied: bool
    availability_reason: str
    observation_scope: Literal['current_read', 'at_selection_commit'] = 'current_read'
    state_authority: Literal['receipts_lane_sqlite'] = 'receipts_lane_sqlite'
    project_migrated: Literal[False] = False
    connection_redirected: Literal[False] = False
    blob_mirror_primary_allowed: Literal[False] = False


STORAGE_MIGRATIONS = (Migration('storage', 1, 'Hash-linked primary storage selections in Receipts', (
    '''CREATE TABLE storage_selection_events (sequence INTEGER PRIMARY KEY,
       request_id TEXT NOT NULL UNIQUE, client_id TEXT NOT NULL, input_digest TEXT NOT NULL,
       previous_digest TEXT, body_json TEXT NOT NULL CHECK(json_valid(body_json)),
       digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)''',
)),)


class StorageSelection:
    def __init__(self, engine, project):
        self.engine, self.project = engine, project
        self.store = project.lane('receipts')

    def _history(self, connection, limit=4096):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='storage_selection_events'").fetchone():
            return []
        count, size = connection.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(body_json AS BLOB))),0) FROM storage_selection_events').fetchone()
        if count > limit or size > STORAGE_HISTORY_MAX_BYTES:
            raise LaneError('STORAGE_HISTORY_BUDGET', 'Storage history exceeds the bounded verification budget.')
        rows = connection.execute('SELECT * FROM storage_selection_events ORDER BY sequence LIMIT ?', (limit,)).fetchall()
        previous = None
        verified = []
        for index, row in enumerate(rows, 1):
            event = dict(row)
            digest = event.pop('digest')
            if (event['sequence'] != index or event['previous_digest'] != previous
                    or content_digest(event) != digest):
                raise LaneError('STORAGE_HISTORY_INTEGRITY', 'The storage selection chain changed.')
            try:
                body = json.loads(event['body_json'])
                parsed = StorageSelectionResult.model_validate(body)
            except ValueError:
                raise LaneError('STORAGE_HISTORY_INTEGRITY', 'The storage event violates its result contract.') from None
            if (parsed.project_id != self.project.project_id or parsed.event_digest is not None or parsed.history_count != index
                    or parsed.selected_by != event['client_id'] or parsed.selected_at != event['created_at']
                    or not parsed.selection_recorded or parsed.observation_scope != 'at_selection_commit'):
                raise LaneError('STORAGE_HISTORY_INTEGRITY', 'The storage event attribution changed.')
            verified.append((row, parsed.model_copy(update={'event_digest': digest})))
            previous = digest
        return verified

    def _backend(self, context):
        if context.storage_observer is None:
            return StorageBackend(project_id=self.project.project_id, engine_instance_id=self.engine.instance_id,
                connection='unavailable', evidence_basis='no_authenticated_backend_observer', observed_at=now())
        backend = StorageBackend.model_validate(context.storage_observer(self.project.project_id))
        if (backend.project_id, backend.engine_instance_id) != (self.project.project_id, self.engine.instance_id):
            raise LaneError('STORAGE_BACKEND_BINDING_CHANGED', 'The storage observation belongs to another engine or project.')
        return backend

    @staticmethod
    def _availability(mode, connector_id, backend):
        if backend.connection == 'unavailable':
            return False, 'authenticated_backend_unavailable'
        if mode == 'LOCAL_SQLITE' and backend.connection != 'local_api':
            return False, 'selected_local_backend_not_connected'
        if mode == 'CONFIGURED_DURABLE_CONNECTOR' and (
                backend.connection != 'remote_api' or backend.connector_id != connector_id):
            return False, 'selected_connector_not_connected'
        if backend.storage_class != 'persistent_operator_declared':
            return False, 'persistent_storage_not_declared'
        if backend.connection == 'remote_api' and not backend.restart_recovery_verified:
            return False, 'remote_restart_recovery_unverified'
        return True, ('persistent_local_operator_declaration' if backend.connection == 'local_api'
                      else 'authenticated_remote_restart_recovery')

    def inspect(self, context, request):
        with project_snapshot(self.project.root), self.store.connection(read_only=True) as connection:
            history = self._history(connection, request.history_limit)
            values = history[-1][1].model_dump(mode='json') if history else {'project_id': self.project.project_id}
            backend = self._backend(context)
            satisfied, reason = self._availability(values.get('mode', 'AUTO'), values.get('connector_id'), backend)
            values.update(backend=backend, policy_satisfied=satisfied, availability_reason=reason, observation_scope='current_read')
            if context.authorize:
                context.authorize('read')
            return StorageSelectionResult(**values)

    def require_current(self, context):
        result = self.inspect(context, StorageInspect())
        if not result.policy_satisfied:
            raise LaneError('STORAGE_ROUTE_UNAVAILABLE', 'Boot/Resume requires the selected policy on the connected persistent backend.',
                            details={'reason': result.availability_reason, 'mode': result.mode})
        return result

    def select(self, context, request):
        if request.connector_id and request.connector_id.lower().replace('-', '_') in {'drive', 'gdrive', 'google_drive'}:
            raise LaneError('GOOGLE_DRIVE_PRIMARY_RUNTIME_FORBIDDEN', 'A blob mirror cannot be the transactional primary.')
        token = 'SELECT_STORAGE:' + request.mode + (':' + request.connector_id if request.connector_id else '')
        if request.confirmation != token:
            raise LaneError('STORAGE_SELECTION_CONFIRMATION_INVALID', 'Use the exact mode and connector selection token.')
        input_digest = content_digest(request.model_dump(mode='json'))
        with self.engine.project_work.mutation(self.project) as lease:
            with self.store.connection(read_only=True) as connection:
                history = self._history(connection)
                for row, result in history:
                    if row['request_id'] == context.request_id:
                        if (row['client_id'], row['input_digest']) != (context.client_id, input_digest):
                            raise LaneError('STORAGE_REQUEST_ID_REUSED', 'The storage request ID belongs to another exact selection.')
                        return result
                head = history[-1][1].event_digest if history else None
                if head != request.expected_event_digest:
                    raise LaneError('STORAGE_SELECTION_HEAD_CHANGED', 'Refresh the exact selection head before changing it.')
                if history and (history[-1][1].mode, history[-1][1].connector_id) == (request.mode, request.connector_id):
                    return history[-1][1]
                if len(history) >= 4096:
                    raise LaneError('STORAGE_HISTORY_BUDGET', 'The storage ledger reached its bounded event capacity.')
                from .session_authority import SessionAuthority
                current = SessionAuthority.current(connection)
                if current and current['state'] == 'active' and (current['owner_client_id'], current['owner_engine_id']) != (
                        context.client_id, self.engine.instance_id):
                    raise LaneError('SESSION_OWNER_REQUIRED', 'Only the active session owner may change its storage preference.')
            self.engine.sessions._safe_boundary(self.project)
            backend = self._backend(context)
            satisfied, reason = self._availability(request.mode, request.connector_id, backend)
            created = now()
            body = StorageSelectionResult(project_id=self.project.project_id, mode=request.mode,
                connector_id=request.connector_id, selection_recorded=True, history_count=len(history) + 1,
                selected_by=context.client_id, selected_at=created, reason=request.reason,
                backend=backend, policy_satisfied=satisfied, availability_reason=reason,
                observation_scope='at_selection_commit')
            event = {'sequence': len(history) + 1, 'request_id': context.request_id, 'client_id': context.client_id,
                'input_digest': input_digest, 'previous_digest': head, 'body_json': json_text(body.model_dump(mode='json')),
                'created_at': created}
            if sum(len(row['body_json'].encode()) for row, _ in history) + len(event['body_json'].encode()) > STORAGE_HISTORY_MAX_BYTES:
                raise LaneError('STORAGE_HISTORY_BUDGET', 'This selection would exceed the readable history byte budget.')
            digest = content_digest(event)
            with lease.coordinated_transaction(['receipts']):
                apply_migrations(self.store, STORAGE_MIGRATIONS, writer=lease)
                with self.store.transaction() as connection:
                    if context.authorize:
                        context.authorize('admin')
                    connection.execute('INSERT INTO storage_selection_events VALUES(?,?,?,?,?,?,?,?)',
                        (event['sequence'], event['request_id'], event['client_id'], input_digest, head, event['body_json'], digest, created))
                    self.store.append_receipt('storage_selection', {'event_digest': digest,
                        'request_id': context.request_id, 'client_id': context.client_id, 'mode': request.mode,
                        'connector_id': request.connector_id, 'backend_policy_digest': backend.policy_digest}, connection=connection)
            return body.model_copy(update={'event_digest': digest})


def register_storage_actions(engine):
    def service(context, write=False):
        return StorageSelection(engine, engine.directory.open(context.project_id, write=write))
    engine.registry.register(ActionSpec('storage_connector_inspect',
        'Read the verified storage selection chain and current authenticated backend evidence without changing routing.',
        StorageInspect, StorageSelectionResult, lambda context, request: service(context).inspect(context, request),
        workflow='select-project-storage', profile='receipts', queryable_in_delta=True, studio_read=True))
    engine.registry.register(ActionSpec('storage_connector_select',
        'Append an exact primary storage preference at a safe boundary; Boot/Resume enforces its connected backend.',
        StorageSelect, StorageSelectionResult, lambda context, request: service(context, True).select(context, request),
        workflow='select-project-storage', profile='receipts', permission='admin', mutates=True))
