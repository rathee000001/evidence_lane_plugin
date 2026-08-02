"""Append-only governance for bounded connector and AI-toolchain plugins."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .lanes import CANONICAL_LANE_IDS
from .timeutil import utc_now

MAX_ADDITIONAL_PERSISTENT_PLUGINS = 8
_PLUGIN_ID = re.compile(r"[a-z][a-z0-9-]{2,63}")
_ENV_KEY = re.compile(r"[A-Z][A-Z0-9_]{2,127}")
_ROLE_ID = re.compile(r"[a-z][a-z0-9_-]{2,63}")
_SCHEMA_FIELD = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SCHEMA_FIELD_TYPES = {
    "text",
    "integer",
    "number",
    "boolean",
    "datetime",
    "json",
    "blob_hash",
}
_HOST_PROFILE_ORDER = ("CODEX", "CHATGPT")
_HOST_PROFILES = set(_HOST_PROFILE_ORDER)
_BACKEND_RUNTIMES = {
    "python",
    "java",
    "kotlin",
    "go",
    "rust",
    "cpp",
    "external_mcp",
}
_SECRET_SCHEMA_PARTS = {"password", "secret", "token", "api_key", "credential"}


class ConnectorGovernance:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS plugin_registration(
                plugin_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                plugin_kind TEXT NOT NULL CHECK(plugin_kind IN ('connector','toolchain')),
                description TEXT NOT NULL,
                config_env_keys_json TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                allowed_lanes_json TEXT NOT NULL,
                purpose TEXT NOT NULL DEFAULT '',
                allowed_actions_json TEXT NOT NULL DEFAULT '[]',
                write_scope_json TEXT NOT NULL DEFAULT '[]',
                expires_at TEXT NOT NULL DEFAULT 'NO_EXPIRY',
                role TEXT NOT NULL DEFAULT '',
                role_schema_json TEXT NOT NULL DEFAULT '{}',
                host_profiles_json TEXT NOT NULL DEFAULT '["CODEX","CHATGPT"]',
                backend_runtime TEXT NOT NULL DEFAULT 'python',
                registered_at TEXT NOT NULL,
                dropped_at TEXT,
                status TEXT NOT NULL CHECK(status IN ('ACTIVE','DROPPED')),
                registration_sha256 TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS plugin_event(
                event_id TEXT PRIMARY KEY,
                plugin_id TEXT NOT NULL REFERENCES plugin_registration(plugin_id),
                event_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                prior_event_sha256 TEXT,
                event_sha256 TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS route_decision(
                route_id TEXT PRIMARY KEY,
                requested_capability TEXT NOT NULL,
                canonical_lane_id TEXT,
                selected_plugin_id TEXT REFERENCES plugin_registration(plugin_id),
                decision TEXT NOT NULL,
                host_profile TEXT NOT NULL DEFAULT 'CODEX',
                role_schema_sha256 TEXT,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS role_schema_field(
                plugin_id TEXT NOT NULL REFERENCES plugin_registration(plugin_id),
                field_order INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                field_type TEXT NOT NULL,
                PRIMARY KEY(plugin_id, field_name),
                UNIQUE(plugin_id, field_order)
            ) STRICT;
            CREATE VIRTUAL TABLE IF NOT EXISTS plugin_fts USING fts5(
                plugin_id UNINDEXED,
                name,
                description,
                capabilities,
                tokenize='unicode61'
            );
            """
        )
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(plugin_registration)")
        }
        migrations = {
            "purpose": "TEXT NOT NULL DEFAULT ''",
            "allowed_actions_json": "TEXT NOT NULL DEFAULT '[]'",
            "write_scope_json": "TEXT NOT NULL DEFAULT '[]'",
            "expires_at": "TEXT NOT NULL DEFAULT 'NO_EXPIRY'",
            "role": "TEXT NOT NULL DEFAULT ''",
            "role_schema_json": "TEXT NOT NULL DEFAULT '{}'",
            "host_profiles_json": "TEXT NOT NULL DEFAULT '[\"CODEX\",\"CHATGPT\"]'",
            "backend_runtime": "TEXT NOT NULL DEFAULT 'python'",
        }
        for name, declaration in migrations.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE plugin_registration ADD COLUMN {name} {declaration}"
                )
        route_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(route_decision)")
        }
        route_migrations = {
            "host_profile": "TEXT NOT NULL DEFAULT 'CODEX'",
            "role_schema_sha256": "TEXT",
        }
        for name, declaration in route_migrations.items():
            if name not in route_columns:
                connection.execute(
                    f"ALTER TABLE route_decision ADD COLUMN {name} {declaration}"
                )
        default_schema = self._normalize_role_schema(None)
        connection.execute(
            "UPDATE plugin_registration SET role=plugin_kind WHERE role=''"
        )
        connection.execute(
            "UPDATE plugin_registration SET role_schema_json=? WHERE role_schema_json='{}'",
            (json.dumps(default_schema, sort_keys=True, separators=(",", ":")),),
        )
        for row in connection.execute(
            "SELECT plugin_id,role_schema_json FROM plugin_registration"
        ):
            existing_field_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM role_schema_field WHERE plugin_id=?",
                    (row["plugin_id"],),
                ).fetchone()[0]
            )
            if existing_field_count:
                continue
            schema = self._normalize_role_schema(
                json.loads(row["role_schema_json"]) or None
            )
            connection.executemany(
                """
                INSERT INTO role_schema_field(
                    plugin_id, field_order, field_name, field_type
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (row["plugin_id"], index, field, field_type)
                    for index, (field, field_type) in enumerate(
                        schema.items(), start=1
                    )
                ],
            )
        for route in connection.execute(
            """
            SELECT route_id,selected_plugin_id
            FROM route_decision
            WHERE selected_plugin_id IS NOT NULL
              AND role_schema_sha256 IS NULL
            """
        ):
            registration = connection.execute(
                "SELECT role_schema_json FROM plugin_registration WHERE plugin_id=?",
                (route["selected_plugin_id"],),
            ).fetchone()
            if registration is None:
                continue
            schema = json.loads(registration["role_schema_json"])
            connection.execute(
                "UPDATE route_decision SET role_schema_sha256=? WHERE route_id=?",
                (
                    sha256_bytes(canonical_json_bytes(schema)),
                    route["route_id"],
                ),
            )
        connection.commit()
        return connection

    @staticmethod
    def _expiry_is_live(value: str) -> bool:
        if value == "NO_EXPIRY":
            return True
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed > datetime.now(UTC)

    @staticmethod
    def _normalize_role_schema(value: dict[str, str] | None) -> dict[str, str]:
        if value is None:
            return {"evidence_ref": "blob_hash", "lane_id": "text"}
        require(
            isinstance(value, dict),
            "PLUGIN_ROLE_SCHEMA_INVALID",
            "A governed role schema must map field names to deterministic types.",
            status="BLOCKED",
        )
        schema: dict[str, str] = {}
        for raw_field, raw_type in value.items():
            field = str(raw_field).strip()
            field_type = str(raw_type).strip().lower()
            require(
                bool(_SCHEMA_FIELD.fullmatch(field))
                and not any(part in field for part in _SECRET_SCHEMA_PARTS),
                "PLUGIN_ROLE_SCHEMA_FIELD_INVALID",
                "Role-schema fields must be lowercase data fields and cannot define credential storage.",
                status="BLOCKED",
                field=field,
            )
            require(
                field_type in _SCHEMA_FIELD_TYPES,
                "PLUGIN_ROLE_SCHEMA_TYPE_INVALID",
                "Role-schema fields must use a supported deterministic type.",
                status="BLOCKED",
                field=field,
                allowed_types=sorted(_SCHEMA_FIELD_TYPES),
            )
            schema[field] = field_type
        require(
            bool(schema),
            "PLUGIN_ROLE_SCHEMA_REQUIRED",
            "A governed plugin role requires at least one schema field.",
            status="BLOCKED",
        )
        return dict(sorted(schema.items()))

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        *,
        plugin_id: str,
        event_type: str,
        actor: str,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        prior = connection.execute(
            "SELECT event_sha256 FROM plugin_event ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        payload = {
            "event_id": prefixed_id("pluginevt"),
            "plugin_id": plugin_id,
            "event_type": event_type,
            "actor": actor,
            "occurred_at": utc_now(),
            "details": details,
            "prior_event_sha256": str(prior[0]) if prior else None,
        }
        payload["event_sha256"] = sha256_bytes(canonical_json_bytes(payload))
        connection.execute(
            """
            INSERT INTO plugin_event(
                event_id, plugin_id, event_type, actor, occurred_at,
                details_json, prior_event_sha256, event_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["event_id"],
                plugin_id,
                event_type,
                actor,
                payload["occurred_at"],
                json.dumps(details, sort_keys=True, separators=(",", ":")),
                payload["prior_event_sha256"],
                payload["event_sha256"],
            ),
        )
        return payload

    def register(
        self,
        *,
        plugin_id: str,
        name: str,
        plugin_kind: str,
        description: str,
        config_env_keys: list[str],
        capabilities: list[str],
        allowed_lanes: list[str],
        registered_by: str,
        purpose: str | None = None,
        allowed_actions: list[str] | None = None,
        write_scope: list[str] | None = None,
        expires_at: str = "NO_EXPIRY",
        role: str | None = None,
        role_schema: dict[str, str] | None = None,
        host_profiles: list[str] | None = None,
        backend_runtime: str = "python",
    ) -> dict[str, Any]:
        exact_id = plugin_id.strip().lower()
        exact_kind = plugin_kind.strip().lower()
        require(
            bool(_PLUGIN_ID.fullmatch(exact_id)),
            "PLUGIN_ID_INVALID",
            "A governed plugin ID must be a lowercase kebab-case identifier.",
            status="BLOCKED",
        )
        require(
            exact_kind in {"connector", "toolchain"},
            "PLUGIN_KIND_INVALID",
            "A governed plugin must be a connector or toolchain.",
            status="BLOCKED",
        )
        keys = list(
            dict.fromkeys(item.strip() for item in config_env_keys if item.strip())
        )
        require(
            all(_ENV_KEY.fullmatch(item) for item in keys),
            "PLUGIN_CONFIG_SECRET_VALUE_FORBIDDEN",
            "Plugin governance stores environment variable names only, never values.",
            status="BLOCKED",
        )
        exact_capabilities = list(
            dict.fromkeys(item.strip() for item in capabilities if item.strip())
        )
        lanes = list(
            dict.fromkeys(item.strip() for item in allowed_lanes if item.strip())
        )
        exact_purpose = (purpose or description).strip()
        actions = list(
            dict.fromkeys(
                item.strip()
                for item in (allowed_actions or exact_capabilities)
                if item.strip()
            )
        )
        scopes = list(
            dict.fromkeys(
                item.strip()
                for item in (write_scope or [f"lane:{item}" for item in lanes])
                if item.strip()
            )
        )
        exact_expiry = expires_at.strip() or "NO_EXPIRY"
        exact_role = (role or exact_kind).strip().lower()
        schema = self._normalize_role_schema(role_schema)
        requested_profiles = host_profiles or list(_HOST_PROFILE_ORDER)
        profile_set = {
            str(item).strip().upper() for item in requested_profiles if str(item).strip()
        }
        profiles = [item for item in _HOST_PROFILE_ORDER if item in profile_set]
        exact_backend = backend_runtime.strip().lower()
        require(
            bool(exact_capabilities)
            and bool(lanes)
            and set(lanes) <= set(CANONICAL_LANE_IDS),
            "PLUGIN_CAPABILITY_OR_LANE_INVALID",
            "A governed plugin needs capabilities and canonical allowed lanes.",
            status="BLOCKED",
            allowed_lanes=list(CANONICAL_LANE_IDS),
        )
        require(
            bool(exact_purpose)
            and bool(actions)
            and bool(scopes)
            and self._expiry_is_live(exact_expiry),
            "PLUGIN_GRANT_INVALID_OR_EXPIRED",
            "A persistent plugin needs a visible purpose, actions, write scope, and live expiry.",
            status="BLOCKED",
        )
        require(
            bool(_ROLE_ID.fullmatch(exact_role)),
            "PLUGIN_ROLE_INVALID",
            "A governed plugin role must be a lowercase identifier.",
            status="BLOCKED",
        )
        require(
            bool(profiles) and profile_set <= _HOST_PROFILES,
            "PLUGIN_HOST_PROFILE_INVALID",
            "A governed plugin must target CODEX, CHATGPT, or both.",
            status="BLOCKED",
            allowed_profiles=list(_HOST_PROFILE_ORDER),
        )
        require(
            exact_backend in _BACKEND_RUNTIMES,
            "PLUGIN_BACKEND_RUNTIME_INVALID",
            "The optional backend runtime is not governed by this release.",
            status="BLOCKED",
            allowed_runtimes=sorted(_BACKEND_RUNTIMES),
        )
        role_schema_sha256 = sha256_bytes(canonical_json_bytes(schema))
        registration = {
            "plugin_id": exact_id,
            "name": name.strip(),
            "plugin_kind": exact_kind,
            "description": description.strip(),
            "config_env_keys": keys,
            "capabilities": exact_capabilities,
            "allowed_lanes": lanes,
            "purpose": exact_purpose,
            "allowed_actions": actions,
            "write_scope": scopes,
            "expires_at": exact_expiry,
            "role": exact_role,
            "role_schema": schema,
            "role_schema_sha256": role_schema_sha256,
            "host_profiles": profiles,
            "backend_runtime": exact_backend,
        }
        require(
            bool(registration["name"]) and bool(registration["description"]),
            "PLUGIN_DESCRIPTION_REQUIRED",
            "Plugin governance requires a visible name and description.",
            status="BLOCKED",
        )
        registration_sha256 = sha256_bytes(canonical_json_bytes(registration))
        v083_registration = {
            key: registration[key]
            for key in (
                "plugin_id",
                "name",
                "plugin_kind",
                "description",
                "config_env_keys",
                "capabilities",
                "allowed_lanes",
                "purpose",
                "allowed_actions",
                "write_scope",
                "expires_at",
            )
        }
        v083_sha256 = sha256_bytes(canonical_json_bytes(v083_registration))
        compatibility_profile = (
            role is None
            and role_schema is None
            and host_profiles is None
            and exact_backend == "python"
        )
        legacy_registration = {
            key: registration[key]
            for key in (
                "plugin_id",
                "name",
                "plugin_kind",
                "description",
                "config_env_keys",
                "capabilities",
                "allowed_lanes",
            )
        }
        legacy_sha256 = sha256_bytes(canonical_json_bytes(legacy_registration))
        connection = self._connect()
        try:
            existing = connection.execute(
                "SELECT * FROM plugin_registration WHERE plugin_id=?", (exact_id,)
            ).fetchone()
            if existing:
                require(
                    (
                        existing["registration_sha256"] == registration_sha256
                        or (
                            compatibility_profile
                            and existing["registration_sha256"]
                            in {v083_sha256, legacy_sha256}
                        )
                    )
                    and existing["status"] == "ACTIVE",
                    "PLUGIN_REGISTRATION_CONFLICT",
                    "The plugin ID already binds different or dropped governance bytes.",
                    status="BLOCKED",
                    plugin_id=exact_id,
                )
                return {
                    "status": "PASS",
                    "idempotent": True,
                    **registration,
                    "legacy_registration_reused": existing[
                        "registration_sha256"
                    ]
                    in {v083_sha256, legacy_sha256},
                }
            active_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM plugin_registration WHERE status='ACTIVE'"
                ).fetchone()[0]
            )
            require(
                active_count < MAX_ADDITIONAL_PERSISTENT_PLUGINS,
                "PLUGIN_PERSISTENT_LIMIT_REACHED",
                "At most eight additional persistent plugins may be active.",
                status="BLOCKED",
                maximum=MAX_ADDITIONAL_PERSISTENT_PLUGINS,
                active=active_count,
            )
            registered_at = utc_now()
            connection.execute(
                """
                INSERT INTO plugin_registration(
                    plugin_id, name, plugin_kind, description, config_env_keys_json,
                    capabilities_json, allowed_lanes_json, purpose,
                    allowed_actions_json, write_scope_json, expires_at, role,
                    role_schema_json, host_profiles_json, backend_runtime,
                    registered_at, dropped_at, status, registration_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'ACTIVE', ?)
                """,
                (
                    exact_id,
                    registration["name"],
                    exact_kind,
                    registration["description"],
                    json.dumps(keys, separators=(",", ":")),
                    json.dumps(exact_capabilities, separators=(",", ":")),
                    json.dumps(lanes, separators=(",", ":")),
                    exact_purpose,
                    json.dumps(actions, separators=(",", ":")),
                    json.dumps(scopes, separators=(",", ":")),
                    exact_expiry,
                    exact_role,
                    json.dumps(schema, sort_keys=True, separators=(",", ":")),
                    json.dumps(profiles, separators=(",", ":")),
                    exact_backend,
                    registered_at,
                    registration_sha256,
                ),
            )
            connection.execute(
                "INSERT INTO plugin_fts(plugin_id,name,description,capabilities) VALUES(?,?,?,?)",
                (
                    exact_id,
                    registration["name"],
                    registration["description"],
                    " ".join([*exact_capabilities, exact_role, exact_backend]),
                ),
            )
            connection.executemany(
                """
                INSERT INTO role_schema_field(
                    plugin_id, field_order, field_name, field_type
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (exact_id, index, field, field_type)
                    for index, (field, field_type) in enumerate(schema.items(), start=1)
                ],
            )
            event = self._event(
                connection,
                plugin_id=exact_id,
                event_type="REGISTER",
                actor=registered_by,
                details={
                    "registration_sha256": registration_sha256,
                    "purpose_sha256": sha256_bytes(exact_purpose.encode("utf-8")),
                    "role": exact_role,
                    "role_schema_sha256": role_schema_sha256,
                    "host_profiles": profiles,
                    "backend_runtime": exact_backend,
                },
            )
            connection.commit()
            return {
                "status": "PASS",
                "idempotent": False,
                **registration,
                "registration_sha256": registration_sha256,
                "event": event,
                "active_after": active_count + 1,
                "maximum": MAX_ADDITIONAL_PERSISTENT_PLUGINS,
                "secret_values_persisted": False,  # nosec B105
                "purpose_recorded_once": True,
                "backend_execution_authorized": False,
            }
        finally:
            connection.close()

    def drop(
        self, *, plugin_id: str, confirmation: str, dropped_by: str
    ) -> dict[str, Any]:
        exact_id = plugin_id.strip().lower()
        require(
            confirmation == f"DROP:{exact_id}",
            "PLUGIN_DROP_CONFIRMATION_INVALID",
            "Dropping a governed plugin requires the exact token DROP:<plugin-id>.",
            status="BLOCKED",
            required=f"DROP:{exact_id}",
        )
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT status FROM plugin_registration WHERE plugin_id=?", (exact_id,)
            ).fetchone()
            require(
                row is not None and row["status"] == "ACTIVE",
                "PLUGIN_DROP_TARGET_INVALID",
                "Only one active governed plugin can be dropped.",
                status="BLOCKED",
                plugin_id=exact_id,
            )
            dropped_at = utc_now()
            connection.execute(
                "UPDATE plugin_registration SET status='DROPPED', dropped_at=? WHERE plugin_id=?",
                (dropped_at, exact_id),
            )
            event = self._event(
                connection,
                plugin_id=exact_id,
                event_type="DROP",
                actor=dropped_by,
                details={
                    "confirmation_sha256": sha256_bytes(confirmation.encode("utf-8"))
                },
            )
            connection.commit()
            return {
                "status": "PASS",
                "plugin_id": exact_id,
                "plugin_status": "DROPPED",
                "history_preserved": True,
                "event": event,
            }
        finally:
            connection.close()

    def catalog(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            rows = [
                {
                    **dict(row),
                    "config_env_keys": json.loads(row["config_env_keys_json"]),
                    "capabilities": json.loads(row["capabilities_json"]),
                    "allowed_lanes": json.loads(row["allowed_lanes_json"]),
                    "allowed_actions": json.loads(row["allowed_actions_json"]),
                    "write_scope": json.loads(row["write_scope_json"]),
                    "role_schema": json.loads(row["role_schema_json"]),
                    "host_profiles": json.loads(row["host_profiles_json"]),
                }
                for row in connection.execute(
                    "SELECT * FROM plugin_registration ORDER BY registered_at, plugin_id"
                )
            ]
            for row in rows:
                row.pop("config_env_keys_json", None)
                row.pop("capabilities_json", None)
                row.pop("allowed_lanes_json", None)
                row.pop("allowed_actions_json", None)
                row.pop("write_scope_json", None)
                row.pop("role_schema_json", None)
                row.pop("host_profiles_json", None)
                if not row["purpose"]:
                    row["purpose"] = row["description"]
                if not row["allowed_actions"]:
                    row["allowed_actions"] = list(row["capabilities"])
                if not row["write_scope"]:
                    row["write_scope"] = [
                        f"lane:{lane}" for lane in row["allowed_lanes"]
                    ]
                if not row["role"]:
                    row["role"] = row["plugin_kind"]
                if not row["role_schema"]:
                    row["role_schema"] = self._normalize_role_schema(None)
                if not row["host_profiles"]:
                    row["host_profiles"] = list(_HOST_PROFILE_ORDER)
                row["role_schema_sha256"] = sha256_bytes(
                    canonical_json_bytes(row["role_schema"])
                )
                row["grant_live"] = self._expiry_is_live(row["expires_at"])
            integrity = [
                item[0] for item in connection.execute("PRAGMA integrity_check")
            ]
            foreign_keys = [
                dict(item) for item in connection.execute("PRAGMA foreign_key_check")
            ]
            return {
                "status": "PASS"
                if integrity == ["ok"] and not foreign_keys
                else "FAIL",
                "maximum_active": MAX_ADDITIONAL_PERSISTENT_PLUGINS,
                "active_count": sum(1 for row in rows if row["status"] == "ACTIVE"),
                "routable_count": sum(
                    1
                    for row in rows
                    if row["status"] == "ACTIVE" and row["grant_live"]
                ),
                "registrations": rows,
                "integrity": integrity,
                "foreign_key_errors": foreign_keys,
                "secret_values_persisted": False,  # nosec B105
                "available_slots": MAX_ADDITIONAL_PERSISTENT_PLUGINS
                - sum(1 for row in rows if row["status"] == "ACTIVE"),
                "host_profiles": list(_HOST_PROFILE_ORDER),
                "supported_backend_runtimes": sorted(_BACKEND_RUNTIMES),
                "backend_execution_authorized": False,
            }
        finally:
            connection.close()

    def settings(self, *, host_profile: str) -> dict[str, Any]:
        exact_host = host_profile.strip().upper()
        require(
            exact_host in _HOST_PROFILES,
            "PLUGIN_HOST_PROFILE_INVALID",
            "Connector settings require CODEX or CHATGPT.",
            status="BLOCKED",
            allowed_profiles=list(_HOST_PROFILE_ORDER),
        )
        catalog = self.catalog()
        active = [
            row
            for row in catalog["registrations"]
            if row["status"] == "ACTIVE" and exact_host in row["host_profiles"]
        ]
        active.sort(key=lambda row: row["plugin_id"])
        slots: list[dict[str, Any]] = []
        for index in range(MAX_ADDITIONAL_PERSISTENT_PLUGINS):
            plugin = active[index] if index < len(active) else None
            slots.append(
                {
                    "slot": index + 1,
                    "state": "CONFIGURED" if plugin else "AVAILABLE",
                    "plugin_id": plugin["plugin_id"] if plugin else None,
                    "role": plugin["role"] if plugin else None,
                    "backend_runtime": plugin["backend_runtime"] if plugin else None,
                    "role_schema_sha256": (
                        plugin["role_schema_sha256"] if plugin else None
                    ),
                }
            )
        return {
            "status": "PASS",
            "surface": "EVI_CONNECTOR_SETTINGS",
            "host_profile": exact_host,
            "slots": slots,
            "configured_count": len(active),
            "maximum": MAX_ADDITIONAL_PERSISTENT_PLUGINS,
            "profiles_are_independent": True,
            "registration_command": "/evi-plugin ADD:",
            "drop_confirmation": "DROP:<plugin-id>",
            "credentials": "HOST_MANAGED_ENVIRONMENT_NAMES_ONLY",
            "role_schema_types": sorted(_SCHEMA_FIELD_TYPES),
            "backend_runtimes": sorted(_BACKEND_RUNTIMES),
            "backend_execution_authorized": False,
        }

    def route(
        self,
        *,
        capability: str,
        canonical_lane_id: str | None = None,
        host_profile: str = "CODEX",
    ) -> dict[str, Any]:
        exact_host = host_profile.strip().upper()
        require(
            exact_host in _HOST_PROFILES,
            "PLUGIN_HOST_PROFILE_INVALID",
            "Plugin routing requires CODEX or CHATGPT.",
            status="BLOCKED",
            allowed_profiles=list(_HOST_PROFILE_ORDER),
        )
        if (
            canonical_lane_id is not None
            and canonical_lane_id not in CANONICAL_LANE_IDS
        ):
            raise EvidenceLaneError(
                "PLUGIN_ROUTE_LANE_INVALID",
                "Plugin routing requires one canonical lane.",
                status="BLOCKED",
            )
        catalog = self.catalog()
        matches = [
            row
            for row in catalog["registrations"]
            if row["status"] == "ACTIVE"
            and row["grant_live"]
            and capability in row["capabilities"]
            and capability in row["allowed_actions"]
            and exact_host in row["host_profiles"]
            and (canonical_lane_id is None or canonical_lane_id in row["allowed_lanes"])
        ]
        matches.sort(key=lambda row: row["plugin_id"])
        selected = matches[0]["plugin_id"] if matches else None
        decision = (
            "PERSISTENT_PLUGIN" if selected else "BUILTIN_OR_FAIL_CLOSED_FALLBACK"
        )
        connection = self._connect()
        try:
            route_id = prefixed_id("pluginroute")
            connection.execute(
                """
                INSERT INTO route_decision(
                    route_id, requested_capability, canonical_lane_id,
                    selected_plugin_id, decision, host_profile,
                    role_schema_sha256, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    route_id,
                    capability,
                    canonical_lane_id,
                    selected,
                    decision,
                    exact_host,
                    matches[0]["role_schema_sha256"] if matches else None,
                    utc_now(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        return {
            "status": "PASS",
            "route_id": route_id,
            "requested_capability": capability,
            "canonical_lane_id": canonical_lane_id,
            "host_profile": exact_host,
            "selected_plugin_id": selected,
            "decision": decision,
            "deterministic_order": [row["plugin_id"] for row in matches],
            "selected_role": matches[0]["role"] if matches else None,
            "selected_role_schema": matches[0]["role_schema"] if matches else None,
            "selected_role_schema_sha256": (
                matches[0]["role_schema_sha256"] if matches else None
            ),
            "selected_backend_runtime": (
                matches[0]["backend_runtime"] if matches else None
            ),
            "backend_execution_authorized": False,
        }


def validate_connector_brain(path: str | Path) -> dict[str, Any]:
    """Validate one packaged connector brain without creating or mutating it."""

    target = Path(path).resolve()
    require(
        target.is_file(),
        "CONNECTOR_BRAIN_MISSING",
        "The packaged connector brain does not exist.",
        status="FAIL",
    )
    connection = sqlite3.connect(
        f"file:{target.as_posix()}?mode=ro&immutable=1", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
            )
        }
        required_tables = {
            "plugin_registration",
            "plugin_event",
            "route_decision",
            "plugin_fts",
        }
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_keys = [
            dict(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        rows = list(
            connection.execute("SELECT * FROM plugin_registration")
        )
        registration_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(plugin_registration)")
        }
        governed_grant_columns = {
            "purpose",
            "allowed_actions_json",
            "write_scope_json",
            "expires_at",
        }
        governed_grants_present = governed_grant_columns <= registration_columns
        role_profile_columns = {
            "role",
            "role_schema_json",
            "host_profiles_json",
            "backend_runtime",
        }
        role_profiles_present = role_profile_columns <= registration_columns
        role_schema_table_present = "role_schema_field" in tables
        route_columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(route_decision)")
        }
        route_profiles_present = {
            "host_profile",
            "role_schema_sha256",
        } <= route_columns
        active_count = sum(1 for row in rows if row["status"] == "ACTIVE")
        config_keys_valid = True
        governed_grants_valid = True
        role_profiles_valid = True
        expected_role_field_count = 0
        for row in rows:
            try:
                keys = json.loads(row["config_env_keys_json"])
            except (TypeError, json.JSONDecodeError):
                config_keys_valid = False
                break
            if governed_grants_present:
                try:
                    actions = json.loads(row["allowed_actions_json"])
                    scopes = json.loads(row["write_scope_json"])
                except (TypeError, json.JSONDecodeError):
                    governed_grants_valid = False
                    break
                if (
                    not str(row["purpose"]).strip()
                    or not isinstance(actions, list)
                    or not actions
                    or not isinstance(scopes, list)
                    or not scopes
                    or not ConnectorGovernance._expiry_is_live(
                        str(row["expires_at"])
                    )
                ):
                    governed_grants_valid = False
                    break
            if role_profiles_present:
                try:
                    schema = json.loads(row["role_schema_json"])
                    profiles = json.loads(row["host_profiles_json"])
                except (TypeError, json.JSONDecodeError):
                    role_profiles_valid = False
                    break
                if not _ROLE_ID.fullmatch(str(row["role"])):
                    role_profiles_valid = False
                    break
                try:
                    normalized_schema = ConnectorGovernance._normalize_role_schema(
                        schema
                    )
                except EvidenceLaneError:
                    role_profiles_valid = False
                    break
                expected_role_field_count += len(normalized_schema)
                if (
                    not isinstance(profiles, list)
                    or not profiles
                    or not all(isinstance(profile, str) for profile in profiles)
                    or not {str(profile) for profile in profiles}
                    <= _HOST_PROFILES
                    or str(row["backend_runtime"]) not in _BACKEND_RUNTIMES
                ):
                    role_profiles_valid = False
                    break
            if not isinstance(keys, list) or not all(
                isinstance(key, str) and _ENV_KEY.fullmatch(key) for key in keys
            ):
                config_keys_valid = False
                break
        fts_count = int(
            connection.execute("SELECT COUNT(*) FROM plugin_fts").fetchone()[0]
        )
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM plugin_event").fetchone()[0]
        )
        route_count = int(
            connection.execute("SELECT COUNT(*) FROM route_decision").fetchone()[0]
        )
        role_schema_field_count = (
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM role_schema_field"
                ).fetchone()[0]
            )
            if role_schema_table_present
            else 0
        )
        route_profiles_valid = True
        if route_profiles_present:
            registrations = {str(row["plugin_id"]): row for row in rows}
            for route in connection.execute(
                """
                SELECT selected_plugin_id,host_profile,role_schema_sha256
                FROM route_decision
                """
            ):
                host_profile = str(route["host_profile"])
                selected_id = route["selected_plugin_id"]
                if host_profile not in _HOST_PROFILES:
                    route_profiles_valid = False
                    break
                if selected_id is None:
                    if route["role_schema_sha256"] is not None:
                        route_profiles_valid = False
                        break
                    continue
                registration = registrations.get(str(selected_id))
                if registration is None:
                    route_profiles_valid = False
                    break
                try:
                    profiles = json.loads(registration["host_profiles_json"])
                    schema = json.loads(registration["role_schema_json"])
                except (TypeError, json.JSONDecodeError):
                    route_profiles_valid = False
                    break
                expected_sha256 = sha256_bytes(canonical_json_bytes(schema))
                if (
                    host_profile not in profiles
                    or route["role_schema_sha256"] != expected_sha256
                ):
                    route_profiles_valid = False
                    break
    finally:
        connection.close()
    valid = (
        required_tables <= tables
        and integrity == ["ok"]
        and not foreign_keys
        and active_count <= MAX_ADDITIONAL_PERSISTENT_PLUGINS
        and config_keys_valid
        and governed_grants_valid
        and role_profiles_valid
        and route_profiles_valid
        and (
            not role_profiles_present
            or (
                role_schema_table_present
                and role_schema_field_count == expected_role_field_count
            )
        )
        and fts_count == len(rows)
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "registration_count": len(rows),
        "active_count": active_count,
        "maximum_active": MAX_ADDITIONAL_PERSISTENT_PLUGINS,
        "event_count": event_count,
        "route_count": route_count,
        "fts_count": fts_count,
        "config_environment_names_only": config_keys_valid,
        "governed_grants_present": governed_grants_present,
        "governed_grants_valid": governed_grants_valid,
        "legacy_grant_schema_supported": not governed_grants_present,
        "role_profiles_present": role_profiles_present,
        "role_profiles_valid": role_profiles_valid,
        "legacy_role_profile_schema_supported": not role_profiles_present,
        "role_schema_table_present": role_schema_table_present,
        "role_schema_field_count": role_schema_field_count,
        "expected_role_schema_field_count": expected_role_field_count,
        "route_profiles_present": route_profiles_present,
        "route_profiles_valid": route_profiles_valid,
        "legacy_route_profile_schema_supported": not route_profiles_present,
    }
