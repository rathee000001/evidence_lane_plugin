"""Governed SQLite FTS5 authority plus bounded ripgrep file-search fallback."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess  # nosec B404 - exact executable, fixed argv, no shell
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .redaction import redact

MANIFEST_SCHEMA = "evidence-lane.search-toolchain-manifest.v1"
CAPABILITY_SCHEMA = "evidence-lane.search-tool-capability.v1"
INVOCATION_SCHEMA = "evidence-lane.search-tool-invocation.v1"

_DEFAULT_EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".runtime",
    ".venv",
    "__pycache__",
    "node_modules",
    "venv",
}
_DEFAULT_EXCLUDED_FILES = {
    ".env",
    ".env.example",
    ".env.local",
    ".npmrc",
    ".pypirc",
}
_HASH_RE = re.compile(r"^[A-F0-9]{64}$")
_PLUGIN_ROOT_ENV = "EVIDENCE_LANE_PLUGIN_ROOT"


class SearchToolchainError(ValueError):
    """Raised when a caller exceeds a governed search boundary."""


@dataclass(frozen=True)
class ToolResolution:
    tool_id: str
    backend: str
    executable: Path | None
    version: str | None
    binary_sha256: str | None
    path_sha256: str | None
    reason: str
    manifest_sha256: str
    fallback_backend: str

    def receipt(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "schema": CAPABILITY_SCHEMA,
            "status": "PASS",
            "tool_id": self.tool_id,
            "backend": self.backend,
            "version": self.version,
            "binary_sha256": self.binary_sha256,
            "executable_path_sha256": self.path_sha256,
            "raw_executable_path_included": False,
            "reason": self.reason,
            "fallback_backend": self.fallback_backend,
            "manifest_sha256": self.manifest_sha256,
            "path_lookup_used": False,
            "shell_used": False,
            "auto_download_used": False,
            "source_write_authorized": False,
            "git_authorized": False,
            "lifecycle_authorized": False,
            "candidate_authorized": False,
            "hil_authorized": False,
            "pointer_authorized": False,
            "raw_secret_stored": False,
        }
        body["capability_receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
        return body


def plugin_root() -> Path:
    configured = os.environ.get(_PLUGIN_ROOT_ENV, "").strip()
    if configured:
        candidate = Path(configured)
        if (
            not candidate.is_absolute()
            or not (candidate / "toolchains" / "search-tools.v1.json").is_file()
            or not (candidate / ".codex-plugin" / "plugin.json").is_file()
        ):
            raise SearchToolchainError("SEARCH_TOOLCHAIN_PLUGIN_ROOT_INVALID")
        return candidate.resolve()

    source = Path(__file__).resolve()
    for ancestor in source.parents:
        if (
            (ancestor / "toolchains" / "search-tools.v1.json").is_file()
            and (ancestor / ".codex-plugin" / "plugin.json").is_file()
        ):
            return ancestor
        if ancestor.name == ".venv":
            candidate = ancestor.parent
            if (
                (candidate / "toolchains" / "search-tools.v1.json").is_file()
                and (candidate / ".codex-plugin" / "plugin.json").is_file()
            ):
                return candidate
    raise SearchToolchainError("SEARCH_TOOLCHAIN_PLUGIN_ROOT_UNAVAILABLE")


def _manifest_path(root: Path) -> Path:
    return root / "toolchains" / "search-tools.v1.json"


def load_search_toolchain_manifest(root: Path | None = None) -> dict[str, Any]:
    root = (root or plugin_root()).resolve()
    path = _manifest_path(root)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SearchToolchainError("SEARCH_TOOLCHAIN_MANIFEST_UNAVAILABLE") from exc
    if (
        value.get("schema") != MANIFEST_SCHEMA
        or value.get("version") != 1
        or value.get("scope") != "ALL_GOVERNED_PROJECTS"
        or value.get("resolution_order")
        != [
            "PACKAGE_LOCAL_VERIFIED_BINARY",
            "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY",
            "DETERMINISTIC_BUILTIN_FALLBACK",
        ]
        or value.get("auto_download_during_mcp_handshake") is not False
        or value.get("path_lookup_allowed") is not False
        or value.get("shell_execution_allowed") is not False
    ):
        raise SearchToolchainError("SEARCH_TOOLCHAIN_MANIFEST_INVALID")
    fts = value.get("fts_authority")
    if fts != {
        "backend": "SQLITE_FTS5",
        "query_mode": "BOUNDED_FTS5",
        "scope": "PLAN_LANE_CHATLINEAGE_AND_PROJECT_SECTORS",
        "pointer_and_locator_required": True,
        "model_context_policy": "BOUNDED_QUERY_RESULTS_ONLY",
        "pv_package_loaded_into_model_context": False,
        "fallback": "FAIL_CLOSED_WHEN_SQLITE_FTS5_UNAVAILABLE",
    }:
        raise SearchToolchainError("SEARCH_FTS_AUTHORITY_INVALID")
    tools = value.get("tools")
    if not isinstance(tools, list) or [row.get("tool_id") for row in tools] != [
        "ripgrep",
    ]:
        raise SearchToolchainError("SEARCH_TOOLCHAIN_INVENTORY_INVALID")
    return value


def declared_search_toolchain_identity(root: Path | None = None) -> dict[str, Any]:
    root = (root or plugin_root()).resolve()
    manifest = load_search_toolchain_manifest(root)
    manifest_path = _manifest_path(root)
    binaries: list[dict[str, Any]] = []
    for tool in manifest["tools"]:
        for platform_id, row in sorted(tool["package_binaries"].items()):
            binary = (root / row["path"]).resolve()
            licenses = [(root / item).resolve() for item in row["licenses"]]
            binaries.append(
                {
                    "tool_id": tool["tool_id"],
                    "platform_id": platform_id,
                    "version": tool["version"],
                    "declared_sha256": row["sha256"],
                    "declared_size_bytes": row["size_bytes"],
                    "package_binary_present": binary.is_file(),
                    "package_binary_matches": (
                        binary.is_file()
                        and binary.stat().st_size == row["size_bytes"]
                        and sha256_file(binary) == row["sha256"]
                    ),
                    "license_files_present": all(path.is_file() for path in licenses),
                }
            )
    body = {
        "schema": MANIFEST_SCHEMA,
        "manifest_sha256": sha256_file(manifest_path),
        "scope": manifest["scope"],
        "resolution_order": manifest["resolution_order"],
        "fts_authority": manifest["fts_authority"],
        "binaries": binaries,
    }
    body["identity_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def _platform_id() -> str:
    system = platform.system().casefold()
    machine = platform.machine().casefold()
    architecture = "x86_64" if machine in {"amd64", "x86_64"} else machine
    names = {"windows": "windows", "linux": "linux", "darwin": "macos"}
    return f"{names.get(system, system)}-{architecture}"


def _tool_contract(manifest: Mapping[str, Any], tool_id: str) -> Mapping[str, Any]:
    for row in manifest.get("tools") or []:
        if isinstance(row, Mapping) and row.get("tool_id") == tool_id:
            return row
    raise SearchToolchainError("SEARCH_TOOL_UNKNOWN")


def _path_hash(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest().upper()


def _hidden_creationflags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _probe_binary(path: Path, contract: Mapping[str, Any]) -> tuple[str, str]:
    if not path.is_absolute() or not path.is_file():
        raise SearchToolchainError("SEARCH_BINARY_ABSOLUTE_FILE_REQUIRED")
    try:
        completed = subprocess.run(  # nosec B603
            [str(path), "--version"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            shell=False,
            creationflags=_hidden_creationflags(),
        )
        binary_hash = sha256_file(path)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SearchToolchainError("SEARCH_BINARY_PROBE_FAILED") from exc
    first_line = completed.stdout.splitlines()[0] if completed.stdout else ""
    if completed.returncode != 0 or re.match(str(contract["version_pattern"]), first_line) is None:
        raise SearchToolchainError("SEARCH_BINARY_VERSION_MISMATCH")
    return first_line, binary_hash


def resolve_search_tool(
    tool_id: str,
    *,
    root: Path | None = None,
    configured_path: str | Path | None = None,
    configured_sha256: str | None = None,
) -> ToolResolution:
    root = (root or plugin_root()).resolve()
    manifest = load_search_toolchain_manifest(root)
    manifest_sha = sha256_file(_manifest_path(root))
    contract = _tool_contract(manifest, tool_id)
    fallback = str(contract["fallback_backend"])
    reasons: list[str] = []

    package_row = (contract.get("package_binaries") or {}).get(_platform_id())
    if isinstance(package_row, Mapping):
        candidate = (root / str(package_row["path"])).resolve()
        try:
            candidate.relative_to(root)
            if (
                not candidate.is_file()
                or candidate.stat().st_size != int(package_row["size_bytes"])
                or sha256_file(candidate) != package_row["sha256"]
            ):
                raise SearchToolchainError("PACKAGE_BINARY_IDENTITY_MISMATCH")
            version_line, binary_hash = _probe_binary(candidate, contract)
            return ToolResolution(
                tool_id,
                "PACKAGE_LOCAL_VERIFIED_BINARY",
                candidate,
                version_line,
                binary_hash,
                _path_hash(candidate),
                "PACKAGE_BINARY_VERIFIED",
                manifest_sha,
                fallback,
            )
        except (OSError, ValueError, SearchToolchainError) as exc:
            reasons.append(str(exc))
    else:
        reasons.append("PACKAGE_BINARY_PLATFORM_UNAVAILABLE")

    supplied_path = configured_path or os.environ.get(str(contract["configured_path_env"]))
    supplied_hash = configured_sha256 or os.environ.get(
        str(contract["configured_sha256_env"])
    )
    if supplied_path:
        candidate = Path(supplied_path).expanduser().resolve()
        lowered = os.path.normcase(str(candidate)).casefold()
        if "\\windowsapps\\" in lowered or "\\.codex\\" in lowered:
            reasons.append("HOST_BINARY_UNTRUSTED_OWNERSHIP_ROOT")
        elif not supplied_hash or _HASH_RE.fullmatch(str(supplied_hash).upper()) is None:
            reasons.append("CONFIGURED_BINARY_SHA256_REQUIRED")
        else:
            try:
                version_line, binary_hash = _probe_binary(candidate, contract)
                if binary_hash != str(supplied_hash).upper():
                    raise SearchToolchainError("CONFIGURED_BINARY_HASH_MISMATCH")
                return ToolResolution(
                    tool_id,
                    "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY",
                    candidate,
                    version_line,
                    binary_hash,
                    _path_hash(candidate),
                    "CONFIGURED_BINARY_VERIFIED",
                    manifest_sha,
                    fallback,
                )
            except (OSError, SearchToolchainError) as exc:
                reasons.append(str(exc))
    else:
        reasons.append("CONFIGURED_BINARY_NOT_SUPPLIED")

    return ToolResolution(
        tool_id,
        "DETERMINISTIC_BUILTIN_FALLBACK",
        None,
        None,
        None,
        None,
        "|".join(reasons),
        manifest_sha,
        fallback,
    )


def _read_limited(
    stream: BinaryIO,
    output: bytearray,
    limit: int,
    overflow: threading.Event,
    process: subprocess.Popen[bytes],
) -> None:
    while True:
        chunk = stream.read(65_536)
        if not chunk:
            return
        if len(output) + len(chunk) > limit:
            overflow.set()
            try:
                process.kill()
            except OSError:
                pass
            return
        output.extend(chunk)


def _run_bounded(
    executable: Path,
    arguments: Sequence[str],
    *,
    input_bytes: bytes | None,
    timeout_seconds: int,
    max_output_bytes: int,
) -> tuple[int, bytes, bytes, str | None]:
    environment = os.environ.copy()
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "FZF_DEFAULT_OPTS": "",
            "RIPGREP_CONFIG_PATH": "",
        }
    )
    process = subprocess.Popen(  # nosec B603
        [str(executable), *arguments],
        stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=environment,
        creationflags=_hidden_creationflags(),
    )
    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    overflow = threading.Event()
    readers = [
        threading.Thread(
            target=_read_limited,
            args=(process.stdout, stdout, max_output_bytes, overflow, process),
            daemon=True,
        ),
        threading.Thread(
            target=_read_limited,
            args=(process.stderr, stderr, 65_536, overflow, process),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()
    if input_bytes is not None:
        assert process.stdin is not None
        try:
            process.stdin.write(input_bytes)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
    failure: str | None = None
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        failure = "SEARCH_TOOL_TIMEOUT"
        process.kill()
        process.wait(timeout=3)
    for reader in readers:
        reader.join(timeout=3)
    if overflow.is_set():
        failure = "SEARCH_TOOL_OUTPUT_BOUND_EXCEEDED"
    return int(process.returncode or 0), bytes(stdout), bytes(stderr), failure


def _excluded(relative: Path) -> bool:
    parts = {part.casefold() for part in relative.parts}
    return bool(parts.intersection(_DEFAULT_EXCLUDED_DIRS)) or (
        relative.name.casefold() in _DEFAULT_EXCLUDED_FILES
        or relative.name.casefold().startswith(".env.")
    )


def _safe_match(root: Path, path_text: str, line: int, column: int, text: str) -> dict[str, Any] | None:
    candidate = Path(path_text)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return None
    relative_path = Path(relative)
    if _excluded(relative_path):
        return None
    visible = str(redact(text.rstrip("\r\n")))
    return {
        "path": relative,
        "line": int(line),
        "column": int(column),
        "visible_text_after_redaction": visible,
        "visible_text_sha256": sha256_bytes(visible.encode("utf-8")),
    }


def _fallback_search(
    root: Path,
    query: str,
    *,
    limit: int,
    bounds: Mapping[str, int],
) -> list[dict[str, Any]]:
    query_folded = query.casefold()
    results: list[dict[str, Any]] = []
    files_seen = 0
    total_read = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative_dir = Path(directory).resolve().relative_to(root)
        dirs[:] = sorted(
            name
            for name in dirs
            if not _excluded(relative_dir / name)
            and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(files):
            relative = relative_dir / name
            path = Path(directory) / name
            if _excluded(relative) or path.is_symlink():
                continue
            files_seen += 1
            if files_seen > bounds["max_files"]:
                return results
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > bounds["max_file_bytes"]:
                continue
            total_read += size
            if total_read > bounds["max_total_read_bytes"]:
                return results
            try:
                raw = path.read_bytes()
                if b"\0" in raw:
                    continue
                content = raw.decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line_number, line_text in enumerate(content.splitlines(), start=1):
                column = line_text.casefold().find(query_folded)
                if column < 0:
                    continue
                match = _safe_match(root, str(path), line_number, column + 1, line_text)
                if match is not None:
                    results.append(match)
                if len(results) >= limit:
                    return results
    return results


def bounded_text_search(
    project_root: str | Path,
    query: str,
    *,
    limit: int = 100,
    plugin_source_root: Path | None = None,
    configured_path: str | Path | None = None,
    configured_sha256: str | None = None,
) -> dict[str, Any]:
    """Run bounded literal search through rg or the deterministic fallback."""

    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise SearchToolchainError("SEARCH_ROOT_DIRECTORY_REQUIRED")
    manifest = load_search_toolchain_manifest(plugin_source_root)
    bounds = manifest["bounds"]
    if not query or len(query) > bounds["max_query_chars"] or "\0" in query:
        raise SearchToolchainError("SEARCH_QUERY_BOUND_INVALID")
    if not 1 <= limit <= bounds["max_results"]:
        raise SearchToolchainError("SEARCH_RESULT_LIMIT_INVALID")
    resolution = resolve_search_tool(
        "ripgrep",
        root=plugin_source_root,
        configured_path=configured_path,
        configured_sha256=configured_sha256,
    )
    selected = resolution
    results: list[dict[str, Any]] = []
    binary_failure: str | None = None
    if resolution.executable is not None:
        args = [
            "--json",
            "--fixed-strings",
            "--ignore-case",
            "--no-config",
            "--color",
            "never",
            "--max-filesize",
            str(bounds["max_file_bytes"]),
        ]
        for name in sorted(_DEFAULT_EXCLUDED_DIRS):
            args.extend(["--glob", f"!**/{name}/**"])
        for name in sorted(_DEFAULT_EXCLUDED_FILES):
            args.extend(["--glob", f"!**/{name}"])
        args.extend(["--glob", "!**/.env.*"])
        args.extend(["--", query, str(root)])
        code, stdout, _stderr, binary_failure = _run_bounded(
            resolution.executable,
            args,
            input_bytes=None,
            timeout_seconds=bounds["timeout_seconds"],
            max_output_bytes=bounds["max_process_output_bytes"],
        )
        if binary_failure is None and code in {0, 1}:
            try:
                for line in stdout.splitlines():
                    row = json.loads(line)
                    if row.get("type") != "match":
                        continue
                    data = row["data"]
                    path_text = str(data["path"]["text"])
                    line_text = str(data["lines"]["text"])
                    submatches = data.get("submatches") or []
                    column = int(submatches[0]["start"]) + 1 if submatches else 1
                    match = _safe_match(
                        root,
                        path_text,
                        int(data["line_number"]),
                        column,
                        line_text,
                    )
                    if match is not None:
                        results.append(match)
                results.sort(key=lambda row: (row["path"].casefold(), row["line"], row["column"]))
                results = results[:limit]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                binary_failure = "SEARCH_TOOL_OUTPUT_INVALID"
        elif binary_failure is None:
            binary_failure = "SEARCH_TOOL_NONZERO_EXIT"
    if resolution.executable is None or binary_failure is not None:
        selected = ToolResolution(
            resolution.tool_id,
            "DETERMINISTIC_BUILTIN_FALLBACK",
            None,
            None,
            None,
            None,
            binary_failure or resolution.reason,
            resolution.manifest_sha256,
            resolution.fallback_backend,
        )
        results = _fallback_search(root, query, limit=limit, bounds=bounds)

    receipt: dict[str, Any] = {
        "schema": INVOCATION_SCHEMA,
        "status": "PASS",
        "operation": "BOUNDED_TEXT_SEARCH",
        "capability": selected.receipt(),
        "query_sha256": sha256_bytes(query.encode("utf-8")),
        "project_root_sha256": _path_hash(root),
        "result_count": len(results),
        "limit": limit,
        "stable_order": "PATH_CASEFOLD_LINE_COLUMN",
        "secret_paths_excluded": True,
        "results_redacted": True,
        "source_mutated": False,
        "git_mutated": False,
        "lifecycle_mutated": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }
    receipt["invocation_receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return {"results": results, "receipt": receipt}
