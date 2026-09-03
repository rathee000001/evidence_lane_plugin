"""Offline multi-language Tree-sitter extraction for Codex code lanes."""

from __future__ import annotations

import importlib.util
import multiprocessing
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .hashing import canonical_json_bytes, sha256_bytes
from .native_toolchain import configured_runtime_root

CODE_TOOLCHAIN_LANGUAGES: tuple[str, ...] = (
    "astro",
    "bash",
    "c",
    "cmake",
    "cpp",
    "c_sharp",
    "css",
    "dockerfile",
    "go",
    "hcl",
    "html",
    "ini",
    "java",
    "javascript",
    "json",
    "kotlin",
    "lua",
    "make",
    "markdown",
    "php",
    "powershell",
    "python",
    "ruby",
    "rust",
    "scss",
    "sql",
    "svelte",
    "swift",
    "toml",
    "typescript",
    "vue",
    "xml",
    "yaml",
)

_SYMBOL_NODE_TYPES = frozenset(
    {
        "class_definition",
        "class_declaration",
        "function_definition",
        "function_declaration",
        "method_definition",
        "method_declaration",
        "interface_declaration",
        "struct_item",
        "enum_item",
        "trait_item",
        "impl_item",
        "module",
    }
)
_IMPORT_NODE_TYPES = frozenset(
    {
        "import_statement",
        "import_from_statement",
        "import_declaration",
        "use_declaration",
        "using_directive",
        "package_clause",
        "require_call",
    }
)
_CALL_NODE_TYPES = frozenset(
    {"call", "call_expression", "method_invocation", "invocation_expression"}
)
_TREE_SITTER_POOL_LOCK = threading.Lock()
_TREE_SITTER_PROCESS_POOL: ProcessPoolExecutor | None = None
_TREE_SITTER_WORKER_TIMEOUT_SECONDS = 120


class TreeSitterExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    parser: str
    language: str | None
    symbols: list[dict[str, Any]]
    imports: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    diagnostics: list[dict[str, Any]]
    nodes_visited: int
    offline_only: bool
    auto_download_used: bool
    extraction_sha256: str


def tree_sitter_available() -> bool:
    return (
        importlib.util.find_spec("tree_sitter") is not None
        and importlib.util.find_spec("tree_sitter_language_pack") is not None
    )


def initialize_hidden_tree_sitter_runtime() -> Path | None:
    runtime_root = configured_runtime_root()
    if runtime_root is None or not tree_sitter_available():
        return None
    from tree_sitter_language_pack import (  # type: ignore[import-not-found]
        PackConfig,
        init,
    )

    cache = (
        runtime_root
        / "toolchains"
        / "tree-sitter-language-pack"
        / "1.14.3"
        / "libs"
    )
    cache.mkdir(parents=True, exist_ok=True)
    init(PackConfig(cache_dir=str(cache)))
    return cache


def _node_text(source: bytes, node: Any, *, limit: int = 500) -> str:
    value = source[int(node.start_byte) : int(node.end_byte)].decode(
        "utf-8", errors="replace"
    )
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _empty_extraction(
    *,
    status: str,
    language: str | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
) -> TreeSitterExtraction:
    core = {
        "status": status,
        "parser": "tree-sitter-language-pack",
        "language": language,
        "symbols": [],
        "imports": [],
        "calls": [],
        "diagnostics": list(diagnostics or []),
        "nodes_visited": 0,
        "offline_only": True,
        "auto_download_used": False,
    }
    return TreeSitterExtraction.model_validate(
        {**core, "extraction_sha256": sha256_bytes(canonical_json_bytes(core))}
    )


def _extract_tree_sitter_facts_in_process(path: str, text: str) -> TreeSitterExtraction:
    """Run the native parser inside its dedicated worker process."""

    if not tree_sitter_available():
        return _empty_extraction(status="UNAVAILABLE")

    runtime_cache = initialize_hidden_tree_sitter_runtime()
    from tree_sitter import Parser  # type: ignore[import-not-found]
    from tree_sitter_language_pack import (  # type: ignore[import-not-found]
        available_languages,
        detect_language,
        get_language,
    )

    detected = detect_language(path)
    available = {str(item) for item in available_languages()}
    if not detected or detected not in available:
        status = (
            "UNSUPPORTED_LANGUAGE"
            if not detected
            else (
                "RUNTIME_NOT_PREWARMED"
                if runtime_cache is None
                else "RUNTIME_LANGUAGE_MISSING"
            )
        )
        core: dict[str, Any] = {
            "status": status,
            "parser": "tree-sitter-language-pack",
            "language": str(detected) if detected else None,
            "symbols": [],
            "imports": [],
            "calls": [],
            "diagnostics": [],
            "nodes_visited": 0,
            "offline_only": True,
            "auto_download_used": False,
        }
        return TreeSitterExtraction.model_validate(
            {**core, "extraction_sha256": sha256_bytes(canonical_json_bytes(core))}
        )

    language = get_language(detected)
    parser = Parser(language)
    source = text.encode("utf-8")
    tree = parser.parse(source)
    if tree is None:
        raise ValueError("TREE_SITTER_PARSE_RETURNED_NONE")

    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    stack = [tree.root_node]
    visited = 0
    max_nodes = 250_000
    while stack and visited < max_nodes:
        node = stack.pop()
        visited += 1
        node_type = str(node.type)
        start_line = int(node.start_point.row) + 1
        end_line = int(node.end_point.row) + 1
        if node_type in _SYMBOL_NODE_TYPES and len(symbols) < 20_000:
            name_node = node.child_by_field_name("name")
            name = _node_text(source, name_node) if name_node is not None else node_type
            symbols.append(
                {
                    "kind": node_type,
                    "name": name,
                    "qualified_name": name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "signature": _node_text(source, node, limit=300).split("\n", 1)[0],
                    "parser": "tree-sitter-language-pack",
                    "language": detected,
                }
            )
        if node_type in _IMPORT_NODE_TYPES and len(imports) < 20_000:
            imports.append(
                {
                    "module": _node_text(source, node),
                    "imported_name": None,
                    "alias": None,
                    "line_number": start_line,
                    "parser": "tree-sitter-language-pack",
                    "language": detected,
                }
            )
        if node_type in _CALL_NODE_TYPES and len(calls) < 50_000:
            function_node = node.child_by_field_name("function")
            calls.append(
                {
                    "callee": (
                        _node_text(source, function_node)
                        if function_node is not None
                        else _node_text(source, node, limit=200)
                    ),
                    "start_line": start_line,
                    "end_line": end_line,
                    "parser": "tree-sitter-language-pack",
                    "language": detected,
                }
            )
        if bool(node.is_error) or bool(node.is_missing):
            diagnostics.append(
                {
                    "node_type": node_type,
                    "start_line": start_line,
                    "end_line": end_line,
                    "is_error": bool(node.is_error),
                    "is_missing": bool(node.is_missing),
                }
            )
        stack.extend(reversed(node.children))

    core = {
        "status": "PASS",
        "parser": "tree-sitter-language-pack",
        "language": detected,
        "symbols": symbols,
        "imports": imports,
        "calls": calls,
        "diagnostics": diagnostics,
        "nodes_visited": visited,
        "offline_only": True,
        "auto_download_used": False,
    }
    return TreeSitterExtraction.model_validate(
        {**core, "extraction_sha256": sha256_bytes(canonical_json_bytes(core))}
    )


def _tree_sitter_worker(path: str, text: str) -> dict[str, Any]:
    return _extract_tree_sitter_facts_in_process(path, text).model_dump(mode="json")


def _tree_sitter_pool() -> ProcessPoolExecutor:
    global _TREE_SITTER_PROCESS_POOL
    with _TREE_SITTER_POOL_LOCK:
        if _TREE_SITTER_PROCESS_POOL is None:
            _TREE_SITTER_PROCESS_POOL = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return _TREE_SITTER_PROCESS_POOL


def _discard_tree_sitter_pool() -> None:
    global _TREE_SITTER_PROCESS_POOL
    with _TREE_SITTER_POOL_LOCK:
        pool = _TREE_SITTER_PROCESS_POOL
        _TREE_SITTER_PROCESS_POOL = None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def shutdown_tree_sitter_runtime() -> None:
    """Close the reusable native parser worker after one bounded lane build.

    A project Refresh owns a transaction directory.  Leaving the shared worker
    alive after all parser futures have resolved can keep Windows resources
    open while a failed transaction is being removed.  The active pool has no
    outstanding jobs at this boundary, so a deterministic close is safe.
    """

    global _TREE_SITTER_PROCESS_POOL
    with _TREE_SITTER_POOL_LOCK:
        pool = _TREE_SITTER_PROCESS_POOL
        _TREE_SITTER_PROCESS_POOL = None
    if pool is not None:
        pool.shutdown(wait=True, cancel_futures=True)


def extract_tree_sitter_facts(path: str, text: str) -> TreeSitterExtraction:
    """Extract bounded syntax facts with native crashes isolated from the host.

    Tree-sitter grammars are native libraries.  A Windows access violation
    cannot be caught by Python and previously terminated the entire MCP or
    regression process.  Keep one reusable spawned worker for the native
    parser; a worker crash becomes an explicit fail-visible extraction receipt
    while the owning Evidence Lane process remains available.
    """

    if not tree_sitter_available():
        return _empty_extraction(status="UNAVAILABLE")
    try:
        payload = _tree_sitter_pool().submit(_tree_sitter_worker, path, text).result(
            timeout=_TREE_SITTER_WORKER_TIMEOUT_SECONDS
        )
        return TreeSitterExtraction.model_validate(payload)
    except FutureTimeoutError:
        _discard_tree_sitter_pool()
        return _empty_extraction(
            status="NATIVE_PARSER_TIMEOUT_ISOLATED",
            diagnostics=[
                {
                    "reason": "TREE_SITTER_WORKER_TIMEOUT",
                    "timeout_seconds": _TREE_SITTER_WORKER_TIMEOUT_SECONDS,
                    "host_process_preserved": True,
                }
            ],
        )
    except BrokenProcessPool as exc:
        _discard_tree_sitter_pool()
        return _empty_extraction(
            status="NATIVE_PARSER_CRASH_ISOLATED",
            diagnostics=[
                {
                    "reason": "TREE_SITTER_WORKER_PROCESS_TERMINATED",
                    "exception_type": type(exc).__name__,
                    "host_process_preserved": True,
                }
            ],
        )
    except (OSError, RuntimeError, ValueError) as exc:
        _discard_tree_sitter_pool()
        return _empty_extraction(
            status="NATIVE_PARSER_FAILURE_ISOLATED",
            diagnostics=[
                {
                    "reason": "TREE_SITTER_WORKER_FAILURE",
                    "exception_type": type(exc).__name__,
                    "host_process_preserved": True,
                }
            ],
        )


__all__ = [
    "CODE_TOOLCHAIN_LANGUAGES",
    "TreeSitterExtraction",
    "extract_tree_sitter_facts",
    "initialize_hidden_tree_sitter_runtime",
    "shutdown_tree_sitter_runtime",
    "tree_sitter_available",
]
