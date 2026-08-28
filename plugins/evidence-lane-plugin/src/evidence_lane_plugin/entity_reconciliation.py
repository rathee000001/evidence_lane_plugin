"""Bounded entity reconciliation with scored candidates and no implicit merge."""

from __future__ import annotations

import difflib
import importlib.util
from itertools import combinations
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes


class EntityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    left: str
    right: str
    score: float = Field(ge=0.0, le=100.0)
    scorer: str
    decision: str
    auto_merged: bool


def rapidfuzz_available() -> bool:
    return importlib.util.find_spec("rapidfuzz") is not None


def reconcile_entity_candidates(
    values: list[str] | tuple[str, ...],
    *,
    threshold: float = 84.0,
    max_pairs: int = 20_000,
) -> dict[str, Any]:
    exact = sorted({str(value).strip() for value in values if str(value).strip()})
    if not 0 <= threshold <= 100:
        raise ValueError("ENTITY_RECONCILIATION_THRESHOLD_INVALID")
    if len(exact) * max(len(exact) - 1, 0) // 2 > max_pairs:
        raise ValueError("ENTITY_RECONCILIATION_PAIR_BOUND_EXCEEDED")

    use_rapidfuzz = rapidfuzz_available()
    if use_rapidfuzz:
        from rapidfuzz import fuzz  # type: ignore[import-not-found]

    matches: list[dict[str, Any]] = []
    for left, right in combinations(exact, 2):
        score = (
            float(fuzz.WRatio(left, right))
            if use_rapidfuzz
            else difflib.SequenceMatcher(None, left.casefold(), right.casefold()).ratio()
            * 100.0
        )
        if score < threshold:
            continue
        matches.append(
            EntityMatch(
                left=left,
                right=right,
                score=round(score, 4),
                scorer="RapidFuzz.WRatio" if use_rapidfuzz else "difflib.SequenceMatcher",
                decision="HUMAN_OR_GOVERNED_SCHEMA_REVIEW_REQUIRED",
                auto_merged=False,
            ).model_dump()
        )
    matches.sort(key=lambda row: (-float(row["score"]), row["left"], row["right"]))
    core = {
        "schema": "evidence-lane.entity-reconciliation.v1",
        "status": "PASS",
        "engine": "RAPIDFUZZ" if use_rapidfuzz else "DETERMINISTIC_DIFFLIB_FALLBACK",
        "candidate_count": len(exact),
        "pair_count": len(exact) * max(len(exact) - 1, 0) // 2,
        "threshold": threshold,
        "matches": matches,
        "auto_merge_allowed": False,
        "authority_identity_mutated": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = ["EntityMatch", "rapidfuzz_available", "reconcile_entity_candidates"]
