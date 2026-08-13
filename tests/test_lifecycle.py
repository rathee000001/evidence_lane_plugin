from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError

from .conftest import boot_local, build_and_approve_pv1, git


def _prepare_single_task_hil_correction(
    service,
    source_repository: Path,
) -> tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]:
    session_id, _ = build_and_approve_pv1(service)
    completed_task = {
        "task_id": "single-task-before-hil-correction",
        "task_class": "fix_bug",
        "requested_outcome": "Add the first governed README correction.",
        "permitted_paths": ["README.md"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The first correction is candidate-sealed."],
        "stop_condition": "Stop at the unaccepted PV2 HIL.",
    }
    replacement = {
        "task_id": "first-queued-after-hil-correction",
        "task_class": "fix_bug",
        "requested_outcome": "Apply the exact HIL correction and reseal PV2.",
        "permitted_paths": ["README.md"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The exact HIL correction is candidate-sealed."],
        "stop_condition": "Stop at a fresh unaccepted PV2 HIL.",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[completed_task, replacement],
        planned_by="human-test",
        plan_id="single-task-hil-correction-plan",
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=completed_task["task_class"],
        requested_outcome=completed_task["requested_outcome"],
        permitted_paths=completed_task["permitted_paths"],
        permitted_tools=completed_task["permitted_tools"],
        acceptance_checks=completed_task["acceptance_checks"],
        stop_condition=completed_task["stop_condition"],
        backlog_task_id=completed_task["task_id"],
    )
    readme = source_repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nFirst governed correction.\n",
        encoding="utf-8",
    )
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "README.md"},
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    refreshed = service.refresh("book-faires", session_id)
    correction_delta = "Repair only the exact governed README correction."
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE_WITH_DELTA",
        decided_by="human-test",
        correction_delta=correction_delta,
        decision_id="decision_single_task_hil_correction",
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome=correction_delta,
        permitted_paths=["README.md"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["The exact correction remains bounded."],
        stop_condition="Continue only through the first queued Plan row.",
    )
    return session_id, completed_task, replacement, {
        "candidate": refreshed["candidate"],
        "decision": decision["decision"],
    }


def test_interrupted_exit_retries_only_without_a_sealed_candidate(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Seal the verified source after an interrupted build.",
        permitted_paths=[],
        permitted_tools=["repository_read", "test"],
        acceptance_checks=["source remains unchanged"],
        stop_condition="Stop at the fresh unaccepted HIL.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    pointer_before = service.store.pointer("book-faires").as_dict()
    original_build = service.sessions.engine.build_candidate

    def interrupt_build(**_arguments):
        raise RuntimeError("simulated process interruption")

    monkeypatch.setattr(
        service.sessions.engine,
        "build_candidate",
        interrupt_build,
    )
    with pytest.raises(RuntimeError, match="simulated process interruption"):
        service.refresh("book-faires", session_id)
    interrupted = service.sessions.load("book-faires", session_id)
    assert interrupted.state.value == "EXIT_BUILDING"
    assert interrupted.candidate_id is None
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    monkeypatch.setattr(
        service.sessions.engine,
        "build_candidate",
        original_build,
    )
    recovered = service.refresh("book-faires", session_id)
    receipt = recovered["interrupted_exit_recovery"]
    assert receipt["candidate_absent"] is True
    assert receipt["pointer_moved"] is False
    assert receipt["acceptance_inferred"] is False
    assert recovered["session"]["state"] == "PVN1_CANDIDATE"
    assert recovered["candidate"]["candidate_id"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    lineage_path = (
        service.store.project_root("book-faires")
        / "lineage"
        / f"{session_id}.jsonl"
    )
    lineage = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    recoveries = [
        event
        for event in lineage
        if event["event_type"] == "pv.interrupted_exit.recovered"
    ]
    assert len(recoveries) == 1
    assert recoveries[0]["visible_payload"]["candidate_absent"] is True


def test_completed_stale_classification_reconciles_into_first_queued_task_only(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Preserve the obsolete classified task as history.",
        permitted_paths=[],
        permitted_tools=["repository_read", "test"],
        acceptance_checks=["The obsolete task remains auditable."],
        stop_condition="Stop without creating a candidate.",
    )
    completed_task = {
        "task_id": "completed-before-reconciliation",
        "task_class": "verify_result",
        "requested_outcome": "Seal the already completed prerequisite.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["The prerequisite evidence remains sealed."],
        "stop_condition": "Stop at its unaccepted HIL.",
    }
    replacement = {
        "task_id": "first-queued-replacement",
        "task_class": "fix_bug",
        "requested_outcome": "Repair the Goal projection boundary.",
        "permitted_paths": ["README.md"],
        "permitted_tools": ["repository_write", "test"],
        "acceptance_checks": ["The Goal projection boundary is exact."],
        "stop_condition": "Stop at a fresh unaccepted HIL.",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[completed_task],
        planned_by="human-test",
        plan_id="completed-before-reconciliation-plan",
    )
    evidence = [
        {
            "task_id": completed_task["task_id"],
            "status": "PASS",
            "implementation_evidence": ["The prerequisite was implemented."],
            "verification_evidence": ["The prerequisite check passed."],
            "limitations": ["The receipt remains unaccepted pending HIL."],
        }
    ]
    batch_receipt = service.store.record_backlog_batch_done(
        "book-faires",
        session_id=session_id,
        candidate_id="PV2_CANDIDATE__STALE_CLASSIFICATION_FIXTURE",
        task_evidence=evidence,
        confirmation="BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED",
    )
    service.plan_tasks(
        "book-faires",
        tasks=[replacement],
        planned_by="human-test",
        plan_id="first-queued-replacement-plan",
    )
    stale = service.sessions.load("book-faires", session_id)
    stale.metadata["active_backlog_task_status"] = "DONE"
    stale.metadata["batch_backlog_task_status"] = "DONE_PENDING_HIL"
    stale.metadata["batch_completion_receipt_id"] = batch_receipt["receipt_id"]
    service.sessions._save(stale)
    readme = source_repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nUnfinished future-test source.\n",
        encoding="utf-8",
    )
    pointer_before = service.store.pointer("book-faires").as_dict()

    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class=replacement["task_class"],
        requested_outcome=replacement["requested_outcome"],
        permitted_paths=replacement["permitted_paths"],
        permitted_tools=replacement["permitted_tools"],
        acceptance_checks=replacement["acceptance_checks"],
        stop_condition=replacement["stop_condition"],
        backlog_task_id=replacement["task_id"],
    )

    reconciliation = classified["classification_reconciliation"]
    assert reconciliation["prior_task_id"]
    assert reconciliation["replacement_backlog_task_id"] == replacement["task_id"]
    assert reconciliation["candidate_created"] is False
    assert reconciliation["hil_inferred"] is False
    assert reconciliation["pointer_moved"] is False
    assert classified["session"]["candidate_id"] is None
    assert classified["session"]["state"] == "TASK_CLASSIFIED"
    assert classified["session"]["metadata"]["task_source_basis"]["kind"] == (
        "RECONCILED_UNFINISHED_SOURCE_BOUNDARY"
    )
    assert classified["session"]["metadata"]["active_backlog_task_id"] == (
        replacement["task_id"]
    )
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    backlog = service.task_backlog("book-faires")
    assert backlog["counts"] == {"ACTIVE": 1, "DONE": 1}
    assert [row["task_id"] for row in backlog["active"]] == [replacement["task_id"]]
    completed_runs = classified["session"]["metadata"]["completed_runs"]
    assert completed_runs[-1]["completion_disposition"] == (
        "STALE_CLASSIFICATION_RECONCILED_WITHOUT_HIL"
    )


def test_stale_classification_reconciliation_fails_without_sealed_batch_receipt(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Keep one active classified task.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=["The task remains active."],
        stop_condition="Stop without a candidate.",
    )
    replacement = {
        "task_id": "unsafe-replacement",
        "task_class": "verify_result",
        "requested_outcome": "Attempt an unsafe replacement.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["The attempt fails closed."],
        "stop_condition": "Stop immediately.",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[replacement],
        planned_by="human-test",
        plan_id="unsafe-replacement-plan",
    )
    stale = service.sessions.load("book-faires", session_id)
    stale.metadata["active_backlog_task_status"] = "DONE"
    stale.metadata["batch_backlog_task_status"] = "DONE_PENDING_HIL"
    stale.metadata["batch_completion_receipt_id"] = "batchdone_missing"
    service.sessions._save(stale)

    with pytest.raises(EvidenceLaneError) as blocked:
        service.sessions.classify(
            "book-faires",
            session_id,
            task_class=replacement["task_class"],
            requested_outcome=replacement["requested_outcome"],
            permitted_paths=replacement["permitted_paths"],
            permitted_tools=replacement["permitted_tools"],
            acceptance_checks=replacement["acceptance_checks"],
            stop_condition=replacement["stop_condition"],
            backlog_task_id=replacement["task_id"],
        )
    assert blocked.value.code == "COMPLETED_TASK_RECONCILIATION_MISMATCH"
    assert service.task_backlog("book-faires")["counts"] == {"QUEUED": 1}
    assert service.store.pointer("book-faires").accepted_pv == "PV1"


def test_single_task_hil_correction_reconciles_into_first_queued_task(
    service,
    source_repository: Path,
) -> None:
    session_id, completed_task, replacement, sealed = (
        _prepare_single_task_hil_correction(service, source_repository)
    )
    pointer_before = service.store.pointer("book-faires").as_dict()

    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class=replacement["task_class"],
        requested_outcome=replacement["requested_outcome"],
        permitted_paths=replacement["permitted_paths"],
        permitted_tools=replacement["permitted_tools"],
        acceptance_checks=replacement["acceptance_checks"],
        stop_condition=replacement["stop_condition"],
        backlog_task_id=replacement["task_id"],
    )

    reconciliation = classified["classification_reconciliation"]
    assert reconciliation["completion_basis_kind"] == (
        "SEALED_SINGLE_TASK_HIL_CORRECTION"
    )
    assert reconciliation["completion_basis_receipt_id"] == (
        sealed["decision"]["decision_id"]
    )
    assert reconciliation["completed_backlog_task_id"] == completed_task["task_id"]
    assert reconciliation["candidate_created"] is False
    assert reconciliation["hil_inferred"] is False
    assert reconciliation["pointer_moved"] is False
    assert classified["session"]["candidate_id"] is None
    assert classified["session"]["metadata"][
        "last_reconciled_hil_correction_decision_id"
    ] == sealed["decision"]["decision_id"]
    assert "resumed_from_pending" not in classified["session"]["metadata"]
    assert classified["session"]["metadata"]["active_backlog_task_id"] == (
        replacement["task_id"]
    )
    completed_runs = classified["session"]["metadata"]["completed_runs"]
    assert completed_runs[-1]["completion_disposition"] == (
        "HIL_CORRECTION_RECONCILED_WITHOUT_CANDIDATE"
    )
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    assert service.store.candidate_path(
        "book-faires", sealed["candidate"]["candidate_id"]
    ).is_dir()
    backlog = service.task_backlog("book-faires")
    assert backlog["counts"] == {"ACTIVE": 1, "DONE": 1}
    assert [row["task_id"] for row in backlog["active"]] == [
        replacement["task_id"]
    ]


def test_single_task_hil_correction_reconciliation_rejects_tampered_receipt(
    service,
    source_repository: Path,
) -> None:
    session_id, _completed_task, replacement, sealed = (
        _prepare_single_task_hil_correction(service, source_repository)
    )
    decision_id = sealed["decision"]["decision_id"]
    receipt_path = (
        service.store.project_root("book-faires")
        / "receipts"
        / f"{decision_id}.json"
    )
    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["decided_by"] = "tampered-actor"
    receipt_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    pointer_before = service.store.pointer("book-faires").as_dict()

    with pytest.raises(EvidenceLaneError) as blocked:
        service.sessions.classify(
            "book-faires",
            session_id,
            task_class=replacement["task_class"],
            requested_outcome=replacement["requested_outcome"],
            permitted_paths=replacement["permitted_paths"],
            permitted_tools=replacement["permitted_tools"],
            acceptance_checks=replacement["acceptance_checks"],
            stop_condition=replacement["stop_condition"],
            backlog_task_id=replacement["task_id"],
        )
    assert blocked.value.code == "COMPLETED_TASK_RECONCILIATION_MISMATCH"
    assert (
        "single_correction.single_decision_receipt_matches_session"
        in blocked.value.details["failed_checks"]
    )
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    assert service.store.candidate_path(
        "book-faires", sealed["candidate"]["candidate_id"]
    ).is_dir()
    assert service.task_backlog("book-faires")["counts"] == {
        "DONE": 1,
        "QUEUED": 1,
    }


def test_full_pv1_task_pv2_approve_next_entry_proves_pv3(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    task = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome="Return a deterministic book count.",
        permitted_paths=["src/app.py"],
        permitted_tools=[
            "pv_search",
            "pv_fetch",
            "repository_read",
            "repository_write",
            "terminal",
            "test",
            "git_diff",
        ],
        acceptance_checks=["GET /books includes count=1"],
        stop_condition="Stop after the bounded test and PV Refresh candidate.",
    )
    assert task["task"]["write_boundary"] == "AUTHORIZED_SANDBOX_PATHS_ONLY"
    application_file = source_repository / "src" / "app.py"
    application_file.write_text(
        application_file.read_text(encoding="utf-8").replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "count": 1}',
        ),
        encoding="utf-8",
    )
    activity = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py", "reason": "bounded fixture change"},
        event_id="evt_bounded_change",
    )
    assert activity["source_state"] == "MUTATED_AFTER_ENTRY"
    assert activity["accepted_pv_query_scope"] == "ENTRY_STATE_ONLY"
    host_identity = activity["event"]["visible_payload"]["host_identity"]
    assert host_identity["raw_host_session_id_stored"] is False
    assert host_identity["host_kind"] == "CODEX_DESKTOP"
    refresh = service.complete_task_and_refresh(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    assert refresh["automatic_refresh"] is True
    assert refresh["user_refresh_command_required"] is False
    assert refresh["next_action"] == "PRESENT_SIX_WAY_HIL"
    assert refresh["suggested_next_prompt"].startswith("/evi-build ")
    assert refresh["next_action_contract"]["composer_authority"] == "HOST_OWNED"
    assert (
        refresh["next_action_contract"]["documented_mcp_composer_mutation_supported"]
        is False
    )
    assert refresh["next_action_contract"]["auto_submit"] is False
    assert refresh["next_action_contract"]["stop_and_wait"] is True
    assert refresh["candidate"]["proposed_pv"] == "PV2"
    assert [
        row["path"] for row in refresh["candidate"]["source_delta"]["modified"]
    ] == ["src/app.py"]
    candidate_path = Path(refresh["candidate"]["stored_path"])
    entry_slip = json.loads(
        (candidate_path / "entry_slip.json").read_text(encoding="utf-8")
    )
    exit_slip = json.loads(
        (candidate_path / "exit_slip.json").read_text(encoding="utf-8")
    )
    assert entry_slip["accepted_entry_pv"] == "PV1"
    assert exit_slip["proposed_pv"] == "PV2"
    assert exit_slip["candidate_id"] == refresh["candidate"]["candidate_id"]
    assert exit_slip["next_action"] == refresh["next_action_contract"]
    assert exit_slip["next_action"]["display_position"] == "BEFORE_HIL_DECISION"
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_pv2",
    )
    assert decision["pointer_advanced"] is True
    assert decision["pointer"]["accepted_pv"] == "PV2"
    assert decision["pointer"]["generation"] == 2
    assert decision["session"]["state"] == "PVN1_ACCEPTED"
    handoff = decision["state_travel_handoff"]["state_travel"]
    assert handoff["target_surface"] == "NEW_CODEX_TASK"
    assert handoff["accepted_pv"] == "PV2"
    assert handoff["pointer_generation"] == 2
    assert handoff["host_window_opened"] is False
    with pytest.raises(EvidenceLaneError) as same_window:
        service.sessions.begin_next_turn("book-faires", session_id)
    assert same_window.value.code == "STATE_TRAVEL_RESUME_REQUIRED"
    service.resume_session(
        project_id="book-faires",
        host="CODEX_DESKTOP",
        host_session_id="host-session-bypass-attempt",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-session-without-state-travel-verification"},
    )
    with pytest.raises(EvidenceLaneError) as bypass:
        service.sessions.begin_next_turn("book-faires", session_id)
    assert bypass.value.code == "STATE_TRAVEL_RESUME_REQUIRED"
    with pytest.raises(EvidenceLaneError) as false_same_host:
        service.sessions.begin_next_turn(
            "book-faires",
            session_id,
            continue_same_host=True,
            continuation_reason="EXPLICIT_USER_CONTINUATION",
        )
    assert false_same_host.value.code == (
        "STATE_TRAVEL_SAME_HOST_CONTINUATION_MISMATCH"
    )
    traveled = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="host-session-state-travel-pv2",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-codex-task"},
    )
    assert traveled["pointer_verification"]["accepted_pv"] == "PV2"
    assert traveled["pointer_verification"]["generation"] == 2
    assert traveled["pointer_verification"]["manifest_sha256"]
    assert traveled["pointer_verification"]["package_sha256"]
    assert traveled["session"]["metadata"]["entry_pv"] == "PV2"
    assert traveled["session"]["metadata"]["next_candidate_would_be"] == "PV3"
    assert traveled["wait_state"] == "WAITING_FOR_NEXT_USER_COMMAND"
    assert traveled["task_started"] is False
    diff = service.reader.diff("book-faires", "PV1", "PV2")
    assert [row["path"] for row in diff["modified"]] == ["src/app.py"]


def test_fresh_host_task_resumes_same_session_and_entry_without_rebuild(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    pointer_before = service.store.pointer("book-faires").as_dict()
    resumed = service.resume_session(
        project_id="book-faires",
        host="codex",
        host_session_id="host-session-resumed",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "new-host-task"},
    )
    assert resumed["resumed"] is True
    assert resumed["session"]["session_id"] == session_id
    assert resumed["session"]["metadata"]["entry_pv"] == "PV1"
    assert resumed["session"]["metadata"]["current_host_session_id"] == (
        "host-session-resumed"
    )
    assert resumed["entry_action"] == "CLASSIFY_ONE_TASK"
    assert resumed["event"]["visible_payload"]["pointer_moved"] is False
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    assert service.store.accepted_ids("book-faires") == ["PV1"]


def test_explicit_exit_boot_closes_only_the_persistent_session(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    pointer_before = service.store.pointer("book-faires").as_dict()
    installation_before = service.sessions.installation_status()
    flash_before = service.session_flash_status()

    resumed = service.resume_session(
        project_id="book-faires",
        host="CODEX_CLI",
        host_session_id="host-session-before-exit-boot",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-local-cli"},
    )
    assert resumed["session"]["session_id"] == session_id

    closed = service.sessions.close(
        "book-faires",
        session_id,
        reason="USER_REQUESTED_EVI_EXIT_BOOT",
    )

    assert closed["status"] == "PASS"
    assert closed["session"]["metadata"]["close_reason"] == (
        "USER_REQUESTED_EVI_EXIT_BOOT"
    )
    assert not (
        service.store.project_root("book-faires") / "active_session.json"
    ).exists()
    assert service.sessions.installation_status() == installation_before
    assert closed["runtime_activation"]["state"] == "DETACHED"
    assert closed["runtime_activation"]["flash_context_attached"] is False
    assert closed["runtime_activation"]["prompt_capture_active"] is False
    assert closed["runtime_activation"]["visible_response_capture_active"] is False
    assert closed["plugin_installation_preserved"] is True
    assert closed["immutable_store_preserved"] is True
    flash_after = service.session_flash_status()
    assert flash_after["authority_digest"] == flash_before["authority_digest"]
    assert flash_after["receipt_sha256"] == flash_before["receipt_sha256"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    with pytest.raises(EvidenceLaneError) as error:
        service.resume_session(
            project_id="book-faires",
            host="CODEX_DESKTOP",
            host_session_id="host-session-after-exit-boot",
            ephemeral=False,
            client_can_edit_source=True,
            server_has_durable_filesystem=True,
            runtime_context={"source": "must-not-resume"},
        )
    assert error.value.code == "NO_ACTIVE_SESSION_TO_RESUME"


def test_retired_chatgpt_host_cannot_boot_or_prepare_state_travel(service) -> None:
    with pytest.raises(EvidenceLaneError) as blocked:
        service.boot_session(
            project_id="book-faires",
            user_id="user-test",
            workspace_id="workspace-test",
            host="CHATGPT",
            agent_id="retired-host-agent",
            sandbox_id=None,
            ephemeral=False,
            runtime_context={"source": "retired-host"},
            host_session_id="retired-host-session",
            client_can_edit_source=False,
            server_has_durable_filesystem=True,
        )
    assert blocked.value.code == "HOST_KIND_INVALID"
    assert not (service.store.project_root("book-faires") / "active_session.json").exists()


@pytest.mark.parametrize(
    ("decision", "kwargs", "state"),
    [
        (
            "APPROVE_WITH_DELTA",
            {"correction_delta": "Correct only src/app.py return shape."},
            "CORRECTION_TASK_PENDING",
        ),
        (
            "MORE_RESEARCH",
            {"research_question": "Does the route contract require pagination?"},
            "RESEARCH_TASK_PENDING",
        ),
        ("REJECT", {"reason": "Fixture does not meet the contract."}, "REJECTED_RUN"),
        ("FAIL", {"reason": "Gate TEST_OUTPUT_MISSING failed."}, "FAILED_RUN"),
    ],
)
def test_nonapprove_outcomes_preserve_pointer(
    service,
    decision: str,
    kwargs: dict,
    state: str,
) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    result = service.decide(
        "book-faires",
        session_id,
        decision=decision,
        decided_by="human-test",
        decision_id=f"decision_{decision.lower()}",
        **kwargs,
    )
    assert result["pointer_advanced"] is False
    assert result["pointer"]["accepted_pv"] is None
    assert result["pointer"]["generation"] == 0
    assert result["session"]["state"] == state


def test_approve_with_delta_requires_exact_delta(service) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    with pytest.raises(EvidenceLaneError) as error:
        service.sessions.decide(
            "book-faires",
            session_id,
            decision="APPROVE_WITH_DELTA",
            decided_by="human-test",
        )
    assert error.value.code == "CORRECTION_DELTA_REQUIRED"


def test_approve_with_delta_records_an_integral_nonpromotable_candidate(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed promotion gate must not prevent the corrective HIL decision."""

    from evidence_lane_plugin import pv_package

    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    candidate = service.build_initial("book-faires", session_id)["candidate"]

    original_validate_lane_bundle = pv_package.validate_lane_bundle

    def newly_strict_lane_validation(path):
        report = original_validate_lane_bundle(path)
        return {
            **report,
            "valid": False,
            "errors": [
                *report.get("errors", []),
                {
                    "lane": "github_code",
                    "error": "CODE_LOGICAL_TOPOLOGY missing",
                },
            ],
        }

    monkeypatch.setattr(
        pv_package,
        "validate_lane_bundle",
        newly_strict_lane_validation,
    )

    with pytest.raises(EvidenceLaneError) as promotion_error:
        service.fuse(
            "book-faires",
            session_id,
            approval="APPROVE",
            decided_by="human-test",
            decision_id="decision_nonpromotable_approve",
        )
    assert promotion_error.value.code == "PV_LANE_BUNDLE_INVALID"

    exact_delta = "Rebuild the exact code logical topology and reseal."
    result = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE_WITH_DELTA",
        decided_by="human-test",
        correction_delta=exact_delta,
        decision_id="decision_nonpromotable_delta",
    )

    assert result["pointer_advanced"] is False
    assert result["pointer"]["accepted_pv"] is None
    assert result["pointer"]["generation"] == 0
    assert result["session"]["state"] == "CORRECTION_TASK_PENDING"
    assert service.store.candidate_path(
        "book-faires", candidate["candidate_id"]
    ).is_dir()
    receipt = result["decision"]
    assert receipt["candidate_integrity_validated"] is True
    assert receipt["candidate_promotable"] is False


def test_pv_fuse_requires_exact_case_sensitive_approve(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    with pytest.raises(EvidenceLaneError) as error:
        service.fuse(
            "book-faires",
            session_id,
            approval="approve",
            decided_by="human-test",
        )
    assert error.value.code == "PV_FUSE_EXACT_APPROVE_REQUIRED"
    assert service.store.pointer("book-faires").accepted_pv is None
    assert service.store.pointer("book-faires").generation == 0


def test_initial_approve_with_delta_reseals_pv1_without_inventing_parent(
    service,
    source_repository: Path,
) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    first = service.build_initial("book-faires", session_id)["candidate"]
    exact_delta = "Add the exact governed note and reseal PV1."
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE_WITH_DELTA",
        decided_by="human-test",
        correction_delta=exact_delta,
        decision_id="decision_initial_delta",
    )
    assert decision["pointer"]["accepted_pv"] is None
    assert decision["pointer"]["generation"] == 0

    classified = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome=exact_delta,
        permitted_paths=["README.md"],
        permitted_tools=["repository_write", "terminal", "test"],
        acceptance_checks=["The governed note is present."],
        stop_condition="Stop at the corrected PV1 HIL.",
    )
    assert classified["session"]["metadata"]["task_source_basis"] == {
        "kind": "HIL_CANDIDATE_SOURCE",
        "candidate_id": first["candidate_id"],
        "accepted_pv_context": None,
    }
    readme = source_repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\nGoverned correction.\n",
        encoding="utf-8",
    )
    git(source_repository, "add", "README.md")
    git(source_repository, "commit", "-m", "Apply initial PV1 correction")
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "README.md", "commit_created": True},
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    corrected = service.refresh("book-faires", session_id)["candidate"]
    assert corrected["proposed_pv"] == "PV1"
    assert corrected["candidate_id"] != first["candidate_id"]
    assert service.store.candidate_path("book-faires", first["candidate_id"]).is_dir()
    assert service.store.pointer("book-faires").generation == 0

    fused = service.fuse(
        "book-faires",
        session_id,
        approval="APPROVE",
        decided_by="human-test",
        decision_id="decision_corrected_pv1",
    )
    assert fused["pointer"]["accepted_pv"] == "PV1"
    assert fused["pointer"]["generation"] == 1
    assert fused["pointer_moved"] is True
    handoff = fused["state_travel_handoff"]["state_travel"]
    assert handoff["accepted_pv"] == "PV1"
    assert handoff["target_surface"] == "NEW_CODEX_TASK"
    traveled = service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="host-session-corrected-pv1",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-corrected-pv1-task"},
    )
    assert traveled["session"]["metadata"]["entry_pv"] == "PV1"
    assert traveled["session"]["metadata"]["next_candidate_would_be"] == "PV2"
    assert traveled["wait_state"] == "WAITING_FOR_NEXT_USER_COMMAND"


def test_approve_with_delta_resumes_only_the_exact_correction(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome="Return a deterministic book count.",
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["GET /books includes count=1"],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    application_file = source_repository / "src" / "app.py"
    application_file.write_text(
        application_file.read_text(encoding="utf-8").replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "count": 1}',
        ),
        encoding="utf-8",
    )
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py"},
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    exact_delta = "Correct only src/app.py return shape."
    decision = service.decide(
        "book-faires",
        session_id,
        decision="APPROVE_WITH_DELTA",
        decided_by="human-test",
        correction_delta=exact_delta,
        decision_id="decision_exact_delta",
    )
    assert decision["session"]["task"] is None
    assert decision["session"]["candidate_id"] is None
    assert (
        decision["session"]["metadata"]["pending_task"]["required_task_class"]
        == "fix_bug"
    )

    with pytest.raises(EvidenceLaneError) as wrong_class:
        service.sessions.classify(
            "book-faires",
            session_id,
            task_class="research",
            requested_outcome=exact_delta,
            permitted_paths=[],
            permitted_tools=["repository_read"],
            acceptance_checks=[],
            stop_condition="Stop after the bounded check.",
        )
    assert wrong_class.value.code == "PENDING_TASK_CLASS_MISMATCH"

    with pytest.raises(EvidenceLaneError) as wrong_outcome:
        service.sessions.classify(
            "book-faires",
            session_id,
            task_class="fix_bug",
            requested_outcome="Broaden the correction.",
            permitted_paths=["src/app.py"],
            permitted_tools=["repository_write", "test"],
            acceptance_checks=["Exact return-shape test passes"],
            stop_condition="Stop at the next HIL gate.",
        )
    assert wrong_outcome.value.code == "PENDING_TASK_OUTCOME_MISMATCH"

    resumed = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome=exact_delta,
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["Exact return-shape test passes"],
        stop_condition="Stop at the next HIL gate.",
    )
    assert "pending_task" not in resumed["session"]["metadata"]
    assert resumed["session"]["metadata"]["task_source_basis"]["kind"] == (
        "HIL_CANDIDATE_SOURCE"
    )
    assert service.store.pointer("book-faires").generation == 1


def test_more_research_resumes_only_as_exact_research(
    service,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Verify the route contract.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=[],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    question = "Does the route contract require pagination?"
    service.decide(
        "book-faires",
        session_id,
        decision="MORE_RESEARCH",
        decided_by="human-test",
        research_question=question,
        decision_id="decision_exact_research",
    )
    resumed = service.sessions.classify(
        "book-faires",
        session_id,
        task_class="research",
        requested_outcome=question,
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=[],
        stop_condition="Stop after the bounded research answer.",
    )
    assert resumed["task"]["task_class"] == "research"
    assert resumed["session"]["metadata"]["source_state"] == (
        "PENDING_CANDIDATE_SOURCE_EXACT"
    )


def test_return_to_accepted_requires_exact_source_restore(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="fix_bug",
        requested_outcome="Return a deterministic book count.",
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["GET /books includes count=1"],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    application_file = source_repository / "src" / "app.py"
    original = application_file.read_text(encoding="utf-8")
    application_file.write_text(
        original.replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "count": 1}',
        ),
        encoding="utf-8",
    )
    service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="file.modified",
        visible_payload={"path": "src/app.py"},
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    service.decide(
        "book-faires",
        session_id,
        decision="REJECT",
        decided_by="human-test",
        reason="The bounded change is rejected.",
        decision_id="decision_rejected_pv2",
    )
    with pytest.raises(EvidenceLaneError) as not_restored:
        service.sessions.return_to_accepted(
            "book-faires",
            session_id,
            reason="Return to PV1.",
        )
    assert not_restored.value.code == "ACCEPTED_SOURCE_RESTORE_REQUIRED"

    application_file.write_text(original, encoding="utf-8")
    returned = service.sessions.return_to_accepted(
        "book-faires",
        session_id,
        reason="Rejected source removed; return to PV1.",
    )
    assert returned["pointer_advanced"] is False
    assert returned["pointer"]["accepted_pv"] == "PV1"
    assert returned["pointer"]["generation"] == 1
    assert returned["session"]["state"] == "PVN_ACCEPTED"
    assert returned["session"]["metadata"]["source_state"] == "ACCEPTED_ENTRY_EXACT"


def test_return_to_accepted_blocks_without_any_accepted_pv(service) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    service.decide(
        "book-faires",
        session_id,
        decision="FAIL",
        decided_by="human-test",
        reason="Initial candidate gate failed.",
        decision_id="decision_initial_fail",
    )
    with pytest.raises(EvidenceLaneError) as no_entry:
        service.sessions.return_to_accepted(
            "book-faires",
            session_id,
            reason="Attempt return.",
        )
    assert no_entry.value.code == "RETURN_TO_ACCEPTED_NO_ACCEPTED_PV"


def test_pointer_compare_and_swap_blocks_stale_promotion(service) -> None:
    from .conftest import boot_local

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    candidate = service.build_initial("book-faires", session_id)["candidate"]
    with pytest.raises(EvidenceLaneError) as error:
        service.store.promote(
            "book-faires",
            candidate["candidate_id"],
            expected_pointer_generation=9,
            decided_by="human-test",
            decision_id="decision_stale",
        )
    assert error.value.code == "POINTER_COMPARE_AND_SWAP_FAILED"
    assert service.store.pointer("book-faires").accepted_pv is None


def test_promotion_requires_matching_postseal_acceptance_receipt(
    service,
    source_repository: Path,
) -> None:
    session_id, _candidate = build_and_approve_pv1(service)
    declaration = "AC12 executable: validate the immutable candidate."
    manifest = source_repository / "evidence" / "acceptance" / "commands.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    declaration: {
                        "argv": [
                            "$RUNTIME_PYTHON",
                            "-c",
                            (
                                "import os,sys,pathlib; p=pathlib.Path("
                                "os.environ.get('EVIDENCE_LANE_CANDIDATE_PATH','')); "
                                "sys.exit(0 if p.is_dir() else 9)"
                            ),
                        ],
                        "phase": "POSTSEAL",
                        "timeout_seconds": 30,
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    git(source_repository, "add", ".")
    git(source_repository, "commit", "-m", "Add exact postseal check")
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Prove post-seal acceptance is promotion-gating.",
        permitted_paths=["evidence/acceptance/commands.json"],
        permitted_tools=["repository_read", "test"],
        acceptance_checks=[declaration],
        stop_condition="Stop at the unaccepted PV2 HIL.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    refreshed = service.refresh("book-faires", session_id)
    candidate = refreshed["candidate"]
    assert candidate["acceptance_checks"]["verdict"] == "POSTSEAL_CHECKS_PENDING"
    assert candidate["postseal_acceptance"]["verdict"] == (
        "ALL_EXECUTABLE_CHECKS_PASS"
    )
    receipt_path = Path(candidate["postseal_acceptance_receipt"])
    receipt_bytes = receipt_path.read_bytes()
    receipt_path.unlink()
    with pytest.raises(EvidenceLaneError) as missing:
        service.store.promote(
            "book-faires",
            candidate["candidate_id"],
            expected_pointer_generation=1,
            decided_by="human-test",
            decision_id="decision_missing_postseal",
        )
    assert missing.value.code == "POSTSEAL_ACCEPTANCE_RECEIPT_REQUIRED"
    receipt_path.write_bytes(receipt_bytes)
    promoted = service.store.promote(
        "book-faires",
        candidate["candidate_id"],
        expected_pointer_generation=1,
        decided_by="human-test",
        decision_id="decision_valid_postseal",
    )
    assert promoted["pointer"]["accepted_pv"] == "PV2"
    assert promoted["receipt"]["postseal_acceptance"]["status"] == "PASS"


def test_rollback_travels_backward_forward_and_preserves_next_ordinal(
    service,
    source_repository: Path,
) -> None:
    from .conftest import boot_local

    first_session, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        first_session,
        task_class="verify_result",
        requested_outcome="Verify the unchanged accepted source.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=["Human verifies the unchanged source."],
        stop_condition="Stop at the PV2 HIL gate.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        first_session,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", first_session)
    service.decide(
        "book-faires",
        first_session,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_pv2_for_rollback",
    )
    service.sessions.close(
        "book-faires",
        first_session,
        reason="Open a fresh prompt at accepted PV2.",
    )
    accepted_before = {
        pv_id: {
            path.relative_to(
                service.store.accepted_path("book-faires", pv_id)
            ).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                item
                for item in service.store.accepted_path("book-faires", pv_id).rglob("*")
                if item.is_file()
            )
        }
        for pv_id in ("PV1", "PV2")
    }
    readme = source_repository / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + "\nLive source changed after accepted PV2.\n",
        encoding="utf-8",
    )

    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    assert boot["persistent_state_envelope"]["entry_pv"] == "PV2"
    assert (
        boot["persistent_state_envelope"]["freshness"]["state"] == "DIRTY_WORKING_TREE"
    )

    backward = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PV1",
        decision_id="decision_rollback_backward",
    )
    assert backward["pointer"]["accepted_pv"] == "PV1"
    assert backward["pointer"]["generation"] == 3
    assert backward["pointer_moved"] is True
    assert backward["decision"]["history_preserved"] is True
    assert backward["decision"]["live_source_rewritten"] is False
    assert (
        backward["decision"]["freshness_after_state_travel"]["state"]
        == "DIRTY_WORKING_TREE"
    )
    assert service.store.next_pv_id("book-faires") == "PV3"
    stale_search = service.reader.search("book-faires", "list_books")
    assert stale_search["authority_state"] == "CURRENT_ACCEPTED_PV"
    assert stale_search["status"] == "STALE"
    assert stale_search["live_truth_status"] == "DIRTY_WORKING_TREE"

    forward = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        decision_id="decision_rollback_bare_to_entry",
    )
    assert forward["decision"]["default_entry_target_used"] is True
    assert forward["pointer"]["accepted_pv"] == "PV2"
    assert forward["pointer"]["generation"] == 4
    assert forward["decision"]["history_preserved"] is True
    assert forward["decision"]["live_source_rewritten"] is False

    same_target = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="2",
        decision_id="decision_rollback_same_target",
    )
    assert same_target["pointer_moved"] is False
    assert same_target["pointer"]["generation"] == 4

    with pytest.raises(EvidenceLaneError) as invalid_target:
        service.rollback(
            "book-faires",
            session_id,
            decided_by="human-test",
            rollback_to="PV9",
            decision_id="decision_rollback_invalid",
        )
    assert invalid_target.value.code == "ROLLBACK_TARGET_NOT_ACCEPTED"

    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="verify_result",
        requested_outcome="Build a preserved PV3 candidate.",
        permitted_paths=[],
        permitted_tools=["repository_read"],
        acceptance_checks=["Human reviews the PV3 candidate."],
        stop_condition="Stop at the PV3 HIL gate.",
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    candidate = service.refresh("book-faires", session_id)["candidate"]
    rolled_candidate = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PV1",
        decision_id="decision_rollback_candidate",
    )
    assert rolled_candidate["candidate_promoted"] is False
    assert (
        rolled_candidate["decision"]["candidate_preserved_unaccepted"]
        == (candidate["candidate_id"])
    )
    assert rolled_candidate["pointer"]["accepted_pv"] == "PV1"
    assert service.store.candidate_path(
        "book-faires", candidate["candidate_id"]
    ).is_dir()
    assert service.store.accepted_ids("book-faires") == ["PV1", "PV2"]
    assert service.store.next_pv_id("book-faires") == "PV3"
    accepted_after = {
        pv_id: {
            path.relative_to(
                service.store.accepted_path("book-faires", pv_id)
            ).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(
                item
                for item in service.store.accepted_path("book-faires", pv_id).rglob("*")
                if item.is_file()
            )
        }
        for pv_id in ("PV1", "PV2")
    }
    assert accepted_after == accepted_before
    with pytest.raises(EvidenceLaneError) as stale_pointer:
        service.store.rollback(
            "book-faires",
            target_pv="PV2",
            expected_pointer_generation=4,
            decided_by="human-test",
            decision_id="decision_rollback_stale_generation",
            default_entry_target_used=False,
            entry_pv="PV2",
            candidate_id=None,
            freshness={"state": "UNVERIFIED"},
        )
    assert stale_pointer.value.code == "ROLLBACK_POINTER_COMPARE_AND_SWAP_FAILED"
