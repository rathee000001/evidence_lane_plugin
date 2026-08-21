"""Bounded, provenance-first polyglot graphs over registered source authority.

This is an independent Evidence Lane implementation.  It adapts the useful
concepts proven by the supplied Graphify reference (stable identities, explicit
confidence, coverage, diff, and affected-subgraph traversal) without importing
its runtime, source, NetworkX dependency, LLM extraction, or server surfaces.
"""

from __future__ import annotations

import ast
import json
import re
import sqlite3
import tomllib
import unicodedata
import zipfile
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .source_authority import initialize_source_authority_registry
from .timeutil import utc_now

GRAPH_SCHEMA = "evidence-lane.source-graph.v1"
GRAPH_DIFF_SCHEMA = "evidence-lane.source-graph-diff.v1"
GRAPH_IMPACT_SCHEMA = "evidence-lane.source-graph-impact.v1"
EXTRACTOR_VERSION = "evidence-lane-bounded-polyglot-v1.0.0"

CONFIDENCES = ("EXTRACTED", "INFERRED", "AMBIGUOUS")
_CONFIDENCE_RANK = {value: index for index, value in enumerate(CONFIDENCES)}

_LANGUAGE_BY_SUFFIX = {
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
    ".vue": "typescript",
    ".svelte": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".groovy": "groovy",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cu": "cpp",
    ".cuh": "cpp",
    ".m": "objective-c",
    ".mm": "objective-cpp",
    ".cs": "csharp",
    ".swift": "swift",
    ".rb": "ruby",
    ".rake": "ruby",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".sql": "sql",
    ".tf": "terraform",
    ".dart": "dart",
    ".jl": "julia",
    ".ex": "elixir",
    ".exs": "elixir",
    ".f": "fortran",
    ".f90": "fortran",
    ".f95": "fortran",
    ".pas": "pascal",
    ".pp": "pascal",
    ".cls": "apex",
    ".trigger": "apex",
    ".v": "verilog",
    ".sv": "verilog",
    ".proto": "protobuf",
}

_MANIFEST_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements.in",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
}
_MANIFEST_SUFFIXES = {".csproj", ".fsproj", ".vbproj"}
_TEXT_ONLY_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".svg",
    ".mmd",
    ".dot",
}


@dataclass(frozen=True, slots=True)
class RegisteredOccurrence:
    ordinal: int
    object_id: str
    source_pointer: str
    resolved_pointer: str
    kind: str
    lane_id: str
    identity_sha256: str
    byte_sha256: str | None
    size_bytes: int | None


@dataclass(frozen=True, slots=True)
class RegisteredMember:
    occurrence: RegisteredOccurrence
    member_path: str
    size_bytes: int | None
    byte_sha256: str | None
    policy_state: str
    policy_reason: str


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _canonical_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value.replace("\\", "/")).strip()


def _member_path(value: str) -> str:
    normalized = _canonical_text(value)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix()


def _stable_id(prefix: str, value: dict[str, Any], *, length: int = 40) -> str:
    digest = sha256_bytes(canonical_json_bytes(value)).lower()
    return f"{prefix}_{digest[:length]}"


def _source_scope(occurrence: RegisteredOccurrence) -> tuple[str, str]:
    source_name = _canonical_text(Path(occurrence.source_pointer).name).casefold()
    identity = {"source_name": source_name, "lane_id": occurrence.lane_id}
    return _stable_id("scope", identity, length=32), source_name


def _language(member_path: str) -> str:
    path = PurePosixPath(member_path)
    return _LANGUAGE_BY_SUFFIX.get(path.suffix.casefold(), "")


def _is_manifest(member_path: str) -> bool:
    path = PurePosixPath(member_path)
    return (
        path.name.casefold() in _MANIFEST_NAMES
        or path.suffix.casefold() in _MANIFEST_SUFFIXES
    )


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_slice_sha256(text: str, start_line: int, end_line: int) -> str:
    lines = text.splitlines(keepends=True)
    bounded_start = max(start_line - 1, 0)
    bounded_end = min(max(end_line, start_line), len(lines))
    return sha256_bytes("".join(lines[bounded_start:bounded_end]).encode("utf-8"))


def _decode(payload: bytes) -> tuple[str | None, str | None]:
    if not payload:
        return "", "utf-8"
    if b"\x00" in payload[:8192]:
        return None, None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeError:
            continue
    return None, None


def _load_occurrences(
    registry_path: Path,
    batch_id: str,
    occurrence_ordinals: list[int] | None,
) -> tuple[str, list[RegisteredOccurrence]]:
    with _connect(registry_path) as connection:
        batch = connection.execute(
            "SELECT batch_sha256 FROM intake_batch WHERE batch_id=?", (batch_id,)
        ).fetchone()
        require(
            batch is not None,
            "SOURCE_GRAPH_BATCH_MISSING",
            "The requested Source Intake batch is not registered.",
            status="MISMATCH",
            batch_id=batch_id,
        )
        rows = list(
            connection.execute(
                """SELECT o.ordinal, s.object_id, s.source_pointer,
                s.resolved_pointer, s.kind, s.lane_id, s.identity_sha256,
                s.byte_sha256, s.size_bytes
                FROM source_occurrence o JOIN source_object s USING(object_id)
                WHERE o.batch_id=? ORDER BY o.ordinal""",
                (batch_id,),
            )
        )
    requested = occurrence_ordinals or [int(row["ordinal"]) for row in rows]
    require(
        bool(requested)
        and all(isinstance(value, int) and value >= 1 for value in requested)
        and len(set(requested)) == len(requested),
        "SOURCE_GRAPH_SELECTION_INVALID",
        "Graph selection needs one or more unique positive occurrence ordinals.",
        status="BLOCKED",
    )
    requested_set = set(requested)
    selected_rows = [row for row in rows if int(row["ordinal"]) in requested_set]
    require(
        len(selected_rows) == len(requested_set),
        "SOURCE_GRAPH_SELECTION_MISSING",
        "One or more selected occurrence ordinals are absent from the batch.",
        status="MISMATCH",
        requested=sorted(requested_set),
        matched=[int(row["ordinal"]) for row in selected_rows],
    )
    occurrences = [
        RegisteredOccurrence(
            ordinal=int(row["ordinal"]),
            object_id=str(row["object_id"]),
            source_pointer=str(row["source_pointer"]),
            resolved_pointer=str(row["resolved_pointer"]),
            kind=str(row["kind"]),
            lane_id=str(row["lane_id"]),
            identity_sha256=str(row["identity_sha256"]),
            byte_sha256=(str(row["byte_sha256"]) if row["byte_sha256"] else None),
            size_bytes=(int(row["size_bytes"]) if row["size_bytes"] is not None else None),
        )
        for row in selected_rows
    ]
    scope_names: dict[str, int] = {}
    for occurrence in occurrences:
        _, name = _source_scope(occurrence)
        require(
            name not in scope_names,
            "SOURCE_GRAPH_SCOPE_NAME_COLLISION",
            "Selected sources must have distinct normalized names for stable scope IDs.",
            status="MISMATCH",
            source_name=name,
            first_ordinal=scope_names.get(name),
            second_ordinal=occurrence.ordinal,
        )
        scope_names[name] = occurrence.ordinal
    return str(batch["batch_sha256"]), occurrences


def _members_for_occurrences(
    registry_path: Path,
    batch_id: str,
    occurrences: list[RegisteredOccurrence],
) -> tuple[list[RegisteredMember], list[dict[str, Any]]]:
    skip_receipts: list[dict[str, Any]] = []
    members: list[RegisteredMember] = []
    selected_ids = {row.object_id for row in occurrences}
    occurrence_by_id = {row.object_id: row for row in occurrences}
    with _connect(registry_path) as connection:
        skipped_ids = {
            str(row["archive_object_id"]): dict(row)
            for row in connection.execute(
                """SELECT archive_object_id, counterpart_object_id,
                counterpart_scope, matched_prefix, receipt_sha256
                FROM source_archive_receipt
                WHERE batch_id=? AND skip_status=
                'SKIP_ARCHIVE_USE_EXACT_EXTRACTED_COUNTERPART'""",
                (batch_id,),
            )
            if str(row["archive_object_id"]) in selected_ids
        }
        for object_id, row in sorted(skipped_ids.items()):
            occurrence = occurrence_by_id[object_id]
            skip_receipts.append(
                {
                    "ordinal": occurrence.ordinal,
                    "object_id": object_id,
                    "member_path": "<archive>",
                    "file_state": "SKIPPED_EXACT_EXTRACTED_COUNTERPART",
                    "reason": "DELTA067A_EXACT_COUNTERPART_PROOF",
                    "counterpart_object_id": row["counterpart_object_id"],
                    "counterpart_scope": row["counterpart_scope"],
                    "matched_prefix": row["matched_prefix"],
                    "archive_receipt_sha256": row["receipt_sha256"],
                }
            )
        for occurrence in occurrences:
            if occurrence.object_id in skipped_ids:
                continue
            if occurrence.kind == "file":
                members.append(
                    RegisteredMember(
                        occurrence=occurrence,
                        member_path="<direct-file>",
                        size_bytes=occurrence.size_bytes,
                        byte_sha256=occurrence.byte_sha256,
                        policy_state="INCLUDED",
                        policy_reason="POLICY_APPROVED",
                    )
                )
                continue
            for row in connection.execute(
                """SELECT member_path, size_bytes, sha256,
                policy_state, policy_reason FROM source_member
                WHERE object_id=? AND member_kind='file'
                ORDER BY member_path COLLATE NOCASE, member_path""",
                (occurrence.object_id,),
            ):
                members.append(
                    RegisteredMember(
                        occurrence=occurrence,
                        member_path=str(row["member_path"]),
                        size_bytes=(
                            int(row["size_bytes"])
                            if row["size_bytes"] is not None
                            else None
                        ),
                        byte_sha256=(str(row["sha256"]) if row["sha256"] else None),
                        policy_state=str(row["policy_state"]),
                        policy_reason=str(row["policy_reason"]),
                    )
                )
    return members, skip_receipts


def _read_registered_member(member: RegisteredMember) -> bytes:
    occurrence = member.occurrence
    expected_size = member.size_bytes
    expected_sha256 = member.byte_sha256
    require(
        member.policy_state == "INCLUDED"
        and expected_size is not None
        and expected_sha256 is not None,
        "SOURCE_GRAPH_MEMBER_AUTHORITY_INCOMPLETE",
        "A graph-readable member needs included size and byte authority.",
        status="BLOCKED",
        object_id=occurrence.object_id,
        member_path=member.member_path,
    )
    source = Path(occurrence.resolved_pointer).resolve()
    if occurrence.kind == "file":
        target = source
        require(
            target.is_file() and not target.is_symlink(),
            "SOURCE_GRAPH_FILE_MISSING",
            "A registered direct file is no longer a regular file.",
            status="STALE",
            source=str(target),
        )
        payload = target.read_bytes()
    elif occurrence.kind == "directory":
        root = source
        target = (root / Path(_member_path(member.member_path))).resolve()
        try:
            target.relative_to(root)
        except ValueError as error:
            raise EvidenceLaneError(
                "SOURCE_GRAPH_MEMBER_ESCAPES_ROOT",
                "A registered member path escaped its source root.",
                status="FAIL",
                details={"member_path": member.member_path},
            ) from error
        require(
            target.is_file() and not target.is_symlink(),
            "SOURCE_GRAPH_MEMBER_MISSING",
            "A registered directory member is no longer a regular file.",
            status="STALE",
            source=str(target),
        )
        payload = target.read_bytes()
    elif occurrence.kind == "zip":
        require(
            source.is_file()
            and occurrence.byte_sha256 is not None
            and sha256_file(source) == occurrence.byte_sha256,
            "SOURCE_GRAPH_ARCHIVE_CHANGED",
            "A registered archive changed before graph extraction.",
            status="STALE",
            source=str(source),
        )
        with zipfile.ZipFile(source) as archive:
            exact = [
                info
                for info in archive.infolist()
                if not info.is_dir()
                and info.filename.replace("\\", "/") == member.member_path
            ]
            require(
                len(exact) == 1,
                "SOURCE_GRAPH_ARCHIVE_MEMBER_MISMATCH",
                "The registered archive member was not found exactly once.",
                status="STALE",
                member_path=member.member_path,
                match_count=len(exact),
            )
            payload = archive.read(exact[0])
    else:
        raise EvidenceLaneError(
            "SOURCE_GRAPH_KIND_UNSUPPORTED",
            "The registered source kind cannot be read by the graph extractor.",
            status="BLOCKED",
            details={"kind": occurrence.kind},
        )
    require(
        len(payload) == expected_size and sha256_bytes(payload) == expected_sha256,
        "SOURCE_GRAPH_MEMBER_CHANGED",
        "A source member no longer matches its registered byte authority.",
        status="STALE",
        object_id=occurrence.object_id,
        member_path=member.member_path,
    )
    return payload


def _ast_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _ast_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _parse_python(text: str) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    imports: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return {
            "symbols": [],
            "imports": [],
            "calls": [],
            "dependencies": [],
            "parser_id": "python-ast-v1",
            "parse_state": "PARSE_ERROR_FILE_NODE_ONLY",
            "parse_reason": f"SyntaxError:{error.lineno or 0}",
        }

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: list[str] = []
            self.callers: list[str] = []

        def _symbol(
            self,
            node: ast.AST,
            kind: str,
            name: str,
            signature: str,
        ) -> str:
            qualified = ".".join([*self.scope, name])
            start = int(getattr(node, "lineno", 1))
            end = int(getattr(node, "end_lineno", start))
            symbols.append(
                {
                    "kind": kind,
                    "name": name,
                    "qualified_name": qualified,
                    "signature": signature,
                    "start_line": start,
                    "end_line": end,
                    "definition_sha256": _line_slice_sha256(text, start, end),
                }
            )
            return qualified

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._symbol(node, "class", node.name, node.name)
            self.scope.append(node.name)
            self.generic_visit(node)
            self.scope.pop()

        def _function(
            self, node: ast.FunctionDef | ast.AsyncFunctionDef
        ) -> None:
            arguments = [argument.arg for argument in node.args.args]
            signature = f"{node.name}({', '.join(arguments)})"
            qualified = self._symbol(
                node,
                "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function",
                node.name,
                signature,
            )
            self.scope.append(node.name)
            self.callers.append(qualified)
            self.generic_visit(node)
            self.callers.pop()
            self.scope.pop()

        visit_FunctionDef = _function
        visit_AsyncFunctionDef = _function

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                imports.append(
                    {
                        "module": alias.name,
                        "imported_name": None,
                        "alias": alias.asname,
                        "line_number": int(node.lineno),
                    }
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            module = "." * int(node.level) + (node.module or "")
            for alias in node.names:
                imports.append(
                    {
                        "module": module,
                        "imported_name": alias.name,
                        "alias": alias.asname,
                        "line_number": int(node.lineno),
                    }
                )

        def visit_Call(self, node: ast.Call) -> None:
            name = _ast_call_name(node.func)
            if name:
                calls.append(
                    {
                        "caller": self.callers[-1] if self.callers else None,
                        "target": name,
                        "line_number": int(getattr(node, "lineno", 1)),
                    }
                )
            self.generic_visit(node)

    Visitor().visit(tree)
    return {
        "symbols": symbols,
        "imports": imports,
        "calls": calls,
        "dependencies": [],
        "parser_id": "python-ast-v1",
        "parse_state": "PARSED_AST",
        "parse_reason": "PYTHON_AST_EXACT",
    }


_SYMBOL_PATTERNS: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "javascript": (
        ("class", re.compile(r"(?m)^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")),
        ("interface", re.compile(r"(?m)^\s*(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"(?m)^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")),
        ("function", re.compile(r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>")),
    ),
    "typescript": (),
    "go": (
        ("type", re.compile(r"(?m)^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+(?:struct|interface)\b")),
        ("function", re.compile(r"(?m)^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)\s*\(")),
    ),
    "rust": (
        ("type", re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ),
    "java": (
        ("type", re.compile(r"(?m)^\s*(?:public|protected|private|abstract|final|sealed|static|\s)*\b(?:class|interface|enum|record)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*(?:public|protected|private|static|final|abstract|synchronized|native|\s)+[\w<>,.?\[\]]+\s+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:throws\s+[^\{]+)?\{")),
    ),
    "kotlin": (),
    "scala": (),
    "groovy": (),
    "c": (
        ("type", re.compile(r"(?m)^\s*(?:typedef\s+)?(?:struct|union|enum)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*(?:[A-Za-z_]\w*[\s*]+)+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{")),
    ),
    "cpp": (),
    "objective-c": (),
    "objective-cpp": (),
    "csharp": (
        ("type", re.compile(r"(?m)^\s*(?:public|private|protected|internal|abstract|sealed|static|partial|\s)*\b(?:class|interface|record|struct|enum)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*(?:public|private|protected|internal|static|async|virtual|override|sealed|partial|\s)+[\w<>,.?\[\]]+\s+(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:=>|\{)")),
    ),
    "swift": (
        ("type", re.compile(r"(?m)^\s*(?:public|private|internal|open|final|\s)*(?:class|struct|enum|protocol|actor)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*(?:public|private|internal|open|static|class|mutating|async|\s)*func\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ),
    "ruby": (
        ("type", re.compile(r"(?m)^\s*(?:class|module)\s+(?P<name>[A-Za-z_:]\w*(?:::\w+)*)")),
        ("function", re.compile(r"(?m)^\s*def\s+(?:self\.)?(?P<name>[A-Za-z_]\w*[!?=]?)")),
    ),
    "php": (
        ("type", re.compile(r"(?mi)^\s*(?:(?:abstract|final|readonly)\s+)*(?:class|interface|trait|enum)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?mi)^\s*(?:(?:public|private|protected|static|final|abstract)\s+)*function\s+&?\s*(?P<name>[A-Za-z_]\w*)\s*\(")),
    ),
    "shell": (
        ("function", re.compile(r"(?m)^\s*(?:function\s+)?(?P<name>[A-Za-z_]\w*)\s*\(\s*\)\s*\{")),
    ),
    "powershell": (
        ("function", re.compile(r"(?mi)^\s*function\s+(?P<name>[A-Za-z_][\w-]*)\b")),
        ("type", re.compile(r"(?mi)^\s*class\s+(?P<name>[A-Za-z_]\w*)\b")),
    ),
    "sql": (
        ("table", re.compile(r"(?mi)\bCREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\[\"`']?(?P<name>[A-Za-z_][\w.$-]*)")),
        ("view", re.compile(r"(?mi)\bCREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+[\[\"`']?(?P<name>[A-Za-z_][\w.$-]*)")),
        ("function", re.compile(r"(?mi)\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:FUNCTION|PROCEDURE|TRIGGER)\s+[\[\"`']?(?P<name>[A-Za-z_][\w.$-]*)")),
    ),
    "terraform": (
        ("resource", re.compile(r"(?m)^\s*(?:resource|data)\s+\"[^\"]+\"\s+\"(?P<name>[^\"]+)\"")),
        ("module", re.compile(r"(?m)^\s*module\s+\"(?P<name>[^\"]+)\"")),
        ("variable", re.compile(r"(?m)^\s*(?:variable|output)\s+\"(?P<name>[^\"]+)\"")),
    ),
    "dart": (
        ("type", re.compile(r"(?m)^\s*(?:abstract\s+)?(?:class|enum|mixin|extension)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*(?:[A-Za-z_<>,?\[\]]+\s+)?(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*(?:async\s*)?\{")),
    ),
    "julia": (
        ("type", re.compile(r"(?m)^\s*(?:mutable\s+)?struct\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*function\s+(?P<name>[A-Za-z_]\w*[!.?]?)")),
    ),
    "elixir": (
        ("module", re.compile(r"(?m)^\s*defmodule\s+(?P<name>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")),
        ("function", re.compile(r"(?m)^\s*defp?\s+(?P<name>[a-z_]\w*[!?]?)")),
    ),
    "fortran": (
        ("module", re.compile(r"(?mi)^\s*module\s+(?!procedure\b)(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?mi)^\s*(?:[A-Za-z_][\w\s*(),]+\s+)?(?:subroutine|function)\s+(?P<name>[A-Za-z_]\w*)")),
    ),
    "pascal": (
        ("type", re.compile(r"(?mi)^\s*(?P<name>[A-Za-z_]\w*)\s*=\s*(?:class|record|interface)\b")),
        ("function", re.compile(r"(?mi)^\s*(?:class\s+)?(?:procedure|function|constructor|destructor)\s+(?P<name>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)")),
    ),
    "apex": (),
    "verilog": (
        ("module", re.compile(r"(?mi)^\s*(?:module|interface|package)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?mi)^\s*(?:function|task)\b[^;\n]*?\b(?P<name>[A-Za-z_]\w*)\s*(?:\(|;)")),
    ),
    "protobuf": (
        ("type", re.compile(r"(?m)^\s*(?:message|enum|service)\s+(?P<name>[A-Za-z_]\w*)")),
        ("function", re.compile(r"(?m)^\s*rpc\s+(?P<name>[A-Za-z_]\w*)\s*\(")),
    ),
}

for _alias, _base in {
    "typescript": "javascript",
    "kotlin": "java",
    "scala": "java",
    "groovy": "java",
    "cpp": "c",
    "objective-c": "c",
    "objective-cpp": "c",
    "apex": "java",
}.items():
    _SYMBOL_PATTERNS[_alias] = _SYMBOL_PATTERNS[_base]

_IMPORT_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "javascript": (
        re.compile(r"(?m)^\s*import(?:[\s\S]*?\sfrom\s+|\s*)[\"'](?P<module>[^\"']+)[\"']"),
        re.compile(r"(?m)\brequire\s*\(\s*[\"'](?P<module>[^\"']+)[\"']\s*\)"),
    ),
    "go": (re.compile(r"(?m)^\s*(?:import\s+)?(?:[A-Za-z_]\w*\s+)?\"(?P<module>[^\"]+)\""),),
    "rust": (re.compile(r"(?m)^\s*(?:pub\s+)?(?:use|mod)\s+(?P<module>[A-Za-z_][\w:]*)"),),
    "java": (re.compile(r"(?m)^\s*import\s+(?:static\s+)?(?P<module>[A-Za-z_][\w.*]+)\s*;"),),
    "c": (re.compile(r"(?m)^\s*#\s*include\s*[<\"](?P<module>[^>\"]+)[>\"]"),),
    "csharp": (re.compile(r"(?m)^\s*using\s+(?:[A-Za-z_]\w*\s*=\s*)?(?P<module>[A-Za-z_][\w.]*)\s*;"),),
    "swift": (re.compile(r"(?m)^\s*import\s+(?P<module>[A-Za-z_]\w*)"),),
    "ruby": (re.compile(r"(?m)^\s*(?:require|require_relative|load)\s*[\( ]\s*[\"'](?P<module>[^\"']+)[\"']"),),
    "php": (re.compile(r"(?mi)^\s*(?:use|require|require_once|include|include_once)\s*(?:\(?\s*)?[\"']?(?P<module>[A-Za-z_\\./-][\w\\./-]*)"),),
    "shell": (re.compile(r"(?m)^\s*(?:source|\.)\s+[\"']?(?P<module>[^\s\"']+)"),),
    "powershell": (re.compile(r"(?mi)^\s*(?:Import-Module|\.\s+)\s*[\"']?(?P<module>[^\s\"']+)"),),
    "terraform": (re.compile(r"(?m)^\s*source\s*=\s*\"(?P<module>[^\"]+)\""),),
    "dart": (re.compile(r"(?m)^\s*import\s+[\"'](?P<module>[^\"']+)[\"']"),),
    "julia": (re.compile(r"(?m)^\s*(?:using|import)\s+(?P<module>[A-Za-z_][\w.]*)"),),
    "elixir": (re.compile(r"(?m)^\s*(?:alias|import|require|use)\s+(?P<module>[A-Z][\w.]*)"),),
    "fortran": (re.compile(r"(?mi)^\s*use(?:\s*,[^:]*)?(?:::)?\s*(?P<module>[A-Za-z_]\w*)"),),
    "pascal": (re.compile(r"(?mi)^\s*uses\s+(?P<module>[^;]+);"),),
    "protobuf": (re.compile(r"(?m)^\s*import\s+(?:public\s+|weak\s+)?\"(?P<module>[^\"]+)\""),),
}
for _alias, _base in {
    "typescript": "javascript",
    "kotlin": "java",
    "scala": "java",
    "groovy": "java",
    "cpp": "c",
    "objective-c": "c",
    "objective-cpp": "c",
    "apex": "java",
}.items():
    _IMPORT_PATTERNS[_alias] = _IMPORT_PATTERNS[_base]


def _parse_regex_language(text: str, language: str) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for kind, pattern in _SYMBOL_PATTERNS.get(language, ()):
        for match in pattern.finditer(text):
            name = _canonical_text(match.group("name"))
            line = _line_number(text, match.start())
            key = (kind, name, line)
            if key in seen:
                continue
            seen.add(key)
            symbols.append(
                {
                    "kind": kind,
                    "name": name,
                    "qualified_name": name,
                    "signature": name,
                    "start_line": line,
                    "end_line": line,
                    "definition_sha256": _line_slice_sha256(text, line, line),
                }
            )
    imports: list[dict[str, Any]] = []
    seen_imports: set[tuple[str, int]] = set()
    for pattern in _IMPORT_PATTERNS.get(language, ()):
        for match in pattern.finditer(text):
            module = _canonical_text(match.group("module"))
            line = _line_number(text, match.start())
            import_key = (module, line)
            if import_key in seen_imports:
                continue
            seen_imports.add(import_key)
            imports.append(
                {
                    "module": module,
                    "imported_name": None,
                    "alias": None,
                    "line_number": line,
                }
            )
    return {
        "symbols": sorted(symbols, key=lambda row: (row["start_line"], row["kind"], row["name"])),
        "imports": sorted(imports, key=lambda row: (row["line_number"], row["module"])),
        "calls": [],
        "dependencies": [],
        "parser_id": f"evidence-lane-{language}-regex-v1",
        "parse_state": "PARSED_REGEX",
        "parse_reason": "BOUNDED_DECLARATION_AND_IMPORT_PATTERNS",
    }


def _manifest_dependencies(member_path: str, text: str) -> list[dict[str, str]]:
    path = PurePosixPath(member_path)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    dependencies: list[dict[str, str]] = []

    def add(ecosystem: str, dependency: str, constraint: object, group: str) -> None:
        exact_name = _canonical_text(str(dependency))
        if not exact_name:
            return
        dependencies.append(
            {
                "ecosystem": ecosystem,
                "name": exact_name,
                "constraint": _canonical_text(str(constraint)) if constraint else "",
                "group": group,
            }
        )

    try:
        if name == "package.json":
            payload = json.loads(text)
            for group in (
                "dependencies",
                "devDependencies",
                "peerDependencies",
                "optionalDependencies",
            ):
                values = payload.get(group, {}) if isinstance(payload, dict) else {}
                if isinstance(values, dict):
                    for dependency, constraint in sorted(values.items()):
                        add("npm", dependency, constraint, group)
        elif name in {"pyproject.toml", "cargo.toml"}:
            payload = tomllib.loads(text)
            if name == "cargo.toml":
                for group in ("dependencies", "dev-dependencies", "build-dependencies"):
                    values = payload.get(group, {})
                    if isinstance(values, dict):
                        for dependency, constraint in sorted(values.items()):
                            add("cargo", dependency, constraint, group)
            else:
                project = payload.get("project", {})
                for value in project.get("dependencies", []) if isinstance(project, dict) else []:
                    match = re.match(r"([A-Za-z0-9_.-]+)(.*)", str(value))
                    if match:
                        add("python", match.group(1), match.group(2), "project.dependencies")
                optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
                if isinstance(optional, dict):
                    for group, values in sorted(optional.items()):
                        if isinstance(values, list):
                            for value in values:
                                match = re.match(r"([A-Za-z0-9_.-]+)(.*)", str(value))
                                if match:
                                    add("python", match.group(1), match.group(2), f"optional:{group}")
        elif name.startswith("requirements") and suffix in {".txt", ".in"}:
            for line in text.splitlines():
                value = line.strip()
                if not value or value.startswith(("#", "-")):
                    continue
                match = re.match(r"([A-Za-z0-9_.-]+)(.*)", value)
                if match:
                    add("python", match.group(1), match.group(2), "requirements")
        elif name == "go.mod":
            for match in re.finditer(r"(?m)^\s*([A-Za-z0-9_.~/-]+)\s+(v[^\s]+)", text):
                add("go", match.group(1), match.group(2), "require")
        elif suffix in _MANIFEST_SUFFIXES:
            for match in re.finditer(
                r"(?i)<PackageReference\s+Include=\"([^\"]+)\"(?:\s+Version=\"([^\"]*)\")?",
                text,
            ):
                add("nuget", match.group(1), match.group(2), "PackageReference")
        elif name == "pom.xml":
            for match in re.finditer(
                r"(?is)<dependency>.*?<groupId>(.*?)</groupId>.*?<artifactId>(.*?)</artifactId>.*?(?:<version>(.*?)</version>)?.*?</dependency>",
                text,
            ):
                add("maven", f"{match.group(1)}:{match.group(2)}", match.group(3), "dependency")
        elif name in {"build.gradle", "build.gradle.kts"}:
            for match in re.finditer(
                r"(?m)^\s*(implementation|api|compileOnly|runtimeOnly|testImplementation)\s*\(?[\"']([^\"']+)[\"']",
                text,
            ):
                add("gradle", match.group(2), "", match.group(1))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError):
        return []
    return dependencies


def _parse_member(member_path: str, text: str, language: str) -> dict[str, Any]:
    if language == "python":
        result = _parse_python(text)
    elif language:
        result = _parse_regex_language(text, language)
    else:
        result = {
            "symbols": [],
            "imports": [],
            "calls": [],
            "dependencies": [],
            "parser_id": "manifest-parser-v1" if _is_manifest(member_path) else "none",
            "parse_state": "PARSED_MANIFEST" if _is_manifest(member_path) else "FILE_ONLY_NON_CODE",
            "parse_reason": "DEPENDENCY_MANIFEST" if _is_manifest(member_path) else "NO_CODE_PARSER_SELECTED",
        }
    if _is_manifest(member_path):
        result["dependencies"] = _manifest_dependencies(member_path, text)
        if not language:
            result["parse_state"] = "PARSED_MANIFEST"
    return result


def _node(
    *,
    scope_id: str,
    occurrence: RegisteredOccurrence,
    member_path: str,
    node_kind: str,
    language: str,
    label: str,
    qualified_name: str,
    start_line: int | None,
    end_line: int | None,
    extraction_authority: str,
    parser_id: str,
    semantic_identity: dict[str, Any],
    content: dict[str, Any],
) -> dict[str, Any]:
    require(
        extraction_authority in CONFIDENCES,
        "SOURCE_GRAPH_NODE_AUTHORITY_INVALID",
        "Every graph node needs one exact extraction authority.",
        status="FAIL",
        extraction_authority=extraction_authority,
    )
    identity = {
        "schema": "evidence-lane.source-graph-node-identity.v1",
        "source_scope_id": scope_id,
        **semantic_identity,
    }
    identity_sha256 = sha256_bytes(canonical_json_bytes(identity))
    node_id = f"node_{identity_sha256[:40].lower()}"
    content_sha256 = sha256_bytes(canonical_json_bytes(content))
    node_projection = {
        "identity_sha256": identity_sha256,
        "content_sha256": content_sha256,
        "node_kind": node_kind,
        "language": language,
        "label": label,
        "qualified_name": qualified_name,
        "start_line": start_line,
        "end_line": end_line,
        "extraction_authority": extraction_authority,
        "parser_id": parser_id,
    }
    return {
        "node_id": node_id,
        "source_scope_id": scope_id,
        "object_id": occurrence.object_id,
        "member_path": member_path,
        "node_kind": node_kind,
        "language": language,
        "label": label,
        "qualified_name": qualified_name,
        "start_line": start_line,
        "end_line": end_line,
        "extraction_authority": extraction_authority,
        "parser_id": parser_id,
        "identity_json": canonical_json_bytes(identity).decode("utf-8").strip(),
        "identity_sha256": identity_sha256,
        "content_sha256": content_sha256,
        "node_sha256": sha256_bytes(canonical_json_bytes(node_projection)),
    }


def _add_node(nodes: dict[str, dict[str, Any]], node: dict[str, Any]) -> str:
    node_id = str(node["node_id"])
    prior = nodes.get(node_id)
    require(
        prior is None
        or (
            prior["identity_sha256"] == node["identity_sha256"]
            and prior["node_sha256"] == node["node_sha256"]
        ),
        "SOURCE_GRAPH_NODE_ID_COLLISION",
        "One stable node ID resolved to different semantic content.",
        status="FAIL",
        node_id=node_id,
    )
    if prior is None:
        nodes[node_id] = node
    return node_id


def _external_node(
    nodes: dict[str, dict[str, Any]],
    *,
    scope_id: str,
    occurrence: RegisteredOccurrence,
    external_kind: str,
    language: str,
    name: str,
    authority: str,
) -> str:
    normalized_name = _canonical_text(name)
    node = _node(
        scope_id=scope_id,
        occurrence=occurrence,
        member_path=f"<external:{external_kind.casefold()}:{normalized_name}>",
        node_kind=external_kind,
        language=language,
        label=normalized_name,
        qualified_name=normalized_name,
        start_line=None,
        end_line=None,
        extraction_authority=authority,
        parser_id="external-reference-v1",
        semantic_identity={
            "node_kind": external_kind,
            "language": language,
            "qualified_name": normalized_name,
        },
        content={
            "reference_only": True,
            "authority": authority,
        },
    )
    return _add_node(nodes, node)


def _edge_confidence(values: list[str]) -> str:
    return max(values, key=lambda value: _CONFIDENCE_RANK[value])


def _compile_edges(raw_edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in raw_edges:
        grouped[(row["source_node_id"], row["target_node_id"], row["relation"])].append(
            row
        )
    edges: list[dict[str, Any]] = []
    for (source_id, target_id, relation), rows in sorted(grouped.items()):
        evidence_by_sha: dict[str, dict[str, Any]] = {}
        for row in rows:
            evidence = dict(row["evidence"])
            evidence_sha = sha256_bytes(canonical_json_bytes(evidence))
            evidence_by_sha[evidence_sha] = evidence
        evidence_rows = [evidence_by_sha[key] for key in sorted(evidence_by_sha)]
        confidence = _edge_confidence([str(row["confidence"]) for row in rows])
        identity = {
            "schema": "evidence-lane.source-graph-edge-identity.v1",
            "source_node_id": source_id,
            "target_node_id": target_id,
            "relation": relation,
        }
        edge_id = _stable_id("edge", identity)
        evidence_sha256 = sha256_bytes(canonical_json_bytes(evidence_rows))
        edge_projection = {
            **identity,
            "confidence": confidence,
            "evidence_sha256": evidence_sha256,
        }
        primary = evidence_rows[0]
        edges.append(
            {
                "edge_id": edge_id,
                "source_node_id": source_id,
                "target_node_id": target_id,
                "relation": relation,
                "confidence": confidence,
                "source_scope_id": primary["source_scope_id"],
                "object_id": primary["object_id"],
                "member_path": primary["member_path"],
                "line_number": primary.get("line_number"),
                "parser_id": primary["parser_id"],
                "evidence_json": canonical_json_bytes(evidence_rows)
                .decode("utf-8")
                .strip(),
                "evidence_sha256": evidence_sha256,
                "edge_sha256": sha256_bytes(canonical_json_bytes(edge_projection)),
            }
        )
    return edges


def _edge_evidence(
    *,
    scope_id: str,
    occurrence: RegisteredOccurrence,
    member_path: str,
    line_number: int | None,
    parser_id: str,
    extraction: str,
    resolution: str,
    file_sha256: str | None,
) -> dict[str, Any]:
    return {
        "source_scope_id": scope_id,
        "object_id": occurrence.object_id,
        "source_identity_sha256": occurrence.identity_sha256,
        "member_path": member_path,
        "line_number": line_number,
        "parser_id": parser_id,
        "extraction": extraction,
        "resolution": resolution,
        "file_sha256": file_sha256,
    }


def _internal_import_candidates(
    member_path: str,
    module: str,
    language: str,
    available_paths: set[str],
) -> list[str]:
    current = PurePosixPath(member_path)
    normalized_module = _canonical_text(module).strip("'\"")
    bases: list[PurePosixPath] = []
    if language == "python":
        leading = len(normalized_module) - len(normalized_module.lstrip("."))
        remainder = normalized_module.lstrip(".").replace(".", "/")
        base = current.parent
        for _ in range(max(leading - 1, 0)):
            base = base.parent
        bases.append(base / remainder if remainder else base)
        if leading == 0:
            bases.append(PurePosixPath(remainder))
    elif normalized_module.startswith(("./", "../")):
        bases.append(current.parent / normalized_module)
    else:
        bases.extend(
            [current.parent / normalized_module, PurePosixPath(normalized_module)]
        )
    suffixes = (
        "",
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".java",
        ".kt",
        ".cs",
        ".h",
        ".hpp",
        ".php",
        ".rb",
        ".swift",
        ".proto",
    )
    candidates: set[str] = set()
    for base in bases:
        raw = _member_path(base.as_posix())
        for suffix in suffixes:
            candidate = raw if not suffix or raw.casefold().endswith(suffix) else raw + suffix
            if candidate in available_paths:
                candidates.add(candidate)
        for index_name in (
            "__init__.py",
            "index.js",
            "index.ts",
            "index.tsx",
            "mod.rs",
        ):
            candidate = _member_path((PurePosixPath(raw) / index_name).as_posix())
            if candidate in available_paths:
                candidates.add(candidate)
    if not candidates and "/" not in normalized_module and "." not in normalized_module:
        basename_matches = {
            path
            for path in available_paths
            if PurePosixPath(path).stem == normalized_module
            or PurePosixPath(path).name == normalized_module
        }
        if len(basename_matches) == 1:
            candidates.update(basename_matches)
    return sorted(candidates)


def _graph_existing(
    registry_path: Path, graph_id: str
) -> dict[str, Any] | None:
    with _connect(registry_path) as connection:
        row = connection.execute(
            "SELECT receipt_json FROM source_graph_snapshot WHERE graph_id=?",
            (graph_id,),
        ).fetchone()
    if row is None:
        return None
    receipt = json.loads(str(row["receipt_json"]))
    receipt["append_status"] = "IDEMPOTENT_REUSE"
    return receipt


def _path_prefixes(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        exact = _member_path(str(value))
        require(
            bool(exact)
            and not exact.startswith(("/", "<"))
            and ".." not in PurePosixPath(exact).parts,
            "SOURCE_GRAPH_PATH_PREFIX_INVALID",
            "Graph path prefixes must be safe repository-relative paths.",
            status="BLOCKED",
            path_prefix=value,
        )
        normalized.append(exact.rstrip("/"))
    return sorted(set(normalized))


def _matches_prefix(member_path: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    exact = _member_path(member_path)
    return any(exact == prefix or exact.startswith(prefix + "/") for prefix in prefixes)


def build_registered_source_graph(
    registry_path: str | Path,
    batch_id: str,
    *,
    occurrence_ordinals: list[int] | None = None,
    member_path_prefixes: list[str] | None = None,
    max_files: int = 25_000,
    max_total_bytes: int = 1024 * 1024 * 1024,
    max_file_bytes: int = 8 * 1024 * 1024,
    max_nodes: int = 500_000,
    max_edges: int = 1_000_000,
) -> dict[str, Any]:
    """Build one deterministic graph without mutating registered source bytes."""

    require(
        1 <= max_files <= 250_000
        and 1 <= max_file_bytes <= 64 * 1024 * 1024
        and max_file_bytes <= max_total_bytes <= 8 * 1024 * 1024 * 1024
        and 1 <= max_nodes <= 2_000_000
        and 1 <= max_edges <= 4_000_000,
        "SOURCE_GRAPH_BOUNDS_INVALID",
        "Graph bounds are outside the governed finite limits.",
        status="BLOCKED",
    )
    target = initialize_source_authority_registry(registry_path)
    batch_sha256, occurrences = _load_occurrences(
        target, batch_id, occurrence_ordinals
    )
    exact_prefixes = _path_prefixes(member_path_prefixes)
    selected_ordinals = [row.ordinal for row in occurrences]
    selection_projection = [
        {
            "ordinal": row.ordinal,
            "object_id": row.object_id,
            "identity_sha256": row.identity_sha256,
            "source_scope_id": _source_scope(row)[0],
        }
        for row in occurrences
    ]
    selection_sha256 = sha256_bytes(canonical_json_bytes(selection_projection))
    configuration = {
        "extractor_version": EXTRACTOR_VERSION,
        "occurrence_ordinals": selected_ordinals,
        "member_path_prefixes": exact_prefixes,
        "max_files": max_files,
        "max_total_bytes": max_total_bytes,
        "max_file_bytes": max_file_bytes,
        "max_nodes": max_nodes,
        "max_edges": max_edges,
        "archive_policy": "SKIP_ONLY_WITH_DELTA067A_EXACT_COUNTERPART_RECEIPT",
        "source_reads": "REGISTERED_BYTES_ONLY",
    }
    graph_id = _stable_id(
        "graph",
        {
            "schema": GRAPH_SCHEMA,
            "batch_id": batch_id,
            "batch_sha256": batch_sha256,
            "selection_sha256": selection_sha256,
            "configuration": configuration,
        },
        length=32,
    )
    existing = _graph_existing(target, graph_id)
    if existing is not None:
        return existing

    members, archive_skips = _members_for_occurrences(
        target, batch_id, occurrences
    )
    registered_member_count = len(members) + len(archive_skips)
    if exact_prefixes:
        members = [
            member
            for member in members
            if _matches_prefix(member.member_path, exact_prefixes)
        ]
        archive_skips = [
            row
            for row in archive_skips
            if _matches_prefix(str(row["member_path"]), exact_prefixes)
        ]
        require(
            bool(members or archive_skips),
            "SOURCE_GRAPH_PATH_SELECTION_EMPTY",
            "No registered source member matched the declared graph path prefixes.",
            status="MISMATCH",
            member_path_prefixes=exact_prefixes,
        )
    omitted_registered_member_count = registered_member_count - (
        len(members) + len(archive_skips)
    )
    require(
        len(members) + len(archive_skips) <= max_files,
        "SOURCE_GRAPH_FILE_BOUND_EXCEEDED",
        "The selected graph sources exceed the governed file bound.",
        status="BLOCKED",
        selected_file_count=len(members) + len(archive_skips),
        max_files=max_files,
    )

    nodes: dict[str, dict[str, Any]] = {}
    raw_edges: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    parsed_records: list[dict[str, Any]] = []
    file_nodes: dict[tuple[str, str], str] = {}
    path_sets: dict[str, set[str]] = defaultdict(set)
    symbol_nodes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    symbol_lookup: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    total_read_bytes = 0

    for skip in archive_skips:
        coverage.append(
            {
                "object_id": skip["object_id"],
                "member_path": skip["member_path"],
                "language": "archive",
                "file_state": skip["file_state"],
                "parser_id": "delta067a-counterpart-proof",
                "size_bytes": None,
                "byte_sha256": None,
                "symbol_count": 0,
                "import_count": 0,
                "call_count": 0,
                "node_count": 0,
                "edge_count": 0,
                "reason": skip["reason"],
            }
        )

    for member in members:
        occurrence = member.occurrence
        scope_id, _ = _source_scope(occurrence)
        normalized_path = (
            Path(occurrence.source_pointer).name
            if member.member_path == "<direct-file>"
            else _member_path(member.member_path)
        )
        language = _language(normalized_path)
        is_manifest = _is_manifest(normalized_path)
        if member.policy_state != "INCLUDED":
            coverage.append(
                {
                    "object_id": occurrence.object_id,
                    "member_path": normalized_path,
                    "language": language or "excluded",
                    "file_state": "EXCLUDED_POLICY",
                    "parser_id": "none",
                    "size_bytes": member.size_bytes,
                    "byte_sha256": None,
                    "symbol_count": 0,
                    "import_count": 0,
                    "call_count": 0,
                    "node_count": 0,
                    "edge_count": 0,
                    "reason": member.policy_reason,
                }
            )
            continue
        require(
            member.size_bytes is not None and member.byte_sha256 is not None,
            "SOURCE_GRAPH_INCLUDED_MEMBER_UNSEALED",
            "An included source member lacks exact registered byte authority.",
            status="FAIL",
            object_id=occurrence.object_id,
            member_path=normalized_path,
        )
        assert member.size_bytes is not None
        assert member.byte_sha256 is not None
        member_size = member.size_bytes
        file_node = _node(
            scope_id=scope_id,
            occurrence=occurrence,
            member_path=normalized_path,
            node_kind="FILE",
            language=language or ("manifest" if is_manifest else "non-code"),
            label=PurePosixPath(normalized_path).name,
            qualified_name=normalized_path,
            start_line=1,
            end_line=None,
            extraction_authority="EXTRACTED",
            parser_id="registered-file-authority-v1",
            semantic_identity={"node_kind": "FILE", "member_path": normalized_path},
            content={
                "byte_sha256": member.byte_sha256,
                "size_bytes": member.size_bytes,
                "source_identity_sha256": occurrence.identity_sha256,
            },
        )
        file_node_id = _add_node(nodes, file_node)
        file_nodes[(scope_id, normalized_path)] = file_node_id
        path_sets[scope_id].add(normalized_path)

        parse_state = "FILE_ONLY_NON_CODE"
        parse_reason = "REGISTERED_FILE_NODE_ONLY"
        parser_id = "none"
        parsed: dict[str, Any] = {
            "symbols": [],
            "imports": [],
            "calls": [],
            "dependencies": [],
        }
        should_parse = bool(language or is_manifest)
        if should_parse and member_size > max_file_bytes:
            parse_state = "BOUNDED_FILE_TOO_LARGE"
            parse_reason = "MAX_FILE_BYTES"
        elif should_parse:
            require(
                total_read_bytes + member_size <= max_total_bytes,
                "SOURCE_GRAPH_TOTAL_BYTE_BOUND_EXCEEDED",
                "The graph extraction byte budget was exhausted.",
                status="BLOCKED",
                bytes_read=total_read_bytes,
                next_file_bytes=member_size,
                max_total_bytes=max_total_bytes,
            )
            payload = _read_registered_member(member)
            total_read_bytes += len(payload)
            text, _encoding = _decode(payload)
            if text is None:
                parse_state = "DECODE_ERROR_FILE_NODE_ONLY"
                parse_reason = "NO_GOVERNED_TEXT_DECODER"
            else:
                parsed = _parse_member(normalized_path, text, language)
                parse_state = str(parsed["parse_state"])
                parse_reason = str(parsed["parse_reason"])
                parser_id = str(parsed["parser_id"])
                duplicate_count: dict[tuple[str, str, str], int] = defaultdict(int)
                for symbol in parsed["symbols"]:
                    semantic_key = (
                        str(symbol["kind"]),
                        str(symbol["qualified_name"]),
                        str(symbol.get("signature") or ""),
                    )
                    overload_ordinal = duplicate_count[semantic_key]
                    duplicate_count[semantic_key] += 1
                    symbol_node = _node(
                        scope_id=scope_id,
                        occurrence=occurrence,
                        member_path=normalized_path,
                        node_kind="SYMBOL",
                        language=language,
                        label=str(symbol["name"]),
                        qualified_name=str(symbol["qualified_name"]),
                        start_line=int(symbol["start_line"]),
                        end_line=int(symbol["end_line"]),
                        extraction_authority="EXTRACTED",
                        parser_id=parser_id,
                        semantic_identity={
                            "node_kind": "SYMBOL",
                            "member_path": normalized_path,
                            "symbol_kind": symbol["kind"],
                            "qualified_name": symbol["qualified_name"],
                            "signature": symbol.get("signature") or "",
                            "overload_ordinal": overload_ordinal,
                        },
                        content={
                            "definition_sha256": symbol["definition_sha256"],
                            "start_line": symbol["start_line"],
                            "end_line": symbol["end_line"],
                        },
                    )
                    symbol_node_id = _add_node(nodes, symbol_node)
                    bare_name = str(symbol["name"])
                    symbol_nodes[(scope_id, bare_name)].append(symbol_node)
                    symbol_lookup[
                        (scope_id, normalized_path, str(symbol["qualified_name"]))
                    ].append(symbol_node_id)
                    raw_edges.append(
                        {
                            "source_node_id": file_node_id,
                            "target_node_id": symbol_node_id,
                            "relation": "CONTAINS",
                            "confidence": "EXTRACTED",
                            "evidence": _edge_evidence(
                                scope_id=scope_id,
                                occurrence=occurrence,
                                member_path=normalized_path,
                                line_number=int(symbol["start_line"]),
                                parser_id=parser_id,
                                extraction="DECLARATION",
                                resolution="SAME_FILE_SYMBOL",
                                file_sha256=member.byte_sha256,
                            ),
                        }
                    )
        coverage.append(
            {
                "object_id": occurrence.object_id,
                "member_path": normalized_path,
                "language": language or ("manifest" if is_manifest else "non-code"),
                "file_state": parse_state,
                "parser_id": parser_id,
                "size_bytes": member.size_bytes,
                "byte_sha256": member.byte_sha256,
                "symbol_count": len(parsed["symbols"]),
                "import_count": len(parsed["imports"]),
                "call_count": len(parsed["calls"]),
                "node_count": 1 + len(parsed["symbols"]),
                "edge_count": len(parsed["symbols"]),
                "reason": parse_reason,
            }
        )
        parsed_records.append(
            {
                "occurrence": occurrence,
                "scope_id": scope_id,
                "member_path": normalized_path,
                "language": language,
                "file_node_id": file_node_id,
                "file_sha256": member.byte_sha256,
                "parser_id": parser_id,
                "parsed": parsed,
            }
        )

    for record in parsed_records:
        occurrence = record["occurrence"]
        scope_id = str(record["scope_id"])
        member_path = str(record["member_path"])
        language = str(record["language"])
        parser_id = str(record["parser_id"])
        file_node_id = str(record["file_node_id"])
        file_sha256 = str(record["file_sha256"])
        available_paths = path_sets[scope_id]
        for item in record["parsed"]["imports"]:
            module = str(item["module"])
            candidates = _internal_import_candidates(
                member_path, module, language, available_paths
            )
            if len(candidates) == 1:
                target_node_id = file_nodes[(scope_id, candidates[0])]
                confidence = "EXTRACTED"
                resolution = "UNIQUE_REGISTERED_FILE"
            else:
                confidence = "AMBIGUOUS" if len(candidates) > 1 else "EXTRACTED"
                resolution = (
                    "MULTIPLE_REGISTERED_FILES"
                    if len(candidates) > 1
                    else "EXTERNAL_REFERENCE"
                )
                target_node_id = _external_node(
                    nodes,
                    scope_id=scope_id,
                    occurrence=occurrence,
                    external_kind=(
                        "AMBIGUOUS_MODULE" if len(candidates) > 1 else "EXTERNAL_MODULE"
                    ),
                    language=language,
                    name=module,
                    authority=confidence,
                )
            raw_edges.append(
                {
                    "source_node_id": file_node_id,
                    "target_node_id": target_node_id,
                    "relation": "IMPORTS",
                    "confidence": confidence,
                    "evidence": _edge_evidence(
                        scope_id=scope_id,
                        occurrence=occurrence,
                        member_path=member_path,
                        line_number=int(item["line_number"]),
                        parser_id=parser_id,
                        extraction="IMPORT_STATEMENT",
                        resolution=resolution,
                        file_sha256=file_sha256,
                    ),
                }
            )
        for item in record["parsed"]["dependencies"]:
            dependency_name = f"{item['ecosystem']}:{item['name']}"
            target_node_id = _external_node(
                nodes,
                scope_id=scope_id,
                occurrence=occurrence,
                external_kind="DEPENDENCY",
                language=str(item["ecosystem"]),
                name=dependency_name,
                authority="EXTRACTED",
            )
            raw_edges.append(
                {
                    "source_node_id": file_node_id,
                    "target_node_id": target_node_id,
                    "relation": "DEPENDS_ON",
                    "confidence": "EXTRACTED",
                    "evidence": {
                        **_edge_evidence(
                            scope_id=scope_id,
                            occurrence=occurrence,
                            member_path=member_path,
                            line_number=None,
                            parser_id="manifest-parser-v1",
                            extraction="DEPENDENCY_DECLARATION",
                            resolution="EXTERNAL_PACKAGE_IDENTITY",
                            file_sha256=file_sha256,
                        ),
                        "constraint_sha256": sha256_bytes(
                            str(item.get("constraint") or "").encode("utf-8")
                        ),
                        "dependency_group": item["group"],
                    },
                }
            )
        for item in record["parsed"]["calls"]:
            caller = item.get("caller")
            caller_candidates = (
                symbol_lookup.get((scope_id, member_path, str(caller)), [])
                if caller
                else []
            )
            source_node_id = (
                caller_candidates[0] if len(caller_candidates) == 1 else file_node_id
            )
            target_name = str(item["target"]).rsplit(".", 1)[-1]
            symbol_candidates = symbol_nodes.get((scope_id, target_name), [])
            same_file = [
                row
                for row in symbol_candidates
                if row["member_path"] == member_path
            ]
            resolved = same_file if len(same_file) == 1 else symbol_candidates
            if len(resolved) == 1:
                target_node_id = str(resolved[0]["node_id"])
                confidence = "INFERRED"
                resolution = "UNIQUE_SYMBOL_NAME_RESOLUTION"
            else:
                confidence = "AMBIGUOUS"
                resolution = (
                    "MULTIPLE_SYMBOL_CANDIDATES" if resolved else "NO_SYMBOL_CANDIDATE"
                )
                target_node_id = _external_node(
                    nodes,
                    scope_id=scope_id,
                    occurrence=occurrence,
                    external_kind="UNRESOLVED_CALL",
                    language=language,
                    name=str(item["target"]),
                    authority="AMBIGUOUS",
                )
            raw_edges.append(
                {
                    "source_node_id": source_node_id,
                    "target_node_id": target_node_id,
                    "relation": "CALLS",
                    "confidence": confidence,
                    "evidence": _edge_evidence(
                        scope_id=scope_id,
                        occurrence=occurrence,
                        member_path=member_path,
                        line_number=int(item["line_number"]),
                        parser_id=parser_id,
                        extraction="CALL_EXPRESSION",
                        resolution=resolution,
                        file_sha256=file_sha256,
                    ),
                }
            )

    edges = _compile_edges(raw_edges)
    require(
        len(nodes) <= max_nodes and len(edges) <= max_edges,
        "SOURCE_GRAPH_SIZE_BOUND_EXCEEDED",
        "The compiled graph exceeds its governed node or edge bound.",
        status="BLOCKED",
        node_count=len(nodes),
        edge_count=len(edges),
        max_nodes=max_nodes,
        max_edges=max_edges,
    )

    node_counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in nodes.values():
        if not str(row["member_path"]).startswith("<external:"):
            node_counts[(str(row["object_id"]), str(row["member_path"]))] += 1
    edge_counts: dict[tuple[str, str], int] = defaultdict(int)
    for row in edges:
        edge_counts[(str(row["object_id"]), str(row["member_path"]))] += 1
    for row in coverage:
        key = (str(row["object_id"]), str(row["member_path"]))
        row["node_count"] = node_counts.get(key, int(row["node_count"]))
        row["edge_count"] = edge_counts.get(key, int(row["edge_count"]))

    node_projection = [
        {"node_id": row["node_id"], "node_sha256": row["node_sha256"]}
        for row in sorted(nodes.values(), key=lambda item: item["node_id"])
    ]
    edge_projection = [
        {"edge_id": row["edge_id"], "edge_sha256": row["edge_sha256"]}
        for row in edges
    ]
    coverage_projection = [
        {
            key: row[key]
            for key in (
                "object_id",
                "member_path",
                "language",
                "file_state",
                "parser_id",
                "size_bytes",
                "byte_sha256",
                "symbol_count",
                "import_count",
                "call_count",
                "node_count",
                "edge_count",
                "reason",
            )
        }
        for row in sorted(
            coverage, key=lambda item: (item["object_id"], item["member_path"])
        )
    ]
    coverage_sha256 = sha256_bytes(canonical_json_bytes(coverage_projection))
    graph_root_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "selection_sha256": selection_sha256,
                "nodes": node_projection,
                "edges": edge_projection,
                "coverage_sha256": coverage_sha256,
            }
        )
    )
    gap_states = sorted(
        {
            str(row["file_state"])
            for row in coverage
            if row["file_state"]
            in {
                "BOUNDED_FILE_TOO_LARGE",
                "DECODE_ERROR_FILE_NODE_ONLY",
                "PARSE_ERROR_FILE_NODE_ONLY",
            }
        }
    )
    status = (
        "PASS_WITH_BOUNDED_GAPS"
        if gap_states
        else "PASS_BOUNDED_SELECTION"
        if exact_prefixes
        else "PASS"
    )
    counts_by_confidence = {
        confidence: sum(1 for row in edges if row["confidence"] == confidence)
        for confidence in CONFIDENCES
    }
    coverage_states: dict[str, int] = defaultdict(int)
    for row in coverage:
        coverage_states[str(row["file_state"])] += 1
    receipt_core = {
        "schema": GRAPH_SCHEMA,
        "status": status,
        "append_status": "APPENDED",
        "graph_id": graph_id,
        "batch_id": batch_id,
        "batch_sha256": batch_sha256,
        "extractor_version": EXTRACTOR_VERSION,
        "selection_sha256": selection_sha256,
        "selected_occurrence_ordinals": selected_ordinals,
        "source_count": len(occurrences),
        "path_bounded_selection": bool(exact_prefixes),
        "member_path_prefixes": exact_prefixes,
        "registered_member_count": registered_member_count,
        "omitted_registered_member_count": omitted_registered_member_count,
        "file_count": len(coverage),
        "parsed_file_count": sum(
            1
            for row in coverage
            if row["file_state"] in {"PARSED_AST", "PARSED_REGEX", "PARSED_MANIFEST"}
        ),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "edge_confidence_counts": counts_by_confidence,
        "coverage_states": dict(sorted(coverage_states.items())),
        "gap_states": gap_states,
        "bytes_read": total_read_bytes,
        "coverage_sha256": coverage_sha256,
        "graph_root_sha256": graph_root_sha256,
        "stable_identity_law": (
            "source-scope + normalized member path + semantic kind/name/signature; "
            "line numbers and content hashes are mutable content, not node identity"
        ),
        "provenance_law": (
            "Every edge stores exact registered object/member/file hash, parser, "
            "line when available, extraction tier, and resolution state"
        ),
        "source_bytes_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
        "configuration": configuration,
        "node_samples": [
            {
                key: row[key]
                for key in (
                    "node_id",
                    "node_kind",
                    "language",
                    "label",
                    "member_path",
                    "extraction_authority",
                    "node_sha256",
                )
            }
            for row in sorted(nodes.values(), key=lambda item: item["node_id"])[:20]
        ],
        "edge_samples": [
            {
                key: row[key]
                for key in (
                    "edge_id",
                    "source_node_id",
                    "target_node_id",
                    "relation",
                    "confidence",
                    "member_path",
                    "edge_sha256",
                )
            }
            for row in edges[:20]
        ],
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
    receipt = {**receipt_core, "receipt_sha256": receipt_sha256}
    with _connect(target) as connection, connection:
        connection.execute(
            """INSERT INTO source_graph_snapshot VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                graph_id,
                batch_id,
                EXTRACTOR_VERSION,
                canonical_json_bytes(configuration).decode("utf-8").strip(),
                selection_sha256,
                len(occurrences),
                len(coverage),
                receipt["parsed_file_count"],
                len(nodes),
                len(edges),
                coverage_sha256,
                graph_root_sha256,
                status,
                canonical_json_bytes(receipt).decode("utf-8").strip(),
                receipt_sha256,
                utc_now(),
            ),
        )
        connection.executemany(
            """INSERT INTO source_graph_node VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    graph_id,
                    row["node_id"],
                    row["source_scope_id"],
                    row["object_id"],
                    row["member_path"],
                    row["node_kind"],
                    row["language"],
                    row["label"],
                    row["qualified_name"],
                    row["start_line"],
                    row["end_line"],
                    row["extraction_authority"],
                    row["parser_id"],
                    row["identity_json"],
                    row["identity_sha256"],
                    row["content_sha256"],
                    row["node_sha256"],
                )
                for row in sorted(nodes.values(), key=lambda item: item["node_id"])
            ],
        )
        connection.executemany(
            """INSERT INTO source_graph_edge VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    graph_id,
                    row["edge_id"],
                    row["source_node_id"],
                    row["target_node_id"],
                    row["relation"],
                    row["confidence"],
                    row["source_scope_id"],
                    row["object_id"],
                    row["member_path"],
                    row["line_number"],
                    row["parser_id"],
                    row["evidence_json"],
                    row["evidence_sha256"],
                    row["edge_sha256"],
                )
                for row in edges
            ],
        )
        connection.executemany(
            """INSERT INTO source_graph_file_coverage VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    graph_id,
                    row["object_id"],
                    row["member_path"],
                    row["language"],
                    row["file_state"],
                    row["parser_id"],
                    row["size_bytes"],
                    row["byte_sha256"],
                    row["symbol_count"],
                    row["import_count"],
                    row["call_count"],
                    row["node_count"],
                    row["edge_count"],
                    row["reason"],
                )
                for row in coverage_projection
            ],
        )
        event = {
            "schema": "evidence-lane.source-graph-event.v1",
            "graph_id": graph_id,
            "batch_id": batch_id,
            "graph_root_sha256": graph_root_sha256,
            "receipt_sha256": receipt_sha256,
        }
        event_sha256 = sha256_bytes(canonical_json_bytes(event))
        connection.execute(
            "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"registry_{event_sha256[:32].lower()}",
                batch_id,
                "source.graph.built",
                canonical_json_bytes(event).decode("utf-8").strip(),
                event_sha256,
                utc_now(),
            ),
        )
        receipt["registry_event_sha256"] = event_sha256
        connection.execute(
            "UPDATE source_graph_snapshot SET receipt_json=? WHERE graph_id=?",
            (
                canonical_json_bytes(receipt).decode("utf-8").strip(),
                graph_id,
            ),
        )
    return receipt


def _require_graphs(
    registry_path: Path, graph_ids: list[str]
) -> dict[str, dict[str, Any]]:
    require(
        bool(graph_ids) and len(set(graph_ids)) == len(graph_ids),
        "SOURCE_GRAPH_ID_SET_INVALID",
        "Graph operations require distinct exact graph IDs.",
        status="BLOCKED",
    )
    placeholders = ",".join("?" for _ in graph_ids)
    with _connect(registry_path) as connection:
        rows = list(
            connection.execute(
                f"""SELECT graph_id, batch_id, graph_root_sha256, status,
                receipt_sha256 FROM source_graph_snapshot
                WHERE graph_id IN ({placeholders})""",
                graph_ids,
            )
        )
    found = {str(row["graph_id"]): dict(row) for row in rows}
    missing = [graph_id for graph_id in graph_ids if graph_id not in found]
    require(
        not missing,
        "SOURCE_GRAPH_SNAPSHOT_MISSING",
        "One or more exact graph snapshots are not registered.",
        status="MISMATCH",
        missing_graph_ids=missing,
    )
    return found


def diff_source_graphs(
    registry_path: str | Path,
    from_graph_id: str,
    to_graph_id: str,
    *,
    sample_limit: int = 100,
) -> dict[str, Any]:
    """Persist one deterministic semantic diff between exact graph snapshots."""

    require(
        from_graph_id != to_graph_id and 0 <= sample_limit <= 1000,
        "SOURCE_GRAPH_DIFF_ARGUMENT_INVALID",
        "A graph diff needs two different graph IDs and a bounded sample limit.",
        status="BLOCKED",
    )
    target = initialize_source_authority_registry(registry_path)
    graph_meta = _require_graphs(target, [from_graph_id, to_graph_id])
    diff_id = _stable_id(
        "graphdiff",
        {
            "schema": GRAPH_DIFF_SCHEMA,
            "from_graph_id": from_graph_id,
            "to_graph_id": to_graph_id,
        },
        length=32,
    )
    with _connect(target) as connection:
        existing = connection.execute(
            "SELECT receipt_json FROM source_graph_diff WHERE diff_id=?", (diff_id,)
        ).fetchone()
        if existing is not None:
            receipt = json.loads(str(existing["receipt_json"]))
            receipt["append_status"] = "IDEMPOTENT_REUSE"
            return receipt
        nodes: dict[str, dict[str, str]] = {}
        edges: dict[str, dict[str, str]] = {}
        for graph_id in (from_graph_id, to_graph_id):
            nodes[graph_id] = {
                str(row["node_id"]): str(row["node_sha256"])
                for row in connection.execute(
                    """SELECT node_id, node_sha256 FROM source_graph_node
                    WHERE graph_id=? ORDER BY node_id""",
                    (graph_id,),
                )
            }
            edges[graph_id] = {
                str(row["edge_id"]): str(row["edge_sha256"])
                for row in connection.execute(
                    """SELECT edge_id, edge_sha256 FROM source_graph_edge
                    WHERE graph_id=? ORDER BY edge_id""",
                    (graph_id,),
                )
            }

    def compare(
        before: dict[str, str], after: dict[str, str]
    ) -> tuple[list[str], list[str], list[str]]:
        before_ids = set(before)
        after_ids = set(after)
        return (
            sorted(after_ids - before_ids),
            sorted(before_ids - after_ids),
            sorted(
                item
                for item in before_ids & after_ids
                if before[item] != after[item]
            ),
        )

    added_nodes, removed_nodes, changed_nodes = compare(
        nodes[from_graph_id], nodes[to_graph_id]
    )
    added_edges, removed_edges, changed_edges = compare(
        edges[from_graph_id], edges[to_graph_id]
    )
    projection = {
        "schema": GRAPH_DIFF_SCHEMA,
        "from_graph_id": from_graph_id,
        "to_graph_id": to_graph_id,
        "from_graph_root_sha256": graph_meta[from_graph_id]["graph_root_sha256"],
        "to_graph_root_sha256": graph_meta[to_graph_id]["graph_root_sha256"],
        "counts": {
            "added_nodes": len(added_nodes),
            "removed_nodes": len(removed_nodes),
            "changed_nodes": len(changed_nodes),
            "added_edges": len(added_edges),
            "removed_edges": len(removed_edges),
            "changed_edges": len(changed_edges),
        },
        "samples": {
            "added_nodes": added_nodes[:sample_limit],
            "removed_nodes": removed_nodes[:sample_limit],
            "changed_nodes": changed_nodes[:sample_limit],
            "added_edges": added_edges[:sample_limit],
            "removed_edges": removed_edges[:sample_limit],
            "changed_edges": changed_edges[:sample_limit],
        },
        "samples_truncated": any(
            len(items) > sample_limit
            for items in (
                added_nodes,
                removed_nodes,
                changed_nodes,
                added_edges,
                removed_edges,
                changed_edges,
            )
        ),
    }
    projection_sha256 = sha256_bytes(canonical_json_bytes(projection))
    receipt_core = {
        **projection,
        "status": "PASS",
        "append_status": "APPENDED",
        "diff_id": diff_id,
        "projection_sha256": projection_sha256,
        "source_bytes_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
    receipt = {**receipt_core, "receipt_sha256": receipt_sha256}
    with _connect(target) as connection, connection:
        connection.execute(
            """INSERT INTO source_graph_diff VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                diff_id,
                from_graph_id,
                to_graph_id,
                len(added_nodes),
                len(removed_nodes),
                len(changed_nodes),
                len(added_edges),
                len(removed_edges),
                len(changed_edges),
                canonical_json_bytes(projection).decode("utf-8").strip(),
                projection_sha256,
                canonical_json_bytes(receipt).decode("utf-8").strip(),
                receipt_sha256,
                utc_now(),
            ),
        )
        event = {
            "schema": "evidence-lane.source-graph-diff-event.v1",
            "diff_id": diff_id,
            "projection_sha256": projection_sha256,
            "receipt_sha256": receipt_sha256,
        }
        event_sha256 = sha256_bytes(canonical_json_bytes(event))
        connection.execute(
            "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"registry_{event_sha256[:32].lower()}",
                str(graph_meta[to_graph_id]["batch_id"]),
                "source.graph.diffed",
                canonical_json_bytes(event).decode("utf-8").strip(),
                event_sha256,
                utc_now(),
            ),
        )
        receipt["registry_event_sha256"] = event_sha256
        connection.execute(
            "UPDATE source_graph_diff SET receipt_json=? WHERE diff_id=?",
            (canonical_json_bytes(receipt).decode("utf-8").strip(), diff_id),
        )
    return receipt


def source_graph_impact(
    registry_path: str | Path,
    graph_id: str,
    seed_node_ids: list[str],
    *,
    relations: list[str] | None = None,
    direction: str = "UPSTREAM",
    max_depth: int = 3,
    max_nodes: int = 1000,
) -> dict[str, Any]:
    """Return a deterministic bounded affected subgraph from exact node IDs."""

    target = initialize_source_authority_registry(registry_path)
    graph_meta = _require_graphs(target, [graph_id])
    exact_seeds = [str(value).strip() for value in seed_node_ids]
    exact_relations = sorted(
        {
            str(value).strip().upper()
            for value in (
                relations
                or ["CALLS", "IMPORTS", "DEPENDS_ON", "CONTAINS", "USES"]
            )
            if str(value).strip()
        }
    )
    exact_direction = direction.strip().upper()
    require(
        bool(exact_seeds)
        and all(value.startswith("node_") for value in exact_seeds)
        and len(set(exact_seeds)) == len(exact_seeds)
        and bool(exact_relations)
        and exact_direction in {"UPSTREAM", "DOWNSTREAM", "BOTH"}
        and 0 <= max_depth <= 8
        and 1 <= max_nodes <= 10_000,
        "SOURCE_GRAPH_IMPACT_ARGUMENT_INVALID",
        "Impact traversal needs exact unique node IDs, allowed direction, and finite bounds.",
        status="BLOCKED",
    )
    with _connect(target) as connection:
        node_rows = {
            str(row["node_id"]): dict(row)
            for row in connection.execute(
                """SELECT node_id, node_kind, language, label, qualified_name,
                member_path, node_sha256 FROM source_graph_node
                WHERE graph_id=? ORDER BY node_id""",
                (graph_id,),
            )
        }
        missing = [seed for seed in exact_seeds if seed not in node_rows]
        require(
            not missing,
            "SOURCE_GRAPH_IMPACT_SEED_MISSING",
            "Every impact seed must be an exact node in the selected graph.",
            status="MISMATCH",
            missing_seed_node_ids=missing,
        )
        edge_rows = [
            dict(row)
            for row in connection.execute(
                """SELECT edge_id, source_node_id, target_node_id, relation,
                confidence, member_path, line_number, edge_sha256
                FROM source_graph_edge WHERE graph_id=?
                ORDER BY edge_id""",
                (graph_id,),
            )
            if str(row["relation"]) in exact_relations
        ]
    forward: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reverse: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edge_rows:
        forward[str(edge["source_node_id"])].append(edge)
        reverse[str(edge["target_node_id"])].append(edge)
    for rows in (*forward.values(), *reverse.values()):
        rows.sort(key=lambda row: row["edge_id"])

    seen = set(exact_seeds)
    queue: deque[tuple[str, int]] = deque((seed, 0) for seed in exact_seeds)
    hits: list[dict[str, Any]] = []
    traversed_edges: set[str] = set()
    truncated = False
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        candidates: list[tuple[str, dict[str, Any], str]] = []
        if exact_direction in {"DOWNSTREAM", "BOTH"}:
            candidates.extend(
                (str(edge["target_node_id"]), edge, "DOWNSTREAM")
                for edge in forward.get(current, [])
            )
        if exact_direction in {"UPSTREAM", "BOTH"}:
            candidates.extend(
                (str(edge["source_node_id"]), edge, "UPSTREAM")
                for edge in reverse.get(current, [])
            )
        for neighbor, edge, traversed_direction in sorted(
            candidates, key=lambda row: (row[1]["edge_id"], row[0], row[2])
        ):
            traversed_edges.add(str(edge["edge_id"]))
            if neighbor in seen:
                continue
            if len(seen) >= max_nodes:
                truncated = True
                queue.clear()
                break
            seen.add(neighbor)
            queue.append((neighbor, depth + 1))
            hits.append(
                {
                    "node_id": neighbor,
                    "depth": depth + 1,
                    "via_edge_id": edge["edge_id"],
                    "via_relation": edge["relation"],
                    "via_confidence": edge["confidence"],
                    "traversed_direction": traversed_direction,
                    "evidence_member_path": edge["member_path"],
                    "evidence_line_number": edge["line_number"],
                }
            )
    ordered_nodes = [
        {
            **{key: node_rows[node_id][key] for key in (
                "node_id",
                "node_kind",
                "language",
                "label",
                "qualified_name",
                "member_path",
                "node_sha256",
            )},
            "depth": 0,
            "seed": True,
        }
        for node_id in exact_seeds
    ]
    ordered_nodes.extend(
        {
            **{key: node_rows[hit["node_id"]][key] for key in (
                "node_id",
                "node_kind",
                "language",
                "label",
                "qualified_name",
                "member_path",
                "node_sha256",
            )},
            "depth": hit["depth"],
            "seed": False,
            "via_edge_id": hit["via_edge_id"],
            "via_relation": hit["via_relation"],
            "via_confidence": hit["via_confidence"],
            "traversed_direction": hit["traversed_direction"],
            "evidence_member_path": hit["evidence_member_path"],
            "evidence_line_number": hit["evidence_line_number"],
        }
        for hit in sorted(hits, key=lambda row: (row["depth"], row["node_id"]))
    )
    projection = {
        "schema": GRAPH_IMPACT_SCHEMA,
        "graph_id": graph_id,
        "graph_root_sha256": graph_meta[graph_id]["graph_root_sha256"],
        "seed_node_ids": exact_seeds,
        "relations": exact_relations,
        "direction": exact_direction,
        "max_depth": max_depth,
        "max_nodes": max_nodes,
        "node_count": len(ordered_nodes),
        "edge_count": len(traversed_edges),
        "truncated": truncated,
        "nodes": ordered_nodes,
        "traversed_edge_ids": sorted(traversed_edges),
    }
    projection_sha256 = sha256_bytes(canonical_json_bytes(projection))
    impact_id = _stable_id(
        "impact",
        {
            "graph_id": graph_id,
            "projection_sha256": projection_sha256,
        },
        length=32,
    )
    with _connect(target) as connection:
        existing = connection.execute(
            "SELECT receipt_json FROM source_graph_impact WHERE impact_id=?",
            (impact_id,),
        ).fetchone()
        if existing is not None:
            receipt = json.loads(str(existing["receipt_json"]))
            receipt["append_status"] = "IDEMPOTENT_REUSE"
            return receipt
    receipt_core = {
        **projection,
        "status": "PASS_BOUNDED" if truncated else "PASS",
        "append_status": "APPENDED",
        "impact_id": impact_id,
        "projection_sha256": projection_sha256,
        "source_bytes_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
    receipt = {**receipt_core, "receipt_sha256": receipt_sha256}
    with _connect(target) as connection, connection:
        connection.execute(
            """INSERT INTO source_graph_impact VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                impact_id,
                graph_id,
                canonical_json_bytes(exact_seeds).decode("utf-8").strip(),
                canonical_json_bytes(exact_relations).decode("utf-8").strip(),
                exact_direction,
                max_depth,
                max_nodes,
                canonical_json_bytes(projection).decode("utf-8").strip(),
                projection_sha256,
                canonical_json_bytes(receipt).decode("utf-8").strip(),
                receipt_sha256,
                utc_now(),
            ),
        )
        event = {
            "schema": "evidence-lane.source-graph-impact-event.v1",
            "impact_id": impact_id,
            "graph_id": graph_id,
            "projection_sha256": projection_sha256,
            "receipt_sha256": receipt_sha256,
        }
        event_sha256 = sha256_bytes(canonical_json_bytes(event))
        connection.execute(
            "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"registry_{event_sha256[:32].lower()}",
                str(graph_meta[graph_id]["batch_id"]),
                "source.graph.impact.traversed",
                canonical_json_bytes(event).decode("utf-8").strip(),
                event_sha256,
                utc_now(),
            ),
        )
        receipt["registry_event_sha256"] = event_sha256
        connection.execute(
            "UPDATE source_graph_impact SET receipt_json=? WHERE impact_id=?",
            (canonical_json_bytes(receipt).decode("utf-8").strip(), impact_id),
        )
    return receipt
