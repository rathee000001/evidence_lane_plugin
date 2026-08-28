"""SQLite execution engine selection and post-build verification.

The core speed law is one explicit transaction for bulk writes.  APSW is the
preferred Codex runtime adapter because it exposes the complete SQLite API;
stdlib sqlite3 remains the deterministic compatibility fallback for source
development before the hidden runtime is installed.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


class SQLiteExecutionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["evidence-lane.sqlite-execution.v1"]
    status: Literal["PASS"]
    engine: Literal["APSW", "STDLIB_SQLITE3"]
    engine_version: str
    sqlite_version: str
    database_path: str
    database_sha256: str
    integrity_check: Literal["ok"]
    journal_mode: str
    page_count: int
    page_size: int
    explicit_bulk_transaction_required: Literal[True]
    apsw_full_api_available: bool
    sqlite_session_available: bool
    sqlite_rbu_available: bool
    sqlite_backup_available: bool
    sqlite_fts5_available: bool
    durable_authority: Literal[True]
    receipt_sha256: str


def apsw_available() -> bool:
    return importlib.util.find_spec("apsw") is not None


def verify_and_optimize_sqlite_authority(
    database_path: str | Path,
) -> SQLiteExecutionReceipt:
    path = Path(database_path).resolve(strict=True)
    if apsw_available():
        import apsw  # type: ignore[import-not-found]

        connection = apsw.Connection(str(path))
        try:
            cursor = connection.cursor()
            integrity = str(cursor.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ValueError(f"SQLITE_INTEGRITY_CHECK_FAILED:{integrity}")
            cursor.execute("PRAGMA optimize")
            journal_mode = str(cursor.execute("PRAGMA journal_mode").fetchone()[0])
            page_count = int(cursor.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(cursor.execute("PRAGMA page_size").fetchone()[0])
            compile_options = {
                str(row[0]) for row in cursor.execute("PRAGMA compile_options")
            }
        finally:
            connection.close()
        engine = "APSW"
        engine_version = str(apsw.apswversion())
        sqlite_version = str(apsw.sqlitelibversion())
        session_available = hasattr(apsw, "Session")
        rbu_available = hasattr(apsw, "RBU")
        backup_available = hasattr(apsw.Connection, "backup")
        fts5_available = any("ENABLE_FTS5" in option for option in compile_options)
    else:
        connection = sqlite3.connect(path, timeout=30)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            if integrity != "ok":
                raise ValueError(f"SQLITE_INTEGRITY_CHECK_FAILED:{integrity}")
            connection.execute("PRAGMA optimize")
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            compile_options = {
                str(row[0]) for row in connection.execute("PRAGMA compile_options")
            }
            connection.commit()
        finally:
            connection.close()
        engine = "STDLIB_SQLITE3"
        engine_version = str(getattr(sqlite3, "version", "stdlib"))
        sqlite_version = sqlite3.sqlite_version
        session_available = False
        rbu_available = False
        backup_available = hasattr(sqlite3.Connection, "backup")
        fts5_available = any("ENABLE_FTS5" in option for option in compile_options)

    core: dict[str, Any] = {
        "schema_name": "evidence-lane.sqlite-execution.v1",
        "status": "PASS",
        "engine": engine,
        "engine_version": engine_version,
        "sqlite_version": sqlite_version,
        "database_path": str(path),
        "database_sha256": sha256_file(path),
        "integrity_check": "ok",
        "journal_mode": journal_mode,
        "page_count": page_count,
        "page_size": page_size,
        "explicit_bulk_transaction_required": True,
        "apsw_full_api_available": engine == "APSW",
        "sqlite_session_available": session_available,
        "sqlite_rbu_available": rbu_available,
        "sqlite_backup_available": backup_available,
        "sqlite_fts5_available": fts5_available,
        "durable_authority": True,
    }
    return SQLiteExecutionReceipt.model_validate(
        {
            **core,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
        }
    )


__all__ = [
    "SQLiteExecutionReceipt",
    "apsw_available",
    "verify_and_optimize_sqlite_authority",
]
