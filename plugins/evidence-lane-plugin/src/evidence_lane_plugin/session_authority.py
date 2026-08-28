"""SQLite authority for sessions, task attachments, and State Travel entries."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes
from .sqlite_indexing import rebuild_connection_authority_index
from .timeutil import utc_now

SESSION_AUTHORITY_SCHEMA = "evidence-lane.session-authority.v1"


def initialize_session_authority(database: str | Path) -> None:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS session_record(
                session_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                state TEXT NOT NULL,
                active_host_task_id TEXT,
                generation INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS task_attachment(
                attachment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES session_record(session_id),
                host_task_id TEXT NOT NULL,
                destination_title TEXT,
                role TEXT NOT NULL,
                generation INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(session_id, host_task_id, generation)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS state_travel_entry(
                entry_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                source_task_id TEXT NOT NULL,
                destination_task_id TEXT NOT NULL,
                route TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL UNIQUE,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS goal_projection(
                projection_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                host_task_id TEXT NOT NULL,
                goal_state TEXT NOT NULL,
                active_plan_row INTEGER,
                plan_identity_sha256 TEXT,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE VIRTUAL TABLE IF NOT EXISTS session_authority_fts USING fts5(
                record_id UNINDEXED,
                record_kind,
                session_id,
                host_task_id,
                payload_text,
                tokenize='unicode61'
            );
            CREATE INDEX IF NOT EXISTS task_attachment_session_idx
            ON task_attachment(session_id, generation, host_task_id);
            CREATE INDEX IF NOT EXISTS state_travel_destination_idx
            ON state_travel_entry(session_id, destination_task_id, recorded_at);
            """
        )
        connection.commit()
    finally:
        connection.close()


def upsert_session(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    session_id: str,
    state: str,
    generation: int,
    payload: dict[str, Any],
    active_host_task_id: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    exact_time = recorded_at or utc_now()
    payload_bytes = canonical_json_bytes(payload)
    payload_sha256 = sha256_bytes(payload_bytes)
    created = connection.execute(
        "SELECT created_at FROM session_record WHERE session_id=?", (session_id,)
    ).fetchone()
    connection.execute(
        """
        INSERT INTO session_record(
            session_id,project_id,state,active_host_task_id,generation,
            payload_json,payload_sha256,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(session_id) DO UPDATE SET
            project_id=excluded.project_id,
            state=excluded.state,
            active_host_task_id=excluded.active_host_task_id,
            generation=excluded.generation,
            payload_json=excluded.payload_json,
            payload_sha256=excluded.payload_sha256,
            updated_at=excluded.updated_at
        """,
        (
            session_id,
            project_id,
            state,
            active_host_task_id,
            int(generation),
            payload_bytes.decode("utf-8"),
            payload_sha256,
            str(created[0]) if created else exact_time,
            exact_time,
        ),
    )
    return {
        "schema": SESSION_AUTHORITY_SCHEMA,
        "status": "PASS",
        "session_id": session_id,
        "project_id": project_id,
        "state": state,
        "generation": int(generation),
        "payload_sha256": payload_sha256,
        "recorded_at": exact_time,
    }


def append_state_travel_entry(
    database: str | Path,
    *,
    session_id: str,
    source_task_id: str,
    destination_task_id: str,
    route: str,
    status: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    path = Path(database)
    initialize_session_authority(path)
    connection = sqlite3.connect(path, timeout=30)
    try:
        payload_bytes = canonical_json_bytes(payload)
        payload_sha256 = sha256_bytes(payload_bytes)
        entry_id = "travel_" + payload_sha256[:32].lower()
        exact_time = utc_now()
        connection.execute(
            """
            INSERT OR IGNORE INTO state_travel_entry(
                entry_id,session_id,source_task_id,destination_task_id,
                route,status,payload_json,payload_sha256,recorded_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                entry_id,
                session_id,
                source_task_id,
                destination_task_id,
                route,
                status,
                payload_bytes.decode("utf-8"),
                payload_sha256,
                exact_time,
            ),
        )
        rebuild_connection_authority_index(
            connection,
            authority_id="session_authority",
            table_names=(
                "session_record",
                "task_attachment",
                "state_travel_entry",
                "goal_projection",
            ),
        )
        connection.commit()
        return {
            "schema": SESSION_AUTHORITY_SCHEMA,
            "status": "PASS",
            "entry_id": entry_id,
            "payload_sha256": payload_sha256,
            "recorded_at": exact_time,
            "filesystem_entry_created": False,
        }
    finally:
        connection.close()


__all__ = [
    "SESSION_AUTHORITY_SCHEMA",
    "append_state_travel_entry",
    "initialize_session_authority",
    "upsert_session",
]
