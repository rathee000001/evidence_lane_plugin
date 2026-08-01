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
                recorded_at TEXT NOT NULL
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
        }
        for name, declaration in migrations.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE plugin_registration ADD COLUMN {name} {declaration}"
                )
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
        }
        require(
            bool(registration["name"]) and bool(registration["description"]),
            "PLUGIN_DESCRIPTION_REQUIRED",
            "Plugin governance requires a visible name and description.",
            status="BLOCKED",
        )
        registration_sha256 = sha256_bytes(canonical_json_bytes(registration))
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
                    existing["registration_sha256"]
                    in {registration_sha256, legacy_sha256}
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
                    "legacy_registration_reused": (
                        existing["registration_sha256"] == legacy_sha256
                    ),
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
                    allowed_actions_json, write_scope_json, expires_at,
                    registered_at, dropped_at, status, registration_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'ACTIVE', ?)
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
                    " ".join(exact_capabilities),
                ),
            )
            event = self._event(
                connection,
                plugin_id=exact_id,
                event_type="REGISTER",
                actor=registered_by,
                details={"registration_sha256": registration_sha256},
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
                if not row["purpose"]:
                    row["purpose"] = row["description"]
                if not row["allowed_actions"]:
                    row["allowed_actions"] = list(row["capabilities"])
                if not row["write_scope"]:
                    row["write_scope"] = [
                        f"lane:{lane}" for lane in row["allowed_lanes"]
                    ]
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
            }
        finally:
            connection.close()

    def route(
        self, *, capability: str, canonical_lane_id: str | None = None
    ) -> dict[str, Any]:
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
                    selected_plugin_id, decision, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    route_id,
                    capability,
                    canonical_lane_id,
                    selected,
                    decision,
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
            "selected_plugin_id": selected,
            "decision": decision,
            "deterministic_order": [row["plugin_id"] for row in matches],
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
        active_count = sum(1 for row in rows if row["status"] == "ACTIVE")
        config_keys_valid = True
        governed_grants_valid = True
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
                    or not ConnectorGovernance._expiry_is_live(str(row["expires_at"]))
                ):
                    governed_grants_valid = False
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
    finally:
        connection.close()
    valid = (
        required_tables <= tables
        and integrity == ["ok"]
        and not foreign_keys
        and active_count <= MAX_ADDITIONAL_PERSISTENT_PLUGINS
        and config_keys_valid
        and governed_grants_valid
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
    }
