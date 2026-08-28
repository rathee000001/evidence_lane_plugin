from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "apps" / "evidence-lane-remote-adapter"
PUBLIC_ROOT = ADAPTER / "public" / "dummy-lane-packages"
INDEX_PATH = ADAPTER / "app" / "_data" / "dummy-lane-artifacts.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_all_canonical_lane_dummy_packages_are_exact_and_downloadable() -> None:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    assert index["lane_count"] == 18
    assert index["canonical_file_count_per_lane"] == 4
    assert index["derived_render_count_per_lane"] == 2
    assert index["artifact_storage"] == "WEBSITE_STATIC_PUBLIC"
    assert index["topology_boundary"] == (
        "ACTUAL_FULL_LANE_ENGINE_MMD_AND_DOT_NOT_FOUR_FILE_OVERVIEW"
    )
    assert index["graph_projection_boundary"] == (
        "SQLITE_DERIVED_STABLE_IDENTITY_GRAPH_WITH_DISTINCT_GITHUB_AND_LOCAL_CODE_PROFILES"
    )
    assert index["renderer"]["install_performed"] is False
    assert [row["lane_id"] for row in index["lanes"]] == list(CANONICAL_LANE_IDS)
    assert {path.name for path in PUBLIC_ROOT.iterdir() if path.is_dir()} == set(
        CANONICAL_LANE_IDS
    )

    for row in index["lanes"]:
        lane_id = row["lane_id"]
        definition = LANE_REGISTRY[lane_id]
        lane_root = PUBLIC_ROOT / lane_id
        expected = {
            definition.sqlite_filename,
            definition.mmd_filename,
            definition.dot_filename,
            "refresh_receipt.json",
            f"{lane_id}.mmd.8k.png",
            f"{lane_id}.mmd.vector.svg",
        }
        assert {path.name for path in lane_root.iterdir() if path.is_file()} == expected
        assert len(row["canonical_artifacts"]) == 4
        for artifact in [
            *row["canonical_artifacts"],
            row["render"],
            row["vector_render"],
        ]:
            path = ADAPTER / "public" / artifact["url"].removeprefix("/")
            assert path.is_file()
            assert path.name == artifact["filename"]
            assert path.stat().st_size == artifact["bytes"]
            assert _sha256(path) == artifact["sha256"]

        database = lane_root / definition.sqlite_filename
        with sqlite3.connect(database) as connection:
            table_names = {
                row[1]
                for row in connection.execute("PRAGMA table_list")
                if row[2] in {"table", "virtual"} and not row[1].startswith("sqlite_")
            }
            assert table_names == set(definition.schema_contract)
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            metadata = dict(connection.execute("SELECT key, value FROM lane_meta"))
            assert metadata["lane_id"] == lane_id
            assert metadata["schema_version"] == "evidence-lane.universal-lane.v2"

        mmd_text = (lane_root / definition.mmd_filename).read_text(encoding="utf-8")
        dot_text = (lane_root / definition.dot_filename).read_text(encoding="utf-8")
        assert 'subgraph SOURCE_INTAKE["1. Source intake and exact-byte registry"]' in mmd_text
        assert 'subgraph SEMANTIC_MODEL["2. Lane-specific semantic model from SQLite"]' in mmd_text
        assert 'subgraph SQLITE_PHYSICAL_SCHEMA["3. Additive SQLite physical schema contract"]' in mmd_text
        assert "subgraph RETRIEVAL[" in mmd_text
        assert "Retrieval and changed-section reuse" in mmd_text
        assert "subgraph LIFECYCLE[" in mmd_text
        assert "Refresh, pointer, and mutation evidence" in mmd_text
        assert "subgraph OUTPUTS[" in mmd_text
        assert "Inspectable lane package" in mmd_text
        assert "digraph" in dot_text
        assert "SQLite physical schema" in dot_text
        assert len(row["topology_generator_sha256"]) == 64
        assert row["graph_profile"] == (
            "GITHUB_REPOSITORY_HISTORY"
            if lane_id == "github_code"
            else "LOCAL_WORKTREE"
            if lane_id == "local_code"
            else "SQLITE_SCHEMA_RELATION_SAMPLE"
        )

        with Image.open(lane_root / f"{lane_id}.mmd.8k.png") as render:
            assert render.size == (7680, 4320)
            assert render.format == "PNG"

        vector = (lane_root / f"{lane_id}.mmd.vector.svg").read_text(encoding="utf-8")
        assert "<svg" in vector
        assert "viewBox=" in vector
        assert "<script" not in vector.casefold()
        assert "javascript:" not in vector.casefold()
        assert row["render"]["source_mmd_sha256"] == row["vector_render"]["source_mmd_sha256"]
        assert row["render"]["rasterizer"] == "stable_svg_chromium_screenshot"


def test_dummy_git_lane_proves_real_multi_commit_parent_history() -> None:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    by_lane = {row["lane_id"]: row for row in index["lanes"]}
    lane_id = "github_code"
    row = by_lane[lane_id]
    history = row["git_history"]
    assert len(history) == 3
    assert [len(commit["parents"]) for commit in history] == [0, 1, 1]
    assert history[1]["parents"] == [history[0]["commit"]]
    assert history[2]["parents"] == [history[1]["commit"]]
    assert len({commit["commit"] for commit in history}) == 3
    assert sum(len(commit["file_changes"]) for commit in history) >= 5

    database = PUBLIC_ROOT / lane_id / LANE_REGISTRY[lane_id].sqlite_filename
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM git_commit_registry"
        ).fetchone() == (3,)
        assert connection.execute(
            "SELECT COUNT(*) FROM git_commit_parent"
        ).fetchone() == (2,)
        assert connection.execute(
            "SELECT COUNT(*) FROM git_file_change"
        ).fetchone()[0] >= 5

    receipt = json.loads(
        (PUBLIC_ROOT / lane_id / "refresh_receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["git_history"]["counts"]["commits"] == 3
    assert receipt["git_history"]["full_reachable_history"] is True
    assert receipt["git_history"]["source_policy"]["tracked_history_only"] is True

    assert by_lane["local_code"]["git_history"] is None


def test_code_lane_topologies_are_sqlite_derived_and_structurally_distinct() -> None:
    github = PUBLIC_ROOT / "github_code"
    local = PUBLIC_ROOT / "local_code"
    github_mmd = (github / LANE_REGISTRY["github_code"].mmd_filename).read_text(
        encoding="utf-8"
    )
    local_mmd = (local / LANE_REGISTRY["local_code"].mmd_filename).read_text(
        encoding="utf-8"
    )

    assert "subgraph GITHUB_REPOSITORY_GRAPH" in github_mmd
    assert "subgraph LOCAL_WORKTREE_GRAPH" not in github_mmd
    assert "refs/heads/main" in github_mmd
    assert "parent 0 -&gt; child" in github_mmd
    assert "records file change" in github_mmd
    assert "resolves content-addressed blob" in github_mmd
    assert "contains chunk 0" in github_mmd
    assert "stable_id=" in github_mmd
    assert "EXTRACTED" in github_mmd

    assert "subgraph LOCAL_WORKTREE_GRAPH" in local_mmd
    assert "subgraph GITHUB_REPOSITORY_GRAPH" not in local_mmd
    assert "NO_GIT_HISTORY_LOADED" in local_mmd
    assert "poc-fixtures/local.py" in local_mmd
    assert "module pathlib" in local_mmd
    assert "commit boundary" not in local_mmd
    assert hashlib.sha256(github_mmd.encode("utf-8")).digest() != hashlib.sha256(
        local_mmd.encode("utf-8")
    ).digest()
