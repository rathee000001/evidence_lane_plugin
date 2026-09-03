"""One-time migration of legacy Agent Learning JSON projections into SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .compact_storage import compress_exact_bytes, decompress_exact_bytes
from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes, sha256_file
from .project_root_binding import validate_project_root_binding
from .timeutil import utc_now

LEGACY_LEARNING_NORMALIZATION_SCHEMA = (
    "evidence-lane.legacy-learning-normalization.v1"
)
LEGACY_LEARNING_NORMALIZATION_CONFIRMATION = (
    "MIGRATE VERIFIED LEGACY LEARNING JSON INTO SQLITE AND QUARANTINE RAW FILES"
)


def _file_manifest(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        path.relative_to(directory).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _initialize_migration_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS legacy_learning_receipt_content_cas(
            receipt_sha256 TEXT PRIMARY KEY,
            schema_id TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS legacy_learning_receipt_file(
            logical_path TEXT PRIMARY KEY,
            receipt_sha256 TEXT NOT NULL
                REFERENCES legacy_learning_receipt_content_cas(receipt_sha256),
            file_sha256 TEXT NOT NULL UNIQUE,
            migrated_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS legacy_learning_migration_receipt(
            migration_id TEXT PRIMARY KEY,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            receipt_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE TRIGGER IF NOT EXISTS legacy_learning_receipt_content_no_update
        BEFORE UPDATE ON legacy_learning_receipt_content_cas
        BEGIN
            SELECT RAISE(ABORT, 'legacy learning receipt CAS is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS legacy_learning_receipt_content_no_delete
        BEFORE DELETE ON legacy_learning_receipt_content_cas
        BEGIN
            SELECT RAISE(ABORT, 'legacy learning receipt CAS is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS legacy_learning_receipt_file_no_update
        BEFORE UPDATE ON legacy_learning_receipt_file
        BEGIN
            SELECT RAISE(ABORT, 'legacy learning receipt file is immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS legacy_learning_receipt_file_no_delete
        BEFORE DELETE ON legacy_learning_receipt_file
        BEGIN
            SELECT RAISE(ABORT, 'legacy learning receipt file is immutable');
        END;
        """
    )


def migrate_legacy_learning_files(
    project_root: str | Path,
    *,
    project_id: str,
    quarantine_root: str | Path,
    confirmation: str,
) -> dict[str, Any]:
    """Verify duplicate candidates, ingest receipts, and quarantine raw JSON."""

    require(
        confirmation == LEGACY_LEARNING_NORMALIZATION_CONFIRMATION,
        "LEGACY_LEARNING_NORMALIZATION_CONFIRMATION_REQUIRED",
        "Legacy Learning normalization requires the exact bounded confirmation.",
        status="BLOCKED",
    )
    root = validate_project_root_binding(
        project_root,
        project_id=project_id,
        error_code="LEGACY_LEARNING_NORMALIZATION_PROJECT_ROOT_INVALID",
    )
    learning_root = root / "ai_learning"
    database = learning_root / "agent-learning.sqlite"
    candidates_root = learning_root / "candidates"
    receipts_root = learning_root / "receipts"
    quarantine = Path(quarantine_root).resolve()
    receipt_path = root / "receipts" / "learning" / "legacy-learning-normalization.json"
    require(
        root.is_dir()
        and database.is_file()
        and candidates_root.is_dir()
        and receipts_root.is_dir()
        and not quarantine.exists(),
        "LEGACY_LEARNING_NORMALIZATION_BOUNDARY_INVALID",
        "The exact Learning authority, raw projections, or quarantine boundary is invalid.",
        status="MISMATCH",
        project_root=str(root),
        quarantine_root=str(quarantine),
    )

    candidate_manifest = _file_manifest(candidates_root)
    receipt_manifest = _file_manifest(receipts_root)
    candidate_manifest_sha256 = sha256_bytes(
        canonical_json_bytes(candidate_manifest)
    )
    receipt_manifest_sha256 = sha256_bytes(canonical_json_bytes(receipt_manifest))
    migration_id = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "candidate_manifest_sha256": candidate_manifest_sha256,
                "receipt_manifest_sha256": receipt_manifest_sha256,
            }
        )
    )
    recorded_at = utc_now()
    connection = sqlite3.connect(database, timeout=60)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        from .agent_learning import _apply_learning_schema

        _apply_learning_schema(connection)
        db_candidates = {
            str(row["candidate_id"]): row
            for row in connection.execute(
                """
                SELECT candidate_id,candidate_sha256,candidate_json
                FROM learning_candidate ORDER BY candidate_id
                """
            )
        }
        candidate_ids: list[str] = []
        for path in sorted(candidates_root.glob("*.json")):
            candidate = json.loads(path.read_text(encoding="utf-8"))
            candidate_id = str(candidate.get("candidate_id") or "")
            row = db_candidates.get(candidate_id)
            require(
                row is not None
                and candidate.get("project_id") == project_id
                and str(row["candidate_sha256"])
                == str(candidate.get("candidate_sha256") or "")
                and json.loads(str(row["candidate_json"])) == candidate,
                "LEGACY_LEARNING_CANDIDATE_SQLITE_MISMATCH",
                "A raw Learning candidate is absent from or differs from SQLite authority.",
                status="MISMATCH",
                candidate_id=candidate_id or None,
                file=path.name,
            )
            candidate_ids.append(candidate_id)
        require(
            len(candidate_ids) == len(set(candidate_ids)) == len(db_candidates),
            "LEGACY_LEARNING_CANDIDATE_SET_MISMATCH",
            "Raw and SQLite Learning candidate identity sets differ.",
            status="MISMATCH",
        )

        _initialize_migration_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        receipt_schemas: dict[str, int] = {}
        for path in sorted(receipts_root.glob("*.json")):
            data = path.read_bytes()
            receipt = json.loads(data.decode("utf-8"))
            receipt_sha256 = str(receipt.get("receipt_sha256") or "")
            receipt_body = {
                key: value for key, value in receipt.items() if key != "receipt_sha256"
            }
            schema_id = str(receipt.get("schema") or "UNKNOWN")
            filename_valid = path.stem == receipt_sha256 or (
                schema_id == "evidence-lane.learning-authority-layout-migration.v1"
                and path.name == "authority-layout-migration-v1.json"
            )
            require(
                filename_valid
                and receipt_sha256
                == sha256_bytes(canonical_json_bytes(receipt_body)),
                "LEGACY_LEARNING_RECEIPT_HASH_MISMATCH",
                "A raw Learning receipt failed its immutable hash.",
                status="MISMATCH",
                file=path.name,
            )
            compression, compressed = compress_exact_bytes(data)
            connection.execute(
                """
                INSERT OR IGNORE INTO legacy_learning_receipt_content_cas(
                    receipt_sha256,schema_id,size_bytes,compression,
                    compressed_bytes,first_seen_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    receipt_sha256,
                    schema_id,
                    len(data),
                    compression,
                    compressed,
                    recorded_at,
                ),
            )
            candidate_id = str(receipt.get("candidate_id") or "")
            if candidate_id not in db_candidates:
                candidate_id = ""
            occurred_at = str(
                receipt.get("decided_at")
                or receipt.get("recorded_at")
                or receipt.get("as_of")
                or ""
            )
            encoded_receipt = canonical_json_bytes(receipt).decode("utf-8")
            connection.execute(
                """
                INSERT OR IGNORE INTO learning_receipt(
                    receipt_sha256,schema_id,receipt_kind,project_id,
                    candidate_id,occurred_at,receipt_json
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    receipt_sha256,
                    schema_id,
                    "LEGACY_MIGRATED",
                    project_id,
                    candidate_id or None,
                    occurred_at or None,
                    encoded_receipt,
                ),
            )
            generic = connection.execute(
                "SELECT receipt_json FROM learning_receipt WHERE receipt_sha256=?",
                (receipt_sha256,),
            ).fetchone()
            require(
                generic is not None and str(generic[0]) == encoded_receipt,
                "LEGACY_LEARNING_GENERIC_RECEIPT_READBACK_MISMATCH",
                "A legacy Learning receipt failed SQLite-first receipt readback.",
                status="FAIL",
                file=path.name,
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO legacy_learning_receipt_file(
                    logical_path,receipt_sha256,file_sha256,migrated_at
                ) VALUES(?,?,?,?)
                """,
                (
                    f"legacy-learning-receipt/{path.name}",
                    receipt_sha256,
                    sha256_file(path),
                    recorded_at,
                ),
            )
            stored = connection.execute(
                """
                SELECT c.size_bytes,c.compression,c.compressed_bytes,f.file_sha256
                FROM legacy_learning_receipt_file AS f
                JOIN legacy_learning_receipt_content_cas AS c
                  ON c.receipt_sha256=f.receipt_sha256
                WHERE f.logical_path=?
                """,
                (f"legacy-learning-receipt/{path.name}",),
            ).fetchone()
            require(
                stored is not None
                and str(stored[3]) == sha256_file(path)
                and decompress_exact_bytes(
                    compression=str(stored[1]),
                    payload=bytes(stored[2]),
                    expected_size=int(stored[0]),
                    expected_sha256=sha256_bytes(data),
                )
                == data,
                "LEGACY_LEARNING_RECEIPT_READBACK_MISMATCH",
                "A migrated Learning receipt failed exact SQLite readback.",
                status="FAIL",
                file=path.name,
            )
            receipt_schemas[schema_id] = receipt_schemas.get(schema_id, 0) + 1
        connection.commit()
        require(
            [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
            == ["ok"]
            and not list(connection.execute("PRAGMA foreign_key_check")),
            "LEGACY_LEARNING_SQLITE_INTEGRITY_FAILED",
            "The Learning authority failed integrity after raw receipt migration.",
            status="FAIL",
        )
    finally:
        connection.close()

    quarantine.mkdir(parents=True)
    moved_candidates = quarantine / "candidates"
    moved_receipts = quarantine / "receipts"
    moved_first = False
    try:
        os.replace(candidates_root, moved_candidates)
        moved_first = True
        os.replace(receipts_root, moved_receipts)
    except OSError:
        if moved_first and moved_candidates.exists() and not candidates_root.exists():
            os.replace(moved_candidates, candidates_root)
        if quarantine.is_dir() and not any(quarantine.iterdir()):
            quarantine.rmdir()
        raise
    require(
        _file_manifest(moved_candidates) == candidate_manifest
        and _file_manifest(moved_receipts) == receipt_manifest
        and not candidates_root.exists()
        and not receipts_root.exists(),
        "LEGACY_LEARNING_QUARANTINE_MISMATCH",
        "The quarantined Learning raw projections changed during relocation.",
        status="FAIL",
    )

    receipt_body = {
        "schema": LEGACY_LEARNING_NORMALIZATION_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "migration_id": migration_id,
        "candidate_count": len(candidate_manifest),
        "candidate_identity_set_sha256": sha256_bytes(
            canonical_json_bytes(sorted(candidate_ids))
        ),
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "candidate_sqlite_semantic_parity": True,
        "receipt_count": len(receipt_manifest),
        "receipt_manifest_sha256": receipt_manifest_sha256,
        "receipt_schema_counts": receipt_schemas,
        "receipt_exact_bytes_ingested": True,
        "quarantine_root": str(quarantine),
        "raw_candidate_directory_present": False,
        "raw_receipt_directory_present": False,
        "project_candidate_created": False,
        "learning_hil_invoked": False,
        "project_hil_invoked": False,
        "pointer_moved": False,
        "recorded_at": recorded_at,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    connection = sqlite3.connect(database, timeout=60)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            """
            INSERT INTO legacy_learning_migration_receipt(
                migration_id,receipt_sha256,receipt_json,recorded_at
            ) VALUES(?,?,?,?)
            """,
            (
                migration_id,
                receipt["receipt_sha256"],
                canonical_json_bytes(receipt).decode("utf-8"),
                recorded_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO learning_receipt(
                receipt_sha256,schema_id,receipt_kind,project_id,
                candidate_id,occurred_at,receipt_json
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                receipt["receipt_sha256"],
                LEGACY_LEARNING_NORMALIZATION_SCHEMA,
                "LEGACY_NORMALIZATION",
                project_id,
                None,
                recorded_at,
                canonical_json_bytes(receipt).decode("utf-8"),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    atomic_write_json(receipt_path, receipt)
    return {**receipt, "receipt_path": str(receipt_path)}


__all__ = [
    "LEGACY_LEARNING_NORMALIZATION_CONFIRMATION",
    "LEGACY_LEARNING_NORMALIZATION_SCHEMA",
    "migrate_legacy_learning_files",
]
