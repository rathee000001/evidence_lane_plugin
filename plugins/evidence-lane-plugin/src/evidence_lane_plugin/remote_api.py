"""Explicit remote engine access; transactional state stays at the selected server.

Adapts v3 persistence routing and storage-selection intent. Blob uploads are not
live SQLite authority. A restart probe measures recovery, not physical disk life.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field, ValidationError, field_validator

from .capture_routing import HookEnvelope
from .engine import Engine
from .errors import LaneError
from .host_routing import ClientHello
from .internal_sdk import dispatch_authenticated
from .migrations import Migration, apply_migrations
from .projects import ProjectAccess
from .registry import Contract
from .sdk import UUID_PATTERN, ActionRequest
from .storage import json_text
from .writers import WriterLease

MAX_BYTES = 1_048_576
TOKEN_PATTERN = r"[A-Za-z0-9_-]{43,128}"
REMOTE_MIGRATIONS = (Migration("remote", 1, "Scoped remote restart probes", (
    """CREATE TABLE remote_probe (
        principal_id TEXT PRIMARY KEY, policy_digest TEXT NOT NULL,
        nonce_digest TEXT NOT NULL, object_digest TEXT NOT NULL,
        engine_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
)),)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def https_origin(value: str) -> str:
    """No credentials, paths, fragments or redirects in the selected endpoint."""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {"", "/"} or parsed.query
                or parsed.fragment or not 1 <= (parsed.port or 443) <= 65535
                or re.search(r"[\s\\%]", value)):
            raise ValueError()
    except ValueError:
        raise LaneError("REMOTE_HTTPS_REQUIRED", "Select an explicit HTTPS origin without credentials or a path.") from None
    return "https://" + parsed.netloc.lower()


class RemoteGrant(Contract):
    principal_id: str = Field(pattern=UUID_PATTERN)
    project_id: str = Field(pattern=UUID_PATTERN)
    access_grant_id: str = Field(pattern=UUID_PATTERN)
    credential_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,127}$")
    actions: list[str] = Field(min_length=1, max_length=128)
    allow_storage_probe: bool = False
    expires_at: str
    purpose: str = Field(min_length=1, max_length=1000)

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value):
        if datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("A timezone-aware expiry is required")
        return value

    @field_validator("actions")
    @classmethod
    def action_names(cls, value):
        if len(set(value)) != len(value) or any(not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item) for item in value):
            raise ValueError("Select each canonical action once")
        return sorted(value)


class RemotePolicy(Contract):
    server_id: str = Field(pattern=UUID_PATTERN)
    origin: str
    storage_class: Literal["persistent_operator_declared", "ephemeral", "unverified"] = "unverified"
    volume_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    grants: list[RemoteGrant] = Field(min_length=1, max_length=64)

    @field_validator("origin")
    @classmethod
    def origin_value(cls, value):
        return https_origin(value)


def issue_remote_grant(engine: Engine, *, project_id: str, actions: list[str], credential_env: str,
                       purpose: str, expires_at: datetime, allow_storage_probe: bool = False) -> RemoteGrant:
    """Owner-side setup API; this function has no remote or ordinary MCP route."""
    if expires_at.tzinfo is None or not 0 < (expires_at - datetime.now(UTC)).total_seconds() <= 30 * 86400:
        raise LaneError("REMOTE_EXPIRY_INVALID", "Select a future remote expiry within 30 days.")
    permissions = {"read"}
    for action in actions:
        spec = engine.registry.get(action)
        if not spec.project_required:
            raise LaneError("REMOTE_GLOBAL_ACTION_FORBIDDEN", "Remote scopes contain project actions only.")
        permissions.add(spec.permission)
    if allow_storage_probe:
        permissions.add("write")
    principal_id = str(uuid4())
    # Validate all metadata before any permissions are created.
    provisional = RemoteGrant(principal_id=principal_id, project_id=project_id,
                              access_grant_id=str(uuid4()), credential_env=credential_env, actions=actions,
                              purpose=purpose, expires_at=expires_at.isoformat(), allow_storage_probe=allow_storage_probe)
    store = engine.directory.open(project_id, write=True)
    with engine.admit(), WriterLease(store, engine.instance_id) as lease:
        access = ProjectAccess(store)
        access.initialize(writer=lease)
        grant_id = access.issue(principal_id, permissions, [store.source_root], expires_at=expires_at, writer=lease)
        return provisional.model_copy(update={"access_grant_id": grant_id})


class DiscoverRequest(Contract):
    hello: ClientHello = Field(default_factory=ClientHello)


class RemoteAction(Contract):
    instance_id: str = Field(pattern=UUID_PATTERN)
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    connection_token: str = Field(pattern='^' + TOKEN_PATTERN + '$')
    request: ActionRequest


class RemoteConnect(DiscoverRequest):
    instance_id: str = Field(pattern=UUID_PATTERN)
    policy_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class RemoteConnection(Contract):
    instance_id: str = Field(pattern=UUID_PATTERN)
    policy_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    connection_token: str = Field(pattern='^' + TOKEN_PATTERN + '$')


class RemoteCapture(RemoteConnection):
    capture: HookEnvelope


class ProbeRequest(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{43,128}$")


class ProbeVerification(ProbeRequest):
    previous_engine_id: str = Field(pattern=UUID_PATTERN)
    policy_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RemoteGateway:
    """Owner-configured capabilities; no network bootstrap or owner/Studio routes."""

    def __init__(self, engine: Engine, policy: RemotePolicy, *, environment=None, clock=None):
        self.engine = engine
        self.policy = RemotePolicy.model_validate(policy.model_dump())
        self.clock = clock or (lambda: datetime.now(UTC))
        self._credentials: dict[str, RemoteGrant] = {}
        self._revoked: set[str] = set()
        self._verified: dict[str, datetime] = {}
        self._connections: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()
        environment = os.environ if environment is None else environment
        for grant in self.policy.grants:
            token = environment.get(grant.credential_env, "")
            if not re.fullmatch(TOKEN_PATTERN, token):
                raise LaneError("REMOTE_CREDENTIAL_UNAVAILABLE", "Supply a bounded random credential through the configured environment name.")
            key = digest(token.encode())
            if key in self._credentials or any(g.principal_id == grant.principal_id for g in self._credentials.values()):
                raise LaneError("REMOTE_CREDENTIAL_COLLISION", "Each scoped remote principal requires a distinct credential.")
            self._credentials[key] = grant
            try:
                self._access(grant, "read")
            except LaneError as error:
                if error.code not in {"REMOTE_GRANT_EXPIRED", "REMOTE_PERMISSION_DENIED"}:
                    raise
                self._revoked.add(grant.principal_id)
                continue
            for action in grant.actions:
                spec = engine.registry.get(action)
                if not spec.project_required:
                    raise LaneError("REMOTE_GLOBAL_ACTION_FORBIDDEN", "Remote scopes contain project actions only.")
                self._access(grant, spec.permission)
            if grant.allow_storage_probe:
                self._access(grant, "write")

    def _access(self, grant: RemoteGrant, permission: str):
        with self._lock:
            if grant.principal_id in self._revoked or datetime.fromisoformat(grant.expires_at) <= self.clock():
                raise LaneError("REMOTE_GRANT_EXPIRED", "This remote grant is expired or revoked.")
        store = self.engine.directory.open(grant.project_id, write=permission != "read")
        # The exact project grant must still be active; another principal grant
        # cannot resurrect a revoked remote binding.
        with store.lane('receipts').connection(read_only=True) as connection:
            row = connection.execute("SELECT * FROM access_grants WHERE grant_id=? AND principal_id=?",
                                     (grant.access_grant_id, grant.principal_id)).fetchone()
        if (row is None or row["revoked_at"] or not row["expires_at"]
                or datetime.fromisoformat(row["expires_at"]) <= self.clock()
                or permission not in json.loads(row["permissions_json"])):
            raise LaneError("REMOTE_PERMISSION_DENIED", "The selected project grant no longer covers this action.")
        return store, frozenset(json.loads(row["permissions_json"]))

    def authenticate(self, token: str) -> RemoteGrant:
        if not re.fullmatch(TOKEN_PATTERN, token):
            raise LaneError("REMOTE_AUTHENTICATION_REQUIRED", "A current scoped remote credential is required.")
        with self._lock:
            grant = self._credentials.get(digest(token.encode()))
        if grant is None:
            raise LaneError("REMOTE_AUTHENTICATION_REQUIRED", "A current scoped remote credential is required.")
        self._access(grant, "read")
        return grant

    def policy_digest(self, grant: RemoteGrant) -> str:
        return digest(json_text({"server_id": self.policy.server_id, "origin": self.policy.origin,
                                "storage_class": self.policy.storage_class, "volume_id": self.policy.volume_id,
                                "grant": grant.model_dump()}).encode())

    def _require_durable(self, grant):
        self._access(grant, 'read')
        with self._lock:
            verified = self._verified.get(grant.principal_id)
        if (self.policy.storage_class != 'persistent_operator_declared' or verified is None
                or not 0 <= (self.clock() - verified).total_seconds() <= 300):
            raise LaneError('REMOTE_DURABILITY_UNVERIFIED', 'Verify this server storage after an engine restart before remote execution.')

    def storage_evidence(self, grant, project_id):
        """Resolve evidence on the server for this exact authenticated connection."""
        from .storage_selection import StorageBackend
        if project_id != grant.project_id:
            raise LaneError('REMOTE_SCOPE_DENIED', 'The storage observer is bound to its exact project.')
        self._require_durable(grant)
        with self._lock:
            verified = self._verified[grant.principal_id]
        return StorageBackend(project_id=project_id, engine_instance_id=self.engine.instance_id,
            connection='remote_api', evidence_basis='authenticated_gateway_policy_and_restart_probe',
            storage_class=self.policy.storage_class, volume_id=self.policy.volume_id,
            policy_digest=self.policy_digest(grant), connector_id=self.policy.server_id,
            restart_recovery_verified=True, restart_verified_at=verified.isoformat(), observed_at=self.clock().isoformat())

    def _connection(self, envelope, grant, *, durable=True):
        if envelope.instance_id != self.engine.instance_id or envelope.policy_digest != self.policy_digest(grant):
            raise LaneError('REMOTE_BINDING_CHANGED', 'Reconnect and verify the selected remote engine before execution.')
        with self._lock:
            owner = self._connections.get(digest(envelope.connection_token.encode()))
        if owner is None or owner[0] != grant.principal_id:
            raise LaneError('REMOTE_CONNECTION_REQUIRED', 'Use the authenticated connection issued for this exact remote grant.')
        session = self.engine.clients.authenticate(envelope.connection_token)
        if durable:
            self._require_durable(grant)
        return session

    def prepare_probe(self, principal_id: str) -> ProbeVerification:
        """Owner-only setup for a read-only client; never exposed on HTTP/MCP.

        The client may verify this ticket after a server restart. It cannot
        seed or replace the record unless its separate probe grant permits it.
        """
        with self._lock:
            grant = next((row for row in self._credentials.values() if row.principal_id == principal_id), None)
        if grant is None:
            raise LaneError('REMOTE_GRANT_UNKNOWN', 'Select a configured remote principal.')
        nonce = secrets.token_urlsafe(48)
        with self.engine.admit():
            result = self._seed_probe(grant, nonce, owner_prepared=True)
        return ProbeVerification(project_id=grant.project_id, nonce=nonce,
            previous_engine_id=result['instance_id'], policy_digest=result['policy_digest'], object_digest=result['object_digest'])

    def _seed_probe(self, grant, nonce, *, owner_prepared=False):
        permission = 'read' if owner_prepared else 'write'
        self._access(grant, permission)
        store = self.engine.directory.open(grant.project_id, write=True)
        receipts, policy = store.lane('receipts'), self.policy_digest(grant)
        nonce_digest = digest(nonce.encode())
        with WriterLease(store, self.engine.instance_id) as lease:
            apply_migrations(receipts, REMOTE_MIGRATIONS, writer=lease)
            content = json_text({'nonce_digest': nonce_digest, 'policy_digest': policy}).encode()
            with lease.transaction('receipts') as connection:
                self._access(grant, permission)
                object_digest = receipts.put_object(content)
                connection.execute('INSERT OR REPLACE INTO remote_probe VALUES(?,?,?,?,?,?)',
                    (grant.principal_id, policy, nonce_digest, object_digest, self.engine.instance_id, self.clock().isoformat()))
                receipts.append_receipt('remote_storage_probe', {'principal_id': grant.principal_id,
                    'policy_digest': policy, 'object_digest': object_digest,
                    'prepared_by': 'server_owner' if owner_prepared else 'probe_authorized_remote_principal'}, connection=connection)
        with self._lock:
            self._verified.pop(grant.principal_id, None)
        return {'server_id': self.policy.server_id, 'project_id': grant.project_id,
            'instance_id': self.engine.instance_id, 'policy_digest': policy,
            'object_digest': object_digest, 'durable_verified': False}

    def revoke(self, principal_id: str) -> None:
        """Owner-only revocation, durable through the project's existing grant ledger."""
        with self.engine.admit():
            with self._lock:
                grant = next((g for g in self._credentials.values() if g.principal_id == principal_id), None)
                if grant is None:
                    raise LaneError("REMOTE_GRANT_UNKNOWN", "Select a configured remote principal.")
                self._revoked.add(principal_id)
            store = self.engine.directory.open(grant.project_id, write=True)
            with self.engine.project_work.mutation(store, kind="signal") as lease:
                ProjectAccess(store).revoke(grant.access_grant_id, writer=lease)

    def handle(self, route: str, payload: bytes, grant: RemoteGrant) -> dict:
        with self.engine.admit():
            return self._handle(route, payload, grant)

    def _handle(self, route: str, payload: bytes, grant: RemoteGrant) -> dict:
        if route == "discover":
            request = DiscoverRequest.model_validate_json(payload)
            self._access(grant, "read")
            observation = self.engine.detector.inspect(trigger="client_connect", client=request.hello)
            return {"protocol_version": 4, "server_id": self.policy.server_id,
                    "instance_id": self.engine.instance_id, "project_id": grant.project_id,
                    "policy_digest": self.policy_digest(grant), "storage_class": self.policy.storage_class,
                    "volume_id": self.policy.volume_id, "durable_verified": False,
                    "host_observation": observation.model_dump(mode="json"),
                    "package_digest": self.engine.runtime_identity['source_digest'],
                    "connection_required": True, "native_task_attestation": "not_provided",
                    "actions": [self.engine.registry.get(name).schema() for name in grant.actions]}
        if route == 'connect':
            request = RemoteConnect.model_validate_json(payload)
            if (request.instance_id != self.engine.instance_id or request.policy_digest != self.policy_digest(grant)):
                raise LaneError('REMOTE_BINDING_CHANGED', 'Discover and verify this engine before connecting.')
            self._require_durable(grant)
            self._access(grant, 'read')
            permissions = {'read', *(self.engine.registry.get(name).permission for name in grant.actions)}
            token, session = self.engine.clients.connect_scoped(project_id=grant.project_id,
                parent_grant_id=grant.access_grant_id, permissions=permissions,
                expires_at=datetime.fromisoformat(grant.expires_at), hello=request.hello,
                authorize=lambda permission: self._access(grant, permission), actions=grant.actions,
                storage_observer=lambda project_id: self.storage_evidence(grant, project_id))
            live = {row['client_id'] for row in self.engine.clients.status()}
            with self._lock:
                # Tokens are digested and bounded by the engine's live-client budget.
                self._connections = {key: owner for key, owner in self._connections.items()
                                     if owner[1] in live}
                self._connections[digest(token.encode())] = (grant.principal_id, session.client_id)
            return {'server_id': self.policy.server_id, 'instance_id': self.engine.instance_id,
                'policy_digest': self.policy_digest(grant), 'project_id': grant.project_id,
                'client_id': session.client_id, 'connection_token': token, 'expires_at': session.expires_at.isoformat(),
                'identity_scope': 'authenticated_engine_client', 'native_task_attestation': 'not_provided'}
        if route == 'disconnect':
            envelope = RemoteConnection.model_validate_json(payload)
            session = self._connection(envelope, grant, durable=False)
            with self._lock:
                self._connections.pop(digest(envelope.connection_token.encode()), None)
            self.engine.capture.detach_project(session.client_id, grant.project_id)
            self.engine.clients.disconnect(envelope.connection_token)
            return {'server_id': self.policy.server_id, 'instance_id': self.engine.instance_id,
                'policy_digest': self.policy_digest(grant), 'disconnected': True, 'client_id': session.client_id}
        if route == 'capture':
            envelope = RemoteCapture.model_validate_json(payload)
            session = self._connection(envelope, grant)
            if 'capture_bind' not in grant.actions:
                raise LaneError('REMOTE_SCOPE_DENIED', 'This grant does not include remote visible capture.')
            context = self.engine.clients.context(session, grant.project_id, 'write')
            with self.engine.project_work.authorized(context, 'write'):
                result = self.engine.capture.capture(envelope.capture, expected_client_id=session.client_id,
                    expected_project_id=grant.project_id, provenance='authenticated_remote_hook_report')
            return {'server_id': self.policy.server_id, 'instance_id': self.engine.instance_id,
                'policy_digest': self.policy_digest(grant), 'result': result.model_dump(mode='json')}
        if route == "action":
            envelope = RemoteAction.model_validate_json(payload)
            session = self._connection(envelope, grant)
            request = envelope.request
            if request.project_id != grant.project_id or request.action not in grant.actions:
                raise LaneError("REMOTE_SCOPE_DENIED", "The remote grant does not cover this project and action.")
            spec = self.engine.registry.get(request.action)
            context = replace(self.engine.clients.context(session, grant.project_id, spec.permission),
                              allowed_actions=frozenset(grant.actions))
            result = dispatch_authenticated(self.engine, request, context)
            return {"server_id": self.policy.server_id, "instance_id": self.engine.instance_id,
                    "policy_digest": self.policy_digest(grant), "response": result.model_dump(mode="json")}
        if route not in {"probe", "verify"}:
            raise LaneError("REMOTE_ROUTE_UNKNOWN", "The remote route is not supported.")
        request = (ProbeRequest if route == "probe" else ProbeVerification).model_validate_json(payload)
        if request.project_id != grant.project_id or (route == 'probe' and not grant.allow_storage_probe):
            raise LaneError("REMOTE_SCOPE_DENIED", "This grant does not permit a project storage probe.")
        store, _ = self._access(grant, "write" if route == "probe" else "read")
        policy_digest = self.policy_digest(grant)
        nonce_digest = digest(request.nonce.encode())
        receipts = store.lane('receipts')
        if route == "probe":
            return self._seed_probe(grant, request.nonce)
        with receipts.connection(read_only=True) as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='remote_probe'").fetchone()
            row = connection.execute("SELECT * FROM remote_probe WHERE principal_id=?", (grant.principal_id,)).fetchone() if exists else None
            content = receipts.read_object(row['object_digest']) if row is not None else None
        if (row is None or not hmac.compare_digest(row["nonce_digest"], nonce_digest)
                or row["policy_digest"] != policy_digest or request.policy_digest != policy_digest
                or row["object_digest"] != request.object_digest or row["engine_id"] != request.previous_engine_id):
            raise LaneError("REMOTE_PROBE_MISMATCH", "The scoped restart probe does not match this server state.")
        if content != json_text({"nonce_digest": nonce_digest, "policy_digest": policy_digest}).encode():
            raise LaneError("REMOTE_PROBE_MISMATCH", "The remote probe object no longer matches its record.")
        restarted = row["engine_id"] != self.engine.instance_id
        durable = restarted and self.policy.storage_class == "persistent_operator_declared"
        with self._lock:
            if durable:
                self._verified[grant.principal_id] = self.clock()
            else:
                self._verified.pop(grant.principal_id, None)
        return {"server_id": self.policy.server_id, "instance_id": self.engine.instance_id,
                "project_id": grant.project_id, "policy_digest": policy_digest,
                "object_digest": row["object_digest"], "restart_observed": restarted,
                "storage_class": self.policy.storage_class, "volume_id": self.policy.volume_id,
                "durable_verified": durable, "verified_at": self.clock().isoformat(),
                "evidence_basis": "authenticated_server_sqlite_and_object_read_after_engine_restart",
                "physical_volume_durability": "operator_declaration_only"}


class RemoteApplication:
    """Small bounded ASGI surface, served only with TLS and proxy headers disabled."""

    def __init__(self, gateway: RemoteGateway):
        self.gateway = gateway

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        headers = scope.get("headers", [])
        values = lambda name: [value.decode("latin1") for key, value in headers if key == name]
        status, result = 400, {"error": "INVALID_MESSAGE"}
        try:
            if (scope.get("scheme") != "https" or scope["method"] != "POST"
                    or values(b"host") != [urlsplit(self.gateway.policy.origin).netloc]
                    or values(b"origin") or values(b"cookie") or scope.get("query_string")):
                raise LaneError("REMOTE_ORIGIN_DENIED", "Use the configured native HTTPS endpoint.")
            authorization = values(b"authorization")
            if len(authorization) != 1 or not authorization[0].startswith("Bearer "):
                raise LaneError("REMOTE_AUTHENTICATION_REQUIRED", "A current scoped remote credential is required.")
            grant = await asyncio.to_thread(self.gateway.authenticate, authorization[0][7:])
            lengths = values(b"content-length")
            if (len(lengths) != 1 or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_BYTES
                    or values(b"transfer-encoding") or values(b"content-type") != ["application/json"]):
                raise ValueError()
            content = bytearray()
            async with asyncio.timeout(10):
                while True:
                    event = await receive()
                    if event["type"] != "http.request":
                        raise ValueError()
                    content.extend(event.get("body", b""))
                    if len(content) > int(lengths[0]):
                        raise ValueError()
                    if not event.get("more_body", False):
                        break
            if len(content) != int(lengths[0]):
                raise ValueError()
            prefix = "/remote/v4/"
            if not scope["path"].startswith(prefix):
                raise LaneError("REMOTE_ROUTE_UNKNOWN", "The remote route is not supported.")
            result = await asyncio.to_thread(self.gateway.handle, scope["path"][len(prefix):], bytes(content), grant)
            status = 200
        except LaneError as error:
            status = 401 if error.code in {"REMOTE_AUTHENTICATION_REQUIRED", "REMOTE_GRANT_EXPIRED"} else 409
            result = {"error": error.code}
        except (ValueError, TypeError, ValidationError, TimeoutError):
            pass
        except Exception:  # noqa: BLE001 - do not expose request or credential values
            status, result = 500, {"error": "INTERNAL_ERROR"}
        encoded = json_text(result).encode()
        if len(encoded) > MAX_BYTES:
            status, encoded = 500, b'{"error":"RESPONSE_TOO_LARGE"}'
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(encoded)).encode()),
            (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": encoded})
