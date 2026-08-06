"""Explicit, append-only primary storage connector selection."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .store import ProjectStore
from .timeutil import utc_now

STORAGE_SELECTION_SCHEMA = "evidence-lane.storage-selection.v1"
STORAGE_MODES = (
    "AUTO",
    "LOCAL_SQLITE",
    "CONFIGURED_DURABLE_CONNECTOR",
)


class StorageSelection:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def _path(self, project_id: str) -> Path:
        return self.store.project_root(project_id) / "storage_selection.sqlite"

    def _connect(self, project_id: str) -> sqlite3.Connection:
        self.store.config(project_id)
        path = self._path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS storage_selection_event(
                event_id TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN (
                    'AUTO','LOCAL_SQLITE','CONFIGURED_DURABLE_CONNECTOR'
                )),
                connector_id TEXT,
                selected_by TEXT NOT NULL,
                selected_at TEXT NOT NULL,
                reason TEXT NOT NULL,
                previous_event_sha256 TEXT,
                event_sha256 TEXT NOT NULL UNIQUE
            ) STRICT;
            CREATE TABLE IF NOT EXISTS storage_selection_current(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                event_id TEXT NOT NULL REFERENCES storage_selection_event(event_id),
                mode TEXT NOT NULL,
                connector_id TEXT
            ) STRICT;
            """
        )
        return connection

    def inspect(self, project_id: str) -> dict[str, Any]:
        self.store.config(project_id)
        path = self._path(project_id)
        if not path.is_file():
            return {
                "status": "PASS",
                "schema": STORAGE_SELECTION_SCHEMA,
                "mode": "AUTO",
                "connector_id": None,
                "selection_recorded": False,
                "google_drive_role": "OPTIONAL_FALLBACK_MIRROR_NEVER_PRIMARY",
                "secret_values_persisted": False,  # nosec B105
                "history_count": 0,
            }
        uri = f"file:{path.resolve().as_posix()}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute(
                """
                SELECT c.mode,c.connector_id,e.event_id,e.selected_by,
                       e.selected_at,e.reason,e.event_sha256
                FROM storage_selection_current c
                JOIN storage_selection_event e ON e.event_id=c.event_id
                WHERE c.singleton=1
                """
            ).fetchone()
            integrity = [item[0] for item in connection.execute("PRAGMA integrity_check")]
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            history_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM storage_selection_event"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        require(
            row is not None and integrity == ["ok"] and not foreign_keys,
            "STORAGE_SELECTION_INVALID",
            "The project storage selection ledger failed validation.",
            status="FAIL",
            integrity=integrity,
            foreign_key_errors=len(foreign_keys),
        )
        return {
            "status": "PASS",
            "schema": STORAGE_SELECTION_SCHEMA,
            "mode": row["mode"],
            "connector_id": row["connector_id"],
            "selection_recorded": True,
            "event_id": row["event_id"],
            "selected_by": row["selected_by"],
            "selected_at": row["selected_at"],
            "reason": row["reason"],
            "event_sha256": row["event_sha256"],
            "history_count": history_count,
            "integrity": integrity,
            "foreign_key_errors": 0,
            "google_drive_role": (
                "SEALED_ARTIFACT_CARRIER_ONLY_WHEN_HOST_POLICY_ALLOWS_NEVER_PRIMARY"
            ),
            "secret_values_persisted": False,  # nosec B105
        }

    def select(
        self,
        project_id: str,
        *,
        mode: str,
        selected_by: str,
        reason: str,
        confirmation: str,
        connector_id: str | None = None,
    ) -> dict[str, Any]:
        exact_mode = mode.strip().upper()
        exact_connector = connector_id.strip() if connector_id else None
        require(
            exact_mode in STORAGE_MODES,
            "STORAGE_SELECTION_MODE_INVALID",
            "Storage selection must be AUTO, LOCAL_SQLITE, or CONFIGURED_DURABLE_CONNECTOR.",
            status="BLOCKED",
            allowed=list(STORAGE_MODES),
        )
        require(
            bool(selected_by.strip()) and bool(reason.strip()),
            "STORAGE_SELECTION_RATIONALE_REQUIRED",
            "Storage selection requires a visible actor and reason.",
            status="BLOCKED",
        )
        require(
            (exact_mode == "CONFIGURED_DURABLE_CONNECTOR") == bool(exact_connector),
            "STORAGE_SELECTION_CONNECTOR_ID_INVALID",
            "Only CONFIGURED_DURABLE_CONNECTOR requires one connector_id.",
            status="BLOCKED",
        )
        normalized_connector = (
            exact_connector.lower().replace("-", "_").replace(" ", "_")
            if exact_connector
            else ""
        )
        require(
            normalized_connector not in {"drive", "gdrive", "google_drive"},
            "GOOGLE_DRIVE_PRIMARY_RUNTIME_FORBIDDEN",
            "Google Drive may carry sealed artifacts only when host policy allows; it cannot be selected as the transactional runtime authority.",
            status="BLOCKED",
        )
        token = f"SELECT_STORAGE:{exact_mode}"
        if exact_connector:
            token += f":{exact_connector}"
        require(
            confirmation == token,
            "STORAGE_SELECTION_CONFIRMATION_INVALID",
            "Changing the primary storage route requires the exact selection token.",
            status="BLOCKED",
            required=token,
        )
        current = self.inspect(project_id)
        if (
            current["mode"] == exact_mode
            and current.get("connector_id") == exact_connector
            and current["selection_recorded"]
        ):
            return {**current, "idempotent": True}
        connection = self._connect(project_id)
        try:
            connection.execute("BEGIN IMMEDIATE")
            prior = connection.execute(
                "SELECT event_sha256 FROM storage_selection_event ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            event = {
                "schema": STORAGE_SELECTION_SCHEMA,
                "event_id": prefixed_id("storageevt"),
                "mode": exact_mode,
                "connector_id": exact_connector,
                "selected_by": selected_by.strip(),
                "selected_at": utc_now(),
                "reason": reason.strip(),
                "previous_event_sha256": str(prior[0]) if prior else None,
            }
            event["event_sha256"] = sha256_bytes(canonical_json_bytes(event))
            connection.execute(
                "INSERT INTO storage_selection_event VALUES(?,?,?,?,?,?,?,?)",
                (
                    event["event_id"],
                    exact_mode,
                    exact_connector,
                    event["selected_by"],
                    event["selected_at"],
                    event["reason"],
                    event["previous_event_sha256"],
                    event["event_sha256"],
                ),
            )
            connection.execute(
                """
                INSERT INTO storage_selection_current(singleton,event_id,mode,connector_id)
                VALUES(1,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    event_id=excluded.event_id,
                    mode=excluded.mode,
                    connector_id=excluded.connector_id
                """,
                (event["event_id"], exact_mode, exact_connector),
            )
            connection.commit()
        finally:
            connection.close()
        return {**self.inspect(project_id), "idempotent": False}
