from __future__ import annotations

from pathlib import Path
import subprocess

from evidence_lane_plugin.source_fingerprint import (
    staged_index_file_manifest,
    tracked_worktree_file_manifest,
)


def test_tracked_worktree_manifest_hashes_current_files_and_tracked_deletion(
    source_repository: Path,
) -> None:
    first = tracked_worktree_file_manifest(source_repository)
    app = source_repository / "src" / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + "\nACTIVE_DELTA = True\n")
    deleted = source_repository / "fixture.bin"
    deleted.unlink()
    untracked = source_repository / "local-only.tmp"
    untracked.write_text("not admitted", encoding="utf-8")

    second = tracked_worktree_file_manifest(source_repository)
    replay = tracked_worktree_file_manifest(source_repository)
    by_path = {row["path"]: row for row in second["entries"]}

    assert second == replay
    assert second["path_set_sha256"] == first["path_set_sha256"]
    assert second["file_manifest_sha256"] != first["file_manifest_sha256"]
    assert by_path["src/app.py"]["state"] == "CURRENT_WORKTREE_FILE_BYTES"
    assert by_path["fixture.bin"]["state"] == "TRACKED_DELETED_IN_WORKTREE"
    assert "local-only.tmp" not in by_path
    assert second["untracked_paths_included"] is False
    assert second["git_index_mutated"] is False
    assert second["git_ref_mutated"] is False


def test_staged_manifest_hashes_index_not_unstaged_worktree(
    source_repository: Path,
) -> None:
    first = staged_index_file_manifest(source_repository)
    app = source_repository / "src" / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + "\nUNSTAGED = True\n", encoding="utf-8")
    unstaged = staged_index_file_manifest(source_repository)
    assert unstaged == first

    subprocess.run(
        ["git", "-C", str(source_repository), "add", "--", "src/app.py"],
        check=True,
    )
    staged = staged_index_file_manifest(source_repository)
    assert staged["staged_manifest_sha256"] != first["staged_manifest_sha256"]
    assert staged["current_worktree_bytes_substituted"] is False
    assert staged["untracked_paths_included"] is False
    assert staged["git_index_mutated"] is False
    assert staged["git_ref_mutated"] is False
