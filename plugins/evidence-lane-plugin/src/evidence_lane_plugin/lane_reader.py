"""Bounded FTS5/BM25 + TF-IDF reads over universal lane SQLite authorities."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Event
from typing import Any, cast

from .errors import EvidenceLaneError, require
from .freshness import (
    evaluate_freshness,
    evaluate_working_lane_freshness,
    result_status,
)
from .hashing import canonical_json_bytes, sha256_bytes
from .lane_engine import validate_lane_bundle
from .lanes import LANE_REGISTRY, LaneRegistryError, catalog, get_lane
from .store import ProjectStore

_TOKEN_RE = re.compile(r"[\w][\w.-]{1,63}", flags=re.UNICODE)
_PUBLIC_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)

MAX_PARALLEL_LANE_QUERIES = 8
MAX_PARALLEL_LANE_WORKERS = 4
MAX_PARALLEL_LANE_QUERY_CHARS = 2_000
DEFAULT_PARALLEL_LANE_TIMEOUT_MS = 5_000
MAX_PARALLEL_LANE_TIMEOUT_MS = 60_000
DEFAULT_PARALLEL_AGGREGATE_RESULTS = 100
MAX_PARALLEL_AGGREGATE_RESULTS = 200

MIN_CROSS_LANE_COUNT = 2
MAX_CROSS_LANE_COUNT = MAX_PARALLEL_LANE_QUERIES
DEFAULT_CROSS_LANE_RELATIONS = 100
MAX_CROSS_LANE_RELATIONS = 200
DEFAULT_CROSS_LANE_CONFLICTS = 100
MAX_CROSS_LANE_CONFLICTS = 200
DEFAULT_CROSS_LANE_SYNTHESIS = 50
MAX_CROSS_LANE_SYNTHESIS = 100
CROSS_LANE_RELATION_TYPES = (
    "EXACT_SOURCE_SHA256",
    "EXACT_CHUNK_SHA256",
    "EXACT_PATH_LOCATOR",
)

MIN_CROSS_PROJECTS = 2
MAX_CROSS_PROJECTS = 4
DEFAULT_CROSS_PROJECT_WORKERS = 2
MAX_CROSS_PROJECT_WORKERS = 2
DEFAULT_CROSS_PROJECT_TIMEOUT_MS = 30_000
MAX_CROSS_PROJECT_TIMEOUT_MS = 120_000
DEFAULT_CROSS_PROJECT_RESULTS = 200
MAX_CROSS_PROJECT_RESULTS = 400
class LaneReader:
    def __init__(
        self,
        store: ProjectStore,
        *,
        cross_project_authorizer: Callable[[dict[str, Any]], dict[str, Any]]
        | None = None,
    ) -> None:
        self.store = store
        self.cross_project_authorizer = cross_project_authorizer

    def _resolve(
        self,
        project_id: str,
        lane_alias: str,
        pv_ref: str | None,
    ) -> tuple[Path, str, Any, dict[str, Any]]:
        require(
            pv_ref is None,
            "LANE_ARCHIVE_QUERY_OBSOLETE",
            "Lane reads query the live project root; accepted ZIPs are HIL-only.",
            status="BLOCKED",
            requested_ref=pv_ref,
        )
        lanes_root = self.store.project_root(project_id) / "sectors"
        require(
            lanes_root.is_dir() and (lanes_root / "manifest.json").is_file(),
            "LIVE_ROOT_SECTORS_REQUIRED",
            "The live project root has no materialized sector authority.",
            status="MISMATCH",
        )
        authority_ref = ""
        validation = validate_lane_bundle(lanes_root)
        require(
            validation["valid"],
            "LANE_BUNDLE_INVALID",
            "The selected PV lane bundle failed validation.",
            status="FAIL",
            validation=validation,
        )
        bundle = json.loads((lanes_root / "manifest.json").read_text(encoding="utf-8"))
        if pv_ref is None and lanes_root.name == "sectors":
            authority_ref = str(bundle.get("proposed_pv") or "WORKING_SECTORS")
        lane = get_lane(lane_alias, code_mode=bundle["code_mode"])
        return lanes_root, authority_ref, lane, bundle

    def _freshness(
        self,
        project_id: str,
        lanes_root: Path,
        authority_ref: str,
    ) -> dict[str, Any]:
        if lanes_root.name == "sectors":
            return evaluate_working_lane_freshness(
                self.store,
                project_id,
                lanes_root,
                bounded_dirty_read=True,
            )
        return evaluate_freshness(
            self.store,
            project_id,
            lanes_root.parent,
            bounded_dirty_read=True,
        )

    def lane_catalog(self) -> dict[str, Any]:
        return {
            "status": "PASS",
            "lane_count": len(LANE_REGISTRY),
            "lanes": catalog(),
            "single_registry": True,
            "command_alias_table_outside_registry": False,
        }

    def lane_status(
        self,
        project_id: str,
        lane_alias: str,
        *,
        pv_ref: str | None = None,
    ) -> dict[str, Any]:
        lanes_root, authority_ref, lane, bundle = self._resolve(
            project_id, lane_alias, pv_ref
        )
        lane_root = lanes_root / lane.canonical_lane_id
        manifest = json.loads(
            (lane_root / "lane_manifest.json").read_text(encoding="utf-8")
        )
        pointer = json.loads(
            (lane_root / "lane_pointer.json").read_text(encoding="utf-8")
        )
        refresh = json.loads(
            (lane_root / "refresh_receipt.json").read_text(encoding="utf-8")
        )
        tools = json.loads((lane_root / "tools.json").read_text(encoding="utf-8"))
        freshness = self._freshness(project_id, lanes_root, authority_ref)
        return {
            "status": result_status("PASS", freshness),
            "project_id": project_id,
            "pv_ref": authority_ref,
            "lane": lane.as_dict(),
            "bundle": {
                "schema": bundle["schema"],
                "code_mode": bundle["code_mode"],
                "bundle_sha256": bundle["bundle_sha256"],
                "summary": bundle["summary"],
            },
            "lane_manifest": manifest,
            "lane_pointer": pointer,
            "refresh": refresh,
            "tools": tools,
            "freshness": freshness,
        }

    @staticmethod
    def _fts_query(query: str) -> tuple[str, list[str]]:
        terms = [token.lower() for token in _TOKEN_RE.findall(query)][:12]
        require(
            bool(terms),
            "LANE_QUERY_EMPTY",
            "Lane search requires at least one lexical term.",
            status="EMPTY",
        )
        fts = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        return fts, terms

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
        require(
            1 <= limit <= 100,
            "LANE_SEARCH_LIMIT_INVALID",
            "Lane search limit must be between 1 and 100.",
            status="BLOCKED",
        )
        require(
            retrieval in {"hybrid", "fts5", "bm25", "tfidf"},
            "LANE_RETRIEVAL_INVALID",
            "Lane retrieval must be hybrid, fts5, bm25, or tfidf.",
            status="BLOCKED",
        )
        lanes_root, authority_ref, lane, _ = self._resolve(
            project_id, lane_alias, pv_ref
        )
        lane_root = lanes_root / lane.canonical_lane_id
        database_path = lane_root / lane.sqlite_filename
        fts_query, terms = self._fts_query(query)
        connection = sqlite3.connect(
            f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        bm25_rows = []
        if retrieval in {"hybrid", "fts5", "bm25"}:
            # The table identifier comes from the immutable validated registry.
            bm25_sql = (
                "SELECT f.chunk_id AS chunk_id, f.path AS path, "  # nosec B608
                "f.locator AS locator, "
                f"snippet({lane.fts_table}, 2, '[', ']', ' ... ', 24) AS snippet, "
                f"bm25({lane.fts_table}) AS bm25_rank, "
                "c.sha256 AS chunk_sha256, s.sha256 AS source_sha256, "
                "s.parser_state AS parser_state "
                f"FROM {lane.fts_table} f "
                "JOIN chunk_index c ON c.chunk_id=CAST(f.chunk_id AS INTEGER) "
                "JOIN source_registry s ON s.source_id=c.source_id "
                f"WHERE {lane.fts_table} MATCH ? "
                "ORDER BY bm25_rank, path, locator, chunk_id LIMIT ?"
            )
            bm25_rows = [
                dict(row)
                for row in connection.execute(
                    bm25_sql,
                    (fts_query, limit * 3),
                )
            ]
        tfidf_rows = []
        if retrieval in {"hybrid", "tfidf"}:
            placeholders = ",".join("?" for _ in terms)
            tfidf_sql = (
                "SELECT c.chunk_id AS chunk_id, s.path AS path, "  # nosec B608
                "c.locator AS locator, substr(c.text_content, 1, 1000) AS snippet, "
                "SUM(v.tfidf) AS tfidf_score, c.sha256 AS chunk_sha256, "
                "s.sha256 AS source_sha256, s.parser_state AS parser_state "
                "FROM tfidf_vector v JOIN chunk_index c ON c.chunk_id=v.chunk_id "
                "JOIN source_registry s ON s.source_id=c.source_id "
                f"WHERE v.term IN ({placeholders}) "
                "GROUP BY c.chunk_id, s.path, c.locator, c.sha256, "
                "s.sha256, s.parser_state "
                "ORDER BY tfidf_score DESC, path, locator, c.chunk_id LIMIT ?"
            )
            tfidf_rows = [
                dict(row)
                for row in connection.execute(
                    tfidf_sql,
                    (*terms, limit * 3),
                )
            ]
        connection.close()

        merged: dict[int, dict[str, Any]] = {}
        for rank_index, row in enumerate(bm25_rows, start=1):
            chunk_id = int(row["chunk_id"])
            merged[chunk_id] = {
                **row,
                "bm25_position": rank_index,
                "tfidf_position": None,
                "tfidf_score": None,
            }
        for rank_index, row in enumerate(tfidf_rows, start=1):
            chunk_id = int(row["chunk_id"])
            target = merged.setdefault(
                chunk_id,
                {
                    **row,
                    "bm25_rank": None,
                    "bm25_position": None,
                },
            )
            target["tfidf_position"] = rank_index
            target["tfidf_score"] = row["tfidf_score"]
            target.setdefault("snippet", row["snippet"])
        for row in merged.values():
            positions = [
                position
                for position in (row.get("bm25_position"), row.get("tfidf_position"))
                if position is not None
            ]
            row["hybrid_rank_score"] = sum(
                1.0 / (60 + position) for position in positions
            )
            row["ref_id"] = f"lane:{lane.canonical_lane_id}:chunk:{row['chunk_id']}"
        if retrieval in {"fts5", "bm25"}:
            ordered = sorted(
                merged.values(),
                key=lambda row: (
                    row.get("bm25_position") or 10**9,
                    row["path"],
                    row["chunk_id"],
                ),
            )
        elif retrieval == "tfidf":
            ordered = sorted(
                merged.values(),
                key=lambda row: (
                    row.get("tfidf_position") or 10**9,
                    row["path"],
                    row["chunk_id"],
                ),
            )
        else:
            ordered = sorted(
                merged.values(),
                key=lambda row: (
                    -row["hybrid_rank_score"],
                    row["path"],
                    row["chunk_id"],
                ),
            )
        freshness = self._freshness(project_id, lanes_root, authority_ref)
        results = ordered[:limit]
        return {
            "status": result_status("PASS", freshness),
            "result_state": "HITS" if results else "EMPTY",
            "project_id": project_id,
            "pv_ref": authority_ref,
            "lane": lane.as_dict(),
            "query": query,
            "terms": terms,
            "retrieval": retrieval,
            "ranking": {
                "fts5": (
                    "SQLite FTS5 unicode61 MATCH with deterministic lexical "
                    "term normalization and BM25 ordering"
                ),
                "bm25": "SQLite FTS5 bm25; lower raw rank is better",
                "tfidf": "tf=count/tokens; idf=ln((1+N)/(1+df))+1",
                "hybrid": "reciprocal-rank fusion with k=60",
                "bm25_mislabeled_as_tfidf": False,
            },
            "results": results,
            "freshness": freshness,
        }

    def _parallel_bundle(
        self,
        project_id: str,
        pv_ref: str | None,
    ) -> dict[str, Any]:
        require(
            pv_ref is None,
            "LANE_ARCHIVE_QUERY_OBSOLETE",
            "Parallel lane reads query the live project root; accepted ZIPs are HIL-only.",
            status="BLOCKED",
            requested_ref=pv_ref,
        )
        lanes_root = self.store.project_root(project_id) / "sectors"
        require(
            lanes_root.is_dir() and (lanes_root / "manifest.json").is_file(),
            "LIVE_ROOT_SECTORS_REQUIRED",
            "The live project root has no materialized sector authority.",
            status="MISMATCH",
        )
        authority_ref = ""
        validation = validate_lane_bundle(lanes_root)
        require(
            validation["valid"],
            "LANE_BUNDLE_INVALID",
            "The selected PV lane bundle failed validation.",
            status="FAIL",
            validation=validation,
        )
        bundle = json.loads(
            (lanes_root / "manifest.json").read_text(encoding="utf-8")
        )
        if pv_ref is None and lanes_root.name == "sectors":
            authority_ref = str(bundle.get("proposed_pv") or "WORKING_SECTORS")
        return {**bundle, "_resolved_pv_ref": authority_ref}

    @staticmethod
    def _parallel_integer(
        value: Any,
        *,
        field: str,
        minimum: int,
        maximum: int,
    ) -> int:
        require(
            isinstance(value, int) and not isinstance(value, bool),
            "LANE_PARALLEL_BUDGET_INVALID",
            "Parallel lane-query budgets must be exact integers.",
            status="BLOCKED",
            field=field,
        )
        exact = int(value)
        require(
            minimum <= exact <= maximum,
            "LANE_PARALLEL_BUDGET_INVALID",
            "A parallel lane-query budget is outside its enforced range.",
            status="BLOCKED",
            field=field,
            minimum=minimum,
            maximum=maximum,
            value=exact,
        )
        return exact

    def _parallel_search_worker(
        self,
        *,
        project_id: str,
        pv_ref: str | None,
        job: dict[str, Any],
        cancel_event: Event,
    ) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"kind": "CANCELLED", "completed_at": time.monotonic()}
        try:
            result = self.search(
                project_id,
                str(job["canonical_lane_id"]),
                str(job["query"]),
                pv_ref=pv_ref,
                limit=int(job["result_limit"]),
                retrieval=str(job["retrieval"]),
            )
        except EvidenceLaneError as error:
            return {
                "kind": "ERROR",
                "completed_at": time.monotonic(),
                "error": error.as_dict(),
            }
        except Exception as error:  # noqa: BLE001  # pragma: no cover
            return {
                "kind": "ERROR",
                "completed_at": time.monotonic(),
                "error": {
                    "code": "LANE_PARALLEL_WORKER_FAILED",
                    "message": "A bounded lane-query worker failed unexpectedly.",
                    "status": "FAIL",
                    "details": {"exception_type": type(error).__name__},
                },
            }
        return {
            "kind": "RESULT",
            "completed_at": time.monotonic(),
            "result": result,
        }

    def search_parallel(
        self,
        project_id: str,
        lane_queries: list[dict[str, Any]],
        *,
        pv_ref: str | None = None,
        max_workers: int = MAX_PARALLEL_LANE_WORKERS,
        aggregate_limit: int = DEFAULT_PARALLEL_AGGREGATE_RESULTS,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Run one bounded, deterministic, read-only fan-out over one PV."""

        require(
            isinstance(lane_queries, list)
            and 1 <= len(lane_queries) <= MAX_PARALLEL_LANE_QUERIES,
            "LANE_PARALLEL_QUERY_COUNT_INVALID",
            "Parallel lane search requires between one and eight lane queries.",
            status="BLOCKED",
            count=len(lane_queries) if isinstance(lane_queries, list) else None,
        )
        worker_count = self._parallel_integer(
            max_workers,
            field="max_workers",
            minimum=1,
            maximum=MAX_PARALLEL_LANE_WORKERS,
        )
        aggregate_result_limit = self._parallel_integer(
            aggregate_limit,
            field="aggregate_limit",
            minimum=1,
            maximum=MAX_PARALLEL_AGGREGATE_RESULTS,
        )
        bundle = self._parallel_bundle(project_id, pv_ref)
        resolved_pv_ref = str(bundle["_resolved_pv_ref"])
        bundle_sha256 = str(bundle.get("bundle_sha256") or "")
        require(
            bool(resolved_pv_ref) and len(bundle_sha256) == 64,
            "LANE_PARALLEL_PV_BINDING_INVALID",
            "Parallel lane search could not bind one exact validated PV bundle.",
            status="MISMATCH",
        )
        authority_binding = {
            "project_id": project_id,
            "live_root_authority_ref": resolved_pv_ref,
            "bundle_sha256": bundle_sha256,
            "code_mode": str(bundle["code_mode"]),
        }
        cancellation = cancel_event or Event()

        jobs_by_sha256: dict[str, dict[str, Any]] = {}
        jobs: list[dict[str, Any]] = []
        for input_index, raw in enumerate(lane_queries):
            require(
                isinstance(raw, dict),
                "LANE_PARALLEL_QUERY_INVALID",
                "Every parallel lane query must be one structured object.",
                status="BLOCKED",
                input_index=input_index,
            )
            unknown_fields = sorted(
                set(raw) - {"lane", "query", "retrieval", "limit", "timeout_ms"}
            )
            require(
                not unknown_fields,
                "LANE_PARALLEL_QUERY_FIELD_UNSUPPORTED",
                "A parallel lane query contains unsupported fields.",
                status="BLOCKED",
                input_index=input_index,
                fields=unknown_fields,
            )
            lane_alias = str(raw.get("lane") or "").strip()
            query = str(raw.get("query") or "").strip()
            require(
                bool(lane_alias)
                and len(lane_alias) <= 128
                and bool(query)
                and len(query) <= MAX_PARALLEL_LANE_QUERY_CHARS,
                "LANE_PARALLEL_QUERY_INVALID",
                "Every parallel lane query requires a bounded exact lane and query.",
                status="BLOCKED",
                input_index=input_index,
            )
            try:
                lane = get_lane(lane_alias, code_mode=str(bundle["code_mode"]))
            except LaneRegistryError as error:
                raise EvidenceLaneError(
                    code="LANE_PARALLEL_LANE_INVALID",
                    message="A parallel lane query names an unsupported lane.",
                    status="BLOCKED",
                    details={"input_index": input_index, "lane": lane_alias},
                ) from error
            self._fts_query(query)
            retrieval = str(raw.get("retrieval") or "hybrid").strip().lower()
            require(
                retrieval in {"hybrid", "fts5", "bm25", "tfidf"},
                "LANE_RETRIEVAL_INVALID",
                "Lane retrieval must be hybrid, fts5, bm25, or tfidf.",
                status="BLOCKED",
                input_index=input_index,
            )
            result_limit = self._parallel_integer(
                raw.get("limit", 20),
                field=f"lane_queries[{input_index}].limit",
                minimum=1,
                maximum=100,
            )
            timeout_ms = self._parallel_integer(
                raw.get("timeout_ms", DEFAULT_PARALLEL_LANE_TIMEOUT_MS),
                field=f"lane_queries[{input_index}].timeout_ms",
                minimum=1,
                maximum=MAX_PARALLEL_LANE_TIMEOUT_MS,
            )
            identity = {
                "project_id": project_id,
                "live_root_authority_ref": resolved_pv_ref,
                "canonical_lane_id": lane.canonical_lane_id,
                "query": query,
                "retrieval": retrieval,
                "result_limit": result_limit,
                "timeout_ms": timeout_ms,
            }
            request_sha256 = sha256_bytes(canonical_json_bytes(identity))
            existing = jobs_by_sha256.get(request_sha256)
            if existing is not None:
                existing["input_indexes"].append(input_index)
                continue
            job = {
                **identity,
                "request_sha256": request_sha256,
                "input_indexes": [input_index],
                "lane_alias": lane_alias,
            }
            jobs_by_sha256[request_sha256] = job
            jobs.append(job)

        request_receipts: list[dict[str, Any]] = []
        lane_results: dict[str, dict[str, Any]] = {}
        if cancellation.is_set():
            for job in jobs:
                request_receipts.append(
                    {
                        **job,
                        "status": "CANCELLED",
                        "within_time_budget": True,
                        "result_count": 0,
                        "result_sha256": None,
                    }
                )
        else:
            executor = ThreadPoolExecutor(
                max_workers=min(worker_count, len(jobs)),
                thread_name_prefix="evidence-lane-query",
            )
            pending: dict[Future[dict[str, Any]], dict[str, Any]] = {}
            deadlines: dict[Future[dict[str, Any]], float] = {}
            try:
                for job in jobs:
                    submitted_at = time.monotonic()
                    future = executor.submit(
                        self._parallel_search_worker,
                        project_id=project_id,
                        # The validated bundle above is the live-root binding.
                        # Passing its continuity label back through ``pv_ref``
                        # would incorrectly re-enter the retired archive route.
                        pv_ref=None,
                        job=job,
                        cancel_event=cancellation,
                    )
                    pending[future] = job
                    deadlines[future] = submitted_at + (
                        cast(int, job["timeout_ms"]) / 1_000
                    )

                while pending:
                    if cancellation.is_set():
                        for future, job in list(pending.items()):
                            future.cancel()
                            request_receipts.append(
                                {
                                    **job,
                                    "status": "CANCELLED",
                                    "within_time_budget": True,
                                    "result_count": 0,
                                    "result_sha256": None,
                                }
                            )
                            pending.pop(future)
                        break

                    now = time.monotonic()
                    expired = [
                        future
                        for future in pending
                        if not future.done() and now >= deadlines[future]
                    ]
                    for future in expired:
                        job = pending.pop(future)
                        future.cancel()
                        request_receipts.append(
                            {
                                **job,
                                "status": "TIMEOUT",
                                "within_time_budget": False,
                                "result_count": 0,
                                "result_sha256": None,
                            }
                        )
                    if not pending:
                        break

                    nearest = min(deadlines[future] for future in pending)
                    wait_seconds = max(0.0, min(0.05, nearest - time.monotonic()))
                    completed, _ = wait(
                        tuple(pending),
                        timeout=wait_seconds,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in completed:
                        job = pending.pop(future)
                        worker = future.result()
                        if float(worker["completed_at"]) > deadlines[future]:
                            request_receipts.append(
                                {
                                    **job,
                                    "status": "TIMEOUT",
                                    "within_time_budget": False,
                                    "result_count": 0,
                                    "result_sha256": None,
                                }
                            )
                            continue
                        if worker["kind"] == "CANCELLED":
                            request_receipts.append(
                                {
                                    **job,
                                    "status": "CANCELLED",
                                    "within_time_budget": True,
                                    "result_count": 0,
                                    "result_sha256": None,
                                }
                            )
                            continue
                        if worker["kind"] == "ERROR":
                            worker_error = dict(worker["error"])
                            request_receipts.append(
                                {
                                    **job,
                                    "status": str(
                                        worker_error.get("status") or "FAIL"
                                    ),
                                    "within_time_budget": True,
                                    "result_count": 0,
                                    "result_sha256": None,
                                    "error": worker_error,
                                }
                            )
                            continue
                        result = dict(worker["result"])
                        lane_results[str(job["request_sha256"])] = result
                        result_count = len(result.get("results") or [])
                        execution_status = str(result.get("status") or "FAIL")
                        receipt_status = (
                            "EMPTY"
                            if execution_status == "PASS" and result_count == 0
                            else execution_status
                        )
                        request_receipts.append(
                            {
                                **job,
                                "status": receipt_status,
                                "within_time_budget": True,
                                "result_count": result_count,
                                "result_sha256": sha256_bytes(
                                    canonical_json_bytes(result)
                                ),
                            }
                        )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        request_receipts.sort(key=lambda row: int(row["input_indexes"][0]))
        aggregate: list[dict[str, Any]] = []
        aggregate_by_key: dict[str, dict[str, Any]] = {}
        pre_dedupe_count = 0
        for receipt in request_receipts:
            lane_result = lane_results.get(str(receipt["request_sha256"]))
            if lane_result is None:
                continue
            result_project_id = str(lane_result.get("project_id") or project_id)
            result_pv_ref = str(lane_result.get("pv_ref") or resolved_pv_ref)
            lane_id = str(
                (lane_result.get("lane") or {}).get("canonical_lane_id")
                or receipt["canonical_lane_id"]
            )
            for hit in lane_result.get("results") or []:
                pre_dedupe_count += 1
                identity = {
                    "project_id": result_project_id,
                    "live_root_authority_ref": result_pv_ref,
                    "canonical_lane_id": lane_id,
                    "ref_id": hit.get("ref_id"),
                    "path": hit.get("path"),
                    "locator": hit.get("locator"),
                    "source_sha256": hit.get("source_sha256"),
                    "chunk_sha256": hit.get("chunk_sha256"),
                }
                dedupe_key = sha256_bytes(canonical_json_bytes(identity))
                existing = aggregate_by_key.get(dedupe_key)
                if existing is not None:
                    existing["request_indexes"] = sorted(
                        set(existing["request_indexes"])
                        | set(receipt["input_indexes"])
                    )
                    continue
                projected = {
                    **dict(hit),
                    "project_id": result_project_id,
                    "live_root_authority_ref": result_pv_ref,
                    "canonical_lane_id": lane_id,
                    "request_indexes": list(receipt["input_indexes"]),
                    "dedupe_key_sha256": dedupe_key,
                }
                aggregate_by_key[dedupe_key] = projected
                aggregate.append(projected)

        unique_result_count = len(aggregate)
        aggregate_truncated = unique_result_count > aggregate_result_limit
        if aggregate_truncated:
            aggregate = aggregate[:aggregate_result_limit]

        statuses = [str(receipt["status"]) for receipt in request_receipts]
        if statuses and all(status == "CANCELLED" for status in statuses):
            status = "CANCELLED"
        elif any(item not in {"PASS", "EMPTY", "STALE"} for item in statuses):
            status = "PARTIAL"
        elif any(item == "STALE" for item in statuses):
            status = "STALE"
        elif aggregate:
            status = "PASS"
        else:
            status = "EMPTY"

        result_sha256 = sha256_bytes(canonical_json_bytes(aggregate))
        receipt_body = {
            "schema": "evidence-lane.parallel-lane-query-receipt.v1",
            "status": status,
            "project_id": project_id,
            "live_root_authority_ref": resolved_pv_ref,
            "bundle_sha256": bundle_sha256,
            "authority_binding_sha256": sha256_bytes(
                canonical_json_bytes(authority_binding)
            ),
            "ordering": "INPUT_INDEX_THEN_SOURCE_RANK",
            "dedupe_identity": [
                "project_id",
                "live_root_authority_ref",
                "canonical_lane_id",
                "ref_id",
                "path",
                "locator",
                "source_sha256",
                "chunk_sha256",
            ],
            "budgets": {
                "maximum_queries": MAX_PARALLEL_LANE_QUERIES,
                "maximum_workers": MAX_PARALLEL_LANE_WORKERS,
                "selected_workers": min(worker_count, len(jobs)),
                "maximum_timeout_ms": MAX_PARALLEL_LANE_TIMEOUT_MS,
                "maximum_results_per_lane": 100,
                "selected_aggregate_limit": aggregate_result_limit,
                "maximum_aggregate_results": MAX_PARALLEL_AGGREGATE_RESULTS,
            },
            "input_query_count": len(lane_queries),
            "executed_query_count": len(jobs),
            "deduplicated_input_count": len(lane_queries) - len(jobs),
            "result_count_before_dedupe": pre_dedupe_count,
            "unique_result_count_before_aggregate_limit": unique_result_count,
            "result_count": len(aggregate),
            "aggregate_truncated": aggregate_truncated,
            "aggregate_omitted_count": unique_result_count - len(aggregate),
            "aggregate_results_sha256": result_sha256,
            "request_receipts": request_receipts,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        return {
            "schema": "evidence-lane.parallel-lane-query.v1",
            "status": status,
            "project_id": project_id,
            "live_root_authority_ref": resolved_pv_ref,
            "bundle_sha256": bundle_sha256,
            "results": aggregate,
            "receipt": receipt,
        }

    @staticmethod
    def _cross_lane_groups(
        results: list[dict[str, Any]],
        *,
        relation_type: str,
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            authority = result["authority_provenance"]
            if relation_type == "EXACT_SOURCE_SHA256":
                value: Any = authority["source_sha256"]
            elif relation_type == "EXACT_CHUNK_SHA256":
                value = authority["chunk_sha256"]
            elif relation_type == "EXACT_PATH_LOCATOR":
                value = {
                    "path": authority["path"],
                    "locator": authority["locator"],
                }
            else:  # The caller validates the closed relation-type vocabulary.
                raise AssertionError(f"Unsupported cross-lane relation {relation_type}")
            key_sha256 = sha256_bytes(canonical_json_bytes(value))
            grouped.setdefault(key_sha256, []).append(result)
        return sorted(grouped.items())

    @staticmethod
    def _cross_lane_conflict_groups(
        results: list[dict[str, Any]],
        *,
        conflict_type: str,
    ) -> list[tuple[str, list[dict[str, Any]]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for result in results:
            authority = result["authority_provenance"]
            if conflict_type == "PATH_LOCATOR_CONTENT_CONFLICT":
                basis: Any = {
                    "path": authority["path"],
                    "locator": authority["locator"],
                }
            elif conflict_type == "SOURCE_LOCATOR_CONTENT_CONFLICT":
                basis = {
                    "source_sha256": authority["source_sha256"],
                    "locator": authority["locator"],
                }
            else:  # The caller owns the closed conflict-type vocabulary.
                raise AssertionError(f"Unsupported cross-lane conflict {conflict_type}")
            basis_sha256 = sha256_bytes(canonical_json_bytes(basis))
            grouped.setdefault(basis_sha256, []).append(result)
        return sorted(grouped.items())

    def search_cross_lane(
        self,
        project_id: str,
        lane_set: list[str],
        query: str,
        *,
        pv_ref: str | None = None,
        retrieval: str = "hybrid",
        result_limit_per_lane: int = 20,
        timeout_ms_per_lane: int = DEFAULT_PARALLEL_LANE_TIMEOUT_MS,
        max_workers: int = MAX_PARALLEL_LANE_WORKERS,
        aggregate_limit: int | None = None,
        relation_types: list[str] | None = None,
        relation_limit: int = DEFAULT_CROSS_LANE_RELATIONS,
        conflict_limit: int = DEFAULT_CROSS_LANE_CONFLICTS,
        synthesis_limit: int = DEFAULT_CROSS_LANE_SYNTHESIS,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Join independently ranked evidence from an explicit set of lanes."""

        require(
            isinstance(lane_set, list)
            and MIN_CROSS_LANE_COUNT <= len(lane_set) <= MAX_CROSS_LANE_COUNT,
            "CROSS_LANE_SET_COUNT_INVALID",
            "Cross-lane search requires an explicit set of two to eight lanes.",
            status="BLOCKED",
            count=len(lane_set) if isinstance(lane_set, list) else None,
        )
        normalized_query = str(query or "").strip()
        require(
            bool(normalized_query)
            and len(normalized_query) <= MAX_PARALLEL_LANE_QUERY_CHARS,
            "CROSS_LANE_QUERY_INVALID",
            "Cross-lane search requires one bounded lexical query.",
            status="BLOCKED",
        )
        self._fts_query(normalized_query)
        require(
            retrieval in {"hybrid", "fts5", "bm25", "tfidf"},
            "LANE_RETRIEVAL_INVALID",
            "Lane retrieval must be hybrid, fts5, bm25, or tfidf.",
            status="BLOCKED",
        )
        per_lane_limit = self._parallel_integer(
            result_limit_per_lane,
            field="result_limit_per_lane",
            minimum=1,
            maximum=100,
        )
        per_lane_timeout = self._parallel_integer(
            timeout_ms_per_lane,
            field="timeout_ms_per_lane",
            minimum=1,
            maximum=MAX_PARALLEL_LANE_TIMEOUT_MS,
        )
        selected_relation_limit = self._parallel_integer(
            relation_limit,
            field="relation_limit",
            minimum=1,
            maximum=MAX_CROSS_LANE_RELATIONS,
        )
        selected_conflict_limit = self._parallel_integer(
            conflict_limit,
            field="conflict_limit",
            minimum=1,
            maximum=MAX_CROSS_LANE_CONFLICTS,
        )
        selected_synthesis_limit = self._parallel_integer(
            synthesis_limit,
            field="synthesis_limit",
            minimum=1,
            maximum=MAX_CROSS_LANE_SYNTHESIS,
        )

        bundle = self._parallel_bundle(project_id, pv_ref)
        resolved_pv_ref = str(bundle["_resolved_pv_ref"])
        bundle_sha256 = str(bundle.get("bundle_sha256") or "")
        canonical_lanes: list[str] = []
        for lane_index, raw_lane in enumerate(lane_set):
            require(
                isinstance(raw_lane, str) and bool(raw_lane.strip()),
                "CROSS_LANE_SET_INVALID",
                "Every cross-lane set member must be one exact lane identifier.",
                status="BLOCKED",
                lane_index=lane_index,
            )
            try:
                lane = get_lane(raw_lane.strip(), code_mode=str(bundle["code_mode"]))
            except LaneRegistryError as error:
                raise EvidenceLaneError(
                    code="CROSS_LANE_SET_INVALID",
                    message="A cross-lane set member is unsupported by this PV.",
                    status="BLOCKED",
                    details={"lane_index": lane_index, "lane": raw_lane},
                ) from error
            canonical_lanes.append(lane.canonical_lane_id)
        require(
            len(set(canonical_lanes)) == len(canonical_lanes),
            "CROSS_LANE_SET_DUPLICATE",
            "A cross-lane set must contain unique canonical lanes.",
            status="BLOCKED",
            canonical_lane_set=canonical_lanes,
        )

        required_aggregate_limit = len(canonical_lanes) * per_lane_limit
        require(
            required_aggregate_limit <= MAX_PARALLEL_AGGREGATE_RESULTS,
            "CROSS_LANE_AGGREGATE_BUDGET_INVALID",
            "The selected per-lane result budget exceeds the bounded cross-lane aggregate.",
            status="BLOCKED",
            lane_count=len(canonical_lanes),
            result_limit_per_lane=per_lane_limit,
            required_aggregate_limit=required_aggregate_limit,
            maximum_aggregate_limit=MAX_PARALLEL_AGGREGATE_RESULTS,
        )
        selected_aggregate_limit = (
            required_aggregate_limit
            if aggregate_limit is None
            else self._parallel_integer(
                aggregate_limit,
                field="aggregate_limit",
                minimum=1,
                maximum=MAX_PARALLEL_AGGREGATE_RESULTS,
            )
        )
        require(
            selected_aggregate_limit >= required_aggregate_limit,
            "CROSS_LANE_AGGREGATE_BUDGET_INVALID",
            "The aggregate result budget must cover every explicit lane budget.",
            status="BLOCKED",
            lane_count=len(canonical_lanes),
            result_limit_per_lane=per_lane_limit,
            required_aggregate_limit=required_aggregate_limit,
            selected_aggregate_limit=selected_aggregate_limit,
        )

        selected_relation_types = list(
            CROSS_LANE_RELATION_TYPES if relation_types is None else relation_types
        )
        require(
            bool(selected_relation_types)
            and all(isinstance(item, str) for item in selected_relation_types)
            and len(set(selected_relation_types)) == len(selected_relation_types)
            and set(selected_relation_types).issubset(CROSS_LANE_RELATION_TYPES),
            "CROSS_LANE_RELATION_TYPE_INVALID",
            "Cross-lane joins require a unique non-empty allowlisted relation set.",
            status="BLOCKED",
            supported_relation_types=list(CROSS_LANE_RELATION_TYPES),
        )

        lane_queries = [
            {
                "lane": lane_id,
                "query": normalized_query,
                "retrieval": retrieval,
                "limit": per_lane_limit,
                "timeout_ms": per_lane_timeout,
            }
            for lane_id in canonical_lanes
        ]
        parallel = self.search_parallel(
            project_id,
            lane_queries,
            pv_ref=None,
            max_workers=max_workers,
            aggregate_limit=selected_aggregate_limit,
            cancel_event=cancel_event,
        )

        lane_positions = {lane_id: index for index, lane_id in enumerate(canonical_lanes)}
        lane_ranks = {lane_id: 0 for lane_id in canonical_lanes}
        results: list[dict[str, Any]] = []
        for raw_result in parallel["results"]:
            result = dict(raw_result)
            lane_id = str(result.get("canonical_lane_id") or "")
            require(
                lane_id in lane_positions,
                "CROSS_LANE_RESULT_AUTHORITY_INVALID",
                "A cross-lane result is outside the explicit canonical lane set.",
                status="MISMATCH",
                canonical_lane_id=lane_id,
            )
            source_sha256 = str(result.get("source_sha256") or "")
            chunk_sha256 = str(result.get("chunk_sha256") or "")
            require(
                len(source_sha256) == 64
                and len(chunk_sha256) == 64
                and bool(result.get("ref_id"))
                and bool(result.get("path"))
                and bool(result.get("locator")),
                "CROSS_LANE_RESULT_AUTHORITY_INVALID",
                "Every cross-lane result must carry complete immutable authority provenance.",
                status="MISMATCH",
                canonical_lane_id=lane_id,
            )
            lane_ranks[lane_id] += 1
            authority = {
                "project_id": project_id,
                "live_root_authority_ref": resolved_pv_ref,
                "bundle_sha256": bundle_sha256,
                "canonical_lane_id": lane_id,
                "ref_id": str(result["ref_id"]),
                "path": str(result["path"]),
                "locator": str(result["locator"]),
                "source_sha256": source_sha256,
                "chunk_sha256": chunk_sha256,
                "parser_state": str(result.get("parser_state") or "UNKNOWN"),
            }
            authority_sha256 = sha256_bytes(canonical_json_bytes(authority))
            result.pop("request_indexes", None)
            result.pop("dedupe_key_sha256", None)
            results.append(
                {
                    **result,
                    "lane_rank": lane_ranks[lane_id],
                    "rank_domain": f"LANE:{lane_id}",
                    "cross_lane_score": None,
                    "result_id": f"cross-lane-result:{authority_sha256}",
                    "authority_provenance": authority,
                    "authority_provenance_sha256": authority_sha256,
                }
            )
        results.sort(
            key=lambda result: (
                lane_positions[str(result["canonical_lane_id"])],
                int(result["lane_rank"]),
                str(result["result_id"]),
            )
        )

        all_relations: list[dict[str, Any]] = []
        for relation_type in selected_relation_types:
            for key_sha256, members in self._cross_lane_groups(
                results, relation_type=relation_type
            ):
                lane_ids = sorted(
                    {str(member["canonical_lane_id"]) for member in members},
                    key=lane_positions.__getitem__,
                )
                if len(lane_ids) < 2:
                    continue
                member_ids = [str(member["result_id"]) for member in members]
                relation_body = {
                    "relation_type": relation_type,
                    "relation_key_sha256": key_sha256,
                    "lane_ids": lane_ids,
                    "member_result_ids": member_ids,
                    "member_count": len(member_ids),
                }
                relation_sha256 = sha256_bytes(canonical_json_bytes(relation_body))
                all_relations.append(
                    {
                        **relation_body,
                        "relation_id": f"cross-lane-relation:{relation_sha256}",
                        "relation_sha256": relation_sha256,
                    }
                )
        all_relations.sort(key=lambda item: (item["relation_type"], item["relation_id"]))
        relations = all_relations[:selected_relation_limit]

        all_conflicts: list[dict[str, Any]] = []
        for conflict_type in (
            "PATH_LOCATOR_CONTENT_CONFLICT",
            "SOURCE_LOCATOR_CONTENT_CONFLICT",
        ):
            for basis_sha256, members in self._cross_lane_conflict_groups(
                results, conflict_type=conflict_type
            ):
                lane_ids = sorted(
                    {str(member["canonical_lane_id"]) for member in members},
                    key=lane_positions.__getitem__,
                )
                observed_chunks = sorted(
                    {
                        str(member["authority_provenance"]["chunk_sha256"])
                        for member in members
                    }
                )
                if len(lane_ids) < 2 or len(observed_chunks) < 2:
                    continue
                member_ids = [str(member["result_id"]) for member in members]
                conflict_body = {
                    "conflict_type": conflict_type,
                    "basis_sha256": basis_sha256,
                    "lane_ids": lane_ids,
                    "member_result_ids": member_ids,
                    "observed_chunk_sha256": observed_chunks,
                    "member_count": len(member_ids),
                }
                conflict_sha256 = sha256_bytes(canonical_json_bytes(conflict_body))
                all_conflicts.append(
                    {
                        **conflict_body,
                        "conflict_id": f"cross-lane-conflict:{conflict_sha256}",
                        "conflict_sha256": conflict_sha256,
                    }
                )
        all_conflicts.sort(key=lambda item: (item["conflict_type"], item["conflict_id"]))
        conflicts = all_conflicts[:selected_conflict_limit]

        synthesis_candidates = [
            {
                "kind": "EXPLICIT_CONFLICT",
                "source_id": item["conflict_id"],
                "basis_type": item["conflict_type"],
                "lane_ids": item["lane_ids"],
                "member_result_ids": item["member_result_ids"],
                "statement": "Exact authority keys disagree on immutable chunk bytes.",
            }
            for item in conflicts
        ]
        synthesis_candidates.extend(
            {
                "kind": "SUPPORTED_RELATION",
                "source_id": item["relation_id"],
                "basis_type": item["relation_type"],
                "lane_ids": item["lane_ids"],
                "member_result_ids": item["member_result_ids"],
                "statement": "Exact immutable authority keys connect these lane results.",
            }
            for item in relations
        )
        synthesis = synthesis_candidates[:selected_synthesis_limit]

        source_status = str(parallel["status"])
        if source_status in {"PARTIAL", "CANCELLED"}:
            status = source_status
        elif conflicts:
            status = "CONFLICT"
        else:
            status = source_status
        results_sha256 = sha256_bytes(canonical_json_bytes(results))
        relations_sha256 = sha256_bytes(canonical_json_bytes(relations))
        conflicts_sha256 = sha256_bytes(canonical_json_bytes(conflicts))
        synthesis_sha256 = sha256_bytes(canonical_json_bytes(synthesis))
        lane_receipts = [
            {
                key: request[key]
                for key in (
                    "canonical_lane_id",
                    "request_sha256",
                    "status",
                    "within_time_budget",
                    "result_count",
                    "result_sha256",
                )
            }
            for request in parallel["receipt"]["request_receipts"]
        ]
        lane_receipts_sha256 = sha256_bytes(canonical_json_bytes(lane_receipts))
        receipt_body = {
            "schema": "evidence-lane.cross-lane-query-receipt.v1",
            "status": status,
            "source_status": source_status,
            "project_id": project_id,
            "live_root_authority_ref": resolved_pv_ref,
            "bundle_sha256": bundle_sha256,
            "canonical_lane_set": canonical_lanes,
            "query": normalized_query,
            "retrieval": retrieval,
            "rank_contract": "INDEPENDENT_PER_LANE_SOURCE_RANK_ONLY",
            "cross_lane_score_emitted": False,
            "relation_types": selected_relation_types,
            "parallel_receipt_sha256": parallel["receipt"]["receipt_sha256"],
            "lane_receipts_sha256": lane_receipts_sha256,
            "budgets": {
                "result_limit_per_lane": per_lane_limit,
                "timeout_ms_per_lane": per_lane_timeout,
                "aggregate_limit": int(parallel["receipt"]["budgets"]["selected_aggregate_limit"]),
                "required_aggregate_limit": required_aggregate_limit,
                "relation_limit": selected_relation_limit,
                "conflict_limit": selected_conflict_limit,
                "synthesis_limit": selected_synthesis_limit,
            },
            "result_count": len(results),
            "relation_count_before_limit": len(all_relations),
            "relation_count": len(relations),
            "relation_omitted_count": len(all_relations) - len(relations),
            "conflict_count_before_limit": len(all_conflicts),
            "conflict_count": len(conflicts),
            "conflict_omitted_count": len(all_conflicts) - len(conflicts),
            "synthesis_count_before_limit": len(synthesis_candidates),
            "synthesis_count": len(synthesis),
            "synthesis_omitted_count": len(synthesis_candidates) - len(synthesis),
            "results_sha256": results_sha256,
            "relations_sha256": relations_sha256,
            "conflicts_sha256": conflicts_sha256,
            "synthesis_sha256": synthesis_sha256,
            "synthesis_mode": "EVIDENCE_ONLY_NO_SEMANTIC_INFERENCE",
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        return {
            "schema": "evidence-lane.cross-lane-query.v1",
            "status": status,
            "source_status": source_status,
            "project_id": project_id,
            "live_root_authority_ref": resolved_pv_ref,
            "bundle_sha256": bundle_sha256,
            "canonical_lane_set": canonical_lanes,
            "query": normalized_query,
            "rank_contract": "INDEPENDENT_PER_LANE_SOURCE_RANK_ONLY",
            "lane_receipts": lane_receipts,
            "results": results,
            "relations": relations,
            "conflicts": conflicts,
            "synthesis": synthesis,
            "receipt": receipt,
        }

    def _cross_project_permission(
        self,
        *,
        principal_id: str,
        project_id: str,
        live_root_authority_ref: str,
    ) -> dict[str, Any]:
        require(
            self.cross_project_authorizer is not None,
            "CROSS_PROJECT_PERMISSION_PROVIDER_REQUIRED",
            "Cross-project reads require an explicit host permission provider.",
            status="BLOCKED",
            project_id=project_id,
        )
        request = {
            "schema": "evidence-lane.cross-project-read-request.v1",
            "principal_id": principal_id,
            "project_id": project_id,
            "live_root_authority_ref": live_root_authority_ref,
            "scope": "READ_LIVE_PROJECT_ROOT",
        }
        authorizer = cast(
            Callable[[dict[str, Any]], dict[str, Any]],
            self.cross_project_authorizer,
        )
        grant = authorizer(request)
        require(
            isinstance(grant, dict),
            "CROSS_PROJECT_PERMISSION_GRANT_INVALID",
            "The cross-project permission provider returned no structured grant.",
            status="BLOCKED",
            project_id=project_id,
        )
        allowed_fields = {
            "schema",
            "status",
            "principal_id",
            "project_id",
            "live_root_authority_ref",
            "scope",
            "grant_id",
            "grant_sha256",
        }
        require(
            set(grant) == allowed_fields,
            "CROSS_PROJECT_PERMISSION_GRANT_INVALID",
            "A permission grant must contain only the exact public grant fields.",
            status="BLOCKED",
            project_id=project_id,
            fields=sorted(grant),
        )
        grant_body = {
            key: grant[key] for key in sorted(allowed_fields - {"grant_sha256"})
        }
        grant_sha256 = sha256_bytes(canonical_json_bytes(grant_body))
        grant_id = str(grant.get("grant_id") or "")
        require(
            grant.get("schema") == "evidence-lane.cross-project-read-grant.v1"
            and grant.get("status") == "PASS"
            and grant.get("principal_id") == principal_id
            and grant.get("project_id") == project_id
            and grant.get("live_root_authority_ref") == live_root_authority_ref
            and grant.get("scope") == "READ_LIVE_PROJECT_ROOT"
            and bool(grant_id)
            and len(grant_id) <= 128
            and all(character in _PUBLIC_ID_CHARS for character in grant_id)
            and grant.get("grant_sha256") == grant_sha256,
            "CROSS_PROJECT_PERMISSION_GRANT_INVALID",
            "The permission grant does not seal the exact principal, project, live-root authority, and read scope.",
            status="BLOCKED",
            project_id=project_id,
        )
        return {
            "schema": str(grant["schema"]),
            "status": "PASS",
            "principal_id": principal_id,
            "project_id": project_id,
            "live_root_authority_ref": live_root_authority_ref,
            "scope": "READ_LIVE_PROJECT_ROOT",
            "grant_id": grant_id,
            "grant_sha256": grant_sha256,
        }

    def _cross_project_worker(
        self,
        *,
        job: dict[str, Any],
        cancel_event: Event,
    ) -> dict[str, Any]:
        if cancel_event.is_set():
            return {"kind": "CANCELLED", "completed_at": time.monotonic()}
        try:
            result = self.search_parallel(
                str(job["project_id"]),
                list(job["lane_queries"]),
                pv_ref=None,
                max_workers=int(job["lane_workers"]),
                aggregate_limit=int(job["project_result_limit"]),
                cancel_event=cancel_event,
            )
        except EvidenceLaneError as error:
            return {
                "kind": "ERROR",
                "completed_at": time.monotonic(),
                "error": error.as_dict(),
            }
        except Exception as error:  # noqa: BLE001  # pragma: no cover
            return {
                "kind": "ERROR",
                "completed_at": time.monotonic(),
                "error": {
                    "code": "CROSS_PROJECT_WORKER_FAILED",
                    "message": "A bounded cross-project query worker failed unexpectedly.",
                    "status": "FAIL",
                    "details": {"exception_type": type(error).__name__},
                },
            }
        return {
            "kind": "RESULT",
            "completed_at": time.monotonic(),
            "result": result,
        }

    def search_cross_project(
        self,
        principal_id: str,
        project_queries: list[dict[str, Any]],
        *,
        max_project_workers: int = DEFAULT_CROSS_PROJECT_WORKERS,
        overall_result_limit: int = DEFAULT_CROSS_PROJECT_RESULTS,
        cancel_event: Event | None = None,
    ) -> dict[str, Any]:
        """Read explicit live project roots without weakening isolation."""

        exact_principal = str(principal_id or "").strip()
        require(
            bool(exact_principal)
            and len(exact_principal) <= 128
            and all(character in _PUBLIC_ID_CHARS for character in exact_principal),
            "CROSS_PROJECT_PRINCIPAL_INVALID",
            "Cross-project reads require one bounded explicit principal ID.",
            status="BLOCKED",
        )
        require(
            isinstance(project_queries, list)
            and MIN_CROSS_PROJECTS <= len(project_queries) <= MAX_CROSS_PROJECTS,
            "CROSS_PROJECT_SET_COUNT_INVALID",
            "Cross-project search requires an explicit set of two to four projects.",
            status="BLOCKED",
            count=len(project_queries) if isinstance(project_queries, list) else None,
        )
        selected_project_workers = self._parallel_integer(
            max_project_workers,
            field="max_project_workers",
            minimum=1,
            maximum=MAX_CROSS_PROJECT_WORKERS,
        )
        selected_overall_limit = self._parallel_integer(
            overall_result_limit,
            field="overall_result_limit",
            minimum=1,
            maximum=MAX_CROSS_PROJECT_RESULTS,
        )

        jobs: list[dict[str, Any]] = []
        canonical_project_keys: set[str] = set()
        allowed_project_fields = {
            "project_id",
            "lane_queries",
            "project_timeout_ms",
            "project_result_limit",
            "lane_workers",
        }
        for project_index, raw in enumerate(project_queries):
            require(
                isinstance(raw, dict),
                "CROSS_PROJECT_QUERY_INVALID",
                "Every cross-project query must be one structured object.",
                status="BLOCKED",
                project_index=project_index,
            )
            unknown_fields = sorted(set(raw) - allowed_project_fields)
            require(
                not unknown_fields,
                "CROSS_PROJECT_QUERY_FIELD_UNSUPPORTED",
                "A cross-project query contains unsupported fields.",
                status="BLOCKED",
                project_index=project_index,
                fields=unknown_fields,
            )
            project_id = self.store.validate_project_id(
                str(raw.get("project_id") or "").strip()
            )
            comparison_key = self.store.canonical_project_key(project_id)
            require(
                comparison_key not in canonical_project_keys,
                "CROSS_PROJECT_SET_DUPLICATE",
                "The explicit project set contains a duplicate canonical project ID.",
                status="BLOCKED",
                project_id=project_id,
            )
            canonical_project_keys.add(comparison_key)
            config = self.store.config(project_id)
            require(
                config.enabled is True,
                "CROSS_PROJECT_DISABLED",
                "A disabled project cannot participate in a cross-project read.",
                status="BLOCKED",
                project_id=project_id,
            )
            bundle = self._parallel_bundle(project_id, None)
            live_root_authority_ref = str(bundle["_resolved_pv_ref"])
            require(
                bool(live_root_authority_ref),
                "CROSS_PROJECT_LIVE_ROOT_BINDING_MISMATCH",
                "A cross-project query did not resolve one exact live-root authority.",
                status="MISMATCH",
                project_id=project_id,
                live_root_authority_ref=live_root_authority_ref,
            )
            permission = self._cross_project_permission(
                principal_id=exact_principal,
                project_id=project_id,
                live_root_authority_ref=live_root_authority_ref,
            )
            project_timeout_ms = self._parallel_integer(
                raw.get("project_timeout_ms", DEFAULT_CROSS_PROJECT_TIMEOUT_MS),
                field=f"project_queries[{project_index}].project_timeout_ms",
                minimum=1,
                maximum=MAX_CROSS_PROJECT_TIMEOUT_MS,
            )
            project_result_limit = self._parallel_integer(
                raw.get("project_result_limit", DEFAULT_PARALLEL_AGGREGATE_RESULTS),
                field=f"project_queries[{project_index}].project_result_limit",
                minimum=1,
                maximum=MAX_PARALLEL_AGGREGATE_RESULTS,
            )
            lane_workers = self._parallel_integer(
                raw.get("lane_workers", MAX_PARALLEL_LANE_WORKERS),
                field=f"project_queries[{project_index}].lane_workers",
                minimum=1,
                maximum=MAX_PARALLEL_LANE_WORKERS,
            )
            raw_lane_queries = raw.get("lane_queries")
            require(
                isinstance(raw_lane_queries, list)
                and 1 <= len(raw_lane_queries) <= MAX_PARALLEL_LANE_QUERIES,
                "CROSS_PROJECT_LANE_QUERY_COUNT_INVALID",
                "Every project requires between one and eight explicit lane queries.",
                status="BLOCKED",
                project_id=project_id,
            )
            lane_queries = cast(list[dict[str, Any]], raw_lane_queries)
            normalized_lane_queries: list[dict[str, Any]] = []
            for lane_index, lane_query in enumerate(lane_queries):
                require(
                    isinstance(lane_query, dict),
                    "CROSS_PROJECT_LANE_QUERY_INVALID",
                    "Every project lane query must be one structured object.",
                    status="BLOCKED",
                    project_id=project_id,
                    lane_index=lane_index,
                )
                unknown_lane_fields = sorted(
                    set(lane_query)
                    - {"lane", "query", "retrieval", "limit", "timeout_ms"}
                )
                require(
                    not unknown_lane_fields,
                    "CROSS_PROJECT_LANE_QUERY_FIELD_UNSUPPORTED",
                    "A project lane query contains unsupported fields.",
                    status="BLOCKED",
                    project_id=project_id,
                    lane_index=lane_index,
                    fields=unknown_lane_fields,
                )
                lane_alias = str(lane_query.get("lane") or "").strip()
                query = str(lane_query.get("query") or "").strip()
                require(
                    bool(lane_alias)
                    and len(lane_alias) <= 128
                    and bool(query)
                    and len(query) <= MAX_PARALLEL_LANE_QUERY_CHARS,
                    "CROSS_PROJECT_LANE_QUERY_INVALID",
                    "Every project lane query requires a bounded lane and query.",
                    status="BLOCKED",
                    project_id=project_id,
                    lane_index=lane_index,
                )
                try:
                    lane = get_lane(lane_alias, code_mode=str(bundle["code_mode"]))
                except LaneRegistryError as error:
                    raise EvidenceLaneError(
                        code="CROSS_PROJECT_LANE_QUERY_INVALID",
                        message="A project lane query names an unsupported lane.",
                        status="BLOCKED",
                        details={
                            "project_id": project_id,
                            "lane_index": lane_index,
                            "lane": lane_alias,
                        },
                    ) from error
                self._fts_query(query)
                retrieval = str(lane_query.get("retrieval") or "hybrid").lower()
                require(
                    retrieval in {"hybrid", "fts5", "bm25", "tfidf"},
                    "LANE_RETRIEVAL_INVALID",
                    "Lane retrieval must be hybrid, fts5, bm25, or tfidf.",
                    status="BLOCKED",
                    project_id=project_id,
                    lane_index=lane_index,
                )
                result_limit = self._parallel_integer(
                    lane_query.get("limit", 20),
                    field=(
                        f"project_queries[{project_index}]."
                        f"lane_queries[{lane_index}].limit"
                    ),
                    minimum=1,
                    maximum=100,
                )
                timeout_ms = self._parallel_integer(
                    lane_query.get("timeout_ms", DEFAULT_PARALLEL_LANE_TIMEOUT_MS),
                    field=(
                        f"project_queries[{project_index}]."
                        f"lane_queries[{lane_index}].timeout_ms"
                    ),
                    minimum=1,
                    maximum=MAX_PARALLEL_LANE_TIMEOUT_MS,
                )
                require(
                    timeout_ms <= project_timeout_ms,
                    "CROSS_PROJECT_TIMEOUT_HIERARCHY_INVALID",
                    "A lane timeout cannot exceed its enclosing project timeout.",
                    status="BLOCKED",
                    project_id=project_id,
                    lane_index=lane_index,
                )
                normalized_lane_queries.append(
                    {
                        "lane": lane.canonical_lane_id,
                        "query": query,
                        "retrieval": retrieval,
                        "limit": result_limit,
                        "timeout_ms": timeout_ms,
                    }
                )
            required_project_result_limit = sum(
                int(lane_query["limit"]) for lane_query in normalized_lane_queries
            )
            require(
                required_project_result_limit <= MAX_PARALLEL_AGGREGATE_RESULTS
                and project_result_limit >= required_project_result_limit,
                "CROSS_PROJECT_RESULT_BUDGET_INVALID",
                "A project result budget must cover every explicit lane-query budget.",
                status="BLOCKED",
                project_id=project_id,
                required_project_result_limit=required_project_result_limit,
                selected_project_result_limit=project_result_limit,
                maximum_project_result_limit=MAX_PARALLEL_AGGREGATE_RESULTS,
            )
            authority_binding = {
                "project_id": project_id,
                "live_root_authority_ref": live_root_authority_ref,
                "bundle_sha256": str(bundle["bundle_sha256"]),
                "permission_grant_sha256": permission["grant_sha256"],
                "accepted_archive_opened": False,
                "accepted_archive_queried": False,
            }
            jobs.append(
                {
                    "project_index": project_index,
                    "project_id": project_id,
                    "live_root_authority_ref": live_root_authority_ref,
                    "bundle_sha256": str(bundle["bundle_sha256"]),
                    "permission": permission,
                    "authority_binding_sha256": sha256_bytes(
                        canonical_json_bytes(authority_binding)
                    ),
                    "project_timeout_ms": project_timeout_ms,
                    "project_result_limit": project_result_limit,
                    "required_project_result_limit": required_project_result_limit,
                    "lane_workers": lane_workers,
                    "lane_queries": normalized_lane_queries,
                }
            )

        required_overall_result_limit = sum(
            int(job["project_result_limit"]) for job in jobs
        )
        require(
            required_overall_result_limit <= MAX_CROSS_PROJECT_RESULTS
            and selected_overall_limit >= required_overall_result_limit,
            "CROSS_PROJECT_OVERALL_BUDGET_INVALID",
            "The overall result budget must cover every explicit project budget.",
            status="BLOCKED",
            required_overall_result_limit=required_overall_result_limit,
            selected_overall_result_limit=selected_overall_limit,
            maximum_overall_result_limit=MAX_CROSS_PROJECT_RESULTS,
        )

        cancellation = cancel_event or Event()
        project_receipts: list[dict[str, Any]] = []
        project_results: dict[int, dict[str, Any]] = {}
        if cancellation.is_set():
            for job in jobs:
                project_receipts.append(
                    {
                        **job,
                        "status": "CANCELLED",
                        "within_time_budget": True,
                        "result_count": 0,
                        "result_sha256": None,
                        "lane_receipts": [],
                    }
                )
        else:
            executor = ThreadPoolExecutor(
                max_workers=min(selected_project_workers, len(jobs)),
                thread_name_prefix="evidence-cross-project-query",
            )
            pending: dict[Future[dict[str, Any]], dict[str, Any]] = {}
            deadlines: dict[Future[dict[str, Any]], float] = {}
            project_cancellations: dict[Future[dict[str, Any]], Event] = {}
            try:
                for job in jobs:
                    project_cancellation = Event()
                    submitted_at = time.monotonic()
                    future = executor.submit(
                        self._cross_project_worker,
                        job=job,
                        cancel_event=project_cancellation,
                    )
                    pending[future] = job
                    deadlines[future] = submitted_at + (
                        int(job["project_timeout_ms"]) / 1_000
                    )
                    project_cancellations[future] = project_cancellation
                while pending:
                    if cancellation.is_set():
                        for future, job in list(pending.items()):
                            project_cancellations[future].set()
                            future.cancel()
                            project_receipts.append(
                                {
                                    **job,
                                    "status": "CANCELLED",
                                    "within_time_budget": True,
                                    "result_count": 0,
                                    "result_sha256": None,
                                    "lane_receipts": [],
                                }
                            )
                            pending.pop(future)
                        break
                    now = time.monotonic()
                    expired = [
                        future
                        for future in pending
                        if not future.done() and now >= deadlines[future]
                    ]
                    for future in expired:
                        job = pending.pop(future)
                        project_cancellations[future].set()
                        future.cancel()
                        project_receipts.append(
                            {
                                **job,
                                "status": "TIMEOUT",
                                "within_time_budget": False,
                                "result_count": 0,
                                "result_sha256": None,
                                "lane_receipts": [],
                            }
                        )
                    if not pending:
                        break
                    nearest = min(deadlines[future] for future in pending)
                    wait_seconds = max(0.0, min(0.05, nearest - time.monotonic()))
                    completed, _ = wait(
                        tuple(pending),
                        timeout=wait_seconds,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in completed:
                        job = pending.pop(future)
                        worker = future.result()
                        if float(worker["completed_at"]) > deadlines[future]:
                            project_cancellations[future].set()
                            project_receipts.append(
                                {
                                    **job,
                                    "status": "TIMEOUT",
                                    "within_time_budget": False,
                                    "result_count": 0,
                                    "result_sha256": None,
                                    "lane_receipts": [],
                                }
                            )
                            continue
                        if worker["kind"] == "CANCELLED":
                            project_receipts.append(
                                {
                                    **job,
                                    "status": "CANCELLED",
                                    "within_time_budget": True,
                                    "result_count": 0,
                                    "result_sha256": None,
                                    "lane_receipts": [],
                                }
                            )
                            continue
                        if worker["kind"] == "ERROR":
                            worker_error = dict(worker["error"])
                            project_receipts.append(
                                {
                                    **job,
                                    "status": str(
                                        worker_error.get("status") or "FAIL"
                                    ),
                                    "within_time_budget": True,
                                    "result_count": 0,
                                    "result_sha256": None,
                                    "lane_receipts": [],
                                    "error": worker_error,
                                }
                            )
                            continue
                        result = dict(worker["result"])
                        project_results[int(job["project_index"])] = result
                        project_receipts.append(
                            {
                                **job,
                                "status": str(result.get("status") or "FAIL"),
                                "within_time_budget": True,
                                "result_count": len(result.get("results") or []),
                                "result_sha256": sha256_bytes(
                                    canonical_json_bytes(result)
                                ),
                                "lane_receipts": list(
                                    result["receipt"]["request_receipts"]
                                ),
                            }
                        )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)

        project_receipts.sort(key=lambda row: int(row["project_index"]))
        results: list[dict[str, Any]] = []
        for project_receipt in project_receipts:
            project_index = int(project_receipt["project_index"])
            project_result = project_results.get(project_index)
            if project_result is None:
                continue
            for project_rank, raw_result in enumerate(
                project_result.get("results") or [], start=1
            ):
                result = dict(raw_result)
                require(
                    result.get("project_id") == project_receipt["project_id"]
                    and result.get("live_root_authority_ref")
                    == project_receipt["live_root_authority_ref"]
                    and bool(str(result.get("canonical_lane_id") or ""))
                    and bool(str(result.get("ref_id") or ""))
                    and bool(str(result.get("path") or ""))
                    and bool(str(result.get("locator") or ""))
                    and len(str(result.get("source_sha256") or "")) == 64
                    and len(str(result.get("chunk_sha256") or "")) == 64,
                    "CROSS_PROJECT_RESULT_AUTHORITY_INVALID",
                    "A cross-project result is not bound to its exact project and live-root authority.",
                    status="MISMATCH",
                    project_id=project_receipt["project_id"],
                )
                authority = {
                    "project_id": str(project_receipt["project_id"]),
                    "live_root_authority_ref": str(
                        project_receipt["live_root_authority_ref"]
                    ),
                    "bundle_sha256": str(project_receipt["bundle_sha256"]),
                    "permission_grant_sha256": str(
                        project_receipt["permission"]["grant_sha256"]
                    ),
                    "canonical_lane_id": str(result["canonical_lane_id"]),
                    "ref_id": str(result["ref_id"]),
                    "path": str(result["path"]),
                    "locator": str(result["locator"]),
                    "source_sha256": str(result["source_sha256"]),
                    "chunk_sha256": str(result["chunk_sha256"]),
                }
                authority_sha256 = sha256_bytes(canonical_json_bytes(authority))
                results.append(
                    {
                        **result,
                        "project_index": project_index,
                        "project_rank": project_rank,
                        "rank_domain": (
                            f"PROJECT:{project_receipt['project_id']}:"
                            "PARALLEL_AGGREGATE_SOURCE_ORDER"
                        ),
                        "cross_project_score": None,
                        "cross_project_authority": authority,
                        "cross_project_authority_sha256": authority_sha256,
                    }
                )
        result_count_before_limit = len(results)
        results = results[:selected_overall_limit]

        statuses = [str(receipt["status"]) for receipt in project_receipts]
        if statuses and all(status == "CANCELLED" for status in statuses):
            status = "CANCELLED"
        elif any(item not in {"PASS", "EMPTY", "STALE"} for item in statuses):
            status = "PARTIAL"
        elif any(item == "STALE" for item in statuses):
            status = "STALE"
        elif results:
            status = "PASS"
        else:
            status = "EMPTY"

        public_project_receipts = [
            {
                key: receipt[key]
                for key in (
                    "project_index",
                    "project_id",
                    "live_root_authority_ref",
                    "bundle_sha256",
                    "permission",
                    "authority_binding_sha256",
                    "project_timeout_ms",
                    "project_result_limit",
                    "required_project_result_limit",
                    "lane_workers",
                    "status",
                    "within_time_budget",
                    "result_count",
                    "result_sha256",
                    "lane_receipts",
                )
            }
            | ({"error": receipt["error"]} if "error" in receipt else {})
            for receipt in project_receipts
        ]
        results_sha256 = sha256_bytes(canonical_json_bytes(results))
        project_receipts_sha256 = sha256_bytes(
            canonical_json_bytes(public_project_receipts)
        )
        receipt_body = {
            "schema": "evidence-lane.cross-project-query-receipt.v1",
            "status": status,
            "principal_id": exact_principal,
            "project_set": [str(job["project_id"]) for job in jobs],
            "ranking": "SEPARATE_PROJECT_AGGREGATE_RANK_DOMAINS",
            "cross_project_score_emitted": False,
            "budgets": {
                "maximum_projects": MAX_CROSS_PROJECTS,
                "selected_project_workers": min(
                    selected_project_workers, len(jobs)
                ),
                "maximum_project_timeout_ms": MAX_CROSS_PROJECT_TIMEOUT_MS,
                "selected_overall_result_limit": selected_overall_limit,
                "required_overall_result_limit": required_overall_result_limit,
                "maximum_overall_results": MAX_CROSS_PROJECT_RESULTS,
            },
            "result_count_before_limit": result_count_before_limit,
            "result_count": len(results),
            "results_truncated": result_count_before_limit > len(results),
            "result_omitted_count": result_count_before_limit - len(results),
            "project_receipts_sha256": project_receipts_sha256,
            "results_sha256": results_sha256,
            "permission_provider_required": True,
            "implicit_project_discovery": False,
            "default_single_project_isolation_preserved": True,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
        }
        return {
            "schema": "evidence-lane.cross-project-query.v1",
            "status": status,
            "principal_id": exact_principal,
            "project_set": [str(job["project_id"]) for job in jobs],
            "rank_contract": "SEPARATE_PROJECT_AGGREGATE_RANK_DOMAINS",
            "project_receipts": public_project_receipts,
            "results": results,
            "receipt": receipt,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
        }

    def fetch_source(
        self,
        project_id: str,
        lane_alias: str,
        path: str,
        *,
        pv_ref: str | None = None,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        require(
            1 <= max_bytes <= 1_000_000,
            "LANE_FETCH_LIMIT_INVALID",
            "Lane fetch max_bytes must be between 1 and 1,000,000.",
            status="BLOCKED",
        )
        lanes_root, authority_ref, lane, _ = self._resolve(
            project_id, lane_alias, pv_ref
        )
        database_path = lanes_root / lane.canonical_lane_id / lane.sqlite_filename
        connection = sqlite3.connect(
            f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            """
            SELECT source_id, path, size_bytes, sha256, mime_type, extension,
                   encoding, parser_state, exact_bytes
            FROM source_registry WHERE path=?
            """,
            (path.replace("\\", "/"),),
        ).fetchone()
        require(
            row is not None,
            "LANE_SOURCE_NOT_FOUND",
            "The requested source is not registered in this lane.",
            status="EMPTY",
            path=path,
        )
        facts = [
            {
                **dict(fact),
                "payload": json.loads(fact["payload_json"]),
            }
            for fact in connection.execute(
                """
                SELECT kind, locator, payload_json
                FROM structured_fact WHERE source_id=?
                ORDER BY fact_id LIMIT 200
                """,
                (row["source_id"],),
            )
        ]
        connection.close()
        data = bytes(row["exact_bytes"])
        if row["encoding"]:
            content = data[:max_bytes].decode(row["encoding"], errors="replace")
            representation = "text"
        else:
            content = None
            representation = "binary_exact_bytes_not_returned"
        freshness = self._freshness(project_id, lanes_root, authority_ref)
        return {
            "status": result_status("PASS", freshness),
            "project_id": project_id,
            "pv_ref": authority_ref,
            "lane": lane.as_dict(),
            "path": row["path"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "mime_type": row["mime_type"],
            "parser_state": row["parser_state"],
            "representation": representation,
            "content": content,
            "truncated": len(data) > max_bytes,
            "facts": facts,
            "freshness": freshness,
        }
