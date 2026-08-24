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
    [False, True],
    ids=["donor-current", "destination-boot-attached"],
)
def test_direct_forced_same_worktree_entry_binds_fresh_task_once_without_seal(
    service,
    destination_boot_attached: bool,
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
                "state": "TASK_CLASSIFIED",
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
        with monkeypatch.context() as patcher:
            patcher.setattr(
                service.flash_authority,
                "ensure_flashed",
                lambda: pytest.fail("malformed preflight reached Flash"),
            )
            with pytest.raises(EvidenceLaneError) as caller_runtime:
                service._direct_force_same_worktree_state_travel_with_binding(
                    project_id="book-faires",
                    session_id=session_id,
                    binding=forbidden_binding,
                )
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
    else:
        result = service._direct_force_same_worktree_state_travel_with_binding(
            project_id="book-faires",
            session_id=session_id,
            binding=binding,
        )
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
    whole = orchestration["whole_plan_reprojection"]
    assert whole["serialized_task_rows"] == []
    assert whole["serialized_fixed_batch"] is False
    assert "Implement this plan" in whole["paste_prompt"]
    assert orchestration["post_acceptance_step_projection"][
        "fallback_projector_enabled"
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
    recovery = rebound.metadata["active_contract_rebind_receipt"]
    assert recovery["schema"] == "evidence-lane.active-contract-session-rebind.v1"
    assert recovery["status"] == "PASS"
    assert recovery["host_task_id"] == destination_task_id
    assert recovery["active_plan_task_id"] == active["task_id"]
    assert recovery["recovery_binding_contract"]["reentry_target"] == (
        destination_task_id
    )
    recovery_body = {
        key: value for key, value in recovery.items() if key != "receipt_sha256"
    }
    assert recovery["receipt_sha256"] == sha256_bytes(
        canonical_json_bytes(recovery_body)
    )
    assert receipt["calling_task_recovery_authority"]["receipt_sha256"] == (
        recovery["receipt_sha256"]
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
    assert shared["active_contract_rebind_receipt_sha256"] == recovery[
        "receipt_sha256"
    ]
    legacy_session = rebound.as_dict()
    legacy_receipt = copy.deepcopy(receipt)
    legacy_receipt.pop("calling_task_recovery_authority")
    legacy_body = {
        key: value
        for key, value in legacy_receipt.items()
        if key != "receipt_sha256"
    }
    legacy_receipt["receipt_sha256"] = sha256_bytes(
        canonical_json_bytes(legacy_body)
    )
    legacy_session["metadata"]["direct_forced_same_worktree_entry"] = legacy_receipt
    legacy_authority = _verified_direct_entry_authority(legacy_session)
    assert legacy_authority is not None
    assert legacy_authority["destination_task_id"] == destination_task_id
    assert legacy_authority["recovery_authority_source"] == (
        "CURRENT_DIRECT_ENTRY_RECOVERY_RECEIPT"
    )
    assert legacy_authority["recovery_authority_receipt_sha256"] == (
        recovery["receipt_sha256"]
    )
    conflicting_legacy = copy.deepcopy(legacy_session)
    conflicting_legacy["metadata"]["active_contract_rebind_receipt"] = {
        "host_task_id": donor_task_id,
        "receipt_sha256": "A" * 64,
    }
    with pytest.raises(TurnControlError) as conflicting_rebind:
        _verified_direct_entry_authority(conflicting_legacy)
    assert conflicting_rebind.value.code == (
        "TURN_CONTROL_DIRECT_ENTRY_RECOVERY_AUTHORITY_MISMATCH"
    )

    continuity = _direct_entry_checkpoint_continuity(
        project_root=service.store.project_root("book-faires"),
        project={
            "repository_path": service.store.config("book-faires").repository_path
        },
        session=rebound.as_dict(),
        binding={
            "candidate_boundary": {
                "state": "NO_PENDING_CANDIDATE",
                "candidate_id": None,
            }
        },
        direct_entry=legacy_authority,
    )
    assert continuity["pointer"]["candidate_absent"] is True
    assert continuity["pointer"]["pending_hil"] is False
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
            binding={
                "candidate_boundary": {
                    "state": "NO_PENDING_CANDIDATE",
                    "candidate_id": None,
                }
            },
            direct_entry=legacy_authority,
        )
    assert pointer_drift.value.code == "CODEX_DIRECT_ENTRY_POINTER_DRIFT"

    with pytest.raises(TurnControlError) as workspace_drift:
        _direct_entry_checkpoint_continuity(
            project_root=service.store.project_root("book-faires"),
            project={"repository_path": service.store.root},
            session=rebound.as_dict(),
            binding={
                "candidate_boundary": {
                    "state": "NO_PENDING_CANDIDATE",
                    "candidate_id": None,
                }
            },
            direct_entry=legacy_authority,
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
                binding={
                    "candidate_boundary": {
                        "state": "NO_PENDING_CANDIDATE",
                        "candidate_id": None,
                    }
                },
                direct_entry=legacy_authority,
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
            direct_entry=legacy_authority,
        )
    assert candidate_present.value.code == (
        "CODEX_DIRECT_ENTRY_CANDIDATE_OR_HIL_PRESENT"
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
        "entry_recovery_subagents": ("READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"),
        "later_subagents": "EXPLICIT_USER_COMMAND_ONLY",
        "alternate_checkout_writer": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "background_mutation": "FORBIDDEN_UNLESS_EXPLICIT_USER_CHANGE",
        "browser_profile": (
            "ONE_USER_SELECTED_PROFILE_ONLY_UNLESS_EXPLICIT_USER_CHANGE"
        ),
    }
    execution_writer_boundary = handoff["resume_contract"]["execution_writer_boundary"]
    assert execution_writer_boundary == {
        "schema": "evidence-lane.execution-writer-boundary.v1",
        "project_policy": "ONE_GOVERNED_PROJECT",
        "writer_policy": "ONE_LIVE_WRITER",
        "execution_order": "LINEAR",
        "verification_order": "EVIDENCE_FIRST",
        "execution_profile": profile,
        "execution_profile_change_authority": "EXPLICIT_USER_CHANGE_ONLY",
        "entry_recovery_agents": ("READ_ONLY_ONLY_AT_GENUINE_STATE_TRAVEL_ENTRY"),
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
        "goal_completion_allowed_when": ("HUMAN_EXPLICIT_GOAL_COMPLETION_DISPOSITION"),
        "goal_completion_authority": "HUMAN_ONLY",
        "hil_may_complete_goal": False,
        "goal_completion_modes": [
            "COMPLETE_THIS_TASK_AND_STATE_TRAVEL",
            "COMPLETE_FULLY",
        ],
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
    assert orchestration["destination_creation_action"] == ("CONTINUE_IN_NEW_CHAT")
    assert orchestration["destination_creation_exactly_once"] is True
    assert orchestration["destination_creation_user_click_required"] is False
    assert orchestration["app_restart_or_renderer_reload_allowed"] is False
    assert orchestration["full_thread_history_hydration_allowed"] is False
    assert orchestration["collaboration_overlay_hydration_allowed"] is False
    assert orchestration["destination_thread_hydration_mode"] == (
        "BOUNDED_HANDOFF_ENVELOPE_ONLY"
    )
    assert orchestration["host_plan_projection_source"] == (
        "CANONICAL_PLAN_LANE_NOT_THREAD_HISTORY"
    )
    assert orchestration["unexpected_task_or_agent_activation_allowed"] is False
    assert orchestration["host_continuity_failure_retry_allowed"] is False
    assert (
        orchestration["host_continuity_required_proof"][
            "exact_initial_shell_source_and_destination_task_ids"
        ]
        is True
    )
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
    assert source_binding["plugin_build"]["plugin_version"].startswith("3.0.0+codex.")
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
            "APP_RENDERER_RELOAD",
            "HOST_REACT_ROOT_RERENDER",
            "THREAD_HYDRATION_OVERFLOW",
            "COLLABORATION_OVERLAY_CONFLICT",
            "TASK_PANEL_LOSS",
            "CHANGES_SURFACE_LOSS",
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
        "visible_row_numbering": ("DYNAMIC_ASCENDING_CURRENT_EXECUTION_PROJECTION"),
        "stable_identity_field": "task_id",
        "renumber_after_insert_or_non_executable_transition": True,
        "active_row": 2,
        "non_empty_task_list_requires_exactly_one_in_progress": True,
        "preserve_order_and_row_count": True,
        "preserve_completed_and_pending_descriptions_unabridged": True,
        "preserve_row_task_name_class_group_batch_dependencies_git_stage": True,
        "visible_label_contract": handoff["resume_contract"]["panel_reactivation"][
            "visible_label_contract"
        ],
        "visible_through_pause_and_hil": True,
        "native_host_surfaces": [
            "CODEX_RIGHT_SIDE_PLAN",
            "CODEX_RIGHT_SIDE_CHANGES",
        ],
        "native_plan_activation_action": "update_plan",
        "changes_surface_binding": "EXACT_TASK_UUID_AND_WORKTREE",
        "surface_drop_before_goal_completion": (
            "HOST_CONTINUITY_FAILURE_THEN_REHYDRATE_BEFORE_WORK"
        ),
        "canonical_rehydration_source": ("PLAN_LANE_BACKLOG_NOT_THREAD_HISTORY"),
        "full_thread_history_hydration_allowed": False,
        "collaboration_overlay_hydration_allowed_during_recovery": False,
        "recovery_concurrency": "ONE_ACTIVE_TASK_ZERO_SUBAGENTS",
        "renderer_reset_effect": ("FAIL_CLOSED_THEN_REPROJECT_EXACTLY_ONCE_PER_EVENT"),
        "host_owned_surface_guarantee_claimed": False,
        "goal_completion_authority": "HUMAN_ONLY",
        "drop_allowed_when": (
            "HUMAN_MARKS_GOAL_COMPLETE_OR_EXPLICIT_TASK_STATE_TRAVEL_HANDOFF_PASSES"
        ),
    }
    assert handoff["next_action_contract"]["task_panel_reactivation"] == (
        panel_reactivation
    )
    assert handoff["resume_contract"]["task_panel_persistent_until"] == (
        "HUMAN_MARKS_GOAL_COMPLETE_OR_EXPLICIT_TASK_STATE_TRAVEL_HANDOFF_PASSES"
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
    assert (
        destination_binding["host_continuity"]["host_process_continuity_proven"] is True
    )
    assert destination_binding["host_continuity"]["app_restart_count"] == 0
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
    assert wrong_destination.value.code == ("STATE_TRAVEL_REPLAY_DESTINATION_MISMATCH")
    assert service.store.pointer("book-faires").as_dict() == pointer_before_replay


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("initial_shell_source_task_id", "wrong-source-task"),
        ("initial_shell_destination_task_id", "wrong-destination-task"),
        ("host_process_instance_id_after", "restarted-codex-process"),
        ("app_restart_invoked", True),
        ("app_restart_count", 1),
        ("renderer_reload_count", 1),
        ("ui_freeze_count", 1),
        ("unexpected_navigation_count", 1),
        ("unexpected_task_activation_count", 1),
        ("background_agent_activation_count", 1),
        ("unbounded_thread_hydration_count", 1),
        ("collaboration_overlay_hydration_count", 1),
        ("thread_hydration_mode", "FULL_THREAD_HISTORY"),
        ("full_thread_history_requested", True),
        ("plan_projection_source", "THREAD_SCROLLBACK"),
        ("collaboration_overlay_active", True),
        (
            "live_canonical_title_task_ids",
            ["continuity-destination-task", "duplicate-title-task"],
        ),
    ],
)
def test_state_travel_host_continuity_incident_fails_before_consumption(
    service,
    field: str,
    bad_value: object,
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
        host_session_id="continuity-origin-task",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    handoff = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={
            "task_list": [
                {
                    "number": 1,
                    "step": "Resume without restarting the host",
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
        "continuity-origin-task",
        "continuity-destination-task",
    )
    continuity = dict(creation["host_continuity"])
    continuity[field] = bad_value
    creation["host_continuity"] = continuity

    with pytest.raises(EvidenceLaneError) as failure:
        service.resume_state_travel(
            project_id="book-faires",
            session_id=session_id,
            handoff_id=handoff["handoff_id"],
            host="CODEX_DESKTOP",
            host_session_id="continuity-destination-task",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={
                "execution_profile": profile,
                "state_travel_destination_creation": creation,
            },
        )

    assert failure.value.code == "STATE_TRAVEL_HOST_CONTINUITY_FAILURE"
    assert failure.value.details["retry_allowed"] is False
    assert failure.value.details["handoff_consumption_allowed"] is False
    current = service.sessions.load("book-faires", session_id)
    assert current.metadata["current_host_session_id"] == "continuity-origin-task"
    assert current.metadata["state_travel"]["status"] == "PREPARED"


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
    assert resolution["live_destination_task_ids"] == ["resolved-real-destination-task"]
    assert resolution["duplicate_task_ids"] == ["duplicate-destination-history"]
    assert resolution["archived_task_ids"] == ["archived-destination-history"]
    assert (
        resumed["state_travel"]["resume_consumption_receipt"]["resume_invocation_count"]
        == 1
    )

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
    assert (
        replay["state_travel"]["resume_consumption_receipt"]["resume_invocation_count"]
        == 1
    )

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
    assert ambiguous.value.code == ("STATE_TRAVEL_DESTINATION_CLIENT_THREAD_AMBIGUOUS")


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
        row for row in results if row["status"] == "ALREADY_CONSUMED_NO_REBIND"
    )
    consumption = consumed["state_travel"]["resume_consumption_receipt"]
    assert consumption["resume_invocation_count"] == 1
    assert (
        replay["replay_incident"]["original_consumption_receipt_sha256"]
        == consumption["receipt_sha256"]
    )
    assert replay["replay_incident"]["resume_invoked"] is False
    assert not (
        service.store.project_root("book-faires") / ".state-travel-resume.lock"
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
    assert persisted.metadata["state_travel"]["handoff_id"] == replacement["handoff_id"]
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


def test_state_travel_explicitly_supersedes_orphaned_stale_host_handoff(
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
        host_session_id="stale-origin-task-4",
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

    current = service.sessions.load("book-faires", session_id)
    current.metadata["current_host_session_id"] = "replacement-task-6"
    service.sessions._save(current)
    corrected_rows = [
        {**task_rows[0], "step": "Keep exact work after stale Task4"},
        task_rows[1],
    ]
    base_contract = {
        "task_list": corrected_rows,
        "resume_step": 1,
        "additive_deltas": [
            {
                "delta_id": "ORPHAN_CORRECTION_001",
                "text": "Replace only the stale prepared receipt.",
                "linked_step": 1,
            }
        ],
        "execution_profile": profile,
        "supersede_prepared_handoff_id": first["handoff_id"],
        "supersede_prepared_reason": "EXPLICIT_USER_CORRECTION",
    }

    with pytest.raises(EvidenceLaneError) as missing_orphan_authority:
        service.prepare_state_travel(
            "book-faires",
            session_id,
            resume_contract=base_contract,
        )
    assert missing_orphan_authority.value.code == (
        "STATE_TRAVEL_PREPARED_ORPHAN_CORRECTION_INVALID"
    )

    with pytest.raises(EvidenceLaneError) as wrong_old_receipt:
        service.prepare_state_travel(
            "book-faires",
            session_id,
            resume_contract={
                **base_contract,
                "supersede_prepared_handoff_sha256": "0" * 64,
                "supersede_prepared_origin_host_session_id": ("stale-origin-task-4"),
                "supersede_prepared_scope": "ORPHANED_STALE_HOST_TASK",
                "supersede_prepared_confirmation": (
                    "SUPERSEDE_ORPHANED_PREPARED_HANDOFF"
                ),
            },
        )
    assert wrong_old_receipt.value.code == (
        "STATE_TRAVEL_PREPARED_ORPHAN_CORRECTION_INVALID"
    )

    replacement_result = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={
            **base_contract,
            "supersede_prepared_handoff_sha256": first["handoff_sha256"],
            "supersede_prepared_origin_host_session_id": "stale-origin-task-4",
            "supersede_prepared_scope": "ORPHANED_STALE_HOST_TASK",
            "supersede_prepared_confirmation": ("SUPERSEDE_ORPHANED_PREPARED_HANDOFF"),
        },
    )
    replacement = replacement_result["state_travel"]
    assert replacement["handoff_id"] != first["handoff_id"]
    disposition = replacement["supersedes_prepared_handoff"]
    assert disposition["status"] == ("SUPERSEDED_BY_EXPLICIT_ORPHAN_USER_CORRECTION")
    assert disposition["handoff_id"] == first["handoff_id"]
    assert disposition["origin_host_session_id"] == "stale-origin-task-4"
    assert disposition["replacement_host_session_id"] == "replacement-task-6"
    assert disposition["orphaned_stale_host_task"] is True
    assert disposition["state_travel_consumed"] is False
    assert disposition["pointer_moved"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    persisted = service.sessions.load("book-faires", session_id)
    assert persisted.metadata["state_travel_history"][-1] == first
    assert persisted.metadata["state_travel"]["handoff_id"] == replacement["handoff_id"]
    assert replacement_result["supersession_event"]["event_type"] == (
        "pv.state_travel.superseded_orphan_user_correction"
    )


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
    assert prepared["task_list"][-1]["panel_role"] == ("PHYSICALLY_FINAL_HIL")
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
    canonical_rows = service.task_backlog("book-faires")["goal_projection"]["rows"]

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
