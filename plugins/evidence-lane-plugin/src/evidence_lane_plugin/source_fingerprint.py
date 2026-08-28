"""Per-file Git-tracked current-worktree fingerprint receipts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file


def _tracked_index_rows(repository_root: Path) -> list[dict[str, str]]:
    completed = subprocess.run(
        ["git", "-C", str(repository_root), "ls-files", "-z", "--stage"],
        check=True,
        capture_output=True,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        ),
    )
    rows: list[dict[str, str]] = []
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", maxsplit=1)
        mode, object_id, stage = metadata.decode("ascii").split(" ")
        if stage != "0":
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape").replace(
            "\\", "/"
        )
        rows.append(
            {
                "path": relative,
                "index_mode": mode,
                "index_object_id": object_id,
            }
        )
    rows.sort(key=lambda row: row["path"])
    require(
        rows and len(rows) == len({row["path"] for row in rows}),
        "TRACKED_SOURCE_FINGERPRINT_INDEX_INVALID",
        "The Git index path set is empty, duplicated, or conflicted.",
        status="MISMATCH",
    )
    return rows


def tracked_worktree_file_manifest(repository_path: str | Path) -> dict[str, Any]:
    """Hash every stage-0 tracked path using its current worktree bytes."""

    root = Path(repository_path).resolve()
    require(
        root.is_dir() and (root / ".git").exists(),
        "TRACKED_SOURCE_FINGERPRINT_REPOSITORY_REQUIRED",
        "Per-file source fingerprinting requires the exact Git worktree root.",
        status="BLOCKED",
    )
    entries: list[dict[str, Any]] = []
    for row in _tracked_index_rows(root):
        path = (root / row["path"]).resolve()
        path.relative_to(root)
        if path.is_symlink():
            link_bytes = os.readlink(path).encode("utf-8")
            entry = {
                **row,
                "state": "CURRENT_WORKTREE_SYMLINK_TARGET_BYTES",
                "current_sha256": sha256_bytes(link_bytes),
                "current_bytes": len(link_bytes),
            }
        elif path.is_file():
            entry = {
                **row,
                "state": "CURRENT_WORKTREE_FILE_BYTES",
                "current_sha256": sha256_file(path),
                "current_bytes": path.stat().st_size,
            }
        else:
            entry = {
                **row,
                "state": "TRACKED_DELETED_IN_WORKTREE",
                "current_sha256": None,
                "current_bytes": 0,
            }
        entries.append(entry)
    path_projection = [row["path"] for row in entries]
    core = {
        "schema": "evidence-lane.git-tracked-worktree-file-manifest.v1",
        "status": "PASS",
        "selection": "GIT_INDEX_STAGE0_PATH_SET_WITH_CURRENT_WORKTREE_BYTES",
        "tracked_path_count": len(entries),
        "current_file_count": sum(
            row["state"] == "CURRENT_WORKTREE_FILE_BYTES" for row in entries
        ),
        "current_symlink_count": sum(
            row["state"] == "CURRENT_WORKTREE_SYMLINK_TARGET_BYTES"
            for row in entries
        ),
        "tracked_deleted_count": sum(
            row["state"] == "TRACKED_DELETED_IN_WORKTREE" for row in entries
        ),
        "path_set_sha256": sha256_bytes(canonical_json_bytes(path_projection)),
        "file_manifest_sha256": sha256_bytes(canonical_json_bytes(entries)),
        "entries": entries,
        "untracked_paths_included": False,
        "ignored_paths_included": False,
        "remote_git_mutated": False,
        "git_index_mutated": False,
        "git_ref_mutated": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def staged_index_file_manifest(repository_path: str | Path) -> dict[str, Any]:
    """Hash every exact stage-0 Git index blob, independent of worktree drift."""

    root = Path(repository_path).resolve()
    require(
        root.is_dir() and (root / ".git").exists(),
        "STAGED_FINGERPRINT_REPOSITORY_REQUIRED",
        "A staged-tree fingerprint requires the exact Git worktree root.",
        status="BLOCKED",
    )
    index_rows = _tracked_index_rows(root)
    entries: list[dict[str, Any]] = []
    for row in index_rows:
        object_id = row["index_object_id"]
        require(
            bool(object_id) and all(character in "0123456789abcdef" for character in object_id),
            "STAGED_FINGERPRINT_OBJECT_ID_INVALID",
            "The Git index contains a non-canonical object identity.",
            status="MISMATCH",
            path=row["path"],
        )
        completed = subprocess.run(
            ["git", "-C", str(root), "cat-file", "blob", object_id],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            ),
        )
        data = completed.stdout
        entries.append(
            {
                **row,
                "state": "EXACT_GIT_INDEX_BLOB",
                "staged_sha256": sha256_bytes(data),
                "staged_bytes": len(data),
                "lfs_pointer": data.startswith(b"version https://git-lfs.github.com/spec/v1"),
            }
        )
    core = {
        "schema": "evidence-lane.git-staged-index-file-manifest.v1",
        "status": "PASS",
        "selection": "GIT_INDEX_STAGE0_EXACT_BLOB_BYTES",
        "staged_path_count": len(entries),
        "staged_bytes": sum(int(row["staged_bytes"]) for row in entries),
        "lfs_pointer_count": sum(bool(row["lfs_pointer"]) for row in entries),
        "path_set_sha256": sha256_bytes(
            canonical_json_bytes([row["path"] for row in entries])
        ),
        "staged_manifest_sha256": sha256_bytes(canonical_json_bytes(entries)),
        "entries": entries,
        "current_worktree_bytes_substituted": False,
        "untracked_paths_included": False,
        "ignored_paths_included": False,
        "git_index_mutated": False,
        "git_ref_mutated": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


__all__ = ["staged_index_file_manifest", "tracked_worktree_file_manifest"]
