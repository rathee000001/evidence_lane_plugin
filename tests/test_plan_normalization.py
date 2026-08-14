from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_bytes
from evidence_lane_plugin.mcp_server import create_mcp_server

from .conftest import build_and_approve_pv1


def _task(
    task_id: str,
    outcome: str,
    *,
    supersedes_task_id: str | None = None,
    panel_role: str | None = None,
) -> dict:
    row = {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": outcome,
        "permitted_paths": [],
        "permitted_tools": ["repository_read", "test"],
        "acceptance_checks": [f"Verify {task_id}."],
        "stop_condition": f"Stop when {task_id} is verified.",
    }
    if supersedes_task_id:
        row["supersedes_task_id"] = supersedes_task_id
    if panel_role:
        row["panel_role"] = panel_role
    return row


def _prepared_normalization(service) -> tuple[str, list[dict], dict]:
    session_id, _ = build_and_approve_pv1(service)
    old_active = _task("old-active", "Preserve the old active umbrella row.")
    old_queued = _task("old-queued", "Preserve the old queued umbrella row.")
    service.plan_tasks(
        "book-faires",
        tasks=[old_active, old_queued],
        planned_by="human-test",
        plan_id="old-plan",
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=old_active["task_class"],
        requested_outcome=old_active["requested_outcome"],
        permitted_paths=old_active["permitted_paths"],
        permitted_tools=old_active["permitted_tools"],
        acceptance_checks=old_active["acceptance_checks"],
        stop_condition=old_active["stop_condition"],
        backlog_task_id=old_active["task_id"],
    )
    new_tasks = [
        _task("normalization-review", "Verify the approved normalization."),
        _task(
            "normalized-active",
            "Prove the live normalized surface.",
            supersedes_task_id="old-active",
        ),
        _task(
            "normalized-queued",
            "Execute the next bounded normalized row.",
            supersedes_task_id="old-queued",
        ),
        _task(
            "normalized-final-hil",
            "Present the physically final six-way HIL.",
            panel_role="PHYSICALLY_FINAL_HIL",
        ),
    ]
    backlog = service.task_backlog("book-faires")
    status = service.status("book-faires")
    assert status["active_session"]["backlog_task_id"] == "old-active"
    transition = {
        "transition_id": "normalization-test-001",
        "session_id": session_id,
        "expected_canonical_plan_sha256": backlog["canonical_plan_projection"][
            "projection_sha256"
        ],
        "expected_session_sha256": status["active_session"][
            "session_snapshot_sha256"
        ],
        "expected_pointer_sha256": status["persistent_state_envelope"][
            "pointer_snapshot_sha256"
        ],
        "old_active_task_id": "old-active",
        "replacement_task_id": "normalized-active",
        "approved_review_gate_id": "normalization-review",
        "approval_receipt_sha256": "A" * 64,
        "expected_candidate_absent": True,
        "expected_pending_hil": False,
        "expected_superseded_task_ids": ["old-active", "old-queued"],
        "expected_result_task_count": 6,
    }
    return session_id, new_tasks, transition


def _apply(service, tasks: list[dict], transition: dict) -> dict:
    return service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="normalized-plan",
        host_kind="CODEX_DESKTOP",
        host_mode="PLAN",
        normalization_transition=transition,
    )


def test_plan_normalization_is_exact_idempotent_and_state_travel_uses_current_goal(
    service,
) -> None:
    session_id, tasks, transition = _prepared_normalization(service)
    before = service.task_backlog("book-faires")
    wrong = {**transition, "expected_canonical_plan_sha256": "0" * 64}
    with pytest.raises(EvidenceLaneError) as mismatch:
        _apply(service, tasks, wrong)
    assert mismatch.value.code == "PLAN_NORMALIZATION_PRECONDITION_MISMATCH"
    after_mismatch = service.task_backlog("book-faires")
    assert after_mismatch["event_count"] == before["event_count"]
    assert len(after_mismatch["tasks"]) == 2
    journal_root = service.store.project_root("book-faires") / "plan_normalization"
    assert not journal_root.exists()

    pointer_before = service.store.pointer("book-faires").as_dict()
    normalized = _apply(service, tasks, transition)
    receipt = normalized["normalization_transition"]
    assert receipt["status"] == "PASS"
    assert receipt["journal_phase"] == "COMMITTED"
    assert receipt["idempotent_replay"] is False
    assert receipt["task_count"] == 6
    assert receipt["counts"] == {
        "ACTIVE": 1,
        "DONE": 1,
        "QUEUED": 2,
        "SUPERSEDED": 2,
    }
    assert [row["task_id"] for row in normalized["active"]] == ["normalized-active"]
    assert normalized["tasks"][-1]["task_id"] == "normalized-final-hil"
    assert normalized["tasks"][-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert service.store.pointer("book-faires").as_dict() == pointer_before
    session = service.sessions.load("book-faires", session_id)
    assert session.metadata["active_backlog_task_id"] == "normalized-active"
    assert session.task is not None
    assert session.task["task_id"] == "normalized-active"
    assert session.candidate_id is None

    event_count = normalized["event_count"]
    replayed = _apply(service, tasks, transition)
    assert replayed["normalization_transition"]["idempotent_replay"] is True
    assert replayed["event_count"] == event_count
    lineage_path = (
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    )
    lineage = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rebound = [
        row for row in lineage if row["event_type"] == "plan.normalization.rebound"
    ]
    assert len(rebound) == 1

    profile = {
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
    }
    handoff = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": profile},
    )["state_travel"]
    prepared = handoff["resume_contract"]
    assert len(prepared["task_list"]) == 4
    assert [row["task_id"] for row in prepared["task_list"]] == [
        "normalization-review",
        "normalized-active",
        "normalized-queued",
        "normalized-final-hil",
    ]
    assert [row["status"] for row in prepared["task_list"]] == [
        "COMPLETED",
        "IN_PROGRESS",
        "PENDING",
        "PENDING",
    ]
    assert [row["canonical_plan_sequence"] for row in prepared["task_list"]] == [
        3,
        4,
        5,
        6,
    ]
    assert prepared["resume_step"] == 2
    assert prepared["task_list"][-1]["panel_role"] == "PHYSICALLY_FINAL_HIL"
    assert handoff["plan_snapshot"]["task_count"] == 6
    assert handoff["plan_snapshot"]["executable_task_count"] == 4
    assert handoff["plan_snapshot"]["history_task_count"] == 2
    assert handoff["plan_snapshot"]["history_projection_sha256"] == normalized[
        "history_projection"
    ]["projection_sha256"]


def test_plan_normalization_recovers_after_plan_append_crash(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, tasks, transition = _prepared_normalization(service)
    original = service.store.activate_plan_normalization

    def interrupt_activation(*_args, **_kwargs):
        raise RuntimeError("simulated normalization interruption")

    monkeypatch.setattr(
        service.store,
        "activate_plan_normalization",
        interrupt_activation,
    )
    with pytest.raises(RuntimeError, match="simulated normalization interruption"):
        _apply(service, tasks, transition)
    journal_path = (
        service.store.project_root("book-faires")
        / "plan_normalization"
        / "normalization-test-001.json"
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["phase"] == "PLAN_APPENDED"
    partial = service.task_backlog("book-faires")
    assert len(partial["tasks"]) == 6
    assert partial["active"] == []
    assert (
        service.sessions.load("book-faires", session_id).metadata[
            "active_backlog_task_id"
        ]
        == "old-active"
    )

    monkeypatch.setattr(service.store, "activate_plan_normalization", original)
    recovered = _apply(service, tasks, transition)
    assert recovered["normalization_transition"]["status"] == "PASS"
    assert recovered["normalization_transition"]["journal_phase"] == "COMMITTED"
    assert recovered["normalization_transition"]["idempotent_replay"] is False
    assert [row["task_id"] for row in recovered["active"]] == ["normalized-active"]
    session = service.sessions.load("book-faires", session_id)
    assert session.metadata["active_backlog_task_id"] == "normalized-active"


def test_plan_normalization_rejects_candidate_before_writing(service) -> None:
    session_id, tasks, transition = _prepared_normalization(service)
    session = service.sessions.load("book-faires", session_id)
    session.candidate_id = "PV2_CANDIDATE__UNACCEPTED"
    service.sessions._save(session)
    transition["expected_session_sha256"] = service.sessions.session_snapshot_sha256(
        service.sessions.load("book-faires", session_id)
    )
    with pytest.raises(EvidenceLaneError) as blocked:
        _apply(service, tasks, transition)
    assert blocked.value.code == "PLAN_NORMALIZATION_CANDIDATE_OR_HIL_PRESENT"
    assert len(service.task_backlog("book-faires")["tasks"]) == 2
    assert not (
        service.store.project_root("book-faires") / "plan_normalization"
    ).exists()


def _prepared_normalization_correction(
    service,
) -> tuple[str, dict, dict, str, str]:
    session_id, tasks, transition = _prepared_normalization(service)
    normalized = _apply(service, tasks, transition)
    original_receipt = normalized["normalization_transition"]
    correction_text = (
        "Restore the original active task; the replacement normalized the Plan "
        "shape but did not complete or replace the live implementation contract."
    )
    correction_delta_id = "CORRECT_MISTAKEN_ACTIVE_REPLACEMENT"
    service.record_steer_delta(
        "book-faires",
        delta_text=correction_text,
        actor="human-test",
        delta_id=correction_delta_id,
        linked_task_id="normalized-active",
    )
    backlog = service.task_backlog("book-faires")
    status = service.status("book-faires")
    correction = {
        "transition_id": "normalization-correction-test-001",
        "correction_of_transition_id": transition["transition_id"],
        "session_id": session_id,
        "expected_canonical_plan_sha256": backlog[
            "canonical_plan_projection"
        ]["projection_sha256"],
        "expected_session_sha256": status["active_session"][
            "session_snapshot_sha256"
        ],
        "expected_pointer_sha256": status["persistent_state_envelope"][
            "pointer_snapshot_sha256"
        ],
        "expected_original_request_sha256": original_receipt["request_sha256"],
        "expected_original_result_sha256": original_receipt["result_sha256"],
        "restored_active_task_id": "old-active",
        "mistaken_replacement_task_id": "normalized-active",
        "correction_delta_id": correction_delta_id,
        "correction_delta_sha256": sha256_bytes(correction_text.encode("utf-8")),
        "correction_receipt_sha256": sha256_bytes(
            correction_text.encode("utf-8")
        ),
        "expected_candidate_absent": True,
        "expected_pending_hil": False,
        "expected_result_task_count": 6,
        "expected_physically_final_task_id": "normalized-final-hil",
        "goal_row_offset": 80,
    }
    original_journal_sha256 = sha256_bytes(
        Path(original_receipt["journal_path"]).read_bytes()
    )
    return (
        session_id,
        correction,
        original_receipt,
        correction_text,
        original_journal_sha256,
    )


def _apply_normalization_correction(service) -> tuple[str, dict, dict, str]:
    (
        session_id,
        correction,
        original_receipt,
        correction_text,
        original_journal_sha256,
    ) = _prepared_normalization_correction(service)
    corrected = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="normalized-plan-correction",
        host_kind="CODEX_DESKTOP",
        host_mode="PLAN",
        normalization_transition=correction,
    )
    assert sha256_bytes(Path(original_receipt["journal_path"]).read_bytes()) == (
        original_journal_sha256
    )
    return session_id, correction, corrected, correction_text


def test_plan_normalization_correction_restores_active_and_dynamic_rows(
    service,
) -> None:
    session_id, correction, corrected, _ = _apply_normalization_correction(service)
    receipt = corrected["normalization_transition"]
    assert receipt["status"] == "PASS"
    assert receipt["rows_appended"] == 0
    assert receipt["plan_events_appended"] == 2
    assert receipt["active_task_id"] == "old-active"
    assert receipt["superseded_mistaken_task_id"] == "normalized-active"
    assert receipt["goal_row_start"] == 81
    assert receipt["goal_row_end"] == 84
    assert [row["task_id"] for row in corrected["active"]] == ["old-active"]
    assert len(corrected["tasks"]) == 6
    assert [row["number"] for row in corrected["goal_projection"]["rows"]] == [
        81,
        82,
        83,
        84,
    ]
    assert corrected["history_projection"]["task_count"] == 2
    session = service.sessions.load("book-faires", session_id)
    assert session.metadata["active_backlog_task_id"] == "old-active"
    assert session.task is not None and session.task["task_id"] == "old-active"

    event_count = corrected["event_count"]
    replayed = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="normalized-plan-correction",
        host_kind="CODEX_DESKTOP",
        host_mode="PLAN",
        normalization_transition=correction,
    )
    assert replayed["normalization_transition"]["idempotent_replay"] is True
    assert replayed["event_count"] == event_count

    profile = {
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
    }
    handoff = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": profile},
    )["state_travel"]
    prepared = handoff["resume_contract"]
    assert [row["number"] for row in prepared["task_list"]] == [81, 82, 83, 84]
    assert prepared["resume_step"] == 81
    assert prepared["panel_reactivation"]["visible_row_start"] == 81
    assert prepared["panel_reactivation"]["visible_row_end"] == 84


def test_current_execution_row_numbers_shift_on_insert_and_drop(service) -> None:
    _apply_normalization_correction(service)
    inserted = _task("inserted-current-delta", "Verify one inserted current Delta.")
    service.record_steer_delta(
        "book-faires",
        delta_text="Insert one independent current Delta before the final HIL.",
        actor="human-test",
        delta_id="INSERT_CURRENT_DELTA",
        new_task_contract=inserted,
    )
    after_insert = service.task_backlog("book-faires")
    assert [row["number"] for row in after_insert["goal_projection"]["rows"]] == [
        81,
        82,
        83,
        84,
        85,
    ]
    assert after_insert["goal_projection"]["rows"][-1]["task_id"] == (
        "normalized-final-hil"
    )

    service.transition_task(
        "book-faires",
        task_id="inserted-current-delta",
        transition_name="DROP",
        decided_by="human-test",
        reason="The inserted test Delta is no longer executable.",
    )
    after_drop = service.task_backlog("book-faires")
    assert [row["number"] for row in after_drop["goal_projection"]["rows"]] == [
        81,
        82,
        83,
        84,
    ]
    assert after_drop["goal_projection"]["row_end"] == 84
    assert any(
        row["task_id"] == "inserted-current-delta"
        and row["lifecycle_status"] == "DROPPED"
        for row in after_drop["history_projection"]["rows"]
    )


def test_plan_normalization_correction_precondition_mismatch_writes_nothing(
    service,
) -> None:
    session_id, correction, original_receipt, _, original_sha = (
        _prepared_normalization_correction(service)
    )
    before = service.task_backlog("book-faires")
    wrong = {**correction, "expected_canonical_plan_sha256": "0" * 64}

    with pytest.raises(EvidenceLaneError) as mismatch:
        service.plan_tasks(
            "book-faires",
            tasks=[],
            planned_by="human-test",
            plan_id="normalized-plan-correction",
            host_kind="CODEX_DESKTOP",
            host_mode="PLAN",
            normalization_transition=wrong,
        )

    assert mismatch.value.code == (
        "PLAN_NORMALIZATION_CORRECTION_PRECONDITION_MISMATCH"
    )
    after = service.task_backlog("book-faires")
    assert after["event_count"] == before["event_count"]
    assert [row["task_id"] for row in after["active"]] == ["normalized-active"]
    assert service.sessions.load("book-faires", session_id).metadata[
        "active_backlog_task_id"
    ] == "normalized-active"
    assert not (
        service.store.project_root("book-faires")
        / "plan_normalization"
        / "normalization-correction-test-001.json"
    ).exists()
    assert sha256_bytes(Path(original_receipt["journal_path"]).read_bytes()) == (
        original_sha
    )


def test_plan_normalization_correction_recovers_after_plan_write(
    service,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, correction, original_receipt, _, original_sha = (
        _prepared_normalization_correction(service)
    )
    original_write = service.sessions._write_plan_normalization_journal
    interrupted = False

    def interrupt_after_plan(project_id, transition_id, journal):
        nonlocal interrupted
        if (
            not interrupted
            and transition_id == "normalization-correction-test-001"
            and journal.get("phase") == "PLAN_CORRECTED"
        ):
            interrupted = True
            raise RuntimeError("simulated correction journal interruption")
        return original_write(project_id, transition_id, journal)

    monkeypatch.setattr(
        service.sessions,
        "_write_plan_normalization_journal",
        interrupt_after_plan,
    )
    with pytest.raises(RuntimeError, match="simulated correction journal interruption"):
        service.plan_tasks(
            "book-faires",
            tasks=[],
            planned_by="human-test",
            plan_id="normalized-plan-correction",
            host_kind="CODEX_DESKTOP",
            host_mode="PLAN",
            normalization_transition=correction,
        )

    partial = service.task_backlog("book-faires")
    assert [row["task_id"] for row in partial["active"]] == ["old-active"]
    assert service.sessions.load("book-faires", session_id).metadata[
        "active_backlog_task_id"
    ] == "normalized-active"
    correction_journal = (
        service.store.project_root("book-faires")
        / "plan_normalization"
        / "normalization-correction-test-001.json"
    )
    assert json.loads(correction_journal.read_text(encoding="utf-8"))["phase"] == (
        "PREPARED"
    )

    monkeypatch.setattr(
        service.sessions,
        "_write_plan_normalization_journal",
        original_write,
    )
    recovered = service.plan_tasks(
        "book-faires",
        tasks=[],
        planned_by="human-test",
        plan_id="normalized-plan-correction",
        host_kind="CODEX_DESKTOP",
        host_mode="PLAN",
        normalization_transition=correction,
    )
    assert recovered["normalization_transition"]["status"] == "PASS"
    assert recovered["normalization_transition"]["idempotent_replay"] is False
    assert service.sessions.load("book-faires", session_id).metadata[
        "active_backlog_task_id"
    ] == "old-active"
    assert sha256_bytes(Path(original_receipt["journal_path"]).read_bytes()) == (
        original_sha
    )


def test_plan_normalization_preserves_current_public_catalog(
    service,
) -> None:
    server = create_mcp_server(service=service)
    tools = asyncio.run(server.list_tools())
    assert len(tools) == 83
    assert sum(tool.annotations.readOnlyHint is True for tool in tools) == 26
    assert sum(tool.annotations.readOnlyHint is False for tool in tools) == 57
    plan_tool = next(tool for tool in tools if tool.name == "pv_plan_tasks")
    assert "normalization_transition" in plan_tool.inputSchema["properties"]
