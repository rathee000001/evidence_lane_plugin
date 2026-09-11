"""Bounded parser leaves adapted from hash-admitted original Code ingestion.

This module has no database, session, tool installation or accepted-PV imports.
Static facts describe syntax; they do not establish dynamic call targets.
"""
from __future__ import annotations

import ast
import json
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .dependency_detection import parse_pnpm_lock_dependencies

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
    if name == "pnpm-lock.yaml":
        result = parse_pnpm_lock_dependencies(text)
        if result["status"] != "PASS":
            return dependencies
        for row in result["dependencies"]:
            dependencies.append(
                {
                    "ecosystem": "pnpm",
                    "name": str(row["name"]),
                    "constraint_text": str(row["constraint"]) or None,
                    "dependency_group": str(row["group"]),
                    "source_path": path,
                    "detector_id": str(row["detector_id"]),
                    "lock_importer": str(row["importer"]),
                    "resolved_version": str(row["resolved_version"]) or None,
                }
            )
    elif name == "package.json":
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
        # Parse declared dependency fields; arbitrary quoted TOML strings are
        # not package dependencies.
        payload = tomllib.loads(text)
        project = payload.get('project', {})
        groups = {'project': project.get('dependencies', []),
                  'build-system': payload.get('build-system', {}).get('requires', [])}
        groups.update({'optional:' + key: value for key, value in project.get('optional-dependencies', {}).items()})
        for group, values in groups.items():
            for value in values:
                match = re.match(r'([A-Za-z0-9_.-]+)(.*)', value)
                if match:
                    dependencies.append({'ecosystem': 'python', 'name': match[1],
                        'constraint_text': match[2] or None, 'dependency_group': group, 'source_path': path})
    return dependencies


def parse_code(path: str, content: bytes, *, syntax: bool = False) -> dict:
    """Parse exact selected bytes without executing, importing or fetching code."""
    from .hashing import canonical_json_bytes, sha256_bytes
    text, encoding = _decode(content)
    family = _CODE_FAMILIES.get(Path(path).suffix.lower(), 'text-or-binary')
    facts: dict[str, list[dict]] = {name: [] for name in ('symbol', 'import', 'call', 'route', 'dependency', 'parser_diagnostic')}
    state, parser = 'opaque_bytes', 'none'
    if text is not None:
        state, parser = 'text_only', 'line_chunks'
        try:
            if family == 'python':
                tree = ast.parse(text)
                facts['symbol'], facts['import'] = _python_facts(text)
                facts['call'] = [{'callee': ast.unparse(node.func)[:500], 'start_line': node.lineno,
                    'end_line': node.end_lineno, 'parser': 'python-ast', 'resolution': 'syntax_only'}
                    for node in ast.walk(tree) if isinstance(node, ast.Call)]
                state, parser = 'parsed_syntax', 'python-ast'
            elif syntax:
                from .code_toolchain import _extract_tree_sitter_facts_in_process
                extracted = _extract_tree_sitter_facts_in_process(path, text)
                if extracted.status not in {'PASS', 'UNSUPPORTED_LANGUAGE'}:
                    raise ValueError('The explicitly selected native grammar did not produce a complete syntax result')
                if extracted.status == 'PASS':
                    facts['symbol'], facts['import'] = extracted.symbols, extracted.imports
                    facts['call'] = extracted.calls
                    facts['parser_diagnostic'] = extracted.diagnostics
                    state, parser = 'parse_diagnostics' if extracted.diagnostics else 'parsed_syntax', extracted.parser
            elif family in {'javascript', 'typescript', 'svelte', 'vue'}:
                facts['symbol'], facts['import'] = _script_facts(text)
                state, parser = 'heuristic_partial', 'script-regex'
            facts['route'] = _route_facts(text, family)
            facts['dependency'] = _dependency_facts(path, text)
            if Path(path).name == 'pnpm-lock.yaml':
                detection = parse_pnpm_lock_dependencies(text)
                if detection['status'] != 'PASS':
                    facts['parser_diagnostic'].append({'reason': detection['reason'], 'parser': detection['detector_id']})
                    state = 'parse_diagnostics'
        except (SyntaxError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
            state = 'parse_diagnostics'
            facts['parser_diagnostic'].append({'reason': type(error).__name__,
                'start_line': getattr(error, 'lineno', 1), 'message': 'Selected source has parser diagnostics.'})
    if sum(len(rows) for rows in facts.values()) > 20_000:
        raise ValueError('CODE_FACT_BUDGET')
    facts['parser_receipt'] = [{'parser': parser, 'status': state, 'language': family,
        'offline_only': True, 'auto_download_used': False, 'runtime_code_executed': False,
        'static_target_resolution_complete': False, 'syntax_requested': syntax}]
    return {'language': family, 'encoding': encoding, 'parser_state': state, 'facts': facts,
            'facts_digest': sha256_bytes(canonical_json_bytes(facts)).lower(),
            'chunks': [{'ordinal': order, 'start_line': first, 'end_line': last, 'text': value}
                       for order, first, last, value in _line_chunks(text or '', lines_per_chunk=80, overlap=8)]}

