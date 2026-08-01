"""Full reachable Git history with content-addressed blob and chunk reuse."""

from __future__ import annotations

import shutil
import sqlite3
import subprocess  # nosec B404
from pathlib import Path
from typing import Any, BinaryIO, cast

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes

_TEXT_CHUNK_CHARS = 6000
_TEXT_CHUNK_OVERLAP = 500


def _git_executable() -> str:
    executable = shutil.which("git")
    require(
        executable is not None,
        "GIT_EXECUTABLE_MISSING",
        "Git is required for full-history indexing.",
        status="BLOCKED",
    )
    return cast(str, executable)


def _git(root: Path, *args: str, timeout: int = 180) -> bytes:
    completed = subprocess.run(  # nosec B603
        [_git_executable(), *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
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
            exact_bytes BLOB NOT NULL,
            first_commit_sha TEXT NOT NULL
        ) STRICT;
        CREATE TABLE IF NOT EXISTS git_content_chunk_cas(
            chunk_sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            text_content TEXT NOT NULL
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
            path,
            message,
            text_content,
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
        "message": values[9].rstrip("\r\n"),
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
        rows.append(
            {
                "mode": mode,
                "blob_sha": object_sha,
                "path": path.decode("utf-8", errors="replace"),
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
        rows.append({"status": status, "path": path, "prior_path": prior_path})
    return rows


def _read_blobs(root: Path, blob_shas: list[str]) -> dict[str, bytes]:
    if not blob_shas:
        return {}
    process = subprocess.Popen(  # nosec B603
        [_git_executable(), "cat-file", "--batch"],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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
    try:
        for expected_sha in blob_shas:
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
        process.wait(timeout=30)
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
    signature = git_history_signature(root)
    create_git_history_schema(connection)
    commits = [
        item
        for item in _git(root, "rev-list", "--topo-order", "--reverse", "--all")
        .decode("ascii", errors="strict")
        .splitlines()
        if item
    ]
    existing_commits = {
        str(row[0])
        for row in connection.execute("SELECT commit_sha FROM git_commit_registry")
    }
    existing_blobs = {
        str(row[0]) for row in connection.execute("SELECT blob_sha FROM git_blob_cas")
    }
    commit_rows = [_commit(root, commit_sha) for commit_sha in commits]
    trees = {row["commit_sha"]: _tree(root, row["commit_sha"]) for row in commit_rows}
    first_commit_for_blob: dict[str, str] = {}
    for row in commit_rows:
        for item in trees[row["commit_sha"]]:
            first_commit_for_blob.setdefault(item["blob_sha"], row["commit_sha"])
    missing_blobs = sorted(set(first_commit_for_blob) - existing_blobs)
    blob_bytes = _read_blobs(root, missing_blobs)
    chunks_created = 0
    chunks_reused = 0
    for blob_sha in missing_blobs:
        data = blob_bytes[blob_sha]
        text, encoding = _decode_blob(data)
        connection.execute(
            """
            INSERT INTO git_blob_cas(
                blob_sha, content_sha256, size_bytes, is_binary, encoding,
                exact_bytes, first_commit_sha
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                blob_sha,
                sha256_bytes(data),
                len(data),
                int(text is None),
                encoding,
                data,
                first_commit_for_blob[blob_sha],
            ),
        )
        if text is not None:
            for _, _, _, content in _chunks(text):
                digest = sha256_bytes(content.encode("utf-8"))
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO git_content_chunk_cas(
                        chunk_sha256, size_bytes, text_content
                    ) VALUES (?, ?, ?)
                    """,
                    (digest, len(content.encode("utf-8")), content),
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
                "SELECT is_binary, encoding, exact_bytes FROM git_blob_cas WHERE blob_sha=?",
                (item["blob_sha"],),
            ).fetchone()
            if blob is None or int(blob["is_binary"]):
                continue
            text = bytes(blob["exact_bytes"]).decode(str(blob["encoding"]))
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
    connection.execute(
        """
        INSERT INTO git_history_fts(commit_sha, path, message, text_content)
        SELECT o.commit_sha, o.path, '', c.text_content
        FROM git_chunk_occurrence o
        JOIN git_content_chunk_cas c ON c.chunk_sha256=o.chunk_sha256
        """
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
        "reused_blobs": len(set(first_commit_for_blob) & existing_blobs),
        "chunk_cas_created": chunks_created,
        "chunk_cas_reused": chunks_reused,
        "counts": counts,
        "single_index_reuse": True,
        "full_reachable_history": counts["commits"] == len(commits),
    }
