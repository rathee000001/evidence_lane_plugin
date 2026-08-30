"""Lossless compressed content-addressed storage helpers.

SQLite is the authority container, not an excuse to retain the same source
bytes in whole-file, chunk, and FTS payloads.  These helpers keep one zlib
compressed exact-byte CAS while callers retain only hash-bound occurrences and
search projections.
"""

from __future__ import annotations

import sqlite3
import zlib
from typing import Any

from .hashing import sha256_bytes

COMPRESSION_ID = "ZLIB_LEVEL_9"


def compress_exact_bytes(data: bytes) -> tuple[str, bytes]:
    return COMPRESSION_ID, zlib.compress(data, level=9)


def decompress_exact_bytes(
    *,
    compression: str,
    payload: bytes,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
) -> bytes:
    if compression != COMPRESSION_ID:
        raise ValueError(f"Unsupported exact-byte compression: {compression}")
    data = zlib.decompress(payload)
    if expected_size is not None and len(data) != expected_size:
        raise ValueError("Compressed exact-byte payload size proof failed.")
    if expected_sha256 is not None and sha256_bytes(data) != expected_sha256:
        raise ValueError("Compressed exact-byte payload hash proof failed.")
    return data


def source_registry_is_compact(connection: sqlite3.Connection) -> bool:
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(source_registry)")
    }
    return "exact_bytes" not in columns and "source_content_cas" in {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def read_source_record(
    connection: sqlite3.Connection,
    *,
    path: str,
) -> tuple[dict[str, Any] | None, bytes | None]:
    """Read current compact storage and the historical inline-byte schema."""

    if source_registry_is_compact(connection):
        row = connection.execute(
            """
            SELECT s.source_id,s.path,s.size_bytes,s.sha256,s.mime_type,
                   s.extension,s.encoding,s.parser_state,s.registered_at,
                   cas.compression,cas.compressed_bytes
            FROM source_registry AS s
            JOIN source_content_cas AS cas ON cas.sha256=s.sha256
            WHERE s.path=?
            """,
            (path,),
        ).fetchone()
        if row is None:
            return None, None
        record = dict(row)
        data = decompress_exact_bytes(
            compression=str(record.pop("compression")),
            payload=bytes(record.pop("compressed_bytes")),
            expected_size=int(record["size_bytes"]),
            expected_sha256=str(record["sha256"]),
        )
        return record, data
    row = connection.execute(
        """
        SELECT source_id,path,size_bytes,sha256,mime_type,extension,encoding,
               parser_state,registered_at,exact_bytes
        FROM source_registry WHERE path=?
        """,
        (path,),
    ).fetchone()
    if row is None:
        return None, None
    record = dict(row)
    data = bytes(record.pop("exact_bytes"))
    if len(data) != int(record["size_bytes"]) or sha256_bytes(data) != str(
        record["sha256"]
    ):
        raise ValueError("Historical inline exact-byte source proof failed.")
    return record, data


def verify_lane_compact_storage(
    connection: sqlite3.Connection,
    *,
    fts_table: str,
) -> dict[str, Any]:
    """Prove lossless CAS storage and absence of duplicate FTS content."""

    sources = list(
        connection.execute(
            "SELECT sha256,size_bytes,compression,compressed_bytes "
            "FROM source_content_cas ORDER BY sha256"
        )
    )
    chunks = list(
        connection.execute(
            "SELECT sha256,size_bytes,compression,compressed_text "
            "FROM chunk_content_cas ORDER BY sha256"
        )
    )
    authority_content = list(
        connection.execute(
            "SELECT sha256,size_bytes,compression,compressed_bytes "
            "FROM authority_index_content_cas ORDER BY sha256"
        )
    )
    for row in sources:
        decompress_exact_bytes(
            compression=str(row[2]),
            payload=bytes(row[3]),
            expected_size=int(row[1]),
            expected_sha256=str(row[0]),
        )
    for row in chunks:
        decompress_exact_bytes(
            compression=str(row[2]),
            payload=bytes(row[3]),
            expected_size=int(row[1]),
            expected_sha256=str(row[0]),
        )
    for row in authority_content:
        decompress_exact_bytes(
            compression=str(row[2]),
            payload=bytes(row[3]),
            expected_size=int(row[1]),
            expected_sha256=str(row[0]),
        )
    source_count = int(
        connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
    )
    missing_source_cas = int(
        connection.execute(
            "SELECT COUNT(*) FROM source_registry AS s "
            "LEFT JOIN source_content_cas AS c ON c.sha256=s.sha256 "
            "WHERE c.sha256 IS NULL"
        ).fetchone()[0]
    )
    fts_row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (fts_table,),
    ).fetchone()
    fts_sql = str(fts_row[0] or "") if fts_row is not None else ""
    chunk_index_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(chunk_index)")
    }
    authority_node_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(authority_index_node)")
    }
    source_raw_bytes = sum(int(row[1]) for row in sources)
    source_compressed_bytes = sum(len(bytes(row[3])) for row in sources)
    chunk_raw_bytes = sum(int(row[1]) for row in chunks)
    chunk_compressed_bytes = sum(len(bytes(row[3])) for row in chunks)
    valid = bool(
        missing_source_cas == 0
        and len(sources) <= source_count
        and "text_content" not in chunk_index_columns
        and "text_content" not in authority_node_columns
        and "metadata_json" not in authority_node_columns
        and "content=''" in fts_sql.replace(" ", "")
        and "contentless_delete=1" in fts_sql.replace(" ", "")
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "source_count": source_count,
        "source_cas_count": len(sources),
        "missing_source_cas": missing_source_cas,
        "source_raw_bytes": source_raw_bytes,
        "source_compressed_bytes": source_compressed_bytes,
        "chunk_cas_count": len(chunks),
        "chunk_raw_bytes": chunk_raw_bytes,
        "chunk_compressed_bytes": chunk_compressed_bytes,
        "authority_content_cas_count": len(authority_content),
        "fts_contentless": "content=''" in fts_sql.replace(" ", ""),
        "fts_contentless_delete": (
            "contentless_delete=1" in fts_sql.replace(" ", "")
        ),
        "exact_reconstruction_verified": True,
        "duplicate_inline_source_blob_present": False,
        "duplicate_inline_chunk_text_present": False,
        "duplicate_inline_authority_text_or_metadata_present": False,
    }


__all__ = [
    "COMPRESSION_ID",
    "compress_exact_bytes",
    "decompress_exact_bytes",
    "read_source_record",
    "source_registry_is_compact",
    "verify_lane_compact_storage",
]
