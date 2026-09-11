"""Bounded lexical/vector retrieval adapters and deterministic rank fusion."""

from __future__ import annotations

import importlib.util
import math
import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .context_index_routing import context_index_catalog
from .hashing import canonical_json_bytes, sha256_bytes

_TOKEN = re.compile(r"[\w.-]+", re.UNICODE)


class RetrievalCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=131_072)


class VectorRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_ids: list[str] = Field(max_length=512)
    vectors: list[list[float]] = Field(max_length=512)
    query_vector: list[float] = Field(min_length=1, max_length=4096)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode='after')
    def finite_page(self):
        if (len(self.candidate_ids) != len(self.vectors)
                or len(set(self.candidate_ids)) != len(self.candidate_ids)
                or any(not key or len(key) > 128 for key in self.candidate_ids)):
            raise ValueError('FAISS_CANDIDATE_IDENTITY_INVALID')
        for vector in [self.query_vector, *self.vectors]:
            if (len(vector) != len(self.query_vector)
                    or any(not math.isfinite(value) or abs(value) > 1e18 for value in vector)
                    or not 1e-30 <= sum(value * value for value in vector) <= 1e38):
                raise ValueError('FAISS_VECTOR_INVALID')
        return self


def _tokens(value: str) -> list[str]:
    return [match.group(0).casefold() for match in _TOKEN.finditer(value)]


def rank_bm25_candidates(
    query: str,
    candidates: Sequence[RetrievalCandidate],
    *,
    limit: int = 20,
) -> dict[str, Any]:
    _lexical_bounds(query, candidates, limit)
    corpus = [_tokens(candidate.text) for candidate in candidates]
    engine = "rank_bm25.BM25Okapi"
    if importlib.util.find_spec("rank_bm25") is None:
        raise RuntimeError('RANK_BM25_DEPENDENCY_UNAVAILABLE')
    if corpus and any(corpus):
        from rank_bm25 import BM25Okapi  # type: ignore[import-not-found]

        scores = [float(value) for value in BM25Okapi(corpus).get_scores(_tokens(query))]
    else:
        scores = [0.0 for _ in corpus]
    if any(not math.isfinite(score) for score in scores):
        raise ValueError('RANK_BM25_NONFINITE_SCORE')
    rows: list[dict[str, str | float]] = [
        {"candidate_id": candidate.candidate_id, "score": scores[index]}
        for index, candidate in enumerate(candidates)
    ]
    rows.sort(key=lambda row: (-float(row["score"]), str(row["candidate_id"])))
    core = {
        "schema": "evidence-lane.rank-bm25-candidates.v4",
        "status": "PASS",
        "engine": engine,
        "query_sha256": sha256_bytes(query.encode("utf-8")),
        "candidate_count": len(candidates),
        "results": rows[:limit],
        "sqlite_fts5_bm25_replaced": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _lexical_bounds(query, candidates, limit):
    if (not 1 <= limit <= 100 or len(query) > 500 or len(candidates) > 100
            or len({candidate.candidate_id for candidate in candidates}) != len(candidates)
            or sum(len(candidate.text.encode('utf-8')) for candidate in candidates) > 262_144):
        raise ValueError('LEXICAL_CANDIDATE_BUDGET')


def tfidf_candidates(query, candidates, *, limit=20):
    """Adapt the original LaneReader's query-time TF/IDF over one candidate page."""
    _lexical_bounds(query, candidates, limit)
    tokens = [_tokens(candidate.text) for candidate in candidates]
    terms = sorted(set(_tokens(query)))
    counts = [Counter(row) for row in tokens]
    frequency = {term: sum(term in row for row in counts) for term in terms}
    rows = [{'candidate_id': candidate.candidate_id,
        'score': sum(counts[index][term] / max(len(tokens[index]), 1)
            * (math.log((1 + len(candidates)) / (1 + frequency[term])) + 1) for term in terms)}
        for index, candidate in enumerate(candidates)]
    return sorted(rows, key=lambda row: (-row['score'], row['candidate_id']))[:limit]


def rank_owner_candidates(query, rows, *, text_field, method, limit):
    """Return row references; do not change or relabel the owning reader's evidence."""
    if method not in {'tfidf', 'hybrid', 'rank_bm25'}:
        raise ValueError('LEXICAL_RANKING_UNSUPPORTED')
    if any(not isinstance(row.get(text_field), str) for row in rows):
        raise ValueError('LEXICAL_OWNER_TEXT_MISSING')
    candidates = [RetrievalCandidate(candidate_id=f'{index:03d}', text=row[text_field])
        for index, row in enumerate(rows)]
    _lexical_bounds(query, candidates, limit)
    if method == 'rank_bm25':
        ranked = rank_bm25_candidates(query, candidates, limit=limit)['results']
    else:
        ranked = tfidf_candidates(query, candidates, limit=max(1, len(rows)))
        if method == 'hybrid':
            ranked = reciprocal_rank_fusion([[candidate.candidate_id for candidate in candidates],
                [row['candidate_id'] for row in ranked]], limit=limit)
    return {'method': method, 'candidate_scope': 'bounded_owner_fts5_bm25_page',
        'candidate_count': len(rows), 'candidate_rows_sha256': sha256_bytes(canonical_json_bytes(rows)),
        'query_sha256': sha256_bytes(query.encode('utf-8')), 'text_field': text_field,
        'rrf_k': 60 if method == 'hybrid' else None,
        'selected': [{'owner_row_index': int(row['candidate_id']), 'score': row['score'],
            'row_sha256': sha256_bytes(canonical_json_bytes(rows[int(row['candidate_id'])]))}
            for row in ranked[:limit]], 'persistent_authority': False}


def faiss_vector_rank(request: VectorRetrievalRequest) -> dict[str, Any]:
    if importlib.util.find_spec("faiss") is None:
        raise RuntimeError("FAISS_CPU_DEPENDENCY_UNAVAILABLE")
    import faiss  # type: ignore[import-not-found]
    import numpy as np

    if not request.vectors:
        return {"schema": "evidence-lane.faiss-vector-rank.v4", "status": "EMPTY", 'results': []}
    dimension = len(request.query_vector)
    if any(len(vector) != dimension for vector in request.vectors):
        raise ValueError("FAISS_VECTOR_DIMENSION_MISMATCH")
    matrix = np.asarray(request.vectors, dtype="float32")
    query = np.asarray([request.query_vector], dtype="float32")
    faiss.normalize_L2(matrix)
    faiss.normalize_L2(query)
    index = faiss.IndexFlatIP(dimension)
    index.add(matrix)
    # Search the entire already bounded page so ties at the limit are stable.
    scores, positions = index.search(query, len(request.vectors))
    results: list[dict[str, Any]] = [
        {
            "candidate_id": request.candidate_ids[int(position)],
            "score": float(scores[0][ordinal]),
        }
        for ordinal, position in enumerate(positions[0])
        if int(position) >= 0
    ]
    if any(not math.isfinite(row['score']) for row in results):
        raise ValueError('FAISS_SCORE_NONFINITE')
    results.sort(key=lambda row: (-round(row['score'], 6), row['candidate_id']))
    core = {
        "schema": "evidence-lane.faiss-vector-rank.v4",
        "status": "PASS",
        "engine": "FAISS_INDEX_FLAT_INNER_PRODUCT",
        "dimension": dimension,
        "results": results[:request.limit],
        "candidate_count": len(request.candidate_ids),
        "input_sha256": sha256_bytes(canonical_json_bytes(request.model_dump())),
        "persistent_authority": False,
        "sqlite_node_ids_canonical": True,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[str]],
    *,
    k: int = 60,
    limit: int = 20,
) -> list[dict[str, Any]]:
    if (not 1 <= k <= 1000 or not 1 <= limit <= 100 or len(ranked_lists) > 8
            or any(len(rows) > 100 or len(set(rows)) != len(rows) for rows in ranked_lists)):
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


def inspect_context_index_backends() -> dict[str, Any]:
    """Return the unified local and remote rebuildable-index contract."""

    return context_index_catalog()


__all__ = [
    "RetrievalCandidate",
    "VectorRetrievalRequest",
    "faiss_vector_rank",
    "inspect_context_index_backends",
    "langchain_retrieval_pipeline",
    "rank_bm25_candidates",
    "reciprocal_rank_fusion",
]
