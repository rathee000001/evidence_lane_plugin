from __future__ import annotations

import json
from pathlib import Path

from evidence_lane_plugin import freshness
from evidence_lane_plugin.models import RepositoryIdentity


class _Config:
    repository_path = "C:/governed/repository"


class _Store:
    def config(self, project_id: str) -> _Config:
        assert project_id == "project"
        return _Config()


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "pv"
    root.mkdir()
    (root / "project_identity.json").write_text(
        json.dumps(
            {
                "repository": {
                    "owner": "owner",
                    "name": "repository",
                    "repository_url": "https://github.com/owner/repository",
                    "commit_sha": "1" * 40,
                    "tree_sha": "2" * 40,
                    "worktree_sha256": "A" * 64,
                }
            }
        ),
        encoding="utf-8",
    )
    return root


def test_bounded_dirty_read_does_not_hash_dirty_worktree(
    tmp_path: Path, monkeypatch
) -> None:
    package = _package(tmp_path)
    monkeypatch.setattr(
        freshness,
        "inspect_repository",
        lambda repository: RepositoryIdentity(
            provider="github",
            repository_url="https://github.com/owner/repository",
            owner="owner",
            name="repository",
            branch="main",
            commit_sha="1" * 40,
            tree_sha="2" * 40,
            is_clean=False,
        ),
    )
    monkeypatch.setattr(
        freshness,
        "identity_json",
        lambda identity, repository: (_ for _ in ()).throw(
            AssertionError("bounded dirty reads must not hash dirty bytes")
        ),
    )

    result = freshness.evaluate_freshness(
        _Store(),
        "project",
        package,
        bounded_dirty_read=True,
    )

    assert result["state"] == "DIRTY_WORKING_TREE"
    assert result["live_worktree_sha256"] is None
    assert result["worktree_identity_evaluated"] is False
    assert result["bounded_dirty_read"] is True
