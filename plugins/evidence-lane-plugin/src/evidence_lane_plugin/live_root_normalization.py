"""Hash-gated migration from legacy live-root files into SQLite authorities.

The planner never enters the root ``accepted`` directory.  Execution ingests
exact bytes into their owning SQLite authority, reads them back by SHA-256, and
only then removes the redundant source file.  The route is intended for the
new installed runtime transaction; it must not run under an older package.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .compact_storage import compress_exact_bytes, decompress_exact_bytes
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .lanes import LANE_REGISTRY
from .receipt_ledger import (
    append_receipt_bytes,
    initialize_receipt_ledger,
)
from .session_authority import initialize_session_authority, upsert_session
from .timeutil import utc_now

LIVE_ROOT_NORMALIZATION_SCHEMA = "evidence-lane.live-root-normalization.v1"
LIVE_ROOT_NORMALIZATION_CONFIRMATION = (
    "MIGRATE LIVE ROOT TO SQLITE AFTER EXACT HASH READBACK"
)

_ROOT_PROJECT_FILES = {
    "active_pointer.json",
    "active_session.json",
    "capture_route.json",
    "PROJECT_AUTHORITY_MANIFEST.json",
    "project_authority.dot",
    "project_authority.json",
    "project_authority.mmd",
    "project_authority.tools.json",
    "project.json",
}
_RECEIPT_AUTHORITY_FILES = {
    "receipt-ledger.sqlite",
    "receipt-ledger.sqlite-wal",
    "receipt-ledger.sqlite-shm",
    "receipt-ledger.mmd",
    "receipt-ledger.dot",
    "receipt-ledger.tools.json",
    "manifest.json",
}
_SESSION_AUTHORITY_FILES = {
    "session-authority.sqlite",
    "session-authority.sqlite-wal",
    "session-authority.sqlite-shm",
    "session-authority.mmd",
    "session-authority.dot",
    "session-authority.tools.json",
    "manifest.json",
}
_SHA256 = re.compile(r"[A-F0-9]{64}")
_PURGE_ONLY_INTERNAL_PREFIXES = (
    "github_docs_history_",
    "remote_adapter_generated_caches_",
    "removed_",
)


def _files(root: Path) -> list[Path]:
    return [item for item in sorted(root.rglob("*")) if item.is_file()]


def _migration_row(
    project_root: Path,
    source: Path,
    *,
    target_database: Path,
    owner: str,
    logical_path: str,
) -> dict[str, Any]:
    return {
        "source_relative_path": source.relative_to(project_root).as_posix(),
        "target_database_relative_path": target_database.relative_to(
            project_root
        ).as_posix(),
        "owner": owner,
        "logical_path": logical_path,
        "byte_count": source.stat().st_size,
        "sha256": sha256_file(source),
    }


def plan_live_root_normalization(project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if not root.is_dir():
        raise ValueError("LIVE_ROOT_NORMALIZATION_PROJECT_ROOT_MISSING")
    rows: list[dict[str, Any]] = []
    purge_only_rows: list[dict[str, Any]] = []
    receipt_db = root / "receipts" / "receipt-ledger.sqlite"
    session_db = root / "sessions" / "session-authority.sqlite"
    source_db = root / "sources" / "source_authority.sqlite"
    project_db = root / "project_authority" / "project-authority.sqlite"

    internal_sources = root / "internal_sources"
    if internal_sources.is_dir():
        for source in _files(internal_sources):
            relative = source.relative_to(internal_sources)
            top = relative.parts[0].casefold() if relative.parts else ""
            row = _migration_row(
                root,
                source,
                target_database=source_db,
                owner="SOURCE_AUTHORITY",
                logical_path=(
                    "historical-internal/"
                    + relative.as_posix()
                ),
            )
            if top.startswith(_PURGE_ONLY_INTERNAL_PREFIXES):
                purge_only_rows.append(
                    {
                        "source_relative_path": row["source_relative_path"],
                        "classification": "PURGE_NON_AUTHORITY_GENERATED_OR_REMOVED_HISTORY",
                        "byte_count": row["byte_count"],
                        "sha256": row["sha256"],
                    }
                )
            else:
                rows.append(row)
    receipts = root / "receipts"
    if receipts.is_dir():
        rows.extend(
            _migration_row(
                root,
                source,
                target_database=receipt_db,
                owner="RECEIPT_LEDGER",
                logical_path="historical-live-root/"
                + source.relative_to(receipts).as_posix(),
            )
            for source in _files(receipts)
            if source.name not in _RECEIPT_AUTHORITY_FILES
        )
    sessions = root / "sessions"
    if sessions.is_dir():
        rows.extend(
            _migration_row(
                root,
                source,
                target_database=session_db,
                owner="SESSION_AUTHORITY",
                logical_path="legacy-session/" + source.name,
            )
            for source in _files(sessions)
            if source.name not in _SESSION_AUTHORITY_FILES
        )
    travel = root / "direct_state_travel_entries"
    if travel.is_dir():
        rows.extend(
            _migration_row(
                root,
                source,
                target_database=session_db,
                owner="SESSION_AUTHORITY",
                logical_path="legacy-state-travel/" + source.name,
            )
            for source in _files(travel)
        )
    profiles = root / "profiles"
    if profiles.is_dir():
        rows.extend(
            _migration_row(
                root,
                source,
                target_database=project_db,
                owner="PROJECT_AUTHORITY",
                logical_path="legacy-profile/"
                + source.relative_to(profiles).as_posix(),
            )
            for source in _files(profiles)
        )
    for name in sorted(_ROOT_PROJECT_FILES):
        source = root / name
        if source.is_file():
            rows.append(
                _migration_row(
                    root,
                    source,
                    target_database=project_db,
                    owner="PROJECT_AUTHORITY",
                    logical_path="legacy-root/" + name,
                )
            )
    sectors = root / "sectors"
    if sectors.is_dir():
        for lane_id, lane in LANE_REGISTRY.items():
            history = sectors / lane_id / "accepted_history"
            if not history.is_dir():
                continue
            lane_database = sectors / lane_id / lane.sqlite_filename
            rows.extend(
                _migration_row(
                    root,
                    source,
                    target_database=lane_database,
                    owner=f"PROJECT_SECTOR:{lane_id}",
                    logical_path="legacy-accepted-history/"
                    + source.relative_to(history).as_posix(),
                )
                for source in _files(history)
            )
    connector_source = root / "connector_brain.sqlite"
    root / "connector_brain" / "connector-brain.sqlite"
    connector = (
        {
            "source_relative_path": "connector_brain.sqlite",
            "target_relative_path": "connector_brain/connector-brain.sqlite",
            "byte_count": connector_source.stat().st_size,
            "sha256": sha256_file(connector_source),
        }
        if connector_source.is_file()
        else None
    )
    ordered = sorted(
        rows,
        key=lambda row: (
            row["target_database_relative_path"],
            row["logical_path"],
        ),
    )
    core = {
        "schema": LIVE_ROOT_NORMALIZATION_SCHEMA,
        "status": "PASS",
        "project_root": str(root),
        "file_count": len(ordered) + len(purge_only_rows),
        "byte_count": sum(int(row["byte_count"]) for row in ordered)
        + sum(int(row["byte_count"]) for row in purge_only_rows),
        "migration_file_count": len(ordered),
        "migration_byte_count": sum(int(row["byte_count"]) for row in ordered),
        "purge_only_file_count": len(purge_only_rows),
        "purge_only_byte_count": sum(
            int(row["byte_count"]) for row in purge_only_rows
        ),
        "rows": ordered,
        "purge_only_rows": sorted(
            purge_only_rows, key=lambda row: row["source_relative_path"]
        ),
        "connector_brain_relocation": connector,
        "accepted_folder_queried": False,
        "accepted_archive_used_as_authority": False,
        "runtime_root_migration_deferred_until_hidden_install": (
            root / "runtime"
        ).exists(),
        "source_removal_requires_hash_readback": True,
    }
    return {**core, "plan_sha256": sha256_bytes(canonical_json_bytes(core))}


def _initialize_exact_file_table(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS authority_file_content_cas(
            source_sha256 TEXT PRIMARY KEY,
            byte_count INTEGER NOT NULL CHECK(byte_count >= 0),
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_migrated_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS authority_file_migration(
            logical_path TEXT PRIMARY KEY,
            source_relative_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL
                REFERENCES authority_file_content_cas(source_sha256),
            byte_count INTEGER NOT NULL,
            migrated_at TEXT NOT NULL
        ) STRICT;
        CREATE VIRTUAL TABLE IF NOT EXISTS authority_file_migration_fts USING fts5(
            logical_path,
            source_relative_path,
            payload_text,
            content='',
            contentless_delete=1,
            tokenize='unicode61'
        );
        """
    )


def _ingest_purge_manifest(
    database: Path,
    *,
    project_root: Path,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=60)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS purged_non_authority_file(
                source_relative_path TEXT PRIMARY KEY,
                classification TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                byte_count INTEGER NOT NULL,
                purged_at TEXT NOT NULL
            ) STRICT
            """
        )
        for row in rows:
            source = project_root / row["source_relative_path"]
            if (
                not source.is_file()
                or sha256_file(source) != row["sha256"]
                or source.stat().st_size != row["byte_count"]
            ):
                raise RuntimeError("LIVE_ROOT_PURGE_SOURCE_CHANGED")
            connection.execute(
                "INSERT OR IGNORE INTO purged_non_authority_file VALUES(?,?,?,?,?)",
                (
                    row["source_relative_path"],
                    row["classification"],
                    row["sha256"],
                    row["byte_count"],
                    utc_now(),
                ),
            )
            stored = connection.execute(
                "SELECT classification,source_sha256,byte_count "
                "FROM purged_non_authority_file WHERE source_relative_path=?",
                (row["source_relative_path"],),
            ).fetchone()
            if (
                stored is None
                or str(stored[0]) != row["classification"]
                or str(stored[1]) != row["sha256"]
                or int(stored[2]) != row["byte_count"]
            ):
                raise RuntimeError("LIVE_ROOT_PURGE_MANIFEST_READBACK_MISMATCH")
        connection.commit()
    finally:
        connection.close()


def _ingest_generic(
    database: Path,
    *,
    project_root: Path,
    rows: list[dict[str, Any]],
) -> None:
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database, timeout=60)
    try:
        _initialize_exact_file_table(connection)
        for row in rows:
            source = project_root / row["source_relative_path"]
            data = source.read_bytes()
            if sha256_bytes(data) != row["sha256"] or len(data) != row["byte_count"]:
                raise RuntimeError("LIVE_ROOT_NORMALIZATION_SOURCE_CHANGED")
            migrated_at = utc_now()
            compression, compressed_bytes = compress_exact_bytes(data)
            connection.execute(
                "INSERT OR IGNORE INTO authority_file_content_cas "
                "VALUES(?,?,?,?,?)",
                (
                    row["sha256"],
                    row["byte_count"],
                    compression,
                    compressed_bytes,
                    migrated_at,
                ),
            )
            connection.execute(
                "INSERT OR IGNORE INTO authority_file_migration VALUES(?,?,?,?,?)",
                (
                    row["logical_path"],
                    row["source_relative_path"],
                    row["sha256"],
                    row["byte_count"],
                    migrated_at,
                ),
            )
            stored = connection.execute(
                "SELECT m.source_sha256,m.byte_count,c.compression,"
                "c.compressed_bytes FROM authority_file_migration AS m "
                "JOIN authority_file_content_cas AS c "
                "ON c.source_sha256=m.source_sha256 WHERE m.logical_path=?",
                (row["logical_path"],),
            ).fetchone()
            if (
                stored is None
                or str(stored[0]) != row["sha256"]
                or int(stored[1]) != row["byte_count"]
                or decompress_exact_bytes(
                    compression=str(stored[2]),
                    payload=bytes(stored[3]),
                    expected_size=int(stored[1]),
                    expected_sha256=str(stored[0]),
                )
                != data
            ):
                raise RuntimeError("LIVE_ROOT_NORMALIZATION_READBACK_MISMATCH")
            try:
                payload_text = data.decode("utf-8")
            except UnicodeDecodeError:
                payload_text = ""
            fts_rowid = int(
                connection.execute(
                    "SELECT rowid FROM authority_file_migration WHERE logical_path=?",
                    (row["logical_path"],),
                ).fetchone()[0]
            )
            connection.execute(
                "DELETE FROM authority_file_migration_fts WHERE rowid=?",
                (fts_rowid,),
            )
            connection.execute(
                "INSERT INTO authority_file_migration_fts("
                "rowid,logical_path,source_relative_path,payload_text) "
                "VALUES(?,?,?,?)",
                (
                    fts_rowid,
                    row["logical_path"],
                    row["source_relative_path"],
                    payload_text,
                ),
            )
        integrity = [str(value[0]) for value in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise RuntimeError("LIVE_ROOT_NORMALIZATION_SQLITE_INTEGRITY_FAILED")
        connection.commit()
    finally:
        connection.close()


def _project_legacy_sessions(database: Path, project_root: Path, rows: list[dict[str, Any]]) -> None:
    initialize_session_authority(database)
    connection = sqlite3.connect(database, timeout=60)
    try:
        for row in rows:
            source = project_root / row["source_relative_path"]
            payload = json.loads(source.read_text(encoding="utf-8"))
            if row["logical_path"].startswith("legacy-session/"):
                metadata = dict(payload.get("metadata") or {})
                continuity = dict(metadata.get("runtime_continuity") or {})
                upsert_session(
                    connection,
                    project_id=str(payload.get("project_id") or project_root.name),
                    session_id=str(payload["session_id"]),
                    state=str(payload.get("state") or "UNKNOWN"),
                    generation=int(continuity.get("generation") or 0),
                    payload=payload,
                    active_host_task_id=str(
                        metadata.get("current_host_session_id") or ""
                    )
                    or None,
                    recorded_at=str(payload.get("updated_at") or utc_now()),
                )
            elif row["logical_path"].startswith("legacy-state-travel/"):
                receipt = dict(payload.get("receipt") or {})
                binding = dict(receipt.get("host_task_binding") or {})
                destination = dict(
                    dict(receipt.get("destination_orchestration") or {}).get(
                        "destination"
                    )
                    or {}
                )
                source_task = str(
                    dict(binding.get("authoritative_source") or {}).get("task_id")
                    or "UNKNOWN_SOURCE"
                )
                destination_task = str(destination.get("task_id") or "UNKNOWN_DESTINATION")
                payload_bytes = canonical_json_bytes(payload)
                payload_sha256 = sha256_bytes(payload_bytes)
                connection.execute(
                    "INSERT OR IGNORE INTO state_travel_entry VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "travel_" + payload_sha256[:32].lower(),
                        str(receipt.get("session_id") or "UNKNOWN_SESSION"),
                        source_task,
                        destination_task,
                        str(receipt.get("route") or "LEGACY_DIRECT"),
                        str(payload.get("status") or receipt.get("status") or "UNKNOWN"),
                        payload_bytes.decode("utf-8"),
                        payload_sha256,
                        str(receipt.get("bound_at") or utc_now()),
                    ),
                )
        connection.commit()
    finally:
        connection.close()


def _remove_empty_tree(root: Path) -> None:
    if not root.is_dir():
        return
    for directory in sorted(
        (item for item in root.rglob("*") if item.is_dir()),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
    if root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def execute_live_root_normalization(
    project_root: str | Path,
    *,
    expected_plan_sha256: str,
    confirmation: str,
) -> dict[str, Any]:
    root = Path(project_root).resolve()
    if confirmation != LIVE_ROOT_NORMALIZATION_CONFIRMATION:
        raise ValueError("LIVE_ROOT_NORMALIZATION_CONFIRMATION_REQUIRED")
    plan = plan_live_root_normalization(root)
    if plan["plan_sha256"] != expected_plan_sha256:
        raise RuntimeError("LIVE_ROOT_NORMALIZATION_PLAN_CHANGED")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in plan["rows"]:
        grouped.setdefault(row["target_database_relative_path"], []).append(row)
    for relative, rows in grouped.items():
        database = root / relative
        if relative == "receipts/receipt-ledger.sqlite":
            initialize_receipt_ledger(database)
            connection = sqlite3.connect(database, timeout=60)
            try:
                for row in rows:
                    source = root / row["source_relative_path"]
                    result = append_receipt_bytes(
                        connection,
                        logical_path=row["logical_path"],
                        data=source.read_bytes(),
                        receipt_kind="HISTORICAL_LIVE_ROOT_MIGRATION",
                        project_id=root.name,
                    )
                    if result["receipt_sha256"] != row["sha256"]:
                        raise RuntimeError("LIVE_ROOT_NORMALIZATION_READBACK_MISMATCH")
                connection.commit()
            finally:
                connection.close()
        else:
            if relative == "sessions/session-authority.sqlite":
                initialize_session_authority(database)
            _ingest_generic(database, project_root=root, rows=rows)
            if relative == "sessions/session-authority.sqlite":
                _project_legacy_sessions(database, root, rows)
    _ingest_purge_manifest(
        root / "project_authority" / "project-authority.sqlite",
        project_root=root,
        rows=list(plan["purge_only_rows"]),
    )
    connector = plan["connector_brain_relocation"]
    if connector is not None:
        source = root / connector["source_relative_path"]
        target = root / connector["target_relative_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_suffix(target.suffix + ".migration")
        if staged.exists():
            staged.unlink()
        shutil.copy2(source, staged)
        if sha256_file(staged) != connector["sha256"]:
            raise RuntimeError("CONNECTOR_BRAIN_RELOCATION_HASH_MISMATCH")
        connection = sqlite3.connect(staged)
        try:
            if [str(row[0]) for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                raise RuntimeError("CONNECTOR_BRAIN_RELOCATION_INTEGRITY_FAILED")
        finally:
            connection.close()
        if target.exists() and sha256_file(target) != connector["sha256"]:
            raise RuntimeError("CONNECTOR_BRAIN_RELOCATION_TARGET_CONFLICT")
        if not target.exists():
            staged.replace(target)
        else:
            staged.unlink()
    for row in plan["rows"]:
        source = root / row["source_relative_path"]
        if source.is_file():
            if sha256_file(source) != row["sha256"]:
                raise RuntimeError("LIVE_ROOT_NORMALIZATION_SOURCE_CHANGED_BEFORE_PURGE")
            source.unlink()
    for row in plan["purge_only_rows"]:
        source = root / row["source_relative_path"]
        if source.is_file():
            if sha256_file(source) != row["sha256"]:
                raise RuntimeError("LIVE_ROOT_PURGE_SOURCE_CHANGED_BEFORE_PURGE")
            source.unlink()
    if connector is not None:
        source = root / connector["source_relative_path"]
        if source.is_file():
            if sha256_file(source) != connector["sha256"]:
                raise RuntimeError("CONNECTOR_BRAIN_SOURCE_CHANGED_BEFORE_PURGE")
            source.unlink()
    for relative in (
        "internal_sources",
        "direct_state_travel_entries",
        "profiles",
    ):
        _remove_empty_tree(root / relative)
    sectors = root / "sectors"
    if sectors.is_dir():
        for lane_id in LANE_REGISTRY:
            _remove_empty_tree(sectors / lane_id / "accepted_history")
    receipt = {
        "schema": LIVE_ROOT_NORMALIZATION_SCHEMA,
        "status": "PASS",
        "state": "MIGRATED_AND_PURGED_AFTER_READBACK",
        "plan_sha256": plan["plan_sha256"],
        "migrated_file_count": plan["file_count"],
        "migrated_byte_count": plan["byte_count"],
        "exact_byte_migration_file_count": plan["migration_file_count"],
        "purge_only_file_count": plan["purge_only_file_count"],
        "connector_brain_relocated": connector is not None,
        "accepted_folder_queried": False,
        "accepted_archive_used_as_authority": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
        "recorded_at": utc_now(),
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


__all__ = [
    "LIVE_ROOT_NORMALIZATION_CONFIRMATION",
    "LIVE_ROOT_NORMALIZATION_SCHEMA",
    "execute_live_root_normalization",
    "plan_live_root_normalization",
]
