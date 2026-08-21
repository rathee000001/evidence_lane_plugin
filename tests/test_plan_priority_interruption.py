from __future__ import annotations

from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.host_plan_rehydration import _exact_projection

from .conftest import build_and_approve_pv1


def _task(task_id: str, outcome: str, **metadata: object) -> dict:
    return {
        "task_id": task_id,
        "task_class": "fix_bug",
        "requested_outcome": outcome,
        "permitted_paths": ["src/**", "tests/**"],
        "permitted_tools": [
            "patch",
            "repository_read",
            "repository_write",
            "terminal",
            "test",
        ],
        "acceptance_checks": ["Verify the bounded priority correction."],
        "stop_condition": "Stop after the bounded correction passes.",
        **metadata,
    }


def test_priority_insertion_pauses_and_preserves_live_row(service) -> None:
    session_id, _ = build_and_approve_pv1(service)
    live = _task("learning-live", "Complete the current Learning runtime row.")
    next_row = _task("next-row", "Complete the next queued row.")
    final_hil = _task(
        "final-hil",
        "Present the physically final HIL.",
        panel_role="PHYSICALLY_FINAL_HIL",
    )
    service.plan_tasks(
        "book-faires",
        tasks=[live, next_row, final_hil],
        planned_by="human-test",
        plan_id="priority-seed-plan",
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [live["task_id"], next_row["task_id"], final_hil["task_id"]],
    }
    service.sessions._save(session)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=live["task_class"],
        requested_outcome=live["requested_outcome"],
        permitted_paths=live["permitted_paths"],
        permitted_tools=live["permitted_tools"],
        acceptance_checks=live["acceptance_checks"],
        stop_condition=live["stop_condition"],
        backlog_task_id=live["task_id"],
    )
    before = service.task_backlog("book-faires")
    pointer_before = service.store.pointer("book-faires").as_dict()
    inserted = _task(
        "local-slot-priority",
        "Create and verify the persistent local test slot.",
        dependencies=[],
        plan_group="LOCAL_TEST_RUNTIME",
        commit_batch_id="NO_GIT",
        git_commit_stage="NONE",
    )
    atomic = {
        "batch_id": "priority-local-slot-batch",
        "research_batch_sha256": "A" * 64,
        "expected_backlog_sha256": sha256_bytes(
            canonical_json_bytes(service.store._load_backlog("book-faires"))
        ),
        "expected_canonical_plan_sha256": before["canonical_plan_projection"][
            "projection_sha256"
        ],
        "expected_executable_projection_sha256": before["goal_projection"][
            "projection_sha256"
        ],
        "expected_physical_final_task_id": "final-hil",
        "insertions": [
            {
                "insert_before_task_id": "learning-live",
                "tasks": [inserted],
            }
        ],
        "priority_interruption": {
            "interruption_id": "priority-local-slot-001",
            "session_id": session_id,
            "old_active_task_id": "learning-live",
            "replacement_task_id": "local-slot-priority",
            "reason": "The user moved persistent local-slot correction ahead of Learning.",
            "expected_candidate_absent": True,
            "expected_pending_hil": False,
        },
    }

    result = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="priority-local-slot-plan",
        atomic_insertion=atomic,
    )

    rows = result["goal_projection"]["rows"]
    assert [row["task_id"] for row in rows] == [
        "local-slot-priority",
        "learning-live",
        "next-row",
        "final-hil",
    ]
    assert [row["status"] for row in rows] == [
        "in_progress",
        "pending",
        "pending",
        "pending",
    ]
    assert result["priority_steer_receipt"]["history_preserved"] is True
    assert result["atomic_insertion_receipt"]["task_count"] == 1
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    session = service.sessions.load("book-faires", session_id)
    assert session.metadata["active_backlog_task_id"] == "local-slot-priority"
    assert session.task is not None
    assert session.task["task_id"] == "local-slot-priority"
    assert session.candidate_id is None
    fixed_task_ids = [
        "local-slot-priority",
        "learning-live",
        "next-row",
        "final-hil",
    ]
    projection = _exact_projection(
        service.store,
        project_id="book-faires",
        fixed_window_task_ids=fixed_task_ids,
    )
    assert projection["total_executable_count"] == 4
    assert projection["sole_active_row"] == 1
    assert projection["row_start"] == 1
    assert projection["row_end"] == 4
    assert projection["physically_final_hil_row"] == 4
    assert projection["items"][0]["step"] == (
        "PV1/generation 1 | ACTIVE R1 | ACTIVE BATCH R1-R4 | NEXT_HIL R4 | FINAL_HIL R4"
    )
    assert "Create and verify the persistent local test slot" in " ".join(
        projection["items"][1]["step"].split()
    )

    replay = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="priority-local-slot-plan",
        atomic_insertion=atomic,
    )
    assert replay["atomic_insertion_receipt"]["idempotent_replay"] is True
    assert replay["priority_steer_receipt"]["idempotent_replay"] is True
    assert [row["task_id"] for row in replay["active"]] == ["local-slot-priority"]
    replay_projection = _exact_projection(
        service.store,
        project_id="book-faires",
        fixed_window_task_ids=fixed_task_ids,
    )
    assert replay_projection["total_executable_count"] == 4
    assert replay_projection["sole_active_row"] == 1
