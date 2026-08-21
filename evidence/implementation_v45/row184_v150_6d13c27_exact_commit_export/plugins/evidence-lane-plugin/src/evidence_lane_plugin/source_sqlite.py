"""Read-only forensic intake for direct and ZIP-embedded SQLite authorities."""

from __future__ import annotations

import sqlite3
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .source_authority import initialize_source_authority_registry
from .timeutil import utc_now

SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
SQLITE_HEADER = b"SQLite format 3\x00"


@dataclass(frozen=True, slots=True)
class RegisteredSQLiteAsset:
    ordinal: int
    object_id: str
    source_pointer: str
    source_kind: str
    member_path: str
    size_bytes: int | None
    byte_sha256: str | None
    policy_state: str
    policy_reason: str

    @property
    def key(self) -> tuple[str, str]:
        return self.object_id, self.member_path


def _connect_registry(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _registered_assets(path: Path, batch_id: str) -> list[RegisteredSQLiteAsset]:
    with _connect_registry(path) as connection:
        member_rows = list(
            connection.execute(
                """SELECT o.ordinal, s.object_id, s.source_pointer, s.kind,
                m.member_path, m.size_bytes, m.sha256, m.policy_state, m.policy_reason
                FROM source_occurrence o
                JOIN source_object s USING(object_id)
                JOIN source_member m USING(object_id)
                WHERE o.batch_id=? AND (
                    lower(m.member_path) GLOB '*.db'
                    OR lower(m.member_path) GLOB '*.sqlite'
                    OR lower(m.member_path) GLOB '*.sqlite3'
                )
                ORDER BY o.ordinal, m.member_path COLLATE NOCASE, m.member_path""",
                (batch_id,),
            )
        )
        direct_rows = list(
            connection.execute(
                """SELECT o.ordinal, s.object_id, s.source_pointer, s.kind,
                '<direct-file>' AS member_path, s.size_bytes, s.byte_sha256 AS sha256,
                'INCLUDED' AS policy_state, 'POLICY_APPROVED' AS policy_reason
                FROM source_occurrence o JOIN source_object s USING(object_id)
                WHERE o.batch_id=? AND s.kind='file' AND (
                    lower(s.source_pointer) GLOB '*.db'
                    OR lower(s.source_pointer) GLOB '*.sqlite'
                    OR lower(s.source_pointer) GLOB '*.sqlite3'
                ) ORDER BY o.ordinal""",
                (batch_id,),
            )
        )
    return [
        RegisteredSQLiteAsset(
            ordinal=int(row["ordinal"]),
            object_id=str(row["object_id"]),
            source_pointer=str(row["source_pointer"]),
            source_kind=str(row["kind"]),
            member_path=str(row["member_path"]),
            size_bytes=(
                int(row["size_bytes"]) if row["size_bytes"] is not None else None
            ),
            byte_sha256=(str(row["sha256"]) if row["sha256"] else None),
            policy_state=str(row["policy_state"]),
            policy_reason=str(row["policy_reason"]),
        )
        for row in [*member_rows, *direct_rows]
    ]


def _archive_skip_objects(path: Path, batch_id: str) -> set[str]:
    with _connect_registry(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                """SELECT archive_object_id FROM source_archive_receipt
                WHERE batch_id=? AND skip_status=
                'SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART'""",
                (batch_id,),
            )
        }


def _direct_path(asset: RegisteredSQLiteAsset) -> Path:
    source = Path(asset.source_pointer)
    if asset.member_path == "<direct-file>":
        return source.resolve()
    return (source / PurePosixPath(asset.member_path)).resolve()


def _open_direct(asset: RegisteredSQLiteAsset) -> tuple[sqlite3.Connection, str]:
    target = _direct_path(asset)
    require(
        target.is_file(),
        "SOURCE_SQLITE_ASSET_MISSING",
        "A registered direct SQLite asset is no longer present.",
        status="STALE",
        source_pointer=asset.source_pointer,
        member_path=asset.member_path,
    )
    require(
        target.stat().st_size == asset.size_bytes,
        "SOURCE_SQLITE_SIZE_CHANGED",
        "A registered SQLite asset changed size after source intake.",
        status="STALE",
        source_pointer=asset.source_pointer,
        member_path=asset.member_path,
    )
    actual_sha = sha256_file(target)
    require(
        actual_sha == asset.byte_sha256,
        "SOURCE_SQLITE_BYTES_CHANGED",
        "A registered SQLite asset changed bytes after source intake.",
        status="STALE",
        source_pointer=asset.source_pointer,
        member_path=asset.member_path,
    )
    connection = sqlite3.connect(
        f"file:{target.as_posix()}?mode=ro&immutable=1",
        uri=True,
        timeout=30,
    )
    return connection, "sqlite_uri_mode_ro_immutable_query_only"


def _open_embedded(
    asset: RegisteredSQLiteAsset,
    *,
    max_embedded_member_bytes: int,
) -> tuple[sqlite3.Connection, str]:
    require(
        asset.size_bytes is not None
        and asset.size_bytes <= max_embedded_member_bytes,
        "SOURCE_SQLITE_EMBEDDED_SIZE_LIMIT",
        "An embedded SQLite member exceeds the bounded in-memory inspection limit.",
        status="BLOCKED",
        source_pointer=asset.source_pointer,
        member_path=asset.member_path,
        size_bytes=asset.size_bytes,
        max_embedded_member_bytes=max_embedded_member_bytes,
    )
    archive_path = Path(asset.source_pointer).resolve()
    require(
        archive_path.is_file(),
        "SOURCE_SQLITE_ARCHIVE_MISSING",
        "A registered SQLite archive is no longer present.",
        status="STALE",
        source_pointer=asset.source_pointer,
    )
    with zipfile.ZipFile(archive_path) as archive:
        matches = [
            info
            for info in archive.infolist()
            if not info.is_dir()
            and info.filename.replace("\\", "/") == asset.member_path
        ]
        require(
            len(matches) == 1,
            "SOURCE_SQLITE_ARCHIVE_MEMBER_MISMATCH",
            "The exact registered SQLite archive member was not found once.",
            status="STALE",
            source_pointer=asset.source_pointer,
            member_path=asset.member_path,
            match_count=len(matches),
        )
        payload = archive.read(matches[0])
    require(
        len(payload) == asset.size_bytes
        and sha256_bytes(payload) == asset.byte_sha256,
        "SOURCE_SQLITE_ARCHIVE_MEMBER_CHANGED",
        "An embedded SQLite member no longer matches registered byte authority.",
        status="STALE",
        source_pointer=asset.source_pointer,
        member_path=asset.member_path,
    )
    require(
        payload.startswith(SQLITE_HEADER),
        "SOURCE_SQLITE_HEADER_INVALID",
        "An embedded member named as SQLite lacks the SQLite format header.",
        status="MISMATCH",
        source_pointer=asset.source_pointer,
        member_path=asset.member_path,
    )
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(payload)
    except AttributeError as error:
        connection.close()
        raise EvidenceLaneError(
            "SOURCE_SQLITE_DESERIALIZE_UNAVAILABLE",
            "This Python SQLite runtime cannot inspect embedded databases in memory.",
            status="BLOCKED",
        ) from error
    return connection, "sqlite_in_memory_deserialize_query_only"


def _inspect_connection(
    connection: sqlite3.Connection,
    asset: RegisteredSQLiteAsset,
    *,
    inspection_mode: str,
    exact_count_max_database_bytes: int,
) -> dict[str, Any]:
    byte_sha256 = asset.byte_sha256
    require(
        byte_sha256 is not None,
        "SOURCE_SQLITE_AUTHORITY_INCOMPLETE",
        "A SQLite inspection requires registered byte authority.",
        status="BLOCKED",
    )
    assert byte_sha256 is not None
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    integrity = [
        str(row[0]) for row in connection.execute("PRAGMA integrity_check").fetchmany(101)
    ]
    integrity_truncated = len(integrity) > 100
    if integrity_truncated:
        integrity = integrity[:100]
    foreign_key_errors = [
        dict(row)
        for row in connection.execute("PRAGMA foreign_key_check").fetchmany(1001)
    ]
    foreign_key_errors_truncated = len(foreign_key_errors) > 1000
    if foreign_key_errors_truncated:
        foreign_key_errors = foreign_key_errors[:1000]
    schema_rows = [
        dict(row)
        for row in connection.execute(
            """SELECT type, name, tbl_name, sql FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"""
        )
    ]
    schema_objects = [
        {
            "object_type": str(row["type"]),
            "object_name": str(row["name"]),
            "table_name": str(row["tbl_name"]),
            "sql_sha256": (
                sha256_bytes(str(row["sql"]).encode("utf-8"))
                if row["sql"] is not None
                else None
            ),
        }
        for row in schema_rows
    ]
    schema_sha256 = sha256_bytes(canonical_json_bytes(schema_objects))
    table_stats: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    fts_tables: list[str] = []
    for row in schema_rows:
        if row["type"] != "table":
            continue
        table_name = str(row["name"])
        quoted = table_name.replace('"', '""')
        sql = str(row.get("sql") or "")
        virtual = sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
        fts = virtual and "USING FTS" in sql.upper()
        if fts:
            fts_tables.append(table_name)
        for fk in connection.execute(
            f'PRAGMA foreign_key_list("{quoted}")'  # nosec B608
        ):
            payload = dict(fk)
            foreign_keys.append(
                {
                    "from_table": table_name,
                    "foreign_key_id": int(payload["id"]),
                    "sequence_id": int(payload["seq"]),
                    "to_table": payload.get("table"),
                    "from_column": payload.get("from"),
                    "to_column": payload.get("to"),
                    "on_update": payload.get("on_update"),
                    "on_delete": payload.get("on_delete"),
                    "match_rule": payload.get("match"),
                }
            )
        if virtual:
            row_count = None
            count_state = "VIRTUAL_TABLE_NOT_COUNTED"
        elif (asset.size_bytes or 0) > exact_count_max_database_bytes:
            row_count = None
            count_state = "DEFERRED_TO_DELTA075_SIZE_BOUND"
        else:
            try:
                row_count = int(
                    connection.execute(  # nosec B608
                        f'SELECT COUNT(*) FROM "{quoted}"'
                    ).fetchone()[0]
                )
                count_state = "EXACT"
            except sqlite3.DatabaseError as error:
                row_count = None
                count_state = f"UNREADABLE:{type(error).__name__}"
        table_stats.append(
            {
                "table_name": table_name,
                "row_count": row_count,
                "count_state": count_state,
            }
        )
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
    page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    encoding = str(connection.execute("PRAGMA encoding").fetchone()[0])
    passed = integrity == ["ok"] and not foreign_key_errors
    receipt = {
        "schema": "evidence-lane.source-sqlite-inspection.v1",
        "canonical_asset_id": f"sqlite_{byte_sha256[:32].lower()}",
        "byte_sha256": byte_sha256,
        "size_bytes": asset.size_bytes,
        "inspection_mode": inspection_mode,
        "status": "PASS" if passed else "FAIL",
        "integrity": integrity,
        "integrity_truncated": integrity_truncated,
        "foreign_key_error_count": len(foreign_key_errors),
        "foreign_key_errors_truncated": foreign_key_errors_truncated,
        "user_version": user_version,
        "application_id": application_id,
        "page_count": page_count,
        "page_size": page_size,
        "encoding": encoding,
        "schema_sha256": schema_sha256,
        "schema_object_count": len(schema_objects),
        "table_count": len(table_stats),
        "fts_table_count": len(fts_tables),
        "exact_count_table_count": sum(
            row["count_state"] == "EXACT" for row in table_stats
        ),
        "deferred_count_table_count": sum(
            row["count_state"] == "DEFERRED_TO_DELTA075_SIZE_BOUND"
            for row in table_stats
        ),
        "imported_sql_executed": False,
        "source_bytes_mutated": False,
        "source_payload_persisted": False,
    }
    return {
        "receipt": receipt,
        "schema_objects": schema_objects,
        "table_stats": table_stats,
        "foreign_keys": foreign_keys,
        "foreign_key_errors": foreign_key_errors,
    }


def _inspect_asset(
    asset: RegisteredSQLiteAsset,
    *,
    max_embedded_member_bytes: int,
    exact_count_max_database_bytes: int,
) -> dict[str, Any]:
    require(
        asset.byte_sha256 is not None and asset.size_bytes is not None,
        "SOURCE_SQLITE_AUTHORITY_INCOMPLETE",
        "A SQLite asset requires registered bytes and size before inspection.",
        status="BLOCKED",
        object_id=asset.object_id,
        member_path=asset.member_path,
    )
    connection: sqlite3.Connection | None = None
    try:
        if asset.source_kind == "zip":
            connection, mode = _open_embedded(
                asset,
                max_embedded_member_bytes=max_embedded_member_bytes,
            )
        else:
            direct = _direct_path(asset)
            with direct.open("rb") as handle:
                header = handle.read(len(SQLITE_HEADER))
            require(
                header == SQLITE_HEADER,
                "SOURCE_SQLITE_HEADER_INVALID",
                "A registered file named as SQLite lacks the SQLite format header.",
                status="MISMATCH",
                source_pointer=asset.source_pointer,
                member_path=asset.member_path,
            )
            connection, mode = _open_direct(asset)
        return _inspect_connection(
            connection,
            asset,
            inspection_mode=mode,
            exact_count_max_database_bytes=exact_count_max_database_bytes,
        )
    finally:
        if connection is not None:
            connection.close()


def inspect_registered_sqlite_assets(
    registry_path: str | Path,
    batch_id: str,
    *,
    max_embedded_member_bytes: int = 768 * 1024 * 1024,
    exact_count_max_database_bytes: int = 32 * 1024 * 1024,
) -> dict[str, Any]:
    """Inspect every registered SQLite occurrence, deduplicated by frozen bytes."""

    target = initialize_source_authority_registry(registry_path)
    with _connect_registry(target) as connection:
        require(
            connection.execute(
                "SELECT 1 FROM intake_batch WHERE batch_id=?", (batch_id,)
            ).fetchone()
            is not None,
            "SOURCE_AUTHORITY_BATCH_MISSING",
            "The requested source authority batch is not registered.",
            status="MISMATCH",
            batch_id=batch_id,
        )
    assets = _registered_assets(target, batch_id)
    skip_archive_objects = _archive_skip_objects(target, batch_id)
    inspectable = [
        asset
        for asset in assets
        if asset.policy_state == "INCLUDED"
        and asset.byte_sha256
        and not (
            asset.source_kind == "zip" and asset.object_id in skip_archive_objects
        )
    ]
    by_sha: defaultdict[str, list[RegisteredSQLiteAsset]] = defaultdict(list)
    for asset in inspectable:
        by_sha[str(asset.byte_sha256)].append(asset)
    canonical_by_sha = {
        sha: min(
            rows,
            key=lambda row: (
                row.source_kind == "zip",
                row.ordinal,
                row.member_path.casefold(),
            ),
        )
        for sha, rows in by_sha.items()
    }
    inspections: dict[str, dict[str, Any]] = {}
    for sha in sorted(canonical_by_sha):
        asset = canonical_by_sha[sha]
        try:
            inspections[sha] = _inspect_asset(
                asset,
                max_embedded_member_bytes=max_embedded_member_bytes,
                exact_count_max_database_bytes=exact_count_max_database_bytes,
            )
        except (EvidenceLaneError, OSError, sqlite3.DatabaseError, zipfile.BadZipFile) as error:
            if isinstance(error, EvidenceLaneError):
                code, status = error.code, error.status
            else:
                code, status = type(error).__name__, "FAIL"
            failure_receipt: dict[str, Any] = {
                "schema": "evidence-lane.source-sqlite-inspection.v1",
                "canonical_asset_id": f"sqlite_{sha[:32].lower()}",
                "byte_sha256": sha,
                "size_bytes": asset.size_bytes,
                "inspection_mode": "failed_before_read_only_inspection",
                "status": status,
                "error_code": code,
                "integrity": [],
                "foreign_key_error_count": 0,
                "user_version": None,
                "page_count": None,
                "page_size": None,
                "schema_sha256": None,
                "schema_object_count": 0,
                "table_count": 0,
                "fts_table_count": 0,
                "imported_sql_executed": False,
                "source_bytes_mutated": False,
                "source_payload_persisted": False,
            }
            inspections[sha] = {
                "receipt": failure_receipt,
                "schema_objects": [],
                "table_stats": [],
                "foreign_keys": [],
                "foreign_key_errors": [],
            }
    receipt_rows: dict[str, dict[str, Any]] = {}
    for sha, inspection in inspections.items():
        receipt = dict(inspection["receipt"])
        canonical_receipt_sha = sha256_bytes(canonical_json_bytes(receipt))
        receipt_rows[sha] = {
            **receipt,
            "receipt_sha256": canonical_receipt_sha,
        }
    nonpass_findings: list[dict[str, Any]] = []
    for sha in sorted(receipt_rows):
        receipt = receipt_rows[sha]
        if receipt["status"] == "PASS":
            continue
        canonical = canonical_by_sha[sha]
        if receipt.get("error_code"):
            error_code = str(receipt["error_code"])
        elif int(receipt.get("foreign_key_error_count") or 0):
            error_code = "SOURCE_SQLITE_FOREIGN_KEY_CHECK_FAILED"
        else:
            error_code = "SOURCE_SQLITE_INTEGRITY_CHECK_FAILED"
        nonpass_findings.append(
            {
                "canonical_asset_id": receipt["canonical_asset_id"],
                "status": receipt["status"],
                "error_code": error_code,
                "source_ordinal": canonical.ordinal,
                "member_path": canonical.member_path,
            }
        )
    asset_rows: list[dict[str, Any]] = []
    for asset in assets:
        asset_receipt: dict[str, Any] | None = receipt_rows.get(
            str(asset.byte_sha256)
        )
        state: str
        canonical_asset_id: str | None
        schema_sha: str | None
        asset_receipt_sha: str | None
        if asset.policy_state != "INCLUDED" or not asset.byte_sha256:
            state = "POLICY_EXCLUDED_NOT_INSPECTED"
            canonical_asset_id = None
            schema_sha = None
            asset_receipt_sha = None
        elif asset.source_kind == "zip" and asset.object_id in skip_archive_objects:
            state = "SKIPPED_EXACT_EXTRACTED_COUNTERPART"
            canonical_asset_id = (
                str(asset_receipt["canonical_asset_id"])
                if asset_receipt is not None
                else None
            )
            schema_sha = (
                str(asset_receipt["schema_sha256"])
                if asset_receipt is not None
                and asset_receipt["schema_sha256"] is not None
                else None
            )
            asset_receipt_sha = (
                str(asset_receipt["receipt_sha256"])
                if asset_receipt is not None
                else None
            )
        else:
            require(
                asset_receipt is not None,
                "SOURCE_SQLITE_RECEIPT_MISSING",
                "An inspectable SQLite occurrence lacks its canonical receipt.",
                status="FAIL",
                object_id=asset.object_id,
                member_path=asset.member_path,
            )
            assert asset_receipt is not None
            canonical = canonical_by_sha[str(asset.byte_sha256)]
            state = (
                "CANONICAL_" + str(asset_receipt["status"])
                if asset.key == canonical.key
                else "DEDUP_REUSE_" + str(asset_receipt["status"])
            )
            canonical_asset_id = str(asset_receipt["canonical_asset_id"])
            schema_sha = (
                str(asset_receipt["schema_sha256"])
                if asset_receipt["schema_sha256"] is not None
                else None
            )
            asset_receipt_sha = str(asset_receipt["receipt_sha256"])
        asset_rows.append(
            {
                "object_id": asset.object_id,
                "member_path": asset.member_path,
                "inspection_state": state,
                "schema_sha256": schema_sha,
                "byte_sha256": asset.byte_sha256,
                "size_bytes": asset.size_bytes,
                "canonical_asset_id": canonical_asset_id,
                "inspection_receipt_sha256": asset_receipt_sha,
            }
        )
    append_status = "IDEMPOTENT_REUSE"
    with _connect_registry(target) as connection, connection:
        for sha in sorted(inspections):
            inspection = inspections[sha]
            receipt = receipt_rows[sha]
            existing = connection.execute(
                "SELECT receipt_sha256 FROM source_sqlite_receipt WHERE canonical_asset_id=?",
                (receipt["canonical_asset_id"],),
            ).fetchone()
            require(
                existing is None
                or existing["receipt_sha256"] == receipt["receipt_sha256"],
                "SOURCE_SQLITE_RECEIPT_MISMATCH",
                "An immutable SQLite receipt resolved to different evidence.",
                status="MISMATCH",
                canonical_asset_id=receipt["canonical_asset_id"],
            )
            if existing is None:
                append_status = "APPENDED"
                connection.execute(
                    """INSERT INTO source_sqlite_receipt VALUES
                    (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        receipt["canonical_asset_id"],
                        receipt["byte_sha256"],
                        receipt["size_bytes"],
                        receipt["inspection_mode"],
                        receipt["status"],
                        canonical_json_bytes(receipt.get("integrity", [])).decode(
                            "utf-8"
                        ).strip(),
                        receipt.get("foreign_key_error_count", 0),
                        receipt.get("user_version"),
                        receipt.get("page_count"),
                        receipt.get("page_size"),
                        receipt.get("schema_sha256"),
                        receipt.get("schema_object_count", 0),
                        receipt.get("table_count", 0),
                        receipt.get("fts_table_count", 0),
                        canonical_json_bytes(receipt).decode("utf-8").strip(),
                        receipt["receipt_sha256"],
                        utc_now(),
                    ),
                )
                canonical = canonical_by_sha[sha]
                for row in inspection["schema_objects"]:
                    connection.execute(
                        "INSERT INTO source_sqlite_schema_object VALUES (?, ?, ?, ?, ?)",
                        (
                            canonical.object_id,
                            canonical.member_path,
                            row["object_type"],
                            row["object_name"],
                            row["sql_sha256"],
                        ),
                    )
                for row in inspection["table_stats"]:
                    connection.execute(
                        "INSERT INTO source_sqlite_table_stat VALUES (?, ?, ?, ?, ?)",
                        (
                            canonical.object_id,
                            canonical.member_path,
                            row["table_name"],
                            row["row_count"],
                            row["count_state"],
                        ),
                    )
                for row in inspection["foreign_keys"]:
                    connection.execute(
                        "INSERT INTO source_sqlite_foreign_key VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            receipt["canonical_asset_id"],
                            row["from_table"],
                            row["foreign_key_id"],
                            row["sequence_id"],
                            row["to_table"],
                            row["from_column"],
                            row["to_column"],
                            row["on_update"],
                            row["on_delete"],
                            row["match_rule"],
                        ),
                    )
        for row in asset_rows:
            existing = connection.execute(
                """SELECT * FROM source_sqlite_asset
                WHERE object_id=? AND member_path=?""",
                (row["object_id"], row["member_path"]),
            ).fetchone()
            comparable = {
                key: row[key]
                for key in (
                    "inspection_state",
                    "schema_sha256",
                    "byte_sha256",
                    "size_bytes",
                    "canonical_asset_id",
                    "inspection_receipt_sha256",
                )
            }
            require(
                existing is None
                or all(existing[key] == value for key, value in comparable.items()),
                "SOURCE_SQLITE_ASSET_RECEIPT_MISMATCH",
                "An immutable SQLite occurrence receipt resolved to different evidence.",
                status="MISMATCH",
                object_id=row["object_id"],
                member_path=row["member_path"],
            )
            if existing is None:
                append_status = "APPENDED"
                connection.execute(
                    """INSERT INTO source_sqlite_asset(
                    object_id, member_path, inspection_state, schema_sha256,
                    byte_sha256, size_bytes, canonical_asset_id,
                    inspection_receipt_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["object_id"],
                        row["member_path"],
                        row["inspection_state"],
                        row["schema_sha256"],
                        row["byte_sha256"],
                        row["size_bytes"],
                        row["canonical_asset_id"],
                        row["inspection_receipt_sha256"],
                    ),
                )
        receipt_set = sorted(
            {
                str(row["receipt_sha256"])
                for row in receipt_rows.values()
            }
        )
        asset_set_sha = sha256_bytes(canonical_json_bytes(asset_rows))
        receipt_set_sha = sha256_bytes(canonical_json_bytes(receipt_set))
        event = {
            "schema": "evidence-lane.source-sqlite-intake.summary.v1",
            "batch_id": batch_id,
            "asset_occurrence_count": len(assets),
            "included_asset_count": sum(
                row.policy_state == "INCLUDED" for row in assets
            ),
            "policy_excluded_asset_count": sum(
                row.policy_state != "INCLUDED" for row in assets
            ),
            "skip_exact_counterpart_asset_count": sum(
                row["inspection_state"]
                == "SKIPPED_EXACT_EXTRACTED_COUNTERPART"
                for row in asset_rows
            ),
            "unique_byte_authority_count": len(by_sha),
            "canonical_inspection_count": len(inspections),
            "canonical_pass_count": sum(
                row["receipt"]["status"] == "PASS"
                for row in inspections.values()
            ),
            "canonical_failure_count": len(nonpass_findings),
            "asset_set_sha256": asset_set_sha,
            "receipt_set_sha256": receipt_set_sha,
            "max_embedded_member_bytes": max_embedded_member_bytes,
            "exact_count_max_database_bytes": exact_count_max_database_bytes,
            "imported_sql_executed": False,
            "source_bytes_mutated": False,
            "source_payloads_extracted_to_filesystem": False,
            "candidate_created": False,
            "pointer_moved": False,
        }
        event_sha = sha256_bytes(canonical_json_bytes(event))
        event_id = f"registry_{event_sha[:32].lower()}"
        if connection.execute(
            "SELECT 1 FROM registry_event WHERE event_id=?", (event_id,)
        ).fetchone() is None:
            append_status = "APPENDED"
            connection.execute(
                "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    batch_id,
                    "source.sqlite.intake.completed",
                    canonical_json_bytes(event).decode("utf-8").strip(),
                    event_sha,
                    utc_now(),
                ),
            )
    return {
        "status": "PASS" if not nonpass_findings else "MISMATCH",
        "append_status": append_status,
        **event,
        "event_sha256": event_sha,
        "failure_samples": nonpass_findings[:100],
        "failure_samples_truncated": len(nonpass_findings) > 100,
    }
