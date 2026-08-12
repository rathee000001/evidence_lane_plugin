from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.website_plan_projection import (
    WEBSITE_DELTA_LEDGER_PATH,
    WEBSITE_PLAN_PROJECTION_PATH,
    build_website_plan_projection,
    require_website_plan_projection_for_push,
    validate_website_plan_projection,
)


def _backlog(*, linked_delta: str = "DELTA-A") -> dict[str, Any]:
    return {
        "status": "PASS",
        "event_count": 7,
        "event_head_sha256": "A" * 64,
        "planning_mode_event_count": 2,
        "planning_mode_event_head_sha256": "B" * 64,
        "plan_runtime_projection": {"projection_content_sha256": "C" * 64},
        "goal_projection": {
            "canonical_authority": "PLAN_LANE",
            "project_id": "projection-test",
            "task_count": 3,
            "canonical_task_count": 5,
            "history_task_count": 2,
            "row_offset": 80,
            "row_start": 81,
            "row_end": 83,
            "canonical_plan_sha256": "D" * 64,
            "history_projection_sha256": "E" * 64,
            "projection_sha256": "F" * 64,
            "persistent_until": "NEXT_SIX_WAY_HIL_PRESENTED",
            "rows": [
                {
                    "number": 81,
                    "task_id": "TASK-81",
                    "step": "Completed contract.",
                    "status": "completed",
                    "lifecycle_status": "DONE",
                    "plan_sequence": 1,
                    "steer_deltas": [],
                },
                {
                    "number": 82,
                    "task_id": "TASK-82",
                    "step": "Active contract.",
                    "status": "in_progress",
                    "lifecycle_status": "ACTIVE",
                    "plan_sequence": 2,
                    "steer_deltas": [{"delta_id": linked_delta, "text": "private"}],
                },
                {
                    "number": 83,
                    "task_id": "TASK-83",
                    "step": "Final HIL contract.",
                    "status": "pending",
                    "lifecycle_status": "QUEUED",
                    "plan_sequence": 3,
                    "steer_deltas": [],
                    "panel_role": "PHYSICALLY_FINAL_HIL",
                },
            ],
        },
    }


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def test_projection_exposes_exact_contracts_but_not_raw_delta_json() -> None:
    snapshot = build_website_plan_projection(_backlog())

    assert snapshot["canonical_authority"] == "PLAN_LANE"
    assert snapshot["status_counts"] == {
        "completed": 1,
        "in_progress": 1,
        "pending": 1,
    }
    assert snapshot["active_row"] == 82
    assert snapshot["physically_final_hil_row"] == 83
    assert [row["row"] for row in snapshot["rows"]] == [81, 82, 83]
    assert snapshot["rows"][1]["linked_delta_ids"] == ["DELTA-A"]
    assert "private" not in json.dumps(snapshot)
    assert validate_website_plan_projection(snapshot) == snapshot


def test_projection_rejects_a_tampered_seal() -> None:
    snapshot = build_website_plan_projection(_backlog())
    snapshot["active_row"] = 81

    with pytest.raises(EvidenceLaneError) as error:
        validate_website_plan_projection(snapshot)

    assert error.value.code == "WEBSITE_PLAN_SNAPSHOT_HASH_MISMATCH"


def test_commit_bound_remote_gate_blocks_a_stale_native_plan(tmp_path: Path) -> None:
    repository = tmp_path / "evidence-lane"
    repository.mkdir()
    _git(repository, "init", "-b", "agent/plan-preview")
    _git(repository, "config", "user.name", "Evidence Lane Test")
    _git(repository, "config", "user.email", "evidence-lane@example.invalid")
    marker = repository / WEBSITE_DELTA_LEDGER_PATH
    marker.parent.mkdir(parents=True)
    marker.write_text("export const deltaLedger = [];\n", encoding="utf-8")
    snapshot_path = repository / WEBSITE_PLAN_PROJECTION_PATH
    snapshot_path.write_text(
        json.dumps(build_website_plan_projection(_backlog()), indent=2) + "\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "Add sealed website Plan")
    commit = _git(repository, "rev-parse", "HEAD")

    class FakeStore:
        def __init__(self, payload: dict[str, Any]) -> None:
            self.payload = payload

        def backlog_status(self, project_id: str) -> dict[str, Any]:
            assert project_id == "projection-test"
            return self.payload

    passed = require_website_plan_projection_for_push(
        FakeStore(_backlog()),  # type: ignore[arg-type]
        project_id="projection-test",
        repository=repository,
        commit=commit,
    )
    assert passed["status"] == "PASS"
    assert passed["row_end"] == 83

    with pytest.raises(EvidenceLaneError) as error:
        require_website_plan_projection_for_push(
            FakeStore(_backlog(linked_delta="DELTA-B")),  # type: ignore[arg-type]
            project_id="projection-test",
            repository=repository,
            commit=commit,
        )
    assert error.value.code == "REMOTE_WEBSITE_PLAN_PROJECTION_STALE"
