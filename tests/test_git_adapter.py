from __future__ import annotations

import json
import subprocess
from pathlib import Path

from evidence_lane_plugin.git_adapter import (
    calculate_worktree_change_identity,
    calculate_worktree_sha256,
    inspect_repository,
)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_repository_provider_requires_exact_normalized_github_host(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Provider Classification Test")
    _git(repository, "config", "user.email", "provider-test@example.invalid")
    (repository / "README.md").write_text("provider test\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "provider fixture")
    _git(repository, "remote", "add", "origin", "https://github.com/example/repo.git")

    cases = (
        ("https://github.com/example/repo.git", "github"),
        ("ssh://git@GITHUB.COM:22/example/repo.git", "github"),
        ("git@github.com.:example/repo.git", "github"),
        ("https://github.com.attacker.example/example/repo.git", "git"),
        ("git@github.com.attacker.example:example/repo.git", "git"),
        ("https://attacker.example/github.com/example/repo.git", "git"),
    )

    for remote, expected_provider in cases:
        _git(repository, "remote", "set-url", "origin", remote)
        identity = inspect_repository(repository)
        assert identity.provider == expected_provider, remote


def test_worktree_change_identity_seals_tracked_and_untracked_bytes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Change Identity Test")
    _git(repository, "config", "user.email", "change-test@example.invalid")
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    (repository / "README.md").write_text("tracked\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "README.md")
    _git(repository, "commit", "-m", "change identity fixture")

    (repository / "README.md").write_text("tracked changed\n", encoding="utf-8")
    (repository / "private-note.txt").write_text("alpha\n", encoding="utf-8")
    (repository / "ignored.txt").write_text("ignored bytes\n", encoding="utf-8")
    first = calculate_worktree_change_identity(repository)

    assert first["schema"] == "evidence-lane.git-worktree-change-identity.v1"
    assert first["dirty_path_count"] == 2
    assert first["tracked_dirty_path_count"] == 1
    assert first["untracked_path_count"] == 1
    assert first["content_identity_count"] == 2
    assert first["raw_paths_persisted"] is False
    assert first["ignored_paths_included"] is False
    assert "private-note.txt" not in json.dumps(first)
    assert "ignored.txt" not in json.dumps(first)

    (repository / "private-note.txt").write_text("bravo\n", encoding="utf-8")
    second = calculate_worktree_change_identity(repository)

    assert second["complete_path_set_sha256"] == first["complete_path_set_sha256"]
    assert second["tracked_head_diff_sha256"] == first["tracked_head_diff_sha256"]
    assert second["untracked_content_sha256"] != first["untracked_content_sha256"]
    assert second["dirty_content_sha256"] != first["dirty_content_sha256"]
    assert second["working_identity_sha256"] != first["working_identity_sha256"]


def test_change_identity_does_not_rehash_clean_tracked_content(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Bounded Identity Test")
    _git(repository, "config", "user.email", "bounded-test@example.invalid")
    clean_large = repository / "clean-large.bin"
    with clean_large.open("wb") as stream:
        stream.seek((65 * 1024 * 1024) - 1)
        stream.write(b"\0")
    _git(repository, "add", "clean-large.bin")
    _git(repository, "commit", "-m", "large clean fixture")
    dirty = repository / "dirty.txt"
    dirty.write_text("only-dirty-bytes\n", encoding="utf-8")

    identity = calculate_worktree_change_identity(repository)

    assert identity["clean_member_content_rehashed"] is False
    assert identity["dirty_content_budget"]["consumed_file_count"] == 1
    assert identity["dirty_content_budget"]["consumed_bytes"] == dirty.stat().st_size
    assert calculate_worktree_sha256(repository) == identity["working_identity_sha256"]
