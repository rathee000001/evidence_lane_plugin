"""Bounded read tools over the live project-root authority."""

from __future__ import annotations

import base64
import json
import re
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .errors import EvidenceLaneError, require
from .freshness import evaluate_working_lane_freshness, result_status
from .git_adapter import inspect_repository
from .hashing import canonical_json_bytes, sha256_bytes
from .lanes import LANE_REGISTRY
from .store import ProjectStore


class PVReader:
    """Compatibility class whose ordinary reads are live-root only."""

    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def _live_local_code_authority(
        self, project_id: str, pv_ref: str | None = None
    ) -> tuple[Path, dict[str, Any]]:
        root = self.store.project_root(project_id)
        pointer = self.store.pointer(project_id)
        exact_ref = str(pv_ref or "").strip()
        require(
            not exact_ref or exact_ref == "LIVE_ROOT",
            "PV_ARCHIVE_QUERY_OBSOLETE",
            "Ordinary reads query the live project root; accepted ZIPs and candidates are HIL-only.",
            status="BLOCKED",
            requested_ref=exact_ref or None,
        )
        sectors_root = root / "sectors"
        manifest_path = sectors_root / "manifest.json"
        require(
            manifest_path.is_file(),
            "LIVE_ROOT_SECTORS_REQUIRED",
            "The live project root has no materialized sector authority.",
            status="MISMATCH",
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        lane = LANE_REGISTRY["local_code"]
        database_path = sectors_root / "local_code" / lane.sqlite_filename
        require(
            database_path.is_file(),
            "LIVE_ROOT_LOCAL_CODE_DATABASE_MISMATCH",
            "Live-root Local Code requires its one canonical SQLite authority.",
            status="MISMATCH",
        )
        repository = inspect_repository(self.store.config(project_id).repository_path)
        freshness = evaluate_working_lane_freshness(
            self.store,
            project_id,
            sectors_root,
            bounded_dirty_read=True,
        )
        context = {
            "authority_state": "LIVE_PROJECT_ROOT",
            "accepted_truth": False,
            "live_working_truth": True,
            "current_accepted": False,
            "pv_ref": "LIVE_ROOT",
            "accepted_pv": pointer.accepted_pv,
            "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
            "accepted_pointer_generation": pointer.generation,
            "source_commit": repository.commit_sha,
            "source_tree": repository.tree_sha,
            "repository_url": repository.repository_url,
            "freshness": freshness,
            "live_truth_status": freshness["state"],
            "retrieval_authority": "LIVE_ROOT_SECTORS_LOCAL_CODE",
            "live_source_used": True,
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "accepted_pointer_used_as_baseline_only": True,
            "browser_history_used": False,
            "scrollback_used": False,
            "transcript_used": False,
            "sector_bundle_sha256": manifest.get("bundle_sha256"),
        }
        return database_path, context

    @staticmethod
    def _live_connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _blob_url(
        context: dict[str, Any],
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
    ) -> str:
        repository_url = str(context["repository_url"]).removesuffix(".git")
        if not repository_url.startswith(("https://", "http://")):
            return ""
        url = (
            f"{repository_url}/blob/{context['source_commit']}/"
            f"{quote(path, safe='/')}"
        )
        if start_line is not None:
            url += f"#L{start_line}"
            if end_line is not None and end_line != start_line:
                url += f"-L{end_line}"
        return url

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = [
            term
            for term in re.findall(r"[\w.$/@:-]+", query, flags=re.UNICODE)
            if term
        ]
        require(
            bool(terms),
            "SEARCH_QUERY_EMPTY",
            "Search requires at least one alphanumeric term.",
            status="EMPTY",
        )
        return " OR ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:12]
        )

    def search(
        self,
        project_id: str,
        query: str,
        *,
        pv_ref: str | None = None,
        limit: int = 20,
        candidate_overlay_ref: str | None = None,
        candidate_overlay_authorization: str | None = None,
    ) -> dict[str, Any]:
        require(
            candidate_overlay_ref is None and candidate_overlay_authorization is None,
            "CANDIDATE_QUERY_REQUIRES_HIL_SURFACE",
            "Candidate overlays are presented only by the governed HIL renderer.",
            status="BLOCKED",
        )
        require(
            1 <= limit <= 100,
            "SEARCH_LIMIT_INVALID",
            "Search limit must be between 1 and 100.",
            status="BLOCKED",
        )
        database_path, context = self._live_local_code_authority(project_id, pv_ref)
        lane = LANE_REGISTRY["local_code"]
        with self._live_connect(database_path) as connection:
            rows = connection.execute(
                (
                    "SELECT f.path,f.chunk_id,f.locator,"
                    f"snippet({lane.fts_table},2,'[',']',' ... ',24) AS snippet,"
                    f"bm25({lane.fts_table}) AS rank,c.sha256 AS chunk_sha256,"
                    "s.sha256 AS file_sha256 "
                    f"FROM {lane.fts_table} AS f "
                    "JOIN chunk_index AS c ON c.chunk_id=CAST(f.chunk_id AS INTEGER) "
                    "JOIN source_registry AS s ON s.source_id=c.source_id "
                    f"WHERE {lane.fts_table} MATCH ? "
                    "ORDER BY rank,f.path,f.locator LIMIT ?"
                ),
                (self._fts_query(query), limit),
            ).fetchall()
        results = [
            {
                "id": f"chunk:{row['chunk_id']}",
                "ref_id": f"chunk:{row['chunk_id']}",
                "title": f"{row['path']} {row['locator']}",
                "url": self._blob_url(context, str(row["path"])),
                "path": row["path"],
                "locator": row["locator"],
                "snippet": row["snippet"],
                "rank": row["rank"],
                "metadata": {
                    "kind": "chunk",
                    "chunk_sha256": row["chunk_sha256"],
                    "file_sha256": row["file_sha256"],
                    "source_commit": context["source_commit"],
                },
                "provenance": {
                    "authority": "LIVE_ROOT_LOCAL_CODE",
                    "source_locator": (
                        f"sector://local_code/{row['path']}#{row['locator']}"
                    ),
                    "source_commit": context["source_commit"],
                    "accepted_archive_opened": False,
                },
            }
            for row in rows
        ]
        return {
            "status": result_status("PASS", context["freshness"]),
            "result_state": "HITS" if results else "EMPTY",
            "project_id": project_id,
            **context,
            "query": query,
            "results": results,
            "result_count": len(results),
            "bounded_result_limit": limit,
            "candidate_overlay_used": False,
            "no_hit_is_valid": not results,
            "private_reasoning_stored": False,
            "warnings": [],
        }

    def fetch(
        self,
        project_id: str,
        ref_id: str,
        *,
        pv_ref: str | None = None,
        max_bytes: int = 256_000,
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int = 400,
    ) -> dict[str, Any]:
        require(
            1 <= max_bytes <= 1_000_000,
            "FETCH_LIMIT_INVALID",
            "Fetch max_bytes must be between 1 and 1,000,000.",
            status="BLOCKED",
        )
        require(
            1 <= max_lines <= 1_000,
            "FETCH_LINE_LIMIT_INVALID",
            "Fetch max_lines must be between 1 and 1,000.",
            status="BLOCKED",
        )
        database_path, context = self._live_local_code_authority(project_id, pv_ref)
        with self._live_connect(database_path) as connection:
            if ref_id.startswith("chunk:"):
                raw_id = ref_id.removeprefix("chunk:")
                require(
                    raw_id.isdigit(),
                    "REF_ID_INVALID",
                    "Chunk references must contain a numeric ID.",
                    status="BLOCKED",
                )
                row = connection.execute(
                    """
                    SELECT s.path,s.sha256 AS file_sha256,c.chunk_id,c.locator,
                           c.text_content,c.sha256 AS chunk_sha256
                    FROM chunk_index AS c
                    JOIN source_registry AS s ON s.source_id=c.source_id
                    WHERE c.chunk_id=?
                    """,
                    (int(raw_id),),
                ).fetchone()
                require(
                    row is not None,
                    "CHUNK_NOT_FOUND",
                    "The requested live-root chunk does not exist.",
                    status="EMPTY",
                )
                encoded = str(row["text_content"]).encode("utf-8")
                content = encoded[:max_bytes].decode("utf-8", errors="replace")
                return {
                    "status": result_status("PASS", context["freshness"]),
                    **context,
                    "id": ref_id,
                    "ref_id": ref_id,
                    "title": f"{row['path']} {row['locator']}",
                    "url": self._blob_url(context, str(row["path"])),
                    "path": row["path"],
                    "locator": row["locator"],
                    "text": content,
                    "content": content,
                    "truncated": len(encoded) > max_bytes,
                    "sha256": row["chunk_sha256"],
                    "metadata": {
                        "kind": "chunk",
                        "chunk_sha256": row["chunk_sha256"],
                        "file_sha256": row["file_sha256"],
                        "source_commit": context["source_commit"],
                    },
                }
            if ref_id.startswith("file:"):
                path = ref_id.removeprefix("file:").replace("\\", "/")
                require(
                    bool(path)
                    and not path.startswith(("/", "../"))
                    and "/../" not in f"/{path}/",
                    "FILE_REF_INVALID",
                    "File references must be repository-relative paths.",
                    status="BLOCKED",
                )
                row = connection.execute(
                    """
                    SELECT path,size_bytes,sha256,mime_type,extension,encoding,
                           parser_state,exact_bytes
                    FROM source_registry WHERE path=?
                    """,
                    (path,),
                ).fetchone()
                require(
                    row is not None,
                    "FILE_NOT_FOUND",
                    "The requested source file does not exist in live-root Local Code.",
                    status="EMPTY",
                )
                data = bytes(row["exact_bytes"])
                encoding = row["encoding"]
                if encoding:
                    decoded = data.decode(str(encoding), errors="replace")
                    lines = decoded.splitlines(keepends=True)
                    exact_start_line = start_line or 1
                    requested_end = end_line or max(len(lines), 1)
                    require(
                        exact_start_line >= 1 and requested_end >= exact_start_line,
                        "FETCH_LINE_RANGE_INVALID",
                        "The requested line range is invalid.",
                        status="BLOCKED",
                    )
                    exact_end_line = min(
                        requested_end,
                        max(len(lines), 1),
                        exact_start_line + max_lines - 1,
                    )
                    selected = "".join(lines[exact_start_line - 1 : exact_end_line])
                    encoded_selection = selected.encode(str(encoding), errors="replace")
                    content = encoded_selection[:max_bytes].decode(
                        str(encoding), errors="replace"
                    )
                    representation = "text"
                    truncated = (
                        exact_start_line > 1
                        or exact_end_line < max(len(lines), 1)
                        or len(encoded_selection) > max_bytes
                    )
                else:
                    require(
                        start_line is None and end_line is None,
                        "BINARY_LINE_RANGE_UNSUPPORTED",
                        "Line ranges are unavailable for binary files.",
                        status="BLOCKED",
                    )
                    content = base64.b64encode(data[:max_bytes]).decode("ascii")
                    representation = "base64"
                    exact_start_line = None
                    exact_end_line = None
                    truncated = len(data) > max_bytes
                return {
                    "status": result_status("PASS", context["freshness"]),
                    **context,
                    "id": ref_id,
                    "ref_id": ref_id,
                    "title": row["path"],
                    "url": self._blob_url(
                        context,
                        str(row["path"]),
                        exact_start_line,
                        exact_end_line,
                    ),
                    "path": row["path"],
                    "size_bytes": row["size_bytes"],
                    "sha256": row["sha256"],
                    "encoding": encoding,
                    "mime_type": row["mime_type"],
                    "extension": row["extension"],
                    "parser_state": row["parser_state"],
                    "representation": representation,
                    "text": content,
                    "content": content,
                    "start_line": exact_start_line,
                    "end_line": exact_end_line,
                    "truncated": truncated,
                    "metadata": {
                        "kind": "file",
                        "file_sha256": row["sha256"],
                        "source_commit": context["source_commit"],
                    },
                }
        raise EvidenceLaneError(
            "REF_ID_UNSUPPORTED",
            "Live-root references must begin with file: or chunk:.",
            status="BLOCKED",
        )

    def project_summary(
        self, project_id: str, *, pv_ref: str | None = None
    ) -> dict[str, Any]:
        database_path, context = self._live_local_code_authority(project_id, pv_ref)
        with self._live_connect(database_path) as connection:
            count_tables = (
                "source_registry",
                "chunk_index",
                "code_symbol",
                "code_import",
                "code_dependency",
                "code_route",
            )
            counts = {
                table: int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
                for table in count_tables
            }
            families = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT COALESCE(NULLIF(extension,''),'[none]') AS code_family,
                           COUNT(*) AS files,SUM(size_bytes) AS bytes
                    FROM source_registry GROUP BY code_family
                    ORDER BY files DESC,code_family
                    """
                ).fetchall()
            ]
            largest_files = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT path,size_bytes,mime_type,extension,parser_state
                    FROM source_registry ORDER BY size_bytes DESC,path LIMIT 10
                    """
                ).fetchall()
            ]
        return {
            "status": result_status("PASS", context["freshness"]),
            "project_id": project_id,
            **context,
            "repository": {
                "repository_url": context["repository_url"],
                "commit_sha": context["source_commit"],
                "tree_sha": context["source_tree"],
            },
            "counts": counts,
            "families": families,
            "largest_files": largest_files,
        }

    def query(
        self,
        project_id: str,
        query_kind: str,
        *,
        pv_ref: str | None = None,
        value: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        database_path, context = self._live_local_code_authority(project_id, pv_ref)
        require(
            1 <= limit <= 500,
            "QUERY_LIMIT_INVALID",
            "Query limit must be between 1 and 500.",
            status="BLOCKED",
        )
        queries = {
            "files": (
                "SELECT path,size_bytes,sha256,encoding,mime_type,extension,parser_state,registered_at FROM source_registry ORDER BY path LIMIT ?",
                (limit,),
            ),
            "symbols": (
                "SELECT s.path,r.locator,r.payload_json FROM code_symbol r JOIN source_registry s ON s.source_id=r.source_id WHERE (? IS NULL OR r.payload_json LIKE '%' || ? || '%') ORDER BY s.path,r.locator LIMIT ?",
                (value, value, limit),
            ),
            "dependencies": (
                "SELECT s.path,r.locator,r.payload_json FROM code_dependency r JOIN source_registry s ON s.source_id=r.source_id WHERE (? IS NULL OR r.payload_json LIKE '%' || ? || '%') ORDER BY s.path,r.locator LIMIT ?",
                (value, value, limit),
            ),
            "routes": (
                "SELECT s.path,r.locator,r.payload_json FROM code_route r JOIN source_registry s ON s.source_id=r.source_id WHERE (? IS NULL OR r.payload_json LIKE '%' || ? || '%') ORDER BY s.path,r.locator LIMIT ?",
                (value, value, limit),
            ),
            "imports": (
                "SELECT s.path,r.locator,r.payload_json FROM code_import r JOIN source_registry s ON s.source_id=r.source_id WHERE (? IS NULL OR r.payload_json LIKE '%' || ? || '%') ORDER BY s.path,r.locator LIMIT ?",
                (value, value, limit),
            ),
            "receipts": (
                "SELECT receipt_id,build_mode,parent_pv,proposed_pv,unchanged_reuse,changed_rebuild,new_register,removed_tombstone,blocked_unsupported,length(details_json) AS details_size_bytes,recorded_at FROM refresh_receipt ORDER BY recorded_at DESC LIMIT ?",
                (limit,),
            ),
        }
        require(
            query_kind in queries,
            "QUERY_KIND_UNSUPPORTED",
            "Use one focused allowlisted live-root query kind.",
            status="BLOCKED",
            supported=sorted(queries),
        )
        sql, parameters = queries[query_kind]
        with self._live_connect(database_path) as connection:
            rows = [dict(row) for row in connection.execute(sql, parameters).fetchall()]
        for row in rows:
            if isinstance(row.get("payload_json"), str):
                row["payload"] = json.loads(row.pop("payload_json"))
        return {
            "status": result_status("PASS", context["freshness"]),
            "result_state": "HITS" if rows else "EMPTY",
            "project_id": project_id,
            **context,
            "query_kind": query_kind,
            "rows": rows,
            "result_count": len(rows),
            "bounded_result_limit": limit,
        }

    def diff(self, project_id: str, left_pv: str, right_pv: str) -> dict[str, Any]:
        root = self.store.project_root(project_id)
        overlay_path = root / "project_overlay" / "project_overlay.sqlite"
        require(
            overlay_path.is_file(),
            "PROJECT_OVERLAY_REQUIRED",
            "PV change comparison requires the live root Project Overlay authority.",
            status="EMPTY",
        )
        with self._live_connect(overlay_path) as connection:
            metadata = {
                str(row["key"]): str(row["value"])
                for row in connection.execute(
                    "SELECT key,value FROM overlay_meta ORDER BY key"
                ).fetchall()
            }
            progressive = bool(
                connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='pv_hil_transition'
                    """
                ).fetchone()
            )
            if progressive:
                baseline = connection.execute(
                    "SELECT * FROM overlay_history_baseline WHERE baseline_id=1"
                ).fetchone()

                def snapshot_for(pv_id: str) -> tuple[str, dict[str, dict[str, Any]]]:
                    if baseline is not None and str(baseline["baseline_pv"]) == pv_id:
                        rows = connection.execute(
                            """
                            SELECT sector_id,lane_database_sha256,source_count,
                                   chunk_count,fact_count
                            FROM sector_hil_baseline_snapshot ORDER BY sector_id
                            """
                        ).fetchall()
                        source = "LIVE_ROOT_MIGRATION_BASELINE"
                    else:
                        transition = connection.execute(
                            """
                            SELECT transition_id FROM pv_hil_transition
                            WHERE proposed_pv=? ORDER BY sequence DESC LIMIT 1
                            """,
                            (pv_id,),
                        ).fetchone()
                        require(
                            transition is not None,
                            "PROJECT_OVERLAY_PV_NOT_IN_LIVE_HISTORY",
                            "The requested PV is not present in the live-root Project Overlay ledger.",
                            status="EMPTY",
                            requested_pv=pv_id,
                            accepted_archive_opened=False,
                            accepted_archive_queried=False,
                        )
                        rows = connection.execute(
                            """
                            SELECT sector_id,lane_database_sha256,source_count,
                                   chunk_count,fact_count
                            FROM sector_hil_snapshot
                            WHERE transition_id=? ORDER BY ordinal
                            """,
                            (str(transition["transition_id"]),),
                        ).fetchall()
                        source = str(transition["transition_id"])
                    return source, {
                        str(row["sector_id"]): {
                            "lane_database_sha256": row["lane_database_sha256"],
                            "source_count": int(row["source_count"]),
                            "chunk_count": int(row["chunk_count"]),
                            "fact_count": int(row["fact_count"]),
                        }
                        for row in rows
                    }

                left_source, left_snapshot = snapshot_for(left_pv)
                right_source, right_snapshot = snapshot_for(right_pv)
                overlay_delta_value: dict[str, Any] = {
                    "schema": "evidence-lane.project-overlay-delta.v1",
                    "added": [
                        {"sector_id": sector_id, "after": right_snapshot[sector_id]}
                        for sector_id in sorted(
                            right_snapshot.keys() - left_snapshot.keys()
                        )
                    ],
                    "modified": [
                        {
                            "sector_id": sector_id,
                            "before": left_snapshot[sector_id],
                            "after": right_snapshot[sector_id],
                        }
                        for sector_id in sorted(
                            left_snapshot.keys() & right_snapshot.keys()
                        )
                        if left_snapshot[sector_id] != right_snapshot[sector_id]
                    ],
                    "removed": [
                        {"sector_id": sector_id, "before": left_snapshot[sector_id]}
                        for sector_id in sorted(
                            left_snapshot.keys() - right_snapshot.keys()
                        )
                    ],
                    "unchanged": sorted(
                        sector_id
                        for sector_id in left_snapshot.keys() & right_snapshot.keys()
                        if left_snapshot[sector_id] == right_snapshot[sector_id]
                    ),
                }
                overlay_delta_value["delta_sha256"] = sha256_bytes(
                    canonical_json_bytes(overlay_delta_value)
                )
                overlay_delta: dict[str, Any] | None = overlay_delta_value
                sector_snapshots = [
                    {"sector_id": sector_id, **right_snapshot[sector_id]}
                    for sector_id in sorted(right_snapshot)
                ]
                fusion_receipts = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT * FROM hil_transition_receipt
                        ORDER BY rowid
                        """
                    ).fetchall()
                ]
                transition_rows = [
                    dict(row)
                    for row in connection.execute(
                        """
                        SELECT transition_id,sequence,parent_pv,proposed_pv,
                               pointer_generation,created_at,truth_state,
                               prior_transition_sha256,transition_sha256
                        FROM pv_hil_transition ORDER BY sequence
                        """
                    ).fetchall()
                ]
            else:
                left_source = metadata.get("proposed_pv") or "LEGACY_SNAPSHOT"
                right_source = left_source
                sector_snapshots = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM sector_candidate_snapshot ORDER BY sector_id"
                    ).fetchall()
                ]
                fusion_receipts = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT * FROM fusion_receipt ORDER BY receipt_id"
                    ).fetchall()
                ]
                overlay_delta = None
                transition_rows = []
        pointer = self.store.pointer(project_id)
        return {
            "status": "PASS",
            "project_id": project_id,
            "left_pv": left_pv,
            "right_pv": right_pv,
            "comparison_authority": "LIVE_ROOT_PROJECT_OVERLAY",
            "overlay_metadata": metadata,
            "progressive_history": progressive,
            "left_snapshot_authority": left_source,
            "right_snapshot_authority": right_source,
            "sector_snapshots": sector_snapshots,
            "project_overlay_delta": overlay_delta,
            "pv_hil_transitions": transition_rows,
            "fusion_receipts": fusion_receipts,
            "accepted_pointer_baseline": pointer.as_dict(),
            "accepted_archive_opened": False,
            "accepted_archive_queried": False,
            "pointer_moved": False,
            "candidate_created": False,
        }
