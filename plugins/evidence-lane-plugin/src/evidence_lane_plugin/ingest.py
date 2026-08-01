"""Whole-source deterministic ingestion for the first Git/code lane."""

from __future__ import annotations

import ast
import json
import mimetypes
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from . import database
from .constants import (
    DEFAULT_CHUNK_LINES,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_MAX_FILE_BYTES,
)
from .errors import EvidenceLaneError, require
from .hashing import sha256_bytes
from .timeutil import utc_now

_EXCLUDED_PARTS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "coverage",
    ".next",
    ".turbo",
    ".cache",
}

_CODE_FAMILIES = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".svelte": "svelte",
    ".vue": "vue",
    ".astro": "astro",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".json": "json",
    ".jsonl": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".xml": "xml",
    ".md": "markdown",
    ".mmd": "mermaid",
    ".proto": "protobuf",
}

_JS_SYMBOL_RE = re.compile(
    r"(?m)^[ \t]*(?:(?:export|default|async|public|private|protected|static)\s+)*"
    r"(?:(class)\s+([A-Za-z_$][\w$]*)|"
    r"(?:function)\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)|"
    r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>)"
)
_JS_IMPORT_RE = re.compile(
    r"(?m)^[ \t]*(?:import[\s\S]*?\sfrom\s+|import\s*\(|require\s*\()\s*['\"]([^'\"]+)['\"]"
)
_ROUTE_RE = re.compile(
    r"(?m)(?:app|router)\.(get|post|put|patch|delete|options|head|use)\s*\(\s*['\"]([^'\"]+)['\"]"
)
_PY_ROUTE_RE = re.compile(
    r"(?m)^[ \t]*@(?:app|router|blueprint)\.(get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)['\"]"
)


@dataclass(slots=True)
class IngestionReport:
    files: int = 0
    text_files: int = 0
    binary_files: int = 0
    chunks: int = 0
    chunk_cas_created: int = 0
    chunk_cas_reused: int = 0
    changed_sections_reused: int = 0
    changed_sections_reindexed: int = 0
    symbols: int = 0
    imports: int = 0
    dependencies: int = 0
    routes: int = 0
    bytes: int = 0
    families: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    refresh: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "text_files": self.text_files,
            "binary_files": self.binary_files,
            "chunks": self.chunks,
            "chunk_cas_created": self.chunk_cas_created,
            "chunk_cas_reused": self.chunk_cas_reused,
            "changed_sections_reused": self.changed_sections_reused,
            "changed_sections_reindexed": self.changed_sections_reindexed,
            "symbols": self.symbols,
            "imports": self.imports,
            "dependencies": self.dependencies,
            "routes": self.routes,
            "bytes": self.bytes,
            "families": dict(sorted(self.families.items())),
            "warnings": self.warnings,
            "refresh": self.refresh,
        }


def iter_source_files(root: str | Path) -> Iterable[tuple[str, Path]]:
    base = Path(root).resolve()
    for target in sorted(base.rglob("*"), key=lambda path: path.as_posix().lower()):
        if not target.is_file() or target.is_symlink():
            continue
        relative = target.relative_to(base)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        yield relative.as_posix(), target


def _decode(data: bytes) -> tuple[str | None, str | None]:
    if not data:
        return "", "utf-8"
    if b"\x00" in data[:8192]:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                text = data.decode(encoding)
                printable = sum(
                    character.isprintable() or character.isspace() for character in text
                )
                if text and printable / len(text) > 0.85:
                    return text, encoding
            except UnicodeError:
                pass
        return None, None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    return None, None


def _file_type(path: str) -> tuple[str, str]:
    suffix = Path(path).suffix.lower()
    family = _CODE_FAMILIES.get(suffix, "text-or-binary")
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    return mime, family


def _line_chunks(
    text: str,
    *,
    lines_per_chunk: int,
    overlap: int,
) -> Iterable[tuple[int, int, int, str]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return
    start = 0
    ordinal = 0
    step = lines_per_chunk - overlap
    while start < len(lines):
        end = min(len(lines), start + lines_per_chunk)
        yield ordinal, start + 1, end, "".join(lines[start:end])
        if end == len(lines):
            break
        start += step
        ordinal += 1


def _python_facts(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return symbols, imports

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []

        def _symbol(
            self, node: ast.AST, kind: str, name: str, signature: str | None
        ) -> None:
            qualified = ".".join([*self.scope, name])
            symbols.append(
                {
                    "kind": kind,
                    "name": name,
                    "qualified_name": qualified,
                    "start_line": getattr(node, "lineno", 1),
                    "end_line": getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                    "signature": signature,
                    "parser": "python-ast",
                }
            )

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._symbol(node, "class", node.name, None)
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
            args = [argument.arg for argument in node.args.args]
            self._symbol(
                node,
                "async_function"
                if isinstance(node, ast.AsyncFunctionDef)
                else "function",
                node.name,
                f"{node.name}({', '.join(args)})",
            )
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        visit_FunctionDef = _visit_function
        visit_AsyncFunctionDef = _visit_function

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                imports.append(
                    {
                        "module": alias.name,
                        "imported_name": None,
                        "alias": alias.asname,
                        "line_number": node.lineno,
                        "parser": "python-ast",
                    }
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = "." * node.level + (node.module or "")
            for alias in node.names:
                imports.append(
                    {
                        "module": module,
                        "imported_name": alias.name,
                        "alias": alias.asname,
                        "line_number": node.lineno,
                        "parser": "python-ast",
                    }
                )

    Visitor().visit(tree)
    return symbols, imports


def _script_facts(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    symbols = []
    for match in _JS_SYMBOL_RE.finditer(text):
        line = text.count("\n", 0, match.start()) + 1
        if match.group(1):
            kind, name, arguments = "class", match.group(2), None
        elif match.group(3):
            kind, name, arguments = "function", match.group(3), match.group(4)
        else:
            kind, name, arguments = "function", match.group(5), match.group(6)
        symbols.append(
            {
                "kind": kind,
                "name": name,
                "qualified_name": name,
                "start_line": line,
                "end_line": line,
                "signature": f"{name}({arguments or ''})"
                if arguments is not None
                else None,
                "parser": "script-regex",
            }
        )
    imports = [
        {
            "module": match.group(1),
            "imported_name": None,
            "alias": None,
            "line_number": text.count("\n", 0, match.start()) + 1,
            "parser": "script-regex",
        }
        for match in _JS_IMPORT_RE.finditer(text)
    ]
    return symbols, imports


def _route_facts(text: str, family: str) -> list[dict[str, Any]]:
    pattern = _PY_ROUTE_RE if family == "python" else _ROUTE_RE
    return [
        {
            "route_kind": "http",
            "method": match.group(1).upper(),
            "path_pattern": match.group(2),
            "handler": None,
            "line_number": text.count("\n", 0, match.start()) + 1,
            "parser": "python-route-regex"
            if family == "python"
            else "script-route-regex",
        }
        for match in pattern.finditer(text)
    ]


def _dependency_facts(path: str, text: str) -> list[dict[str, str | None]]:
    name = Path(path).name.lower()
    dependencies: list[dict[str, str | None]] = []
    if name == "package.json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return dependencies
        for group in (
            "dependencies",
            "devDependencies",
            "peerDependencies",
            "optionalDependencies",
        ):
            values = payload.get(group, {})
            if isinstance(values, dict):
                for dependency, constraint in sorted(values.items()):
                    dependencies.append(
                        {
                            "ecosystem": "npm",
                            "name": str(dependency),
                            "constraint_text": str(constraint),
                            "dependency_group": group,
                            "source_path": path,
                        }
                    )
    elif name.startswith("requirements") and name.endswith((".txt", ".in")):
        for line in text.splitlines():
            value = line.strip()
            if not value or value.startswith(("#", "-")):
                continue
            match = re.match(r"([A-Za-z0-9_.-]+)\s*(.*)", value)
            if match:
                dependencies.append(
                    {
                        "ecosystem": "python",
                        "name": match.group(1),
                        "constraint_text": match.group(2) or None,
                        "dependency_group": "requirements",
                        "source_path": path,
                    }
                )
    elif name == "pyproject.toml":
        for line in text.splitlines():
            match = re.match(r'\s*"([A-Za-z0-9_.-]+)([^"]*)"\s*,?\s*$', line)
            if match:
                dependencies.append(
                    {
                        "ecosystem": "python",
                        "name": match.group(1),
                        "constraint_text": match.group(2) or None,
                        "dependency_group": "pyproject",
                        "source_path": path,
                    }
                )
    return dependencies


def extract_code_lane_facts(path: str, text: str) -> list[dict[str, Any]]:
    """Expose one parser law to both the primary and per-lane code authorities."""
    family = _CODE_FAMILIES.get(Path(path).suffix.lower(), "text-or-binary")
    if family == "python":
        symbols, imports = _python_facts(text)
    elif family in {"javascript", "typescript", "svelte", "vue"}:
        symbols, imports = _script_facts(text)
    else:
        symbols, imports = [], []
    return [
        *({"kind": "code_symbol", "locator": path, "payload": row} for row in symbols),
        *({"kind": "code_import", "locator": path, "payload": row} for row in imports),
        *(
            {"kind": "code_route", "locator": path, "payload": row}
            for row in _route_facts(text, family)
        ),
        *(
            {"kind": "code_dependency", "locator": path, "payload": row}
            for row in _dependency_facts(path, text)
        ),
    ]


def _ingest_file(
    connection: sqlite3.Connection,
    *,
    repository_id: int,
    relative: str,
    target: Path,
    report: IngestionReport,
    max_file_bytes: int,
    lines_per_chunk: int,
    overlap: int,
    observed_at: str,
    prior_chunk_sha256: set[str] | None = None,
) -> None:
    data = target.read_bytes()
    require(
        len(data) <= max_file_bytes,
        "SOURCE_FILE_TOO_LARGE",
        "Whole-source ingestion stopped because a file exceeds the configured exact-byte limit.",
        status="BLOCKED",
        path=relative,
        size_bytes=len(data),
        max_file_bytes=max_file_bytes,
    )
    text, encoding = _decode(data)
    mime, family = _file_type(relative)
    is_binary = text is None
    line_count = len(text.splitlines()) if text is not None else 0
    cursor = connection.execute(
        """
        INSERT INTO files(
            repository_id, path, size_bytes, sha256, encoding, is_binary,
            file_type, code_family, line_count, ingestion_status, exact_bytes, error_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            repository_id,
            relative,
            len(data),
            sha256_bytes(data),
            encoding,
            int(is_binary),
            mime,
            family,
            line_count,
            "EXACT_BINARY" if is_binary else "EXACT_TEXT_CHUNKED",
            data,
        ),
    )
    file_id = database.required_lastrowid(cursor)
    report.files += 1
    report.bytes += len(data)
    report.families[family] = report.families.get(family, 0) + 1
    if is_binary:
        report.binary_files += 1
        return
    report.text_files += 1
    require(
        text is not None,
        "TEXT_DECODE_STATE_INVALID",
        "A decoded text file reached the chunker without text.",
        status="FAIL",
        path=relative,
    )
    text_value = cast(str, text)
    source_sha256 = sha256_bytes(data)
    prior_chunks = prior_chunk_sha256 or set()
    for ordinal, start, end, content in _line_chunks(
        text_value, lines_per_chunk=lines_per_chunk, overlap=overlap
    ):
        chunk_sha256 = sha256_bytes(content.encode("utf-8"))
        cas_cursor = connection.execute(
            """
            INSERT OR IGNORE INTO chunk_content_cas(
                sha256, size_bytes, text_content, first_seen_at
            ) VALUES (?, ?, ?, ?)
            """,
            (chunk_sha256, len(content.encode("utf-8")), content, observed_at),
        )
        if cas_cursor.rowcount:
            report.chunk_cas_created += 1
        else:
            report.chunk_cas_reused += 1
        if chunk_sha256 in prior_chunks:
            report.changed_sections_reused += 1
        else:
            report.changed_sections_reindexed += 1
        chunk_cursor = connection.execute(
            """
            INSERT INTO chunks(file_id, ordinal, start_line, end_line, text_content, sha256)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                ordinal,
                start,
                end,
                content,
                chunk_sha256,
            ),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO chunk_history(
                repository_id, path, source_sha256, ordinal, start_line,
                end_line, chunk_sha256, observed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                repository_id,
                relative,
                source_sha256,
                ordinal,
                start,
                end,
                chunk_sha256,
                observed_at,
            ),
        )
        connection.execute(
            "INSERT INTO chunks_fts(path, text_content, chunk_id) VALUES (?, ?, ?)",
            (relative, content, database.required_lastrowid(chunk_cursor)),
        )
        report.chunks += 1
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    if family == "python":
        symbols, imports = _python_facts(text_value)
    elif family in {"javascript", "typescript", "svelte", "vue", "astro"}:
        symbols, imports = _script_facts(text_value)
    for symbol in symbols:
        connection.execute(
            """
            INSERT INTO symbols(
                file_id, kind, name, qualified_name, start_line, end_line, signature, parser
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                symbol["kind"],
                symbol["name"],
                symbol["qualified_name"],
                symbol["start_line"],
                symbol["end_line"],
                symbol["signature"],
                symbol["parser"],
            ),
        )
        report.symbols += 1
    for item in imports:
        connection.execute(
            """
            INSERT INTO imports(file_id, module, imported_name, alias, line_number, parser)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                item["module"],
                item["imported_name"],
                item["alias"],
                item["line_number"],
                item["parser"],
            ),
        )
        report.imports += 1
    for dependency in _dependency_facts(relative, text_value):
        connection.execute(
            """
            INSERT INTO dependencies(
                file_id, ecosystem, name, constraint_text, dependency_group, source_path
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                dependency["ecosystem"],
                dependency["name"],
                dependency["constraint_text"],
                dependency["dependency_group"],
                dependency["source_path"],
            ),
        )
        report.dependencies += 1
    for route in _route_facts(text_value, family):
        connection.execute(
            """
            INSERT INTO routes(
                file_id, route_kind, method, path_pattern, handler, line_number, parser
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                route["route_kind"],
                route["method"],
                route["path_pattern"],
                route["handler"],
                route["line_number"],
                route["parser"],
            ),
        )
        report.routes += 1


def ingest_repository(
    connection: sqlite3.Connection,
    *,
    repository_id: int,
    repository_root: str | Path,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    lines_per_chunk: int = DEFAULT_CHUNK_LINES,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> IngestionReport:
    require(
        lines_per_chunk > overlap >= 0,
        "CHUNK_CONFIGURATION_INVALID",
        "Chunk overlap must be smaller than chunk size.",
    )
    report = IngestionReport()
    root = Path(repository_root).resolve()
    observed_at = utc_now()
    for relative, target in iter_source_files(root):
        _ingest_file(
            connection,
            repository_id=repository_id,
            relative=relative,
            target=target,
            report=report,
            max_file_bytes=max_file_bytes,
            lines_per_chunk=lines_per_chunk,
            overlap=overlap,
            observed_at=observed_at,
        )
    if report.files == 0:
        raise EvidenceLaneError(
            "NO_SOURCE_FILES",
            "No source files remained after the governed exclusion rules.",
            status="EMPTY",
        )
    report.refresh = {
        "mode": "FULL_PV1",
        "UNCHANGED_REUSE": 0,
        "CHANGED_REBUILD": 0,
        "NEW_REGISTER": report.files,
        "REMOVED_TOMBSTONE": 0,
        "BLOCKED_UNSUPPORTED": 0,
        "CHANGED_SECTION_REUSED": report.changed_sections_reused,
        "CHANGED_SECTION_REINDEXED": report.changed_sections_reindexed,
        "CHUNK_CAS_CREATED": report.chunk_cas_created,
        "CHUNK_CAS_REUSED": report.chunk_cas_reused,
    }
    return report


def refresh_repository(
    connection: sqlite3.Connection,
    *,
    repository_id: int,
    repository_root: str | Path,
    parent_pv: str,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    lines_per_chunk: int = DEFAULT_CHUNK_LINES,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> IngestionReport:
    """Update a copied accepted code database without reparsing unchanged files."""

    require(
        lines_per_chunk > overlap >= 0,
        "CHUNK_CONFIGURATION_INVALID",
        "Chunk overlap must be smaller than chunk size.",
    )
    root = Path(repository_root).resolve()
    targets = {relative: target for relative, target in iter_source_files(root)}
    prior = {
        row["path"]: {
            "file_id": int(row["file_id"]),
            "sha256": row["sha256"],
            "size_bytes": int(row["size_bytes"]),
        }
        for row in connection.execute(
            "SELECT file_id, path, sha256, size_bytes FROM files WHERE repository_id=?",
            (repository_id,),
        )
    }
    current = {
        path: {
            "sha256": sha256_bytes(target.read_bytes()),
            "size_bytes": target.stat().st_size,
        }
        for path, target in targets.items()
    }
    unchanged = sorted(
        path
        for path in prior.keys() & current.keys()
        if prior[path]["sha256"] == current[path]["sha256"]
    )
    changed = sorted(
        path
        for path in prior.keys() & current.keys()
        if prior[path]["sha256"] != current[path]["sha256"]
    )
    added = sorted(current.keys() - prior.keys())
    removed = sorted(prior.keys() - current.keys())
    recorded_at = utc_now()
    report = IngestionReport()
    prior_chunks_by_path = {
        path: {
            str(row["sha256"])
            for row in connection.execute(
                """
                SELECT c.sha256
                FROM chunks c JOIN files f ON f.file_id=c.file_id
                WHERE f.repository_id=? AND f.path=?
                """,
                (repository_id, path),
            )
        }
        for path in changed
    }
    for path in changed + removed:
        row = prior[path]
        connection.execute(
            "DELETE FROM chunks_fts WHERE chunk_id IN "
            "(SELECT chunk_id FROM chunks WHERE file_id=?)",
            (row["file_id"],),
        )
        connection.execute("DELETE FROM files WHERE file_id=?", (row["file_id"],))
        classification = "CHANGED_REBUILD" if path in changed else "REMOVED_TOMBSTONE"
        connection.execute(
            """
            INSERT INTO source_refresh_events(
                path, classification, prior_sha256, current_sha256, recorded_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                path,
                classification,
                row["sha256"],
                current.get(path, {}).get("sha256"),
                recorded_at,
            ),
        )
        if path in removed:
            connection.execute(
                """
                INSERT INTO source_tombstones(
                    path, prior_sha256, prior_size_bytes, parent_pv, removed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    path,
                    row["sha256"],
                    row["size_bytes"],
                    parent_pv,
                    recorded_at,
                ),
            )
    for path in changed + added:
        _ingest_file(
            connection,
            repository_id=repository_id,
            relative=path,
            target=targets[path],
            report=report,
            max_file_bytes=max_file_bytes,
            lines_per_chunk=lines_per_chunk,
            overlap=overlap,
            observed_at=recorded_at,
            prior_chunk_sha256=prior_chunks_by_path.get(path),
        )
        connection.execute(
            """
            INSERT INTO source_refresh_events(
                path, classification, prior_sha256, current_sha256, recorded_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                path,
                "CHANGED_REBUILD" if path in changed else "NEW_REGISTER",
                prior.get(path, {}).get("sha256"),
                current[path]["sha256"],
                recorded_at,
            ),
        )
    counts = connection.execute(
        """
        SELECT
            COUNT(*) AS files,
            COALESCE(SUM(size_bytes), 0) AS bytes,
            SUM(CASE WHEN is_binary=0 THEN 1 ELSE 0 END) AS text_files,
            SUM(CASE WHEN is_binary=1 THEN 1 ELSE 0 END) AS binary_files
        FROM files WHERE repository_id=?
        """,
        (repository_id,),
    ).fetchone()
    report.files = int(counts["files"])
    report.bytes = int(counts["bytes"])
    report.text_files = int(counts["text_files"] or 0)
    report.binary_files = int(counts["binary_files"] or 0)
    report.chunks = int(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])
    report.symbols = int(
        connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
    )
    report.imports = int(
        connection.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
    )
    report.dependencies = int(
        connection.execute("SELECT COUNT(*) FROM dependencies").fetchone()[0]
    )
    report.routes = int(connection.execute("SELECT COUNT(*) FROM routes").fetchone()[0])
    report.families = {
        row["code_family"]: int(row["count"])
        for row in connection.execute(
            """
            SELECT code_family, COUNT(*) AS count
            FROM files WHERE repository_id=?
            GROUP BY code_family ORDER BY code_family
            """,
            (repository_id,),
        )
    }
    report.refresh = {
        "mode": "INCREMENTAL_REFRESH",
        "UNCHANGED_REUSE": len(unchanged),
        "CHANGED_REBUILD": len(changed),
        "NEW_REGISTER": len(added),
        "REMOVED_TOMBSTONE": len(removed),
        "BLOCKED_UNSUPPORTED": 0,
        "CHANGED_SECTION_REUSED": report.changed_sections_reused,
        "CHANGED_SECTION_REINDEXED": report.changed_sections_reindexed,
        "CHUNK_CAS_CREATED": report.chunk_cas_created,
        "CHUNK_CAS_REUSED": report.chunk_cas_reused,
        "unchanged_paths": unchanged,
        "changed_paths": changed,
        "new_paths": added,
        "removed_paths": removed,
    }
    if report.files == 0:
        raise EvidenceLaneError(
            "NO_SOURCE_FILES",
            "No source files remained after incremental Refresh.",
            status="EMPTY",
        )
    return report
