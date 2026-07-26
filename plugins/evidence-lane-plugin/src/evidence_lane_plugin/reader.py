"""Focused read/query tools over immutable accepted or candidate PVs."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote

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

    def _authority_context(
        self,
        project_id: str,
        package: Path,
        connection: Any,
    ) -> dict[str, Any]:
        pointer = self.store.pointer(project_id)
        repository = dict(
            connection.execute("SELECT * FROM repositories LIMIT 1").fetchone()
        )
        is_candidate = "_CANDIDATE__RUN_" in package.name
        if is_candidate:
            authority_state = "UNACCEPTED_CANDIDATE"
        elif package.name == pointer.accepted_pv:
            authority_state = "CURRENT_ACCEPTED_PV"
        else:
            authority_state = "HISTORICAL_ACCEPTED_PV"
        return {
            "authority_state": authority_state,
            "accepted_truth": not is_candidate,
            "current_accepted": authority_state == "CURRENT_ACCEPTED_PV",
            "pv_ref": package.name,
            "accepted_pv": pointer.accepted_pv,
            "accepted_pointer_generation": pointer.generation,
            "source_commit": repository["commit_sha"],
            "source_tree": repository["tree_sha"],
            "source_worktree_sha256": repository["worktree_sha256"],
            "repository_url": repository["repository_url"],
        }

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
            f"{repository_url}/blob/{context['source_commit']}/{quote(path, safe='/')}"
        )
        if start_line is not None:
            url += f"#L{start_line}"
            if end_line is not None and end_line != start_line:
                url += f"-L{end_line}"
        return url

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
            context = self._authority_context(project_id, package, connection)
            chunk_rows = connection.execute(
                """
                SELECT
                    chunks_fts.path AS path,
                    chunks_fts.chunk_id AS chunk_id,
                    snippet(chunks_fts, 1, '[', ']', ' … ', 24) AS snippet,
                    bm25(chunks_fts) AS rank,
                    c.start_line AS start_line,
                    c.end_line AS end_line,
                    c.sha256 AS chunk_sha256,
                    f.sha256 AS file_sha256
                FROM chunks_fts
                JOIN chunks c ON c.chunk_id = CAST(chunks_fts.chunk_id AS INTEGER)
                JOIN files f ON f.file_id = c.file_id
                WHERE chunks_fts MATCH ?
                ORDER BY rank, path, start_line
                LIMIT ?
                """,
                (self._fts_query(query), limit),
            ).fetchall()
            results = [
                {
                    "id": f"chunk:{row['chunk_id']}",
                    "ref_id": f"chunk:{row['chunk_id']}",
                    "title": (f"{row['path']} L{row['start_line']}-{row['end_line']}"),
                    "url": self._blob_url(
                        context,
                        row["path"],
                        row["start_line"],
                        row["end_line"],
                    ),
                    "path": row["path"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "snippet": row["snippet"],
                    "rank": row["rank"],
                    "metadata": {
                        "kind": "chunk",
                        "chunk_sha256": row["chunk_sha256"],
                        "file_sha256": row["file_sha256"],
                        "source_commit": context["source_commit"],
                    },
                }
                for row in chunk_rows
            ]
            remaining = limit - len(results)
            if remaining > 0:
                terms = re.findall(r"[\w.$/@:-]+", query, flags=re.UNICODE)
                needle = max(terms, key=len) if terms else query.strip()
                pattern = f"%{needle}%"
                symbol_rows = connection.execute(
                    """
                    SELECT s.symbol_id, s.kind, s.name, s.qualified_name,
                           s.start_line, s.end_line, s.signature,
                           f.path, f.sha256 AS file_sha256
                    FROM symbols s JOIN files f ON f.file_id = s.file_id
                    WHERE s.name LIKE ? OR s.qualified_name LIKE ?
                    ORDER BY s.name, f.path, s.start_line
                    LIMIT ?
                    """,
                    (pattern, pattern, remaining),
                ).fetchall()
                for row in symbol_rows:
                    results.append(
                        {
                            "id": f"symbol:{row['symbol_id']}",
                            "ref_id": f"symbol:{row['symbol_id']}",
                            "title": (
                                f"{row['kind']} {row['qualified_name']} - "
                                f"{row['path']}:{row['start_line']}"
                            ),
                            "url": self._blob_url(
                                context,
                                row["path"],
                                row["start_line"],
                                row["end_line"],
                            ),
                            "path": row["path"],
                            "start_line": row["start_line"],
                            "end_line": row["end_line"],
                            "snippet": row["signature"] or row["name"],
                            "metadata": {
                                "kind": "symbol",
                                "symbol_kind": row["kind"],
                                "file_sha256": row["file_sha256"],
                                "source_commit": context["source_commit"],
                            },
                        }
                    )
                remaining = limit - len(results)
            if remaining > 0:
                path_rows = connection.execute(
                    """
                    SELECT path, size_bytes, sha256, file_type, code_family
                    FROM files WHERE path LIKE ?
                    ORDER BY path LIMIT ?
                    """,
                    (f"%{query.strip()}%", remaining),
                ).fetchall()
                for row in path_rows:
                    results.append(
                        {
                            "id": f"file:{row['path']}",
                            "ref_id": f"file:{row['path']}",
                            "title": row["path"],
                            "url": self._blob_url(context, row["path"]),
                            "path": row["path"],
                            "snippet": (
                                f"{row['code_family']} {row['file_type']} - "
                                f"{row['size_bytes']} bytes"
                            ),
                            "metadata": {
                                "kind": "file",
                                "file_sha256": row["sha256"],
                                "source_commit": context["source_commit"],
                            },
                        }
                    )
        return {
            "status": "PASS" if results else "EMPTY",
            "project_id": project_id,
            **context,
            "query": query,
            "results": results,
            "warnings": (
                ["Explicit candidate query; results are not accepted project truth."]
                if context["authority_state"] == "UNACCEPTED_CANDIDATE"
                else []
            ),
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
        package = self.resolve(project_id, pv_ref)
        with database.connect(package / "code.sqlite", readonly=True) as connection:
            context = self._authority_context(project_id, package, connection)
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
                    SELECT f.path, f.sha256 AS file_sha256,
                           c.chunk_id, c.start_line, c.end_line,
                           c.text_content, c.sha256 AS chunk_sha256
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
                    **context,
                    "id": ref_id,
                    "ref_id": ref_id,
                    "title": (f"{row['path']} L{row['start_line']}-{row['end_line']}"),
                    "url": self._blob_url(
                        context,
                        row["path"],
                        row["start_line"],
                        row["end_line"],
                    ),
                    "path": row["path"],
                    "start_line": row["start_line"],
                    "end_line": row["end_line"],
                    "text": encoded[:max_bytes].decode("utf-8", errors="replace"),
                    "content": encoded[:max_bytes].decode("utf-8", errors="replace"),
                    "truncated": len(encoded) > max_bytes,
                    "sha256": row["chunk_sha256"],
                    "metadata": {
                        "kind": "chunk",
                        "chunk_sha256": row["chunk_sha256"],
                        "file_sha256": row["file_sha256"],
                        "source_commit": context["source_commit"],
                    },
                }
            if ref_id.startswith("symbol:"):
                raw_id = ref_id.removeprefix("symbol:")
                require(
                    raw_id.isdigit(),
                    "REF_ID_INVALID",
                    "Symbol references must contain a numeric ID.",
                    status="BLOCKED",
                )
                row = connection.execute(
                    """
                    SELECT s.symbol_id, s.kind, s.name, s.qualified_name,
                           s.start_line, s.end_line, s.signature,
                           f.path, f.sha256 AS file_sha256,
                           f.encoding, f.exact_bytes
                    FROM symbols s JOIN files f ON f.file_id = s.file_id
                    WHERE s.symbol_id = ?
                    """,
                    (int(raw_id),),
                ).fetchone()
                require(
                    row is not None,
                    "SYMBOL_NOT_FOUND",
                    "The requested symbol does not exist.",
                    status="EMPTY",
                )
                text = bytes(row["exact_bytes"]).decode(
                    row["encoding"] or "utf-8",
                    errors="replace",
                )
                lines = text.splitlines(keepends=True)
                bounded_end = min(
                    row["end_line"],
                    row["start_line"] + max_lines - 1,
                )
                symbol_text = "".join(lines[row["start_line"] - 1 : bounded_end])
                return {
                    "status": "PASS",
                    **context,
                    "id": ref_id,
                    "ref_id": ref_id,
                    "title": (
                        f"{row['kind']} {row['qualified_name']} - "
                        f"{row['path']}:{row['start_line']}"
                    ),
                    "url": self._blob_url(
                        context,
                        row["path"],
                        row["start_line"],
                        bounded_end,
                    ),
                    "path": row["path"],
                    "start_line": row["start_line"],
                    "end_line": bounded_end,
                    "text": symbol_text,
                    "content": symbol_text,
                    "truncated": bounded_end < row["end_line"],
                    "sha256": row["file_sha256"],
                    "metadata": {
                        "kind": "symbol",
                        "symbol_kind": row["kind"],
                        "name": row["name"],
                        "signature": row["signature"],
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
                    require(
                        start_line is None and end_line is None,
                        "BINARY_LINE_RANGE_UNSUPPORTED",
                        "Line ranges are not available for binary files.",
                        status="BLOCKED",
                    )
                    content = base64.b64encode(data[:max_bytes]).decode("ascii")
                    representation = "base64"
                    exact_start_line = None
                    exact_end_line = None
                    line_truncated = False
                else:
                    decoded = data.decode(encoding or "utf-8", errors="replace")
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
                    selected = (
                        "".join(lines[exact_start_line - 1 : exact_end_line])
                        if lines
                        else ""
                    )
                    encoded_selection = selected.encode(
                        encoding or "utf-8",
                        errors="replace",
                    )
                    content = encoded_selection[:max_bytes].decode(
                        encoding or "utf-8",
                        errors="replace",
                    )
                    representation = "text"
                    line_truncated = (
                        exact_start_line > 1
                        or exact_end_line < max(len(lines), 1)
                        or len(encoded_selection) > max_bytes
                    )
                return {
                    "status": "PASS",
                    **context,
                    "id": ref_id,
                    "ref_id": ref_id,
                    "title": row["path"],
                    "url": self._blob_url(
                        context,
                        row["path"],
                        exact_start_line,
                        exact_end_line,
                    ),
                    "path": row["path"],
                    "size_bytes": row["size_bytes"],
                    "sha256": row["sha256"],
                    "encoding": encoding,
                    "file_type": row["file_type"],
                    "code_family": row["code_family"],
                    "representation": representation,
                    "text": content,
                    "content": content,
                    "start_line": exact_start_line,
                    "end_line": exact_end_line,
                    "truncated": (
                        len(data) > max_bytes if row["is_binary"] else line_truncated
                    ),
                    "metadata": {
                        "kind": "file",
                        "file_sha256": row["sha256"],
                        "source_commit": context["source_commit"],
                    },
                }
        raise EvidenceLaneError(
            "REF_ID_UNSUPPORTED",
            "References must begin with file:, chunk:, or symbol:.",
            status="BLOCKED",
        )

    def project_summary(
        self, project_id: str, *, pv_ref: str | None = None
    ) -> dict[str, Any]:
        package = self.resolve(project_id, pv_ref)
        with database.connect(package / "code.sqlite", readonly=True) as connection:
            context = self._authority_context(project_id, package, connection)
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
            largest_files = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT path, size_bytes, line_count, file_type, code_family
                    FROM files WHERE is_binary = 0
                    ORDER BY size_bytes DESC, path LIMIT 10
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
            **context,
            "repository": repository,
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
            "imports": (
                "SELECT f.path, i.module, i.imported_name, i.alias, i.line_number, i.parser FROM imports i JOIN files f ON f.file_id=i.file_id WHERE (? IS NULL OR i.module LIKE '%' || ? || '%') ORDER BY f.path, i.line_number LIMIT ?",
                (value, value, limit),
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
        with database.connect(package / "code.sqlite", readonly=True) as connection:
            context = self._authority_context(project_id, package, connection)
        return {
            "status": "PASS" if rows else "EMPTY",
            "project_id": project_id,
            **context,
            "query_kind": query_kind,
            "rows": rows,
        }

    def diff(self, project_id: str, left_pv: str, right_pv: str) -> dict[str, Any]:
        left = self.resolve(project_id, left_pv)
        right = self.resolve(project_id, right_pv)

        def index(
            package: Path,
        ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
            with database.connect(package / "code.sqlite", readonly=True) as connection:
                files = {
                    row["path"]: {
                        "sha256": row["sha256"],
                        "size_bytes": row["size_bytes"],
                        "code_family": row["code_family"],
                    }
                    for row in connection.execute(
                        "SELECT path, sha256, size_bytes, code_family FROM files"
                    )
                }
                context = self._authority_context(project_id, package, connection)
            return files, context

        left_files, left_context = index(left)
        right_files, right_context = index(right)
        return {
            "status": "PASS",
            "project_id": project_id,
            "left_pv": left_pv,
            "right_pv": right_pv,
            "left_authority": left_context,
            "right_authority": right_context,
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
