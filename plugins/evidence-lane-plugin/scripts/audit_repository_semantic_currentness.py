"""Audit every exact Git-index member for semantic currentness.

This is deliberately separate from the byte-only fingerprint gate.  It reads
the exact stage-0 blobs from a caller-selected alternate index, parses every
parseable member, validates current Evidence Lane identity claims and local
references, and emits one per-path semantic receipt.  A stale current member
fails the audit; unchanged object identity is never a reason to skip it.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import tomllib
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "evidence-lane.repository-semantic-currentness-audit.v1"
FINAL_CLASSIFICATIONS = {
    "CURRENT",
    "HISTORICAL_ALLOWED_WITH_BOUNDARY",
    "NONSEMANTIC_BINARY_VERIFIED",
    "STALE_PURGED",
}
TEXT_SUFFIXES = {
    ".cjs",
    ".cs",
    ".css",
    ".csv",
    ".dot",
    ".example",
    ".gitattributes",
    ".gitignore",
    ".html",
    ".in",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".mmd",
    ".ps1",
    ".py",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vercelignore",
    ".yaml",
    ".yml",
}
TEXT_NAMES = {"Dockerfile", "LICENSE", "NOTICE"}
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}
NONSEMANTIC_BINARY_SUFFIXES = {".dll", ".exe", ".png", ".woff", ".woff2"}
HISTORICAL_PATH_RE = re.compile(
    r"(?:^|[/_.-])(histor(?:y|ical)|legacy|compatibility|migration|frozen|superseded)(?:[/_.-]|$)",
    re.IGNORECASE,
)
HISTORICAL_CONTEXT_RE = re.compile(
    r"histor(?:y|ical)|legacy|obsolete|superseded|negative proof|must not|not in |preserve.*historical",
    re.IGNORECASE,
)
STALE_PATH_RE = re.compile(
    r"(?:^|[/_.-])(?:codex_v200|v(?:060|070|080|130|140))(?:[/_.-]|$)",
    re.IGNORECASE,
)
STALE_TOKEN_RE = re.compile(
    r"codex-v200|evidence-lane-v200|agent/evi-v200|tunnel-runtime-v140|evidence_lane_v140|EvidenceLane-Tunnel-v140",
    re.IGNORECASE,
)
DATED_CURRENTNESS_RE = re.compile(
    r"(?:current[- ]route[- ]refresh|evidence[- ]lane[- ]current[- ]route[- ]refresh)"
    r"[^\n]*\b20\d\d-\d\d-\d\d\b",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
STRONG_REPOSITORY_PREFIXES = (
    ".github/",
    "apps/",
    "docs/",
    "github-pages/",
    "plugins/",
    "scripts/",
    "tests/",
)
STRONG_PLUGIN_PREFIXES = (
    ".codex-plugin/",
    "assets/",
    "authorities/",
    "env/",
    "hooks/",
    "manifests/",
    "mcp/",
    "schemas/",
    "scripts/",
    "sdk/",
    "skills/",
    "src/",
    "tests/",
    "toolchains/",
    "tunnel/",
    "uop/",
)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest().upper()


def _run_git(root: Path, arguments: list[str], *, index_file: Path | None = None) -> bytes:
    environment = dict(os.environ)
    if index_file is not None:
        environment["GIT_INDEX_FILE"] = str(index_file)
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=environment,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout


def _index_rows(root: Path, index_file: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for record in _run_git(root, ["ls-files", "-z", "--stage"], index_file=index_file).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split(" ")
        if stage != "0":
            raise RuntimeError("SEMANTIC_AUDIT_CONFLICTED_INDEX")
        rows.append(
            {
                "path": raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/"),
                "index_mode": mode,
                "index_object_id": object_id,
            }
        )
    rows.sort(key=lambda row: row["path"])
    if not rows or len(rows) != len({row["path"] for row in rows}):
        raise RuntimeError("SEMANTIC_AUDIT_INDEX_PATH_SET_INVALID")
    return rows


def _head_rows(root: Path) -> dict[str, dict[str, str]]:
    try:
        payload = _run_git(root, ["ls-tree", "-r", "-z", "HEAD"])
    except subprocess.CalledProcessError:
        return {}
    rows: dict[str, dict[str, str]] = {}
    for record in payload.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        if kind != "blob":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        rows[path] = {"path": path, "index_mode": mode, "index_object_id": object_id}
    return rows


def _blob_map(root: Path, object_ids: Iterable[str]) -> dict[str, bytes]:
    ordered = sorted(set(object_ids))
    if not ordered:
        return {}
    completed = subprocess.run(
        ["git", "-C", str(root), "cat-file", "--batch"],
        input=b"".join(object_id.encode("ascii") + b"\n" for object_id in ordered),
        check=True,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    payload = memoryview(completed.stdout)
    offset = 0
    blobs: dict[str, bytes] = {}
    for requested in ordered:
        newline = completed.stdout.index(b"\n", offset)
        object_id, kind, raw_size = completed.stdout[offset:newline].decode("ascii").split(" ")
        if object_id != requested or kind != "blob":
            raise RuntimeError("SEMANTIC_AUDIT_GIT_BLOB_MISMATCH")
        size = int(raw_size)
        start = newline + 1
        end = start + size
        blobs[object_id] = bytes(payload[start:end])
        if end >= len(payload) or payload[end] != 10:
            raise RuntimeError("SEMANTIC_AUDIT_GIT_BATCH_FRAMING_INVALID")
        offset = end + 1
    if offset != len(payload):
        raise RuntimeError("SEMANTIC_AUDIT_GIT_BATCH_TRAILING_BYTES")
    return blobs


def _decode_text(path: str, payload: bytes) -> str:
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"TEXT_UTF8_INVALID:{path}:{exc.start}") from exc


def _duplicate_key_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON_DUPLICATE_KEY:{key}")
        result[key] = value
    return result


def _validate_sqlite(payload: bytes) -> dict[str, Any]:
    if not payload.startswith(b"SQLite format 3\0"):
        raise ValueError("SQLITE_HEADER_INVALID")
    connection = sqlite3.connect(":memory:")
    try:
        if not hasattr(connection, "deserialize"):
            with tempfile.NamedTemporaryFile(suffix=".sqlite") as temporary:
                temporary.write(payload)
                temporary.flush()
                disk = sqlite3.connect(f"file:{Path(temporary.name).as_posix()}?mode=ro", uri=True)
                try:
                    integrity = str(disk.execute("PRAGMA integrity_check").fetchone()[0])
                    foreign_keys = list(disk.execute("PRAGMA foreign_key_check"))
                    table_count = int(disk.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
                    user_version = int(disk.execute("PRAGMA user_version").fetchone()[0])
                finally:
                    disk.close()
        else:
            connection.deserialize(payload)
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            table_count = int(connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0])
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()
    if integrity != "ok" or foreign_keys:
        raise ValueError("SQLITE_STRUCTURAL_VALIDATION_FAILED")
    return {
        "parser": "SQLITE_DESERIALIZE_OR_READONLY_TEMPFILE",
        "integrity_check": integrity,
        "foreign_key_violation_count": len(foreign_keys),
        "table_count": table_count,
        "user_version": user_version,
    }


def _binary_structure(path: str, payload: bytes) -> dict[str, Any]:
    suffix = PurePosixPath(path).suffix.casefold()
    if suffix == ".png":
        if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) < 24:
            raise ValueError("PNG_SIGNATURE_INVALID")
        return {
            "parser": "PNG_IHDR",
            "width": int.from_bytes(payload[16:20], "big"),
            "height": int.from_bytes(payload[20:24], "big"),
        }
    if suffix in {".exe", ".dll"}:
        if not payload.startswith(b"MZ"):
            raise ValueError("PE_SIGNATURE_INVALID")
        return {"parser": "PE_SIGNATURE", "mz_header": True}
    return {"parser": "EXACT_GIT_BLOB_SIGNATURE", "signature_verified": True}


def _normalize_reference(path: str, raw: str) -> str | None:
    value = raw.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
    if not value or value.startswith(("#", "http://", "https://", "mailto:", "data:", "/")):
        return None
    parent = PurePosixPath(path).parent
    parts: list[str] = []
    for part in (parent / value).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return "__PATH_ESCAPE__"
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


def _markdown_reference_findings(path: str, text: str, path_set: set[str]) -> list[str]:
    findings: list[str] = []
    for raw in MARKDOWN_LINK_RE.findall(text):
        normalized = _normalize_reference(path, raw)
        if normalized is None:
            continue
        if normalized == "__PATH_ESCAPE__" or normalized not in path_set:
            findings.append(f"BROKEN_LOCAL_MARKDOWN_REFERENCE:{raw}")
    return sorted(set(findings))


def _json_reference_findings(value: Any, path_set: set[str], plugin_prefix: str) -> list[str]:
    findings: set[str] = set()

    def declared_member_exists(candidate: str) -> bool:
        normalized = candidate.rstrip("/")
        return normalized in path_set or any(
            path.startswith(normalized + "/") for path in path_set
        )

    def visit(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for child_key, child in node.items():
                visit(child, str(child_key))
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str) and key.casefold() in {
            "dot", "json", "manifest", "mcp_binding", "mmd", "path", "schema_path", "sdk_binding"
        }:
            candidate = node.replace("\\", "/")
            resolved_candidates: list[str] = []
            if candidate.startswith(STRONG_PLUGIN_PREFIXES):
                resolved_candidates.append(plugin_prefix + candidate)
            if candidate.startswith(STRONG_REPOSITORY_PREFIXES):
                resolved_candidates.append(candidate)
            if not resolved_candidates:
                return
            if not any(declared_member_exists(item) for item in resolved_candidates):
                findings.add(f"BROKEN_DECLARED_PATH_REFERENCE:{candidate}")

    visit(value)
    return sorted(findings)


def _identity_findings(path: str, text: str) -> tuple[list[str], list[str]]:
    stale: list[str] = []
    allowed: list[str] = []
    path_has_boundary = bool(HISTORICAL_PATH_RE.search(path)) or path in {
        "apps/evidence-lane-app/app/_data/website-plan-projection.json",
        "plugins/evidence-lane-plugin/scripts/audit_repository_semantic_currentness.py",
        "tests/test_plan_steers.py",
        "tests/test_repository_semantic_currentness.py",
    }
    if STALE_PATH_RE.search(path):
        stale.append("STALE_VERSION_IDENTITY_IN_CURRENT_PATH")
    for line_number, line in enumerate(text.splitlines(), start=1):
        for match in STALE_TOKEN_RE.finditer(line):
            finding = f"STALE_ROUTE_TOKEN:{match.group(0)}:LINE_{line_number}"
            if path_has_boundary or HISTORICAL_CONTEXT_RE.search(line):
                allowed.append(finding)
            else:
                stale.append(finding)
        if DATED_CURRENTNESS_RE.search(line):
            finding = f"DATED_CURRENTNESS_CLAIM:LINE_{line_number}"
            if path_has_boundary and HISTORICAL_CONTEXT_RE.search(line):
                allowed.append(finding)
            else:
                stale.append(finding)
    return sorted(set(stale)), sorted(set(allowed))


def _text_structure(path: str, text: str) -> tuple[dict[str, Any], Any | None]:
    suffix = PurePosixPath(path).suffix.casefold()
    parsed: Any | None = None
    if suffix == ".json":
        parsed = json.loads(text, object_pairs_hook=_duplicate_key_guard)
        return {"parser": "JSON_DUPLICATE_KEY_STRICT", "root_type": type(parsed).__name__}, parsed
    if suffix == ".py":
        tree = ast.parse(text, filename=path)
        return {"parser": "PYTHON_AST", "top_level_nodes": len(tree.body)}, None
    if suffix == ".toml":
        parsed = tomllib.loads(text)
        return {"parser": "TOMLLIB", "root_type": type(parsed).__name__}, parsed
    if suffix in {".yaml", ".yml"}:
        meaningful = [
            line
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not meaningful:
            raise ValueError("YAML_EMPTY")
        if any("\t" in line[: len(line) - len(line.lstrip())] for line in meaningful):
            raise ValueError("YAML_TAB_INDENTATION_INVALID")
        structural_rows = sum(
            line.lstrip().startswith("-") or ":" in line for line in meaningful
        )
        if structural_rows == 0:
            raise ValueError("YAML_MAPPING_OR_SEQUENCE_REQUIRED")
        return {
            "parser": "YAML_INDENTATION_MAPPING_SEQUENCE_VALIDATOR",
            "meaningful_line_count": len(meaningful),
            "structural_row_count": structural_rows,
        }, None
    if suffix in {".mmd", ".dot"}:
        if not text.strip() or text.count("{") != text.count("}"):
            raise ValueError("GRAPH_TEXT_STRUCTURE_INVALID")
        return {"parser": "GRAPH_TEXT_BALANCE", "nonempty": True}, None
    if not text.strip():
        raise ValueError("TEXT_MEMBER_EMPTY")
    return {"parser": "UTF8_TEXT", "nonempty": True}, None


def _load_index_json(path: str, rows: dict[str, dict[str, str]], blobs: dict[str, bytes]) -> dict[str, Any]:
    row = rows.get(path)
    if row is None:
        raise RuntimeError(f"SEMANTIC_AUDIT_REQUIRED_REGISTRY_MISSING:{path}")
    value = json.loads(_decode_text(path, blobs[row["index_object_id"]]), object_pairs_hook=_duplicate_key_guard)
    if not isinstance(value, dict):
        raise TypeError(f"SEMANTIC_AUDIT_REQUIRED_REGISTRY_INVALID:{path}")
    return value


def build_audit(repository: Path, index_file: Path) -> dict[str, Any]:
    root = repository.resolve()
    selected_index = index_file.resolve()
    if not root.is_dir() or not (root / ".git").exists() or not selected_index.is_file():
        raise RuntimeError("SEMANTIC_AUDIT_REPOSITORY_OR_INDEX_INVALID")
    index_rows = _index_rows(root, selected_index)
    current = {row["path"]: row for row in index_rows}
    head = _head_rows(root)
    blobs = _blob_map(
        root,
        [row["index_object_id"] for row in [*index_rows, *head.values()]],
    )
    plugin_prefix = "plugins/evidence-lane-plugin/"
    plugin_manifest = _load_index_json(plugin_prefix + ".codex-plugin/plugin.json", current, blobs)
    current_version = str(plugin_manifest.get("version") or "")
    if current_version != "3.0.0":
        raise RuntimeError(f"SEMANTIC_AUDIT_PLUGIN_VERSION_INVALID:{current_version}")

    public = _load_index_json(plugin_prefix + "schemas/public-action-schemas.v001.json", current, blobs)
    skills = _load_index_json(plugin_prefix + "skills/skill-surface-registry.v1.json", current, blobs)
    hooks = _load_index_json(plugin_prefix + "hooks/hooks.json", current, blobs)
    lanes = _load_index_json(plugin_prefix + "authorities/project_sectors/lane-surface-registry.v1.json", current, blobs)
    authorities = _load_index_json(plugin_prefix + "authorities/authority-surface-registry.v1.json", current, blobs)
    tool_matrix = _load_index_json(plugin_prefix + "toolchains/tool-requirement-matrix.v1.json", current, blobs)
    hook_handler_count = sum(
        len(group.get("hooks") or [])
        for groups in (hooks.get("hooks") or {}).values()
        for group in groups
    )
    registry_checks = {
        "plugin_version": current_version == "3.0.0",
        "public_action_count": int(public.get("tool_count", -1)) == len(public.get("tools") or []) == 91,
        "skill_count": int(skills.get("skill_count", -1)) == len(skills.get("skills") or []) == 26,
        "hook_event_count": len(hooks.get("hooks") or {}) == 11,
        "hook_handler_count": hook_handler_count == 44,
        "lane_count": int(lanes.get("lane_count", -1)) == len(lanes.get("lanes") or []) == 18,
        "authority_count": int(authorities.get("authority_count", -1)) == len(authorities.get("authorities") or []) == 11,
        "tool_requirement_count": len(tool_matrix.get("requirements") or []) == 119,
    }
    if not all(registry_checks.values()):
        raise RuntimeError("SEMANTIC_AUDIT_CURRENT_REGISTRY_PARITY_FAILED")

    path_set = set(current)
    entries: list[dict[str, Any]] = []
    stale_paths: list[str] = []
    classification_counts = {name: 0 for name in sorted(FINAL_CLASSIFICATIONS)}
    for row in index_rows:
        path = row["path"]
        payload = blobs[row["index_object_id"]]
        suffix = PurePosixPath(path).suffix.casefold()
        findings: list[str] = []
        allowed_history: list[str] = []
        structure: dict[str, Any]
        media_class: str
        parsed: Any | None = None
        try:
            if suffix in SQLITE_SUFFIXES:
                media_class = "STRUCTURED_SQLITE"
                structure = _validate_sqlite(payload)
                semantic_status = "CURRENT"
            elif suffix in NONSEMANTIC_BINARY_SUFFIXES:
                media_class = "NONSEMANTIC_BINARY_ASSET"
                structure = _binary_structure(path, payload)
                semantic_status = "NONSEMANTIC_BINARY_VERIFIED"
            elif suffix in TEXT_SUFFIXES or PurePosixPath(path).name in TEXT_NAMES or b"\0" not in payload[:8000]:
                media_class = "SEMANTIC_TEXT"
                text = _decode_text(path, payload)
                structure, parsed = _text_structure(path, text)
                identity_stale, allowed_history = _identity_findings(path, text)
                findings.extend(identity_stale)
                if suffix == ".md":
                    findings.extend(_markdown_reference_findings(path, text, path_set))
                if parsed is not None:
                    findings.extend(_json_reference_findings(parsed, path_set, plugin_prefix))
                semantic_status = (
                    "HISTORICAL_ALLOWED_WITH_BOUNDARY" if allowed_history else "CURRENT"
                )
            else:
                media_class = "NONSEMANTIC_BINARY_ASSET"
                structure = _binary_structure(path, payload)
                semantic_status = "NONSEMANTIC_BINARY_VERIFIED"
        except (
            KeyError,
            OSError,
            RuntimeError,
            SyntaxError,
            TypeError,
            UnicodeError,
            ValueError,
            sqlite3.Error,
        ) as exc:  # diagnostic receipt retains the exact expected parser failure
            findings.append(f"STRUCTURAL_PARSE_FAILURE:{type(exc).__name__}:{exc}")
            structure = {"parser": "FAILED", "error_type": type(exc).__name__}
            semantic_status = "STALE_REQUIRES_CORRECTION"

        findings = sorted(set(findings))
        if findings:
            semantic_status = "STALE_REQUIRES_CORRECTION"
            stale_paths.append(path)
        if semantic_status in classification_counts:
            classification_counts[semantic_status] += 1
        entries.append(
            {
                **row,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "media_class": media_class,
                "content_inspected": True,
                "structure": structure,
                "semantic_status": semantic_status,
                "findings": findings,
                "historical_occurrences": allowed_history,
                "provenance": "EXACT_CALLER_SELECTED_ALTERNATE_INDEX_BLOB",
            }
        )

    for path in sorted(set(head) - set(current)):
        row = head[path]
        payload = blobs[row["index_object_id"]]
        classification_counts["STALE_PURGED"] += 1
        entries.append(
            {
                **row,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "media_class": "PURGED_HEAD_MEMBER",
                "content_inspected": True,
                "structure": {"parser": "EXACT_HEAD_BLOB_READBACK"},
                "semantic_status": "STALE_PURGED",
                "findings": [],
                "historical_occurrences": [],
                "provenance": "HEAD_BLOB_ABSENT_FROM_CALLER_SELECTED_ALTERNATE_INDEX",
            }
        )
    entries.sort(key=lambda row: (str(row["path"]), str(row["semantic_status"])))
    final_classifications_only = all(
        row["semantic_status"] in FINAL_CLASSIFICATIONS for row in entries
    )
    status = "PASS" if not stale_paths and final_classifications_only else "FAIL"
    core = {
        "schema": SCHEMA,
        "status": status,
        "repository": str(root),
        "source_head": _run_git(root, ["rev-parse", "HEAD"]).decode("ascii").strip(),
        "alternate_index_path_disclosed": str(selected_index),
        "alternate_tree": _run_git(root, ["write-tree"], index_file=selected_index).decode("ascii").strip(),
        "current_plugin_version": current_version,
        "registry_checks": registry_checks,
        "entry_count": len(entries),
        "current_index_path_count": len(index_rows),
        "purged_head_path_count": len(set(head) - set(current)),
        "classification_counts": classification_counts,
        "stale_path_count": len(stale_paths),
        "stale_paths": sorted(stale_paths),
        "path_set_sha256": _sha256(_canonical_json_bytes([row["path"] for row in entries])),
        "semantic_receipt_set_sha256": _sha256(_canonical_json_bytes(entries)),
        "entries": entries,
        "every_index_blob_content_inspected": len(index_rows) == sum(
            bool(row["content_inspected"]) and row["media_class"] != "PURGED_HEAD_MEMBER"
            for row in entries
        ),
        "unchanged_blob_skip_allowed": False,
        "worktree_bytes_substituted": False,
        "real_git_index_mutated": False,
        "git_ref_mutated": False,
    }
    return {**core, "receipt_sha256": _sha256(_canonical_json_bytes(core))}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--index-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--require-pass", action="store_true")
    arguments = parser.parse_args()
    receipt = build_audit(arguments.repository, arguments.index_file)
    _write_json(arguments.output.resolve(), receipt)
    print(
        json.dumps(
            {
                "status": receipt["status"],
                "entry_count": receipt["entry_count"],
                "stale_path_count": receipt["stale_path_count"],
                "alternate_tree": receipt["alternate_tree"],
                "receipt_sha256": receipt["receipt_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if receipt["status"] == "PASS" or not arguments.require_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
