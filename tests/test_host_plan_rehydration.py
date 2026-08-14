from __future__ import annotations

import subprocess

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.host_plan_rehydration import (
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
    assert projection["row_end"] == projection["item_count"] == 3
    assert projection["sole_active_row"] == 1
    assert projection["physically_final_hil_row"] == 3
    assert [row["status"] for row in projection["items"]] == [
        "in_progress",
        "pending",
        "pending",
    ]
    assert "CLASS=fix_bug" in projection["items"][0]["step"]
    assert "GROUP=host_plan_runtime_continuity" in projection["items"][0][
        "step"
    ]
    assert "BATCH=state_travel_runtime_foundations" in projection["items"][0][
        "step"
    ]
    assert "GIT=IMPLEMENT_BEFORE_GROUP_COMMIT" in projection["items"][0][
        "step"
    ]
    assert "ROLE=PHYSICALLY_FINAL_HIL" in projection["items"][-1]["step"]
    assert receipt["action"] == (
        "CALL_HOST_UPDATE_PLAN_EXACTLY_ONCE_FOR_THIS_TRIGGER"
    )
    assert receipt["host_artifact_visibility_status"] == "UNCONFIRMED"
    assert receipt["host_plan_acceptance_status"] == (
        "PENDING_EXPLICIT_HOST_ACCEPTANCE"
    )
    assert receipt["native_runtime_invoked_host_update_plan"] is False

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
    assert panel_loss["receipt"]["projection"]["projection_sha256"] == (
        projection["projection_sha256"]
    )
    assert panel_loss["receipt"]["action"] == (
        "CALL_HOST_UPDATE_PLAN_EXACTLY_ONCE_FOR_THIS_TRIGGER"
    )
    observation = panel_loss["receipt"]["observation"]
    assert observation["sources_presence_is_visibility_proof"] is False
    assert observation["icon_presence_is_visibility_proof"] is False
    assert observation["native_backlog_readback_is_visibility_proof"] is False
    assert observation["empty_update_plan_receipt_is_visibility_proof"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    assert subprocess.run(
        ["git", "-C", str(source_repository), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout == git_before


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
    assert visible_result["action"] == "NO_UPDATE_PLAN_CURRENT_ARTIFACT_VISIBLE"
    assert visible_result["host_artifact_visibility_status"] == "CONFIRMED_VISIBLE"
    assert visible_result["host_plan_acceptance_status"] == (
        "PENDING_EXPLICIT_HOST_ACCEPTANCE"
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
    assert result["host_goal_presence_changes_projection"] is False
    assert result["candidate_created"] is False
    assert result["pointer_moved"] is False
