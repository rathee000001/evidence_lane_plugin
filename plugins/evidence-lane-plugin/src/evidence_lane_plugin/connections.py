"""Engine-issued client sessions and explicit project selections.

The current Windows user authorizes a selection through the protected local
bootstrap channel. Claimed Codex task identifiers are labels, not attestation.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import threading
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue

from .errors import LaneError
from .host_routing import ClientHello, HostDetector, HostObservation
from .projects import PERMISSIONS, ProjectAccess, ProjectDirectory
from .registry import ActionContext, ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import sqlite_lock_wait
from .writers import WriterLease


class ProjectSelection(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    permissions: list[str] = Field(default_factory=lambda: ["read"], min_length=1, max_length=7)


class ConnectRequest(Contract):
    label: str = Field(default="Native MCP adapter", min_length=1, max_length=100)
    claimed_task_id: str | None = Field(default=None, max_length=128)
    projects: list[ProjectSelection] = Field(default_factory=list, max_length=32)
    hello: ClientHello = Field(default_factory=ClientHello)
    manage_projects: bool = False


class ClientContextQuery(Contract):
    pass


class ClientContextResult(Contract):
    client_id: str
    engine_instance_id: str
    expires_at: str
    host_observation: HostObservation
    projects: list[dict[str, JsonValue]]
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    permission_changed: Literal[False] = False


def register_client_actions(engine):
    def read(context, request):
        session = engine.clients.session(context.client_id)
        projects = []
        for project_id, selection in sorted(session.projects.items()):
            item = {'project_id': project_id, 'permissions': sorted(selection.permissions)}
            try:
                engine.clients.context(session, project_id, 'read')
                store = engine.directory.open(project_id)
                item.update(source_root=str(store.source_root), state_root=str(store.root), authorization='current')
            except LaneError as error:
                item.update(authorization='unavailable', error_code=error.code)
            projects.append(item)
        return ClientContextResult(client_id=session.client_id, engine_instance_id=engine.instance_id,
            expires_at=session.expires_at.isoformat(), host_observation=session.host_observation, projects=projects)
    engine.registry.register(ActionSpec('client_context',
        'Read this authenticated engine client and its explicitly configured project selections.',
        ClientContextQuery, ClientContextResult, read, project_required=False, workflow='boot'))


@dataclass(frozen=True)
class Selection:
    permissions: frozenset[str]
    grant_id: str | None


@dataclass(frozen=True)
class Session:
    client_id: str
    label: str
    claimed_task_id: str | None
    projects: dict[str, Selection]
    expires_at: datetime
    host_observation: HostObservation
    identity_evidence: Literal["client_claim_only"] = "client_claim_only"
    manage_projects: bool = False
    scoped_authorize: Callable[[str], object] | None = None
    allowed_actions: frozenset[str] | None = None
    storage_observer: Callable[[str], object] | None = None


class ClientRouter:
    def __init__(self, directory: ProjectDirectory, *, clock=None, max_clients: int = 256,
                 detector: HostDetector | None = None, engine_id: str | None = None, storage_observer=None):
        self.directory = directory
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_clients = max_clients
        self.detector = detector or HostDetector()
        self.engine_id = engine_id or str(uuid4())
        self.storage_observer = storage_observer
        self._sessions: dict[str, Session] = {}
        self._pending_revocations: dict[str, Session] = {}
        self._pending_connections = 0
        self._lock = threading.RLock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def connect(self, request: ConnectRequest) -> tuple[str, Session]:
        """Only called after the current-user bootstrap credential is authenticated."""
        if request.hello.configured_profile in {'codex_vm_persistent', 'codex_vm_ephemeral'}:
            raise LaneError('REMOTE_DURABILITY_UNVERIFIED', 'This configured VM profile requires a verified remote connection.')
        with self._lock:
            expired = [value for value in self._sessions.values() if value.expires_at <= self.clock()]
            self._pending_revocations.update((session.client_id, session) for session in expired)
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > self.clock()}
            if len(self._sessions) + self._pending_connections >= self.max_clients:
                raise LaneError("CLIENT_LIMIT", "The engine has reached its client limit.")
            if len({item.project_id for item in request.projects}) != len(request.projects):
                raise LaneError("DUPLICATE_PROJECT_SELECTION", "Select each project once.")
            # Validate the complete request before any grant is written.
            validated = []
            for item in request.projects:
                permissions = frozenset(item.permissions)
                if not permissions or not permissions <= PERMISSIONS:
                    raise LaneError("INVALID_SELECTION", "Select supported project permissions.")
                store = self.directory.open(item.project_id, write=bool(permissions - {"read"}))
                validated.append((store, permissions))
            observation = self.detector.inspect(trigger="client_connect", client=request.hello)
            client_id = str(uuid4())
            token = secrets.token_urlsafe(48)
            expires = self.clock() + timedelta(hours=12)
            selections = {}
            issued = []
            with ExitStack() as leases:
                held = {store.project_id: leases.enter_context(WriterLease(store, self.engine_id, clock=self.clock))
                        for store, permissions in sorted(validated, key=lambda item: item[0].project_id)
                        if permissions - {"read"}}
                try:
                    for store, permissions in validated:
                        grant_id = None
                        if permissions - {"read"}:
                            held[store.project_id].check()
                            access = ProjectAccess(store, clock=self.clock)
                            access.initialize(writer=held[store.project_id])
                            grant_id = access.issue(client_id, permissions, [store.source_root], expires_at=expires,
                                                    writer=held[store.project_id])
                            issued.append((access, grant_id))
                            held[store.project_id].check()
                        selections[store.project_id] = Selection(permissions, grant_id)
                except BaseException:
                    for access, grant_id in issued:
                        access.revoke(grant_id, writer=held[access.store.project_id])
                    raise
            session = Session(client_id, request.label, request.claimed_task_id, selections, expires, observation,
                              manage_projects=request.manage_projects, storage_observer=self.storage_observer)
            self._sessions[self._digest(token)] = session
            return token, session

    def authenticate(self, token: str) -> Session:
        with self._lock:
            session = self._sessions.get(self._digest(token))
            if session is None or session.expires_at <= self.clock():
                raise LaneError("CLIENT_SESSION_EXPIRED", "Reconnect this client to the local engine.")
            return session

    def connect_scoped(self, *, project_id, parent_grant_id, permissions, expires_at, hello, authorize, actions,
                       storage_observer=None):
        """Called only by the authenticated remote gateway, with its exact owner grant.

        Every connection has a new identity. Its durable grant depends on the
        parent grant; no bearer secret or reported host label enters the ledger.
        """
        with self._lock:
            expired = [value for value in self._sessions.values() if value.expires_at <= self.clock()]
            self._pending_revocations.update((session.client_id, session) for session in expired)
            self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > self.clock()}
            if len(self._sessions) + self._pending_connections >= self.max_clients:
                raise LaneError('CLIENT_LIMIT', 'The engine has reached its client limit.')
            self._pending_connections += 1
        try:
            authorize('read')
            store = self.directory.open(project_id, write=True)
            client_id, token = str(uuid4()), secrets.token_urlsafe(48)
            expires = min(expires_at, self.clock() + timedelta(hours=12))
            with WriterLease(store, self.engine_id, clock=self.clock) as lease:
                access = ProjectAccess(store, clock=self.clock)
                access.initialize(writer=lease)
                with lease.transaction('receipts'):
                    authorize('read')
                    with store.lane('receipts').connection(read_only=True) as connection:
                        parent = connection.execute('SELECT roots_json FROM access_grants WHERE grant_id=?', (parent_grant_id,)).fetchone()
                    grant_id = access.issue(client_id, permissions, [Path(path) for path in json.loads(parent[0])],
                        expires_at=expires, parent_grant_id=parent_grant_id, writer=lease)
            session = Session(client_id, 'Scoped remote connection', None,
                {project_id: Selection(frozenset(permissions), grant_id)}, expires,
                self.detector.inspect(trigger='client_connect', client=hello), scoped_authorize=authorize,
                allowed_actions=frozenset(actions), storage_observer=storage_observer)
            with self._lock:
                self._sessions[self._digest(token)] = session
            return token, session
        finally:
            with self._lock:
                self._pending_connections -= 1

    def context(self, session: Session, project_id: str | None, permission: str) -> ActionContext:
        with self._lock:
            if session.expires_at <= self.clock() or not any(active is session for active in self._sessions.values()):
                raise LaneError("CLIENT_SESSION_EXPIRED", "Reconnect this client to the local engine.")
        if session.scoped_authorize is not None:
            if project_id not in session.projects:
                raise LaneError('REMOTE_SCOPE_DENIED', 'Remote connections have only their configured project scope.')
            session.scoped_authorize(permission)
        if project_id is None:
            permissions = frozenset({'read', 'project_admin'} if session.manage_projects else {'read'})
        else:
            selected = session.projects.get(project_id)
            if selected is None or permission not in selected.permissions:
                raise LaneError("PROJECT_NOT_SELECTED", "This client has no selection for this action.")
            store = self.directory.open(project_id, write=permission != "read")
            if selected.grant_id:
                ProjectAccess(store, clock=self.clock).authorize(session.client_id, permission)
            permissions = selected.permissions
        return ActionContext(session.client_id, project_id, permissions,
                             allowed_actions=session.allowed_actions,
                             host_observation=session.host_observation,
                             storage_observer=session.storage_observer,
                             authorize=lambda required: self.context(session, project_id, required),
                             project_context=lambda target, required: self.context(session, target, required))

    def select_project(self, client_id, selection: ProjectSelection):
        """Use the separately granted owner administration capability, never a tool argument grant."""
        with self._lock:
            session = self.session(client_id)
            if not session.manage_projects:
                raise LaneError('PROJECT_ADMIN_REQUIRED', 'This connection was not granted project administration.')
            permissions = frozenset(selection.permissions)
            if not permissions or not permissions <= PERMISSIONS:
                raise LaneError('INVALID_SELECTION', 'Select supported project permissions.')
            previous = session.projects.get(selection.project_id)
            if previous is not None:
                if previous.permissions != permissions:
                    raise LaneError('PROJECT_SELECTION_CHANGED', 'Deselect this project before changing its granted permissions.')
                self.context(session, selection.project_id, 'read')
                return previous
            if len(session.projects) >= 32:
                raise LaneError('PROJECT_SELECTION_LIMIT', 'Select at most 32 projects on one client.')
            store = self.directory.open(selection.project_id, write=bool(permissions - {'read'}))
            grant_id = None
            if permissions - {'read'}:
                with WriterLease(store, self.engine_id, clock=self.clock) as lease:
                    access = ProjectAccess(store, clock=self.clock)
                    access.initialize(writer=lease)
                    grant_id = access.issue(client_id, permissions, [store.source_root],
                                            expires_at=session.expires_at, writer=lease)
            selected = Selection(permissions, grant_id)
            session.projects[selection.project_id] = selected
            return selected

    def deselect_project(self, client_id, project_id, expected_permissions):
        with self._lock:
            session = self.session(client_id)
            if not session.manage_projects:
                raise LaneError('PROJECT_ADMIN_REQUIRED', 'This connection was not granted project administration.')
            selected = session.projects.get(project_id)
            if selected is None or selected.permissions != frozenset(expected_permissions):
                raise LaneError('PROJECT_SELECTION_CHANGED', 'Refresh the current selection before deselecting it.')
            if selected.grant_id:
                store = self.directory.open(project_id, write=True)
                with WriterLease(store, self.engine_id, clock=self.clock) as lease:
                    ProjectAccess(store, clock=self.clock).revoke(selected.grant_id, writer=lease)
            del session.projects[project_id]

    def session(self, client_id: str) -> Session:
        """Resolve an existing internal connection; never create it from a label."""
        with self._lock:
            selected = next((value for value in self._sessions.values()
                             if value.client_id == client_id and value.expires_at > self.clock()), None)
            if selected is None:
                raise LaneError("CLIENT_SESSION_EXPIRED", "Reconnect this client to the local engine.")
        if selected.scoped_authorize is not None:
            selected.scoped_authorize('read')
        return selected

    def disconnect(self, token: str) -> None:
        with self._lock:
            session = self._sessions.pop(self._digest(token), None)
            if session:
                self._pending_revocations[session.client_id] = session
        if session is not None:
            self.revoke_pending(client_id=session.client_id)

    def revoke_pending(self, *, client_id: str | None = None) -> dict:
        """Invalidate sessions immediately; serialize grant cleanup after writers drain."""
        with self._lock:
            pending = [session for key, session in self._pending_revocations.items()
                       if client_id is None or key == client_id]
        completed = []
        for session in pending:
            try:
                with sqlite_lock_wait(50), ExitStack() as leases:
                    selected = []
                    for project_id, selection in sorted(session.projects.items()):
                        if selection.grant_id:
                            store = self.directory.open(project_id, write=True)
                            lease = leases.enter_context(WriterLease(store, self.engine_id, clock=self.clock))
                            selected.append((store, selection.grant_id, lease))
                    for store, grant_id, lease in selected:
                        lease.check()
                        ProjectAccess(store).revoke(grant_id, writer=lease)
                        lease.check()
                completed.append(session.client_id)
            except (LaneError, sqlite3.OperationalError):
                # A live writer is not interrupted for metadata cleanup. The
                # removed session already cannot authenticate or execute work.
                continue
        with self._lock:
            for key in completed:
                self._pending_revocations.pop(key, None)
            return {"completed": len(completed), "pending": len(self._pending_revocations)}

    def close(self) -> dict:
        with self._lock:
            self._pending_revocations.update((session.client_id, session) for session in self._sessions.values())
            self._sessions.clear()
        return self.revoke_pending()

    def status(self) -> list[dict]:
        with self._lock:
            return [{"client_id": session.client_id, "label": session.label,
                     "claimed_task_id": session.claimed_task_id,
                     "identity_evidence": session.identity_evidence,
                     "host_observation": session.host_observation.model_dump(mode="json"),
                     "project_ids": sorted(session.projects),
                     "expires_at": session.expires_at.isoformat()}
                    for session in self._sessions.values() if session.expires_at > self.clock()]
