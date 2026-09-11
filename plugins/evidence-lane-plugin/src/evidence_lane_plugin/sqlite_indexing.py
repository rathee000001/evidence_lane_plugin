"""Shared LlamaIndex -> SQLite FTS5/BM25 authority indexing.

SQLite remains the durable authority. LlamaIndex supplies deterministic
document/node construction for every sector and named SQLite authority; the
nodes, metadata, FTS5 index, BM25 query surface, and refresh receipts are all
stored inside that owning SQLite database.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

from llama_index.core import Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.utils import globals_helper

from .compact_storage import compress_exact_bytes, decompress_exact_bytes
from .hashing import canonical_json_bytes, sha256_bytes
from .hybrid_retrieval import RetrievalCandidate, rank_bm25_candidates
from .timeutil import utc_now

AUTHORITY_INDEX_SCHEMA = "evidence-lane.llama-sqlite-authority-index.v1"
AUTHORITY_INDEX_RECEIPT_SCHEMA = (
    "evidence-lane.llama-sqlite-authority-index-refresh.v1"
)
LLAMA_INDEX_CORE_VERSION = "0.14.23"
CHUNK_SIZE_TOKENS = 480
CHUNK_OVERLAP_TOKENS = 64

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_EXCLUDED_TABLE_PREFIXES = (
    "authority_index_",
    "sqlite_",
)
_FTS_MODULE = re.compile(r'\bUSING\s+["`\[]?fts[345]\b', re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class IndexedNode:
    node_id: str
    source_id: str
    ordinal: int
    char_start: int
    char_end: int
    text_content: str
    text_sha256: str
    metadata: dict[str, Any]


def _assert_llama_index_version() -> None:
    installed = version("llama-index-core")
    if installed != LLAMA_INDEX_CORE_VERSION:
        raise RuntimeError(
            "LLAMA_INDEX_CORE_VERSION_MISMATCH: "
            f"expected {LLAMA_INDEX_CORE_VERSION}, got {installed}"
        )


def llama_index_nodes(
    text: str,
    *,
    source_id: str,
    metadata: dict[str, Any] | None = None,
) -> list[IndexedNode]:
    """Create deterministic LlamaIndex nodes with exact source offsets."""

    _assert_llama_index_version()
    exact_text = str(text or "")
    if not exact_text.strip():
        return []
    exact_metadata = dict(metadata or {})
    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE_TOKENS,
        chunk_overlap=CHUNK_OVERLAP_TOKENS,
        include_metadata=True,
        include_prev_next_rel=False,
    )
    document = Document(
        text=exact_text,
        id_=source_id,
        metadata=exact_metadata,
    )
    raw_nodes = splitter.get_nodes_from_documents([document], show_progress=False)
    rows: list[IndexedNode] = []
    for ordinal, node in enumerate(raw_nodes):
        content = str(node.text)
        start = int(node.start_char_idx or 0)
        end = int(node.end_char_idx or (start + len(content)))
        if exact_text[start:end] != content:
            located = exact_text.find(content)
            if located < 0:
                raise ValueError(
                    "LLAMA_INDEX_NODE_SOURCE_OFFSET_MISMATCH: "
                    f"{source_id}:{ordinal}"
                )
            start = located
            end = start + len(content)
        text_sha256 = sha256_bytes(content.encode("utf-8"))
        identity = {
            "schema": AUTHORITY_INDEX_SCHEMA,
            "source_id": source_id,
            "ordinal": ordinal,
            "char_start": start,
            "char_end": end,
            "text_sha256": text_sha256,
        }
        rows.append(
            IndexedNode(
                node_id="linode_"
                + sha256_bytes(canonical_json_bytes(identity))[:32].lower(),
                source_id=source_id,
                ordinal=ordinal,
                char_start=start,
                char_end=end,
                text_content=content,
                text_sha256=text_sha256,
                metadata=exact_metadata,
            )
        )
    return rows


def prewarm_llama_index_sentence_splitter() -> str:
    """Resolve the lazy NLTK/SciPy tokenizer stack before lane workers start.

    LlamaIndex defers its NLTK tokenizer import until the first real split.  If
    multiple lane workers reach that cold import together, Python's import lock
    can leave one worker importing SciPy while another waits inside the same
    NLTK initialization barrier.  A deterministic single-thread split before
    the lane executor removes that cold-start race without serializing lane
    work.
    """

    # A short document does not necessarily enter SentenceSplitter._split and
    # therefore does not touch the lazy Punkt property at all. Resolve that
    # property explicitly on the calling thread; returning from the property
    # proves the complete NLTK import (including its SciPy imports) finished
    # before any lane worker can contend for Python's import lock.
    tokenizer = globals_helper.punkt_tokenizer
    probe_text = "Evidence Lane initializes one sentence. It verifies another."
    spans = list(tokenizer.span_tokenize(probe_text))
    if len(spans) != 2:
        raise RuntimeError("LLAMA_INDEX_PUNKT_TOKENIZER_PREWARM_FAILED")

    nodes = llama_index_nodes(
        "Evidence Lane initializes deterministic sentence splitting.",
        source_id="evidence-lane-llama-index-prewarm",
    )
    if len(nodes) != 1:
        raise RuntimeError("LLAMA_INDEX_SENTENCE_SPLITTER_PREWARM_FAILED")
    return "llama-index-sentence-splitter"


def ensure_authority_index_schema(connection: sqlite3.Connection) -> None:
    legacy_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(authority_index_node)")
    }
    if "text_content" in legacy_columns or "metadata_json" in legacy_columns:
        connection.executescript(
            """
            DROP TABLE IF EXISTS authority_index_fts;
            DROP TABLE IF EXISTS authority_index_node;
            DROP TABLE IF EXISTS authority_index_source;
            DROP TABLE IF EXISTS authority_index_content_cas;
            """
        )
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS authority_index_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS authority_index_source(
            source_id TEXT PRIMARY KEY,
            authority_id TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_identity TEXT NOT NULL,
            source_text_sha256 TEXT NOT NULL,
            metadata_sha256 TEXT NOT NULL
                REFERENCES authority_index_content_cas(sha256),
            recorded_at TEXT NOT NULL,
            UNIQUE(authority_id, source_table, source_identity)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS authority_index_node(
            node_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES authority_index_source(source_id)
                ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            text_sha256 TEXT NOT NULL
                REFERENCES authority_index_content_cas(sha256),
            metadata_sha256 TEXT NOT NULL
                REFERENCES authority_index_content_cas(sha256),
            UNIQUE(source_id, ordinal)
        ) STRICT;
        CREATE VIRTUAL TABLE IF NOT EXISTS authority_index_fts USING fts5(
            node_id UNINDEXED,
            authority_id UNINDEXED,
            source_table UNINDEXED,
            source_identity UNINDEXED,
            text_content,
            content='',
            contentless_delete=1,
            tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS authority_index_refresh_receipt(
            sequence INTEGER PRIMARY KEY,
            authority_id TEXT NOT NULL,
            source_count INTEGER NOT NULL,
            node_count INTEGER NOT NULL,
            indexed_table_count INTEGER NOT NULL,
            llama_index_core_version TEXT NOT NULL,
            chunk_size_tokens INTEGER NOT NULL,
            chunk_overlap_tokens INTEGER NOT NULL,
            prior_receipt_sha256 TEXT,
            recorded_at TEXT NOT NULL,
            receipt_sha256 TEXT NOT NULL UNIQUE,
            receipt_json TEXT NOT NULL
        ) STRICT;
        CREATE INDEX IF NOT EXISTS authority_index_source_table_idx
        ON authority_index_source(authority_id, source_table, source_identity);
        CREATE INDEX IF NOT EXISTS authority_index_node_source_idx
        ON authority_index_node(source_id, ordinal);
        """
    )


def _table_names(connection: sqlite3.Connection) -> list[str]:
    # SQLite owns shadow-table identity. Suffix tests silently discard ordinary
    # authority tables such as user_data or app_config, and miss FTS4 shadows.
    # table_list is supported by the SQLite versions required for our STRICT
    # and contentless-delete schemas.
    table_types = {
        str(row[1]): str(row[2])
        for row in connection.execute("PRAGMA main.table_list")
        if row[0] == "main"
    }
    rows = list(connection.execute(
        "SELECT name,sql FROM main.sqlite_master WHERE type='table' ORDER BY name"
    ))
    return [
        table
        for table, sql in rows
        if _SAFE_IDENTIFIER.fullmatch(table)
        and not table.startswith(_EXCLUDED_TABLE_PREFIXES)
        and table_types.get(table) != "shadow"
        and not (
            table_types.get(table) == "virtual" and _FTS_MODULE.search(sql or "")
        )
        and not table.endswith(
            (
                "_engulfed_row_cas",
                "_engulfed_table_receipt",
                "_engulfed_record",
                "_engulf_receipt",
            )
        )
    ]


def _text_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = list(connection.execute(f'PRAGMA table_info("{table}")'))
    return [
        str(row[1])
        for row in columns
        if str(row[2] or "").upper() in {"", "TEXT", "JSON", "VARCHAR", "CLOB"}
    ]


def _primary_key_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    columns = list(connection.execute(f'PRAGMA table_info("{table}")'))
    return [
        str(row[1])
        for row in sorted(columns, key=lambda value: int(value[5] or 0))
        if int(row[5] or 0) > 0
    ]


def _iter_sources(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
    table_names: Iterable[str] | None,
) -> Iterable[dict[str, Any]]:
    selected = list(table_names) if table_names is not None else _table_names(connection)
    for table in selected:
        if not _SAFE_IDENTIFIER.fullmatch(table):
            raise ValueError(f"Unsafe SQLite authority table: {table}")
        text_columns = _text_columns(connection, table)
        if not text_columns:
            continue
        primary_keys = _primary_key_columns(connection, table)
        cursor = connection.execute(f'SELECT * FROM "{table}"')
        column_names = [str(row[0]) for row in cursor.description or ()]
        for ordinal, row in enumerate(cursor.fetchall()):
            values = dict(zip(column_names, row, strict=True))
            text_values = {
                column: str(values[column])
                for column in text_columns
                if values.get(column) not in {None, ""}
            }
            if not text_values:
                continue
            identity_values = (
                {column: values.get(column) for column in primary_keys}
                if primary_keys
                else {"ordinal": ordinal}
            )
            source_identity = sha256_bytes(
                canonical_json_bytes(
                    {
                        "authority_id": authority_id,
                        "table": table,
                        "identity": identity_values,
                    }
                )
            )
            source_id = f"lisrc_{source_identity[:32].lower()}"
            text = "\n".join(
                f"{column}: {text_values[column]}" for column in sorted(text_values)
            )
            yield {
                "source_id": source_id,
                "authority_id": authority_id,
                "source_table": table,
                "source_identity": source_identity,
                "text": text,
                "metadata": {
                    "authority_id": authority_id,
                    "source_table": table,
                    "source_identity": source_identity,
                    "identity": identity_values,
                    "text_columns": sorted(text_values),
                },
            }


def rebuild_connection_authority_index(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
    table_names: Iterable[str] | None = None,
    recorded_at: str | None = None,
    reset_receipts: bool = False,
) -> dict[str, Any]:
    """Rebuild the derived LlamaIndex node/FTS authority in one transaction."""

    _assert_llama_index_version()
    ensure_authority_index_schema(connection)
    if reset_receipts:
        connection.execute("DELETE FROM authority_index_refresh_receipt")
    sources = list(
        _iter_sources(
            connection,
            authority_id=authority_id,
            table_names=table_names,
        )
    )
    prior = connection.execute(
        "SELECT receipt_sha256 FROM authority_index_refresh_receipt "
        "WHERE authority_id=? ORDER BY sequence DESC LIMIT 1",
        (authority_id,),
    ).fetchone()
    prior_receipt_sha256 = str(prior[0]) if prior is not None else None
    connection.execute("DELETE FROM authority_index_fts")
    connection.execute("DELETE FROM authority_index_node")
    connection.execute("DELETE FROM authority_index_source")
    connection.execute("DELETE FROM authority_index_content_cas")
    node_count = 0
    exact_time = recorded_at or utc_now()
    indexed_tables: set[str] = set()
    for source in sources:
        text_sha256 = sha256_bytes(source["text"].encode("utf-8"))
        metadata_bytes = canonical_json_bytes(source["metadata"])
        metadata_sha256 = sha256_bytes(metadata_bytes)
        metadata_compression, compressed_metadata = compress_exact_bytes(
            metadata_bytes
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO authority_index_content_cas(
                sha256,size_bytes,compression,compressed_bytes,first_seen_at
            ) VALUES(?,?,?,?,?)
            """,
            (
                metadata_sha256,
                len(metadata_bytes),
                metadata_compression,
                compressed_metadata,
                exact_time,
            ),
        )
        connection.execute(
            """
            INSERT INTO authority_index_source(
                source_id, authority_id, source_table, source_identity,
                source_text_sha256, metadata_sha256, recorded_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                source["source_id"],
                authority_id,
                source["source_table"],
                source["source_identity"],
                text_sha256,
                metadata_sha256,
                exact_time,
            ),
        )
        indexed_tables.add(str(source["source_table"]))
        nodes = llama_index_nodes(
            source["text"],
            source_id=source["source_id"],
            metadata=source["metadata"],
        )
        for node in nodes:
            node_text_bytes = node.text_content.encode("utf-8")
            node_compression, compressed_node_text = compress_exact_bytes(
                node_text_bytes
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO authority_index_content_cas(
                    sha256,size_bytes,compression,compressed_bytes,first_seen_at
                ) VALUES(?,?,?,?,?)
                """,
                (
                    node.text_sha256,
                    len(node_text_bytes),
                    node_compression,
                    compressed_node_text,
                    exact_time,
                ),
            )
            node_cursor = connection.execute(
                """
                INSERT INTO authority_index_node(
                    node_id, source_id, ordinal, char_start, char_end,
                    text_sha256, metadata_sha256
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    node.node_id,
                    node.source_id,
                    node.ordinal,
                    node.char_start,
                    node.char_end,
                    node.text_sha256,
                    metadata_sha256,
                ),
            )
            if node_cursor.lastrowid is None:
                raise RuntimeError("Authority index node rowid is unavailable.")
            connection.execute(
                """
                INSERT INTO authority_index_fts(
                    rowid,node_id, authority_id, source_table, source_identity,
                    text_content
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    int(node_cursor.lastrowid),
                    node.node_id,
                    authority_id,
                    source["source_table"],
                    source["source_identity"],
                    node.text_content,
                ),
            )
            node_count += 1
    sequence_row = connection.execute(
        "SELECT COALESCE(MAX(sequence),0)+1 FROM authority_index_refresh_receipt"
    ).fetchone()
    sequence = int(sequence_row[0])
    core = {
        "schema": AUTHORITY_INDEX_RECEIPT_SCHEMA,
        "status": "PASS",
        "sequence": sequence,
        "authority_id": authority_id,
        "source_count": len(sources),
        "node_count": node_count,
        "indexed_table_count": len(indexed_tables),
        "indexed_tables": sorted(indexed_tables),
        "llama_index_core_version": LLAMA_INDEX_CORE_VERSION,
        "node_parser": (
            f"SentenceSplitter({CHUNK_SIZE_TOKENS},{CHUNK_OVERLAP_TOKENS})"
        ),
        "sqlite_backend": "FTS5_BM25",
        "sqlite_remains_authority": True,
        "mmd_dot_remain_traversal_maps": True,
        "prior_receipt_sha256": prior_receipt_sha256,
        "recorded_at": exact_time,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(core))
    receipt = {**core, "receipt_sha256": receipt_sha256}
    connection.execute(
        """
        INSERT INTO authority_index_refresh_receipt(
            sequence, authority_id, source_count, node_count,
            indexed_table_count, llama_index_core_version,
            chunk_size_tokens, chunk_overlap_tokens, prior_receipt_sha256,
            recorded_at, receipt_sha256, receipt_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sequence,
            authority_id,
            len(sources),
            node_count,
            len(indexed_tables),
            LLAMA_INDEX_CORE_VERSION,
            CHUNK_SIZE_TOKENS,
            CHUNK_OVERLAP_TOKENS,
            prior_receipt_sha256,
            exact_time,
            receipt_sha256,
            canonical_json_bytes(receipt).decode("utf-8"),
        ),
    )
    return receipt


def rebuild_sqlite_authority_index(
    database: str | Path,
    *,
    authority_id: str,
    table_names: Iterable[str] | None = None,
    recorded_at: str | None = None,
    reset_receipts: bool = False,
) -> dict[str, Any]:
    path = Path(database).resolve()
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        receipt = rebuild_connection_authority_index(
            connection,
            authority_id=authority_id,
            table_names=table_names,
            recorded_at=recorded_at,
            reset_receipts=reset_receipts,
        )
        connection.commit()
        integrity = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
        if integrity != ["ok"] or list(connection.execute("PRAGMA foreign_key_check")):
            raise RuntimeError("LLAMA_SQLITE_AUTHORITY_INTEGRITY_FAILED")
        return receipt
    finally:
        connection.close()


def query_authority_index(
    connection: sqlite3.Connection,
    *,
    authority_id: str,
    match: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not 1 <= int(limit) <= 100:
        raise ValueError("Authority index limit must be between 1 and 100.")
    rows = connection.execute(
        """
        SELECT n.node_id,s.source_table,s.source_identity,n.text_sha256,
               cas.size_bytes,cas.compression,cas.compressed_bytes,
               bm25(authority_index_fts) AS rank
        FROM authority_index_fts AS f
        JOIN authority_index_node AS n ON n.rowid=f.rowid
        JOIN authority_index_source AS s ON s.source_id=n.source_id
        JOIN authority_index_content_cas AS cas ON cas.sha256=n.text_sha256
        WHERE authority_index_fts MATCH ? AND s.authority_id=?
        ORDER BY rank,n.node_id LIMIT ?
        """,
        (match, authority_id, int(limit)),
    ).fetchall()
    results = [
        {
            "node_id": str(row[0]),
            "source_table": str(row[1]),
            "source_identity": str(row[2]),
            "text_content": decompress_exact_bytes(
                compression=str(row[5]),
                payload=bytes(row[6]),
                expected_size=int(row[4]),
                expected_sha256=str(row[3]),
            ).decode("utf-8"),
            "bm25_rank": float(row[7]),
        }
        for row in rows
    ]
    parity = rank_bm25_candidates(
        match,
        [
            RetrievalCandidate(
                candidate_id=str(row["node_id"]),
                text=str(row["text_content"]),
            )
            for row in results
        ],
        limit=min(limit, max(len(results), 1)),
    )
    parity_by_id = {
        str(row["candidate_id"]): {
            "rank_bm25_score": float(row["score"]),
            "rank_bm25_parity_rank": index,
        }
        for index, row in enumerate(parity["results"], start=1)
    }
    return [
        {
            **row,
            **parity_by_id.get(
                str(row["node_id"]),
                {"rank_bm25_score": 0.0, "rank_bm25_parity_rank": None},
            ),
            "rank_bm25_receipt_sha256": parity["receipt_sha256"],
            "sqlite_bm25_remains_primary": True,
        }
        for row in results
    ]


__all__ = [
    "AUTHORITY_INDEX_RECEIPT_SCHEMA",
    "AUTHORITY_INDEX_SCHEMA",
    "CHUNK_OVERLAP_TOKENS",
    "CHUNK_SIZE_TOKENS",
    "LLAMA_INDEX_CORE_VERSION",
    "IndexedNode",
    "ensure_authority_index_schema",
    "llama_index_nodes",
    "prewarm_llama_index_sentence_splitter",
    "query_authority_index",
    "rebuild_connection_authority_index",
    "rebuild_sqlite_authority_index",
]
