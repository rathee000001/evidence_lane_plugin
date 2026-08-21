from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import atomic_write_json, sha256_bytes
from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS, LANE_REGISTRY
from evidence_lane_plugin.project_universe import (
    inspect_project_universe,
    query_project_universe,
    refresh_project_universe,
)

PROJECT_ID = "universe-fixture"


def _lane_database(path: Path, lane_id: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE source_registry(
                source_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                mime_type TEXT,
                extension TEXT,
                encoding TEXT,
                parser_state TEXT NOT NULL,
                exact_bytes INTEGER NOT NULL,
                registered_at TEXT NOT NULL
            );
            CREATE TABLE structured_fact(fact_id TEXT PRIMARY KEY);
            CREATE TABLE chunk_index(chunk_id TEXT PRIMARY KEY);
            CREATE TABLE refresh_receipt(receipt_id TEXT PRIMARY KEY);
            """
        )
        connection.execute(
            "INSERT INTO source_registry VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                f"source-{lane_id}",
                f"sources/{lane_id}.txt",
                len(lane_id),
                sha256_bytes(lane_id.encode()),
                "text/plain",
                ".txt",
                "utf-8",
                "PASS",
                1,
                "2026-08-21T00:00:00Z",
            ),
        )
        connection.execute("INSERT INTO structured_fact VALUES(?)", (f"fact-{lane_id}",))
        connection.execute("INSERT INTO chunk_index VALUES(?)", (f"chunk-{lane_id}",))
        connection.execute("INSERT INTO refresh_receipt VALUES(?)", (f"receipt-{lane_id}",))
        connection.commit()
    finally:
        connection.close()


def _plan_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE plan_execution_row(
                task_id TEXT PRIMARY KEY,
                plan_sequence INTEGER NOT NULL,
                row_number INTEGER,
                lifecycle_status TEXT NOT NULL,
                host_status TEXT NOT NULL,
                task_classification TEXT NOT NULL,
                plan_group TEXT NOT NULL,
                commit_batch_id TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                effective_for_execution INTEGER NOT NULL,
                task_contract_sha256 TEXT NOT NULL
            );
            CREATE TABLE delta_task(task_id TEXT PRIMARY KEY);
            CREATE TABLE delta_event(event_id TEXT PRIMARY KEY);
            """
        )
        tasks = [
            ("TASK-1", 1, 1, "DONE", "completed", "fix", "G1", "B1", "[]", 1),
            ("TASK-2", 2, 2, "ACTIVE", "in_progress", "add", "G1", "B1", '["TASK-1"]', 1),
            ("TASK-HISTORY", 3, None, "SUPERSEDED", "completed", "fix", "G0", "B0", "[]", 0),
        ]
        for row in tasks:
            connection.execute(
                "INSERT INTO plan_execution_row VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (*row, sha256_bytes(row[0].encode())),
            )
            connection.execute("INSERT INTO delta_task VALUES(?)", (row[0],))
        connection.execute("INSERT INTO delta_event VALUES('event-1')")
        connection.commit()
    finally:
        connection.close()


def _project(tmp_path: Path) -> Path:
    root = tmp_path / PROJECT_ID
    sectors = root / "sectors"
    sectors.mkdir(parents=True)
    for lane_id in CANONICAL_LANE_IDS:
        lane_root = sectors / lane_id
        lane_root.mkdir()
        atomic_write_json(
            lane_root / "authority.ref.json",
            {
                "schema": "evidence-lane.working-sector-authority.v1",
                "state": "LIVE_WORKING",
                "project_id": PROJECT_ID,
                "lane_id": lane_id,
                "historical_parent_pv": "PV12",
                "pointer_generation": 12,
                "working_identity_sha256": sha256_bytes(lane_id.encode()),
                "candidate_directory_created": False,
                "pointer_moved": False,
            },
        )
        if lane_id == "plan":
            _plan_database(lane_root / "plan_runtime_projection.sqlite")
            atomic_write_json(
                lane_root / ".operational-authority.json",
                {
                    "schema": "evidence-lane.sector-operational-authority.v1",
                    "state": "CANONICAL_ACTIVE",
                },
            )
        else:
            _lane_database(lane_root / LANE_REGISTRY[lane_id].sqlite_filename, lane_id)
    atomic_write_json(
        root / "active_pointer.json",
        {
            "project_id": PROJECT_ID,
            "accepted_pv": "PV12",
            "generation": 12,
            "accepted_manifest_sha256": sha256_bytes(b"accepted"),
        },
    )
    return root


def test_universe_refresh_is_real_db_derived_project_projection(tmp_path: Path) -> None:
    root = _project(tmp_path)
    pointer_before = (root / "active_pointer.json").read_bytes()
    receipt = refresh_project_universe(
        root,
        project_id=PROJECT_ID,
        active_plan_task_id="TASK-2",
        recorded_at="2026-08-21T00:00:00Z",
    )
    assert receipt["status"] == "PASS"
    assert receipt["metrics"]["lane_count"] == 18
    assert receipt["metrics"]["source_count"] == 17
    assert receipt["metrics"]["task_count"] == 3
    assert receipt["metrics"]["active_task_count"] == 1
    assert (root / "active_pointer.json").read_bytes() == pointer_before
    assert not (root / "candidates").exists()
    assert {path.name for path in (root / "universe").iterdir()} == {
        "PROJECT_UNIVERSE_MANIFEST.json",
        "project_universe.dot",
        "project_universe.json",
        "project_universe.mmd",
        "project_universe.sqlite",
        "project_universe.tools.json",
        "project_universe_pointer.json",
        "project_universe_refresh_receipt.json",
    }
    assert inspect_project_universe(root, project_id=PROJECT_ID)["status"] == "PASS"


def test_universe_query_is_bounded_and_stably_identified(tmp_path: Path) -> None:
    root = _project(tmp_path)
    first = refresh_project_universe(
        root,
        project_id=PROJECT_ID,
        active_plan_task_id="TASK-2",
        recorded_at="2026-08-21T00:00:00Z",
    )
    second = refresh_project_universe(
        root,
        project_id=PROJECT_ID,
        active_plan_task_id="TASK-2",
        recorded_at="2026-08-21T00:01:00Z",
    )
    assert second["graph_sha256"] == first["graph_sha256"]
    assert second["source_fingerprint_sha256"] == first["source_fingerprint_sha256"]
    result = query_project_universe(
        root, project_id=PROJECT_ID, node_kind="TASK", query="TASK", limit=2
    )
    assert result["status"] == "PASS"
    assert result["truncated"] is True
    assert len(result["hits"]) == 2
    assert all(row["node_kind"] == "TASK" for row in result["hits"])
    assert result["full_graph_returned"] is False
    with pytest.raises(EvidenceLaneError) as exc:
        query_project_universe(root, project_id=PROJECT_ID, limit=101)
    assert exc.value.code == "PROJECT_UNIVERSE_QUERY_LIMIT_INVALID"


def test_universe_fails_closed_on_missing_lane_or_tampered_member(tmp_path: Path) -> None:
    root = _project(tmp_path)
    missing = root / "sectors" / CANONICAL_LANE_IDS[-1]
    moved = root / f"{missing.name}.saved"
    missing.rename(moved)
    with pytest.raises(EvidenceLaneError) as exc:
        refresh_project_universe(root, project_id=PROJECT_ID, active_plan_task_id="TASK-2")
    assert exc.value.code == "PROJECT_UNIVERSE_LANE_MISSING"
    moved.rename(missing)
    refresh_project_universe(root, project_id=PROJECT_ID, active_plan_task_id="TASK-2")
    (root / "universe" / "project_universe.json").write_text("{}", encoding="utf-8")
    with pytest.raises(EvidenceLaneError) as exc:
        inspect_project_universe(root, project_id=PROJECT_ID)
    assert exc.value.code == "PROJECT_UNIVERSE_MEMBER_MISMATCH"
