from __future__ import annotations

from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_file

from .conftest import build_and_approve_pv1


def _tasks(count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "task_id": f"EL-BATCH-DELTA-{position:03d}",
            "task_class": "modify_code",
            "requested_outcome": f"Implement bounded behavior {position}.",
            "permitted_paths": ["src/app.py"],
            "permitted_tools": ["repository_write", "test"],
            "acceptance_checks": [f"bounded check {position} passes"],
            "stop_condition": "Stop at a fresh unaccepted candidate HIL.",
        }
        for position in range(1, count + 1)
    ]


def _evidence(tasks: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "task_id": task["task_id"],
            "status": "PASS",
            "implementation_evidence": [
                f"Implemented the exact contract for {task['task_id']}."
            ],
            "verification_evidence": [
                f"Targeted test for {task['task_id']} passed."
            ],
            "limitations": ["Candidate remains unaccepted pending HIL."],
        }
        for task in tasks
    ]


def test_batch_completion_rejects_missing_or_reordered_evidence_atomically(
    service,
) -> None:
    tasks = _tasks()
    service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-batch-atomic",
    )
    backlog_path = service.store.project_root("book-faires") / "task_backlog.json"
    before_sha256 = sha256_file(backlog_path)
    evidence = _evidence(tasks)

    with pytest.raises(EvidenceLaneError) as missing:
        service.store.record_backlog_batch_done(
            "book-faires",
            session_id="session-batch",
            candidate_id="PV2_CANDIDATE__BATCH_TEST",
            task_evidence=evidence[:-1],
            confirmation="BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED",
        )
    assert missing.value.code == "BATCH_DELTA_ORDER_OR_SET_MISMATCH"
    assert sha256_file(backlog_path) == before_sha256

    with pytest.raises(EvidenceLaneError) as reordered:
        service.store.record_backlog_batch_done(
            "book-faires",
            session_id="session-batch",
            candidate_id="PV2_CANDIDATE__BATCH_TEST",
            task_evidence=list(reversed(evidence)),
            confirmation="BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED",
        )
    assert reordered.value.code == "BATCH_DELTA_ORDER_OR_SET_MISMATCH"
    assert sha256_file(backlog_path) == before_sha256


def test_batch_completion_preserves_order_and_appends_linear_done_events(
    service,
) -> None:
    tasks = _tasks()
    planned = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-batch-linear",
    )
    contract_before = [
        {
            key: task[key]
            for key in (
                "task_id",
                "sequence",
                "plan_id",
                "task_class",
                "requested_outcome",
                "permitted_paths",
                "permitted_tools",
                "acceptance_checks",
                "stop_condition",
            )
        }
        for task in planned["tasks"]
    ]
    pointer_before = service.store.pointer("book-faires").as_dict()
    evidence = _evidence(tasks)
    receipt = service.store.record_backlog_batch_done(
        "book-faires",
        session_id="session-batch",
        candidate_id="PV2_CANDIDATE__BATCH_TEST",
        task_evidence=evidence,
        confirmation="BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED",
    )
    assert receipt["status"] == "PASS"
    assert receipt["task_ids"] == [task["task_id"] for task in tasks]
    assert receipt["task_count"] == 3
    assert receipt["resulting_status"] == "DONE_PENDING_HIL"
    assert receipt["pointer_moved"] is False
    assert receipt["candidate_accepted"] is False

    backlog = service.task_backlog("book-faires")
    assert backlog["counts"] == {"DONE": 3}
    assert [task["task_id"] for task in backlog["tasks"]] == receipt["task_ids"]
    contract_after = [
        {key: task[key] for key in contract}
        for task, contract in zip(backlog["tasks"], contract_before, strict=True)
    ]
    assert contract_after == contract_before
    transition_events = [
        event
        for event in backlog["tasks"][0]["lifecycle_events"]
        if event["event_type"] in {"ACTIVATED", "TASK_DONE"}
    ]
    assert [event["to_status"] for event in transition_events] == ["ACTIVE", "DONE"]
    assert service.store.pointer("book-faires").as_dict() == pointer_before

    replay = service.store.record_backlog_batch_done(
        "book-faires",
        session_id="session-batch",
        candidate_id="PV2_CANDIDATE__BATCH_TEST",
        task_evidence=evidence,
        confirmation="BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED",
    )
    assert replay["idempotent"] is True
    assert replay["receipt_sha256"] == receipt["receipt_sha256"]


def test_complete_and_refresh_binds_batch_to_unaccepted_candidate(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    tasks = _tasks(2)
    service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-batch-refresh",
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class="modify_code",
        requested_outcome="Implement the exact ordered batch in one source state.",
        permitted_paths=["src/app.py"],
        permitted_tools=["repository_write", "test"],
        acceptance_checks=["all batch evidence passes"],
        stop_condition="Stop at the fresh unaccepted HIL.",
    )
    app = source_repository / "src" / "app.py"
    app.write_text(
        app.read_text(encoding="utf-8").replace(
            'return {"books": ["Dune"]}',
            'return {"books": ["Dune"], "batch": True}',
        ),
        encoding="utf-8",
    )
    result = service.complete_task_and_refresh(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
        batch_task_evidence=_evidence(tasks),
        batch_completion_confirmation=(
            "BATCH_DELTA_IMPLEMENTATION_EVIDENCE_CONFIRMED"
        ),
    )
    completion = result["batch_backlog_completion"]
    assert completion["candidate_id"] == result["candidate"]["candidate_id"]
    assert completion["task_ids"] == [task["task_id"] for task in tasks]
    assert result["session"]["state"] == "PVN1_CANDIDATE"
    assert result["candidate"]["candidate_id"]
    assert service.store.pointer("book-faires").accepted_pv == "PV1"
    assert service.task_backlog("book-faires")["counts"] == {"DONE": 2}
