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
    symbols: int = 0
    imports: int = 0
    dependencies: int = 0
    routes: int = 0
    bytes: int = 0
    families: dict[str, int] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "text_files": self.text_files,
            "binary_files": self.binary_files,
            "chunks": self.chunks,
            "symbols": self.symbols,
            "imports": self.imports,
            "dependencies": self.dependencies,
            "routes": self.routes,
            "bytes": self.bytes,
            "families": dict(sorted(self.families.items())),
            "warnings": self.warnings,
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
    for relative, target in iter_source_files(root):
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
            continue
        report.text_files += 1
        require(
            text is not None,
            "TEXT_DECODE_STATE_INVALID",
            "A decoded text file reached the chunker without text.",
            status="FAIL",
            path=relative,
        )
        text_value = cast(str, text)
        for ordinal, start, end, content in _line_chunks(
            text_value, lines_per_chunk=lines_per_chunk, overlap=overlap
        ):
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
                    sha256_bytes(content.encode("utf-8")),
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
    if report.files == 0:
        raise EvidenceLaneError(
            "NO_SOURCE_FILES",
            "No source files remained after the governed exclusion rules.",
            status="EMPTY",
        )
    return report
