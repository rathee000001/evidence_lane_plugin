from __future__ import annotations

import subprocess

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.host_plan_rehydration import (
    _exact_projection,
    _host_step_description_lines,
    prepare_host_plan_rehydration,
    validate_host_plan_rehydration_receipt,
)

from .conftest import build_and_approve_pv1


def _tasks() -> list[dict[str, object]]:
    first = {
        "task_id": "host-plan-active-row",
        "task_class": "fix_bug",
        "requested_outcome": (
            "Restore the exact metadata-rich host Plan artifact. "
            "PLAN_GROUP=host_plan_runtime_continuity "
            "COMMIT_BATCH=state_travel_runtime_foundations "
            "GIT_STAGE=IMPLEMENT_BEFORE_GROUP_COMMIT."
        ),
        "permitted_paths": ["plugins/evidence-lane-plugin/**", "tests/**"],
        "permitted_tools": ["repository_read", "repository_write", "test"],
        "acceptance_checks": ["The exact host Plan projection is replay-safe."],
        "stop_condition": "Stop after the host Plan receipt passes.",
        "plan_group": "host_plan_runtime_continuity",
        "commit_batch_id": "state_travel_runtime_foundations",
        "git_commit_stage": "IMPLEMENT_BEFORE_GROUP_COMMIT",
    }
    return [
        first,
        {
            **first,
            "task_id": "host-plan-pending-row",
            "requested_outcome": "Verify cold and warm Plan recovery.",
        },
        {
            **first,
            "task_id": "host-plan-final-hil",
            "requested_outcome": "Render the physically final six-way HIL.",
            "panel_role": "PHYSICALLY_FINAL_HIL",
        },
    ]


def _classified_host_plan(service) -> tuple[str, str, dict[str, object]]:
    session_id, _ = build_and_approve_pv1(service)
    host_task_id = "host-session-state-travel-pv1"
    tasks = _tasks()
    service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="host-plan-rehydration-plan",
    )
    session = service.sessions.load("book-faires", session_id)
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [str(task["task_id"]) for task in tasks],
    }
    service.sessions._save(session)
    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome=str(tasks[0]["requested_outcome"]),
        permitted_paths=list(tasks[0]["permitted_paths"]),
        permitted_tools=list(tasks[0]["permitted_tools"]),
        acceptance_checks=list(tasks[0]["acceptance_checks"]),
        stop_condition=str(tasks[0]["stop_condition"]),
        backlog_task_id=str(tasks[0]["task_id"]),
    )
    return session_id, host_task_id, classified


def test_host_plan_rehydration_is_exact_replay_safe_and_non_promoting(
    service,
    source_repository,
) -> None:
    session_id, host_task_id, classified = _classified_host_plan(service)
    automatic = classified["host_plan_rehydration"]
    assert automatic["state"] == "REHYDRATION_RECEIPT_SEALED"
    receipt = validate_host_plan_rehydration_receipt(automatic["receipt"])
    projection = receipt["projection"]
    assert projection["row_start"] == 1
    assert projection["row_end"] == 3
    assert projection["item_count"] == 4
    assert projection["delta_item_count"] == 3
    assert projection["fixed_header_item_count"] == 1
    assert projection["step_one_is_delta_row"] is False
    assert projection["sole_active_row"] == 1
    assert projection["physically_final_hil_row"] == 3
    assert [row["status"] for row in projection["items"]] == [
        "completed",
        "in_progress",
        "pending",
        "pending",
    ]
    assert projection["ui_row_max_lines"] == 4
    assert projection["ui_projection_contains_full_plan_row"] is False
    assert projection["ui_overflow_creates_canonical_row"] is False
    assert all(len(row["step"].splitlines()) <= 4 for row in projection["items"])
    assert projection["items"][0]["step"] == (
        "PV1/generation 1 | ACTIVE R1 | ACTIVE BATCH R1-R3 | NEXT_HIL R3 | FINAL_HIL R3"
    )
    assert projection["items"][1]["step"].splitlines()[0].startswith("R1|ID=HOST~")
    assert "C=FIX|" in projection["items"][1]["step"]
    assert "|Git" not in projection["items"][1]["step"]
    assert "|D=ROOT|" in projection["items"][1]["step"]
    assert "|FTS=1:" in projection["items"][1]["step"]
    assert "Restore the exact metadata-rich host Plan" in projection["items"][1]["step"]
    assert projection["detail_retrieval_contract"] == {
        "full_row_authority": "PLAN_LANE",
        "primary_lookup": "EXACT_TASK_ID",
        "linked_evidence_lookup": "BOUNDED_FTS_ON_DEMAND",
        "raw_pv_loaded_into_model_context": False,
        "raw_chat_scrollback_loaded_into_model_context": False,
        "ui_projection_reconstructs_full_row": False,
    }
    assert [row["task_id"] for row in projection["item_bindings"]] == [
        None,
        "host-plan-active-row",
        "host-plan-pending-row",
        "host-plan-final-hil",
    ]
    assert projection["item_bindings"][1]["fts_link_id"].startswith("plan:1:")
    assert receipt["action"] == "ACTIVATE_HOST_PLAN_CURRENT_WINDOW"
    assert receipt["host_artifact_visibility_status"] == "UNCONFIRMED"
    assert receipt["host_plan_acceptance_status"] == (
        "NOT_REQUIRED_FOR_EXISTING_TASK_PLAN"
    )
    assert receipt["native_runtime_invoked_host_update_plan"] is False
    assert receipt["host_surface_persistence"] == {
        "schema": "evidence-lane.host-surface-persistence.v2",
        "native_plan_surface": "CODEX_RIGHT_SIDE_PLAN",
        "native_changes_surface": "CODEX_RIGHT_SIDE_CHANGES",
        "plan_activation_action": "update_plan",
        "host_plan_mode": "FIXED_HEADER_PLUS_NINE_DELTA_WINDOW",
        "ordinary_turn_action": "REUSE_CURRENT_NATIVE_ARTIFACT",
        "plan_steer_action": ("SYNC_ONLY_WHEN_CURRENT_WINDOW_UI_FINGERPRINT_CHANGES"),
        "evi_refresh_command_invoked_by_host_plan_sync": False,
        "task_transition_action": "UPDATE_STATUSES_WITHIN_CURRENT_WINDOW",
        "window_completion_action": ("SEAL_COMPLETED_WINDOW_AND_ACTIVATE_NEXT_WINDOW"),
        "final_window_cardinality": (
            "ONE_FIXED_HEADER_PLUS_EXACT_REMAINING_DELTA_ROWS_UP_TO_NINE"
        ),
        "full_ledger_remains_native_authority": True,
        "pv_exit_reconstructs_new_entry": False,
        "changes_surface_binding": "EXACT_TASK_UUID_AND_WORKTREE",
        "changes_surface_observation_independent_from_plan": True,
        "changes_surface_loss_overrides_plan_visible_noop": True,
        "step_list_projection_owner": "ACTIVE_CANONICAL_PLAN_NOT_GOAL",
        "required_until": (
            "HUMAN_MARKS_GOAL_COMPLETE_OR_EXPLICIT_TASK_STATE_TRAVEL_HANDOFF_PASSES"
        ),
        "goal_completion_authority": "HUMAN_ONLY",
        "hil_may_complete_goal": False,
        "drop_is_continuity_failure": True,
        "rehydrate_before_source_or_lifecycle_work": True,
        "canonical_rehydration_source": ("PLAN_LANE_BACKLOG_NOT_THREAD_HISTORY"),
        "projection_only_law": "NATIVE_HOST_PLAN_PROJECTION_ONLY_LAW",
        "manual_summary_projection_allowed": False,
        "native_contract_forwarded_unchanged": True,
        "full_thread_history_hydration_allowed": False,
        "collaboration_overlay_hydration_allowed_during_recovery": False,
        "recovery_concurrency": "ONE_ACTIVE_TASK_ZERO_SUBAGENTS",
        "renderer_reset_effect": ("FAIL_CLOSED_THEN_REPROJECT_EXACTLY_ONCE_PER_EVENT"),
        "host_owned_surface_survival_guaranteed_by_plugin": False,
        "missing_host_capability_behavior": "FAIL_CLOSED",
    }

    public_window = service.task_backlog_window("book-faires")
    assert public_window["row_ui_contract"] == (
        "HEADER_THEN_FOUR_LINES_PER_DELTA_2_AUTHORITY_2_HUMAN"
    )
    assert public_window["items"] == projection["items"]
    assert public_window["fixed_header"] == projection["continuity_header"]
    assert (
        public_window["host_update_plan_contract"]
        == projection["host_update_plan_contract"]
    )

    pointer_before = service.store.pointer("book-faires").as_dict()
    git_before = subprocess.run(
        ["git", "-C", str(source_repository), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    replay = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="TASK_CLASSIFICATION_TRANSITION",
        trigger_event_id=classified["classification_binding"]["receipt_sha256"],
    )
    assert replay["state"] == "REHYDRATION_RECEIPT_IDEMPOTENT_REUSE"
    assert replay["receipt"] == automatic["receipt"]

    panel_loss = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="PANEL_LOSS",
        trigger_event_id="host-panel-loss-observation-001",
        observed_artifact={
            "state": "MISSING",
            "project_id": "book-faires",
            "host_task_id": host_task_id,
            "observation_event_id": "host-panel-loss-observation-001",
            "sources_visible": True,
            "icon_visible": True,
            "native_backlog_readback": True,
            "host_update_plan_receipt": {},
        },
    )
    assert panel_loss["state"] == "REHYDRATION_RECEIPT_SEALED"
    assert (
        panel_loss["receipt"]["projection"]["projection_sha256"]
        == (projection["projection_sha256"])
    )
    assert panel_loss["receipt"]["action"] == ("REACTIVATE_EXISTING_HOST_PLAN_WINDOW")
    observation = panel_loss["receipt"]["observation"]
    assert observation["sources_presence_is_visibility_proof"] is False
    assert observation["icon_presence_is_visibility_proof"] is False
    assert observation["native_backlog_readback_is_visibility_proof"] is False
    assert observation["empty_update_plan_receipt_is_visibility_proof"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    assert (
        subprocess.run(
            ["git", "-C", str(source_repository), "status", "--porcelain=v1"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout
        == git_before
    )


def test_host_plan_visibility_acceptance_capability_and_project_boundaries(
    service,
) -> None:
    session_id, host_task_id, classified = _classified_host_plan(service)
    projection = classified["host_plan_rehydration"]["receipt"]["projection"]
    visible = {
        "state": "VISIBLE_UNACCEPTED",
        "project_id": "book-faires",
        "host_task_id": host_task_id,
        "observation_event_id": "visible-plan-001",
        "surface": "CODEX_RIGHT_SIDE_PLAN",
        "artifact_id": "codex-plan-artifact-001",
        "projection_sha256": projection["projection_sha256"],
        "item_count": projection["item_count"],
        "host_update_plan_receipt": {},
    }
    visible_result = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="EXPLICIT_HOST_OBSERVATION",
        trigger_event_id="visible-plan-001",
        observed_artifact=visible,
        host_goal_active=True,
    )["receipt"]
    assert visible_result["action"] == ("NO_HOST_PLAN_ACTION_CURRENT_WINDOW_VISIBLE")
    assert visible_result["host_artifact_visibility_status"] == "CONFIRMED_VISIBLE"
    assert visible_result["host_plan_acceptance_status"] == (
        "NOT_REQUIRED_FOR_EXISTING_TASK_PLAN"
    )

    accepted = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="EXPLICIT_HOST_OBSERVATION",
        trigger_event_id="accepted-plan-001",
        observed_artifact={
            **visible,
            "state": "VISIBLE_ACCEPTED",
            "observation_event_id": "accepted-plan-001",
            "explicit_acceptance_event_id": "host-accept-control-001",
            "acceptance_control": "ACCEPT",
        },
        host_goal_active=False,
    )["receipt"]
    assert accepted["host_plan_acceptance_status"] == "EXPLICITLY_ACCEPTED"
    assert accepted["host_plan_acceptance_is_evidence_lane_hil"] is False
    assert accepted["pointer_moved"] is False

    unavailable = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="SESSION_START_COLD",
        trigger_event_id="cold-start-without-plan-capability",
        host_capability="HOST_CAPABILITY_UNAVAILABLE",
    )["receipt"]
    assert unavailable["status"] == "BLOCKED"
    assert unavailable["action"] == "FAIL_CLOSED_HOST_CAPABILITY_UNAVAILABLE"
    assert unavailable["native_runtime_invoked_host_update_plan"] is False

    with pytest.raises(EvidenceLaneError) as cross_project:
        prepare_host_plan_rehydration(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            host_task_id=host_task_id,
            trigger="PANEL_LOSS",
            trigger_event_id="cross-project-observation",
            observed_artifact={
                "state": "MISSING",
                "project_id": "another-project",
                "host_task_id": host_task_id,
            },
        )
    assert cross_project.value.code == "HOST_PLAN_OBSERVATION_BINDING_MISMATCH"


def test_changes_loss_overrides_visible_plan_and_binds_exact_task_worktree(
    service,
) -> None:
    session_id, host_task_id, classified = _classified_host_plan(service)
    activation = classified["host_plan_rehydration"]["receipt"]
    projection = activation["projection"]
    worktree_sha256 = activation["changes_surface_binding"]["worktree_binding_sha256"]
    visible_plan = {
        "state": "VISIBLE_UNACCEPTED",
        "project_id": "book-faires",
        "host_task_id": host_task_id,
        "observation_event_id": "visible-plan-during-changes-loss",
        "surface": "CODEX_RIGHT_SIDE_PLAN",
        "artifact_id": "codex-plan-artifact-001",
        "projection_sha256": projection["projection_sha256"],
        "item_count": projection["item_count"],
    }
    result = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger="CHANGES_SURFACE_LOSS",
        trigger_event_id="changes-loss-001",
        observed_artifact=visible_plan,
        observed_changes_artifact={
            "state": "MISSING",
            "project_id": "book-faires",
            "host_task_id": host_task_id,
            "worktree_binding_sha256": worktree_sha256,
            "observation_event_id": "changes-loss-001",
        },
    )["receipt"]
    assert result["action"] == ("RELOCK_HOST_PLAN_AND_REQUEST_CHANGES_SURFACE_RECOVERY")
    assert result["host_update_plan_required"] is True
    assert result["host_changes_action_receipt_required"] is True
    assert result["host_changes_surface_status"] == "MISSING_OR_STALE"
    assert result["changes_surface_recovery_contract"]["relock_plan_atomically"] is True
    assert result["projection"] == projection

    with pytest.raises(EvidenceLaneError) as wrong_worktree:
        prepare_host_plan_rehydration(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            host_task_id=host_task_id,
            trigger="EXPLICIT_HOST_OBSERVATION",
            trigger_event_id="wrong-worktree-changes",
            observed_changes_artifact={
                "state": "VISIBLE_UNACCEPTED",
                "project_id": "book-faires",
                "host_task_id": host_task_id,
                "active_plan_task_id": projection["sole_active_task_id"],
                "worktree_binding_sha256": "0" * 64,
                "surface": "CODEX_RIGHT_SIDE_CHANGES",
                "artifact_id": "wrong-worktree-changes",
            },
        )
    assert wrong_worktree.value.code == "HOST_CHANGES_OBSERVATION_BINDING_MISMATCH"


@pytest.mark.parametrize(
    ("trigger", "goal_active"),
    [
        ("SESSION_START_COLD", None),
        ("SESSION_START_WARM", None),
        ("HOT_REATTACH", None),
        ("PRECOMPACT", None),
        ("POSTCOMPACT", None),
        ("GOAL_ACTIVE_TURN", True),
        ("NO_GOAL_TURN", False),
        ("STALE_ARTIFACT", None),
        ("APP_RENDERER_RELOAD", True),
        ("HOST_REACT_ROOT_RERENDER", True),
        ("THREAD_HYDRATION_OVERFLOW", True),
        ("COLLABORATION_OVERLAY_CONFLICT", True),
        ("TASK_PANEL_LOSS", True),
        ("CHANGES_SURFACE_LOSS", True),
    ],
)
def test_all_recovery_triggers_preserve_the_same_native_projection(
    service,
    trigger: str,
    goal_active: bool | None,
) -> None:
    session_id, host_task_id, classified = _classified_host_plan(service)
    expected = classified["host_plan_rehydration"]["receipt"]["projection"]
    result = prepare_host_plan_rehydration(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        host_task_id=host_task_id,
        trigger=trigger,
        trigger_event_id=f"{trigger.lower()}-event",
        observed_artifact={"state": "STALE"} if trigger == "STALE_ARTIFACT" else None,
        host_goal_active=goal_active,
    )["receipt"]
    assert result["projection"] == expected
    assert result["projection_owner"] == "ACTIVE_CANONICAL_PLAN"
    assert result["goal_presence_is_projector_precondition"] is False
    assert result["host_goal_presence_changes_projection"] is False
    assert result["candidate_created"] is False
    assert result["pointer_moved"] is False


class _BacklogOnlyStore:
    def __init__(self, goal_projection: dict[str, object]) -> None:
        self._goal_projection = goal_projection

    def backlog_status(self, project_id: str) -> dict[str, object]:
        assert project_id == "window-project"
        return {"goal_projection": self._goal_projection}

    def pointer(self, project_id: str):
        assert project_id == "window-project"

        class _Pointer:
            @staticmethod
            def as_dict() -> dict[str, object]:
                return {"accepted_pv": "PV12", "generation": 12}

        return _Pointer()


def _window_goal(*, active_row: int, total_rows: int = 26) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for number in range(1, total_rows + 1):
        task_id = f"window-task-{number:02d}"
        step = f"Execute bounded window task {number}."
        if number < active_row:
            status = "completed"
            lifecycle_status = "DONE"
        elif number == active_row:
            status = "in_progress"
            lifecycle_status = "ACTIVE"
        else:
            status = "pending"
            lifecycle_status = "QUEUED"
        role = "PHYSICALLY_FINAL_HIL" if number == total_rows else "STANDARD"
        rows.append(
            {
                "number": number,
                "task_id": task_id,
                "step": step,
                "status": status,
                "lifecycle_status": lifecycle_status,
                "panel_role": role,
                "visible_label": (
                    f"Row {number} / {task_id} — "
                    f"[CLASS=test; GROUP=window; BATCH=batch; DEP=ROOT; "
                    f"GIT=NOT_DECLARED@TASK_TEXT; VERSION=3.0.0@TASK_TEXT; "
                    f"BRANCH=test@TASK_TEXT; ROLE={role}; "
                    f"STATE={lifecycle_status}] {step}"
                ),
            }
        )
    return {
        "canonical_authority": "PLAN_LANE",
        "canonical_plan_sha256": "A" * 64,
        "projection_sha256": f"projection-{active_row}",
        "visible_label_contract": "metadata-rich",
        "visible_label_metadata_schema": "v1",
        "row_start": 1,
        "row_end": total_rows,
        "task_count": total_rows,
        "rows": rows,
    }


def test_host_plan_projects_the_active_row_as_step_two_with_next_eight_rows() -> None:
    fixed_task_ids = [f"window-task-{number:02d}" for number in range(11, 20)]
    projection = _exact_projection(
        _BacklogOnlyStore(_window_goal(active_row=11)),  # type: ignore[arg-type]
        project_id="window-project",
        fixed_window_task_ids=fixed_task_ids,
    )
    assert projection["total_executable_count"] == 26
    assert projection["window_size"] == 9
    assert projection["maximum_host_item_count"] == 10
    assert projection["window_index"] == 1
    assert projection["row_start"] == 11
    assert projection["row_end"] == 19
    assert projection["item_count"] == 10
    assert len(projection["items"][0]["step"].splitlines()) == 1
    assert all(
        len(item["step"].splitlines()) == 4
        and all(len(line) <= 72 for line in item["step"].splitlines())
        for item in projection["items"][1:]
    )
    assert projection["sole_active_row"] == 11
    assert projection["completed_window_count"] == 1
    assert projection["completed_window_history"][0]["row_start"] == 1
    assert projection["completed_window_history"][0]["row_end"] == 9
    assert projection["next_window_row_start"] == 20
    assert projection["next_window_row_end"] == 26
    assert projection["physically_final_hil_visible_in_window"] is False
    assert projection["continuity_header"] == {
        "schema": "evidence-lane.host-plan-continuity-header.v1",
        "surface": "HOST_STEP_TASK_LIST_HEADER",
        "purpose": "CROSS_WINDOW_EXECUTION_CONTINUITY",
        "accepted_pv": "PV12",
        "pointer_generation": 12,
        "absolute_active_row": 11,
        "absolute_active_task_id": "window-task-11",
        "window_row_start": 11,
        "window_row_end": 19,
        "window_ordinal": 2,
        "window_count": 3,
        "total_executable_count": 26,
        "completed_count": 10,
        "queued_count": 15,
        "queued_row_start": 12,
        "queued_row_end": 26,
        "queued_after_window_count": 7,
        "queued_after_window_row_start": 20,
        "queued_after_window_row_end": 26,
        "next_hil_boundary_row": 26,
        "next_hil_boundary_task_id": "window-task-26",
        "physically_final_row": 26,
        "physically_final_task_id": "window-task-26",
        "physically_final_candidate": None,
        "physically_final_hil_scope": "FINAL_PROJECT_HIL_NOT_INTERMEDIATE",
        "detailed_hil_queue_surface": "EVIDENCE_LANE_PROJECT_RENDERER",
        "hil_controls_in_step_task_list": False,
        "visible_text": (
            "PV12/generation 12 | ACTIVE R11 | ACTIVE BATCH R11-R19 | "
            "NEXT_HIL R26 | FINAL_HIL R26"
        ),
    }
    assert projection["host_update_plan_contract"] == {
        "explanation": (
            "Evidence Lane bounded Step Task List | Step 1 fixed progress | "
            "Steps 2-10 native Delta rows"
        ),
        "plan": projection["items"],
        "source_window_ui_fingerprint_sha256": projection[
            "window_ui_fingerprint_sha256"
        ],
        "native_contract_must_be_forwarded_unchanged": True,
        "manual_summary_projection_forbidden": True,
        "fallback_projection_forbidden": True,
        "header_surface": "HOST_STEP_TASK_LIST_STEP_1",
        "header_role": "FIXED_PROGRESS_HEADER",
        "header_is_plan_item": True,
        "header_is_delta_row": False,
        "detailed_hil_queue_in_step_task_list": False,
    }
    law = projection["native_host_plan_projection_only_law"]
    assert law["law_id"] == "NATIVE_HOST_PLAN_PROJECTION_ONLY_LAW"
    assert law["authority_route"] == "PV_TASK_BACKLOG_HOST_UPDATE_PLAN_CONTRACT"
    assert law["panel_loss_action"] == (
        "RELOCK_SAME_PERSISTED_CONTRACT_AND_FINGERPRINT"
    )
    assert law["manual_summary_projection_allowed"] is False
    assert law["generic_fallback_projection_allowed"] is False


def test_host_plan_blocks_obsolete_sliding_or_dedup_fallback() -> None:
    with pytest.raises(EvidenceLaneError) as exc:
        _exact_projection(
            _BacklogOnlyStore(_window_goal(active_row=11)),  # type: ignore[arg-type]
            project_id="window-project",
        )
    assert exc.value.code == "HOST_PLAN_FIXED_BATCH_REQUIRED"


def test_host_plan_bounds_long_r259_description_without_fallback() -> None:
    lines = _host_step_description_lines(
        "Build one collision-free v2 package, seal "
        "source/package/commit/tree/worktree/catalog/skill/hook/resource "
        "identities, install supportedly, and hot-reattach the exact open "
        "task; restart is fallback."
    )
    assert len(lines) == 2
    assert lines[0].startswith("Do: ")
    assert lines[1].startswith("   ")
    assert all(len(line) <= 72 for line in lines)
    assert "~" in lines[1]


def test_host_plan_final_window_contains_only_the_exact_remaining_rows() -> None:
    fixed_task_ids = [f"window-task-{number:02d}" for number in range(21, 27)]
    projection = _exact_projection(
        _BacklogOnlyStore(_window_goal(active_row=21)),  # type: ignore[arg-type]
        project_id="window-project",
        fixed_window_task_ids=fixed_task_ids,
    )
    assert projection["window_index"] == 2
    assert projection["row_start"] == 21
    assert projection["row_end"] == 26
    assert projection["item_count"] == 7
    assert projection["completed_window_count"] == 2
    assert projection["next_window_row_start"] is None
    assert projection["remaining_after_current_window"] == 0
    assert projection["physically_final_hil_visible_in_window"] is True
    final_step_lines = projection["items"][-1]["step"].splitlines()
    assert len(final_step_lines) == 4
    assert final_step_lines[0].startswith("R26|ID=")
    assert final_step_lines[1].startswith("C=")
    assert final_step_lines[2] == "Do: Execute bounded"
    assert final_step_lines[3] == "   window task 26."
    assert projection["continuity_header"]["physically_final_row"] == 26


def test_host_plan_header_ignores_out_of_scope_future_pv_mentions() -> None:
    goal = _window_goal(active_row=21)
    rows = goal["rows"]
    assert isinstance(rows, list)
    rows[-1]["steer_deltas"] = [
        {
            "text": (
                "PHYSICALLY FINAL PV14 HIL. Complete PV14 first. "
                "PV15 is a future out-of-scope cycle."
            )
        }
    ]
    projection = _exact_projection(
        _BacklogOnlyStore(goal),  # type: ignore[arg-type]
        project_id="window-project",
        fixed_window_task_ids=[f"window-task-{number:02d}" for number in range(21, 27)],
    )
    assert projection["continuity_header"]["physically_final_candidate"] == ("PV14")
    assert "FINAL_HIL R26" in projection["items"][0]["step"]


def test_host_plan_shows_git_only_on_declared_execution_row() -> None:
    goal = _window_goal(active_row=1, total_rows=3)
    rows = goal["rows"]
    assert isinstance(rows, list)
    rows[0]["git_commit_stage"] = "NO_COMMIT"
    rows[1]["git_commit_stage"] = "COMMIT_AND_PUSH_EXACT_TASK"
    rows[2]["git_commit_stage"] = "COMMIT_AND_PUSH_EXACT_TASK"
    projection = _exact_projection(
        _BacklogOnlyStore(goal),  # type: ignore[arg-type]
        project_id="window-project",
        fixed_window_task_ids=[
            "window-task-01",
            "window-task-02",
            "window-task-03",
        ],
    )
    assert "|Git" not in projection["items"][1]["step"].splitlines()[0]
    assert "|Git" in projection["items"][2]["step"].splitlines()[0]
    assert "|Git" not in projection["items"][3]["step"]


def test_fixed_host_window_does_not_slide_when_active_status_advances() -> None:
    fixed_task_ids = [f"window-task-{number:02d}" for number in range(10, 19)]
    first = _exact_projection(
        _BacklogOnlyStore(_window_goal(active_row=11)),  # type: ignore[arg-type]
        project_id="window-project",
        fixed_window_task_ids=fixed_task_ids,
    )
    second = _exact_projection(
        _BacklogOnlyStore(_window_goal(active_row=12)),  # type: ignore[arg-type]
        project_id="window-project",
        fixed_window_task_ids=fixed_task_ids,
    )

    assert first["row_start"] == second["row_start"] == 10
    assert first["row_end"] == second["row_end"] == 18
    assert first["window_task_ids"] == second["window_task_ids"] == (fixed_task_ids)
    assert first["sole_active_row"] == 11
    assert second["sole_active_row"] == 12
    assert first["items"][1]["status"] == "completed"
    assert second["items"][1]["status"] == "completed"
