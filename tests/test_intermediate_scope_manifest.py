from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.build_intermediate_scope_manifest import build_manifest


def _git(repository: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repository, check=True)


def test_manifest_accounts_for_included_and_preserved_excluded_bytes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Evidence Lane Test")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "base")

    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repository / "source.py").write_text("value = 1\n", encoding="utf-8")
    generated = repository / ".generated"
    generated.mkdir()
    (generated / "artifact.bin").write_bytes(b"artifact")

    output = repository / "evidence" / "scope.json"
    manifest = build_manifest(
        repository=repository,
        output=output,
        exclude_prefixes=(".generated",),
        exclude_paths=(),
    )

    entries = {entry["path"]: entry for entry in manifest["entries"]}
    assert entries["tracked.txt"]["decision"] == "INCLUDE"
    assert entries["source.py"]["decision"] == "INCLUDE"
    assert entries[".generated/artifact.bin"]["decision"] == (
        "EXCLUDE_PRESERVE_ON_DISK"
    )
    assert entries[".generated/artifact.bin"]["size_bytes"] == 8
    included_bytes = (repository / "tracked.txt").stat().st_size + (
        repository / "source.py"
    ).stat().st_size
    assert manifest["summary"] == {
        "dirty_path_count": 3,
        "included_path_count": 2,
        "included_bytes": included_bytes,
        "excluded_path_count": 1,
        "excluded_bytes": 8,
        "entry_digest_sha256": manifest["summary"]["entry_digest_sha256"],
    }
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "PASS"


def test_all_tracked_scope_fails_closed_until_every_path_is_staged(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Evidence Lane Test")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "base")

    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    output = repository / "evidence" / "scope.json"

    with pytest.raises(RuntimeError, match="scope is not fully staged"):
        build_manifest(
            repository=repository,
            output=output,
            exclude_prefixes=(),
            exclude_paths=(),
            require_fully_staged=True,
            require_no_exclusions=True,
        )

    _git(repository, "add", "tracked.txt", "new.txt")
    manifest = build_manifest(
        repository=repository,
        output=output,
        exclude_prefixes=(),
        exclude_paths=(),
        require_fully_staged=True,
        require_no_exclusions=True,
    )

    assert manifest["scope_mode"] == "ALL_DIRTY_PATHS_INCLUDED"
    assert manifest["require_fully_staged"] is True
    assert manifest["require_no_exclusions"] is True
    assert manifest["summary"]["dirty_path_count"] == 2
    assert manifest["summary"]["included_path_count"] == 2
    assert manifest["summary"]["excluded_path_count"] == 0


def test_all_tracked_scope_rejects_any_exclusion(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Evidence Lane Test")
    generated = repository / "generated"
    generated.mkdir()
    (generated / "artifact.bin").write_bytes(b"artifact")
    _git(repository, "add", "generated/artifact.bin")

    with pytest.raises(RuntimeError, match="forbids exclusions"):
        build_manifest(
            repository=repository,
            output=repository / "evidence" / "scope.json",
            exclude_prefixes=("generated",),
            exclude_paths=(),
            require_fully_staged=True,
            require_no_exclusions=True,
        )
