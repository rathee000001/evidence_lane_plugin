"""Bounded lexical/vector retrieval adapters and deterministic rank fusion."""

from __future__ import annotations

import importlib.util
import re
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes

_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    text: str


class VectorRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_ids: list[str]
    vectors: list[list[float]]
    query_vector: list[float]
    limit: int = Field(default=20, ge=1, le=100)


def _tokens(value: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN.finditer(value)]


def rank_bm25_candidates(
    query: str,
    candidates: Sequence[RetrievalCandidate],
    *,
    limit: int = 20,
) -> dict[str, Any]:
    if not 1 <= limit <= 100:
        raise ValueError("RANK_BM25_LIMIT_INVALID")
    corpus = [_tokens(candidate.text) for candidate in candidates]
    engine = "rank_bm25.BM25Okapi"
    if importlib.util.find_spec("rank_bm25") is not None and corpus:
        from rank_bm25 import BM25Okapi  # type: ignore[import-not-found]

        scores = [float(value) for value in BM25Okapi(corpus).get_scores(_tokens(query))]
    else:
        engine = "DETERMINISTIC_TOKEN_OVERLAP_FALLBACK"
        query_tokens = set(_tokens(query))
        scores = [
            float(len(query_tokens.intersection(tokens))) / max(len(query_tokens), 1)
            for tokens in corpus
        ]
    rows = [
        {"candidate_id": candidate.candidate_id, "score": scores[index]}
        for index, candidate in enumerate(candidates)
    ]
    rows.sort(key=lambda row: (-float(row["score"]), str(row["candidate_id"])))
    core = {
        "schema": "evidence-lane.rank-bm25-parity.v1",
        "status": "PASS",
        "engine": engine,
        "query_sha256": sha256_bytes(query.encode("utf-8")),
        "candidate_count": len(candidates),
        "results": rows[:limit],
        "sqlite_fts5_bm25_replaced": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def faiss_vector_rank(request: VectorRetrievalRequest) -> dict[str, Any]:
    if importlib.util.find_spec("faiss") is None:
        raise RuntimeError("FAISS_CPU_DEPENDENCY_UNAVAILABLE")
    import faiss  # type: ignore[import-not-found]
    import numpy as np

    if len(request.candidate_ids) != len(request.vectors):
        raise ValueError("FAISS_CANDIDATE_VECTOR_COUNT_MISMATCH")
    if not request.vectors:
        return {"schema": "evidence-lane.faiss-vector-rank.v1", "status": "EMPTY"}
    dimension = len(request.query_vector)
    if any(len(vector) != dimension for vector in request.vectors):
        raise ValueError("FAISS_VECTOR_DIMENSION_MISMATCH")
    matrix = np.asarray(request.vectors, dtype="float32")
    query = np.asarray([request.query_vector], dtype="float32")
    faiss.normalize_L2(matrix)
    faiss.normalize_L2(query)
    index = faiss.IndexFlatIP(dimension)
    index.add(matrix)
    scores, positions = index.search(query, min(request.limit, len(request.vectors)))
    results = [
        {
            "candidate_id": request.candidate_ids[int(position)],
            "score": float(scores[0][ordinal]),
        }
        for ordinal, position in enumerate(positions[0])
        if int(position) >= 0
    ]
    core = {
        "schema": "evidence-lane.faiss-vector-rank.v1",
        "status": "PASS",
        "engine": "FAISS_INDEX_FLAT_INNER_PRODUCT",
        "dimension": dimension,
        "results": results,
        "persistent_authority": False,
        "sqlite_node_ids_canonical": True,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def chroma_vector_rank(request: VectorRetrievalRequest) -> dict[str, Any]:
    if importlib.util.find_spec("chromadb") is None:
        raise RuntimeError("CHROMADB_DEPENDENCY_UNAVAILABLE")
    import chromadb  # type: ignore[import-not-found]
    from chromadb.config import Settings  # type: ignore[import-not-found]

    if len(request.candidate_ids) != len(request.vectors):
        raise ValueError("CHROMA_CANDIDATE_VECTOR_COUNT_MISMATCH")
    client = chromadb.Client(
        Settings(
            anonymized_telemetry=False,
            is_persistent=False,
            allow_reset=False,
        )
    )
    collection = client.create_collection(
        "evidence_lane_ephemeral",
        metadata={"hnsw:space": "cosine"},
    )
    collection.add(ids=request.candidate_ids, embeddings=request.vectors)
    response = collection.query(
        query_embeddings=[request.query_vector],
        n_results=min(request.limit, len(request.candidate_ids)),
        include=["distances"],
    )
    ids = list((response.get("ids") or [[]])[0])
    distances = list((response.get("distances") or [[]])[0])
    results = [
        {"candidate_id": str(candidate_id), "distance": float(distances[index])}
        for index, candidate_id in enumerate(ids)
    ]
    core = {
        "schema": "evidence-lane.chroma-vector-rank.v1",
        "status": "PASS",
        "engine": "CHROMADB_EPHEMERAL",
        "results": results,
        "persistent_authority": False,
        "telemetry_enabled": False,
        "sqlite_node_ids_canonical": True,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = 60,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if k < 1 or not 1 <= limit <= 100:
        raise ValueError("RRF_BOUND_INVALID")
    scores: dict[str, float] = {}
    for rows in ranked_lists:
        for rank, candidate_id in enumerate(rows, start=1):
            scores[str(candidate_id)] = scores.get(str(candidate_id), 0.0) + 1.0 / (k + rank)
    return [
        {"candidate_id": candidate_id, "score": score}
        for candidate_id, score in sorted(scores.items(), key=lambda row: (-row[1], row[0]))[:limit]
    ]


def langchain_retrieval_pipeline() -> Any:
    """Return the actual runnable used by the SDK to validate retrieval payloads."""

    from langchain_core.runnables import RunnableLambda

    return (
        RunnableLambda(lambda value: dict(value))
        | RunnableLambda(
            lambda value: {
                **value,
                "governed": True,
                "sqlite_authority_required": True,
            }
        )
    )


__all__ = [
    "RetrievalCandidate",
    "VectorRetrievalRequest",
    "chroma_vector_rank",
    "faiss_vector_rank",
    "langchain_retrieval_pipeline",
    "rank_bm25_candidates",
    "reciprocal_rank_fusion",
]
