from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    register_source_batch,
    snapshot_source_authority_registry,
)
from evidence_lane_plugin.source_git_history import (
    build_registered_git_history,
    build_source_git_commit_impact,
)
from evidence_lane_plugin.source_graph import build_registered_source_graph

from .conftest import build_and_approve_pv1


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _repository(root: Path) -> dict[str, str]:
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Git Forensics Test")
    _git(root, "config", "user.email", "git-forensics@example.invalid")
    (root / "lib.py").write_text(
        "def helper():\n    return 1\n", encoding="utf-8"
    )
    (root / "consumer.py").write_text(
        "from lib import helper\n\ndef consumer():\n    return helper()\n",
        encoding="utf-8",
    )
    (root / ".env.example").write_text("TOKEN=example\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "root commit")
    root_commit = _git(root, "rev-parse", "HEAD")

    (root / "lib.py").write_text(
        "def helper():\n    return 2\n", encoding="utf-8"
    )
    _git(root, "add", "lib.py")
    _git(root, "commit", "-m", "change helper")

    _git(root, "mv", "lib.py", "core.py")
    (root / "consumer.py").write_text(
        "from core import helper\n\ndef consumer():\n    return helper()\n",
        encoding="utf-8",
    )
    _git(root, "add", "consumer.py")
    _git(root, "commit", "-m", "rename helper module")
    rename_commit = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "-c", "feature/exact-history")
    (root / "feature.py").write_text(
        "from core import helper\n\ndef feature():\n    return helper()\n",
        encoding="utf-8",
    )
    _git(root, "add", "feature.py")
    _git(root, "commit", "-m", "feature commit")
    feature_commit = _git(root, "rev-parse", "HEAD")

    _git(root, "switch", "main")
    (root / "main.py").write_text(
        "from core import helper\n\ndef main():\n    return helper()\n",
        encoding="utf-8",
    )
    _git(root, "add", "main.py")
    _git(root, "commit", "-m", "main commit")
    _git(root, "merge", "--no-ff", "feature/exact-history", "-m", "merge feature")
    merge_commit = _git(root, "rev-parse", "HEAD")
    return {
        "root": root_commit,
        "rename": rename_commit,
        "feature": feature_commit,
        "merge": merge_commit,
    }


def _rows(path: Path, sql: str, parameters: tuple[object, ...] = ()) -> list[dict]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, parameters)]
    finally:
        connection.close()


def test_registered_git_history_seals_refs_objects_parents_renames_hunks_and_impact(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    commits = _repository(repository)
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(repository), 1, "local_code")],
    )
    graph = build_registered_source_graph(registry, str(batch["batch_id"]))

    history = build_registered_git_history(
        registry,
        str(batch["batch_id"]),
        1,
    )
    reused = build_registered_git_history(
        registry,
        str(batch["batch_id"]),
        1,
    )

    assert history["status"] == "PASS_WITH_POLICY_EXCLUSIONS"
    assert history["full_reachable_history"] is True
    assert history["all_parent_comparisons"] is True
    assert history["counts"]["commits"] == 6
    assert history["counts"]["parent_edges"] == 6
    assert history["counts"]["renames"] >= 1
    assert history["counts"]["hunks"] > 0
    assert history["counts"]["changed_lines"] > 0
    assert history["counts"]["policy_excluded_tree_paths"] > 0
    assert reused["append_status"] == "IDEMPOTENT_REUSE"
    assert reused["history_root_sha256"] == history["history_root_sha256"]

    object_types = {
        row["object_type"]
        for row in _rows(
            registry,
            "SELECT DISTINCT object_type FROM source_git_object WHERE snapshot_id=?",
            (history["snapshot_id"],),
        )
    }
    assert {"blob", "tree", "commit"} <= object_types
    assert _rows(
        registry,
        """SELECT parent_ordinal FROM source_git_parent
        WHERE snapshot_id=? AND commit_sha=? ORDER BY parent_ordinal""",
        (history["snapshot_id"], commits["merge"]),
    ) == [{"parent_ordinal": 0}, {"parent_ordinal": 1}]
    assert _rows(
        registry,
        """SELECT COUNT(*) AS count FROM source_git_file_change
        WHERE snapshot_id=? AND commit_sha=?""",
        (history["snapshot_id"], commits["root"]),
    )[0]["count"] == 3
    assert all(
        "TOKEN=example" not in str(value)
        for row in _rows(registry, "SELECT * FROM source_git_changed_line")
        for value in row.values()
    )

    impact = build_source_git_commit_impact(
        registry,
        history["snapshot_id"],
        graph["graph_id"],
        commits["rename"],
        parent_ordinal=0,
        relations=["IMPORTS", "CALLS"],
        direction="UPSTREAM",
        max_depth=3,
    )
    repeated_impact = build_source_git_commit_impact(
        registry,
        history["snapshot_id"],
        graph["graph_id"],
        commits["rename"],
        parent_ordinal=0,
        relations=["IMPORTS", "CALLS"],
        direction="UPSTREAM",
        max_depth=3,
    )
    assert impact["mapped_path_count"] >= 2
    assert impact["graph_impact_id"]
    assert repeated_impact["append_status"] == "IDEMPOTENT_REUSE"
    snapshot = snapshot_source_authority_registry(registry)
    assert snapshot["git_projection"]["snapshot_count"] == 1
    assert snapshot["git_projection"]["impact_count"] == 1


def test_git_history_rejects_extracted_folder_without_git_metadata(
    tmp_path: Path,
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "app.py").write_text("print('not history')\n", encoding="utf-8")
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(extracted), 1, "local_code")],
    )

    with pytest.raises(EvidenceLaneError) as missing:
        build_registered_git_history(registry, str(batch["batch_id"]), 1)

    assert missing.value.code == "SOURCE_GIT_METADATA_MISSING"
    assert _rows(registry, "SELECT snapshot_id FROM source_git_snapshot") == []


def test_git_history_fails_closed_when_registered_worktree_changes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(repository), 1, "local_code")],
    )
    (repository / "core.py").write_text(
        "def helper():\n    return 999\n", encoding="utf-8"
    )

    with pytest.raises(EvidenceLaneError) as stale:
        build_registered_git_history(registry, str(batch["batch_id"]), 1)

    assert stale.value.code == "SOURCE_GIT_REGISTERED_BYTES_CHANGED"
    assert _rows(registry, "SELECT snapshot_id FROM source_git_snapshot") == []


def test_git_history_bounds_fail_without_partial_snapshot(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    _repository(repository)
    registry = tmp_path / "authority.sqlite"
    batch = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(repository), 1, "local_code")],
    )

    with pytest.raises(EvidenceLaneError) as bounded:
        build_registered_git_history(
            registry,
            str(batch["batch_id"]),
            1,
            max_commits=1,
        )

    assert bounded.value.code == "SOURCE_GIT_COMMIT_BOUND_OR_IDENTITY_INVALID"
    assert _rows(registry, "SELECT snapshot_id FROM source_git_snapshot") == []


def test_service_git_history_and_impact_preserve_session_and_pointer(
    service,
    tmp_path: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    repository = tmp_path / "service-history"
    commits = _repository(repository)
    registry = service.store.source_authority_path("book-faires")
    batch = register_source_batch(
        registry,
        [SourceAuthoritySpec(str(repository), 1, "local_code")],
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="research",
        requested_outcome="Seal exact Git history and map semantic impact.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=[],
        stop_condition="Stop after the Git history and impact receipts are recorded.",
    )
    graph = service.source_graph_build(
        "book-faires",
        str(batch["batch_id"]),
        occurrence_ordinals=[1],
        session_id=session_id,
    )
    state_before = service.sessions.load("book-faires", session_id).as_dict()
    pointer_before = service.store.pointer("book-faires").as_dict()

    history = service.source_git_history_build(
        "book-faires",
        str(batch["batch_id"]),
        1,
        session_id=session_id,
    )
    impact = service.source_git_commit_impact(
        "book-faires",
        history["snapshot_id"],
        graph["graph_id"],
        commits["rename"],
        relations=["IMPORTS", "CALLS"],
        session_id=session_id,
    )

    state_after = service.sessions.load("book-faires", session_id).as_dict()
    assert history["chat_lineage"]["append_status"] == "APPENDED"
    assert impact["chat_lineage"]["append_status"] == "APPENDED"
    assert state_after["state"] == state_before["state"]
    assert state_after["task"] == state_before["task"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before
