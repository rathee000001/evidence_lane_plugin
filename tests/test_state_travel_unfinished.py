from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.state_travel_contract import normalize_task_list


def _profile() -> dict[str, str]:
    return {
        "model": "gpt-5.6",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }


def _destination_creation(
    source_task_id: str,
    destination_task_id: str,
) -> dict[str, object]:
    return {
        "schema": "evidence-lane.host-destination-creation.v1",
        "capability_status": "SUPPORTED",
        "host_action": "CONTINUE_IN_NEW_CHAT",
        "programmatic": True,
        "creation_count": 1,
        "source_task_id": source_task_id,
        "source_task_deep_link": f"codex://threads/{source_task_id}",
        "destination_task_id": destination_task_id,
        "destination_task_deep_link": (
            f"codex://threads/{destination_task_id}"
        ),
        "canonical_title_increment_verified": True,
    }


def _queued_destination_creation(
    source_task_id: str,
    client_thread_id: str,
    destination_task_id: str,
    *,
    live_destination_task_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "schema": "evidence-lane.host-destination-creation.v1",
        "capability_status": "SUPPORTED",
        "host_action": "CONTINUE_IN_NEW_CHAT",
        "programmatic": True,
        "creation_count": 1,
        "source_task_id": source_task_id,
        "source_task_deep_link": f"codex://threads/{source_task_id}",
        "clientThreadId": client_thread_id,
        "canonical_title_increment_verified": True,
        "destination_resolution": {
            "schema": "evidence-lane.host-destination-resolution.v1",
            "status": "RESOLVED_UNIQUE",
            "client_thread_id": client_thread_id,
            "destination_task_id": destination_task_id,
            "destination_task_deep_link": (
                f"codex://threads/{destination_task_id}"
            ),
            "live_destination_task_ids": (
                live_destination_task_ids or [destination_task_id]
            ),
            "duplicate_task_ids": ["duplicate-destination-history"],
            "archived_task_ids": ["archived-destination-history"],
        },
    }


def test_state_travel_preserves_unaccepted_candidate_and_exact_resume_row(
    service,
) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="origin-codex-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    candidate = service.build_initial("book-faires", session_id)["candidate"]
    task_rows = [
        {"number": 1, "step": "Inspect source", "status": "COMPLETED"},
        {"number": 2, "step": "Present exact PV1 HIL", "status": "IN_PROGRESS"},
        {"number": 3, "step": "Fuse only after APPROVE", "status": "PENDING"},
    ]
    resume_contract = {
        "task_list": task_rows,
        "resume_step": 2,
        "additive_deltas": [
            {
                "delta_id": "STEER_001",
                "text": "Keep the task panel visible through the next HIL.",
                "linked_step": 2,
            }
        ],
        "execution_profile": profile,
    }
    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract=resume_contract,
    )
    handoff = prepared["state_travel"]
    assert handoff["schema"] == "evidence-lane.state-travel.v2"
    assert handoff["travel_mode"] == "UNFINISHED_VERIFIED_WORK"
    assert handoff["accepted_pv"] is None
    assert handoff["candidate_snapshot"]["candidate_id"] == candidate["candidate_id"]
    assert handoff["resume_contract"]["resume_step"] == 2
    assert handoff["resume_contract"]["task_list"] == [
        {**row, "task_id": f"STEP_{row['number']:03d}"} for row in task_rows
    ]
    assert handoff["resume_contract"]["collaboration_law"] == {
        "writer_policy": "SOLE_WRITER",
        "entry_recovery_subagents": (
            "READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"
        ),
        "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
        "alternate_checkout_writer": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "background_mutation": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "browser_profile": (
            "ONE_USER_SELECTED_PROFILE_ONLY_UNLESS_EXPLICIT_USER_CHANGE"
        ),
    }
    execution_writer_boundary = handoff["resume_contract"][
        "execution_writer_boundary"
    ]
    assert execution_writer_boundary == {
        "schema": "evidence-lane.execution-writer-boundary.v1",
        "project_policy": "ONE_GOVERNED_PROJECT",
        "writer_policy": "ONE_LIVE_WRITER",
        "execution_order": "LINEAR",
        "verification_order": "EVIDENCE_FIRST",
        "execution_profile": profile,
        "execution_profile_change_authority": "EXPLICIT_USER_CHANGE_ONLY",
        "entry_recovery_agents": (
            "READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"
        ),
        "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
        "alternate_checkout_writer": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "background_mutation": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "browser_profile": (
            "ONE_USER_SELECTED_PROFILE_ONLY_UNLESS_EXPLICIT_USER_CHANGE"
        ),
    }
    assert handoff["next_action_contract"]["execution_writer_boundary"] == (
        execution_writer_boundary
    )
    goal_continuity = handoff["resume_contract"]["goal_continuity"]
    assert goal_continuity == {
        "schema": "evidence-lane.goal-continuity.v1",
        "project_id": "book-faires",
        "session_id": session_id,
        "plan_authority": "SAME_CANONICAL_PLAN_LANE",
        "source_boundary": "SAME_ACTIVE_SOURCE_BOUNDARY",
        "writer_session": "SAME_SINGLE_WRITER_SESSION",
        "task_list_sha256": handoff["resume_contract"]["task_list_sha256"],
        "active_row": 2,
        "pause_triggers": [
            "UI_CRASH",
            "TOKEN_WAIT",
            "REQUIRED_USER_INPUT",
            "HIL_WAIT",
        ],
        "pause_effect": "PAUSE_DEPENDENT_WORK_ONLY",
        "goal_completion_effect_while_waiting": "FORBIDDEN",
        "usage_reporting_task_status_effect": "NONE",
        "reconstruction_requires": [
            "ALL_COMPLETED_BUT_STILL_GOVERNING_ROWS",
            "EXACTLY_ONE_ACTIVE_ROW_WHEN_PANEL_PRESENT",
            "ALL_PENDING_ROWS",
        ],
        "completed_governing_rows_may_be_omitted": False,
        "goal_completion_allowed_when": (
            "PHYSICALLY_FINAL_SIX_WAY_HIL_DECIDED_AND_"
            "DECISION_DEPENDENT_WORK_COMPLETE"
        ),
    }
    assert handoff["next_action_contract"]["goal_continuity"] == goal_continuity
    orchestration = handoff["resume_contract"]["destination_orchestration"]
    assert orchestration["schema"] == (
        "evidence-lane.state-travel-destination-orchestration.v2"
    )
    assert orchestration["enabled"] is True
    assert orchestration["manual_plan_mode_command_required"] is False
    assert orchestration["manual_evi_plan_command_required"] is False
    assert orchestration["manual_goal_prompt_paste_required"] is False
    assert orchestration["host_mode_selector_status"] == (
        "HOST_MODE_SELECTOR_UNAVAILABLE"
    )
    assert orchestration["plan_projection_native_reads"] == [
        "pv_status",
        "pv_task_backlog",
        "pv_query",
    ]
    assert orchestration["host_plan_projection_count"] == 2
    assert orchestration["destination_creation_action"] == (
        "CONTINUE_IN_NEW_CHAT"
    )
    assert orchestration["destination_creation_exactly_once"] is True
    assert orchestration["destination_creation_user_click_required"] is False
    assert orchestration["host_plan_acceptance_required"] is True
    assert orchestration["host_plan_automatic_acceptance_allowed"] is False
    assert orchestration["host_plan_acceptance_is_evidence_lane_hil"] is False
    assert orchestration["phase_4_or_5_before_plan_acceptance_allowed"] is False
    assert [row["phase"] for row in orchestration["ordered_phases"]] == [
        "CREATE_AND_BIND_FRESH_DESTINATION_TASK",
        "ATOMIC_BOOT_FLASH_AND_RESUME_EXACTLY_ONCE",
        "RESTORE_HOST_PLAN_AND_WAIT_FOR_EXPLICIT_ACCEPTANCE",
        "VERIFY_EVIDENCE_PLAN_AFTER_HOST_ACCEPTANCE",
        "START_OR_RESUME_TRANSFERRED_PLUGIN_GOAL",
    ]
    assert orchestration["ordered_phases"][2]["blocks_phases"] == [4, 5]
    assert orchestration["ordered_phases"][3]["manual_command_required"] is False
    source_binding = handoff["resume_contract"]["source_task_binding"]
    assert source_binding == orchestration["source_task_binding"]
    assert source_binding["source_task_id"] == "origin-codex-task"
    assert source_binding["source_task_deep_link"] == (
        "codex://threads/origin-codex-task"
    )
    assert source_binding["task_title_used_as_identity"] is False
    assert source_binding["cwd_used_as_identity"] is False
    assert source_binding["plugin_build"]["plugin_version"].startswith("2.")
    assert handoff["next_action_contract"]["destination_orchestration"] == (
        orchestration
    )
    panel_reactivation = handoff["resume_contract"]["panel_reactivation"]
    assert panel_reactivation == {
        "schema": "evidence-lane.persistent-panel-reactivation.v1",
        "required": True,
        "triggers": [
            "TOKEN_DRIVEN_CONTINUATION",
            "STALLED_GOAL",
            "CONTEXT_COMPACTION",
            "BROWSER_RESTART",
            "CODEX_RESTART",
            "SESSION_CONTINUATION",
            "SESSION_RESUME",
            "STATE_TRAVEL_DESTINATION_ENTRY",
        ],
        "first_required_action": "REPROJECT_EXACT_COMPLETE_TASK_LIST",
        "must_precede": [
            "SOURCE_INSPECTION",
            "SOURCE_MUTATION",
            "TESTING",
            "GIT_ACTIVITY",
            "SUBSEQUENT_LIFECYCLE_CALL",
            ],
            "state_travel_destination_first_native_lifecycle_action": (
                "PV_STATE_TRAVEL_RESUME_EXACTLY_ONCE"
            ),
            "state_travel_destination_first_host_action_after_resume": (
                "REPROJECT_EXACT_COMPLETE_TASK_LIST"
            ),
            "host_plan_acceptance_required_before_evidence_plan": True,
            "host_plan_acceptance_is_evidence_lane_hil": False,
            "goal_or_source_work_before_host_plan_acceptance": False,
            "task_list_sha256": handoff["resume_contract"]["task_list_sha256"],
            "visible_row_start": 1,
            "visible_row_end": 3,
            "visible_row_numbering": (
                "DYNAMIC_ASCENDING_CURRENT_EXECUTION_PROJECTION"
            ),
            "stable_identity_field": "task_id",
            "renumber_after_insert_or_non_executable_transition": True,
            "active_row": 2,
        "non_empty_task_list_requires_exactly_one_in_progress": True,
        "preserve_order_and_row_count": True,
        "preserve_completed_and_pending_descriptions_unabridged": True,
        "preserve_row_task_name_class_group_batch_dependencies_git_stage": True,
        "visible_label_contract": handoff["resume_contract"][
            "panel_reactivation"
        ]["visible_label_contract"],
        "visible_through_pause_and_hil": True,
        "drop_allowed_when": (
            "PHYSICALLY_FINAL_SIX_WAY_HIL_DECIDED_AND_"
            "DECISION_DEPENDENT_WORK_COMPLETE"
        ),
    }
    assert handoff["next_action_contract"]["task_panel_reactivation"] == (
        panel_reactivation
    )
    assert handoff["resume_contract"]["task_panel_persistent_until"] == (
        "PHYSICALLY_FINAL_SIX_WAY_HIL_DECIDED_AND_"
        "DECISION_DEPENDENT_WORK_COMPLETE"
    )

    wrong_profile = {**profile, "reasoning_effort": "medium"}
    with pytest.raises(EvidenceLaneError) as mismatch:
        service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=handoff["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="wrong-profile-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={"execution_profile": wrong_profile},
        )
    assert mismatch.value.code == "STATE_TRAVEL_EXECUTION_PROFILE_MISMATCH"
    assert (
        service.sessions.load("book-faires", session_id).metadata[
            "current_host_session_id"
        ]
        == "origin-codex-task"
    )

    with pytest.raises(EvidenceLaneError) as unavailable:
        service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=handoff["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="missing-create-capability-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={"execution_profile": profile},
        )
    assert unavailable.value.code == (
        "STATE_TRAVEL_HOST_CONTINUE_IN_NEW_CHAT_UNAVAILABLE"
    )

    traveled = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="matching-profile-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={
            "execution_profile": profile,
            "state_travel_destination_creation": _destination_creation(
                "origin-codex-task",
                "matching-profile-task",
            ),
        },
    )
    assert traveled["next_action"] == (
        "RESTORE_HOST_PLAN_AND_WAIT_FOR_EXPLICIT_ACCEPTANCE"
    )
    assert traveled["wait_state"] == "WAITING_FOR_HOST_PLAN_ACCEPTANCE"
    assert traveled["continuation_ready"] is True
    assert traveled["next_action_contract"]["stop_and_wait"] is True
    assert traveled["next_action_contract"]["task_panel_reactivation"] == (
        panel_reactivation
    )
    assert traveled["next_action_contract"]["execution_writer_boundary"] == (
        execution_writer_boundary
    )
    assert traveled["next_action_contract"]["goal_continuity"] == goal_continuity
    assert traveled["next_action_contract"]["destination_orchestration"] == (
        orchestration
    )
    assert traveled["entry"]["destination_orchestration"] == orchestration
    assert traveled["state_travel"]["continuation_ready_scope"] == (
        "NATIVE_RESUME_VERIFIED_ONLY_PENDING_HOST_PLAN_ACCEPTANCE_"
        "EVIDENCE_PLAN_AND_GOAL"
    )
    assert traveled["state_travel"]["native_resume_ready"] is True
    assert traveled["state_travel"]["host_plan_acceptance_pending"] is True
    assert traveled["state_travel"]["goal_start_allowed"] is False
    assert traveled["state_travel"]["source_work_allowed"] is False
    assert traveled["session"]["state"] == "PV1_CANDIDATE"
    assert traveled["session"]["candidate_id"] == candidate["candidate_id"]
    assert traveled["pointer"]["accepted_pv"] is None
    assert traveled["state_travel"]["live_source_verified"] is True
    assert traveled["state_travel"]["execution_profile_verified"] is True
    assert traveled["entry"]["resume_step"] == 2
    destination_binding = traveled["entry"]["destination_task_binding"]
    assert destination_binding["source_task_id"] == "origin-codex-task"
    assert destination_binding["destination_task_id"] == "matching-profile-task"
    assert destination_binding["creation_count"] == 1
    assert destination_binding["task_title_used_as_identity"] is False
    assert destination_binding["cwd_used_as_identity"] is False

    pointer_before_replay = service.store.pointer("book-faires").as_dict()
    replay = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="matching-profile-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={
            "execution_profile": profile,
            "state_travel_destination_creation": _destination_creation(
                "origin-codex-task",
                "matching-profile-task",
            ),
        },
    )
    assert replay["status"] == "ALREADY_CONSUMED_NO_REBIND"
    assert replay["idempotent_reuse"] is True
    assert replay["boot_repeated"] is False
    assert replay["flash_repeated"] is False
    incident = replay["replay_incident"]
    assert incident["resume_invoked"] is False
    assert incident["host_rebound"] is False
    assert incident["source_mutated"] is False
    assert incident["pointer_moved"] is False
    assert incident["candidate_created"] is False
    assert incident["pending_hil_mutated"] is False
    assert incident["hil_inferred"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before_replay

    with pytest.raises(EvidenceLaneError) as wrong_destination:
        service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=handoff["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="different-destination-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={
                "execution_profile": profile,
                "state_travel_destination_creation": _destination_creation(
                    "origin-codex-task",
                    "different-destination-task",
                ),
            },
        )
    assert wrong_destination.value.code == (
        "STATE_TRAVEL_REPLAY_DESTINATION_MISMATCH"
    )
    assert service.store.pointer("book-faires").as_dict() == pointer_before_replay


def test_state_travel_resolves_one_queued_client_thread_and_retains_history(
    service,
) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="queued-origin-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={
            "task_list": [
                {
                    "number": 1,
                    "step": "Resume the exact active row",
                    "status": "IN_PROGRESS",
                },
                {
                    "number": 2,
                    "step": "Present the final HIL",
                    "status": "PENDING",
                    "panel_role": "PHYSICALLY_FINAL_HIL",
                },
            ],
            "resume_step": 1,
            "additive_deltas": [],
            "execution_profile": profile,
        },
    )["state_travel"]
    creation = _queued_destination_creation(
        "queued-origin-task",
        "client-thread-queued-001",
        "resolved-real-destination-task",
    )
    resumed = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=prepared["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="resolved-real-destination-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={
            "execution_profile": profile,
            "state_travel_destination_creation": creation,
        },
    )
    assert resumed["status"] == "PASS"
    binding = resumed["entry"]["destination_task_binding"]
    resolution = binding["destination_resolution"]
    assert resolution["status"] == "RESOLVED_UNIQUE"
    assert resolution["client_thread_id"] == "client-thread-queued-001"
    assert resolution["live_destination_task_ids"] == [
        "resolved-real-destination-task"
    ]
    assert resolution["duplicate_task_ids"] == [
        "duplicate-destination-history"
    ]
    assert resolution["archived_task_ids"] == [
        "archived-destination-history"
    ]
    assert resumed["state_travel"]["resume_consumption_receipt"][
        "resume_invocation_count"
    ] == 1

    replay = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=prepared["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="resolved-real-destination-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={
            "execution_profile": profile,
            "state_travel_destination_creation": creation,
        },
    )
    assert replay["status"] == "ALREADY_CONSUMED_NO_REBIND"
    assert replay["state_travel"]["resume_consumption_receipt"][
        "resume_invocation_count"
    ] == 1

    ambiguous_creation = _queued_destination_creation(
        "queued-origin-task",
        "client-thread-queued-001",
        "resolved-real-destination-task",
        live_destination_task_ids=[
            "resolved-real-destination-task",
            "second-live-destination-task",
        ],
    )
    with pytest.raises(EvidenceLaneError) as ambiguous:
        service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=prepared["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="resolved-real-destination-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={
                "execution_profile": profile,
                "state_travel_destination_creation": ambiguous_creation,
            },
        )
    assert ambiguous.value.code == (
        "STATE_TRAVEL_DESTINATION_CLIENT_THREAD_AMBIGUOUS"
    )


def test_state_travel_global_lock_consumes_one_handoff_once(service) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="concurrent-origin-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={
            "task_list": [
                {
                    "number": 1,
                    "step": "Resume once globally",
                    "status": "IN_PROGRESS",
                },
                {
                    "number": 2,
                    "step": "Present the final HIL",
                    "status": "PENDING",
                    "panel_role": "PHYSICALLY_FINAL_HIL",
                },
            ],
            "resume_step": 1,
            "additive_deltas": [],
            "execution_profile": profile,
        },
    )["state_travel"]
    creation = _destination_creation(
        "concurrent-origin-task",
        "concurrent-real-destination-task",
    )

    def resume() -> dict[str, object]:
        return service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=prepared["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="concurrent-real-destination-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={
                "execution_profile": profile,
                "state_travel_destination_creation": creation,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: resume(), range(2)))

    assert sorted(str(row["status"]) for row in results) == [
        "ALREADY_CONSUMED_NO_REBIND",
        "PASS",
    ]
    consumed = next(row for row in results if row["status"] == "PASS")
    replay = next(
        row
        for row in results
        if row["status"] == "ALREADY_CONSUMED_NO_REBIND"
    )
    consumption = consumed["state_travel"]["resume_consumption_receipt"]
    assert consumption["resume_invocation_count"] == 1
    assert replay["replay_incident"][
        "original_consumption_receipt_sha256"
    ] == consumption["receipt_sha256"]
    assert replay["replay_incident"]["resume_invoked"] is False
    assert not (
        service.store.project_root("book-faires")
        / ".state-travel-resume.lock"
    ).exists()


def test_state_travel_explicit_same_host_user_correction_supersedes_unconsumed_receipt(
    service,
) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="origin-codex-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    task_rows = [
        {"number": 1, "step": "Keep exact work", "status": "IN_PROGRESS"},
        {"number": 2, "step": "Present final HIL", "status": "PENDING"},
    ]
    first = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={
            "task_list": task_rows,
            "resume_step": 1,
            "additive_deltas": [],
            "execution_profile": profile,
        },
    )["state_travel"]
    pointer_before = service.store.pointer("book-faires").as_dict()
    corrected_rows = [
        {**task_rows[0], "step": "Keep exact work with the user correction"},
        task_rows[1],
    ]

    with pytest.raises(EvidenceLaneError) as missing_authority:
        service.prepare_state_travel(
            "book-faires",
            session_id,
            resume_contract={
                "task_list": corrected_rows,
                "resume_step": 1,
                "additive_deltas": [],
                "execution_profile": profile,
            },
        )
    assert missing_authority.value.code == "STATE_TRAVEL_PREPARED_CONTRACT_MISMATCH"

    replacement_result = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={
            "task_list": corrected_rows,
            "resume_step": 1,
            "additive_deltas": [
                {
                    "delta_id": "USER_CORRECTION_001",
                    "text": "Preserve the corrected exact work.",
                    "linked_step": 1,
                }
            ],
            "execution_profile": profile,
            "supersede_prepared_handoff_id": first["handoff_id"],
            "supersede_prepared_reason": "EXPLICIT_USER_CORRECTION",
        },
    )
    replacement = replacement_result["state_travel"]
    assert replacement["handoff_id"] != first["handoff_id"]
    disposition = replacement["supersedes_prepared_handoff"]
    assert disposition["status"] == "SUPERSEDED_BY_SAME_HOST_USER_CORRECTION"
    assert disposition["handoff_id"] == first["handoff_id"]
    assert disposition["state_travel_consumed"] is False
    assert disposition["pointer_moved"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    persisted = service.sessions.load("book-faires", session_id)
    assert persisted.metadata["state_travel_history"][-1] == first
    assert persisted.metadata["state_travel"]["handoff_id"] == replacement[
        "handoff_id"
    ]
    assert replacement_result["supersession_event"] is not None


def test_non_empty_state_travel_panel_requires_exactly_one_active_row() -> None:
    assert normalize_task_list([]) == []

    with pytest.raises(EvidenceLaneError) as missing_active:
        normalize_task_list(
            [
                {"number": 1, "step": "Finished", "status": "COMPLETED"},
                {"number": 2, "step": "Waiting", "status": "PENDING"},
            ]
        )
    assert missing_active.value.code == "STATE_TRAVEL_ACTIVE_STEP_REQUIRED"

    with pytest.raises(EvidenceLaneError) as multiple_active:
        normalize_task_list(
            [
                {"number": 1, "step": "First", "status": "IN_PROGRESS"},
                {"number": 2, "step": "Second", "status": "IN_PROGRESS"},
            ]
        )
    assert multiple_active.value.code == "STATE_TRAVEL_MULTIPLE_ACTIVE_STEPS"


def test_state_travel_derives_full_active_plan_and_seals_every_plan_steer(
    service,
) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="origin-codex-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    active_task = {
        "task_id": "row-active",
        "task_class": "verify_result",
        "requested_outcome": "Finish the current correction.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Verify the bounded result."],
        "stop_condition": "Stop before the final HIL.",
    }
    final_hil = {
        **active_task,
        "task_id": "row-final-hil",
        "requested_outcome": "Present the physically final six-way HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[active_task, final_hil],
        planned_by="human-test",
        plan_id="state-travel-plan",
    )
    service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id="row-active",
        session_id=session_id,
        contract={**active_task, "task_id": "runtime-active"},
    )
    service.record_steer_delta(
        "book-faires",
        delta_text="Keep the exact correction attached to the active row.",
        actor="human-test",
        delta_id="linked-before-travel",
        linked_task_id="row-active",
    )
    service.record_steer_delta(
        "book-faires",
        delta_text="Add the late correction before the final HIL.",
        actor="human-test",
        delta_id="new-before-travel",
        new_task_contract={
            **active_task,
            "task_id": "late-row",
            "requested_outcome": "Implement the late correction.",
        },
    )

    prepared = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": profile},
    )["state_travel"]["resume_contract"]

    assert prepared["task_list_source"] == "ACTIVE_PLAN_LANE_DERIVED"
    assert [row["task_id"] for row in prepared["task_list"]] == [
        "row-active",
        "late-row",
        "row-final-hil",
    ]
    assert prepared["task_list"][-1]["panel_role"] == (
        "PHYSICALLY_FINAL_HIL"
    )
    assert prepared["resume_step"] == 1
    assert [row["delta_id"] for row in prepared["additive_deltas"]] == [
        "linked-before-travel",
        "new-before-travel",
    ]
    assert [row["linked_step"] for row in prepared["additive_deltas"]] == [1, 2]
    assert prepared["unlinked_steer_policy"] == (
        "INSERT_NEW_STEP_BEFORE_NEXT_HIL_AND_INCREASE_COUNT"
    )


def test_state_travel_fails_closed_when_explicit_seal_drops_plan_delta(
    service,
) -> None:
    profile = _profile()
    boot = service.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="sole-writer",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"execution_profile": profile},
        host_session_id="origin-codex-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    task = {
        "task_id": "active-row",
        "task_class": "verify_result",
        "requested_outcome": "Preserve the correction.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Verify the correction."],
        "stop_condition": "Stop at HIL.",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[task],
        planned_by="human-test",
        plan_id="missing-delta-plan",
    )
    service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id="active-row",
        session_id=session_id,
        contract={**task, "task_id": "runtime-active"},
    )
    service.record_steer_delta(
        "book-faires",
        delta_text="This Delta must never disappear from State Travel.",
        actor="human-test",
        delta_id="must-seal-delta",
        linked_task_id="active-row",
    )
    canonical_rows = service.task_backlog("book-faires")["goal_projection"][
        "rows"
    ]

    with pytest.raises(EvidenceLaneError) as dropped:
        service.prepare_state_travel(
            "book-faires",
            session_id,
            resume_contract={
                "task_list": canonical_rows,
                "resume_step": 1,
                "additive_deltas": [],
                "execution_profile": profile,
            },
        )
    assert dropped.value.code == "STATE_TRAVEL_PLAN_DELTA_SEAL_INCOMPLETE"
