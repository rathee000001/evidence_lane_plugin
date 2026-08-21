from __future__ import annotations

import copy

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.plan_runtime import append_delta_event


def _task(task_id: str, dependency: str | None = None) -> dict:
    task = {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": f"Verify {task_id}.",
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["The bounded verification passes."],
        "stop_condition": "Stop after bounded verification.",
    }
    if dependency:
        task["dependencies"] = [dependency]
    return task


def _seed_dependency_plan(service) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[_task("transition-root"), _task("transition-child", "transition-root")],
        planned_by="human-test",
        plan_id="transition-atomic-seed",
    )


def test_drop_dependency_failure_writes_nothing(service) -> None:
    _seed_dependency_plan(service)
    before = service.store._load_backlog("book-faires")

    with pytest.raises(EvidenceLaneError) as exc:
        service.transition_task(
            "book-faires",
            task_id="transition-root",
            transition_name="DROP",
            decided_by="human-test",
            reason="The row is obsolete.",
            event_id="transition-drop-must-not-persist",
        )

    assert exc.value.code == "PLAN_DEPENDENCY_NOT_EARLIER_EXECUTABLE_ROW"
    after = service.store._load_backlog("book-faires")
    assert after == before
    root = next(row for row in after["tasks"] if row["task_id"] == "transition-root")
    assert root["status"] == "QUEUED"


def test_hash_bound_drop_correction_restores_only_exact_partial_event(service) -> None:
    _seed_dependency_plan(service)
    backlog = service.store._load_backlog("book-faires")
    partial = copy.deepcopy(backlog)
    root = next(
        row for row in partial["tasks"] if row["task_id"] == "transition-root"
    )
    failed_event = append_delta_event(
        partial,
        task_id="transition-root",
        event_type="DROPPED",
        to_status="DROPPED",
        actor="legacy-bug",
        event_id="legacy-partial-drop",
        assume_initialized=True,
        details={"reason_sha256": "A" * 64, "history_preserved": True},
    )
    root["history"].append(
        {
            "event": "DROPPED",
            "event_id": failed_event["event_id"],
            "recorded_at": failed_event["recorded_at"],
        }
    )
    service.store._persist_backlog("book-faires", partial)
    partial_sha256 = sha256_bytes(canonical_json_bytes(partial))

    corrected = service.transition_task(
        "book-faires",
        task_id="transition-root",
        transition_name="CORRECT_DROP",
        decided_by="human-test",
        reason="Restore the exact row after the persisted dependency failure.",
        event_id="legacy-partial-drop-correction",
        correction_of_event_id="legacy-partial-drop",
        expected_backlog_sha256=partial_sha256,
    )

    assert corrected["status"] == "PASS"
    receipt = corrected["transition_correction_receipt"]
    assert receipt["correction_of_event_id"] == "legacy-partial-drop"
    assert receipt["dangling_dependent_task_ids"] == ["transition-child"]
    assert receipt["history_preserved"] is True
    assert receipt["pointer_moved"] is False
    restored = service.store._load_backlog("book-faires")
    root = next(row for row in restored["tasks"] if row["task_id"] == "transition-root")
    assert root["status"] == "QUEUED"
    assert [row["event_id"] for row in root["history"]][-2:] == [
        "legacy-partial-drop",
        "legacy-partial-drop-correction",
    ]


def test_drop_correction_rejects_wrong_backlog_hash(service) -> None:
    _seed_dependency_plan(service)

    with pytest.raises(EvidenceLaneError) as exc:
        service.transition_task(
            "book-faires",
            task_id="transition-root",
            transition_name="CORRECT_DROP",
            decided_by="human-test",
            reason="Attempt an unbound correction.",
            event_id="wrong-hash-correction",
            correction_of_event_id="missing-event",
            expected_backlog_sha256="0" * 64,
        )

    assert exc.value.code == "DELTA_DROP_CORRECTION_AUTHORITY_MISMATCH"
