"""Optional SQLite-local vector retrieval behind mandatory FTS5/BM25."""

from __future__ import annotations

import importlib.util
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .shared_tool_assets import resolve_shared_asset

_MODEL_CACHE: dict[tuple[str, str, str], Any] = {}
_MODEL_LOCK = threading.Lock()


class VectorModelContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_id: str
    dimension: int = Field(ge=2, le=65_536)
    local_model_path: Path | None = None
    allow_network_download: bool = False


def sqlite_vec_available() -> bool:
    return importlib.util.find_spec("sqlite_vec") is not None


def sentence_transformers_available() -> bool:
    return importlib.util.find_spec("sentence_transformers") is not None


def _directory_identity(path: Path) -> str:
    rows = [
        {
            "path": item.relative_to(path).as_posix(),
            "bytes": item.stat().st_size,
            "sha256": sha256_file(item),
        }
        for item in sorted(path.rglob("*"))
        if item.is_file() and ".cache" not in item.parts
    ]
    return sha256_bytes(canonical_json_bytes(rows))


def configured_embedding_model() -> VectorModelContract | None:
    path, record = resolve_shared_asset('embedding_snapshot')
    if (record.get('repository') != 'BAAI/bge-small-en-v1.5'
            or record.get('revision') != '5c38ec7c405ec4b44b94cc5a9bb96e735b38267a'
            or record.get('dimension') != 384):
        raise ValueError('EMBEDDING_MODEL_PIN_MISMATCH')
    return VectorModelContract(
        model_id=f"{record['repository']}@{record['revision']}",
        dimension=int(record["dimension"]),
        local_model_path=path,
        allow_network_download=False,
    )


def enable_sqlite_vector_extension(connection: sqlite3.Connection) -> str:
    if not sqlite_vec_available():
        raise RuntimeError("SQLITE_VEC_DEPENDENCY_UNAVAILABLE")
    import sqlite_vec  # type: ignore[import-not-found]

    connection.enable_load_extension(True)
    try:
        sqlite_vec.load(connection)
    finally:
        connection.enable_load_extension(False)
    return str(connection.execute("SELECT vec_version()").fetchone()[0])


def ensure_authority_vector_schema(
    connection: sqlite3.Connection,
    *,
    contract: VectorModelContract,
) -> dict[str, Any]:
    """Create one optional vec0 side index; never replace mandatory FTS5."""

    version = enable_sqlite_vector_extension(connection)
    dimension = int(contract.dimension)
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS authority_vector_model(
            model_id TEXT PRIMARY KEY,
            dimension INTEGER NOT NULL,
            local_model_path_sha256 TEXT,
            network_download_allowed INTEGER NOT NULL CHECK(network_download_allowed=0),
            status TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS authority_vector_map(
            rowid INTEGER PRIMARY KEY,
            node_id TEXT NOT NULL UNIQUE,
            model_id TEXT NOT NULL REFERENCES authority_vector_model(model_id)
        ) STRICT;
        """
    )
    table = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='authority_vector_vec0'"
    ).fetchone()
    expected_token = f"float[{dimension}]"
    if table is not None and expected_token not in str(table[0]).replace(" ", ""):
        raise ValueError("SQLITE_VEC_DIMENSION_MISMATCH")
    if table is None:
        connection.execute(
            f"CREATE VIRTUAL TABLE authority_vector_vec0 "  # nosec B608
            f"USING vec0(embedding float[{dimension}])"
        )
    model_path_sha256 = (
        _directory_identity(contract.local_model_path.resolve(strict=True))
        if contract.local_model_path is not None
        and contract.local_model_path.is_dir()
        else None
    )
    connection.execute(
        """
        INSERT OR REPLACE INTO authority_vector_model(
            model_id,dimension,local_model_path_sha256,
            network_download_allowed,status
        ) VALUES(?,?,?,?,?)
        """,
        (
            contract.model_id,
            dimension,
            model_path_sha256,
            0,
            "ACTIVE_LOCAL_MODEL" if model_path_sha256 else "AWAITING_LOCAL_MODEL",
        ),
    )
    core = {
        "schema": "evidence-lane.sqlite-vector-side-index.v1",
        "status": "PASS",
        "sqlite_vec_version": version,
        "model_id": contract.model_id,
        "dimension": dimension,
        "local_model_path_sha256": model_path_sha256,
        "network_download_allowed": False,
        "fts5_bm25_mandatory": True,
        "sqlite_node_identity_canonical": True,
        "vector_index_optional": True,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def embed_with_local_sentence_transformer(
    texts: Sequence[str],
    *,
    contract: VectorModelContract,
) -> list[list[float]]:
    if contract.allow_network_download:
        raise ValueError("SEMANTIC_MODEL_NETWORK_DOWNLOAD_FORBIDDEN")
    if contract.local_model_path is None:
        raise ValueError("SEMANTIC_LOCAL_MODEL_PATH_REQUIRED")
    model_path = contract.local_model_path.resolve(strict=True)
    if not model_path.is_dir():
        raise ValueError("SEMANTIC_LOCAL_MODEL_DIRECTORY_REQUIRED")
    if not sentence_transformers_available():
        raise RuntimeError("SENTENCE_TRANSFORMERS_DEPENDENCY_UNAVAILABLE")
    from sentence_transformers import (
        SentenceTransformer,  # type: ignore[import-not-found]
    )

    key = (str(model_path), contract.model_id, _directory_identity(model_path))
    with _MODEL_LOCK:
        model = _MODEL_CACHE.get(key)
        if model is None:
            # Prewarm once per owned worker process. A changed file identity
            # creates another cache key; no query downloads or remote code.
            model = SentenceTransformer(str(model_path), local_files_only=True, trust_remote_code=False, device='cpu')
            _MODEL_CACHE.clear()
            _MODEL_CACHE[key] = model
    vectors = model.encode(
        list(texts),
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    rows = [[float(value) for value in vector] for vector in vectors]
    if any(len(row) != contract.dimension for row in rows):
        raise ValueError("SEMANTIC_MODEL_DIMENSION_MISMATCH")
    return rows


def replace_authority_vectors(
    connection: sqlite3.Connection,
    *,
    contract: VectorModelContract,
    node_vectors: Sequence[tuple[str, Sequence[float]]],
) -> dict[str, Any]:
    import sqlite_vec  # type: ignore[import-not-found]

    receipt = ensure_authority_vector_schema(connection, contract=contract)
    connection.execute("DELETE FROM authority_vector_vec0")
    connection.execute("DELETE FROM authority_vector_map")
    for rowid, (node_id, vector) in enumerate(node_vectors, start=1):
        values = [float(value) for value in vector]
        if len(values) != contract.dimension:
            raise ValueError("SQLITE_VEC_VECTOR_DIMENSION_MISMATCH")
        connection.execute(
            "INSERT INTO authority_vector_map(rowid,node_id,model_id) VALUES(?,?,?)",
            (rowid, node_id, contract.model_id),
        )
        connection.execute(
            "INSERT INTO authority_vector_vec0(rowid,embedding) VALUES(?,?)",
            (rowid, sqlite_vec.serialize_float32(values)),
        )
    core = {
        **receipt,
        "node_vector_count": len(node_vectors),
        "source_text_stored_in_vector_index": False,
        "canonical_text_remains_authority_index_node": True,
    }
    return {**core, "vector_receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def query_authority_vectors(
    connection: sqlite3.Connection,
    *,
    vector: Sequence[float],
    limit: int = 20,
) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("SQLITE_VEC_QUERY_LIMIT_INVALID")
    import sqlite_vec  # type: ignore[import-not-found]

    rows = connection.execute(
        """
        SELECT m.node_id,v.distance
        FROM authority_vector_vec0 AS v
        JOIN authority_vector_map AS m ON m.rowid=v.rowid
        WHERE v.embedding MATCH ? AND k=?
        ORDER BY v.distance,m.node_id
        """,
        (sqlite_vec.serialize_float32([float(value) for value in vector]), limit),
    ).fetchall()
    return [
        {"node_id": str(row[0]), "distance": float(row[1])}
        for row in rows
    ]


__all__ = [
    "VectorModelContract",
    "configured_embedding_model",
    "embed_with_local_sentence_transformer",
    "enable_sqlite_vector_extension",
    "ensure_authority_vector_schema",
    "query_authority_vectors",
    "replace_authority_vectors",
    "sentence_transformers_available",
    "sqlite_vec_available",
]
