"""Offline multi-language Tree-sitter extraction for Codex code lanes."""

from __future__ import annotations

import importlib.util
import multiprocessing
import threading
from bisect import bisect_left
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from .hashing import canonical_json_bytes, sha256_bytes
from .shared_tool_assets import resolve_shared_asset

CODE_TOOLCHAIN_LANGUAGES: tuple[str, ...] = (
    "astro",
    "bash",
    "c",
    "cmake",
    "cpp",
    "csharp",
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
    "tsx",
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
        "function_item",
        "method_definition",
        "method_declaration",
        "interface_declaration",
        "struct_item",
        "enum_item",
        "trait_item",
        "impl_item",
        "module",
        "mod_item",
        "class_specifier",
        "struct_specifier",
        "type_declaration",
        "type_spec",
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
_TREE_SITTER_EXECUTION_LOCK = threading.Lock()
_TREE_SITTER_WORKER_PROCESS: Any | None = None
_TREE_SITTER_REQUEST_CONNECTION: Any | None = None
_TREE_SITTER_RESPONSE_CONNECTION: Any | None = None
_TREE_SITTER_REQUEST_SEQUENCE = 0
_TREE_SITTER_WORKER_STARTUP_TIMEOUT_SECONDS = 30
_TREE_SITTER_WORKER_TIMEOUT_SECONDS = 120
_TREE_SITTER_SHUTDOWN_GRACE_SECONDS = 2.0
_TREE_SITTER_KILL_GRACE_SECONDS = 1.0
_ACTIVE_GRAMMAR_IDENTITY = None


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
    """Compatibility name: verify shared assets and configure without downloads."""
    if not tree_sitter_available():
        return None
    import importlib.metadata
    runtime_root, record = resolve_shared_asset('parser_grammars')
    if record.get('version') != '1.14.3' or importlib.metadata.version('tree-sitter-language-pack') != record['version']:
        raise ValueError('TREE_SITTER_SHARED_VERSION_MISMATCH')
    from tree_sitter_language_pack import (  # type: ignore[import-not-found]
        PackConfig,
        configure,
    )
    global _ACTIVE_GRAMMAR_IDENTITY
    identity = (str(runtime_root), record['version'], record['files_sha256'])
    if _ACTIVE_GRAMMAR_IDENTITY is not None and _ACTIVE_GRAMMAR_IDENTITY != identity:
        raise ValueError('TREE_SITTER_WORKER_RESTART_REQUIRED_AFTER_ASSET_CHANGE')
    configure(PackConfig(cache_dir=str(runtime_root)))
    _ACTIVE_GRAMMAR_IDENTITY = identity
    return runtime_root


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
        DownloadError,
        detect_language,
        downloaded_languages,
        get_language,
    )

    detected = detect_language(path)
    if not detected:
        return _empty_extraction(status="UNSUPPORTED_LANGUAGE")
    if runtime_cache is None:
        return _empty_extraction(
            status="RUNTIME_NOT_PREWARMED",
            language=str(detected),
        )
    # get_language can download on a miss. Never call it until the grammar
    # has been measured in the verified local cache.
    if detected not in downloaded_languages():
        return _empty_extraction(status='RUNTIME_LANGUAGE_MISSING', language=str(detected))
    try:
        language = get_language(detected)
    except (DownloadError, KeyError, OSError, RuntimeError, ValueError):
        return _empty_extraction(
            status="RUNTIME_LANGUAGE_MISSING",
            language=str(detected),
        )
    parser = Parser(language)
    source = text.encode("utf-8")
    newline_offsets = [
        index for index, value in enumerate(source) if value == ord("\n")
    ]

    def line_number(byte_offset: int) -> int:
        # tree-sitter 0.25 Point wrappers can access-violate on Windows after
        # thousands of repeated start_point/end_point allocations. Byte
        # offsets are stable scalar fields, so derive the same one-based line
        # coordinate without retaining native Point objects.
        return bisect_left(newline_offsets, int(byte_offset)) + 1

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
        start_line = line_number(int(node.start_byte))
        end_line = line_number(int(node.end_byte))
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
        "status": "PASS" if not stack else 'NODE_BUDGET_EXCEEDED',
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


def _tree_sitter_worker_loop(
    request_connection: Any,
    response_connection: Any,
) -> None:
    """Serve parser requests without ProcessPoolExecutor management threads.

    The persistent Evidence Lane engine owns supervised child processes on
    Windows. A nested ProcessPoolExecutor can leave its management thread
    waiting forever even after a parser child is terminated.  A dedicated
    request/response pipe keeps the native grammar isolated while giving the
    parent an independent, enforceable poll timeout.
    """

    try:
        response_connection.send(("READY", None, None))
        while True:
            request = request_connection.recv()
            if request is None:
                return
            request_id, path, text = request
            try:
                response_connection.send(
                    ("PASS", request_id, _tree_sitter_worker(path, text))
                )
            except (OSError, RuntimeError, ValueError) as exc:
                response_connection.send(
                    (
                        "ERROR",
                        request_id,
                        {"exception_type": type(exc).__name__},
                    )
                )
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        request_connection.close()
        response_connection.close()


def _close_connection(connection: Any | None) -> None:
    if connection is None:
        return
    try:
        connection.close()
    except OSError:
        pass


def _bounded_tree_sitter_worker_shutdown(
    process: Any,
    request_connection: Any | None,
    response_connection: Any | None,
) -> None:
    """Stop the disposable parser worker without an unbounded Windows join."""

    if process.is_alive() and request_connection is not None:
        try:
            request_connection.send(None)
        except (BrokenPipeError, EOFError, OSError):
            pass

    process.join(timeout=_TREE_SITTER_SHUTDOWN_GRACE_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(timeout=_TREE_SITTER_KILL_GRACE_SECONDS)
    if process.is_alive():
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
            process.join(timeout=_TREE_SITTER_KILL_GRACE_SECONDS)

    _close_connection(request_connection)
    _close_connection(response_connection)


def _start_tree_sitter_worker() -> tuple[Any, Any, Any]:
    context = multiprocessing.get_context("spawn")
    child_request, parent_request = context.Pipe(duplex=False)
    parent_response, child_response = context.Pipe(duplex=False)
    process = context.Process(
        target=_tree_sitter_worker_loop,
        args=(child_request, child_response),
        name="evidence-lane-tree-sitter",
        daemon=True,
    )
    process.start()
    child_request.close()
    child_response.close()
    if not parent_response.poll(_TREE_SITTER_WORKER_STARTUP_TIMEOUT_SECONDS):
        _bounded_tree_sitter_worker_shutdown(process, parent_request, parent_response)
        raise RuntimeError("TREE_SITTER_WORKER_STARTUP_TIMEOUT")
    status, request_id, payload = parent_response.recv()
    if status != "READY" or request_id is not None or payload is not None:
        _bounded_tree_sitter_worker_shutdown(process, parent_request, parent_response)
        raise RuntimeError("TREE_SITTER_WORKER_STARTUP_PROTOCOL_INVALID")
    return process, parent_request, parent_response


def _tree_sitter_runtime() -> tuple[Any, Any, Any]:
    global _TREE_SITTER_REQUEST_CONNECTION
    global _TREE_SITTER_RESPONSE_CONNECTION
    global _TREE_SITTER_WORKER_PROCESS
    with _TREE_SITTER_POOL_LOCK:
        if (
            _TREE_SITTER_WORKER_PROCESS is None
            or _TREE_SITTER_REQUEST_CONNECTION is None
            or _TREE_SITTER_RESPONSE_CONNECTION is None
            or not _TREE_SITTER_WORKER_PROCESS.is_alive()
        ):
            (
                _TREE_SITTER_WORKER_PROCESS,
                _TREE_SITTER_REQUEST_CONNECTION,
                _TREE_SITTER_RESPONSE_CONNECTION,
            ) = _start_tree_sitter_worker()
        return (
            _TREE_SITTER_WORKER_PROCESS,
            _TREE_SITTER_REQUEST_CONNECTION,
            _TREE_SITTER_RESPONSE_CONNECTION,
        )


def _discard_tree_sitter_worker() -> None:
    global _TREE_SITTER_REQUEST_CONNECTION
    global _TREE_SITTER_RESPONSE_CONNECTION
    global _TREE_SITTER_WORKER_PROCESS
    with _TREE_SITTER_POOL_LOCK:
        process = _TREE_SITTER_WORKER_PROCESS
        request_connection = _TREE_SITTER_REQUEST_CONNECTION
        response_connection = _TREE_SITTER_RESPONSE_CONNECTION
        _TREE_SITTER_WORKER_PROCESS = None
        _TREE_SITTER_REQUEST_CONNECTION = None
        _TREE_SITTER_RESPONSE_CONNECTION = None
    if process is not None:
        _bounded_tree_sitter_worker_shutdown(
            process, request_connection, response_connection
        )
    else:
        _close_connection(request_connection)
        _close_connection(response_connection)


def shutdown_tree_sitter_runtime() -> None:
    """Close the reusable native parser worker after one bounded lane build.

    A project Refresh owns a transaction directory.  Leaving the shared worker
    alive after all parser futures have resolved can keep Windows resources
    open while a failed transaction is being removed.  The active pool has no
    outstanding jobs at this boundary, so a deterministic close is safe.
    """

    _discard_tree_sitter_worker()


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
    global _TREE_SITTER_REQUEST_SEQUENCE
    with _TREE_SITTER_EXECUTION_LOCK:
        try:
            process, request_connection, response_connection = (
                _tree_sitter_runtime()
            )
            _TREE_SITTER_REQUEST_SEQUENCE += 1
            request_id = _TREE_SITTER_REQUEST_SEQUENCE
            request_connection.send((request_id, path, text))
            if not response_connection.poll(_TREE_SITTER_WORKER_TIMEOUT_SECONDS):
                _discard_tree_sitter_worker()
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
            status, response_request_id, payload = response_connection.recv()
            if response_request_id != request_id:
                raise RuntimeError("TREE_SITTER_WORKER_RESPONSE_ID_MISMATCH")
            if status != "PASS":
                exception_type = (
                    str(payload.get("exception_type") or "UNKNOWN")
                    if isinstance(payload, dict)
                    else "UNKNOWN"
                )
                raise RuntimeError(
                    f"TREE_SITTER_WORKER_REPORTED_FAILURE:{exception_type}"
                )
            if not process.is_alive():
                raise RuntimeError("TREE_SITTER_WORKER_EXITED_AFTER_RESPONSE")
            return TreeSitterExtraction.model_validate(payload)
        except (BrokenPipeError, EOFError, OSError, RuntimeError, ValueError) as exc:
            _discard_tree_sitter_worker()
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
