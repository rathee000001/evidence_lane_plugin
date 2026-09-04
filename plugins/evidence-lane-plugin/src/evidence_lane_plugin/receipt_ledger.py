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
PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS: dict[
    str, tuple[str, str]
] = {
    "source-scaffold-retirement.json": (
        "evidence-lane.source-scaffold-retirement.v1",
        "PROJECT_AUTHORITY_SOURCE_SCAFFOLD_RETIREMENT",
    ),
    "working-sector-migration.json": (
        "evidence-lane.working-sector-migration.v1",
        "PROJECT_AUTHORITY_WORKING_SECTOR_MIGRATION",
    ),
    "working-sector-promotion-block.json": (
        "evidence-lane.working-sector-promotion-block.v1",
        "PROJECT_AUTHORITY_WORKING_SECTOR_PROMOTION_BLOCK",
    ),
    "working-sector-recovery.json": (
        "evidence-lane.working-sector-recovery.v1",
        "PROJECT_AUTHORITY_WORKING_SECTOR_RECOVERY",
    ),
}


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


def _record_content_reuse_links(
    connection: sqlite3.Connection,
    *,
    receipt_sha256: str,
    existing_logical_path: str,
    existing_project_id: str | None,
    requested_logical_path: str,
    requested_project_id: str | None,
    recorded_at: str,
) -> dict[str, bool]:
    alias_recorded = existing_logical_path != requested_logical_path
    project_identity_corrected = bool(
        requested_project_id and existing_project_id != requested_project_id
    )
    links: list[tuple[str, dict[str, Any]]] = []
    if alias_recorded:
        alias_metadata = {
            "schema": "evidence-lane.receipt-logical-alias.v1",
            "logical_path": requested_logical_path,
            "canonical_logical_path": existing_logical_path,
            "project_id": requested_project_id,
            "recorded_at": recorded_at,
        }
        links.append(
            (
                "LOGICAL_ALIAS:"
                + sha256_bytes(requested_logical_path.encode("utf-8"))[:16],
                alias_metadata,
            )
        )
    if project_identity_corrected:
        correction_metadata = {
            "schema": "evidence-lane.receipt-project-identity-correction.v1",
            "logical_path": requested_logical_path,
            "prior_project_id": existing_project_id,
            "corrected_project_id": requested_project_id,
            "history_rewritten": False,
            "recorded_at": recorded_at,
        }
        links.append(
            (
                "PROJECT_ID_CORRECTION:"
                + sha256_bytes(str(requested_project_id).encode("utf-8"))[:16],
                correction_metadata,
            )
        )
    for relation, metadata in links:
        connection.execute(
            """
            INSERT OR IGNORE INTO receipt_link(
                source_receipt_sha256,relation,target_receipt_sha256,metadata_json
            ) VALUES(?,?,?,?)
            """,
            (
                receipt_sha256,
                relation,
                receipt_sha256,
                canonical_json_bytes(metadata).decode("utf-8"),
            ),
        )
    return {
        "logical_alias_recorded": alias_recorded,
        "project_identity_correction_recorded": project_identity_corrected,
    }


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
    exact_time = recorded_at or utc_now()
    existing = connection.execute(
        "SELECT receipt_sha256,byte_count,project_id FROM receipt_record WHERE logical_path=?",
        (exact_path,),
    ).fetchone()
    if existing is not None:
        if str(existing[0]) != receipt_sha256 or int(existing[1]) != len(data):
            raise ValueError("RECEIPT_LOGICAL_PATH_IMMUTABLE_CONFLICT")
        link_state = _record_content_reuse_links(
            connection,
            receipt_sha256=receipt_sha256,
            existing_logical_path=exact_path,
            existing_project_id=(str(existing[2]) if existing[2] is not None else None),
            requested_logical_path=exact_path,
            requested_project_id=project_id,
            recorded_at=exact_time,
        )
        return {
            "schema": RECEIPT_LEDGER_SCHEMA,
            "status": "PASS",
            "state": "ALREADY_RECORDED",
            "logical_path": exact_path,
            "receipt_sha256": receipt_sha256,
            "byte_count": len(data),
            **link_state,
        }
    existing_content = connection.execute(
        """
        SELECT r.logical_path,r.project_id,r.byte_count,
               c.compression,c.compressed_bytes
        FROM receipt_record AS r
        JOIN receipt_content_cas AS c
          ON c.receipt_sha256=r.receipt_sha256
        WHERE r.receipt_sha256=?
        """,
        (receipt_sha256,),
    ).fetchone()
    if existing_content is not None:
        exact_bytes = decompress_exact_bytes(
            compression=str(existing_content[3]),
            payload=bytes(existing_content[4]),
            expected_size=int(existing_content[2]),
            expected_sha256=receipt_sha256,
        )
        if exact_bytes != data:
            raise ValueError("RECEIPT_CONTENT_CAS_IMMUTABLE_CONFLICT")
        link_state = _record_content_reuse_links(
            connection,
            receipt_sha256=receipt_sha256,
            existing_logical_path=str(existing_content[0]),
            existing_project_id=(
                str(existing_content[1])
                if existing_content[1] is not None
                else None
            ),
            requested_logical_path=exact_path,
            requested_project_id=project_id,
            recorded_at=exact_time,
        )
        return {
            "schema": RECEIPT_LEDGER_SCHEMA,
            "status": "PASS",
            "state": "CONTENT_REUSED",
            "logical_path": exact_path,
            "canonical_logical_path": str(existing_content[0]),
            "receipt_sha256": receipt_sha256,
            "byte_count": len(data),
            **link_state,
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


def _validated_project_authority_operational_receipt(
    project_root: Path,
    *,
    project_id: str,
    filename: str,
) -> dict[str, Any]:
    """Validate one exact current operational projection before ledger admission."""

    specification = PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS.get(filename)
    if specification is None or Path(filename).name != filename:
        raise ValueError("PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_NOT_ALLOWLISTED")
    receipt_root = (project_root / "receipts" / "project-authority").resolve()
    source = (receipt_root / filename).resolve()
    try:
        source.relative_to(receipt_root)
    except ValueError as exc:
        raise ValueError(
            "PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_PATH_ESCAPE"
        ) from exc
    if not source.is_file():
        raise FileNotFoundError(source)
    data = source.read_bytes()
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            "PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_JSON_INVALID"
        ) from exc
    if not isinstance(payload, dict):
        raise TypeError("PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_JSON_INVALID")
    schema_id, receipt_kind = specification
    body = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    payload_project_id = payload.get("project_id")
    if (
        payload.get("schema") != schema_id
        or payload.get("receipt_sha256")
        != sha256_bytes(canonical_json_bytes(body))
        or data != canonical_json_bytes(payload)
        or (
            payload_project_id is not None
            and str(payload_project_id) != project_id
        )
    ):
        raise ValueError("PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SEAL_MISMATCH")
    content_sha256 = sha256_bytes(data)
    role = filename.removesuffix(".json")
    return {
        "filename": filename,
        "source": source,
        "data": data,
        "payload": payload,
        "schema_id": schema_id,
        "receipt_kind": receipt_kind,
        "content_sha256": content_sha256,
        "logical_path": (
            f"project-authority/operational/{role}/"
            f"{content_sha256.lower()}.json"
        ),
        "recorded_at": (
            str(payload.get("recorded_at") or payload.get("completed_at") or "")
            or None
        ),
    }


def _append_validated_project_authority_operational_receipt(
    connection: sqlite3.Connection,
    *,
    project_id: str,
    validated: dict[str, Any],
) -> dict[str, Any]:
    previous = connection.execute(
        "SELECT receipt_sha256 FROM receipt_record "
        "WHERE receipt_kind=? AND project_id=? "
        "ORDER BY sequence DESC LIMIT 1",
        (validated["receipt_kind"], project_id),
    ).fetchone()
    previous_sha256 = str(previous[0]) if previous is not None else None
    if previous_sha256 == validated["content_sha256"]:
        previous_sha256 = None
    result = append_receipt_bytes(
        connection,
        logical_path=str(validated["logical_path"]),
        data=bytes(validated["data"]),
        receipt_kind=str(validated["receipt_kind"]),
        media_type="application/json",
        project_id=project_id,
        prior_receipt_sha256=previous_sha256,
        supersedes_receipt_sha256=previous_sha256,
        recorded_at=validated["recorded_at"],
    )
    row = connection.execute(
        "SELECT r.logical_path,r.receipt_kind,r.byte_count,c.compression,"
        "c.compressed_bytes FROM receipt_record AS r "
        "JOIN receipt_content_cas AS c ON c.receipt_sha256=r.receipt_sha256 "
        "WHERE r.receipt_sha256=?",
        (validated["content_sha256"],),
    ).fetchone()
    if row is None:
        raise RuntimeError("PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_READBACK_MISSING")
    exact_bytes = decompress_exact_bytes(
        compression=str(row[3]),
        payload=bytes(row[4]),
        expected_size=int(row[2]),
        expected_sha256=str(validated["content_sha256"]),
    )
    if (
        exact_bytes != validated["data"]
        or str(row[0]) != validated["logical_path"]
        or str(row[1]) != validated["receipt_kind"]
    ):
        raise RuntimeError("PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_READBACK_MISMATCH")
    return {
        **result,
        "filename": validated["filename"],
        "payload_receipt_sha256": validated["payload"]["receipt_sha256"],
        "prior_operational_receipt_sha256": previous_sha256,
        "source_file_mutated": False,
    }


def append_project_authority_operational_receipt(
    project_root: str | Path,
    *,
    project_id: str,
    filename: str,
) -> dict[str, Any]:
    """Admit one allowlisted mutable projection as immutable exact receipt bytes."""

    root = Path(project_root).resolve()
    validated = _validated_project_authority_operational_receipt(
        root,
        project_id=project_id,
        filename=filename,
    )
    database = root / "receipts" / "receipt-ledger.sqlite"
    initialize_receipt_ledger(database)
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        result = _append_validated_project_authority_operational_receipt(
            connection,
            project_id=project_id,
            validated=validated,
        )
        changed = result.get("state") == "APPENDED" or any(
            bool(result.get(field))
            for field in (
                "logical_alias_recorded",
                "project_identity_correction_recorded",
            )
        )
        if changed:
            rebuild_connection_authority_index(
                connection,
                authority_id="receipt_ledger",
                table_names=("receipt_record", "receipt_link"),
            )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def backfill_project_authority_operational_receipts(
    project_root: str | Path,
    *,
    project_id: str,
) -> dict[str, Any]:
    """Idempotently admit only the four fixed project-authority projections."""

    root = Path(project_root).resolve()
    receipt_root = root / "receipts" / "project-authority"
    present_filenames = [
        filename
        for filename in PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS
        if (receipt_root / filename).is_file()
    ]
    validated_rows = [
        _validated_project_authority_operational_receipt(
            root,
            project_id=project_id,
            filename=filename,
        )
        for filename in present_filenames
    ]
    database = root / "receipts" / "receipt-ledger.sqlite"
    initialize_receipt_ledger(database)
    connection = sqlite3.connect(database, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        before_count = int(
            connection.execute("SELECT COUNT(*) FROM receipt_record").fetchone()[0]
        )
        results = [
            _append_validated_project_authority_operational_receipt(
                connection,
                project_id=project_id,
                validated=validated,
            )
            for validated in validated_rows
        ]
        changed = any(
            result.get("state") == "APPENDED"
            or any(
                bool(result.get(field))
                for field in (
                    "logical_alias_recorded",
                    "project_identity_correction_recorded",
                )
            )
            for result in results
        )
        if changed:
            rebuild_connection_authority_index(
                connection,
                authority_id="receipt_ledger",
                table_names=("receipt_record", "receipt_link"),
            )
        after_count = int(
            connection.execute("SELECT COUNT(*) FROM receipt_record").fetchone()[0]
        )
        integrity = [
            str(row[0]) for row in connection.execute("PRAGMA integrity_check")
        ]
        foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
        if integrity != ["ok"] or foreign_keys:
            raise RuntimeError("PROJECT_AUTHORITY_OPERATIONAL_BACKFILL_INTEGRITY_FAILED")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    body = {
        "schema": "evidence-lane.project-authority-operational-backfill.v1",
        "status": "PASS",
        "project_id": project_id,
        "allowlisted_filename_count": len(
            PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS
        ),
        "present_filename_count": len(present_filenames),
        "present_filenames": present_filenames,
        "missing_filenames": [
            filename
            for filename in PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS
            if filename not in present_filenames
        ],
        "receipt_record_count_before": before_count,
        "receipt_record_count_after": after_count,
        "new_receipt_record_count": after_count - before_count,
        "logical_paths": [str(row["logical_path"]) for row in validated_rows],
        "content_sha256s": [str(row["content_sha256"]) for row in validated_rows],
        "source_files_mutated": False,
        "recursive_scan_used": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


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
            exact_logical_path = str(logical_path).replace("\\", "/").lstrip("/")
            row = connection.execute(
                """
                SELECT r.receipt_sha256,r.byte_count,c.compression,c.compressed_bytes
                FROM receipt_record AS r
                JOIN receipt_content_cas AS c
                  ON c.receipt_sha256=r.receipt_sha256
                WHERE r.logical_path=?
                """,
                (exact_logical_path,),
            ).fetchone()
            if row is None:
                alias_rows = connection.execute(
                    """
                    SELECT source_receipt_sha256,metadata_json
                    FROM receipt_link
                    WHERE relation LIKE 'LOGICAL_ALIAS:%'
                    ORDER BY link_id
                    """
                ).fetchall()
                alias_sha256 = next(
                    (
                        str(alias_row[0])
                        for alias_row in alias_rows
                        if json.loads(str(alias_row[1])).get("logical_path")
                        == exact_logical_path
                    ),
                    None,
                )
                if alias_sha256 is not None:
                    row = connection.execute(
                        """
                        SELECT r.receipt_sha256,r.byte_count,
                               c.compression,c.compressed_bytes
                        FROM receipt_record AS r
                        JOIN receipt_content_cas AS c
                          ON c.receipt_sha256=r.receipt_sha256
                        WHERE r.receipt_sha256=?
                        """,
                        (alias_sha256,),
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
    "PROJECT_AUTHORITY_OPERATIONAL_RECEIPT_SPECS",
    "RECEIPT_LEDGER_MIGRATION_SCHEMA",
    "RECEIPT_LEDGER_SCHEMA",
    "append_project_authority_operational_receipt",
    "append_receipt",
    "append_receipt_bytes",
    "backfill_project_authority_operational_receipts",
    "initialize_receipt_ledger",
    "migrate_receipt_tree",
    "read_receipt",
]
