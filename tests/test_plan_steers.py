from __future__ import annotations

import json

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

from .conftest import build_and_approve_pv1


def _task(task_id: str, outcome: str) -> dict:
    return {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": outcome,
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Verify the bounded result."],
        "stop_condition": "Stop at the next governed HIL.",
    }


def test_codex_plan_mode_bridge_and_canonical_steer_classification(service) -> None:
    tasks = [
        _task("step-001", "Verify the first result."),
        _task("step-002", "Verify the second result."),
    ]
    reminder = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-mode-reminder",
        host_kind="CODEX_DESKTOP",
        host_mode="DEFAULT",
    )
    assert reminder["status"] == "PLAN_MODE_REQUIRED"
    assert reminder["suggested_next_prompt"] == "/pl"
    assert service.task_backlog("book-faires")["tasks"] == []

    planned = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-mode-canonical",
        host_kind="CODEX_DESKTOP",
        host_mode="PLAN",
    )
    goal = planned["goal_projection"]
    assert goal["canonical_authority"] == "PLAN_LANE"
    assert goal["task_count"] == 2
    assert goal["persistent_until"] == "NEXT_SIX_WAY_HIL_PRESENTED"
    assert goal["host_projections"]["CODEX"]["plan_mode_shortcut"] == "/pl"
    assert planned["host_plan_bridge"]["copy_paste_required"] is True
    assert "as this Codex task's Goal" in goal["goal_start_prompt"]

    linked = service.record_steer_delta(
        "book-faires",
        delta_text="Also verify the exact source hash.",
        actor="human-test",
        delta_id="steer-linked-001",
        linked_task_id="step-002",
    )
    assert linked["task_count"] == 2
    assert linked["task_count_changed"] is False
    assert linked["steer"]["boundary"] == "BEFORE_NEXT_HIL"
    assert linked["steer"]["classification"] == "LINKED_EXISTING_STEP"
    assert linked["event"]["event_type"] == "STEER_DELTA_LINKED"

    replay = service.record_steer_delta(
        "book-faires",
        delta_text="Also verify the exact source hash.",
        actor="human-test",
        delta_id="steer-linked-001",
        linked_task_id="step-002",
    )
    assert replay["idempotent_reuse"] is True
    assert replay["task_count"] == 2

    appended = service.record_steer_delta(
        "book-faires",
        delta_text="Publish a separate host-boundary explanation.",
        actor="human-test",
        delta_id="steer-new-001",
        new_task_contract=_task(
            "step-003",
            "Publish a separate host-boundary explanation.",
        ),
    )
    assert appended["task_count"] == 3
    assert appended["task_count_changed"] is True
    assert appended["steer"]["classification"] == "NEW_STEP"
    assert appended["event"]["event_type"] == "STEER_DELTA_NEW_STEP"
    assert appended["backlog_receipt"]["canonical_task_count"] == 3
    assert appended["backlog_receipt"]["executable_task_count"] == 3
    assert appended["backlog_receipt"]["full_backlog_returned"] is False
    assert appended["backlog_receipt"]["full_plan_returned"] is False
    assert appended["steer"]["delta_text_returned"] is False
    assert "text" not in appended["steer"]
    assert "backlog" not in appended
    assert len(json.dumps(appended, sort_keys=True).encode("utf-8")) < 8192


def test_plan_steer_updates_only_when_current_host_window_fingerprint_changes(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    tasks = [
        _task(f"window-step-{number:02d}", f"Execute window row {number}.")
        for number in range(1, 13)
    ]
    service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="nine-row-window-steer-routing",
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [str(task["task_id"]) for task in tasks[:9]],
    }
    service.sessions._save(session)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=str(tasks[0]["task_class"]),
        requested_outcome=str(tasks[0]["requested_outcome"]),
        permitted_paths=list(tasks[0]["permitted_paths"]),
        permitted_tools=list(tasks[0]["permitted_tools"]),
        acceptance_checks=list(tasks[0]["acceptance_checks"]),
        stop_condition=str(tasks[0]["stop_condition"]),
        backlog_task_id=str(tasks[0]["task_id"]),
    )

    outside = service.record_steer_delta(
        "book-faires",
        delta_text="Attach this correction to a later host window.",
        actor="human-test",
        delta_id="steer-outside-current-window",
        linked_task_id="window-step-12",
    )
    outside_effect = outside["host_plan_window_effect"]
    assert outside_effect["schema"] == (
        "evidence-lane.plan-steer-host-window-effect.v2"
    )
    assert outside_effect["row_start"] == 1
    assert outside_effect["row_end"] == 9
    assert outside_effect["window_size"] == 9
    assert outside_effect["linked_row_is_currently_visible"] is False
    assert outside_effect["visible_window_changed"] is False
    assert outside_effect["action"] == "LEDGER_ONLY_REUSE_CURRENT_HOST_WINDOW"
    assert outside_effect["host_update_plan_required"] is False

    inside_text_only = service.record_steer_delta(
        "book-faires",
        delta_text="Attach this correction to the visible host window.",
        actor="human-test",
        delta_id="steer-inside-current-window",
        linked_task_id="window-step-05",
    )
    text_effect = inside_text_only["host_plan_window_effect"]
    assert text_effect["linked_row_is_currently_visible"] is True
    assert text_effect["visible_window_changed"] is False
    assert text_effect["action"] == "LEDGER_ONLY_REUSE_CURRENT_HOST_WINDOW"
    assert text_effect["host_update_plan_required"] is False

    inside_metadata = service.record_steer_delta(
        "book-faires",
        delta_text="PLAN_GROUP=updated-window-group.",
        actor="human-test",
        delta_id="steer-inside-window-metadata",
        linked_task_id="window-step-05",
    )
    effect = inside_metadata["host_plan_window_effect"]
    assert effect["linked_row_is_currently_visible"] is True
    assert effect["visible_window_changed"] is True
    assert effect["action"] == "SYNC_CURRENT_HOST_WINDOW_ONCE"
    assert effect["host_update_plan_required"] is True
    assert effect["evi_refresh_invoked"] is False
    assert inside_metadata["host_plan_window_rebind"]["window_task_ids"] == [
        task["task_id"] for task in tasks[:9]
    ]
    assert (
        inside_metadata["host_plan_window_rebind"]["binding_source"]
        == "CANONICAL_PLAN_STEER_MUTATION"
    )


def test_plan_steer_reuses_persisted_batch_when_active_is_mid_window(service) -> None:
    session_id, _ = build_and_approve_pv1(service)
    tasks = [
        _task(f"fixed-step-{number:02d}", f"Execute fixed window row {number}.")
        for number in range(1, 13)
    ]
    service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="persisted-nine-row-window",
    )
    active = tasks[4]
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=str(active["task_class"]),
        requested_outcome=str(active["requested_outcome"]),
        permitted_paths=list(active["permitted_paths"]),
        permitted_tools=list(active["permitted_tools"]),
        acceptance_checks=list(active["acceptance_checks"]),
        stop_condition=str(active["stop_condition"]),
        backlog_task_id=str(active["task_id"]),
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [task["task_id"] for task in tasks[:9]],
    }
    service.sessions._save(session)

    result = service.record_steer_delta(
        "book-faires",
        delta_text="Attach this correction outside the fixed host batch.",
        actor="human-test",
        delta_id="steer-fixed-window-outside",
        linked_task_id="fixed-step-12",
    )
    effect = result["host_plan_window_effect"]
    assert effect["row_start"] == 1
    assert effect["row_end"] == 9
    assert effect["window_size"] == 9
    assert effect["active_row_present"] is True
    assert effect["linked_row_is_currently_visible"] is False
    assert effect["action"] == "LEDGER_ONLY_REUSE_CURRENT_HOST_WINDOW"


def test_task_activity_public_receipt_keeps_exact_bounded_host_window(
    service,
) -> None:
    plan = [
        {"status": "completed", "step": "Tracker | Active=R238"},
        {"status": "in_progress", "step": "R238 | active"},
        {"status": "pending", "step": "R239 | pending"},
    ]
    result = service.invoke(
        "task_record_activity",
        lambda: {
            "status": "PASS",
            "event": {
                "event_id": "host-plan-loss-001",
                "event_type": "task.host.plan.observation",
                "event_sha256": "A" * 64,
                "occurred_at": "2026-08-20T18:00:00Z",
                "session_id": "session-test",
                "task_id": "row238",
                "run_id": "run-test",
            },
            "host_plan_rehydration": {
                "state": "REHYDRATION_RECEIPT_SEALED",
                "request_sha256": "B" * 64,
                "receipt_path": "private-store-path.json",
                "receipt": {
                    "schema": ("evidence-lane.host-plan-window-activation-receipt.v2"),
                    "status": "PASS",
                    "project_id": "book-faires",
                    "evidence_session_id": "session-test",
                    "host_task_id_sha256": "C" * 64,
                    "trigger": "TASK_PANEL_LOSS",
                    "trigger_event_id": "host-plan-loss-001",
                    "action": "REACTIVATE_EXISTING_HOST_PLAN_WINDOW",
                    "host_update_plan_required": True,
                    "host_goal_active": True,
                    "host_artifact_visibility_status": "UNCONFIRMED",
                    "receipt_sha256": "D" * 64,
                    "candidate_created": False,
                    "pending_hil_mutated": False,
                    "pointer_moved": False,
                    "plan_lane_mutated": False,
                    "projection": {
                        "schema": "evidence-lane.host-plan-window.v2",
                        "projection_sha256": "E" * 64,
                        "canonical_plan_sha256": "F" * 64,
                        "executable_projection_sha256": "1" * 64,
                        "window_ui_fingerprint_sha256": "2" * 64,
                        "row_start": 238,
                        "row_end": 239,
                        "item_count": 3,
                        "sole_active_row": 238,
                        "continuity_header": {
                            "next_hil_boundary_row": 267,
                            "physically_final_row": 276,
                        },
                        "host_update_plan_contract": {
                            "explanation": "Bounded native window",
                            "plan": plan,
                        },
                    },
                },
            },
            "source_state": "ENTRY_MATCH",
            "accepted_pv_query_scope": "ENTRY_STATE_ONLY",
        },
        lifecycle=True,
    )

    rehydration = result["data"]["host_plan_rehydration"]
    assert rehydration["action"] == "REACTIVATE_EXISTING_HOST_PLAN_WINDOW"
    assert rehydration["host_update_plan_required"] is True
    assert rehydration["projection"]["row_start"] == 238
    assert rehydration["projection"]["row_end"] == 239
    assert rehydration["projection"]["next_hil_boundary_row"] == 267
    assert rehydration["projection"]["physically_final_row"] == 276
    assert rehydration["projection"]["host_update_plan_contract"]["plan"] == plan
    assert rehydration["bounded_host_window_returned"] is True
    assert rehydration["full_plan_returned"] is False
    assert "receipt_path" not in rehydration


def test_existing_priority_delta_is_promoted_without_duplication_and_rehydrates(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    active = {
        **_task("active-route", "Finish the interrupted direct route."),
        "commit_batch_id": "route-batch",
        "dependencies": [],
    }
    middle_one = {
        **_task("middle-one", "Preserve the first queued route correction."),
        "dependencies": ["active-route"],
    }
    middle_two = {
        **_task("middle-two", "Preserve the second queued route correction."),
        "dependencies": ["middle-one"],
    }
    promoted = {
        **_task("priority-delivery", "Run the already-recorded delivery Delta first."),
        "commit_batch_id": "priority-batch",
        "dependencies": ["active-route"],
    }
    successor = {
        **_task("delivery-successor", "Continue only after the displaced chain."),
        "dependencies": ["priority-delivery"],
    }
    final_hil = {
        **_task("physical-final-hil", "Present the physically final HIL."),
        "panel_role": "PHYSICALLY_FINAL_HIL",
        "dependencies": ["delivery-successor"],
    }
    service.plan_tasks(
        "book-faires",
        tasks=[active, middle_one, middle_two, promoted, successor, final_hil],
        planned_by="human-test",
        plan_id="existing-priority-promotion",
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [
            str(task["task_id"])
            for task in [
                active,
                middle_one,
                middle_two,
                promoted,
                successor,
                final_hil,
            ]
        ],
    }
    service.sessions._save(session)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=str(active["task_class"]),
        requested_outcome=str(active["requested_outcome"]),
        permitted_paths=list(active["permitted_paths"]),
        permitted_tools=list(active["permitted_tools"]),
        acceptance_checks=list(active["acceptance_checks"]),
        stop_condition=str(active["stop_condition"]),
        backlog_task_id=str(active["task_id"]),
    )
    before = service.store.backlog_status("book-faires")
    raw_before = service.store._load_backlog("book-faires")
    session = service.sessions.load("book-faires", session_id)

    result = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        existing_task_promotion={
            "promotion_id": "promote-existing-priority-delivery-001",
            "session_id": session_id,
            "old_active_task_id": "active-route",
            "promoted_task_id": "priority-delivery",
            "expected_host_task_id": session.metadata["current_host_session_id"],
            "reason": "The user ordered the existing delivery Delta first.",
            "expected_backlog_sha256": sha256_bytes(canonical_json_bytes(raw_before)),
            "expected_canonical_plan_sha256": before["canonical_plan_projection"][
                "projection_sha256"
            ],
            "expected_executable_projection_sha256": before["goal_projection"][
                "projection_sha256"
            ],
            "expected_physical_final_task_id": "physical-final-hil",
            "expected_candidate_absent": True,
            "expected_pending_hil": False,
            "expected_pointer_move": False,
            "preserve_task_identity": True,
            "host_goal_active": True,
        },
    )

    rows = result["goal_projection"]["rows"]
    assert [row["task_id"] for row in rows] == [
        "priority-delivery",
        "active-route",
        "middle-one",
        "middle-two",
        "delivery-successor",
        "physical-final-hil",
    ]
    assert len({row["task_id"] for row in rows}) == len(rows) == 6
    assert rows[0]["status"] == "in_progress"
    assert rows[1]["status"] == "pending"
    assert rows[0]["dependencies"] == []
    assert rows[1]["dependencies"] == ["priority-delivery"]
    assert rows[4]["dependencies"] == ["middle-two"]
    assert rows[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert (
        result["existing_task_promotion_receipt"]["stable_task_identity_preserved"]
        is True
    )
    assert result["existing_task_promotion_receipt"]["task_count_unchanged"] is True
    assert result["existing_task_session_rebind"]["goal_completed"] is False
    projection = result["host_plan_rehydration"]["receipt"]["projection"]
    assert projection["sole_active_task_id"] == "priority-delivery"
    assert projection["items"][1]["status"] == "in_progress"
    rebound = service.sessions.load("book-faires", session_id)
    assert rebound.metadata["active_backlog_task_id"] == "priority-delivery"
    assert rebound.task["task_id"] == "priority-delivery"


def test_existing_priority_promotion_accepts_implicit_executable_predecessor(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    active = {
        **_task("active-route", "Finish the interrupted direct route."),
        "dependencies": [],
    }
    middle = {
        **_task("middle-route", "Preserve the queued route correction."),
        "dependencies": ["active-route"],
    }
    promoted = _task(
        "implicit-priority",
        "Promote the existing row whose predecessor is implicit.",
    )
    successor = {
        **_task("implicit-successor", "Continue the displaced chain."),
        "dependencies": ["implicit-priority"],
    }
    final_hil = {
        **_task("physical-final-hil", "Present the physically final HIL."),
        "panel_role": "PHYSICALLY_FINAL_HIL",
        "dependencies": ["implicit-successor"],
    }
    service.plan_tasks(
        "book-faires",
        tasks=[active, middle, promoted, successor, final_hil],
        planned_by="human-test",
        plan_id="implicit-existing-priority-promotion",
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [
            str(task["task_id"])
            for task in [
                active,
                middle,
                promoted,
                successor,
                final_hil,
            ]
        ],
    }
    service.sessions._save(session)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=str(active["task_class"]),
        requested_outcome=str(active["requested_outcome"]),
        permitted_paths=list(active["permitted_paths"]),
        permitted_tools=list(active["permitted_tools"]),
        acceptance_checks=list(active["acceptance_checks"]),
        stop_condition=str(active["stop_condition"]),
        backlog_task_id=str(active["task_id"]),
    )
    before = service.store.backlog_status("book-faires")
    raw_before = service.store._load_backlog("book-faires")
    session = service.sessions.load("book-faires", session_id)

    result = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        existing_task_promotion={
            "promotion_id": "promote-implicit-priority-001",
            "session_id": session_id,
            "old_active_task_id": "active-route",
            "promoted_task_id": "implicit-priority",
            "expected_host_task_id": session.metadata["current_host_session_id"],
            "reason": "The user ordered the existing implicit row first.",
            "expected_backlog_sha256": sha256_bytes(canonical_json_bytes(raw_before)),
            "expected_canonical_plan_sha256": before["canonical_plan_projection"][
                "projection_sha256"
            ],
            "expected_executable_projection_sha256": before["goal_projection"][
                "projection_sha256"
            ],
            "expected_physical_final_task_id": "physical-final-hil",
            "expected_candidate_absent": True,
            "expected_pending_hil": False,
            "expected_pointer_move": False,
            "preserve_task_identity": True,
            "host_goal_active": True,
        },
    )

    rows = result["goal_projection"]["rows"]
    assert [row["task_id"] for row in rows] == [
        "implicit-priority",
        "active-route",
        "middle-route",
        "implicit-successor",
        "physical-final-hil",
    ]
    assert rows[0]["dependencies"] == []
    assert rows[1]["dependencies"] == ["implicit-priority"]
    assert rows[3]["dependencies"] == ["middle-route"]
    receipt = result["existing_task_promotion_receipt"]
    assert receipt["promoted_dependency_mode"] == ("IMPLICIT_EXECUTABLE_PREDECESSOR")
    assert receipt["successor_dependency_mode"] == "EXPLICIT"


def test_non_codex_plan_rows_are_rejected_outside_the_codex_goal(service) -> None:
    unsupported = service.plan_tasks(
        "book-faires",
        tasks=[_task("chatgpt-step-001", "Persist one ChatGPT lane task.")],
        planned_by="human-test",
        plan_id="chatgpt-plan-001",
        host_kind="CHATGPT_WORK",
        host_mode=None,
    )
    assert unsupported["status"] == "HOST_UNSUPPORTED"
    assert unsupported["plan_persisted"] is False
    assert unsupported["host_kind"] == "CHATGPT_WORK"
    assert "accepts Codex hosts only" in unsupported["message"]
    backlog = service.task_backlog("book-faires")
    assert backlog["tasks"] == []
    assert set(backlog["goal_projection"]["host_projections"]) == {"CODEX"}
    assert backlog["history_projection"]["parked_host_surfaces"] == []


def test_unlinked_steers_insert_before_physically_final_hil_and_all_persist(
    service,
) -> None:
    final_hil = _task(
        "row-final-hil",
        "Present the physically final six-way HIL.",
    )
    final_hil["panel_role"] = "PHYSICALLY_FINAL_HIL"
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task("row-active-work", "Finish the current governed correction."),
            final_hil,
        ],
        planned_by="human-test",
        plan_id="plan-with-final-hil",
    )

    for index in range(1, 8):
        task_id = f"late-correction-{index:03d}"
        service.record_steer_delta(
            "book-faires",
            delta_text=f"Preserve late correction {index} before the final HIL.",
            actor="human-test",
            delta_id=f"late-steer-{index:03d}",
            new_task_contract=_task(
                task_id,
                f"Implement late correction {index} before the final HIL.",
            ),
        )

    backlog = service.task_backlog("book-faires")
    ordered = backlog["goal_projection"]["rows"]
    assert [row["number"] for row in ordered] == list(range(1, 10))
    assert [row["task_id"] for row in ordered] == [
        "row-active-work",
        *(f"late-correction-{index:03d}" for index in range(1, 8)),
        "row-final-hil",
    ]
    assert ordered[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert backlog["tasks"][-1]["task_id"] == "row-final-hil"
    assert backlog["goal_projection"]["unlinked_steer_policy"] == (
        "INSERT_NEW_NUMBERED_STEP_BEFORE_NEXT_HIL_AND_INCREASE_COUNT"
    )
    assert [row["steer_deltas"][0]["delta_id"] for row in ordered[1:-1]] == [
        f"late-steer-{index:03d}" for index in range(1, 8)
    ]


def test_universal_host_plan_labels_preserve_structured_execution_metadata(
    service,
) -> None:
    first = {
        **_task("metadata-step-001", "Verify the first governed result."),
        "plan_group": "corpus-ingest",
        "commit_batch_id": "batch-alpha",
        "dependencies": [],
    }
    second = {
        **_task(
            "metadata-step-002",
            "Create the bounded integration commit after verification.",
        ),
        "task_class": "prepare_patch",
        "permitted_paths": ["src/**"],
        "permitted_tools": ["repository_read", "repository_write"],
        "plan_group": "corpus-ingest",
        "commit_batch_id": "batch-alpha",
        "dependencies": ["metadata-step-001"],
        "git_commit_stage": "COMMIT",
        "current_version": "3.0.0",
        "current_branch": "agent/evi-v300-metadata",
    }
    final_hil = {
        **_task(
            "metadata-final-hil",
            "Present the physically final six-way HIL.",
        ),
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    planned = service.plan_tasks(
        "book-faires",
        tasks=[first, second, final_hil],
        planned_by="human-test",
        plan_id="universal-metadata-plan",
    )

    goal = planned["goal_projection"]
    rows = goal["rows"]
    assert goal["raw_linked_delta_json_in_visible_label"] is False
    assert "CLASS=<classification>" in goal["visible_label_contract"]
    assert "BATCH=<commit_batch_id>" in goal["visible_label_contract"]
    assert rows[0]["task_classification"] == "verify_result"
    assert rows[0]["plan_group"] == "corpus-ingest"
    assert rows[0]["commit_batch_id"] == "batch-alpha"
    assert rows[0]["commit_batch_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[0]["dependencies"] == []
    assert rows[0]["dependency_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[0]["git_commit_stage"] == "NOT_DECLARED"
    assert rows[1]["task_classification"] == "prepare_patch"
    assert rows[1]["dependencies"] == ["metadata-step-001"]
    assert rows[1]["git_commit_stage"] == "COMMIT"
    assert rows[1]["git_commit_stage_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[1]["version_marker"] == "3.0.0"
    assert rows[1]["version_marker_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[1]["branch_marker"] == "agent/evi-v300-metadata"
    assert rows[1]["branch_marker_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[1]["authority_scope"] == "CURRENT_EXECUTABLE_PLAN"
    assert rows[1]["effective_for_execution"] is True
    assert rows[1]["visible_label"].startswith(
        "Row 2 / metadata-step-002 — [CLASS=prepare_patch; "
        "GROUP=corpus-ingest; BATCH=batch-alpha; "
        "DEP=metadata-step-001; GIT=COMMIT@EXPLICIT_TASK_CONTRACT; "
        "VERSION=3.0.0@EXPLICIT_TASK_CONTRACT; "
        "BRANCH=agent/evi-v300-metadata@EXPLICIT_TASK_CONTRACT; "
        "ROLE=STANDARD; STATE=QUEUED]"
    )
    assert rows[2]["plan_group"] == "universal-metadata-plan"
    assert rows[2]["commit_batch_id"] == "UNASSIGNED"
    assert rows[2]["dependencies"] == ["metadata-step-002"]
    assert rows[2]["dependency_source"] == "LINEAR_PREDECESSOR"
    assert rows[2]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert rows[-1]["task_id"] == "metadata-final-hil"

    state_travel_projection = goal["host_projections"]["CODEX"][
        "state_travel_destination"
    ]
    assert state_travel_projection["explicit_host_plan_acceptance_required"] is True
    assert state_travel_projection["plan_acceptance_is_evidence_lane_hil"] is False
    assert (
        state_travel_projection["goal_or_source_work_before_plan_acceptance"] is False
    )


def test_current_plan_hydration_marks_conflicts_until_exact_linked_resolution(
    service,
) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task(
                "release-row",
                "Commit version 2.0.0 on agent/evi-v200-release.",
            ),
            {
                **_task(
                    "release-final-hil",
                    "Present the physically final six-way HIL.",
                ),
                "panel_role": "PHYSICALLY_FINAL_HIL",
            },
        ],
        planned_by="human-test",
        plan_id="release-hydration-plan",
    )
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "Preserve version 2.0.0 and agent/evi-v200-release as historical "
            "wording; the proposed current line is 2.1.0 on "
            "agent/evi-v210-release."
        ),
        actor="human-test",
        delta_id="release-conflict-steer",
        linked_task_id="release-row",
    )
    conflicted = service.task_backlog("book-faires")["goal_projection"]["rows"][0]
    assert conflicted["version_marker"] == "CONFLICTING_DECLARATIONS"
    assert conflicted["version_marker_source"] == "RECONCILIATION_REQUIRED"
    assert conflicted["version_reconciliation_required"] is True
    assert conflicted["branch_marker"] == "CONFLICTING_DECLARATIONS"
    assert conflicted["branch_marker_source"] == "RECONCILIATION_REQUIRED"
    assert conflicted["branch_reconciliation_required"] is True

    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "CURRENT_VERSION=2.1.0 CURRENT_BRANCH=agent/evi-v210-release "
            "COMMIT_BATCH=bundle-release GIT_STAGE=COMMIT_AND_PUSH."
        ),
        actor="human-test",
        delta_id="release-current-authority-steer",
        linked_task_id="release-row",
    )
    resolved = service.task_backlog("book-faires")["goal_projection"]["rows"][0]
    assert resolved["version_marker"] == "2.1.0"
    assert resolved["version_marker_source"] == (
        "LINKED_DELTA:release-current-authority-steer"
    )
    assert resolved["version_reconciliation_required"] is False
    assert resolved["branch_marker"] == "agent/evi-v210-release"
    assert resolved["branch_marker_source"] == (
        "LINKED_DELTA:release-current-authority-steer"
    )
    assert resolved["branch_reconciliation_required"] is False
    assert resolved["commit_batch_id"] == "bundle-release"
    assert resolved["commit_batch_source"] == (
        "LINKED_DELTA:release-current-authority-steer"
    )
    assert resolved["git_commit_stage"] == "COMMIT_AND_PUSH"
    assert resolved["git_commit_stage_source"] == (
        "LINKED_DELTA:release-current-authority-steer"
    )
    assert (
        "VERSION=2.1.0@LINKED_DELTA:release-current-authority-steer"
        in (resolved["visible_label"])
    )
    assert (
        service.task_backlog("book-faires")["goal_projection"]["rows"][-1]["panel_role"]
        == "PHYSICALLY_FINAL_HIL"
    )


def test_active_release_context_hydrates_only_current_and_future_rows(service) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task("release-active", "Reconcile the current release context."),
            _task(
                "release-legacy-commit",
                "Commit version 2.0.0 on agent/evi-v200-release.",
            ),
            {
                **_task(
                    "release-final-hil",
                    "Present the physically final six-way HIL.",
                ),
                "panel_role": "PHYSICALLY_FINAL_HIL",
            },
        ],
        planned_by="human-test",
        plan_id="active-release-context-plan",
    )
    active_contract = next(
        task
        for task in service.task_backlog("book-faires")["tasks"]
        if task["task_id"] == "release-active"
    )
    runtime_contract = {
        "task_id": "runtime-release-active",
        **{
            field: active_contract[field]
            for field in (
                "task_class",
                "requested_outcome",
                "permitted_paths",
                "permitted_tools",
                "acceptance_checks",
                "stop_condition",
            )
        },
    }
    service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id="release-active",
        session_id="session-release-context",
        contract=runtime_contract,
    )
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "CURRENT_VERSION=3.0.0 "
            "CURRENT_BRANCH=agent/evi-v300-release "
            "CANDIDATE_PV=PV13 ACCEPTED_PV=PV12 "
            "ACCEPTED_VERSION=2.1.0 "
            "FALLBACK_OBSERVED_VERSION=2.0.0 "
            "FALLBACK_EXPECTED_ACCEPTED_VERSION=2.1.0."
        ),
        actor="human-test",
        delta_id="active-release-context-steer",
        linked_task_id="release-active",
    )

    projection = service.task_backlog("book-faires")["goal_projection"]
    rows = projection["rows"]
    assert rows[0]["version_marker"] == "3.0.0"
    assert rows[0]["version_marker_source"] == (
        "LINKED_DELTA:active-release-context-steer"
    )
    assert rows[1]["version_marker"] == "3.0.0"
    assert rows[1]["version_marker_source"] == (
        "ACTIVE_PLAN_CONTEXT:release-active:active-release-context-steer"
    )
    assert rows[1]["branch_marker"] == "agent/evi-v300-release"
    assert rows[1]["branch_marker_source"] == (
        "ACTIVE_PLAN_CONTEXT:release-active:active-release-context-steer"
    )
    assert "version 2.0.0" in rows[1]["step"]
    assert "VERSION=3.0.0@ACTIVE_PLAN_CONTEXT" in rows[1]["visible_label"]
    assert rows[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert "ROLE=PHYSICALLY_FINAL_HIL" in rows[-1]["visible_label"]
    release = projection["active_release_context"]
    assert release["target_version"] == "3.0.0"
    assert release["target_branch"] == "agent/evi-v300-release"
    assert release["candidate_pv_target"] == "PV13"
    assert release["accepted_pv"] == "PV12"
    assert release["accepted_version"] == "2.1.0"
    assert release["fallback_observed_version"] == "2.0.0"
    assert release["fallback_expected_accepted_version"] == "2.1.0"
    assert release["creates_candidate"] is False
    assert release["moves_pointer"] is False


def test_plan_dependencies_must_reference_earlier_executable_rows(service) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        service.plan_tasks(
            "book-faires",
            tasks=[
                {
                    **_task("dependency-root", "Verify the dependency root."),
                    "dependencies": ["dependency-future"],
                },
                _task("dependency-future", "Verify the future row."),
            ],
            planned_by="human-test",
            plan_id="invalid-forward-dependency-plan",
        )
    assert blocked.value.code == "PLAN_DEPENDENCY_NOT_EARLIER_EXECUTABLE_ROW"
    assert service.task_backlog("book-faires")["goal_projection"]["rows"] == []


def test_linked_group_and_dependency_directives_survive_legacy_task_storage(
    service,
) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[
            _task("directive-root", "Verify the directive root."),
            _task("directive-child", "Verify the directive child."),
        ],
        planned_by="human-test",
        plan_id="legacy-storage-plan",
    )
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "PLAN_GROUP=canon-runtime COMMIT_BATCH=canon-foundation "
            "DEPENDS_ON=directive-root GIT_STAGE=NO_COMMIT."
        ),
        actor="human-test",
        delta_id="directive-metadata-steer",
        linked_task_id="directive-child",
    )

    row = service.task_backlog("book-faires")["goal_projection"]["rows"][1]
    assert row["plan_group"] == "canon-runtime"
    assert row["plan_group_source"] == "LINKED_DELTA:directive-metadata-steer"
    assert row["commit_batch_id"] == "canon-foundation"
    assert row["dependencies"] == ["directive-root"]
    assert row["dependency_source"] == "LINKED_DELTA:directive-metadata-steer"
    assert row["git_commit_stage"] == "NO_COMMIT"
    assert "GROUP=canon-runtime; BATCH=canon-foundation" in row["visible_label"]
    assert "DEP=directive-root" in row["visible_label"]
