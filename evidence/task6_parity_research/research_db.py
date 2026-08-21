"""Small transactional helpers for the separate Task6 parity-research SQLite.

This module never opens or mutates the canonical Evidence Lane Plan store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "TASK6_PARITY_RESEARCH.sqlite"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def directory_fingerprint(paths: Iterable[Path], root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    count = 0
    byte_count = 0
    for path in sorted(paths, key=lambda item: item.as_posix().lower()):
        if not path.is_file():
            continue
        data = path.read_bytes()
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        count += 1
        byte_count += len(data)
    return digest.hexdigest().upper(), count, byte_count


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def upsert_fts(
    connection: sqlite3.Connection,
    *,
    doc_id: str,
    doc_type: str,
    title: str,
    body: str,
    evidence_locator: str,
) -> None:
    connection.execute("DELETE FROM research_fts WHERE doc_id = ?", (doc_id,))
    connection.execute(
        "INSERT INTO research_fts(doc_id, doc_type, title, body, evidence_locator) VALUES (?, ?, ?, ?, ?)",
        (doc_id, doc_type, title, body, evidence_locator),
    )


def transition_step(
    connection: sqlite3.Connection,
    *,
    step_no: int,
    to_status: str,
    evidence_locator: str,
    event_id: str,
    occurred_at: str | None = None,
) -> None:
    timestamp = occurred_at or utc_now()
    row = connection.execute(
        "SELECT status FROM research_step WHERE step_no = ?", (step_no,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown research step {step_no}")
    from_status = str(row[0])
    connection.execute(
        "UPDATE research_step SET status = ?, evidence_locator = ?, updated_at = ? WHERE step_no = ?",
        (to_status, evidence_locator, timestamp, step_no),
    )
    connection.execute(
        "INSERT OR IGNORE INTO research_step_history(event_id, step_no, from_status, to_status, evidence_locator, occurred_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (event_id, step_no, from_status, to_status, evidence_locator, timestamp),
    )


def append_audit_event(
    connection: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: str | None = None,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO audit_event(event_id, event_type, payload_json, occurred_at) VALUES (?, ?, ?, ?)",
        (
            event_id,
            event_type,
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            occurred_at or utc_now(),
        ),
    )

