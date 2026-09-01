"""Full reachable Git history with content-addressed blob and chunk reuse."""

from __future__ import annotations

import sqlite3
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any, BinaryIO, cast

from .bounded_io import run_bounded_process
from .compact_storage import compress_exact_bytes, decompress_exact_bytes
from .errors import EvidenceLaneError, require
from .git_adapter import resolve_git_executable
from .hashing import canonical_json_bytes, sha256_bytes
from .redaction import redact_text
from .source_policy import (
    content_exclusion_reason,
    path_exclusion_reason,
    redact_known_environment_secrets,
)

_TEXT_CHUNK_CHARS = 6000
_TEXT_CHUNK_OVERLAP = 500
MAX_HISTORY_COMMITS = 20_000
MAX_HISTORY_TREE_ENTRIES = 2_000_000
MAX_HISTORY_UNIQUE_BLOBS = 100_000
MAX_HISTORY_SINGLE_BLOB_BYTES = 64 * 1024 * 1024
MAX_HISTORY_TOTAL_BLOB_BYTES = 512 * 1024 * 1024
MAX_HISTORY_OPERATION_SECONDS = 900
MAX_GIT_COMMAND_STDOUT_BYTES = 256 * 1024 * 1024
MAX_GIT_COMMAND_STDERR_BYTES = 2 * 1024 * 1024


def _git(root: Path, *args: str, timeout: int = 180) -> bytes:
    completed = run_bounded_process(
        [resolve_git_executable(root), *args],
        cwd=root,
        timeout_seconds=timeout,
        max_stdout_bytes=MAX_GIT_COMMAND_STDOUT_BYTES,
        max_stderr_bytes=MAX_GIT_COMMAND_STDERR_BYTES,
    )
    if completed.returncode != 0:
        raise EvidenceLaneError(
            "GIT_HISTORY_COMMAND_FAILED",
            "A fixed read-only Git history command failed.",
            status="BLOCKED",
            details={
                "argv": ["git", *args],
                "returncode": completed.returncode,
                "stderr_tail": completed.stderr.decode("utf-8", errors="replace")[
                    -2000:
                ],
            },
        )
    return completed.stdout


def git_history_signature(repository_root: str | Path) -> str:
    """Hash all reachable commit IDs and refs without reading worktree bytes."""

    root = Path(repository_root).resolve()
    commits = _git(root, "rev-list", "--topo-order", "--all").decode(
        "ascii", errors="strict"
    )
    refs = _git(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(*objectname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    ).decode("utf-8", errors="replace")
    return sha256_bytes(canonical_json_bytes({"commits": commits, "refs": refs}))


def create_git_history_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS git_commit_registry(
            commit_sha TEXT PRIMARY KEY,
            ordinal INTEGER NOT NULL,
            tree_sha TEXT NOT NULL,
            authored_at TEXT NOT NULL,
            committed_at TEXT NOT NULL,
            author_name TEXT NOT NULL,
            author_email TEXT NOT NULL,
            committer_name TEXT NOT NULL,
            committer_email TEXT NOT NULL,
            message TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_commit_parent(
            commit_sha TEXT NOT NULL REFERENCES git_commit_registry(commit_sha),
            parent_sha TEXT NOT NULL,
            parent_ordinal INTEGER NOT NULL,
            PRIMARY KEY(commit_sha, parent_ordinal)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_ref_registry(
            ref_name TEXT PRIMARY KEY,
            object_sha TEXT NOT NULL,
            peeled_sha TEXT,
            captured_signature TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_blob_cas(
            blob_sha TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            is_binary INTEGER NOT NULL CHECK(is_binary IN (0, 1)),
            encoding TEXT,
            compression TEXT NOT NULL,
            compressed_bytes BLOB NOT NULL,
            first_commit_sha TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_content_chunk_cas(
            chunk_sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            compression TEXT NOT NULL,
            compressed_text BLOB NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_chunk_occurrence(
            commit_sha TEXT NOT NULL REFERENCES git_commit_registry(commit_sha),
            path TEXT NOT NULL,
            blob_sha TEXT NOT NULL REFERENCES git_blob_cas(blob_sha),
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            chunk_sha256 TEXT NOT NULL REFERENCES git_content_chunk_cas(chunk_sha256),
            PRIMARY KEY(commit_sha, path, ordinal)
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_file_change(
            commit_sha TEXT NOT NULL REFERENCES git_commit_registry(commit_sha),
            status TEXT NOT NULL,
            path TEXT NOT NULL,
            prior_path TEXT,
            blob_sha TEXT,
            PRIMARY KEY(commit_sha, status, path)
        ) STRICT;
        CREATE VIRTUAL TABLE IF NOT EXISTS git_history_fts USING fts5(
            commit_sha UNINDEXED,
            path UNINDEXED,
            message,
            text_content,
            content='',
            contentless_delete=1,
            tokenize='unicode61'
        );
        CREATE INDEX IF NOT EXISTS git_chunk_path_idx
        ON git_chunk_occurrence(path, commit_sha, ordinal);
        CREATE INDEX IF NOT EXISTS git_file_change_path_idx
        ON git_file_change(path, commit_sha);
        """
    )


def _decode_blob(data: bytes) -> tuple[str | None, str | None]:
    if b"\x00" in data[:8192]:
        return None, None
    for encoding in ("utf-8", "utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    return None, None


def _chunks(text: str) -> list[tuple[int, int, int, str]]:
    rows: list[tuple[int, int, int, str]] = []
    start = 0
    ordinal = 0
    step = _TEXT_CHUNK_CHARS - _TEXT_CHUNK_OVERLAP
    while start < len(text):
        end = min(len(text), start + _TEXT_CHUNK_CHARS)
        rows.append((ordinal, start, end, text[start:end]))
        if end == len(text):
            break
        start += step
        ordinal += 1
    return rows


def _commit(root: Path, commit_sha: str) -> dict[str, Any]:
    raw = _git(
        root,
        "show",
        "-s",
        "--no-show-signature",
        "--format=%H%x00%P%x00%T%x00%aI%x00%cI%x00%an%x00%ae%x00%cn%x00%ce%x00%B",
        commit_sha,
    )
    parts = raw.split(b"\x00", 9)
    require(
        len(parts) == 10,
        "GIT_COMMIT_METADATA_INVALID",
        "Git returned an unexpected commit metadata shape.",
        status="FAIL",
        commit_sha=commit_sha,
    )
    values = [part.decode("utf-8", errors="replace") for part in parts]
    return {
        "commit_sha": values[0].strip(),
        "parents": [item for item in values[1].strip().split() if item],
        "tree_sha": values[2].strip(),
        "authored_at": values[3].strip(),
        "committed_at": values[4].strip(),
        "author_name": values[5],
        "author_email": values[6],
        "committer_name": values[7],
        "committer_email": values[8],
        "message": redact_text(
            redact_known_environment_secrets(values[9].rstrip("\r\n"))
        ),
    }


def _tree(root: Path, commit_sha: str) -> list[dict[str, str]]:
    payload = _git(root, "ls-tree", "-r", "-z", "--full-tree", commit_sha)
    rows: list[dict[str, str]] = []
    for record in payload.split(b"\x00"):
        if not record:
            continue
        header, path = record.split(b"\t", 1)
        mode, kind, object_sha = header.decode("ascii").split(" ", 2)
        if kind != "blob":
            continue
        normalized_path = path.decode("utf-8", errors="replace")
        if path_exclusion_reason(normalized_path) is not None:
            continue
        rows.append(
            {
                "mode": mode,
                "blob_sha": object_sha,
                "path": normalized_path,
            }
        )
    return rows


def _changes(root: Path, commit_sha: str) -> list[dict[str, str | None]]:
    payload = _git(
        root,
        "diff-tree",
        "--root",
        "--no-commit-id",
        "--name-status",
        "-r",
        "-M",
        "-z",
        commit_sha,
    )
    fields = [item for item in payload.split(b"\x00") if item]
    rows: list[dict[str, str | None]] = []
    index = 0
    while index < len(fields):
        status = fields[index].decode("ascii", errors="replace")
        index += 1
        if status.startswith(("R", "C")):
            prior_path = fields[index].decode("utf-8", errors="replace")
            path = fields[index + 1].decode("utf-8", errors="replace")
            index += 2
        else:
            prior_path = None
            path = fields[index].decode("utf-8", errors="replace")
            index += 1
        if path_exclusion_reason(path) is not None:
            continue
        if prior_path is not None and path_exclusion_reason(prior_path) is not None:
            continue
        rows.append({"status": status, "path": path, "prior_path": prior_path})
    return rows


def _purge_unsafe_history(connection: sqlite3.Connection) -> dict[str, int]:
    """Remove unsafe bytes inherited from an older accepted lane database."""

    unsafe_blobs = set()
    for row in connection.execute(
        "SELECT blob_sha,content_sha256,size_bytes,compression,compressed_bytes "
        "FROM git_blob_cas"
    ):
        data = decompress_exact_bytes(
            compression=str(row["compression"]),
            payload=bytes(row["compressed_bytes"]),
            expected_size=int(row["size_bytes"]),
            expected_sha256=str(row["content_sha256"]),
        )
        if content_exclusion_reason(data) is not None:
            unsafe_blobs.add(str(row["blob_sha"]))
    sensitive_occurrences = [
        (str(row["commit_sha"]), str(row["path"]), int(row["ordinal"]))
        for row in connection.execute(
            "SELECT commit_sha,path,ordinal FROM git_chunk_occurrence"
        )
        if path_exclusion_reason(str(row["path"])) is not None
    ]
    for commit_sha, path, ordinal in sensitive_occurrences:
        connection.execute(
            "DELETE FROM git_chunk_occurrence WHERE commit_sha=? AND path=? AND ordinal=?",
            (commit_sha, path, ordinal),
        )
    sensitive_changes = [
        (str(row["commit_sha"]), str(row["status"]), str(row["path"]))
        for row in connection.execute(
            "SELECT commit_sha,status,path,prior_path FROM git_file_change"
        )
        if path_exclusion_reason(str(row["path"])) is not None
        or (
            row["prior_path"] is not None
            and path_exclusion_reason(str(row["prior_path"])) is not None
        )
    ]
    for commit_sha, status, path in sensitive_changes:
        connection.execute(
            "DELETE FROM git_file_change WHERE commit_sha=? AND status=? AND path=?",
            (commit_sha, status, path),
        )
    for blob_sha in sorted(unsafe_blobs):
        connection.execute(
            "DELETE FROM git_chunk_occurrence WHERE blob_sha=?", (blob_sha,)
        )
        connection.execute("DELETE FROM git_file_change WHERE blob_sha=?", (blob_sha,))
        connection.execute("DELETE FROM git_blob_cas WHERE blob_sha=?", (blob_sha,))
    connection.execute(
        "DELETE FROM git_content_chunk_cas WHERE chunk_sha256 NOT IN "
        "(SELECT DISTINCT chunk_sha256 FROM git_chunk_occurrence)"
    )
    connection.execute(
        "DELETE FROM git_blob_cas WHERE blob_sha NOT IN "
        "(SELECT DISTINCT blob_sha FROM git_chunk_occurrence) "
        "AND blob_sha NOT IN "
        "(SELECT DISTINCT blob_sha FROM git_file_change WHERE blob_sha IS NOT NULL)"
    )
    for row in connection.execute("SELECT commit_sha,message FROM git_commit_registry"):
        sanitized = redact_text(redact_known_environment_secrets(str(row["message"])))
        if sanitized != row["message"]:
            connection.execute(
                "UPDATE git_commit_registry SET message=? WHERE commit_sha=?",
                (sanitized, row["commit_sha"]),
            )
    return {
        "unsafe_blobs_removed": len(unsafe_blobs),
        "sensitive_occurrences_removed": len(sensitive_occurrences),
        "sensitive_changes_removed": len(sensitive_changes),
    }


def _read_blobs(
    root: Path,
    blob_shas: list[str],
    *,
    deadline: float,
) -> dict[str, bytes]:
    if not blob_shas:
        return {}
    process = subprocess.Popen(  # nosec B603
        [resolve_git_executable(root), "cat-file", "--batch"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    require(
        process.stdin is not None and process.stdout is not None,
        "GIT_CAT_FILE_PIPE_UNAVAILABLE",
        "Git blob batch pipes could not be opened.",
        status="FAIL",
    )
    stdin = cast(BinaryIO, process.stdin)
    stdout = cast(BinaryIO, process.stdout)
    values: dict[str, bytes] = {}
    total_bytes = 0
    try:
        for expected_sha in blob_shas:
            require(
                time.monotonic() <= deadline,
                "GIT_HISTORY_DURATION_BUDGET_EXCEEDED",
                "Git history exceeded its aggregate duration budget.",
                status="BLOCKED",
                max_seconds=MAX_HISTORY_OPERATION_SECONDS,
            )
            stdin.write((expected_sha + "\n").encode("ascii"))
            stdin.flush()
            header = stdout.readline().decode("ascii", errors="replace").strip()
            parts = header.split()
            require(
                len(parts) == 3 and parts[1] == "blob",
                "GIT_BLOB_HEADER_INVALID",
                "Git returned an unexpected blob header.",
                status="FAIL",
                expected_blob=expected_sha,
                header=header,
            )
            size = int(parts[2])
            require(
                0 <= size <= MAX_HISTORY_SINGLE_BLOB_BYTES,
                "GIT_HISTORY_SINGLE_BLOB_BUDGET_EXCEEDED",
                "A Git history blob exceeded the per-object byte budget.",
                status="BLOCKED",
                blob_sha=expected_sha,
                declared_bytes=size,
                max_bytes=MAX_HISTORY_SINGLE_BLOB_BYTES,
            )
            total_bytes += size
            require(
                total_bytes <= MAX_HISTORY_TOTAL_BLOB_BYTES,
                "GIT_HISTORY_TOTAL_BLOB_BUDGET_EXCEEDED",
                "Git history exceeded its aggregate new-blob byte budget.",
                status="BLOCKED",
                total_bytes=total_bytes,
                max_bytes=MAX_HISTORY_TOTAL_BLOB_BYTES,
            )
            data = stdout.read(size)
            separator = stdout.read(1)
            require(
                len(data) == size and separator == b"\n",
                "GIT_BLOB_LENGTH_MISMATCH",
                "Git returned incomplete blob bytes.",
                status="FAIL",
                blob_sha=expected_sha,
                expected_bytes=size,
                actual_bytes=len(data),
            )
            values[expected_sha] = data
    finally:
        stdin.close()
        try:
            process.wait(timeout=30)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    require(
        process.returncode == 0,
        "GIT_CAT_FILE_FAILED",
        "Git blob batch reading failed.",
        status="FAIL",
        returncode=process.returncode,
    )
    return values


def index_git_history(
    connection: sqlite3.Connection,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Append every reachable commit and reuse every existing blob/chunk CAS row."""

    root = Path(repository_root).resolve()
    deadline = time.monotonic() + MAX_HISTORY_OPERATION_SECONDS
    signature = git_history_signature(root)
    create_git_history_schema(connection)
    purge_report = _purge_unsafe_history(connection)
    commits = [
        item
        for item in _git(root, "rev-list", "--topo-order", "--reverse", "--all")
        .decode("ascii", errors="strict")
        .splitlines()
        if item
    ]
    require(
        len(commits) <= MAX_HISTORY_COMMITS,
        "GIT_HISTORY_COMMIT_BUDGET_EXCEEDED",
        "Git history exceeded the reachable-commit budget.",
        status="BLOCKED",
        commit_count=len(commits),
        max_commits=MAX_HISTORY_COMMITS,
    )
    existing_commits = {
        str(row[0])
        for row in connection.execute("SELECT commit_sha FROM git_commit_registry")
    }
    existing_blobs = {
        str(row[0]) for row in connection.execute("SELECT blob_sha FROM git_blob_cas")
    }
    commit_rows = []
    trees: dict[str, list[dict[str, str]]] = {}
    total_tree_entries = 0
    for commit_sha in commits:
        require(
            time.monotonic() <= deadline,
            "GIT_HISTORY_DURATION_BUDGET_EXCEEDED",
            "Git history exceeded its aggregate duration budget.",
            status="BLOCKED",
            max_seconds=MAX_HISTORY_OPERATION_SECONDS,
        )
        row = _commit(root, commit_sha)
        commit_rows.append(row)
        tree = _tree(root, row["commit_sha"])
        total_tree_entries += len(tree)
        require(
            total_tree_entries <= MAX_HISTORY_TREE_ENTRIES,
            "GIT_HISTORY_TREE_ENTRY_BUDGET_EXCEEDED",
            "Git history exceeded the aggregate tree-entry budget.",
            status="BLOCKED",
            tree_entry_count=total_tree_entries,
            max_tree_entries=MAX_HISTORY_TREE_ENTRIES,
        )
        trees[row["commit_sha"]] = tree
    first_commit_for_blob: dict[str, str] = {}
    for row in commit_rows:
        for item in trees[row["commit_sha"]]:
            first_commit_for_blob.setdefault(item["blob_sha"], row["commit_sha"])
    missing_blobs = sorted(set(first_commit_for_blob) - existing_blobs)
    require(
        len(first_commit_for_blob) <= MAX_HISTORY_UNIQUE_BLOBS,
        "GIT_HISTORY_BLOB_COUNT_BUDGET_EXCEEDED",
        "Git history exceeded the unique-blob budget.",
        status="BLOCKED",
        blob_count=len(first_commit_for_blob),
        max_blobs=MAX_HISTORY_UNIQUE_BLOBS,
    )
    blob_bytes = _read_blobs(root, missing_blobs, deadline=deadline)
    excluded_blob_shas = {
        blob_sha
        for blob_sha, data in blob_bytes.items()
        if content_exclusion_reason(data, exclude_opaque_binary=True) is not None
    }
    safe_missing_blobs = [
        blob_sha for blob_sha in missing_blobs if blob_sha not in excluded_blob_shas
    ]
    chunks_created = 0
    chunks_reused = 0
    for blob_sha in safe_missing_blobs:
        data = blob_bytes[blob_sha]
        text, encoding = _decode_blob(data)
        blob_compression, compressed_blob = compress_exact_bytes(data)
        connection.execute(
            """
            INSERT INTO git_blob_cas(
                blob_sha, content_sha256, size_bytes, is_binary, encoding,
                compression,compressed_bytes,first_commit_sha
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blob_sha,
                sha256_bytes(data),
                len(data),
                int(text is None),
                encoding,
                blob_compression,
                compressed_blob,
                first_commit_for_blob[blob_sha],
            ),
        )
        if text is not None:
            for _, _, _, content in _chunks(text):
                content_bytes = content.encode("utf-8")
                digest = sha256_bytes(content_bytes)
                chunk_compression, compressed_content = compress_exact_bytes(
                    content_bytes
                )
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO git_content_chunk_cas(
                        chunk_sha256,size_bytes,compression,compressed_text
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        digest,
                        len(content_bytes),
                        chunk_compression,
                        compressed_content,
                    ),
                )
                if cursor.rowcount:
                    chunks_created += 1
                else:
                    chunks_reused += 1
    for ordinal, row in enumerate(commit_rows):
        connection.execute(
            """
            INSERT OR IGNORE INTO git_commit_registry(
                commit_sha, ordinal, tree_sha, authored_at, committed_at,
                author_name, author_email, committer_name, committer_email, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["commit_sha"],
                ordinal,
                row["tree_sha"],
                row["authored_at"],
                row["committed_at"],
                row["author_name"],
                row["author_email"],
                row["committer_name"],
                row["committer_email"],
                row["message"],
            ),
        )
        for parent_ordinal, parent_sha in enumerate(row["parents"]):
            connection.execute(
                """
                INSERT OR IGNORE INTO git_commit_parent(
                    commit_sha, parent_sha, parent_ordinal
                ) VALUES (?, ?, ?)
                """,
                (row["commit_sha"], parent_sha, parent_ordinal),
            )
        tree_by_path = {item["path"]: item for item in trees[row["commit_sha"]]}
        for change in _changes(root, row["commit_sha"]):
            tree_item = tree_by_path.get(str(change["path"]))
            if tree_item is not None and tree_item["blob_sha"] in excluded_blob_shas:
                continue
            connection.execute(
                """
                INSERT OR IGNORE INTO git_file_change(
                    commit_sha, status, path, prior_path, blob_sha
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["commit_sha"],
                    change["status"],
                    change["path"],
                    change["prior_path"],
                    tree_item["blob_sha"] if tree_item else None,
                ),
            )
        for item in trees[row["commit_sha"]]:
            blob = connection.execute(
                "SELECT content_sha256,size_bytes,is_binary,encoding,compression,"
                "compressed_bytes FROM git_blob_cas WHERE blob_sha=?",
                (item["blob_sha"],),
            ).fetchone()
            if blob is None or int(blob["is_binary"]):
                continue
            blob_data = decompress_exact_bytes(
                compression=str(blob["compression"]),
                payload=bytes(blob["compressed_bytes"]),
                expected_size=int(blob["size_bytes"]),
                expected_sha256=str(blob["content_sha256"]),
            )
            text = blob_data.decode(str(blob["encoding"]))
            for chunk_ordinal, start, end, content in _chunks(text):
                digest = sha256_bytes(content.encode("utf-8"))
                connection.execute(
                    """
                    INSERT OR IGNORE INTO git_chunk_occurrence(
                        commit_sha, path, blob_sha, ordinal, char_start,
                        char_end, chunk_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["commit_sha"],
                        item["path"],
                        item["blob_sha"],
                        chunk_ordinal,
                        start,
                        end,
                        digest,
                    ),
                )
    connection.execute("DELETE FROM git_ref_registry")
    ref_payload = _git(
        root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)%00%(*objectname)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    ).decode("utf-8", errors="replace")
    for line in ref_payload.splitlines():
        if not line:
            continue
        ref_name, object_sha, peeled_sha = (line.split("\x00") + ["", ""])[:3]
        connection.execute(
            """
            INSERT INTO git_ref_registry(
                ref_name, object_sha, peeled_sha, captured_signature
            ) VALUES (?, ?, ?, ?)
            """,
            (ref_name, object_sha, peeled_sha or None, signature),
        )
    connection.execute("DELETE FROM git_history_fts")
    connection.execute(
        """
        INSERT INTO git_history_fts(commit_sha, path, message, text_content)
        SELECT c.commit_sha, '', c.message, '' FROM git_commit_registry c
        """
    )
    chunk_rows = connection.execute(
        """
        SELECT o.commit_sha,o.path,c.chunk_sha256,c.size_bytes,c.compression,
               c.compressed_text
        FROM git_chunk_occurrence AS o
        JOIN git_content_chunk_cas AS c ON c.chunk_sha256=o.chunk_sha256
        ORDER BY o.commit_sha,o.path,o.ordinal
        """
    )
    connection.executemany(
        "INSERT INTO git_history_fts(commit_sha,path,message,text_content) "
        "VALUES(?,?,?,?)",
        (
            (
                str(row["commit_sha"]),
                str(row["path"]),
                "",
                decompress_exact_bytes(
                    compression=str(row["compression"]),
                    payload=bytes(row["compressed_text"]),
                    expected_size=int(row["size_bytes"]),
                    expected_sha256=str(row["chunk_sha256"]),
                ).decode("utf-8"),
            )
            for row in chunk_rows
        ),
    )
    counts = {
        "commits": int(
            connection.execute("SELECT COUNT(*) FROM git_commit_registry").fetchone()[0]
        ),
        "refs": int(
            connection.execute("SELECT COUNT(*) FROM git_ref_registry").fetchone()[0]
        ),
        "blobs": int(
            connection.execute("SELECT COUNT(*) FROM git_blob_cas").fetchone()[0]
        ),
        "chunks": int(
            connection.execute("SELECT COUNT(*) FROM git_content_chunk_cas").fetchone()[
                0
            ]
        ),
        "occurrences": int(
            connection.execute("SELECT COUNT(*) FROM git_chunk_occurrence").fetchone()[
                0
            ]
        ),
        "file_changes": int(
            connection.execute("SELECT COUNT(*) FROM git_file_change").fetchone()[0]
        ),
    }
    return {
        "status": "PASS",
        "signature": signature,
        "reachable_commits": len(commits),
        "new_commits": len(set(commits) - existing_commits),
        "reused_commits": len(set(commits) & existing_commits),
        "new_blobs": len(missing_blobs),
        "safe_new_blobs": len(safe_missing_blobs),
        "excluded_secret_blobs": len(excluded_blob_shas),
        "reused_blobs": len(set(first_commit_for_blob) & existing_blobs),
        "chunk_cas_created": chunks_created,
        "chunk_cas_reused": chunks_reused,
        "counts": counts,
        "single_index_reuse": True,
        "full_reachable_history": counts["commits"] == len(commits),
        "source_policy": {
            "tracked_history_only": True,
            "sensitive_paths_excluded": True,
            "secret_content_excluded": True,
            **purge_report,
        },
    }
