"""Bounded FTS5/BM25 + TF-IDF reads over universal lane SQLite authorities."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .errors import require
from .freshness import evaluate_freshness, result_status
from .lane_engine import validate_lane_bundle
from .lanes import LANE_REGISTRY, catalog, get_lane
from .pv_package import validate_pv_package
from .reader import PVReader
from .store import ProjectStore

_TOKEN_RE = re.compile(r"[\w][\w.-]{1,63}", flags=re.UNICODE)


class LaneReader:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store
        self.pv_reader = PVReader(store)

    def _resolve(
        self,
        project_id: str,
        lane_alias: str,
        pv_ref: str | None,
    ) -> tuple[Path, Any, dict[str, Any]]:
        package = self.pv_reader.resolve(project_id, pv_ref)
        validate_pv_package(package)
        lanes_root = package / "lanes"
        validation = validate_lane_bundle(lanes_root)
        require(
            validation["valid"],
            "LANE_BUNDLE_INVALID",
            "The selected PV lane bundle failed validation.",
            status="FAIL",
            validation=validation,
        )
        bundle = json.loads((lanes_root / "manifest.json").read_text(encoding="utf-8"))
        lane = get_lane(lane_alias, code_mode=bundle["code_mode"])
        return package, lane, bundle

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
        package, lane, bundle = self._resolve(project_id, lane_alias, pv_ref)
        lane_root = package / "lanes" / lane.canonical_lane_id
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
        freshness = evaluate_freshness(self.store, project_id, package)
        return {
            "status": result_status("PASS", freshness),
            "project_id": project_id,
            "pv_ref": package.name,
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
            retrieval in {"hybrid", "bm25", "tfidf"},
            "LANE_RETRIEVAL_INVALID",
            "Lane retrieval must be hybrid, bm25, or tfidf.",
            status="BLOCKED",
        )
        package, lane, _ = self._resolve(project_id, lane_alias, pv_ref)
        lane_root = package / "lanes" / lane.canonical_lane_id
        database_path = lane_root / lane.sqlite_filename
        fts_query, terms = self._fts_query(query)
        connection = sqlite3.connect(
            f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        bm25_rows = []
        if retrieval in {"hybrid", "bm25"}:
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
        if retrieval == "bm25":
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
        freshness = evaluate_freshness(self.store, project_id, package)
        results = ordered[:limit]
        return {
            "status": result_status("PASS" if results else "EMPTY", freshness),
            "project_id": project_id,
            "pv_ref": package.name,
            "lane": lane.as_dict(),
            "query": query,
            "terms": terms,
            "retrieval": retrieval,
            "ranking": {
                "bm25": "SQLite FTS5 bm25; lower raw rank is better",
                "tfidf": "tf=count/tokens; idf=ln((1+N)/(1+df))+1",
                "hybrid": "reciprocal-rank fusion with k=60",
                "bm25_mislabeled_as_tfidf": False,
            },
            "results": results,
            "freshness": freshness,
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
        package, lane, _ = self._resolve(project_id, lane_alias, pv_ref)
        database_path = (
            package / "lanes" / lane.canonical_lane_id / lane.sqlite_filename
        )
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
        freshness = evaluate_freshness(self.store, project_id, package)
        return {
            "status": result_status("PASS", freshness),
            "project_id": project_id,
            "pv_ref": package.name,
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
