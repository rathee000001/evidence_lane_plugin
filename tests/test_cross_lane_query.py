from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.lane_reader import (
    CROSS_LANE_RELATION_TYPES,
    DEFAULT_CROSS_LANE_CONFLICTS,
    DEFAULT_CROSS_LANE_RELATIONS,
    DEFAULT_CROSS_LANE_SYNTHESIS,
    MAX_CROSS_LANE_CONFLICTS,
    MAX_CROSS_LANE_COUNT,
    MAX_CROSS_LANE_RELATIONS,
    MAX_CROSS_LANE_SYNTHESIS,
    MIN_CROSS_LANE_COUNT,
    LaneReader,
)

from .conftest import build_and_approve_pv1

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "schemas"
    / "cross-lane-query.v001.json"
)


def _hit(
    lane: str,
    name: str,
    *,
    path: str,
    locator: str,
    source_sha256: str,
    chunk_sha256: str,
    bm25_rank: float,
) -> dict[str, Any]:
    return {
        "chunk_id": len(name),
        "ref_id": f"lane:{lane}:chunk:{name}",
        "path": path,
        "locator": locator,
        "snippet": name,
        "source_sha256": source_sha256,
        "chunk_sha256": chunk_sha256,
        "parser_state": "PARSED",
        "bm25_rank": bm25_rank,
    }


class FakeCrossLaneReader(LaneReader):
    def __init__(self, behaviors: dict[str, dict[str, Any]]) -> None:
        self.behaviors = behaviors

    def _parallel_bundle(
        self,
        project_id: str,
        pv_ref: str | None,
    ) -> dict[str, Any]:
        assert project_id == "project-one"
        assert pv_ref == "PV12"
        return {
            "code_mode": "github_code",
            "bundle_sha256": "B" * 64,
            "_resolved_pv_ref": "PV12",
        }

    def search(
        self,
        project_id: str,
        lane_alias: str,
        query: str,
        *,
        pv_ref: str | None = None,
        limit: int = 20,
        retrieval: str = "hybrid",
    ) -> dict[str, Any]:
        behavior = self.behaviors[lane_alias]
        time.sleep(float(behavior.get("delay", 0)))
        results = list(behavior.get("results") or [])[:limit]
        return {
            "status": "PASS" if results else "EMPTY",
            "project_id": project_id,
            "pv_ref": pv_ref,
            "lane": {"canonical_lane_id": lane_alias},
            "query": query,
            "retrieval": retrieval,
            "results": results,
            "freshness": {"state": "CURRENT"},
        }


def _relation_behaviors(
    *, docs_delay: float = 0, analysis_delay: float = 0
) -> dict[str, dict[str, Any]]:
    return {
        "docs": {
            "delay": docs_delay,
            "results": [
                _hit(
                    "docs",
                    "docs-source",
                    path="docs/source.md",
                    locator="line:1",
                    source_sha256="A" * 64,
                    chunk_sha256="B" * 64,
                    bm25_rank=99.0,
                ),
                _hit(
                    "docs",
                    "docs-only",
                    path="docs/only.md",
                    locator="line:2",
                    source_sha256="D" * 64,
                    chunk_sha256="E" * 64,
                    bm25_rank=100.0,
                ),
            ],
        },
        "analysis": {
            "delay": analysis_delay,
            "results": [
                _hit(
                    "analysis",
                    "analysis-source",
                    path="analysis/source.md",
                    locator="line:8",
                    source_sha256="A" * 64,
                    chunk_sha256="C" * 64,
                    bm25_rank=-100.0,
                ),
                _hit(
                    "analysis",
                    "analysis-chunk",
                    path="analysis/chunk.md",
                    locator="line:9",
                    source_sha256="F" * 64,
                    chunk_sha256="B" * 64,
                    bm25_rank=-99.0,
                ),
            ],
        },
    }


def test_cross_lane_query_is_deterministic_and_keeps_rank_domains_separate() -> None:
    slow_docs = FakeCrossLaneReader(
        _relation_behaviors(docs_delay=0.03)
    ).search_cross_lane(
        "project-one",
        ["docs", "analysis"],
        "shared evidence",
        pv_ref="PV12",
    )
    slow_analysis = FakeCrossLaneReader(
        _relation_behaviors(analysis_delay=0.03)
    ).search_cross_lane(
        "project-one",
        ["docs", "analysis"],
        "shared evidence",
        pv_ref="PV12",
    )

    assert slow_docs == slow_analysis
    assert slow_docs["status"] == "PASS"
    assert slow_docs["canonical_lane_set"] == ["docs", "analysis"]
    assert [result["canonical_lane_id"] for result in slow_docs["results"]] == [
        "docs",
        "docs",
        "analysis",
        "analysis",
    ]
    assert [result["lane_rank"] for result in slow_docs["results"]] == [1, 2, 1, 2]
    assert all(result["cross_lane_score"] is None for result in slow_docs["results"])
    assert {result["rank_domain"] for result in slow_docs["results"]} == {
        "LANE:docs",
        "LANE:analysis",
    }
    assert {relation["relation_type"] for relation in slow_docs["relations"]} == {
        "EXACT_SOURCE_SHA256",
        "EXACT_CHUNK_SHA256",
    }
    assert slow_docs["conflicts"] == []
    for result in slow_docs["results"]:
        authority = result["authority_provenance"]
        assert authority["project_id"] == "project-one"
        assert authority["pv_ref"] == "PV12"
        assert authority["bundle_sha256"] == "B" * 64
        assert result["authority_provenance_sha256"] == sha256_bytes(
            canonical_json_bytes(authority)
        )
    receipt = slow_docs["receipt"]
    assert receipt["budgets"]["required_aggregate_limit"] == 40
    assert receipt["budgets"]["aggregate_limit"] == 40
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    assert receipt["receipt_sha256"] == sha256_bytes(canonical_json_bytes(body))


def test_cross_lane_query_surfaces_exact_conflicts_before_synthesis_relations() -> None:
    reader = FakeCrossLaneReader(
        {
            "docs": {
                "results": [
                    _hit(
                        "docs",
                        "docs-conflict",
                        path="shared/item.md",
                        locator="line:4",
                        source_sha256="A" * 64,
                        chunk_sha256="B" * 64,
                        bm25_rank=1.0,
                    )
                ]
            },
            "analysis": {
                "results": [
                    _hit(
                        "analysis",
                        "analysis-conflict",
                        path="shared/item.md",
                        locator="line:4",
                        source_sha256="A" * 64,
                        chunk_sha256="C" * 64,
                        bm25_rank=1.0,
                    )
                ]
            },
        }
    )

    result = reader.search_cross_lane(
        "project-one",
        ["docs", "analysis"],
        "conflicting evidence",
        pv_ref="PV12",
        synthesis_limit=1,
    )

    assert result["source_status"] == "PASS"
    assert result["status"] == "CONFLICT"
    assert {item["conflict_type"] for item in result["conflicts"]} == {
        "PATH_LOCATOR_CONTENT_CONFLICT",
        "SOURCE_LOCATOR_CONTENT_CONFLICT",
    }
    assert result["synthesis"][0]["kind"] == "EXPLICIT_CONFLICT"
    assert result["receipt"]["synthesis_count_before_limit"] > 1
    assert result["receipt"]["synthesis_count"] == 1
    assert result["receipt"]["synthesis_omitted_count"] > 0


def test_cross_lane_query_rejects_implicit_duplicate_or_unproven_authority() -> None:
    reader = FakeCrossLaneReader(_relation_behaviors())

    with pytest.raises(EvidenceLaneError) as one_lane:
        reader.search_cross_lane(
            "project-one", ["docs"], "query terms", pv_ref="PV12"
        )
    assert one_lane.value.code == "CROSS_LANE_SET_COUNT_INVALID"

    with pytest.raises(EvidenceLaneError) as duplicate:
        reader.search_cross_lane(
            "project-one", ["docs", "docs"], "query terms", pv_ref="PV12"
        )
    assert duplicate.value.code == "CROSS_LANE_SET_DUPLICATE"

    with pytest.raises(EvidenceLaneError) as relation:
        reader.search_cross_lane(
            "project-one",
            ["docs", "analysis"],
            "query terms",
            pv_ref="PV12",
            relation_types=["FUZZY_SEMANTIC_JOIN"],
        )
    assert relation.value.code == "CROSS_LANE_RELATION_TYPE_INVALID"

    with pytest.raises(EvidenceLaneError) as aggregate:
        reader.search_cross_lane(
            "project-one",
            ["docs", "analysis"],
            "query terms",
            pv_ref="PV12",
            result_limit_per_lane=2,
            aggregate_limit=3,
        )
    assert aggregate.value.code == "CROSS_LANE_AGGREGATE_BUDGET_INVALID"

    with pytest.raises(EvidenceLaneError) as product:
        reader.search_cross_lane(
            "project-one",
            ["docs", "analysis", "discussion"],
            "query terms",
            pv_ref="PV12",
            result_limit_per_lane=100,
        )
    assert product.value.code == "CROSS_LANE_AGGREGATE_BUDGET_INVALID"

    invalid = _relation_behaviors()
    invalid["analysis"]["results"][0]["chunk_sha256"] = ""
    with pytest.raises(EvidenceLaneError) as authority:
        FakeCrossLaneReader(invalid).search_cross_lane(
            "project-one",
            ["docs", "analysis"],
            "query terms",
            pv_ref="PV12",
        )
    assert authority.value.code == "CROSS_LANE_RESULT_AUTHORITY_INVALID"


def test_cross_lane_schema_matches_runtime_contract() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["schema"] == "evidence-lane.cross-lane-query-contract.v1"
    assert contract["lane_set"]["minimum_lanes"] == MIN_CROSS_LANE_COUNT
    assert contract["lane_set"]["maximum_lanes"] == MAX_CROSS_LANE_COUNT
    assert contract["relation_types"] == list(CROSS_LANE_RELATION_TYPES)
    budgets = contract["budgets"]
    assert budgets["default_relation_limit"] == DEFAULT_CROSS_LANE_RELATIONS
    assert budgets["maximum_relation_limit"] == MAX_CROSS_LANE_RELATIONS
    assert budgets["default_conflict_limit"] == DEFAULT_CROSS_LANE_CONFLICTS
    assert budgets["maximum_conflict_limit"] == MAX_CROSS_LANE_CONFLICTS
    assert budgets["default_synthesis_limit"] == DEFAULT_CROSS_LANE_SYNTHESIS
    assert budgets["maximum_synthesis_limit"] == MAX_CROSS_LANE_SYNTHESIS
    assert budgets["maximum_aggregate_limit"] == 200
    assert contract["synthesis"]["free_form_model_summary"] is False
    assert contract["receipt"]["persisted"] is False


def test_cross_lane_query_reads_two_real_immutable_lane_sqlites(service) -> None:
    build_and_approve_pv1(service)

    result = service.lane_reader.search_cross_lane(
        "book-faires",
        ["github_code", "docs"],
        "Book Faires",
        max_workers=2,
        timeout_ms_per_lane=10_000,
        result_limit_per_lane=3,
    )

    assert result["source_status"] == "PASS"
    assert result["pv_ref"] == "PV1"
    assert result["canonical_lane_set"] == ["github_code", "docs"]
    assert {item["canonical_lane_id"] for item in result["results"]} == {"docs"}
    assert [item["canonical_lane_id"] for item in result["lane_receipts"]] == [
        "github_code",
        "docs",
    ]
    assert [item["status"] for item in result["lane_receipts"]] == [
        "EMPTY",
        "PASS",
    ]
    assert all(
        item["authority_provenance"]["pv_ref"] == "PV1"
        for item in result["results"]
    )
    assert result["receipt"]["requested_pv_ref"] is None
