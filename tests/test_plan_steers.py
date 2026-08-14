from __future__ import annotations

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError


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
    assert [row["number"] for row in appended["backlog"]["goal_projection"]["rows"]] == [
        1,
        2,
        3,
    ]


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
    assert [
        row["steer_deltas"][0]["delta_id"]
        for row in ordered[1:-1]
    ] == [f"late-steer-{index:03d}" for index in range(1, 8)]


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
        "current_version": "2.2.0",
        "current_branch": "agent/evi-v220-metadata",
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
    assert rows[1]["version_marker"] == "2.2.0"
    assert rows[1]["version_marker_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[1]["branch_marker"] == "agent/evi-v220-metadata"
    assert rows[1]["branch_marker_source"] == "EXPLICIT_TASK_CONTRACT"
    assert rows[1]["authority_scope"] == "CURRENT_EXECUTABLE_PLAN"
    assert rows[1]["effective_for_execution"] is True
    assert rows[1]["visible_label"].startswith(
        "Row 2 / metadata-step-002 — [CLASS=prepare_patch; "
        "GROUP=corpus-ingest; BATCH=batch-alpha; "
        "DEP=metadata-step-001; GIT=COMMIT@EXPLICIT_TASK_CONTRACT; "
        "VERSION=2.2.0@EXPLICIT_TASK_CONTRACT; "
        "BRANCH=agent/evi-v220-metadata@EXPLICIT_TASK_CONTRACT; "
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
    assert state_travel_projection["goal_or_source_work_before_plan_acceptance"] is False


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
    assert "VERSION=2.1.0@LINKED_DELTA:release-current-authority-steer" in (
        resolved["visible_label"]
    )
    assert service.task_backlog("book-faires")["goal_projection"]["rows"][-1][
        "panel_role"
    ] == "PHYSICALLY_FINAL_HIL"


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
            "CURRENT_VERSION=2.2.0 "
            "CURRENT_BRANCH=agent/evi-v220-release "
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
    assert rows[0]["version_marker"] == "2.2.0"
    assert rows[0]["version_marker_source"] == (
        "LINKED_DELTA:active-release-context-steer"
    )
    assert rows[1]["version_marker"] == "2.2.0"
    assert rows[1]["version_marker_source"] == (
        "ACTIVE_PLAN_CONTEXT:release-active:active-release-context-steer"
    )
    assert rows[1]["branch_marker"] == "agent/evi-v220-release"
    assert rows[1]["branch_marker_source"] == (
        "ACTIVE_PLAN_CONTEXT:release-active:active-release-context-steer"
    )
    assert "version 2.0.0" in rows[1]["step"]
    assert "VERSION=2.2.0@ACTIVE_PLAN_CONTEXT" in rows[1]["visible_label"]
    assert rows[-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert "ROLE=PHYSICALLY_FINAL_HIL" in rows[-1]["visible_label"]
    release = projection["active_release_context"]
    assert release["target_version"] == "2.2.0"
    assert release["target_branch"] == "agent/evi-v220-release"
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
