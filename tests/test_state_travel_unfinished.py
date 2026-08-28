from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from evidence_lane_plugin import codex_turn_control
from evidence_lane_plugin import session as session_module
from evidence_lane_plugin.codex_turn_control import (
    TurnControlError,
    _direct_entry_checkpoint_continuity,
    _verified_direct_entry_authority,
    package_surface_inventory,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.state_travel_contract import (
    direct_forced_same_worktree_binding_contract,
    normalize_direct_forced_same_worktree_binding,
    normalize_task_list,
    preflight_direct_forced_same_worktree_binding,
    verify_direct_destination_plan_acceptance,
    verify_direct_source_option2_closeout,
)
from evidence_lane_plugin.task_binding_registry import (
    seal_or_refresh_shared_task_binding,
)

from .conftest import build_and_approve_pv1


def _profile() -> dict[str, str]:
    return {
        "model": "gpt-5.6",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }


def _direct_profile() -> dict[str, str]:
    return {
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }


def _host_continuity(
    source_task_id: str,
    destination_task_id: str,
) -> dict[str, object]:
    return {
        "schema": "evidence-lane.state-travel-host-continuity.v1",
        "status": "PASS",
        "source_task_id": source_task_id,
        "source_task_deep_link": f"codex://threads/{source_task_id}",
        "destination_task_id": destination_task_id,
        "destination_task_deep_link": f"codex://threads/{destination_task_id}",
        "initial_shell_source_task_id": source_task_id,
        "initial_shell_destination_task_id": destination_task_id,
        "host_creation_result_task_id": destination_task_id,
        "host_creation_result_deep_link": f"codex://threads/{destination_task_id}",
        "host_process_instance_id_before": "codex-beta-process-001",
        "host_process_instance_id_after": "codex-beta-process-001",
        "app_restart_invoked": False,
        "app_restart_count": 0,
        "renderer_reload_count": 0,
        "ui_freeze_count": 0,
        "unexpected_navigation_count": 0,
        "unexpected_task_activation_count": 0,
        "background_agent_activation_count": 0,
        "unbounded_thread_hydration_count": 0,
        "collaboration_overlay_hydration_count": 0,
        "thread_hydration_mode": "BOUNDED_HANDOFF_ENVELOPE_ONLY",
        "full_thread_history_requested": False,
        "plan_projection_source": "CANONICAL_PLAN_LANE_NOT_THREAD_HISTORY",
        "collaboration_overlay_active": False,
        "observed_incidents": [],
        "live_canonical_title_task_ids": [destination_task_id],
        "title_used_as_identity": False,
        "cwd_used_as_identity": False,
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
        "destination_task_deep_link": (f"codex://threads/{destination_task_id}"),
        "canonical_title_increment_verified": True,
        "host_continuity": _host_continuity(
            source_task_id,
            destination_task_id,
        ),
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
        "host_continuity": _host_continuity(
            source_task_id,
            destination_task_id,
        ),
        "destination_resolution": {
            "schema": "evidence-lane.host-destination-resolution.v1",
            "status": "RESOLVED_UNIQUE",
            "client_thread_id": client_thread_id,
            "destination_task_id": destination_task_id,
            "destination_task_deep_link": (f"codex://threads/{destination_task_id}"),
            "live_destination_task_ids": (
                live_destination_task_ids or [destination_task_id]
            ),
            "duplicate_task_ids": ["duplicate-destination-history"],
            "archived_task_ids": ["archived-destination-history"],
        },
    }


def test_direct_source_identity_streams_tracked_diff_without_materializing(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_and_approve_pv1(service)
    expected_argv = [
        "diff",
        "--binary",
        "--no-ext-diff",
        "--full-index",
        "HEAD",
        "--",
        ".",
    ]
    expected_sha256 = "A" * 64
    expected_bytes = 1_706_641_843
    digest_calls: list[list[str]] = []
    original_run_git = session_module.run_git

    def guarded_run_git(repository, args, **kwargs):
        argv = [str(value) for value in args]
        if argv == expected_argv:
            pytest.fail("direct State Travel materialized the tracked binary diff")
        return original_run_git(repository, argv, **kwargs)

    def fake_run_git_digest(repository, args, **kwargs):
        del repository, kwargs
        digest_calls.append([str(value) for value in args])
        return expected_sha256, expected_bytes

    monkeypatch.setattr(session_module, "run_git", guarded_run_git)
    monkeypatch.setattr(session_module, "run_git_digest", fake_run_git_digest)

    identity = service.sessions._direct_state_travel_source_identity("book-faires")

    assert digest_calls == [expected_argv]
    assert identity["tracked_diff_sha256"] == expected_sha256
    assert "tracked_diff" not in identity


@pytest.mark.parametrize(
    "destination_boot_attached",
    [True],
    ids=["destination-boot-attached"],
)
@pytest.mark.parametrize(
    "entry_state",
    ["TASK_CLASSIFIED", "EXIT_BUILDING", "PVN1_CANDIDATE"],
    ids=["classified", "interrupted-unsealed-exit", "candidate-pending-hil"],
)
def test_direct_forced_same_worktree_entry_binds_fresh_task_once_without_seal(
    service,
    destination_boot_attached: bool,
    entry_state: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    active = {
        "task_id": "direct-active-row",
        "task_class": "modify_code",
        "requested_outcome": "Verify the direct same-worktree route.",
        "permitted_paths": ["plugins/**"],
        "permitted_tools": ["repository_read", "repository_write", "test"],
        "acceptance_checks": ["The exact direct entry receipt passes."],
        "stop_condition": "Stop before candidate or HIL.",
        "commit_batch_id": "task8-direct-entry",
        "dependencies": [],
    }
    final_hil = {
        "task_id": "direct-physical-final-hil",
        "task_class": "verify_result",
        "requested_outcome": "Present the physically final HIL.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["The final HIL is explicit."],
        "stop_condition": "Stop at the human HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
        "dependencies": ["direct-active-row"],
    }
    service.plan_tasks(
        "book-faires",
        tasks=[active, final_hil],
        planned_by="human-test",
        plan_id="direct-forced-entry-plan",
    )
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

    source_task_id = "11111111-1111-4111-8111-111111111111"
    donor_task_id = "22222222-2222-4222-8222-222222222222"
    destination_task_id = "33333333-3333-4333-8333-333333333333"
    session = service.sessions.load("book-faires", session_id)
    session.metadata["current_host_session_id"] = donor_task_id
    session.metadata.setdefault("host_session_history", []).extend(
        [
            {
                "host_session_id": source_task_id,
                "host": "CODEX_DESKTOP",
                "bound_at": "2026-08-16T00:00:00Z",
            },
            {
                "host_session_id": donor_task_id,
                "host": "CODEX_DESKTOP",
                "bound_at": "2026-08-16T00:01:00Z",
            },
        ]
    )
    if destination_boot_attached:
        session.metadata["current_host_session_id"] = destination_task_id
        session.metadata["host_session_history"].append(
            {
                "host_session_id": destination_task_id,
                "host": "CODEX_DESKTOP",
                "bound_at": "2026-08-16T00:02:00Z",
            }
        )
    session.metadata["execution_profile"] = _direct_profile()
    session.metadata["host_plan_window"] = {
        "schema": "evidence-lane.host-plan-window-state.v1",
        "window_task_ids": [active["task_id"], final_hil["task_id"]],
    }
    if entry_state == "EXIT_BUILDING":
        session.state = type(session.state)(entry_state)
        session.candidate_id = None
        session.metadata.pop("pending_hil", None)
        session.metadata.pop("pending_task", None)
    elif entry_state == "PVN1_CANDIDATE":
        session.state = type(session.state)(entry_state)
        session.candidate_id = "PV2_CANDIDATE__DIRECT_PRESERVATION_TEST"
        session.metadata["pending_hil"] = True
        session.metadata["pending_task"] = {
            "kind": "HIL_DECISION",
            "candidate_id": session.candidate_id,
        }
        monkeypatch.setattr(
            service.store,
            "candidate_preservation_identity",
            lambda *_args, **_kwargs: {
                "status": "PASS",
                "candidate_id": session.candidate_id,
                "manifest_sha256": "C" * 64,
                "package_sha256": "D" * 64,
                "preservation_scope": "IMMUTABLE_CANDIDATE_PACKAGE_IDENTITY",
                "current_live_root_equality_required": False,
            },
        )
    service.sessions._save(session)

    source = service.sessions._direct_state_travel_source_identity("book-faires")
    plan = service.sessions._direct_state_travel_plan_identity("book-faires")
    plugin = service.sessions._state_travel_plugin_build_identity()
    pointer = service.store.pointer("book-faires")
    pointer_sha256 = sha256_bytes(canonical_json_bytes(pointer.as_dict()))
    repository = service.store.config("book-faires").repository_path
    binding = {
        "schema": "evidence-lane.direct-forced-same-worktree-entry.v1",
        "route": "DIRECT_FORCED_SAME_WORKTREE_NEW_TASK",
        "confirmation": "DIRECT_FORCE_SAME_WORKTREE_STATE_TRAVEL",
        "request_nonce": "direct-entry-test-once-001",
        "authoritative_source": {
            "task_id": source_task_id,
            "deep_link": f"codex://threads/{source_task_id}",
        },
        "runtime_attachment_donor": {
            "task_id": donor_task_id,
            "deep_link": f"codex://threads/{donor_task_id}",
        },
        "destination": {
            "task_id": destination_task_id,
            "deep_link": f"codex://threads/{destination_task_id}",
            "title": "Codex_Evidence_Lane_plugin_statetravel_task_test",
            "project_id": "book-faires",
            "workspace_path": repository,
            "creation_kind": "FRESH_NATIVE_CODEX_LOCAL_PROJECT_TASK",
            "fresh_local_task": True,
            "fork": False,
            "continued_from_chat": False,
        },
        "sole_writer": {
            "policy": "SOLE_WRITER",
            "writer_id": destination_task_id,
            "concurrent_writer_count": 1,
        },
        "sealed_transport": {
            "prepare_called": False,
            "resume_called": False,
            "transport_envelope_created": False,
            "transport_envelope_consumed": False,
            "eligible_fresh_handoff_exists": False,
        },
        "host_context": {
            "current_task_id": destination_task_id,
            "current_task_deep_link": f"codex://threads/{destination_task_id}",
            "current_task_title": ("Codex_Evidence_Lane_plugin_statetravel_task_test"),
            "runtime_instance_attestation_mode": "SERVER_DERIVED_ATTESTATION",
            "thread_hydration_mode": "BOUNDED_AUTHORITY_AND_PLAN_SQLITE_ONLY",
            "full_thread_history_requested": False,
            "task7_chat_history_loaded_as_authority": False,
            "collaboration_overlay_active": False,
        },
        "expected": {
            "pointer": {
                "accepted_pv": pointer.accepted_pv,
                "generation": pointer.generation,
                "pointer_sha256": pointer_sha256,
            },
            "source": source,
            "prebootstrap_source": {
                **source,
                "captured_before_authorized_route_bootstrap": True,
            },
            "plan": plan,
            "plugin": plugin,
            "runtime": {
                "state": entry_state,
                "generation": pointer.generation,
                "attachment_donor_task_id": donor_task_id,
                "runtime_instance_attestation_mode": ("SERVER_DERIVED_ATTESTATION"),
                "hooks_mode": "OFF_UNTIL_REPAIRED",
            },
            "execution_profile": _direct_profile(),
        },
    }
    contract = direct_forced_same_worktree_binding_contract()
    assert contract["confirmation"] == "DIRECT_FORCE_SAME_WORKTREE_STATE_TRAVEL"
    assert contract["caller_supplied_process_id"] == "FORBIDDEN"
    preflight = preflight_direct_forced_same_worktree_binding(binding)
    assert preflight["status"] == "PASS"
    assert preflight["one_shot_route_invoked"] is False
    assert preflight["writes_performed"] is False
    normalized = normalize_direct_forced_same_worktree_binding(binding)
    assert normalized["destination"]["fresh_local_task"] is True
    assert normalized["destination"]["fork"] is False

    project_root = service.store.project_root("book-faires")
    journal_root = project_root / "direct_state_travel_entries"
    journal_before = sorted(journal_root.glob("*.json"))
    for container, forbidden_field in (
        ("host_context", "host_process_instance_id"),
        ("host_context", "runtime_instance_id"),
        ("expected.runtime", "host_process_instance_id"),
        ("expected.runtime", "runtime_instance_id"),
    ):
        forbidden_binding = copy.deepcopy(binding)
        target = (
            forbidden_binding["host_context"]
            if container == "host_context"
            else forbidden_binding["expected"]["runtime"]
        )
        target[forbidden_field] = "caller-runtime-identity-forbidden"
        with pytest.raises(EvidenceLaneError) as caller_runtime:
            preflight_direct_forced_same_worktree_binding(forbidden_binding)
        assert (
            caller_runtime.value.code
            == "DIRECT_STATE_TRAVEL_CALLER_RUNTIME_ID_FORBIDDEN"
        )
        assert sorted(journal_root.glob("*.json")) == journal_before

    if destination_boot_attached:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                service.sessions,
                "_direct_state_travel_source_identity",
                lambda project_id: pytest.fail(
                    "wrong-task rejection reached the expensive source digest"
                ),
            )
            with pytest.raises(EvidenceLaneError) as wrong_destination:
                service.direct_force_same_worktree_state_travel(
                    project_id="book-faires",
                    session_id=session_id,
                    authoritative_source_task_id=source_task_id,
                    runtime_attachment_donor_task_id=donor_task_id,
                    destination_task_id="44444444-4444-4444-8444-444444444444",
                    destination_task_title=(
                        "Codex_Evidence_Lane_plugin_statetravel_task_wrong"
                    ),
                )
        assert wrong_destination.value.code == (
            "DIRECT_STATE_TRAVEL_DESTINATION_BOOT_REQUIRED"
        )
        assert wrong_destination.value.details["required_current_route"] == (
            "session_resume"
        )
        assert wrong_destination.value.details["session_boot_allowed"] is False
        assert wrong_destination.value.details["direct_route_retry_allowed"] is False
        assert sorted(journal_root.glob("*.json")) == journal_before

        result = service.direct_force_same_worktree_state_travel(
            project_id="book-faires",
            session_id=session_id,
            authoritative_source_task_id=source_task_id,
            runtime_attachment_donor_task_id=donor_task_id,
            destination_task_id=destination_task_id,
            destination_task_title=(
                "Codex_Evidence_Lane_plugin_statetravel_task_test"
            ),
        )
        assert result["forced_state_travel"]["binding_mode"] == (
            "SERVER_DERIVED_ATOMIC"
        )
        assert result["forced_state_travel"]["caller_supplied_nonce"] is False
        assert "binding_preflight" not in result
        assert "request_nonce" not in result["forced_state_travel"]
    receipt = service.sessions.load("book-faires", session_id).metadata[
        "direct_forced_same_worktree_entry"
    ]
    assert receipt["status"] == "PASS"
    assert receipt["host_task_binding"]["destination"]["task_id"] == (
        destination_task_id
    )
    assert (
        receipt["host_task_binding"]["destination_boot_attached_before_direct_binding"]
        is destination_boot_attached
    )
    assert receipt["sealed_transport"]["prepare_called"] is False
    assert receipt["sealed_transport"]["resume_called"] is False
    assert receipt["no_mutation_flags"]["candidate_created"] is False
    assert receipt["no_mutation_flags"]["candidate_cleared"] is False
    assert receipt["no_mutation_flags"]["candidate_rebuilt"] is False
    if entry_state == "PVN1_CANDIDATE":
        preserved = receipt["preserved_candidate_hil_state"]
        assert preserved["candidate_id"] == session.candidate_id
        assert preserved["pending_hil"] is True
        assert preserved["candidate_cleared"] is False
        assert preserved["candidate_rebuilt"] is False
        assert receipt["no_mutation_flags"]["existing_candidate_preserved"] is True
        assert receipt["no_mutation_flags"]["pending_hil_preserved"] is True
    assert receipt["no_mutation_flags"]["pointer_moved"] is False
    runtime_attestation = receipt["runtime_plugin_profile_proof"][
        "runtime_instance_attestation"
    ]
    assert runtime_attestation["status"] == "PASS"
    assert runtime_attestation["caller_supplied"] is False
    assert runtime_attestation["process_id_exposed"] is False
    assert runtime_attestation["runtime_instance_id"].startswith("runtime_")
    assert "host_process_instance_id" not in receipt["runtime_plugin_profile_proof"]
    assert receipt["plan_task_proof"]["active_batch_row_start"] == 1
    assert receipt["plan_task_proof"]["host_window_row_start"] == 1
    orchestration = receipt["destination_orchestration"]
    assert orchestration["status"] == "PASS"
    assert [row["phase"] for row in orchestration["ordered_phases"]] == [
        1,
        2,
        3,
        4,
        5,
    ]
    assert orchestration["user_gates"] == [
        "PASTE_RETURNED_PROMPT_WITH_NATIVE_PLAN_SELECTED",
        "CLICK_VISIBLE_IMPLEMENT_THIS_PLAN",
    ]
    prompt_0 = orchestration["prompt_0_entry_contract"]
    assert prompt_0["skeleton_id"] == "STATE_TRAVEL_PROMPT_0_BASE_SKELETON_V1"
    assert prompt_0["visible_order"] == [
        "NATIVE_DESTINATION_VERIFICATION_RECEIPT",
        "STATE_TRAVEL_HANDOFF_RECEIPT",
        "SINGLE_EVI_PLAN_PROMPT_FENCED_TEXT_LAST",
        "USER_VISIBLE_IMPLEMENT_THIS_PLAN_CONTROL",
        "ATOMIC_CARRIED_GOAL_RESUME_AND_FIXED_STEP_CHANGES_RELOCK",
    ]
    assert prompt_0["native_destination_receipt_must_be_first_visible_block"] is True
    assert prompt_0["single_evi_plan_prompt_must_be_last"] is True
    assert prompt_0["second_evi_plan_projection_phase_allowed"] is False
    assert prompt_0["obsolete_prepare_resume_history_is_current_runtime_law"] is False
    whole = orchestration["whole_plan_reprojection"]
    assert whole["serialized_task_rows"] == []
    assert whole["serialized_fixed_batch"] is False
    assert "Implement this plan" in whole["paste_prompt"]
    assert orchestration["post_acceptance_step_projection"][
        "fallback_projector_enabled"
    ] is False
    assert orchestration["whole_plan_reprojection"]["evi_plan_invocation_count"] == 1
    assert orchestration["whole_plan_reprojection"][
        "second_evi_plan_invocation_allowed"
    ] is False
    assert orchestration["source_task_option2_closeout"][
        "source_goal_completion_before_destination_goal_resume_allowed"
    ] is False
    phase5 = verify_direct_destination_plan_acceptance(
        orchestration,
        acceptance={
            "schema": "evidence-lane.native-plan-implement-acceptance.v1",
            "destination_task_id": destination_task_id,
            "destination_task_deep_link": f"codex://threads/{destination_task_id}",
            "visible_plan_title": orchestration["destination"]["title"],
            "native_plan_mode_selected": True,
            "plan_prompt_sha256": whole["paste_prompt_sha256"],
            "visible_implement_this_plan_control": True,
            "implement_this_plan_clicked_by_user": True,
            "implement_this_plan_event_id": "host-implement-click-001",
            "prompt_auto_pasted_by_plugin": False,
            "control_auto_accepted_by_plugin": False,
        },
        live_plan=receipt["plan_task_proof"],
        goal_observation={
            "schema": "evidence-lane.carried-goal-observation.v1",
            "goal_count": 1,
            "competing_goal_count": 0,
            "goal_id": "goal-carried-direct-001",
            "status": "RUNNING",
            "active_task_id": receipt["plan_task_proof"]["active_task_id"],
            "goal_projection_sha256": receipt["plan_task_proof"][
                "goal_projection_sha256"
            ],
            "disposition": "RESUMED_EXISTING",
        },
    )
    assert phase5["status"] == "PASS"
    assert phase5["source_work_may_resume"] is True
    assert phase5["plan_acceptance_is_evidence_lane_hil"] is False
    assert phase5["second_evi_plan_invocation"] is False
    assert phase5["goal_resume_precedes_step_projection"] is True
    metrics_body = {
        "schema": "evidence-lane.rich-goal-completion-metrics.v1",
        "status": "PASS",
        "route": "build_rich_goal_completion_metrics_receipt",
        "binding": {"host_task_id": source_task_id},
        "reset_aware_epoch_accounting": {
            "status": "PASS",
            "final_minus_initial_used": False,
        },
    }
    metrics = {
        **metrics_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(metrics_body)),
    }
    completion = {
        "schema": "evidence-lane.goal-completion-authorization.v1",
        "status": "AUTHORIZED_BY_EXACT_HUMAN_COMMAND",
        "current_task_id": source_task_id,
        "disposition": "COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
        "current_task_goal_completed": True,
        "successor_goal_required": True,
    }
    steer = {
        "schema": "evidence-lane.state-travel-consolidated-steer.v1",
        "status": "PASS",
        "steer_id": "source-option2-consolidated-steer-001",
        "source_task_id": source_task_id,
        "destination_task_id": destination_task_id,
        "destination_goal_resume_receipt_sha256": phase5["receipt_sha256"],
        "goal_metrics_receipt_sha256": metrics["receipt_sha256"],
        "send_count": 1,
        "sent_after_destination_goal_resume": True,
        "sent_before_destination_goal_resume": False,
        "includes_goal_metrics_log_steer": True,
    }
    source_closeout = verify_direct_source_option2_closeout(
        orchestration,
        destination_phase5=phase5,
        goal_metrics=metrics,
        goal_completion=completion,
        consolidated_steer=steer,
    )
    assert source_closeout["status"] == "PASS"
    assert source_closeout["source_goal_mark_achieved"] is True
    assert source_closeout["destination_remains_sole_writer"] is True
    with pytest.raises(EvidenceLaneError) as automated_gate:
        verify_direct_destination_plan_acceptance(
            orchestration,
            acceptance={
                "schema": "evidence-lane.native-plan-implement-acceptance.v1",
                "destination_task_id": destination_task_id,
                "destination_task_deep_link": (
                    f"codex://threads/{destination_task_id}"
                ),
                "visible_plan_title": orchestration["destination"]["title"],
                "native_plan_mode_selected": True,
                "plan_prompt_sha256": whole["paste_prompt_sha256"],
                "visible_implement_this_plan_control": True,
                "implement_this_plan_clicked_by_user": True,
                "implement_this_plan_event_id": "automated-click-is-forbidden",
                "prompt_auto_pasted_by_plugin": True,
                "control_auto_accepted_by_plugin": True,
            },
            live_plan=receipt["plan_task_proof"],
            goal_observation={},
        )
    assert (
        automated_gate.value.code
        == "DIRECT_STATE_TRAVEL_IMPLEMENT_ACCEPTANCE_MISMATCH"
    )
    assert service.store.pointer("book-faires").as_dict() == pointer.as_dict()
    rebound = service.sessions.load("book-faires", session_id)
    assert rebound.metadata["current_host_session_id"] == destination_task_id
    task_binding = rebound.metadata["active_contract_rebind_receipt"]
    assert task_binding["schema"] == "evidence-lane.active-contract-session-rebind.v1"
    assert task_binding["status"] == "PASS"
    assert task_binding["host_task_id"] == destination_task_id
    assert task_binding["active_plan_task_id"] == active["task_id"]
    assert task_binding["task_binding_contract"]["reentry_target"] == (
        destination_task_id
    )
    task_binding_body = {
        key: value for key, value in task_binding.items() if key != "receipt_sha256"
    }
    assert task_binding["receipt_sha256"] == sha256_bytes(
        canonical_json_bytes(task_binding_body)
    )
    assert receipt["calling_task_binding_authority"]["receipt_sha256"] == (
        task_binding["receipt_sha256"]
    )
    shared = seal_or_refresh_shared_task_binding(
        service.store.root,
        project_id="book-faires",
        evidence_session_id=session_id,
        task_id=destination_task_id,
        surface=package_surface_inventory(),
        bound_by="DIRECT_ENTRY_CHECKPOINT_TEST",
    )
    assert shared["task_id"] == destination_task_id
    assert shared["active_contract_rebind_receipt_sha256"] == task_binding[
        "receipt_sha256"
    ]
    direct_authority = _verified_direct_entry_authority(rebound.as_dict())
    assert direct_authority is not None
    conflicting_binding = rebound.as_dict()
    conflicting_binding["metadata"]["active_contract_rebind_receipt"] = {
        "host_task_id": donor_task_id,
        "receipt_sha256": "A" * 64,
    }
    with pytest.raises(TurnControlError) as conflicting_task_binding:
        _verified_direct_entry_authority(conflicting_binding)
    assert conflicting_task_binding.value.code == (
        "TURN_CONTROL_DIRECT_ENTRY_TASK_BINDING_AUTHORITY_MISMATCH"
    )

    checkpoint_candidate_boundary = (
        {
            "state": "PENDING_CANDIDATE_PRESERVED",
            "candidate_id": rebound.candidate_id,
        }
        if entry_state == "PVN1_CANDIDATE"
        else {"state": "NO_PENDING_CANDIDATE", "candidate_id": None}
    )
    continuity = _direct_entry_checkpoint_continuity(
        project_root=service.store.project_root("book-faires"),
        project={
            "repository_path": service.store.config("book-faires").repository_path
        },
        session=rebound.as_dict(),
        binding={"candidate_boundary": checkpoint_candidate_boundary},
        direct_entry=direct_authority,
    )
    assert continuity["pointer"]["candidate_absent"] is (
        entry_state != "PVN1_CANDIDATE"
    )
    assert continuity["pointer"]["pending_hil"] is (
        entry_state == "PVN1_CANDIDATE"
    )
    assert continuity["pointer"]["candidate_preserved"] is (
        entry_state == "PVN1_CANDIDATE"
    )
    assert continuity["source"]["branch"] == source["branch"]
    assert continuity["source"]["commit_sha"] == source["commit_sha"]
    assert continuity["source"]["tree_sha"] == source["tree_sha"]

    pointer_drift_session = rebound.as_dict()
    pointer_drift_session["accepted_pointer_generation"] += 1
    with pytest.raises(TurnControlError) as pointer_drift:
        _direct_entry_checkpoint_continuity(
            project_root=service.store.project_root("book-faires"),
            project={
                "repository_path": service.store.config(
                    "book-faires"
                ).repository_path
            },
            session=pointer_drift_session,
            binding={"candidate_boundary": checkpoint_candidate_boundary},
            direct_entry=direct_authority,
        )
    assert pointer_drift.value.code == "CODEX_DIRECT_ENTRY_POINTER_DRIFT"

    with pytest.raises(TurnControlError) as workspace_drift:
        _direct_entry_checkpoint_continuity(
            project_root=service.store.project_root("book-faires"),
            project={"repository_path": service.store.root},
            session=rebound.as_dict(),
            binding={"candidate_boundary": checkpoint_candidate_boundary},
            direct_entry=direct_authority,
        )
    assert workspace_drift.value.code == "CODEX_DIRECT_ENTRY_WORKSPACE_MISMATCH"

    with monkeypatch.context() as patcher:
        patcher.setattr(
            codex_turn_control,
            "calculate_worktree_change_identity",
            lambda repository_path: {
                "branch": "wrong-branch",
                "head": source["commit_sha"],
                "tree": source["tree_sha"],
            },
        )
        with pytest.raises(TurnControlError) as source_drift:
            _direct_entry_checkpoint_continuity(
                project_root=service.store.project_root("book-faires"),
                project={
                    "repository_path": service.store.config(
                        "book-faires"
                    ).repository_path
                },
                session=rebound.as_dict(),
                binding={"candidate_boundary": checkpoint_candidate_boundary},
                direct_entry=direct_authority,
            )
    assert source_drift.value.code == "CODEX_DIRECT_ENTRY_SOURCE_BASE_DRIFT"

    candidate_session = rebound.as_dict()
    candidate_session["candidate_id"] = "candidate-forbidden-at-checkpoint"
    candidate_binding = {
        "candidate_boundary": {
            "state": "PENDING_CANDIDATE",
            "candidate_id": "candidate-forbidden-at-checkpoint",
        }
    }
    with pytest.raises(TurnControlError) as candidate_present:
        _direct_entry_checkpoint_continuity(
            project_root=service.store.project_root("book-faires"),
            project={
                "repository_path": service.store.config(
                    "book-faires"
                ).repository_path
            },
            session=candidate_session,
            binding=candidate_binding,
            direct_entry=direct_authority,
        )
    assert candidate_present.value.code == (
        "CODEX_DIRECT_ENTRY_CANDIDATE_PRESERVATION_MISMATCH"
    )
    with pytest.raises(EvidenceLaneError) as wrong_task:
        seal_or_refresh_shared_task_binding(
            service.store.root,
            project_id="book-faires",
            evidence_session_id=session_id,
            task_id=donor_task_id,
            surface=package_surface_inventory(),
            bound_by="WRONG_TASK_MUST_FAIL",
        )
    assert wrong_task.value.code == "SHARED_TASK_BINDING_REBIND_AUTHORITY_MISMATCH"

    if destination_boot_attached:
        with monkeypatch.context() as patcher:
            patcher.setattr(
                service.sessions,
                "_direct_state_travel_source_identity",
                lambda project_id: pytest.fail(
                    "replay rejection reached the expensive source digest"
                ),
            )
            with pytest.raises(EvidenceLaneError) as replay:
                service.direct_force_same_worktree_state_travel(
                    project_id="book-faires",
                    session_id=session_id,
                    authoritative_source_task_id=source_task_id,
                    runtime_attachment_donor_task_id=donor_task_id,
                    destination_task_id=destination_task_id,
                    destination_task_title=(
                        "Codex_Evidence_Lane_plugin_statetravel_task_test"
                    ),
                )
    else:
        with pytest.raises(EvidenceLaneError) as replay:
            service._direct_force_same_worktree_state_travel_with_binding(
                project_id="book-faires",
                session_id=session_id,
                binding=binding,
            )
    assert replay.value.code == "DIRECT_STATE_TRAVEL_REPLAY_FORBIDDEN"


def test_server_runtime_attestation_rotates_on_restart_without_task_relabel(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    session = service.sessions.load("book-faires", session_id)
    session.metadata["current_host_session_id"] = "exact-destination-task"
    service.sessions._save(session)
    before = dict(service.sessions._runtime_instance_attestation)

    restarted = type(service)(data_root=service.store.root)
    after = dict(restarted.sessions._runtime_instance_attestation)
    reloaded = restarted.sessions.load("book-faires", session_id)

    assert before["runtime_instance_id"] != after["runtime_instance_id"]
    assert before["receipt_sha256"] != after["receipt_sha256"]
    assert after["caller_supplied"] is False
    assert after["process_id_exposed"] is False
    assert reloaded.metadata["current_host_session_id"] == "exact-destination-task"


def test_direct_plan_identity_uses_fixed_batch_instead_of_sliding_window(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = []
    for number in range(1, 12):
        panel_role = "STANDARD"
        if number == 10:
            panel_role = "HIL_GATE"
        elif number == 11:
            panel_role = "PHYSICALLY_FINAL_HIL"
        rows.append(
            {
                "number": number,
                "task_id": f"direct-fixed-row-{number:02d}",
                "status": (
                    "completed"
                    if number < 5
                    else "in_progress"
                    if number == 5
                    else "pending"
                ),
                "panel_role": panel_role,
                "commit_batch_id": f"commit-batch-{number:02d}",
            }
        )
    backlog = {
        "goal_projection": {
            "projection_sha256": "A" * 64,
            "row_start": 1,
            "row_end": 11,
            "task_count": 11,
            "rows": rows,
        },
        "history_projection": {"projection_sha256": "B" * 64},
        "canonical_plan_projection": {"projection_sha256": "C" * 64},
    }
    monkeypatch.setattr(
        service.store,
        "backlog_status",
        lambda project_id: backlog,
    )
    monkeypatch.setattr(
        service.store,
        "persisted_host_plan_window_task_ids",
        lambda project_id: [
            f"direct-fixed-row-{number:02d}" for number in range(1, 10)
        ],
    )
    monkeypatch.setattr(
        service.sessions,
        "_state_travel_plan_snapshot",
        lambda project_id: {"snapshot_sha256": "D" * 64},
    )

    identity = service.sessions._direct_state_travel_plan_identity("book-faires")

    assert identity["active_row"] == 5
    assert identity["active_batch_id"] == "FIXED_HOST_BATCH_R1-R9"
    assert identity["active_batch_row_start"] == 1
    assert identity["active_batch_row_end"] == 9
    assert identity["host_window_row_start"] == 1
    assert identity["host_window_row_end"] == 9
    assert identity["next_hil_row"] == 10
    assert identity["physically_final_hil_row"] == 11
