"""Single SQLite receipt authority for one Evidence Lane project.

Public actions still return JSON receipts. Durable project receipts are stored
as content-addressed rows in this SQLite authority rather than as ever-growing
JSON/JSONL/ZIP trees. Historical file migration is exact-byte and append-only;
source removal is a later explicit, hash-verified operation.
"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
from pathlib import Path
from typing import Any

from .compact_storage import compress_exact_bytes, decompress_exact_bytes
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .sqlite_indexing import rebuild_connection_authority_index
from .timeutil import utc_now

RECEIPT_LEDGER_SCHEMA = "evidence-lane.receipt-ledger.v1"
RECEIPT_LEDGER_MIGRATION_SCHEMA = "evidence-lane.receipt-ledger-migration.v1"


def initialize_receipt_ledger(database: str | Path) -> None:
    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS receipt_content_cas(
                receipt_sha256 TEXT PRIMARY KEY,
                byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
                compression TEXT NOT NULL,
                compressed_bytes BLOB NOT NULL,
                first_recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS receipt_record(
                sequence INTEGER PRIMARY KEY,
                receipt_sha256 TEXT NOT NULL UNIQUE
                    REFERENCES receipt_content_cas(receipt_sha256),
                logical_path TEXT NOT NULL UNIQUE,
                receipt_kind TEXT NOT NULL,
                schema_id TEXT,
                project_id TEXT,
                session_id TEXT,
                host_task_id TEXT,
                media_type TEXT NOT NULL,
                byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
                payload_json TEXT,
                prior_receipt_sha256 TEXT,
                supersedes_receipt_sha256 TEXT,
                recorded_at TEXT NOT NULL,
                source_file_removed INTEGER NOT NULL DEFAULT 0
                    CHECK(source_file_removed IN (0,1))
            ) STRICT;
            CREATE TABLE IF NOT EXISTS receipt_link(
                link_id INTEGER PRIMARY KEY,
                source_receipt_sha256 TEXT NOT NULL
                    REFERENCES receipt_record(receipt_sha256),
                relation TEXT NOT NULL,
                target_receipt_sha256 TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                UNIQUE(source_receipt_sha256, relation, target_receipt_sha256)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS receipt_migration_batch(
                batch_id TEXT PRIMARY KEY,
                source_root TEXT NOT NULL,
                source_file_count INTEGER NOT NULL,
                source_bytes INTEGER NOT NULL,
                ingested_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                removed_count INTEGER NOT NULL,
                source_removal_authorized INTEGER NOT NULL
                    CHECK(source_removal_authorized IN (0,1)),
                receipt_sha256 TEXT NOT NULL UNIQUE,
                receipt_json TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            ) STRICT;
            CREATE VIRTUAL TABLE IF NOT EXISTS receipt_fts USING fts5(
                receipt_sha256 UNINDEXED,
                logical_path,
                receipt_kind,
                schema_id,
                payload_text,
                content='',
                contentless_delete=1,
                tokenize='unicode61'
            );
            CREATE INDEX IF NOT EXISTS receipt_record_kind_idx
            ON receipt_record(receipt_kind, sequence);
            CREATE INDEX IF NOT EXISTS receipt_record_project_session_idx
            ON receipt_record(project_id, session_id, sequence);
            CREATE TRIGGER IF NOT EXISTS receipt_record_no_update
            BEFORE UPDATE ON receipt_record BEGIN
              SELECT RAISE(ABORT, 'receipt ledger is append-only');
            END;
            CREATE TRIGGER IF NOT EXISTS receipt_record_no_delete
            BEFORE DELETE ON receipt_record BEGIN
              SELECT RAISE(ABORT, 'receipt ledger is append-only');
            END;
            """
        )
        connection.commit()
    finally:
        connection.close()


def _decode_json(data: bytes) -> tuple[str | None, dict[str, Any] | None]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    return (
        canonical_json_bytes(value).decode("utf-8"),
        value if isinstance(value, dict) else None,
    )


def append_receipt_bytes(
    connection: sqlite3.Connection,
    *,
    logical_path: str,
    data: bytes,
    receipt_kind: str,
    media_type: str | None = None,
    project_id: str | None = None,
    session_id: str | None = None,
    host_task_id: str | None = None,
    prior_receipt_sha256: str | None = None,
    supersedes_receipt_sha256: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    exact_path = str(logical_path).replace("\\", "/").lstrip("/")
    if not exact_path or ".." in Path(exact_path).parts:
        raise ValueError("Receipt logical path is invalid.")
    receipt_sha256 = sha256_bytes(data)
    existing = connection.execute(
        "SELECT receipt_sha256,byte_count FROM receipt_record WHERE logical_path=?",
        (exact_path,),
    ).fetchone()
    if existing is not None:
        if str(existing[0]) != receipt_sha256 or int(existing[1]) != len(data):
            raise ValueError("RECEIPT_LOGICAL_PATH_IMMUTABLE_CONFLICT")
        return {
            "schema": RECEIPT_LEDGER_SCHEMA,
            "status": "PASS",
            "state": "ALREADY_RECORDED",
            "logical_path": exact_path,
            "receipt_sha256": receipt_sha256,
            "byte_count": len(data),
        }
    payload_json, payload = _decode_json(data)
    schema_id = str(payload.get("schema") or "") if payload is not None else None
    exact_media = media_type or mimetypes.guess_type(exact_path)[0] or (
        "application/json" if payload_json is not None else "application/octet-stream"
    )
    sequence_row = connection.execute(
        "SELECT COALESCE(MAX(sequence),0)+1 FROM receipt_record"
    ).fetchone()
    sequence = int(sequence_row[0])
    exact_time = recorded_at or utc_now()
    compression, compressed_bytes = compress_exact_bytes(data)
    connection.execute(
        """
        INSERT OR IGNORE INTO receipt_content_cas(
            receipt_sha256,byte_count,compression,compressed_bytes,
            first_recorded_at
        ) VALUES(?,?,?,?,?)
        """,
        (receipt_sha256, len(data), compression, compressed_bytes, exact_time),
    )
    connection.execute(
        """
        INSERT INTO receipt_record(
            sequence,receipt_sha256,logical_path,receipt_kind,schema_id,
            project_id,session_id,host_task_id,media_type,byte_count,
            payload_json,prior_receipt_sha256,
            supersedes_receipt_sha256,recorded_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sequence,
            receipt_sha256,
            exact_path,
            receipt_kind,
            schema_id,
            project_id,
            session_id,
            host_task_id,
            exact_media,
            len(data),
            payload_json,
            prior_receipt_sha256,
            supersedes_receipt_sha256,
            exact_time,
        ),
    )
    connection.execute(
        "INSERT INTO receipt_fts(rowid,receipt_sha256,logical_path,receipt_kind,"
        "schema_id,payload_text) VALUES(?,?,?,?,?,?)",
        (
            sequence,
            receipt_sha256,
            exact_path,
            receipt_kind,
            schema_id or "",
            payload_json or "",
        ),
    )
    return {
        "schema": RECEIPT_LEDGER_SCHEMA,
        "status": "PASS",
        "state": "APPENDED",
        "sequence": sequence,
        "logical_path": exact_path,
        "receipt_kind": receipt_kind,
        "schema_id": schema_id,
        "receipt_sha256": receipt_sha256,
        "byte_count": len(data),
        "recorded_at": exact_time,
    }


def append_receipt(
    database: str | Path,
    *,
    logical_path: str,
    receipt: dict[str, Any],
    receipt_kind: str,
    project_id: str | None = None,
    session_id: str | None = None,
    host_task_id: str | None = None,
) -> dict[str, Any]:
    path = Path(database)
    initialize_receipt_ledger(path)
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        result = append_receipt_bytes(
            connection,
            logical_path=logical_path,
            data=canonical_json_bytes(receipt),
            receipt_kind=receipt_kind,
            media_type="application/json",
            project_id=project_id,
            session_id=session_id,
            host_task_id=host_task_id,
        )
        rebuild_connection_authority_index(
            connection,
            authority_id="receipt_ledger",
            table_names=("receipt_record", "receipt_link"),
        )
        connection.commit()
        return result
    finally:
        connection.close()


def read_receipt(
    database: str | Path,
    *,
    logical_path: str | None = None,
    receipt_sha256: str | None = None,
) -> dict[str, Any]:
    if bool(logical_path) == bool(receipt_sha256):
        raise ValueError("Supply exactly one receipt locator.")
    connection = sqlite3.connect(
        f"file:{Path(database).resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        if logical_path:
            row = connection.execute(
                """
                SELECT r.receipt_sha256,r.byte_count,c.compression,c.compressed_bytes
                FROM receipt_record AS r
                JOIN receipt_content_cas AS c
                  ON c.receipt_sha256=r.receipt_sha256
                WHERE r.logical_path=?
                """,
                (str(logical_path).replace("\\", "/").lstrip("/"),),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT r.receipt_sha256,r.byte_count,c.compression,c.compressed_bytes
                FROM receipt_record AS r
                JOIN receipt_content_cas AS c
                  ON c.receipt_sha256=r.receipt_sha256
                WHERE r.receipt_sha256=?
                """,
                (str(receipt_sha256).upper(),),
            ).fetchone()
        if row is None:
            raise KeyError("Receipt not found.")
        data = decompress_exact_bytes(
            compression=str(row[2]),
            payload=bytes(row[3]),
            expected_size=int(row[1]),
            expected_sha256=str(row[0]),
        )
        value = json.loads(data.decode("utf-8"))
        if not isinstance(value, dict):
            raise TypeError("Receipt row is not one JSON object.")
        return value
    finally:
        connection.close()


def migrate_receipt_tree(
    database: str | Path,
    *,
    source_root: str | Path,
    project_id: str,
    remove_source_files: bool = False,
) -> dict[str, Any]:
    """Ingest exact historical files; deletion is separately gated."""

    root = Path(source_root).resolve()
    path = Path(database).resolve()
    initialize_receipt_ledger(path)
    excluded = {
        path,
        path.with_suffix(path.suffix + "-wal"),
        path.with_suffix(path.suffix + "-shm"),
    }
    files = [
        item
        for item in sorted(root.rglob("*"))
        if item.is_file() and item.resolve() not in excluded
    ]
    source_bytes = sum(item.stat().st_size for item in files)
    connection = sqlite3.connect(path, timeout=30)
    ingested = 0
    duplicates = 0
    removable: list[Path] = []
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        for item in files:
            data = item.read_bytes()
            relative = item.relative_to(root).as_posix()
            result = append_receipt_bytes(
                connection,
                logical_path=f"historical/{relative}",
                data=data,
                receipt_kind="HISTORICAL_FILE_MIGRATION",
                project_id=project_id,
            )
            if result["state"] == "APPENDED":
                ingested += 1
            else:
                duplicates += 1
            row = connection.execute(
                "SELECT receipt_sha256,byte_count FROM receipt_record "
                "WHERE logical_path=?",
                (f"historical/{relative}",),
            ).fetchone()
            if row is None or str(row[0]) != sha256_file(item) or int(row[1]) != item.stat().st_size:
                raise RuntimeError("RECEIPT_MIGRATION_READBACK_MISMATCH")
            removable.append(item)
        rebuild_connection_authority_index(
            connection,
            authority_id="receipt_ledger",
            table_names=("receipt_record", "receipt_link"),
        )
        core = {
            "schema": RECEIPT_LEDGER_MIGRATION_SCHEMA,
            "status": "PASS",
            "project_id": project_id,
            "source_root": str(root),
            "source_file_count": len(files),
            "source_bytes": source_bytes,
            "ingested_count": ingested,
            "duplicate_count": duplicates,
            "source_removal_authorized": bool(remove_source_files),
            "recorded_at": utc_now(),
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(core))
        receipt = {**core, "receipt_sha256": receipt_sha256}
        batch_id = "receipt-migration-" + receipt_sha256[:24].lower()
        connection.execute(
            "INSERT OR IGNORE INTO receipt_migration_batch VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                batch_id,
                str(root),
                len(files),
                source_bytes,
                ingested,
                duplicates,
                len(removable) if remove_source_files else 0,
                int(remove_source_files),
                receipt_sha256,
                canonical_json_bytes(receipt).decode("utf-8"),
                receipt["recorded_at"],
            ),
        )
        connection.commit()
    finally:
        connection.close()
    if remove_source_files:
        readback = sqlite3.connect(path)
        try:
            admitted_hashes = {
                str(row[0])
                for row in readback.execute(
                    "SELECT receipt_sha256 FROM receipt_record"
                )
            }
        finally:
            readback.close()
        for item in removable:
            if sha256_file(item) not in admitted_hashes:
                raise RuntimeError("RECEIPT_SOURCE_DELETE_HASH_NOT_ADMITTED")
            item.unlink()
    return receipt


__all__ = [
    "RECEIPT_LEDGER_MIGRATION_SCHEMA",
    "RECEIPT_LEDGER_SCHEMA",
    "append_receipt",
    "append_receipt_bytes",
    "initialize_receipt_ledger",
    "migrate_receipt_tree",
    "read_receipt",
]
