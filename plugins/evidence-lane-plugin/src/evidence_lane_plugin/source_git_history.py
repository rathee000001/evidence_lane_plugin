"""Bounded, read-only Git object, patch, and graph-impact evidence.

The historical universal brain already provides a content-addressed full-text
history index.  This module adds the forensic surface needed by governed Source
Intake: refs, every reachable commit and parent, exact object hashes, per-commit
trees, per-parent raw changes, rename edges, hunk coordinates, changed-line
hashes, and a receipt binding changed paths to a registered semantic graph.

No Git command in this module writes, fetches, checks out, or invokes a hook.
Historical content is hashed but never copied into the Source Authority store.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess  # nosec B404
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from .errors import EvidenceLaneError, require
from .git_adapter import resolve_git_executable
from .hashing import canonical_json_bytes, sha256_bytes
from .redaction import redact_text
from .source_authority import (
    SourceAuthoritySpec,
    freeze_source_authority,
    initialize_source_authority_registry,
)
from .source_graph import source_graph_impact
from .source_policy import path_exclusion_reason, redact_known_environment_secrets
from .timeutil import utc_now

GIT_HISTORY_SCHEMA = "evidence-lane.source-git-history.v1"
GIT_IMPACT_SCHEMA = "evidence-lane.source-git-impact.v1"
EXTRACTOR_VERSION = "evidence-lane-git-forensics-v1.0.0"

_SHA_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_RAW_CHANGE_RE = re.compile(
    rb"^:([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}|[0-9a-f]{64}) "
    rb"([0-9a-f]{40}|[0-9a-f]{64}) ([A-Z])([0-9]{0,3})$"
)
_HUNK_RE = re.compile(
    rb"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)


@dataclass(frozen=True, slots=True)
class _PathEvidence:
    display: str
    path_sha256: str
    policy_reason: str | None
    git_argument: str | None


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_LAZY_FETCH": "1",
        }
    )
    return environment


def _git(
    root: Path,
    *arguments: str,
    timeout: int = 180,
    max_output_bytes: int = 512 * 1024 * 1024,
) -> bytes:
    completed = subprocess.run(  # nosec B603
        [resolve_git_executable(root), "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=_git_environment(),
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    if completed.returncode != 0:
        raise EvidenceLaneError(
            "SOURCE_GIT_COMMAND_FAILED",
            "A fixed read-only Git evidence command failed.",
            status="BLOCKED",
            details={
                "argv": ["git", *arguments],
                "returncode": completed.returncode,
                "stderr_tail": completed.stderr.decode(
                    "utf-8", errors="replace"
                )[-4000:],
            },
        )
    require(
        len(completed.stdout) <= max_output_bytes,
        "SOURCE_GIT_COMMAND_OUTPUT_BOUND_EXCEEDED",
        "A Git evidence command exceeded its finite output bound.",
        status="BLOCKED",
        argv=["git", *arguments],
        output_bytes=len(completed.stdout),
        max_output_bytes=max_output_bytes,
    )
    return completed.stdout


def _stable_id(prefix: str, projection: dict[str, Any]) -> str:
    digest = sha256_bytes(canonical_json_bytes(projection)).lower()
    return f"{prefix}_{digest[:32]}"


def _root(rows: list[dict[str, Any]]) -> str:
    return sha256_bytes(canonical_json_bytes(rows))


def _path_evidence(payload: bytes) -> _PathEvidence:
    path_sha256 = sha256_bytes(payload)
    try:
        exact = payload.decode("utf-8", errors="strict").replace("\\", "/")
    except UnicodeDecodeError:
        return _PathEvidence(
            display=f"<non-utf8-path:{path_sha256}>",
            path_sha256=path_sha256,
            policy_reason="NON_UTF8_PATH_HASH_ONLY",
            git_argument=None,
        )
    reason = path_exclusion_reason(exact)
    if reason is not None:
        return _PathEvidence(
            display=f"<policy-excluded-path:{path_sha256}>",
            path_sha256=path_sha256,
            policy_reason=reason,
            git_argument=None,
        )
    return _PathEvidence(
        display=exact,
        path_sha256=path_sha256,
        policy_reason=None,
        git_argument=exact,
    )


def _load_registered_repository(
    registry_path: Path,
    batch_id: str,
    occurrence_ordinal: int,
) -> tuple[str, dict[str, Any], Path]:
    with _connect(registry_path) as connection:
        batch = connection.execute(
            "SELECT batch_sha256 FROM intake_batch WHERE batch_id=?", (batch_id,)
        ).fetchone()
        row = connection.execute(
            """SELECT o.ordinal, o.lane_id, s.object_id, s.source_pointer,
            s.resolved_pointer, s.kind, s.identity_sha256, s.member_count,
            s.member_path_size_sha256, s.content_merkle_sha256
            FROM source_occurrence o JOIN source_object s USING(object_id)
            WHERE o.batch_id=? AND o.ordinal=?""",
            (batch_id, occurrence_ordinal),
        ).fetchone()
    require(
        batch is not None and row is not None,
        "SOURCE_GIT_OCCURRENCE_MISSING",
        "The selected Source Intake occurrence is not registered.",
        status="MISMATCH",
        batch_id=batch_id,
        occurrence_ordinal=occurrence_ordinal,
    )
    assert batch is not None
    assert row is not None
    require(
        str(row["kind"]) == "directory",
        "SOURCE_GIT_OCCURRENCE_NOT_DIRECTORY",
        "Exact Git history requires a registered directory occurrence.",
        status="BLOCKED",
        kind=row["kind"],
    )
    root = Path(str(row["resolved_pointer"])).resolve()
    require(
        root.is_dir() and (root / ".git").exists(),
        "SOURCE_GIT_METADATA_MISSING",
        "The registered directory has no local Git metadata; extracted source is not history.",
        status="MISMATCH",
        source=str(root),
    )
    refreshed = freeze_source_authority(
        SourceAuthoritySpec(
            source=str(root),
            ordinal=occurrence_ordinal,
            lane_id=str(row["lane_id"]),
        )
    )
    comparison = {
        "identity_sha256": (
            str(row["identity_sha256"]),
            refreshed.identity_sha256,
        ),
        "member_count": (int(row["member_count"]), refreshed.member_count),
        "member_path_size_sha256": (
            str(row["member_path_size_sha256"]),
            refreshed.member_path_size_sha256,
        ),
        "content_merkle_sha256": (
            str(row["content_merkle_sha256"]),
            refreshed.content_merkle_sha256,
        ),
    }
    mismatches = {
        key: {"registered": registered, "current": current}
        for key, (registered, current) in comparison.items()
        if registered != current
    }
    require(
        not mismatches,
        "SOURCE_GIT_REGISTERED_BYTES_CHANGED",
        "The repository worktree no longer matches its registered Source Intake authority.",
        status="STALE",
        mismatches=mismatches,
    )
    inside = _git(root, "rev-parse", "--is-inside-work-tree").decode(
        "ascii", errors="strict"
    ).strip()
    require(
        inside == "true",
        "SOURCE_GIT_NOT_WORKTREE",
        "The selected directory is not a Git worktree.",
        status="MISMATCH",
    )
    return str(batch["batch_sha256"]), dict(row), root


def _refs(root: Path, max_refs: int) -> list[dict[str, Any]]:
    payload = _git(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    )
    rows: list[dict[str, Any]] = []
    for line in payload.splitlines():
        if not line:
            continue
        fields = (line.split(b"\x00") + [b"", b"", b"", b"", b""])[:5]
        ref_name, object_sha, object_type, peeled_sha, peeled_type = [
            field.decode("utf-8", errors="strict") for field in fields
        ]
        require(
            bool(ref_name)
            and bool(_SHA_RE.fullmatch(object_sha))
            and object_type in {"blob", "tree", "commit", "tag"}
            and (not peeled_sha or bool(_SHA_RE.fullmatch(peeled_sha))),
            "SOURCE_GIT_REF_INVALID",
            "Git returned an invalid ref evidence row.",
            status="FAIL",
            ref_name=ref_name,
        )
        projection = {
            "ref_name": ref_name,
            "object_sha": object_sha,
            "object_type": object_type,
            "peeled_sha": peeled_sha or None,
            "peeled_type": peeled_type or None,
        }
        rows.append({**projection, "ref_sha256": sha256_bytes(canonical_json_bytes(projection))})
    require(
        len(rows) <= max_refs,
        "SOURCE_GIT_REF_BOUND_EXCEEDED",
        "The repository exceeds the governed ref bound.",
        status="BLOCKED",
        ref_count=len(rows),
        max_refs=max_refs,
    )
    return sorted(rows, key=lambda item: item["ref_name"])


def _commit_metadata(root: Path, commit_sha: str, ordinal: int) -> dict[str, Any]:
    raw = _git(
        root,
        "show",
        "-s",
        "--no-show-signature",
        "--format=%H%x00%P%x00%T%x00%aI%x00%cI%x00%an%x00%ae%x00%cn%x00%ce%x00%B",
        commit_sha,
    )
    fields = raw.split(b"\x00", 9)
    require(
        len(fields) == 10,
        "SOURCE_GIT_COMMIT_METADATA_INVALID",
        "Git returned an unexpected commit metadata shape.",
        status="FAIL",
        commit_sha=commit_sha,
    )
    decoded = [field.decode("utf-8", errors="replace") for field in fields]
    exact_commit = decoded[0].strip()
    parents = [item for item in decoded[1].strip().split() if item]
    tree_sha = decoded[2].strip()
    require(
        exact_commit == commit_sha
        and bool(_SHA_RE.fullmatch(tree_sha))
        and all(_SHA_RE.fullmatch(parent) for parent in parents),
        "SOURCE_GIT_COMMIT_IDENTITY_INVALID",
        "Git returned an invalid commit, tree, or parent identity.",
        status="FAIL",
        commit_sha=commit_sha,
    )
    message_bytes = fields[9].rstrip(b"\r\n")
    message = redact_text(
        redact_known_environment_secrets(message_bytes.decode("utf-8", errors="replace"))
    )
    author = {"name": decoded[5], "email": decoded[6]}
    committer = {"name": decoded[7], "email": decoded[8]}
    return {
        "commit_sha": commit_sha,
        "ordinal": ordinal,
        "parents": parents,
        "tree_sha": tree_sha,
        "authored_at": decoded[3].strip(),
        "committed_at": decoded[4].strip(),
        "author_sha256": sha256_bytes(canonical_json_bytes(author)),
        "committer_sha256": sha256_bytes(canonical_json_bytes(committer)),
        "message_redacted": message,
        "message_sha256": sha256_bytes(message_bytes),
        "parent_count": len(parents),
    }


def _tree_entries(
    root: Path,
    commit_sha: str,
    *,
    remaining_entries: int,
) -> list[dict[str, Any]]:
    payload = _git(root, "ls-tree", "-r", "-t", "-z", "--full-tree", commit_sha)
    records = [record for record in payload.split(b"\x00") if record]
    require(
        len(records) <= remaining_entries,
        "SOURCE_GIT_TREE_ENTRY_BOUND_EXCEEDED",
        "Full commit trees exceed the governed entry bound.",
        status="BLOCKED",
        commit_sha=commit_sha,
        next_entries=len(records),
        remaining_entries=remaining_entries,
    )
    rows: list[dict[str, Any]] = []
    for ordinal, record in enumerate(records):
        try:
            header, raw_path = record.split(b"\t", 1)
            mode, object_type, object_sha = header.decode("ascii").split(" ", 2)
        except (ValueError, UnicodeError) as error:
            raise EvidenceLaneError(
                "SOURCE_GIT_TREE_ENTRY_INVALID",
                "Git returned an invalid tree entry.",
                status="FAIL",
                details={"commit_sha": commit_sha, "ordinal": ordinal},
            ) from error
        path = _path_evidence(raw_path)
        projection = {
            "commit_sha": commit_sha,
            "entry_ordinal": ordinal,
            "member_path": path.display,
            "path_sha256": path.path_sha256,
            "path_policy_reason": path.policy_reason,
            "mode": mode,
            "object_type": object_type,
            "object_sha": object_sha,
        }
        rows.append(
            {
                **projection,
                "entry_sha256": sha256_bytes(canonical_json_bytes(projection)),
            }
        )
    return rows


def _raw_changes(payload: bytes) -> list[dict[str, Any]]:
    fields = payload.split(b"\x00")
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        if not fields[index]:
            index += 1
            continue
        match = _RAW_CHANGE_RE.fullmatch(fields[index])
        require(
            match is not None,
            "SOURCE_GIT_RAW_CHANGE_INVALID",
            "Git returned an invalid raw change header.",
            status="FAIL",
            header_sha256=sha256_bytes(fields[index]),
        )
        index += 1
        assert match is not None
        status_code = match.group(5).decode("ascii")
        score_text = match.group(6).decode("ascii")
        path_count = 2 if status_code in {"R", "C"} else 1
        require(
            index + path_count <= len(fields),
            "SOURCE_GIT_RAW_CHANGE_PATH_MISSING",
            "Git omitted a path from a raw change record.",
            status="FAIL",
        )
        if path_count == 2:
            raw_prior = fields[index]
            raw_path = fields[index + 1]
        else:
            raw_prior = None
            raw_path = fields[index]
        index += path_count
        prior = _path_evidence(raw_prior) if raw_prior is not None else None
        path = _path_evidence(raw_path)
        rows.append(
            {
                "status_code": status_code,
                "similarity_score": int(score_text) if score_text else None,
                "prior_path": prior.display if prior is not None else None,
                "prior_path_sha256": prior.path_sha256 if prior is not None else None,
                "prior_path_policy_reason": prior.policy_reason if prior is not None else None,
                "member_path": path.display,
                "path_sha256": path.path_sha256,
                "path_policy_reason": path.policy_reason,
                "old_mode": match.group(1).decode("ascii"),
                "new_mode": match.group(2).decode("ascii"),
                "old_object_sha": match.group(3).decode("ascii"),
                "new_object_sha": match.group(4).decode("ascii"),
                "_git_arguments": [
                    item
                    for item in (
                        prior.git_argument if prior is not None else None,
                        path.git_argument,
                    )
                    if item is not None
                ],
            }
        )
    return rows


def _numstat(payload: bytes) -> dict[tuple[str | None, str], tuple[int | None, int | None, bool]]:
    fields = payload.split(b"\x00")
    rows: dict[tuple[str | None, str], tuple[int | None, int | None, bool]] = {}
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        parts = record.split(b"\t", 2)
        require(
            len(parts) == 3,
            "SOURCE_GIT_NUMSTAT_INVALID",
            "Git returned an invalid numstat row.",
            status="FAIL",
            row_sha256=sha256_bytes(record),
        )
        additions = None if parts[0] == b"-" else int(parts[0])
        deletions = None if parts[1] == b"-" else int(parts[1])
        binary = additions is None or deletions is None
        if parts[2]:
            prior = None
            path = _path_evidence(parts[2]).display
        else:
            require(
                index + 1 < len(fields),
                "SOURCE_GIT_NUMSTAT_RENAME_PATH_MISSING",
                "Git omitted paths from a rename numstat row.",
                status="FAIL",
            )
            prior = _path_evidence(fields[index]).display
            path = _path_evidence(fields[index + 1]).display
            index += 2
        rows[(prior, path)] = (additions, deletions, binary)
    return rows


def _comparison_payloads(
    root: Path,
    commit_sha: str,
    parent_sha: str | None,
) -> tuple[bytes, bytes]:
    common = (
        "--no-commit-id",
        "-r",
        "-z",
        "-M50%",
    )
    if parent_sha is None:
        raw = _git(
            root,
            "diff-tree",
            "--root",
            "--raw",
            "--abbrev=40",
            *common,
            commit_sha,
        )
        numbers = _git(root, "diff-tree", "--root", "--numstat", *common, commit_sha)
    else:
        raw = _git(
            root,
            "diff-tree",
            "--raw",
            "--abbrev=40",
            *common,
            parent_sha,
            commit_sha,
        )
        numbers = _git(
            root,
            "diff-tree",
            "--numstat",
            *common,
            parent_sha,
            commit_sha,
        )
    return raw, numbers


def _patch(
    root: Path,
    commit_sha: str,
    parent_sha: str | None,
    git_arguments: list[str],
    max_output_bytes: int,
) -> bytes:
    require(
        bool(git_arguments),
        "SOURCE_GIT_PATCH_PATH_REQUIRED",
        "A patch read requires at least one policy-approved path.",
        status="FAIL",
    )
    options = (
        "--unified=0",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames=50%",
    )
    if parent_sha is None:
        return _git(
            root,
            "show",
            "--format=",
            "--root",
            *options,
            commit_sha,
            "--",
            *git_arguments,
            max_output_bytes=max_output_bytes,
        )
    return _git(
        root,
        "diff",
        *options,
        parent_sha,
        commit_sha,
        "--",
        *git_arguments,
        max_output_bytes=max_output_bytes,
    )


def _parse_patch(payload: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    hunks: list[dict[str, Any]] = []
    changed_lines: list[dict[str, Any]] = []
    current_hunk: dict[str, Any] | None = None
    old_line = 0
    new_line = 0
    current_line_ordinal = 0
    for raw_line in payload.splitlines(keepends=True):
        header = raw_line.rstrip(b"\r\n")
        match = _HUNK_RE.match(header)
        if match is not None:
            old_start = int(match.group(1))
            old_count = int(match.group(2) or b"1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or b"1")
            current_hunk = {
                "hunk_ordinal": len(hunks),
                "old_start": old_start,
                "old_count": old_count,
                "new_start": new_start,
                "new_count": new_count,
                "header_sha256": sha256_bytes(header),
            }
            hunks.append(current_hunk)
            old_line = old_start
            new_line = new_start
            current_line_ordinal = 0
            continue
        if current_hunk is None:
            continue
        if header == b"\\ No newline at end of file":
            if changed_lines:
                changed_lines[-1]["no_newline"] = True
            continue
        if not raw_line:
            continue
        prefix = raw_line[:1]
        if prefix == b"-" and not raw_line.startswith(b"---"):
            kind = "DELETE"
            old_number: int | None = old_line
            new_number: int | None = None
            old_line += 1
        elif prefix == b"+" and not raw_line.startswith(b"+++"):
            kind = "ADD"
            old_number = None
            new_number = new_line
            new_line += 1
        elif prefix == b" ":
            old_line += 1
            new_line += 1
            continue
        else:
            continue
        content = raw_line[1:]
        changed_lines.append(
            {
                "hunk_ordinal": current_hunk["hunk_ordinal"],
                "line_ordinal": current_line_ordinal,
                "line_kind": kind,
                "old_line_number": old_number,
                "new_line_number": new_number,
                "content_size_bytes": len(content),
                "content_sha256": sha256_bytes(content),
                "no_newline": False,
            }
        )
        current_line_ordinal += 1
    for line in changed_lines:
        projection = {key: line[key] for key in (
            "hunk_ordinal",
            "line_ordinal",
            "line_kind",
            "old_line_number",
            "new_line_number",
            "content_size_bytes",
            "content_sha256",
            "no_newline",
        )}
        line["line_sha256"] = sha256_bytes(canonical_json_bytes(projection))
    lines_by_hunk: dict[int, list[str]] = defaultdict(list)
    for line in changed_lines:
        lines_by_hunk[int(line["hunk_ordinal"])].append(str(line["line_sha256"]))
    for hunk in hunks:
        projection = {
            **hunk,
            "changed_line_sha256s": lines_by_hunk[int(hunk["hunk_ordinal"])],
        }
        hunk["hunk_sha256"] = sha256_bytes(canonical_json_bytes(projection))
    return hunks, changed_lines


def _object_evidence(
    root: Path,
    object_shas: list[str],
    *,
    max_single_object_bytes: int,
    max_total_object_bytes: int,
) -> tuple[list[dict[str, Any]], int]:
    if not object_shas:
        return [], 0
    process = subprocess.Popen(  # nosec B603
        [resolve_git_executable(root), "-C", str(root), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_environment(),
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    require(
        process.stdin is not None and process.stdout is not None,
        "SOURCE_GIT_CAT_FILE_PIPE_UNAVAILABLE",
        "Git object batch pipes could not be opened.",
        status="FAIL",
    )
    stdin = cast(BinaryIO, process.stdin)
    stdout = cast(BinaryIO, process.stdout)
    rows: list[dict[str, Any]] = []
    total_bytes = 0
    try:
        for requested_sha in object_shas:
            stdin.write((requested_sha + "\n").encode("ascii"))
            stdin.flush()
            header = stdout.readline().decode("ascii", errors="replace").strip()
            parts = header.split()
            require(
                len(parts) == 3
                and parts[0] == requested_sha
                and parts[1] in {"blob", "tree", "commit", "tag"},
                "SOURCE_GIT_OBJECT_HEADER_INVALID",
                "Git returned an invalid object header.",
                status="FAIL",
                requested_sha=requested_sha,
                header=header,
            )
            size = int(parts[2])
            require(
                size <= max_single_object_bytes
                and total_bytes + size <= max_total_object_bytes,
                "SOURCE_GIT_OBJECT_BYTE_BOUND_EXCEEDED",
                "Reachable Git object bytes exceed the governed hash-only bound.",
                status="BLOCKED",
                object_sha=requested_sha,
                object_bytes=size,
                bytes_read=total_bytes,
                max_single_object_bytes=max_single_object_bytes,
                max_total_object_bytes=max_total_object_bytes,
            )
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                block = stdout.read(min(1024 * 1024, remaining))
                require(
                    bool(block),
                    "SOURCE_GIT_OBJECT_LENGTH_MISMATCH",
                    "Git returned incomplete object bytes.",
                    status="FAIL",
                    object_sha=requested_sha,
                )
                digest.update(block)
                remaining -= len(block)
            separator = stdout.read(1)
            require(
                separator == b"\n",
                "SOURCE_GIT_OBJECT_SEPARATOR_INVALID",
                "Git returned an invalid object boundary.",
                status="FAIL",
                object_sha=requested_sha,
            )
            total_bytes += size
            projection = {
                "object_sha": requested_sha,
                "object_type": parts[1],
                "size_bytes": size,
                "content_sha256": digest.hexdigest().upper(),
            }
            rows.append(
                {
                    **projection,
                    "object_sha256": sha256_bytes(canonical_json_bytes(projection)),
                }
            )
    finally:
        stdin.close()
        if process.poll() is None:
            process.wait(timeout=30)
    require(
        process.returncode == 0,
        "SOURCE_GIT_CAT_FILE_FAILED",
        "Git object hashing failed.",
        status="FAIL",
        returncode=process.returncode,
    )
    return rows, total_bytes


def build_registered_git_history(
    registry_path: str | Path,
    batch_id: str,
    occurrence_ordinal: int,
    *,
    max_refs: int = 20_000,
    max_commits: int = 100_000,
    max_objects: int = 2_000_000,
    max_tree_entries: int = 5_000_000,
    max_file_changes: int = 2_000_000,
    max_hunks: int = 2_000_000,
    max_changed_lines: int = 5_000_000,
    max_patch_bytes: int = 2 * 1024 * 1024 * 1024,
    max_single_object_bytes: int = 1024 * 1024 * 1024,
    max_total_object_bytes: int = 8 * 1024 * 1024 * 1024,
) -> dict[str, Any]:
    """Seal full reachable Git history for one exact registered repository."""

    require(
        occurrence_ordinal >= 1
        and 1 <= max_refs <= 100_000
        and 1 <= max_commits <= 1_000_000
        and 1 <= max_objects <= 10_000_000
        and 1 <= max_tree_entries <= 25_000_000
        and 1 <= max_file_changes <= 10_000_000
        and 1 <= max_hunks <= 10_000_000
        and 1 <= max_changed_lines <= 25_000_000
        and 1 <= max_patch_bytes <= 16 * 1024 * 1024 * 1024
        and 1 <= max_single_object_bytes <= 8 * 1024 * 1024 * 1024
        and max_single_object_bytes <= max_total_object_bytes <= 64 * 1024 * 1024 * 1024,
        "SOURCE_GIT_BOUNDS_INVALID",
        "Git history bounds are outside governed finite limits.",
        status="BLOCKED",
    )
    target = initialize_source_authority_registry(registry_path)
    batch_sha256, occurrence, root = _load_registered_repository(
        target, batch_id, occurrence_ordinal
    )
    configuration = {
        "extractor_version": EXTRACTOR_VERSION,
        "rename_threshold": "50%",
        "all_reachable_refs": True,
        "all_parent_comparisons": True,
        "patch_context_lines": 0,
        "changed_line_payloads_persisted": False,
        "lazy_fetch_disabled": True,
        "bounds": {
            "max_refs": max_refs,
            "max_commits": max_commits,
            "max_objects": max_objects,
            "max_tree_entries": max_tree_entries,
            "max_file_changes": max_file_changes,
            "max_hunks": max_hunks,
            "max_changed_lines": max_changed_lines,
            "max_patch_bytes": max_patch_bytes,
            "max_single_object_bytes": max_single_object_bytes,
            "max_total_object_bytes": max_total_object_bytes,
        },
    }
    head_commit = _git(root, "rev-parse", "HEAD").decode("ascii").strip()
    head_tree = _git(root, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    branch_result = subprocess.run(  # nosec B603
        [
            resolve_git_executable(root),
            "-C",
            str(root),
            "symbolic-ref",
            "--short",
            "-q",
            "HEAD",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=30,
        env=_git_environment(),
        close_fds=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    branch = (
        branch_result.stdout.decode("utf-8", errors="replace").strip()
        if branch_result.returncode == 0
        else "DETACHED"
    )
    worktree_status = _git(
        root, "status", "--porcelain=v2", "-z", "--untracked-files=all"
    )
    worktree_status_sha256 = sha256_bytes(worktree_status)
    refs = _refs(root, max_refs)
    commit_shas = [
        line.decode("ascii", errors="strict")
        for line in _git(root, "rev-list", "--topo-order", "--reverse", "--all").splitlines()
        if line
    ]
    require(
        bool(commit_shas)
        and len(commit_shas) <= max_commits
        and all(_SHA_RE.fullmatch(value) for value in commit_shas),
        "SOURCE_GIT_COMMIT_BOUND_OR_IDENTITY_INVALID",
        "Reachable commits are empty, invalid, or exceed the governed bound.",
        status="BLOCKED",
        commit_count=len(commit_shas),
        max_commits=max_commits,
    )
    commits = [
        _commit_metadata(root, commit_sha, ordinal)
        for ordinal, commit_sha in enumerate(commit_shas)
    ]
    tree_entries: list[dict[str, Any]] = []
    for commit in commits:
        tree_entries.extend(
            _tree_entries(
                root,
                str(commit["commit_sha"]),
                remaining_entries=max_tree_entries - len(tree_entries),
            )
        )

    reachable_objects = {
        line.decode("ascii", errors="strict")
        for line in _git(
            root, "rev-list", "--objects", "--all", "--no-object-names"
        ).splitlines()
        if line
    }
    reachable_objects.update(commit_shas)
    reachable_objects.update(str(row["tree_sha"]) for row in commits)
    reachable_objects.update(str(row["object_sha"]) for row in tree_entries)
    for ref in refs:
        reachable_objects.add(str(ref["object_sha"]))
        if ref["peeled_sha"]:
            reachable_objects.add(str(ref["peeled_sha"]))
    require(
        len(reachable_objects) <= max_objects
        and all(_SHA_RE.fullmatch(value) for value in reachable_objects),
        "SOURCE_GIT_OBJECT_BOUND_OR_IDENTITY_INVALID",
        "Reachable objects are invalid or exceed the governed bound.",
        status="BLOCKED",
        object_count=len(reachable_objects),
        max_objects=max_objects,
    )
    objects, object_bytes_hashed = _object_evidence(
        root,
        sorted(reachable_objects),
        max_single_object_bytes=max_single_object_bytes,
        max_total_object_bytes=max_total_object_bytes,
    )
    object_by_sha = {str(row["object_sha"]): row for row in objects}

    parent_rows: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    hunks: list[dict[str, Any]] = []
    changed_lines: list[dict[str, Any]] = []
    patch_bytes_read = 0
    policy_excluded_patch_count = 0
    for commit in commits:
        commit_sha = str(commit["commit_sha"])
        parents = list(commit["parents"])
        for parent_ordinal, parent_sha in enumerate(parents):
            projection = {
                "commit_sha": commit_sha,
                "parent_ordinal": parent_ordinal,
                "parent_sha": parent_sha,
            }
            parent_rows.append(
                {
                    **projection,
                    "parent_edge_sha256": sha256_bytes(canonical_json_bytes(projection)),
                }
            )
        comparisons: list[str | None] = parents if parents else [None]
        for parent_ordinal, parent_sha in enumerate(comparisons):
            raw_payload, numstat_payload = _comparison_payloads(
                root, commit_sha, parent_sha
            )
            raw_rows = _raw_changes(raw_payload)
            statistics = _numstat(numstat_payload)
            require(
                len(changes) + len(raw_rows) <= max_file_changes,
                "SOURCE_GIT_FILE_CHANGE_BOUND_EXCEEDED",
                "Per-parent Git changes exceed the governed bound.",
                status="BLOCKED",
                max_file_changes=max_file_changes,
            )
            for change_ordinal, raw_row in enumerate(raw_rows):
                additions, deletions, binary = statistics.get(
                    (raw_row["prior_path"], raw_row["member_path"]),
                    (None, None, False),
                )
                comparison_base = parent_sha or "ROOT_COMMIT_EMPTY_TREE"
                projection = {
                    key: raw_row[key]
                    for key in (
                        "status_code",
                        "similarity_score",
                        "prior_path",
                        "prior_path_sha256",
                        "prior_path_policy_reason",
                        "member_path",
                        "path_sha256",
                        "path_policy_reason",
                        "old_mode",
                        "new_mode",
                        "old_object_sha",
                        "new_object_sha",
                    )
                }
                projection.update(
                    {
                        "commit_sha": commit_sha,
                        "parent_ordinal": parent_ordinal,
                        "comparison_base_sha": comparison_base,
                        "change_ordinal": change_ordinal,
                        "additions": additions,
                        "deletions": deletions,
                        "binary": binary,
                    }
                )
                change = {
                    **projection,
                    "change_sha256": sha256_bytes(canonical_json_bytes(projection)),
                }
                changes.append(change)
                git_arguments = list(raw_row["_git_arguments"])
                if not git_arguments:
                    policy_excluded_patch_count += 1
                    continue
                remaining_patch = max_patch_bytes - patch_bytes_read
                require(
                    remaining_patch > 0,
                    "SOURCE_GIT_PATCH_BYTE_BOUND_EXCEEDED",
                    "Patch evidence exceeds the governed byte bound.",
                    status="BLOCKED",
                    max_patch_bytes=max_patch_bytes,
                )
                patch = _patch(
                    root,
                    commit_sha,
                    parent_sha,
                    git_arguments,
                    remaining_patch,
                )
                patch_bytes_read += len(patch)
                change_hunks, change_lines = _parse_patch(patch)
                require(
                    len(hunks) + len(change_hunks) <= max_hunks
                    and len(changed_lines) + len(change_lines) <= max_changed_lines,
                    "SOURCE_GIT_PATCH_STRUCTURE_BOUND_EXCEEDED",
                    "Git hunks or changed lines exceed governed bounds.",
                    status="BLOCKED",
                    max_hunks=max_hunks,
                    max_changed_lines=max_changed_lines,
                )
                for hunk in change_hunks:
                    hunks.append(
                        {
                            "commit_sha": commit_sha,
                            "parent_ordinal": parent_ordinal,
                            "change_ordinal": change_ordinal,
                            **hunk,
                        }
                    )
                for line in change_lines:
                    changed_lines.append(
                        {
                            "commit_sha": commit_sha,
                            "parent_ordinal": parent_ordinal,
                            "change_ordinal": change_ordinal,
                            **line,
                        }
                    )

    for commit in commits:
        object_row = object_by_sha[str(commit["commit_sha"])]
        commit["object_content_sha256"] = object_row["content_sha256"]
        projection = {
            key: commit[key]
            for key in (
                "commit_sha",
                "ordinal",
                "tree_sha",
                "authored_at",
                "committed_at",
                "author_sha256",
                "committer_sha256",
                "message_sha256",
                "object_content_sha256",
                "parent_count",
            )
        }
        commit["commit_sha256"] = sha256_bytes(canonical_json_bytes(projection))

    object_paths_by_sha: dict[str, dict[str, str]] = defaultdict(dict)
    for row in tree_entries:
        object_paths_by_sha[str(row["object_sha"])][str(row["member_path"])] = str(
            row["path_sha256"]
        )
    object_paths: list[dict[str, Any]] = []
    for object_sha in sorted(object_paths_by_sha):
        for path_ordinal, member_path in enumerate(sorted(object_paths_by_sha[object_sha])):
            object_paths.append(
                {
                    "object_sha": object_sha,
                    "path_ordinal": path_ordinal,
                    "observed_path": member_path,
                    "path_sha256": object_paths_by_sha[object_sha][member_path],
                }
            )

    refs_root = _root(refs)
    commits_root = _root(
        [
            {key: row[key] for key in (
                "commit_sha",
                "ordinal",
                "tree_sha",
                "parent_count",
                "commit_sha256",
            )}
            for row in commits
        ]
        + parent_rows
    )
    objects_root = _root(
        [
            {key: row[key] for key in (
                "object_sha",
                "object_type",
                "size_bytes",
                "content_sha256",
                "object_sha256",
            )}
            for row in objects
        ]
    )
    trees_root = _root(
        [
            {key: row[key] for key in (
                "commit_sha",
                "entry_ordinal",
                "member_path",
                "entry_sha256",
            )}
            for row in tree_entries
        ]
    )
    changes_root = _root(
        [
            {key: row[key] for key in (
                "commit_sha",
                "parent_ordinal",
                "change_ordinal",
                "change_sha256",
            )}
            for row in changes
        ]
    )
    hunks_root = _root(
        [
            {key: row[key] for key in (
                "commit_sha",
                "parent_ordinal",
                "change_ordinal",
                "hunk_ordinal",
                "hunk_sha256",
            )}
            for row in hunks
        ]
    )
    lines_root = _root(
        [
            {key: row[key] for key in (
                "commit_sha",
                "parent_ordinal",
                "change_ordinal",
                "hunk_ordinal",
                "line_ordinal",
                "line_sha256",
            )}
            for row in changed_lines
        ]
    )
    repository_identity = {
        "batch_id": batch_id,
        "batch_sha256": batch_sha256,
        "occurrence_ordinal": occurrence_ordinal,
        "object_id": occurrence["object_id"],
        "source_identity_sha256": occurrence["identity_sha256"],
        "head_commit_sha": head_commit,
        "head_tree_sha": head_tree,
        "branch": branch,
    }
    repository_identity_sha256 = sha256_bytes(
        canonical_json_bytes(repository_identity)
    )
    history_signature_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "refs_root_sha256": refs_root,
                "commits_root_sha256": commits_root,
                "objects_root_sha256": objects_root,
            }
        )
    )
    history_root_sha256 = sha256_bytes(
        canonical_json_bytes(
            {
                "repository_identity_sha256": repository_identity_sha256,
                "history_signature_sha256": history_signature_sha256,
                "worktree_status_sha256": worktree_status_sha256,
                "configuration": configuration,
                "roots": {
                    "refs": refs_root,
                    "commits": commits_root,
                    "objects": objects_root,
                    "trees": trees_root,
                    "changes": changes_root,
                    "hunks": hunks_root,
                    "lines": lines_root,
                },
            }
        )
    )
    snapshot_id = _stable_id(
        "githistory", {"history_root_sha256": history_root_sha256}
    )
    with _connect(target) as connection:
        existing = connection.execute(
            "SELECT receipt_json FROM source_git_snapshot WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if existing is not None:
            receipt = json.loads(str(existing["receipt_json"]))
            receipt["append_status"] = "IDEMPOTENT_REUSE"
            return receipt

    excluded_path_count = sum(
        1
        for row in tree_entries
        if row["path_policy_reason"] is not None
    )
    rename_rows = [row for row in changes if row["status_code"] == "R"]
    status = "PASS_WITH_POLICY_EXCLUSIONS" if excluded_path_count else "PASS"
    counts = {
        "refs": len(refs),
        "commits": len(commits),
        "parent_edges": len(parent_rows),
        "objects": len(objects),
        "object_paths": len(object_paths),
        "tree_entries": len(tree_entries),
        "file_changes": len(changes),
        "renames": len(rename_rows),
        "hunks": len(hunks),
        "changed_lines": len(changed_lines),
        "policy_excluded_tree_paths": excluded_path_count,
        "policy_excluded_patches": policy_excluded_patch_count,
    }
    receipt_core = {
        "schema": GIT_HISTORY_SCHEMA,
        "status": status,
        "append_status": "APPENDED",
        "snapshot_id": snapshot_id,
        "batch_id": batch_id,
        "batch_sha256": batch_sha256,
        "occurrence_ordinal": occurrence_ordinal,
        "object_id": occurrence["object_id"],
        "source_identity_sha256": occurrence["identity_sha256"],
        "extractor_version": EXTRACTOR_VERSION,
        "repository_identity_sha256": repository_identity_sha256,
        "head_commit_sha": head_commit,
        "head_tree_sha": head_tree,
        "branch": branch,
        "worktree_clean": not bool(worktree_status),
        "worktree_status_sha256": worktree_status_sha256,
        "history_signature_sha256": history_signature_sha256,
        "history_root_sha256": history_root_sha256,
        "roots": {
            "refs_root_sha256": refs_root,
            "commits_root_sha256": commits_root,
            "objects_root_sha256": objects_root,
            "trees_root_sha256": trees_root,
            "changes_root_sha256": changes_root,
            "hunks_root_sha256": hunks_root,
            "lines_root_sha256": lines_root,
        },
        "counts": counts,
        "object_bytes_hashed": object_bytes_hashed,
        "patch_bytes_read": patch_bytes_read,
        "full_reachable_history": True,
        "all_parent_comparisons": True,
        "rename_detection": "GIT_M50_EXACT_RECEIPT",
        "changed_line_storage": "SHA256_SIZE_AND_COORDINATES_ONLY",
        "path_policy": {
            "excluded_paths_persisted": False,
            "excluded_path_hashes_persisted": True,
            "non_utf8_paths_persisted": False,
        },
        "configuration": configuration,
        "source_bytes_mutated": False,
        "git_repository_mutated": False,
        "network_used": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
    receipt = {**receipt_core, "receipt_sha256": receipt_sha256}
    with _connect(target) as connection, connection:
        connection.execute(
            """INSERT INTO source_git_snapshot VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                batch_id,
                occurrence_ordinal,
                occurrence["object_id"],
                repository_identity_sha256,
                head_commit,
                head_tree,
                branch,
                int(not bool(worktree_status)),
                worktree_status_sha256,
                history_signature_sha256,
                canonical_json_bytes(configuration).decode("utf-8").strip(),
                counts["refs"],
                counts["commits"],
                counts["parent_edges"],
                counts["objects"],
                counts["tree_entries"],
                counts["file_changes"],
                counts["renames"],
                counts["hunks"],
                counts["changed_lines"],
                refs_root,
                commits_root,
                objects_root,
                trees_root,
                changes_root,
                hunks_root,
                lines_root,
                history_root_sha256,
                status,
                canonical_json_bytes(receipt).decode("utf-8").strip(),
                receipt_sha256,
                utc_now(),
            ),
        )
        connection.executemany(
            """INSERT INTO source_git_ref VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    row["ref_name"],
                    row["object_sha"],
                    row["object_type"],
                    row["peeled_sha"],
                    row["peeled_type"],
                    row["ref_sha256"],
                )
                for row in refs
            ],
        )
        connection.executemany(
            """INSERT INTO source_git_commit VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["ordinal"],
                    row["tree_sha"],
                    row["authored_at"],
                    row["committed_at"],
                    row["author_sha256"],
                    row["committer_sha256"],
                    row["message_redacted"],
                    row["message_sha256"],
                    row["object_content_sha256"],
                    row["parent_count"],
                    row["commit_sha256"],
                )
                for row in commits
            ],
        )
        connection.executemany(
            "INSERT INTO source_git_parent VALUES (?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["parent_ordinal"],
                    row["parent_sha"],
                    row["parent_edge_sha256"],
                )
                for row in parent_rows
            ],
        )
        connection.executemany(
            "INSERT INTO source_git_object VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    row["object_sha"],
                    row["object_type"],
                    row["size_bytes"],
                    row["content_sha256"],
                    row["object_sha256"],
                )
                for row in objects
            ],
        )
        connection.executemany(
            "INSERT INTO source_git_object_path VALUES (?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    row["object_sha"],
                    row["path_ordinal"],
                    row["observed_path"],
                    row["path_sha256"],
                )
                for row in object_paths
            ],
        )
        connection.executemany(
            "INSERT INTO source_git_tree_entry VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["entry_ordinal"],
                    row["member_path"],
                    row["mode"],
                    row["object_type"],
                    row["object_sha"],
                    row["entry_sha256"],
                )
                for row in tree_entries
            ],
        )
        connection.executemany(
            """INSERT INTO source_git_file_change VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["parent_ordinal"],
                    row["comparison_base_sha"],
                    row["change_ordinal"],
                    row["status_code"],
                    row["similarity_score"],
                    row["prior_path"],
                    row["member_path"],
                    row["old_mode"],
                    row["new_mode"],
                    row["old_object_sha"],
                    row["new_object_sha"],
                    row["additions"],
                    row["deletions"],
                    int(bool(row["binary"])),
                    row["change_sha256"],
                )
                for row in changes
            ],
        )
        connection.executemany(
            "INSERT INTO source_git_rename VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["parent_ordinal"],
                    row["change_ordinal"],
                    row["prior_path"],
                    row["member_path"],
                    row["similarity_score"],
                    sha256_bytes(
                        canonical_json_bytes(
                            {
                                "commit_sha": row["commit_sha"],
                                "parent_ordinal": row["parent_ordinal"],
                                "prior_path": row["prior_path"],
                                "member_path": row["member_path"],
                                "similarity_score": row["similarity_score"],
                            }
                        )
                    ),
                )
                for row in rename_rows
            ],
        )
        connection.executemany(
            """INSERT INTO source_git_hunk VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["parent_ordinal"],
                    row["change_ordinal"],
                    row["hunk_ordinal"],
                    row["old_start"],
                    row["old_count"],
                    row["new_start"],
                    row["new_count"],
                    row["header_sha256"],
                    row["hunk_sha256"],
                )
                for row in hunks
            ],
        )
        connection.executemany(
            """INSERT INTO source_git_changed_line VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    snapshot_id,
                    row["commit_sha"],
                    row["parent_ordinal"],
                    row["change_ordinal"],
                    row["hunk_ordinal"],
                    row["line_ordinal"],
                    row["line_kind"],
                    row["old_line_number"],
                    row["new_line_number"],
                    row["content_size_bytes"],
                    row["content_sha256"],
                    int(bool(row["no_newline"])),
                    row["line_sha256"],
                )
                for row in changed_lines
            ],
        )
        event = {
            "schema": "evidence-lane.source-git-history-event.v1",
            "snapshot_id": snapshot_id,
            "history_root_sha256": history_root_sha256,
            "receipt_sha256": receipt_sha256,
        }
        event_sha256 = sha256_bytes(canonical_json_bytes(event))
        connection.execute(
            "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"registry_{event_sha256[:32].lower()}",
                batch_id,
                "source.git.history.indexed",
                canonical_json_bytes(event).decode("utf-8").strip(),
                event_sha256,
                utc_now(),
            ),
        )
        receipt["registry_event_sha256"] = event_sha256
        connection.execute(
            "UPDATE source_git_snapshot SET receipt_json=? WHERE snapshot_id=?",
            (canonical_json_bytes(receipt).decode("utf-8").strip(), snapshot_id),
        )
    return receipt


def build_source_git_commit_impact(
    registry_path: str | Path,
    snapshot_id: str,
    graph_id: str,
    commit_sha: str,
    *,
    parent_ordinal: int = 0,
    relations: list[str] | None = None,
    direction: str = "UPSTREAM",
    max_depth: int = 3,
    max_nodes: int = 1000,
) -> dict[str, Any]:
    """Bind one exact per-parent Git change set to a current semantic graph."""

    target = initialize_source_authority_registry(registry_path)
    with _connect(target) as connection:
        snapshot = connection.execute(
            """SELECT batch_id, occurrence_ordinal, object_id,
            history_root_sha256 FROM source_git_snapshot WHERE snapshot_id=?""",
            (snapshot_id,),
        ).fetchone()
        graph = connection.execute(
            """SELECT batch_id, graph_root_sha256, receipt_json
            FROM source_graph_snapshot WHERE graph_id=?""",
            (graph_id,),
        ).fetchone()
        require(
            snapshot is not None and graph is not None,
            "SOURCE_GIT_IMPACT_AUTHORITY_MISSING",
            "The selected Git snapshot or source graph does not exist.",
            status="MISMATCH",
        )
        assert snapshot is not None
        assert graph is not None
        graph_receipt = json.loads(str(graph["receipt_json"]))
        require(
            str(snapshot["batch_id"]) == str(graph["batch_id"])
            and int(snapshot["occurrence_ordinal"])
            in [int(value) for value in graph_receipt["selected_occurrence_ordinals"]],
            "SOURCE_GIT_IMPACT_GRAPH_SCOPE_MISMATCH",
            "The graph does not include the Git snapshot's registered occurrence.",
            status="MISMATCH",
        )
        commit = connection.execute(
            "SELECT commit_sha FROM source_git_commit WHERE snapshot_id=? AND commit_sha=?",
            (snapshot_id, commit_sha),
        ).fetchone()
        changes = [
            dict(row)
            for row in connection.execute(
                """SELECT change_ordinal, status_code, prior_path, member_path,
                change_sha256 FROM source_git_file_change
                WHERE snapshot_id=? AND commit_sha=? AND parent_ordinal=?
                ORDER BY change_ordinal""",
                (snapshot_id, commit_sha, parent_ordinal),
            )
        ]
        require(
            commit is not None and bool(changes),
            "SOURCE_GIT_IMPACT_CHANGE_SET_MISSING",
            "The exact commit and parent comparison has no indexed change set.",
            status="MISMATCH",
            commit_sha=commit_sha,
            parent_ordinal=parent_ordinal,
        )
        exact_paths = sorted(
            {
                str(path)
                for change in changes
                for path in (change["prior_path"], change["member_path"])
                if path is not None and not str(path).startswith("<")
            }
        )
        node_rows = [
            dict(row)
            for row in connection.execute(
                """SELECT node_id, member_path, node_sha256
                FROM source_graph_node
                WHERE graph_id=? AND object_id=? AND node_kind='FILE'
                ORDER BY member_path, node_id""",
                (graph_id, snapshot["object_id"]),
            )
        ]
    nodes_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in node_rows:
        nodes_by_path[str(row["member_path"])].append(row)
    mapped_paths = sorted(path for path in exact_paths if len(nodes_by_path[path]) == 1)
    ambiguous_paths = sorted(path for path in exact_paths if len(nodes_by_path[path]) > 1)
    unmapped_paths = sorted(path for path in exact_paths if not nodes_by_path[path])
    seed_node_ids = [str(nodes_by_path[path][0]["node_id"]) for path in mapped_paths]
    graph_impact_receipt: dict[str, Any] | None = None
    if seed_node_ids:
        graph_impact_receipt = source_graph_impact(
            target,
            graph_id,
            seed_node_ids,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    projection = {
        "schema": GIT_IMPACT_SCHEMA,
        "snapshot_id": snapshot_id,
        "history_root_sha256": snapshot["history_root_sha256"],
        "graph_id": graph_id,
        "graph_root_sha256": graph["graph_root_sha256"],
        "commit_sha": commit_sha,
        "parent_ordinal": parent_ordinal,
        "change_projection_sha256": sha256_bytes(
            canonical_json_bytes(
                [
                    {
                        "change_ordinal": row["change_ordinal"],
                        "change_sha256": row["change_sha256"],
                    }
                    for row in changes
                ]
            )
        ),
        "changed_paths": exact_paths,
        "mapped_paths": mapped_paths,
        "ambiguous_paths": ambiguous_paths,
        "unmapped_paths": unmapped_paths,
        "seed_node_ids": seed_node_ids,
        "graph_impact_id": (
            graph_impact_receipt["impact_id"] if graph_impact_receipt else None
        ),
        "graph_impact_projection_sha256": (
            graph_impact_receipt["projection_sha256"]
            if graph_impact_receipt
            else None
        ),
    }
    projection_sha256 = sha256_bytes(canonical_json_bytes(projection))
    impact_id = _stable_id("gitimpact", {"projection_sha256": projection_sha256})
    with _connect(target) as connection:
        existing = connection.execute(
            "SELECT receipt_json FROM source_git_impact WHERE impact_id=?",
            (impact_id,),
        ).fetchone()
        if existing is not None:
            receipt = json.loads(str(existing["receipt_json"]))
            receipt["append_status"] = "IDEMPOTENT_REUSE"
            return receipt
    status = (
        "PASS"
        if mapped_paths and not unmapped_paths and not ambiguous_paths
        else "PASS_WITH_UNMAPPED_PATHS"
    )
    receipt_core = {
        **projection,
        "status": status,
        "append_status": "APPENDED",
        "impact_id": impact_id,
        "projection_sha256": projection_sha256,
        "mapped_path_count": len(mapped_paths),
        "unmapped_path_count": len(unmapped_paths) + len(ambiguous_paths),
        "source_bytes_mutated": False,
        "git_repository_mutated": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_core))
    receipt = {**receipt_core, "receipt_sha256": receipt_sha256}
    with _connect(target) as connection, connection:
        connection.execute(
            """INSERT INTO source_git_impact VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                impact_id,
                snapshot_id,
                graph_id,
                commit_sha,
                parent_ordinal,
                len(mapped_paths),
                len(unmapped_paths) + len(ambiguous_paths),
                canonical_json_bytes(seed_node_ids).decode("utf-8").strip(),
                canonical_json_bytes(projection).decode("utf-8").strip(),
                projection_sha256,
                graph_impact_receipt["impact_id"] if graph_impact_receipt else None,
                canonical_json_bytes(receipt).decode("utf-8").strip(),
                receipt_sha256,
                utc_now(),
            ),
        )
        event = {
            "schema": "evidence-lane.source-git-impact-event.v1",
            "impact_id": impact_id,
            "projection_sha256": projection_sha256,
            "receipt_sha256": receipt_sha256,
        }
        event_sha256 = sha256_bytes(canonical_json_bytes(event))
        connection.execute(
            "INSERT INTO registry_event VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"registry_{event_sha256[:32].lower()}",
                snapshot["batch_id"],
                "source.git.impact.mapped",
                canonical_json_bytes(event).decode("utf-8").strip(),
                event_sha256,
                utc_now(),
            ),
        )
        receipt["registry_event_sha256"] = event_sha256
        connection.execute(
            "UPDATE source_git_impact SET receipt_json=? WHERE impact_id=?",
            (canonical_json_bytes(receipt).decode("utf-8").strip(), impact_id),
        )
    return receipt


__all__ = [
    "build_registered_git_history",
    "build_source_git_commit_impact",
]
