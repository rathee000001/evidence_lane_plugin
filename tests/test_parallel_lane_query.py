from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.lane_engine import build_lane_bundle
from evidence_lane_plugin.lane_reader import (
    DEFAULT_PARALLEL_AGGREGATE_RESULTS,
    DEFAULT_PARALLEL_LANE_TIMEOUT_MS,
    MAX_PARALLEL_AGGREGATE_RESULTS,
    MAX_PARALLEL_LANE_QUERIES,
    MAX_PARALLEL_LANE_QUERY_CHARS,
    MAX_PARALLEL_LANE_TIMEOUT_MS,
    MAX_PARALLEL_LANE_WORKERS,
    LaneReader,
)

from .conftest import build_and_approve_pv1

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "schemas"
    / "parallel-lane-query.v001.json"
)
FTS5_CONTRACT_PATH = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "schemas"
    / "lane-search-fts5.v001.json"
)
PACKAGED_FTS5_CONTRACT_PATH = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "schemas"
    / "lane-search-fts5.v001.json"
)


def _materialize_live_root(service) -> dict[str, Any]:
    pointer = service.store.pointer("book-faires")
    output = service.store.project_root("book-faires") / "sectors"
    result = build_lane_bundle(
        repository_root=service.store.config("book-faires").repository_path,
        output_directory=output,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=pointer.accepted_pv,
        proposed_pv=f"{pointer.accepted_pv}_WORKING",
        pointer_generation=pointer.generation,
        include_untracked=False,
        materialize_all_lanes=True,
        index_git_history=False,
    )
    return result


def _hit(lane: str, suffix: str) -> dict[str, Any]:
    return {
        "chunk_id": len(suffix),
        "ref_id": f"lane:{lane}:chunk:{suffix}",
        "path": f"{lane}/{suffix}.md",
        "locator": f"line:{len(suffix)}",
        "snippet": suffix,
        "source_sha256": (suffix[0].upper() * 64),
        "chunk_sha256": (suffix[-1].upper() * 64),
        "parser_state": "PARSED",
    }


class FakeParallelLaneReader(LaneReader):
    def __init__(self, behaviors: dict[tuple[str, str], dict[str, Any]]) -> None:
        self.behaviors = behaviors
        self.calls: list[tuple[str, str]] = []

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
        self.calls.append((lane_alias, query))
        behavior = self.behaviors[(lane_alias, query)]
        time.sleep(float(behavior.get("delay", 0)))
        error = behavior.get("error")
        if error is not None:
            raise error
        results = list(behavior.get("results") or [])[:limit]
        return {
            "status": str(behavior.get("status") or ("PASS" if results else "EMPTY")),
            "project_id": project_id,
            "pv_ref": pv_ref,
            "lane": {"canonical_lane_id": lane_alias},
            "query": query,
            "retrieval": retrieval,
            "results": results,
            "freshness": {"state": "CURRENT"},
        }


def _queries() -> list[dict[str, Any]]:
    return [
        {"lane": "docs", "query": "shared term", "limit": 5, "timeout_ms": 500},
        {
            "lane": "analysis",
            "query": "audit term",
            "limit": 5,
            "timeout_ms": 500,
        },
        {"lane": "docs", "query": "shared term", "limit": 5, "timeout_ms": 500},
    ]


def _behaviors(*, docs_delay: float, analysis_delay: float) -> dict[Any, Any]:
    duplicate = _hit("docs", "same")
    return {
        ("docs", "shared term"): {
            "delay": docs_delay,
            "results": [duplicate, dict(duplicate)],
        },
        ("analysis", "audit term"): {
            "delay": analysis_delay,
            "results": [_hit("analysis", "audit")],
        },
    }


def test_parallel_query_is_completion_order_independent_and_deduplicated() -> None:
    slow_first = FakeParallelLaneReader(
        _behaviors(docs_delay=0.03, analysis_delay=0)
    ).search_parallel("project-one", _queries(), pv_ref="PV12")
    fast_first = FakeParallelLaneReader(
        _behaviors(docs_delay=0, analysis_delay=0.03)
    ).search_parallel("project-one", _queries(), pv_ref="PV12")

    assert slow_first == fast_first
    assert slow_first["status"] == "PASS"
    assert [row["canonical_lane_id"] for row in slow_first["results"]] == [
        "docs",
        "analysis",
    ]
    assert slow_first["results"][0]["request_indexes"] == [0, 2]
    receipt = slow_first["receipt"]
    assert receipt["bundle_sha256"] == "B" * 64
    assert len(receipt["authority_binding_sha256"]) == 64
    assert receipt["input_query_count"] == 3
    assert receipt["executed_query_count"] == 2
    assert receipt["deduplicated_input_count"] == 1
    assert receipt["result_count_before_dedupe"] == 3
    assert receipt["unique_result_count_before_aggregate_limit"] == 2
    assert receipt["result_count"] == 2
    assert receipt["aggregate_truncated"] is False
    assert [row["input_indexes"] for row in receipt["request_receipts"]] == [
        [0, 2],
        [1],
    ]
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    assert receipt["receipt_sha256"] == sha256_bytes(canonical_json_bytes(body))


def test_parallel_query_receipt_normalizes_successful_zero_hits_to_empty() -> None:
    result = FakeParallelLaneReader(
        {("github_code", "no matching code"): {"status": "PASS", "results": []}}
    ).search_parallel(
        "project-one",
        [{"lane": "github_code", "query": "no matching code"}],
        pv_ref="PV12",
    )

    assert result["status"] == "EMPTY"
    assert result["results"] == []
    request = result["receipt"]["request_receipts"][0]
    assert request["status"] == "EMPTY"
    assert request["result_count"] == 0


def test_parallel_query_enforces_timeout_and_preflight_cancellation() -> None:
    timeout_reader = FakeParallelLaneReader(
        {
            ("docs", "slow query"): {
                "delay": 0.05,
                "results": [_hit("docs", "slow")],
            }
        }
    )
    timed_out = timeout_reader.search_parallel(
        "project-one",
        [{"lane": "docs", "query": "slow query", "timeout_ms": 2}],
        pv_ref="PV12",
    )
    assert timed_out["status"] == "PARTIAL"
    assert timed_out["results"] == []
    assert timed_out["receipt"]["request_receipts"][0]["status"] == "TIMEOUT"
    assert timed_out["receipt"]["request_receipts"][0]["within_time_budget"] is False

    cancellation = Event()
    cancellation.set()
    cancelled_reader = FakeParallelLaneReader(
        {( "docs", "never run"): {"results": [_hit("docs", "never")]}}
    )
    cancelled = cancelled_reader.search_parallel(
        "project-one",
        [{"lane": "docs", "query": "never run"}],
        pv_ref="PV12",
        cancel_event=cancellation,
    )
    assert cancelled["status"] == "CANCELLED"
    assert cancelled_reader.calls == []
    assert cancelled["receipt"]["request_receipts"][0]["status"] == "CANCELLED"


def test_parallel_query_seals_worker_errors_and_rejects_unbounded_inputs() -> None:
    reader = FakeParallelLaneReader(
        {
            ("docs", "broken query"): {
                "error": EvidenceLaneError(
                    code="TEST_AUTHORITY_MISMATCH",
                    message="The fixture authority does not match.",
                    status="MISMATCH",
                )
            }
        }
    )
    failed = reader.search_parallel(
        "project-one",
        [{"lane": "docs", "query": "broken query"}],
        pv_ref="PV12",
    )
    request = failed["receipt"]["request_receipts"][0]
    assert failed["status"] == "PARTIAL"
    assert request["status"] == "MISMATCH"
    assert request["error"]["code"] == "TEST_AUTHORITY_MISMATCH"

    with pytest.raises(EvidenceLaneError) as too_many:
        reader.search_parallel(
            "project-one",
            [
                {"lane": "docs", "query": f"query {index}"}
                for index in range(MAX_PARALLEL_LANE_QUERIES + 1)
            ],
            pv_ref="PV12",
        )
    assert too_many.value.code == "LANE_PARALLEL_QUERY_COUNT_INVALID"

    with pytest.raises(EvidenceLaneError) as bad_timeout:
        reader.search_parallel(
            "project-one",
            [{"lane": "docs", "query": "query one", "timeout_ms": 0}],
            pv_ref="PV12",
        )
    assert bad_timeout.value.code == "LANE_PARALLEL_BUDGET_INVALID"

    with pytest.raises(EvidenceLaneError) as unknown_field:
        reader.search_parallel(
            "project-one",
            [{"lane": "docs", "query": "query one", "raw_sql": "SELECT 1"}],
            pv_ref="PV12",
        )
    assert unknown_field.value.code == "LANE_PARALLEL_QUERY_FIELD_UNSUPPORTED"

    with pytest.raises(EvidenceLaneError) as long_query:
        reader.search_parallel(
            "project-one",
            [{"lane": "docs", "query": "x" * (MAX_PARALLEL_LANE_QUERY_CHARS + 1)}],
            pv_ref="PV12",
        )
    assert long_query.value.code == "LANE_PARALLEL_QUERY_INVALID"


def test_parallel_query_schema_matches_runtime_limits() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    execution = contract["execution"]

    assert contract["schema"] == "evidence-lane.parallel-lane-query-contract.v1"
    assert execution["maximum_queries"] == MAX_PARALLEL_LANE_QUERIES
    assert execution["maximum_workers"] == MAX_PARALLEL_LANE_WORKERS
    assert execution["maximum_query_characters"] == MAX_PARALLEL_LANE_QUERY_CHARS
    assert execution["default_timeout_ms"] == DEFAULT_PARALLEL_LANE_TIMEOUT_MS
    assert execution["maximum_timeout_ms"] == MAX_PARALLEL_LANE_TIMEOUT_MS
    assert (
        execution["default_aggregate_results"]
        == DEFAULT_PARALLEL_AGGREGATE_RESULTS
    )
    assert execution["maximum_aggregate_results"] == MAX_PARALLEL_AGGREGATE_RESULTS
    assert contract["receipt"]["persisted"] is False


def test_fts5_public_schema_is_complete_and_package_identical() -> None:
    source_bytes = FTS5_CONTRACT_PATH.read_bytes()
    assert source_bytes == PACKAGED_FTS5_CONTRACT_PATH.read_bytes()
    contract = json.loads(source_bytes)
    extension = contract["x-evidence-lane-contract"]

    assert contract["properties"]["retrieval"]["enum"] == [
        "hybrid",
        "fts5",
        "bm25",
        "tfidf",
    ]
    assert len(contract["properties"]["lane"]["enum"]) == 18
    assert set(extension["fts5_tables"]) == set(
        contract["properties"]["lane"]["enum"]
    )
    assert extension["physical_fts5_columns"] == [
        "path",
        "locator",
        "text_content",
        "chunk_id UNINDEXED",
    ]
    assert extension["hooks_required"] is False


def test_explicit_fts5_retrieval_uses_real_lane_authority(service) -> None:
    build_and_approve_pv1(service)
    _materialize_live_root(service)

    direct = service.lane_reader.search(
        "book-faires",
        "docs",
        "Book Faires",
        retrieval="fts5",
        limit=3,
    )
    parallel = service.lane_reader.search_parallel(
        "book-faires",
        [
            {
                "lane": "docs",
                "query": "Book Faires",
                "retrieval": "fts5",
                "limit": 3,
            }
        ],
    )

    assert direct["status"] == "PASS"
    assert direct["retrieval"] == "fts5"
    assert direct["result_state"] == "HITS"
    assert direct["ranking"]["fts5"].startswith("SQLite FTS5")
    assert parallel["status"] == "PASS"
    assert parallel["receipt"]["request_receipts"][0]["retrieval"] == "fts5"


def test_implicit_fts5_prefers_current_working_sectors(service) -> None:
    build_and_approve_pv1(service)
    _materialize_live_root(service)
    manifest_path = (
        service.store.project_root("book-faires") / "sectors" / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    result = service.lane_reader.search(
        "book-faires",
        "docs",
        "Book Faires",
        retrieval="fts5",
        limit=3,
    )

    assert result["status"] == "PASS"
    assert result["pv_ref"] == manifest["proposed_pv"]
    assert result["freshness"]["authority"] == "WORKING_SECTORS"
    assert result["result_state"] == "HITS"


def test_parallel_query_applies_one_deterministic_aggregate_result_budget() -> None:
    reader = FakeParallelLaneReader(
        {
            ("docs", "many results"): {
                "results": [
                    _hit("docs", "one"),
                    _hit("docs", "two"),
                    _hit("docs", "three"),
                ]
            }
        }
    )

    result = reader.search_parallel(
        "project-one",
        [{"lane": "docs", "query": "many results", "limit": 3}],
        pv_ref="PV12",
        aggregate_limit=2,
    )

    assert [row["snippet"] for row in result["results"]] == ["one", "two"]
    receipt = result["receipt"]
    assert receipt["unique_result_count_before_aggregate_limit"] == 3
    assert receipt["result_count"] == 2
    assert receipt["aggregate_truncated"] is True
    assert receipt["aggregate_omitted_count"] == 1


def test_parallel_query_reads_two_real_live_root_lane_sqlites(service) -> None:
    build_and_approve_pv1(service)
    manifest = _materialize_live_root(service)

    result = service.lane_reader.search_parallel(
        "book-faires",
        [
            {
                "lane": "local_code",
                "query": "list_books",
                "limit": 3,
                "timeout_ms": 10_000,
            },
            {
                "lane": "docs",
                "query": "Book Faires",
                "limit": 3,
                "timeout_ms": 10_000,
            },
        ],
        max_workers=2,
    )

    assert result["status"] == "PASS"
    assert result["live_root_authority_ref"] == manifest["proposed_pv"]
    assert {row["canonical_lane_id"] for row in result["results"]} == {
        "local_code",
        "docs",
    }
    receipt = result["receipt"]
    assert [row["canonical_lane_id"] for row in receipt["request_receipts"]] == [
        "local_code",
        "docs",
    ]
    assert all(row["within_time_budget"] for row in receipt["request_receipts"])
    assert receipt["budgets"]["selected_workers"] == 2
