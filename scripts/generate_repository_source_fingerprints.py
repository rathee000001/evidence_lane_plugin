#!/usr/bin/env python3
"""Generate per-file refresh receipts for one exact feature commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "plugins" / "evidence-lane-plugin" / "src"
if str(PLUGIN_SOURCE) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SOURCE))

from evidence_lane_plugin.hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
)

OUTPUT = ROOT / ".github" / "evidence-lane-repository-fingerprints.v1.json"
OUTPUT_RELATIVE = OUTPUT.relative_to(ROOT).as_posix()


def _git(*arguments: str, input_bytes: bytes | None = None) -> bytes:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        input=input_bytes,
        capture_output=True,
    )
    return completed.stdout


def _commit(value: str) -> str:
    return _git("rev-parse", "--verify", f"{value}^{{commit}}").decode("ascii").strip()


def _tree_rows(commit_sha: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    output = _git("ls-tree", "-r", "-z", "--full-tree", commit_sha)
    for record in output.split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", maxsplit=1)
        mode, object_type, object_id = metadata.decode("ascii").split(" ")
        if object_type != "blob":
            continue
        path = raw_path.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        rows[path] = {
            "path": path,
            "index_mode": mode,
            "index_object_id": object_id,
        }
    return rows


def _blob_fingerprints(object_ids: set[str]) -> dict[str, dict[str, Any]]:
    ordered = sorted(object_ids)
    output = _git("cat-file", "--batch", input_bytes=("\n".join(ordered) + "\n").encode("ascii"))
    offset = 0
    fingerprints: dict[str, dict[str, Any]] = {}
    for expected_id in ordered:
        newline = output.index(b"\n", offset)
        header = output[offset:newline].decode("ascii")
        object_id, object_type, size_text = header.split(" ")
        if object_id != expected_id or object_type != "blob":
            raise RuntimeError("REPOSITORY_FINGERPRINT_BLOB_IDENTITY_MISMATCH")
        size = int(size_text)
        start = newline + 1
        end = start + size
        data = output[start:end]
        if output[end : end + 1] != b"\n":
            raise RuntimeError("REPOSITORY_FINGERPRINT_BATCH_BOUNDARY_INVALID")
        fingerprints[object_id] = {
            "staged_sha256": hashlib.sha256(data).hexdigest().upper(),
            "staged_bytes": size,
            "lfs_pointer": data.startswith(b"version https://git-lfs.github.com/spec/v1"),
        }
        offset = end + 1
    return fingerprints


def _commit_subject(commit_sha: str) -> str:
    return _git("show", "-s", "--format=%s", commit_sha).decode("utf-8").rstrip("\n")


def build_manifest(
    *,
    source_commit: str,
    baseline_commit: str,
    refreshed_at: str,
) -> dict[str, Any]:
    source_sha = _commit(source_commit)
    baseline_sha = _commit(baseline_commit)
    source_rows = _tree_rows(source_sha)
    baseline_rows = _tree_rows(baseline_sha)
    source_rows.pop(OUTPUT_RELATIVE, None)
    baseline_rows.pop(OUTPUT_RELATIVE, None)
    fingerprints = _blob_fingerprints(
        {row["index_object_id"] for row in source_rows.values()}
    )
    entries: list[dict[str, Any]] = []
    for path in sorted(source_rows):
        row = source_rows[path]
        baseline = baseline_rows.get(path)
        if baseline is None:
            refresh_status = "ADDED_REFRESH_VERIFIED"
        elif (
            baseline["index_mode"] == row["index_mode"]
            and baseline["index_object_id"] == row["index_object_id"]
        ):
            refresh_status = "UNCHANGED_REFRESH_VERIFIED"
        else:
            refresh_status = "CHANGED_REFRESH_VERIFIED"
        receipt_core = {
            **row,
            **fingerprints[row["index_object_id"]],
            "refresh_status": refresh_status,
            "refresh_commit_sha": source_sha,
            "refreshed_at": refreshed_at,
        }
        entries.append(
            {
                **receipt_core,
                "refresh_receipt_sha256": sha256_bytes(
                    canonical_json_bytes(receipt_core)
                ),
            }
        )
    removed_paths = sorted(set(baseline_rows) - set(source_rows))
    status_counts = {
        status: sum(row["refresh_status"] == status for row in entries)
        for status in (
            "ADDED_REFRESH_VERIFIED",
            "CHANGED_REFRESH_VERIFIED",
            "UNCHANGED_REFRESH_VERIFIED",
        )
    }
    source_tree_sha = _git("rev-parse", f"{source_sha}^{{tree}}").decode("ascii").strip()
    core = {
        "schema": "evidence-lane.repository-source-fingerprints.v2",
        "status": "PASS",
        "selection": "EXACT_FEATURE_COMMIT_GIT_BLOB_BYTES",
        "source_commit_sha": source_sha,
        "source_tree_sha": source_tree_sha,
        "source_commit_subject_sha256": sha256_bytes(
            _commit_subject(source_sha).encode("utf-8")
        ),
        "baseline_commit_sha": baseline_sha,
        "refreshed_at": refreshed_at,
        "tracked_path_count": len(entries),
        "tracked_bytes": sum(int(row["staged_bytes"]) for row in entries),
        "lfs_pointer_count": sum(bool(row["lfs_pointer"]) for row in entries),
        "status_counts": status_counts,
        "path_set_sha256": sha256_bytes(
            canonical_json_bytes([row["path"] for row in entries])
        ),
        "file_receipt_manifest_sha256": sha256_bytes(canonical_json_bytes(entries)),
        "entries": entries,
        "removed_since_baseline": removed_paths,
        "excluded_self_referential_paths": [OUTPUT_RELATIVE],
        "self_reference_law": (
            "THE_RECEIPT_COMMIT_TREE_BINDS_THIS_MANIFEST; THE_MANIFEST CANNOT "
            "CONTAIN ITS OWN FINAL BLOB HASH"
        ),
        "current_worktree_bytes_substituted": False,
        "untracked_paths_included": False,
        "ignored_paths_included": False,
        "git_ref_mutated": False,
    }
    return {
        **core,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(core)),
    }


def _default_refresh_time() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source-commit")
    parser.add_argument("--baseline-commit")
    parser.add_argument("--refreshed-at")
    args = parser.parse_args()
    if args.check:
        if not OUTPUT.is_file():
            raise SystemExit("REPOSITORY_SOURCE_FINGERPRINT_MANIFEST_MISSING")
        saved = json.loads(OUTPUT.read_text(encoding="utf-8"))
        source_commit = str(saved["source_commit_sha"])
        baseline_commit = str(saved["baseline_commit_sha"])
        refreshed_at = str(saved["refreshed_at"])
        expected_parent = _commit("HEAD^")
        if expected_parent != source_commit:
            raise SystemExit("REPOSITORY_SOURCE_FINGERPRINT_RECEIPT_PARENT_MISMATCH")
    else:
        source_commit = args.source_commit or "HEAD"
        baseline_commit = args.baseline_commit or f"{source_commit}^"
        refreshed_at = args.refreshed_at or _default_refresh_time()
    manifest = build_manifest(
        source_commit=source_commit,
        baseline_commit=baseline_commit,
        refreshed_at=refreshed_at,
    )
    exact_bytes = canonical_json_bytes(manifest)
    if args.check:
        if OUTPUT.read_bytes() != exact_bytes:
            raise SystemExit("REPOSITORY_SOURCE_FINGERPRINT_MANIFEST_STALE")
    else:
        atomic_write_json(OUTPUT, manifest)
    print(
        json.dumps(
            {
                "status": "PASS",
                "path": OUTPUT_RELATIVE,
                "source_commit_sha": manifest["source_commit_sha"],
                "tracked_path_count": manifest["tracked_path_count"],
                "status_counts": manifest["status_counts"],
                "receipt_sha256": manifest["receipt_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
