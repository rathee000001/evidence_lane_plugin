"""Governed extensions, adapted from the indexed v3 connector implementation.

The role types, secret-field exclusion, ordered route guards and explicit ambiguity
policy are retained. Versioned configuration and fenced project transactions replace
the separate connector database. Registration never loads code or executes a backend.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, field_validator, model_validator

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .projects import ProjectAccess
from .registry import ActionContext, ActionSpec, Contract
from .storage import LaneStore, ProjectStore, json_text, reject_links
from .writers import WriterLease

_SCHEMA_FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SCHEMA_FIELD_TYPES = {"text", "integer", "number", "boolean", "datetime", "json", "blob_hash"}
_SECRET_SCHEMA_PARTS = {"password", "secret", "token", "api_key", "credential"}
ROUTE_GUARD_ORDER = (
    "ACTIVE", "GRANT_LIVE", "CAPABILITY_AND_ACTION_ALLOWED", "LANE_ALLOWED", "HOST_ALLOWED",
)


def normalize_role_schema(value: dict[str, str]) -> dict[str, str]:
    """Preserve v3's deterministic data-field contract and credential exclusion."""
    if not 1 <= len(value) <= 64:
        raise ValueError("A role schema needs between one and 64 fields")
    schema = {}
    for field, field_type in value.items():
        if not _SCHEMA_FIELD.fullmatch(field) or any(part in field for part in _SECRET_SCHEMA_PARTS):
            raise ValueError("Role schema fields cannot define credential storage")
        if field_type not in _SCHEMA_FIELD_TYPES:
            raise ValueError("Unsupported role schema type")
        schema[field] = field_type
    return dict(sorted(schema.items()))


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("A timezone is required")
    return parsed


class PluginRegistration(Contract):
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    name: str = Field(min_length=1, max_length=128)
    plugin_kind: Literal["connector", "toolchain"]
    description: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=1000)
    config_env_keys: list[str] = Field(default_factory=list, max_length=32)
    capabilities: list[str] = Field(min_length=1, max_length=128)
    allowed_lanes: list[str] = Field(min_length=1, max_length=128)
    allowed_actions: list[str] = Field(min_length=1, max_length=128)
    # These are selected filesystem roots, not unenforced descriptive scope strings.
    write_roots: list[str] = Field(default_factory=list, max_length=32)
    read_roots: list[str] = Field(default_factory=list, max_length=32)
    resource_ids: list[str] = Field(default_factory=list, max_length=128)
    expires_at: str  # An aware timestamp, or explicitly chosen NO_EXPIRY.
    role: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    role_schema: dict[str, str]
    host_profiles: list[str] = Field(min_length=1, max_length=32)
    backend_runtime: Literal["python", "java", "kotlin", "go", "rust", "cpp", "external_mcp"]
    backend_id: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_.-]{2,127}$')
    backend_version: str | None = Field(default=None, pattern=r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$')

    @model_validator(mode='after')
    def exact_backend(self):
        if (self.backend_id is None) != (self.backend_version is None):
            raise ValueError('Select both the registered backend identity and its exact version')
        return self

    @field_validator('resource_ids')
    @classmethod
    def exact_resources(cls, value):
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}', item) for item in value):
            raise ValueError('Use exact secret-free adapter resource identifiers')
        return sorted(set(value))

    @field_validator("role_schema")
    @classmethod
    def schema_fields(cls, value):
        return normalize_role_schema(value)

    @field_validator("config_env_keys")
    @classmethod
    def environment_names(cls, value):
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{2,127}", item) for item in value):
            raise ValueError("Only environment variable names can be configured")
        return sorted(set(value))

    @field_validator("capabilities", "allowed_lanes", "allowed_actions", "host_profiles")
    @classmethod
    def canonical_names(cls, value):
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", item) for item in value):
            raise ValueError("Explicit canonical identifiers are required")
        return sorted(set(value))

    @field_validator("expires_at")
    @classmethod
    def expiry(cls, value):
        if value != "NO_EXPIRY":
            _aware(value)
        return value

    @field_validator("name", "description", "purpose")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Visible descriptive text is required")
        return value.strip()


EXTENSION_MIGRATIONS = (
    Migration("extensions", 1, "Versioned extension grants and append-only events", (
        """CREATE TABLE extensions_registration (
            plugin_id TEXT PRIMARY KEY, current_version INTEGER NOT NULL,
            revoked_at TEXT)""",
        """CREATE TABLE extensions_versions (
            plugin_id TEXT NOT NULL REFERENCES extensions_registration(plugin_id),
            version INTEGER NOT NULL, registration_json TEXT NOT NULL CHECK(json_valid(registration_json)),
            digest TEXT NOT NULL, configured_at TEXT NOT NULL, actor_id TEXT NOT NULL,
            PRIMARY KEY(plugin_id,version))""",
        """CREATE TABLE extensions_events (
            sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
            plugin_id TEXT NOT NULL REFERENCES extensions_registration(plugin_id),
            event_type TEXT NOT NULL, actor_id TEXT NOT NULL, occurred_at TEXT NOT NULL,
            details_json TEXT NOT NULL CHECK(json_valid(details_json)),
            prior_digest TEXT, digest TEXT NOT NULL)""",
    )),
)


class ConnectorGovernance:
    def __init__(self, store: ProjectStore | LaneStore, *, lanes: set[str], hosts: set[str],
                 actions: set[str], max_plugins: int = 8, clock=None):
        if not 1 <= max_plugins <= 256:
            raise ValueError("The configured extension limit must be bounded")
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('receipts')
        self.lanes, self.hosts, self.actions = lanes, hosts, actions
        self.max_plugins = max_plugins
        self.clock = clock or (lambda: datetime.now(UTC))
        self.access = ProjectAccess(store, clock=self.clock)

    def initialize(self, lease: WriterLease) -> None:
        self._lease(lease)
        apply_migrations(self.store, EXTENSION_MIGRATIONS, writer=lease)
        lease.check()

    def _lease(self, lease: WriterLease) -> None:
        if lease.store.root != self.project.root:
            raise LaneError("PROJECT_BINDING_MISMATCH", "The writer belongs to another project.")
        lease.check()

    def _authorize(self, context: ActionContext, permission: str, *, path: Path | None = None):
        if context.project_id != self.store.project_id:
            raise LaneError("PROJECT_BINDING_MISMATCH", "The extension belongs to another project.")
        if permission not in context.permissions:
            raise LaneError("PERMISSION_DENIED", "The connection lacks the required permission.")
        self.access.authorize(context.client_id, permission, path=path)

    def _live(self, expiry: str) -> bool:
        return expiry == "NO_EXPIRY" or _aware(expiry) > self.clock()

    def configure(self, registration: PluginRegistration, context: ActionContext,
                  lease: WriterLease, *, expected_version: int | None = None) -> dict:
        self._lease(lease)
        self._authorize(context, "admin")
        # Revalidate trusted Python callers too; model_copy does not validate updates.
        registration = PluginRegistration.model_validate(registration.model_dump())
        if (not set(registration.allowed_lanes) <= self.lanes
                or not set(registration.host_profiles) <= self.hosts
                or not set(registration.allowed_actions) <= self.actions):
            raise LaneError("PLUGIN_SCOPE_INVALID", "Select registered lanes, actions and hosts.")
        if not self._live(registration.expires_at):
            raise LaneError("PLUGIN_GRANT_EXPIRED", "The extension grant has already expired.")
        values = registration.model_dump()
        for field, permission in (('read_roots', 'read'), ('write_roots', 'write')):
            roots = []
            for value in values[field]:
                root = Path(value).expanduser()
                if not root.is_absolute():
                    raise LaneError("PLUGIN_SCOPE_INVALID", "Select absolute extension roots.")
                root = Path(os.path.abspath(root))
                reject_links(root, Path(root.anchor))
                if not root.is_dir() or root == Path(root.anchor):
                    raise LaneError("PLUGIN_SCOPE_INVALID", "Select an existing directory below the filesystem root.")
                self._authorize(context, permission, path=root)
                roots.append(str(root.resolve(strict=True)))
            values[field] = sorted(set(roots))
        digest = hashlib.sha256(json_text(values).encode()).hexdigest()
        with lease.transaction('receipts') as connection:
            self._authorize(context, "admin")
            previous = connection.execute(
                "SELECT * FROM extensions_registration WHERE plugin_id=?", (registration.plugin_id,),
            ).fetchone()
            if previous:
                if previous["revoked_at"]:
                    raise LaneError("PLUGIN_REVOKED", "A revoked identity cannot be silently reactivated.")
                if expected_version != previous["current_version"]:
                    raise LaneError("PLUGIN_VERSION_CONFLICT", "Configuration requires the current version.")
                version = previous["current_version"] + 1
                current = connection.execute(
                    "SELECT digest FROM extensions_versions WHERE plugin_id=? AND version=?",
                    (registration.plugin_id, previous["current_version"]),
                ).fetchone()
                if current["digest"] == digest:
                    return {"plugin_id": registration.plugin_id, "version": version - 1,
                            "digest": digest, "changed": False}
            else:
                if expected_version is not None:
                    raise LaneError("PLUGIN_VERSION_CONFLICT", "The extension does not exist yet.")
                count = connection.execute(
                    "SELECT count(*) FROM extensions_registration WHERE revoked_at IS NULL",
                ).fetchone()[0]
                if count >= self.max_plugins:
                    raise LaneError("PLUGIN_LIMIT_REACHED", "Revoke an unused extension before adding another.")
                version = 1
                connection.execute("INSERT INTO extensions_registration VALUES(?,?,NULL)",
                                   (registration.plugin_id, version))
            connection.execute("INSERT INTO extensions_versions VALUES(?,?,?,?,?,?)", (
                registration.plugin_id, version, json_text(values), digest,
                self.clock().isoformat(), context.client_id,
            ))
            connection.execute("UPDATE extensions_registration SET current_version=? WHERE plugin_id=?",
                               (version, registration.plugin_id))
            self._event(connection, registration.plugin_id, "configured", context.client_id,
                        {"version": version, "registration_digest": digest})
        return {"plugin_id": registration.plugin_id, "version": version, "digest": digest,
                "changed": True}

    def revoke(self, plugin_id: str, context: ActionContext, lease: WriterLease) -> None:
        self._lease(lease)
        with lease.transaction('receipts') as connection:
            self._authorize(context, "admin")
            result = connection.execute(
                "UPDATE extensions_registration SET revoked_at=? WHERE plugin_id=? AND revoked_at IS NULL",
                (self.clock().isoformat(), plugin_id),
            )
            if result.rowcount:
                self._event(connection, plugin_id, "revoked", context.client_id, {})

    def _event(self, connection, plugin_id, kind, actor, details):
        prior = connection.execute("SELECT digest FROM extensions_events ORDER BY sequence DESC LIMIT 1").fetchone()
        payload = {"event_id": str(uuid4()), "plugin_id": plugin_id, "event_type": kind,
                   "actor_id": actor, "occurred_at": self.clock().isoformat(), "details": details,
                   "prior_digest": prior[0] if prior else None}
        digest = hashlib.sha256(json_text(payload).encode()).hexdigest()
        connection.execute(
            "INSERT INTO extensions_events(event_id,plugin_id,event_type,actor_id,occurred_at,"
            "details_json,prior_digest,digest) VALUES(?,?,?,?,?,?,?,?)",
            (payload["event_id"], plugin_id, kind, actor, payload["occurred_at"],
             json_text(details), payload["prior_digest"], digest),
        )
        self.store.append_receipt("plugin_" + kind, {"plugin_id": plugin_id, "event_digest": digest},
                                  connection=connection)

    @staticmethod
    def _registration_value(row):
        try:
            value = json.loads(row['registration_json'])
            validated = PluginRegistration.model_validate(value)
            if (hashlib.sha256(json_text(value).encode()).hexdigest() != row['digest']
                    or validated.plugin_id != row['plugin_id'] or row['current_version'] < 1):
                raise ValueError('Registration identity')
            if row['revoked_at'] is not None:
                _aware(row['revoked_at'])
        except (ValueError, TypeError, KeyError):
            raise LaneError('PLUGIN_RECORD_INTEGRITY', 'The saved registration differs from its exact identity or schema.') from None
        return validated.model_dump()

    def catalog(self, *, limit: int = 4096, after_id: str = '', active_only: bool = False,
                max_bytes: int = 4_194_304) -> list[dict]:
        if (type(limit) is not int or not 1 <= limit <= 4096 or len(after_id) > 64
                or type(active_only) is not bool or type(max_bytes) is not int or not 1024 <= max_bytes <= 4_194_304):
            raise LaneError('PLUGIN_READ_BUDGET', 'Select a bounded registration page.')
        from .migrations import read_compatibility
        read_compatibility(self.store, EXTENSION_MIGRATIONS)
        with self.store.connection(read_only=True) as connection:
            query = ("FROM extensions_registration r LEFT JOIN extensions_versions v "
                     "ON v.plugin_id=r.plugin_id AND v.version=r.current_version "
                     "WHERE r.plugin_id>? " + ('AND r.revoked_at IS NULL ' if active_only else '') +
                     "ORDER BY r.plugin_id LIMIT ?")
            metadata = connection.execute('SELECT r.plugin_id,length(CAST(v.registration_json AS BLOB)) AS bytes ' +
                query, (after_id, limit)).fetchall()
            if any(row['bytes'] is None for row in metadata):
                raise LaneError('PLUGIN_RECORD_INTEGRITY', 'A registration has no exact current version.')
            if any(not 1 <= row['bytes'] <= 262144 for row in metadata) or sum(row['bytes'] for row in metadata) > max_bytes:
                raise LaneError('PLUGIN_READ_BUDGET', 'The registration page exceeds its byte budget.')
            rows = connection.execute(
                "SELECT r.*,v.registration_json,v.digest " + query, (after_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            value = self._registration_value(row)
            result.append(value | {
                'version': row['current_version'], 'digest': row['digest'],
                'active': row['revoked_at'] is None, 'grant_live': self._live(value['expires_at']),
            })
        return result

    def active_catalog(self) -> list[dict]:
        """Resolve the complete bounded active set, independently of history pages."""
        rows = self.catalog(limit=self.max_plugins + 1, active_only=True)
        if len(rows) > self.max_plugins:
            raise LaneError('PLUGIN_ACTIVE_SET_INVALID', 'The active registration set exceeds its configured bound.')
        return rows

    def route(self, *, capability: str, action: str, lane: str, host: str,
              preferred: str | None = None) -> dict:
        trace, eligible = [], []
        for plugin in self.active_catalog():
            checks = [plugin["active"], plugin["grant_live"],
                      capability in plugin["capabilities"] and action in plugin["allowed_actions"],
                      lane in plugin["allowed_lanes"], host in plugin["host_profiles"]]
            trace.append({"plugin_id": plugin["plugin_id"], "guards": dict(zip(ROUTE_GUARD_ORDER, checks, strict=True)),
                          "eligible": all(checks)})
            if all(checks):
                eligible.append(plugin)
        selected = None
        if preferred is not None:
            selected = next((item for item in eligible if item["plugin_id"] == preferred), None)
            decision = "selected" if selected else "requested_plugin_denied"
        elif len(eligible) == 1:
            selected, decision = eligible[0], "selected"
        else:
            decision = "ambiguous" if eligible else "unavailable"
        return {"decision": decision, "selected": selected, "guard_order": list(ROUTE_GUARD_ORDER),
                "guard_trace": trace, "backend_execution_authorized": False}

    def authorize_execution(self, *, context: ActionContext, lease: WriterLease,
                            plugin_id: str, version: int, capability: str, action: str,
                            lane: str, host: str, permission: str, runtime_ready: bool,
                            path: Path | None = None) -> dict:
        """Called by trusted engine routing immediately before a prepared job effect.

        Host and runtime readiness come from engine probes, never tool arguments.
        The job coordinator must also enforce cancellation and Plan revision fencing.
        """
        self._lease(lease)
        self._authorize(context, "tools")
        self._authorize(context, permission, path=path)
        route = self.route(capability=capability, action=action, lane=lane, host=host, preferred=plugin_id)
        selected = route["selected"]
        if selected is None or selected["version"] != version:
            raise LaneError("PLUGIN_ROUTE_DENIED", "The selected extension grant changed or no longer permits this operation.")
        if not runtime_ready:
            raise LaneError("PLUGIN_RUNTIME_UNAVAILABLE", "The selected backend has not passed its readiness probe.")
        if permission in {"write", "publish"}:
            if path is None:
                raise LaneError("PLUGIN_WRITE_SCOPE_REQUIRED", "A write needs an exact scoped target.")
            target = Path(os.path.abspath(path.expanduser()))
            reject_links(target, Path(target.anchor))
            if not any(target.resolve(strict=False).is_relative_to(Path(root)) for root in selected["write_roots"]):
                raise LaneError("PLUGIN_WRITE_SCOPE_DENIED", "The target is outside the extension write grant.")
        lease.check()
        return selected

    @staticmethod
    def validate_role_output(plugin: dict, output: dict) -> str:
        """Validate data, returning only its digest for a secret-free execution receipt."""
        schema = plugin["role_schema"]
        try:
            if not isinstance(output, dict) or set(output) != set(schema):
                raise ValueError()
            payload = json_text(output).encode()
            if len(payload) > 1_048_576:
                raise ValueError()
            for field, kind in schema.items():
                value = output[field]
                valid = {
                    "text": lambda item: type(item) is str,
                    "integer": lambda item: type(item) is int,
                    "number": lambda item: type(item) in {int, float} and math.isfinite(item),
                    "boolean": lambda item: type(item) is bool,
                    "datetime": lambda item: type(item) is str and bool(_aware(item)),
                    "json": lambda item: True,
                    "blob_hash": lambda item: type(item) is str and bool(re.fullmatch(r"[0-9a-f]{64}", item)),
                }[kind](value)
                if not valid:
                    raise ValueError()
            return hashlib.sha256(payload).hexdigest()
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise LaneError("PLUGIN_ROLE_OUTPUT_INVALID", "The extension output violates its role schema.") from None


class ConnectorRead(Contract):
    after_id: str = Field(default='', max_length=64)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65536, ge=1024, le=262144)


class ConnectorPage(Contract):
    project_id: str
    registrations: list[dict[str, JsonValue]]
    next_after_id: str | None
    truncated: bool
    scopes: dict[str, JsonValue]
    backend_execution_authorized: Literal[False] = False


class ConnectorConfigure(Contract):
    registration: PluginRegistration
    expected_version: int | None = Field(default=None, ge=1)


class ConnectorConfigured(Contract):
    plugin_id: str
    version: int
    digest: str
    changed: bool
    backend_execution_authorized: Literal[False] = False


class ConnectorRevoke(Contract):
    plugin_id: str = Field(pattern=r'^[a-z][a-z0-9-]{2,63}$')
    expected_version: int = Field(ge=1)
    expected_digest: str = Field(pattern=r'^[0-9a-f]{64}$')


class ConnectorRevoked(Contract):
    plugin_id: str
    version: int
    digest: str
    revoked: Literal[True] = True
    changed: bool


def connector_service(engine, store):
    from .custom_lanes import registered_lane_ids
    from .host_routing import HOST_MATRIX
    from .lanes import CANONICAL_LANE_IDS
    actions = engine.registry.schemas()
    return ConnectorGovernance(store, lanes=set(CANONICAL_LANE_IDS) | set(registered_lane_ids(store)),
        hosts=set(HOST_MATRIX), actions={item['name'] for item in actions})


def register_connector_actions(engine):
    def read(context, request):
        store = engine.directory.open(context.project_id)
        with store.lane('receipts').connection(read_only=True) as connection:
            present = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='extensions_registration' AND type='table'").fetchone()
        service = connector_service(engine, store)
        rows = service.catalog(limit=request.limit + 1, after_id=request.after_id, max_bytes=request.max_bytes) if present else []
        selected = rows[:request.limit]
        result = ConnectorPage(project_id=store.project_id, registrations=selected,
            next_after_id=selected[-1]['plugin_id'] if len(rows) > request.limit else None,
            truncated=len(rows) > request.limit,
            scopes={'lanes': sorted(service.lanes), 'actions': sorted(service.actions), 'hosts': sorted(service.hosts),
                    'action_profiles': sorted({row['profile'] for row in engine.registry.schemas()}),
                    'adapters': [dict(operation=row['name'], profile=row['profile'], route_id=route['route_id'], **route['extension'])
                        for row in engine.registry.schemas() for route in row['toolchain']['routes'] if route['extension']],
                    'readiness_action': 'toolchain_resolve', 'registration_loads_code': False})
        if len(result.model_dump_json().encode()) > request.max_bytes:
            raise LaneError('PLUGIN_READ_BUDGET', 'Read fewer registrations or increase the bounded output budget.')
        return result

    def configure(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            service = connector_service(engine, store)
            with lease.transaction('receipts'):
                service.initialize(lease)
                return service.configure(request.registration, context, lease, expected_version=request.expected_version)

    def revoke(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            service = connector_service(engine, store)
            service.initialize(lease)
            # The writer excludes reconfiguration between read and revoke. Bind
            # the user's exact reviewed version, including an idempotent replay.
            with service.store.connection(read_only=True) as connection:
                row = connection.execute('SELECT r.current_version,r.revoked_at,v.digest FROM extensions_registration r '
                    'JOIN extensions_versions v ON v.plugin_id=r.plugin_id AND v.version=r.current_version WHERE r.plugin_id=?',
                    (request.plugin_id,)).fetchone()
            if row is None:
                raise LaneError('PLUGIN_NOT_FOUND', 'The selected registration does not exist.')
            if row['current_version'] != request.expected_version or row['digest'] != request.expected_digest:
                raise LaneError('PLUGIN_VERSION_CONFLICT', 'Read the current registration before revoking it.')
            service.revoke(request.plugin_id, context, lease)
            return ConnectorRevoked(plugin_id=request.plugin_id, version=row['current_version'], digest=row['digest'], changed=row['revoked_at'] is None)

    engine.registry.register(ActionSpec('connector_read', 'Inspect a bounded project registration page and current scope choices without activating a backend.',
        ConnectorRead, ConnectorPage, read, profile='extensions', workflow='inspect-project-connectors', studio_read=True))
    engine.registry.register(ActionSpec('connector_configure', 'Add or configure one versioned project extension grant without executing its backend.',
        ConnectorConfigure, ConnectorConfigured, configure, permission='admin', profile='extensions', workflow='configure-project-connector', mutates=True))
    engine.registry.register(ActionSpec('connector_revoke', 'Revoke the exact reviewed project extension version while preserving its history.',
        ConnectorRevoke, ConnectorRevoked, revoke, permission='admin', profile='extensions', workflow='revoke-project-connector', mutates=True))
