from __future__ import annotations

import subprocess
from pathlib import Path

from evidence_lane_plugin.git_adapter import inspect_repository


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
