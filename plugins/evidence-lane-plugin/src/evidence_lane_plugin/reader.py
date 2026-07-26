"""Focused read/query tools over immutable accepted or candidate PVs."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, cast

from . import database
from .errors import EvidenceLaneError, require
from .pv_package import validate_pv_package
from .store import ProjectStore


class PVReader:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def resolve(self, project_id: str, pv_ref: str | None = None) -> Path:
        pointer = self.store.pointer(project_id)
        reference = pv_ref or pointer.accepted_pv
        require(
            reference is not None,
            "NO_ACCEPTED_PV",
            "This project has no accepted project version.",
            status="EMPTY",
        )
        exact_reference = cast(str, reference)
        if "_CANDIDATE__RUN_" in exact_reference:
            path = self.store.candidate_path(project_id, exact_reference)
        else:
            path = self.store.accepted_path(project_id, exact_reference)
        validate_pv_package(path)
        return path

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = [
            term for term in re.findall(r"[\w.$/@:-]+", query, flags=re.UNICODE) if term
        ]
        require(
            bool(terms),
            "SEARCH_QUERY_EMPTY",
            "Search requires at least one alphanumeric term.",
            status="EMPTY",
        )
        return " AND ".join(
            f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms[:12]
        )

    def search(
        self,
        project_id: str,
        query: str,
        *,
        pv_ref: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        require(
            1 <= limit <= 100,
            "SEARCH_LIMIT_INVALID",
            "Search limit must be between 1 and 100.",
            status="BLOCKED",
        )
        package = self.resolve(project_id, pv_ref)
        with database.connect(package / "code.sqlite", readonly=True) as connection:
            rows = connection.execute(
                """
                SELECT
                    chunks_fts.path AS path,
                    chunks_fts.chunk_id AS chunk_id,
                    snippet(chunks_fts, 1, '[', ']', ' … ', 24) AS snippet,
                    bm25(chunks_fts) AS rank,
                    c.start_line AS start_line,
                    c.end_line AS end_line,
                    c.sha256 AS chunk_sha256
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = CAST(chunks_fts.chunk_id AS INTEGER)
                WHERE chunks_fts MATCH ?
                ORDER BY rank, path, start_line
                LIMIT ?
                """,
                (self._fts_query(query), limit),
            ).fetchall()
        return {
            "status": "PASS" if rows else "EMPTY",
            "project_id": project_id,
            "pv_ref": package.name,
            "query": query,
            "results": [
                {
                    "ref_id": f"chunk:{row['chunk_id']}",
                    "path": row["path"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "snippet": row["snippet"],
                    "rank": row["rank"],
                    "chunk_sha256": row["chunk_sha256"],
                }
                for row in rows
            ],
        }

    def fetch(
        self,
        project_id: str,
        ref_id: str,
        *,
        pv_ref: str | None = None,
        max_bytes: int = 256_000,
    ) -> dict[str, Any]:
        require(
            1 <= max_bytes <= 1_000_000,
            "FETCH_LIMIT_INVALID",
            "Fetch max_bytes must be between 1 and 1,000,000.",
            status="BLOCKED",
        )
        package = self.resolve(project_id, pv_ref)
        with database.connect(package / "code.sqlite", readonly=True) as connection:
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
                    SELECT f.path, c.chunk_id, c.start_line, c.end_line,
                           c.text_content, c.sha256
                    FROM chunks c JOIN files f ON f.file_id = c.file_id
                    WHERE c.chunk_id = ?
                    """,
                    (int(raw_id),),
                ).fetchone()
                require(
                    row is not None,
                    "CHUNK_NOT_FOUND",
                    "The requested chunk does not exist.",
                    status="EMPTY",
                )
                encoded = row["text_content"].encode("utf-8")
                return {
                    "status": "PASS",
                    "ref_id": ref_id,
                    "path": row["path"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "content": encoded[:max_bytes].decode("utf-8", errors="replace"),
                    "truncated": len(encoded) > max_bytes,
                    "sha256": row["sha256"],
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
                    SELECT path, size_bytes, sha256, encoding, is_binary,
                           exact_bytes, file_type, code_family
                    FROM files WHERE path = ?
                    """,
                    (path,),
                ).fetchone()
                require(
                    row is not None,
                    "FILE_NOT_FOUND",
                    "The requested source file does not exist in this PV.",
                    status="EMPTY",
                )
                data = bytes(row["exact_bytes"])
                content: str
                encoding = row["encoding"]
                if row["is_binary"]:
                    content = base64.b64encode(data[:max_bytes]).decode("ascii")
                    representation = "base64"
                else:
                    content = data[:max_bytes].decode(
                        encoding or "utf-8", errors="replace"
                    )
                    representation = "text"
                return {
                    "status": "PASS",
                    "ref_id": ref_id,
                    "path": row["path"],
                    "size_bytes": row["size_bytes"],
                    "sha256": row["sha256"],
                    "encoding": encoding,
                    "file_type": row["file_type"],
                    "code_family": row["code_family"],
                    "representation": representation,
                    "content": content,
                    "truncated": len(data) > max_bytes,
                }
        raise EvidenceLaneError(
            "REF_ID_UNSUPPORTED",
            "References must begin with file: or chunk:.",
            status="BLOCKED",
        )

    def project_summary(
        self, project_id: str, *, pv_ref: str | None = None
    ) -> dict[str, Any]:
        package = self.resolve(project_id, pv_ref)
        with database.connect(package / "code.sqlite", readonly=True) as connection:
            repository = dict(
                connection.execute("SELECT * FROM repositories LIMIT 1").fetchone()
            )
            families = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT code_family, COUNT(*) AS files, SUM(size_bytes) AS bytes
                    FROM files GROUP BY code_family ORDER BY files DESC, code_family
                    """
                )
            ]
            counts = database.count_tables(
                connection,
                (
                    "files",
                    "chunks",
                    "symbols",
                    "imports",
                    "dependencies",
                    "routes",
                ),
            )
        repository["submodules_json"] = json.loads(repository["submodules_json"])
        return {
            "status": "PASS",
            "project_id": project_id,
            "pv_ref": package.name,
            "repository": repository,
            "counts": counts,
            "families": families,
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
        package = self.resolve(project_id, pv_ref)
        queries = {
            "files": (
                "SELECT path, size_bytes, sha256, encoding, is_binary, file_type, code_family, line_count, ingestion_status FROM files ORDER BY path LIMIT ?",
                (limit,),
            ),
            "symbols": (
                "SELECT f.path, s.kind, s.name, s.qualified_name, s.start_line, s.end_line, s.signature, s.parser FROM symbols s JOIN files f ON f.file_id=s.file_id WHERE (? IS NULL OR s.name LIKE '%' || ? || '%') ORDER BY f.path, s.start_line LIMIT ?",
                (value, value, limit),
            ),
            "dependencies": (
                "SELECT ecosystem, name, constraint_text, dependency_group, source_path FROM dependencies WHERE (? IS NULL OR name LIKE '%' || ? || '%') ORDER BY ecosystem, name LIMIT ?",
                (value, value, limit),
            ),
            "routes": (
                "SELECT f.path, r.route_kind, r.method, r.path_pattern, r.handler, r.line_number, r.parser FROM routes r JOIN files f ON f.file_id=r.file_id ORDER BY r.path_pattern LIMIT ?",
                (limit,),
            ),
            "receipts": (
                "SELECT receipt_id, phase, status, started_at, completed_at, details_json FROM builder_receipts ORDER BY started_at LIMIT ?",
                (limit,),
            ),
        }
        require(
            query_kind in queries,
            "QUERY_KIND_UNSUPPORTED",
            "Use one of the focused allowlisted PV query kinds.",
            status="BLOCKED",
            supported=sorted(queries),
        )
        require(
            1 <= limit <= 500,
            "QUERY_LIMIT_INVALID",
            "Query limit must be between 1 and 500.",
            status="BLOCKED",
        )
        sql, parameters = queries[query_kind]
        rows = database.bounded_select(
            package / "code.sqlite", sql, parameters, limit=limit
        )
        return {
            "status": "PASS" if rows else "EMPTY",
            "project_id": project_id,
            "pv_ref": package.name,
            "query_kind": query_kind,
            "rows": rows,
        }

    def diff(self, project_id: str, left_pv: str, right_pv: str) -> dict[str, Any]:
        left = self.resolve(project_id, left_pv)
        right = self.resolve(project_id, right_pv)

        def index(package: Path) -> dict[str, dict[str, Any]]:
            with database.connect(package / "code.sqlite", readonly=True) as connection:
                return {
                    row["path"]: {
                        "sha256": row["sha256"],
                        "size_bytes": row["size_bytes"],
                        "code_family": row["code_family"],
                    }
                    for row in connection.execute(
                        "SELECT path, sha256, size_bytes, code_family FROM files"
                    )
                }

        left_files = index(left)
        right_files = index(right)
        return {
            "status": "PASS",
            "project_id": project_id,
            "left_pv": left_pv,
            "right_pv": right_pv,
            "added": [
                {"path": path, **right_files[path]}
                for path in sorted(right_files.keys() - left_files.keys())
            ],
            "deleted": [
                {"path": path, **left_files[path]}
                for path in sorted(left_files.keys() - right_files.keys())
            ],
            "modified": [
                {
                    "path": path,
                    "before": left_files[path],
                    "after": right_files[path],
                }
                for path in sorted(left_files.keys() & right_files.keys())
                if left_files[path]["sha256"] != right_files[path]["sha256"]
            ],
        }
