from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from evidence_lane_plugin.live_root_normalization import (
    LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    execute_live_root_normalization,
    plan_live_root_normalization,
)
from evidence_lane_plugin.receipt_ledger import read_receipt


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "project-a"
    _write(root / "accepted" / "must-not-open.txt", b"forbidden")
    _write(root / "internal_sources" / "old.txt", b"source-history")
    _write(
        root
        / "internal_sources"
        / "remote_adapter_generated_caches_20260827"
        / "cache.bin",
        b"generated-cache",
    )
    _write(root / "receipts" / "old.json", b'{"schema":"old-receipt"}')
    _write(
        root / "sessions" / "session-a.json",
        json.dumps(
            {
                "session_id": "session-a",
                "project_id": "project-a",
                "state": "ACTIVE",
                "metadata": {"current_host_session_id": "task-a"},
                "updated_at": "2026-08-27T00:00:00Z",
            }
        ).encode(),
    )
    _write(
        root / "direct_state_travel_entries" / "direct-a.json",
        json.dumps(
            {
                "status": "PASS",
                "receipt": {
                    "session_id": "session-a",
                    "route": "DIRECT",
                    "host_task_binding": {
                        "authoritative_source": {"task_id": "source-a"}
                    },
                    "destination_orchestration": {
                        "destination": {"task_id": "task-a"}
                    },
                },
            }
        ).encode(),
    )
    _write(root / "project.json", b'{"project_id":"project-a"}')
    _write(root / "profiles" / "profile.json", b'{"profile":"study"}')
    _write(
        root / "sectors" / "local_code" / "accepted_history" / "PV12" / "x.json",
        b'{"historical":true}',
    )
    lane = root / "sectors" / "local_code" / "local-code.sqlite"
    lane.parent.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(lane).close()
    connector = root / "connector_brain.sqlite"
    connection = sqlite3.connect(connector)
    connection.execute("CREATE TABLE brain(id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    return root


def test_plan_never_enters_accepted_and_is_content_addressed(tmp_path: Path) -> None:
    root = _root(tmp_path)
    plan = plan_live_root_normalization(root)
    paths = {row["source_relative_path"] for row in plan["rows"]}
    assert not any(path.startswith("accepted/") for path in paths)
    assert plan["accepted_folder_queried"] is False
    assert plan["accepted_archive_used_as_authority"] is False
    assert len(plan["plan_sha256"]) == 64
    assert "internal_sources/old.txt" in paths
    assert plan["purge_only_file_count"] == 1
    assert plan["purge_only_rows"][0]["source_relative_path"].endswith(
        "remote_adapter_generated_caches_20260827/cache.bin"
    )
    assert "direct_state_travel_entries/direct-a.json" in paths
    assert "sectors/local_code/accepted_history/PV12/x.json" in paths


def test_execution_ingests_reads_back_then_purges_exact_sources(tmp_path: Path) -> None:
    root = _root(tmp_path)
    plan = plan_live_root_normalization(root)
    receipt = execute_live_root_normalization(
        root,
        expected_plan_sha256=plan["plan_sha256"],
        confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
    )
    assert receipt["state"] == "MIGRATED_AND_PURGED_AFTER_READBACK"
    assert receipt["accepted_folder_queried"] is False
    assert (root / "accepted" / "must-not-open.txt").read_bytes() == b"forbidden"
    assert not (root / "internal_sources").exists()
    assert not (root / "direct_state_travel_entries").exists()
    assert not (root / "connector_brain.sqlite").exists()
    assert (root / "connector_brain" / "connector-brain.sqlite").is_file()
    assert not (
        root / "sectors" / "local_code" / "accepted_history"
    ).exists()

    migrated = read_receipt(
        root / "receipts" / "receipt-ledger.sqlite",
        logical_path="historical-live-root/old.json",
    )
    assert migrated["schema"] == "old-receipt"
    session = sqlite3.connect(root / "sessions" / "session-authority.sqlite")
    try:
        assert session.execute(
            "SELECT state FROM session_record WHERE session_id='session-a'"
        ).fetchone()[0] == "ACTIVE"
        assert session.execute("SELECT COUNT(*) FROM state_travel_entry").fetchone()[0] == 1
    finally:
        session.close()


def test_execution_rejects_changed_plan_or_missing_confirmation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    plan = plan_live_root_normalization(root)
    with pytest.raises(ValueError, match="CONFIRMATION_REQUIRED"):
        execute_live_root_normalization(
            root,
            expected_plan_sha256=plan["plan_sha256"],
            confirmation="wrong",
        )
    _write(root / "internal_sources" / "late.txt", b"changed")
    with pytest.raises(RuntimeError, match="PLAN_CHANGED"):
        execute_live_root_normalization(
            root,
            expected_plan_sha256=plan["plan_sha256"],
            confirmation=LIVE_ROOT_NORMALIZATION_CONFIRMATION,
        )
