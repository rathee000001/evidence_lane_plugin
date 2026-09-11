"""Owner-only Studio views over a separate browser session channel.

No native MCP token can mint a Studio session. Tickets are issued only by the
current-user launcher; cookies and CSRF tokens never enter project storage.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from uuid import uuid4

from pydantic import Field, JsonValue

from .accelerators import AcceleratorService
from .agent_learning import LearningRead, LearningStore
from .canon_task_graph import CanonRead, CanonStore
from .database_recovery import BackupVerify, verify_backup
from .errors import LaneError
from .host_routing import HOST_MATRIX
from .lineage import ChatLineage, LineageRead
from .plan_runtime import PlanStore
from .project_memory import MemoryRead, ProjectMemory
from .project_universe import ProjectUniverse
from .registry import ActionContext, Contract
from .runtime_health import project_health
from .sdk import UUID_PATTERN
from .steering import Steering
from .storage import now
from .store import restoration_control
from .task_binding_registry import ContinuationRead, TaskContinuity


class SessionRequest(Contract):
    ticket: str | None = Field(default=None, min_length=32, max_length=128)


class ProjectCommand(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)


class ReadAction(ProjectCommand):
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class VerifyProjectBackup(ProjectCommand):
    request: BackupVerify


@dataclass(frozen=True)
class BrowserSession:
    actor_id: str
    csrf: str
    expires_at: datetime


class StudioGateway:
    def __init__(self, engine, *, owner_control=None, clock=None):
        self.engine = engine
        self.owner_control = owner_control
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cookie_name = "el_studio_" + engine.instance_id.replace("-", "")
        self._tickets: dict[str, datetime] = {}
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _prune(self):
        current = self.clock()
        self._tickets = {key: expiry for key, expiry in self._tickets.items() if expiry > current}
        self._sessions = {key: value for key, value in self._sessions.items() if value.expires_at > current}

    def issue_ticket(self) -> str:
        with self._lock:
            self._prune()
            # Discard an unconsumed launch link, never an authenticated session.
            if len(self._tickets) >= 16:
                del self._tickets[next(iter(self._tickets))]
            ticket = secrets.token_urlsafe(48)
            self._tickets[self._digest(ticket)] = self.clock() + timedelta(seconds=60)
            return ticket

    def exchange(self, ticket: str, *, cookie: str = "") -> tuple[str | None, BrowserSession]:
        with self._lock:
            self._prune()
            if self._digest(ticket) not in self._tickets:
                raise LaneError("STUDIO_TICKET_EXPIRED", "Open Studio again from its launcher.")
            if cookie:
                try:
                    existing = self.authenticate(cookie)
                except LaneError:
                    pass  # An old engine/session cookie cannot grant owner access.
                else:
                    del self._tickets[self._digest(ticket)]
                    return None, existing
            if len(self._sessions) >= 16:
                raise LaneError("STUDIO_SESSION_LIMIT", "Close a Studio session before opening another.")
            del self._tickets[self._digest(ticket)]
            token = secrets.token_urlsafe(48)
            session = BrowserSession(str(uuid4()), secrets.token_urlsafe(48), self.clock() + timedelta(hours=12))
            self._sessions[self._digest(token)] = session
            return token, session

    def authenticate(self, cookie: str, *, csrf: str | None = None) -> BrowserSession:
        if len(cookie) > 16_384:
            raise LaneError("STUDIO_AUTHENTICATION_REQUIRED", "Open Studio from its launcher.")
        try:
            parsed = SimpleCookie()
            parsed.load(cookie)
            value = parsed[self.cookie_name].value
        except (CookieError, KeyError):
            raise LaneError("STUDIO_AUTHENTICATION_REQUIRED", "Open Studio from its launcher.") from None
        with self._lock:
            self._prune()
            session = self._sessions.get(self._digest(value))
            if session is None:
                raise LaneError("STUDIO_AUTHENTICATION_REQUIRED", "Open Studio from its launcher.")
            if csrf is not None and not hmac.compare_digest(csrf.encode(), session.csrf.encode()):
                raise LaneError("STUDIO_CSRF_REQUIRED", "Refresh Studio before making this change.")
            return session

    def cookie(self, token: str) -> str:
        return f"{self.cookie_name}={token}; HttpOnly; SameSite=Strict; Path=/studio/; Max-Age=43200"

    def logout(self, session: BrowserSession) -> None:
        with self._lock:
            self._sessions = {key: value for key, value in self._sessions.items() if value != session}

    def close(self) -> None:
        with self._lock:
            self._sessions.clear()
            self._tickets.clear()

    def plugin_scopes(self) -> dict:
        actions = self.engine.registry.schemas()
        return {"lanes": sorted({item["profile"] for item in actions}),
                "actions": [item["name"] for item in actions], "hosts": sorted(HOST_MATRIX)}

    def connectors(self, store):
        from .connector_governance import connector_service
        return connector_service(self.engine, store)

    def snapshot(self, project_id: str | None = None) -> dict:
        projects = list(self.engine.directory.entries().values())
        project = None
        if project_id is not None:
            store = self.engine.directory.open(project_id)
            from .storage import project_snapshot
            with project_snapshot(store.root):
                project = project_health(store, limit=50)
                object_count = object_bytes = 0
                for item in store.lane_catalog():
                    with store.lane(item['lane_id']).connection(read_only=True) as lane:
                        objects = lane.execute('SELECT count(*),coalesce(sum(size_bytes),0) FROM objects').fetchone()
                        object_count += objects[0]
                        object_bytes += objects[1]
                with store.lane('receipts').connection(read_only=True) as connection:
                    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
                    receipts = [dict(row) for row in connection.execute(
                        'SELECT receipt_id,kind,created_at FROM receipts ORDER BY created_at DESC,receipt_id LIMIT 50')]
                    recovered = connection.execute('SELECT * FROM recovery_control WHERE singleton=1').fetchone() if 'recovery_control' in tables else None
                with store.lane('sources').connection(read_only=True) as sources:
                    project['recovery'] = {'source_restore': restoration_control(sources),
                                           'database_recovery': dict(recovered) if recovered else None}
                project["evidence"] = {"object_count": object_count, "total_bytes": object_bytes, "receipts": receipts}
                project["accelerator"] = (AcceleratorService(store).settings() if "accelerator_settings" in tables
                                          else {"revision": 0, "config": None})
                project["plugins"] = self.connectors(store).catalog() if "extensions_registration" in tables else []
                project["plan"] = PlanStore(store).snapshot().model_dump(mode="json")
                project["control"] = Steering(store).control()
                project["learning"] = LearningStore(store).read(LearningRead(limit=20, include_history=True)).model_dump(mode="json")
                project["lineage"] = ChatLineage(store).read(LineageRead(limit=20)).model_dump(mode="json")
                project["memory"] = ProjectMemory(store).read(MemoryRead(limit=8)).model_dump(mode="json")
                project["linked_projects"] = ProjectUniverse(store).read().model_dump(mode="json")
                project["canon"] = CanonStore(store).read(CanonRead(limit=20)).model_dump(mode="json")
                project["continuity"] = TaskContinuity(store).read(ContinuationRead(limit=10)).model_dump(mode="json")
        from .tool_catalog import snapshot as tool_catalog_snapshot
        inventory = self.engine.capabilities.snapshot()
        return {"observed_at": now(), "engine": self.engine.health().model_dump(mode="json"),
                "projects": projects, "project": project, "connections": self.engine.clients.status(),
                "inventory": inventory, "tool_catalog": tool_catalog_snapshot(inventory),
                "plugin_scopes": self.plugin_scopes(),
                "lane_views": self.engine.registry.view_schemas(),
                "actions": [{key: item[key] for key in ("name", "description", "permission", "profile", "workflow",
                    "mutates", "requires_delta", "required_tools", "worker_operations", "queryable_in_delta",
                    "studio_read", "cross_project_read")}
                            for item in self.engine.registry.schemas()],
                "workers": self.engine.workers.status() if self.engine.workers else {"state": "not_configured"},
                "active_executions": self.engine.project_work.status()}

    def command(self, route: str, payload: dict, session: BrowserSession) -> dict:
        with self.engine.admit():
            return self._command(route, payload, session)

    def _command(self, route: str, payload: dict, session: BrowserSession) -> dict:
        if route not in {'read', 'backup-verify'}:
            raise LaneError('STUDIO_READ_ONLY', 'Studio observes project state; use the Codex plugin to change it.')
        if route == "read":
            request = ReadAction.model_validate(payload)
            spec = self.engine.registry.get(request.action)
            if not (spec.queryable_in_delta or spec.studio_read):
                raise LaneError("NOT_A_STUDIO_QUERY", "Select an admitted read-only project query.")
            # The protected owner session may read explicitly requested registered
            # projects. It does not impersonate a native MCP client's selections.
            def owner_context(project_id, permission):
                from .storage_selection import local_storage_evidence
                with self._lock:
                    if session.expires_at <= self.clock() or not any(active is session for active in self._sessions.values()):
                        raise LaneError("STUDIO_AUTHENTICATION_REQUIRED", "Open Studio from its launcher.")
                if permission != "read":
                    raise LaneError("PERMISSION_DENIED", "The Studio query channel grants read access only.")
                self.engine.directory.open(project_id)
                return ActionContext(session.actor_id, project_id, frozenset({"read"}),
                    authorize=lambda required: owner_context(project_id, required), project_context=owner_context,
                    storage_observer=lambda selected: local_storage_evidence(self.engine, selected))
            context = owner_context(request.project_id, "read")
            return self.engine.registry.execute(spec.name, request.arguments, context)
        if route == "backup-verify":
            request = VerifyProjectBackup.model_validate(payload)
            self.engine.directory.open(request.project_id)
            body = verify_backup(request.request.backup_root, request.request.manifest_digest, request.project_id)
            return {'project_id': request.project_id, 'manifest_digest': request.request.manifest_digest,
                    'file_count': len(body['files']), 'sqlite_integrity': 'ok', 'project_mutated': False}
        raise LaneError('STUDIO_READ_ONLY',
            'Studio exposes only registered read queries and backup verification.')
