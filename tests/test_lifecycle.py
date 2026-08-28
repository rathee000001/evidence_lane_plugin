from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.lane_engine import build_lane_bundle
from evidence_lane_plugin.mcp_server import create_mcp_server
from evidence_lane_plugin.plan_runtime import append_delta_event
from evidence_lane_plugin.project_authority import PROJECT_AUTHORITY_CONFIRMATION

from .conftest import boot_local, build_and_approve_pv1, git


def _relocate_live_project_root(
    service,
    source_repository: Path,
    target: Path,
) -> None:
    relocated = service.register_project(
        project_id="book-faires",
        display_name="Book Faires",
        repository_path=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
        project_authority_root=str(target),
        project_authority_migration_confirmation=PROJECT_AUTHORITY_CONFIRMATION,
        expected_accepted_pv="PV1",
        expected_pointer_generation=1,
        selected_by="human-test",
    )
    assert relocated["state"] == "REGISTERED_PROJECT_AUTHORITY_RELOCATED"


def _materialize_live_root_sectors(service, source_repository: Path) -> None:
    pointer = service.store.pointer("book-faires")
    sectors = service.store.project_root("book-faires") / "sectors"
    staging = sectors.with_name(".sectors-test-stage")
    assert not staging.exists()
    build_lane_bundle(
        repository_root=source_repository,
        output_directory=staging,
        code_mode="local_code",
        parent_lane_bundle=None,
        parent_pv=pointer.accepted_pv,
        proposed_pv=f"{pointer.accepted_pv}_WORKING",
        pointer_generation=pointer.generation,
        include_untracked=False,
        materialize_all_lanes=True,
        index_git_history=False,
    )
    if sectors.exists():
        shutil.rmtree(sectors)
    staging.replace(sectors)


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
    assert (
        classified["session"]["metadata"]["active_backlog_task_id"]
        == (replacement["task_id"])
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


def test_visible_task_activity_is_redacted_allowlisted_and_idempotent(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="add_bounded_feature",
        requested_outcome="Record the exact visible operational event surface.",
        permitted_paths=["README.md"],
        permitted_tools=["repository_read", "repository_write", "test"],
        acceptance_checks=["One redacted idempotent event exists per activity."],
        stop_condition="Stop after the ordinary-turn activity proof.",
    )
    activity_types = [
        "tool.selected",
        "command.executed",
        "file.inspected",
        "file.created",
        "file.modified",
        "file.deleted",
        "test.output",
        "build.output",
        "git.diff",
        "warning",
        "error",
        "response",
        "usage",
    ]
    receipts: dict[str, dict[str, Any]] = {}
    for index, activity_type in enumerate(activity_types, start=1):
        visible_payload: dict[str, Any] = {
            "subject": activity_type,
            "visible_detail": "access_token=super-secret-value",
        }
        if activity_type == "usage":
            visible_payload["token_metrics"] = {"availability": "UNAVAILABLE"}
        receipt = service.sessions.record_activity(
            "book-faires",
            session_id,
            activity_type=activity_type,
            visible_payload=visible_payload,
            event_id=f"row170-visible-activity-{index}",
        )
        event = receipt["event"]
        receipts[activity_type] = receipt
        assert receipt["status"] == "PASS"
        assert event["event_type"] == f"task.{activity_type}"
        assert event["private_reasoning_stored"] is False
        assert "super-secret-value" not in json.dumps(event, sort_keys=True)
        assert event["visible_payload"]["visible_detail"] == "[REDACTED]"

    lineage_path = (
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    )
    before_replay = lineage_path.read_text(encoding="utf-8").splitlines()
    response_replay = service.sessions.record_activity(
        "book-faires",
        session_id,
        activity_type="response",
        visible_payload={
            "subject": "response",
            "visible_detail": "access_token=super-secret-value",
        },
        event_id="row170-visible-activity-12",
    )
    after_replay = lineage_path.read_text(encoding="utf-8").splitlines()
    assert response_replay["event"] == receipts["response"]["event"]
    assert after_replay == before_replay

    with pytest.raises(EvidenceLaneError) as conflicting_replay:
        service.sessions.record_activity(
            "book-faires",
            session_id,
            activity_type="response",
            visible_payload={
                "subject": "response",
                "visible_detail": "a different visible response",
            },
            event_id="row170-visible-activity-12",
        )
    assert conflicting_replay.value.code == "LINEAGE_EVENT_ID_CONFLICT"

    with pytest.raises(EvidenceLaneError) as private_reasoning_blocked:
        service.sessions.record_activity(
            "book-faires",
            session_id,
            activity_type="response",
            visible_payload={"private_reasoning": "must never persist"},
        )
    assert private_reasoning_blocked.value.code == (
        "LINEAGE_PRIVATE_REASONING_FORBIDDEN"
    )

    with pytest.raises(EvidenceLaneError) as unsupported_blocked:
        service.sessions.record_activity(
            "book-faires",
            session_id,
            activity_type="private.output",
            visible_payload={"subject": "forbidden"},
        )
    assert unsupported_blocked.value.code == "ACTIVITY_TYPE_UNSUPPORTED"


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
    assert not (
        service.store.project_root("book-faires") / "active_session.json"
    ).exists()


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


def test_public_pv_fuse_requires_matching_accepted_learning_weave(service) -> None:
    boot = boot_local(service)
    session_id = boot["session"]["session_id"]
    service.build_initial("book-faires", session_id)
    pointer_before = service.store.pointer("book-faires").as_dict()

    with pytest.raises(EvidenceLaneError) as error:
        service.fuse(
            "book-faires",
            session_id,
            approval="APPROVE",
            decided_by="human-test",
            decision_id="decision_requires_dual_learning",
            require_dual_learning_hil=True,
        )

    assert error.value.code == "PV_FUSE_DUAL_LEARNING_HIL_APPROVAL_REQUIRED"
    assert service.store.pointer("book-faires").as_dict() == pointer_before


def test_dual_hil_presentation_includes_bounded_learning_weave_summary(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import evidence_lane_plugin.service as service_module

    monkeypatch.setattr(
        service_module,
        "inspect_learning_authority",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "candidate_count": 152,
            "learning_weaves": [
                {
                    "accepted_project_pv": "PV12",
                    "target_project_pv": "PV13",
                    "member_count": 150,
                    "member_set_sha256": "A" * 64,
                    "weave_candidate_id": "learn_weave_pv13",
                    "weave_candidate_sha256": "B" * 64,
                    "weave_candidate_state": "PENDING_LEARNING_HIL",
                    "receipt_sha256": "C" * 64,
                }
            ],
        },
    )

    presentation = service._dual_hil_presentation(
        "book-faires",
        candidate={
            "candidate_id": "PV13_HIL_PROPOSAL__RUN_TEST",
            "proposed_pv": "PV13",
            "manifest_sha256": "D" * 64,
        },
    )

    assert presentation["status"] == "PASS"
    assert presentation["learning"]["auto_accepted_delta_member_count"] == 150
    assert presentation["learning"]["hil_relevant_candidate_count"] == 151
    assert presentation["learning"]["ledger_candidate_count"] == 152
    assert presentation["learning"]["immutable_other_history_count"] == 1
    assert presentation["learning"]["full_member_payload_returned"] is False
    assert presentation["required_exact_approval_tokens"] == {
        "project": "PROJECT PV13: APPROVE",
        "learning": "AI LEARNING PV13: APPROVE",
    }
    assert presentation["accepted_archive_queried"] is False
    assert service.store.pointer("book-faires").accepted_pv is None
    assert service.store.pointer("book-faires").generation == 0


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
