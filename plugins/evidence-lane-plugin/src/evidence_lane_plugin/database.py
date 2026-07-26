"""SQLite schema creation, integrity validation, and bounded read helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Any

from .constants import ENGINE_NAME, ENGINE_VERSION, SCHEMA_VERSION
from .errors import EvidenceLaneError, require

_COUNT_QUERIES = {
    "repositories": 'SELECT COUNT(*) AS count FROM "repositories"',
    "files": 'SELECT COUNT(*) AS count FROM "files"',
    "chunks": 'SELECT COUNT(*) AS count FROM "chunks"',
    "symbols": 'SELECT COUNT(*) AS count FROM "symbols"',
    "imports": 'SELECT COUNT(*) AS count FROM "imports"',
    "dependencies": 'SELECT COUNT(*) AS count FROM "dependencies"',
    "routes": 'SELECT COUNT(*) AS count FROM "routes"',
    "builder_receipts": 'SELECT COUNT(*) AS count FROM "builder_receipts"',
}


def count_tables(
    connection: sqlite3.Connection,
    table_names: Sequence[str],
) -> dict[str, int]:
    """Count only compile-time allowlisted schema tables."""

    unknown = sorted(set(table_names) - set(_COUNT_QUERIES))
    require(
        not unknown,
        "COUNT_TABLE_NOT_ALLOWLISTED",
        "A requested count table is outside the fixed schema allowlist.",
        status="BLOCKED",
        tables=unknown,
    )
    return {
        table: int(connection.execute(_COUNT_QUERIES[table]).fetchone()["count"])
        for table in table_names
    }


def required_lastrowid(cursor: sqlite3.Cursor) -> int:
    value = cursor.lastrowid
    require(
        value is not None,
        "SQLITE_LASTROWID_MISSING",
        "SQLite did not return an identity for a required inserted row.",
        status="FAIL",
    )
    return int(value) if value is not None else 0


def connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    database_path = Path(path).resolve()
    if readonly:
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro",
            uri=True,
            timeout=30,
        )
    else:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def initialize(
    path: str | Path, *, extra_metadata: dict[str, str] | None = None
) -> None:
    schema = (
        files("evidence_lane_plugin").joinpath("schema.sql").read_text(encoding="utf-8")
    )
    with connect(path) as connection:
        connection.executescript(schema)
        metadata = {
            "engine_name": ENGINE_NAME,
            "engine_version": ENGINE_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
        metadata.update(extra_metadata or {})
        connection.executemany(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        connection.commit()


def integrity_report(path: str | Path) -> dict[str, Any]:
    with connect(path, readonly=True) as connection:
        integrity_rows = [
            row[0] for row in connection.execute("PRAGMA integrity_check")
        ]
        foreign_key_rows = [
            dict(row) for row in connection.execute("PRAGMA foreign_key_check")
        ]
        schema_row = connection.execute(
            "SELECT value FROM metadata WHERE key = 'schema_version'"
        ).fetchone()
        counts = count_tables(
            connection,
            (
                "repositories",
                "files",
                "chunks",
                "symbols",
                "imports",
                "dependencies",
                "routes",
                "builder_receipts",
            ),
        )
    return {
        "integrity": integrity_rows,
        "foreign_key_errors": foreign_key_rows,
        "schema_version": schema_row[0] if schema_row else None,
        "counts": counts,
        "valid": integrity_rows == ["ok"]
        and not foreign_key_rows
        and schema_row is not None
        and schema_row[0] == SCHEMA_VERSION,
    }


def validate(path: str | Path) -> dict[str, Any]:
    report = integrity_report(path)
    require(
        report["valid"],
        "SQLITE_VALIDATION_FAILED",
        "The project-version SQLite database failed integrity validation.",
        report=report,
    )
    return report


def bounded_select(
    path: str | Path,
    sql: str,
    parameters: Sequence[Any] = (),
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    normalized = sql.lstrip().lower()
    require(
        normalized.startswith(("select", "with", "pragma")),
        "READ_ONLY_QUERY_REQUIRED",
        "Only read-only SELECT, WITH, or PRAGMA statements are permitted.",
        status="BLOCKED",
    )
    require(
        1 <= limit <= 500,
        "QUERY_LIMIT_INVALID",
        "The query limit must be between 1 and 500.",
        status="BLOCKED",
        limit=limit,
    )
    if ";" in sql.rstrip().rstrip(";"):
        raise EvidenceLaneError(
            "MULTI_STATEMENT_QUERY_BLOCKED",
            "Only one read-only SQL statement is permitted.",
            status="BLOCKED",
        )
    with connect(path, readonly=True) as connection:
        cursor = connection.execute(sql, tuple(parameters))
        rows = cursor.fetchmany(limit + 1)
    if len(rows) > limit:
        rows = rows[:limit]
    return [
        {
            key: (
                value.hex().upper()
                if isinstance(value, bytes) and len(value) <= 128
                else f"<BLOB:{len(value)} bytes>"
                if isinstance(value, bytes)
                else value
            )
            for key, value in dict(row).items()
        }
        for row in rows
    ]
