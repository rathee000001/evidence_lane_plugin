from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.service import EvidenceLaneService


def _planned_task(task_id: str, outcome: str) -> dict:
    return {
        "task_id": task_id,
        "task_class": "verify_result",
        "requested_outcome": outcome,
        "permitted_paths": [],
        "permitted_tools": ["repository_read"],
        "acceptance_checks": ["Human verifies the bounded result."],
        "stop_condition": "Stop at the next candidate HIL.",
    }


def test_linear_backlog_queues_many_but_claims_one(service) -> None:
    tasks = [
        _planned_task("delta-001", "Verify the first bounded result."),
        _planned_task("delta-002", "Verify the second bounded result."),
    ]
    planned = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-linear-001",
    )
    assert planned["counts"] == {"QUEUED": 2}
    assert [task["sequence"] for task in planned["tasks"]] == [1, 2]
    assert planned["event_count"] == 2
    assert planned["plan_runtime_projection"]["status"] == "PASS"
    first_projection_sha256 = planned["plan_runtime_projection"]["sqlite_sha256"]

    idempotent = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-linear-001",
    )
    assert len(idempotent["tasks"]) == 2
    assert idempotent["event_count"] == 2
    assert (
        idempotent["plan_runtime_projection"]["sqlite_sha256"]
        == first_projection_sha256
    )

    contract = {
        **tasks[0],
        "task_id": "task_runtime_001",
        "write_boundary": "READ_ONLY",
        "hil_required": True,
        "status": "CLASSIFIED",
    }
    claimed = service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id="delta-001",
        session_id="session_test",
        contract=contract,
    )
    assert claimed["status"] == "ACTIVE"
    with pytest.raises(EvidenceLaneError) as second_active:
        service.store.claim_backlog_task(
            "book-faires",
            backlog_task_id="delta-002",
            session_id="session_test",
            contract={**contract, **tasks[1]},
        )
    assert second_active.value.code == "BACKLOG_ACTIVE_TASK_EXISTS"

    done = service.store.record_backlog_done(
        "book-faires",
        backlog_task_id="delta-001",
        session_id="session_test",
        candidate_id="PV1_CANDIDATE__RUN_TEST",
    )
    assert done["status"] == "DONE"
    replayed_done = service.store.record_backlog_done(
        "book-faires",
        backlog_task_id="delta-001",
        session_id="session_test",
        candidate_id="PV1_CANDIDATE__RUN_TEST",
    )
    assert replayed_done["status"] == "DONE"

    outcome = service.store.record_backlog_outcome(
        "book-faires",
        backlog_task_id="delta-001",
        session_id="session_test",
        decision="APPROVE",
        decided_by="human-test",
        candidate_id="PV1_CANDIDATE__RUN_TEST",
        accepted_pv="PV1",
    )
    assert outcome["status"] == "ACCEPTED"
    replayed_outcome = service.store.record_backlog_outcome(
        "book-faires",
        backlog_task_id="delta-001",
        session_id="session_test",
        decision="APPROVE",
        decided_by="human-test",
        candidate_id="PV1_CANDIDATE__RUN_TEST",
        accepted_pv="PV1",
    )
    assert replayed_outcome["status"] == "ACCEPTED"
    backlog = service.task_backlog("book-faires")
    assert backlog["counts"] == {
        "ACCEPTED": 1,
        "QUEUED": 1,
    }
    first_events = backlog["tasks"][0]["lifecycle_events"]
    assert [event["to_status"] for event in first_events] == [
        "QUEUED",
        "ACTIVE",
        "DONE",
        "ACCEPTED",
    ]
    assert backlog["plan_runtime_projection"]["status"] == "PASS"


def test_dual_hil_acceptance_is_stamped_in_plan_without_archive_query(service) -> None:
    task = _planned_task("dual-hil-delta", "Present one dual PV HIL.")
    service.plan_tasks(
        "book-faires",
        tasks=[task],
        planned_by="human-test",
        plan_id="plan-dual-hil-stamp",
    )
    service.store.claim_backlog_task(
        "book-faires",
        backlog_task_id=task["task_id"],
        session_id="session_dual_hil",
        contract={
            **task,
            "task_id": "runtime-dual-hil",
            "write_boundary": "READ_ONLY",
            "hil_required": True,
            "status": "CLASSIFIED",
        },
    )
    service.store.record_backlog_done(
        "book-faires",
        backlog_task_id=task["task_id"],
        session_id="session_dual_hil",
        candidate_id="PV13_HIL_PROPOSAL__RUN_TEST",
    )
    stamp_body = {
        "schema": "evidence-lane.plan-dual-hil-acceptance-stamp.v1",
        "status": "PASS",
        "project_id": "book-faires",
        "plan_task_id": task["task_id"],
        "target_pv": "PV13",
        "project_decision": "APPROVE",
        "project_decision_id": "project-decision-13",
        "project_decision_receipt_sha256": "A" * 64,
        "project_proposal_id": "PV13_HIL_PROPOSAL__RUN_TEST",
        "learning_decision": "APPROVE",
        "learning_weave_candidate_id": "learn_weave_13",
        "learning_weave_candidate_sha256": "B" * 64,
        "learning_weave_receipt_sha256": "C" * 64,
        "learning_approval_receipt_sha256": "D" * 64,
        "learning_member_count": 150,
        "learning_summary": (
            "150 auto-accepted Delta Learning members woven into one PV13 "
            "Learning approval."
        ),
        "accepted_snapshot_role": "POST_APPROVAL_STORAGE_ONLY",
        "accepted_archive_opened_for_stamp": False,
        "accepted_archive_queried_for_stamp": False,
        "accepted_archive_model_context_source": False,
        "approval_inferred": False,
        "stamped_at": "2026-08-25T22:00:00.000000Z",
    }
    stamp = {
        **stamp_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(stamp_body)),
    }

    outcome = service.store.record_backlog_outcome(
        "book-faires",
        backlog_task_id=task["task_id"],
        session_id="session_dual_hil",
        decision="APPROVE",
        decided_by="human-test",
        candidate_id="PV13_HIL_PROPOSAL__RUN_TEST",
        accepted_pv="PV13",
        dual_hil_stamp=stamp,
    )

    assert outcome["status"] == "ACCEPTED"
    assert outcome["dual_hil_acceptance_stamp"] == stamp
    persisted = service.task_backlog("book-faires")["tasks"][0]
    assert persisted["lifecycle_events"][-1]["event_type"] == "HIL_OUTCOME"
    assert persisted["lifecycle_events"][-1]["details"][
        "dual_hil_acceptance_stamp"
    ] == stamp


def test_delta_drop_and_supersede_are_explicit_append_only_events(service) -> None:
    tasks = [
        _planned_task("delta-drop", "Drop this bounded Delta explicitly."),
        _planned_task("delta-old", "Supersede this bounded Delta explicitly."),
        _planned_task("delta-new", "Replace the superseded bounded Delta."),
    ]
    service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-lifecycle-001",
    )

    dropped = service.transition_task(
        "book-faires",
        task_id="delta-drop",
        transition_name="DROP",
        decided_by="human-test",
        reason="The user explicitly removed this Delta from scope.",
        event_id="delta-drop-event-001",
    )
    assert dropped["task"]["status"] == "DROPPED"
    assert dropped["event"]["details"]["history_preserved"] is True

    replayed_drop = service.transition_task(
        "book-faires",
        task_id="delta-drop",
        transition_name="DROP",
        decided_by="human-test",
        reason="The user explicitly removed this Delta from scope.",
        event_id="delta-drop-event-001",
    )
    assert replayed_drop["event"]["event_sha256"] == dropped["event"]["event_sha256"]
    assert len(replayed_drop["task"]["history"]) == 1

    superseded = service.transition_task(
        "book-faires",
        task_id="delta-old",
        transition_name="SUPERSEDE",
        decided_by="human-test",
        reason="The replacement preserves the intent with a corrected contract.",
        replacement_task_id="delta-new",
        event_id="delta-supersede-event-001",
    )
    assert superseded["task"]["status"] == "SUPERSEDED"
    assert superseded["task"]["superseded_by_task_id"] == "delta-new"
    backlog = service.task_backlog("book-faires")
    assert backlog["counts"] == {
        "DROPPED": 1,
        "QUEUED": 1,
        "SUPERSEDED": 1,
    }
    goal = backlog["goal_projection"]
    assert goal["task_count"] == 1
    assert goal["canonical_task_count"] == 3
    assert goal["history_task_count"] == 2
    assert len(goal["rows"]) == 1
    row = goal["rows"][0]
    assert row == {
        "task_id": "delta-new",
        "step": "Replace the superseded bounded Delta.",
        "plan_sequence": 3,
        "lifecycle_status": "QUEUED",
        "steer_deltas": [],
            "task_classification": "verify_result",
            "plan_group": "plan-lifecycle-001",
            "plan_group_source": "PLAN_ID_FALLBACK",
            "commit_batch_id": "UNASSIGNED",
        "commit_batch_source": "NO_EXPLICIT_CONTRACT_OR_LINKED_DIRECTIVE",
        "dependencies": [],
        "dependency_source": "LINEAR_ROOT",
        "git_commit_stage": "NOT_DECLARED",
        "git_commit_stage_source": "NO_EXPLICIT_CONTRACT_OR_TASK_TEXT",
        "version_marker": "NOT_DECLARED",
        "version_marker_source": "NO_CURRENT_AUTHORITY_CLAIM",
        "version_claims": [],
        "version_reconciliation_required": False,
        "branch_marker": "NOT_DECLARED",
        "branch_marker_source": "NO_CURRENT_AUTHORITY_CLAIM",
        "branch_claims": [],
        "branch_reconciliation_required": False,
        "effective_for_execution": True,
        "supersedes_task_id": "delta-old",
        "superseded_by_task_ids": [],
        "authority_scope": "CURRENT_EXECUTABLE_PLAN",
        "number": 1,
        "status": "pending",
        "visible_label": (
            "Row 1 / delta-new — [CLASS=verify_result; "
            "GROUP=plan-lifecycle-001; BATCH=UNASSIGNED; DEP=ROOT; "
            "GIT=NOT_DECLARED@NO_EXPLICIT_CONTRACT_OR_TASK_TEXT; "
            "VERSION=NOT_DECLARED@NO_CURRENT_AUTHORITY_CLAIM; "
                "BRANCH=NOT_DECLARED@NO_CURRENT_AUTHORITY_CLAIM; "
                "ROLE=STANDARD; STATE=QUEUED] "
            "Replace the superseded bounded Delta."
        ),
    }
    history = backlog["history_projection"]
    assert [row["task_id"] for row in history["rows"]] == [
        "delta-drop",
        "delta-old",
    ]
    assert all("status" not in row for row in history["rows"])
    assert all(
        row["execution_status"] == "NON_EXECUTABLE"
        for row in history["rows"]
    )
    assert [row["task_id"] for row in history["parking_rows"]] == [
        "delta-drop"
    ]
    assert [row["task_id"] for row in history["superseded_rows"]] == [
        "delta-old"
    ]
    assert all(row["effective_for_execution"] is False for row in history["rows"])
    assert all(
        row["dependency_source"] == "NON_EXECUTABLE_HISTORY"
        for row in history["rows"]
    )
    assert next(
        row for row in history["rows"] if row["task_id"] == "delta-old"
    )["authority_scope"] == "IMMUTABLE_SUPERSEDED_HISTORY"
    assert row["dependencies"] == []
    assert row["supersedes_task_id"] == "delta-old"
    replacement = next(
        task for task in backlog["tasks"] if task["task_id"] == "delta-new"
    )
    assert replacement["supersedes_task_id"] == "delta-old"
    assert backlog["plan_runtime_projection"]["status"] == "PASS"


def test_plan_runtime_projection_detects_semantic_sqlite_tamper(service) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[
            _planned_task(
                "delta-projection",
                "Verify semantic projection tamper detection.",
            )
        ],
        planned_by="human-test",
        plan_id="plan-projection-001",
    )
    projection_path = service.store._plan_runtime_path("book-faires")
    with sqlite3.connect(projection_path) as connection:
        connection.execute(
            "UPDATE delta_task SET task_class = ? WHERE task_id = ?",
            ("tampered-class", "delta-projection"),
        )
        connection.commit()

    status = service.store.plan_runtime_status("book-faires")
    assert status["integrity"] == ["ok"]
    assert status["status"] == "STALE"
    assert (
        status["projection_content_sha256"]
        != status["expected_projection_content_sha256"]
    )


def test_plan_runtime_v2_reports_legacy_projection_stale_without_read_failure(
    service,
) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[
            _planned_task(
                "legacy-runtime",
                "Keep canonical Plan authority readable across a hot upgrade.",
            )
        ],
        planned_by="human-test",
        plan_id="legacy-plan-runtime",
    )
    projection_path = service.store._plan_runtime_path("book-faires")
    projection_path.unlink()
    with sqlite3.connect(projection_path) as connection:
        connection.executescript(
            """
            PRAGMA user_version = 1;
            CREATE TABLE projection_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            INSERT INTO projection_meta (key, value)
            VALUES ('schema', 'evidence-lane.plan-runtime-projection.v1');
            CREATE TABLE delta_task (
                task_id TEXT PRIMARY KEY,
                current_status TEXT NOT NULL
            );
            """
        )

    status = service.store.plan_runtime_status("book-faires")
    assert status["status"] == "STALE"
    assert status["reason"] == "DERIVED_SCHEMA_REBUILD_REQUIRED"
    assert status["observed_schema"].endswith(".v1")
    assert status["sqlite_user_version"] == 1
    assert status["expected_sqlite_user_version"] == 4
    assert status["rebuild_action"] == (
        "NEXT_GOVERNED_PLAN_WRITE_ATOMIC_REBUILD"
    )
    assert status["canonical_plan_sector_mutated"] is False
    assert status["raw_pv_model_context_loading"] is False


def test_plan_runtime_v3_indexes_full_contract_steers_rows_and_bounded_fts(
    service,
) -> None:
    service.plan_tasks(
        "book-faires",
        tasks=[
            {
                **_planned_task(
                    "runtime-root",
                    "Implement the bounded Plan runtime authority.",
                ),
                "plan_group": "runtime-foundation",
                "commit_batch_id": "runtime-batch",
                "dependencies": [],
            },
            _planned_task(
                "runtime-child",
                "Verify the live Plan query contract without loading a PV.",
            ),
        ],
        planned_by="human-test",
        plan_id="plan-runtime-v3",
    )
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "PLAN_GROUP=canon-runtime COMMIT_BATCH=canon-query "
            "DEPENDS_ON=runtime-root GIT_STAGE=NO_COMMIT. "
            "Keep Memory SQLite separate from the AI Learning arm."
        ),
        actor="human-test",
        delta_id="runtime-child-steer",
        linked_task_id="runtime-child",
    )

    status = service.store.plan_runtime_status("book-faires")
    assert status["status"] == "PASS"
    assert status["sqlite_user_version"] == 4
    assert status["task_formula_event_count"] == 0
    assert status["task_formula_lineage_indexed"] is True
    assert status["full_task_contracts_indexed"] is True
    assert status["steer_deltas_indexed"] is True
    assert status["steer_count"] == 1
    assert status["execution_row_count"] == 2
    assert status["history_row_count"] == 0
    assert status["fts_record_count"] == 3
    assert status["accepted_pv_payload_copied"] is False
    assert status["raw_pv_model_context_loading"] is False
    assert status["memory_sqlite_authority"] == (
        "SEPARATE_FROM_AI_LEARNING_AND_PROJECT_TRUTH"
    )

    exact = service.store.plan_runtime_query(
        "book-faires",
        task_id="runtime-child",
    )
    assert exact["query_mode"] == "EXACT_TASK_ID"
    assert exact["row"]["plan_group"] == "canon-runtime"
    assert exact["row"]["commit_batch_id"] == "canon-query"
    assert exact["row"]["dependencies_json"] == '["runtime-root"]'
    assert exact["row"]["git_commit_stage"] == "NO_COMMIT"
    assert exact["contract"]["requested_outcome"].startswith(
        "Verify the live Plan query contract"
    )
    assert [steer["delta_id"] for steer in exact["steers"]] == [
        "runtime-child-steer"
    ]
    assert exact["accepted_pv_payload_loaded"] is False

    fts = service.store.plan_runtime_query(
        "book-faires",
        query="Memory SQLite learning",
    )
    assert fts["query_mode"] == "BOUNDED_FTS5"
    assert [hit["source_id"] for hit in fts["hits"]] == [
        "runtime-child-steer"
    ]
    assert fts["accepted_pv_payload_loaded"] is False

    window = service.task_backlog_window("book-faires")
    assert window["full_ledger_returned"] is False
    assert window["accepted_pv_payload_loaded"] is False
    assert len(window["rows"]) == 2
    assert all("step" not in row and "requested_outcome" not in row for row in window["rows"])
    assert window["row_ui_contract"] == "NO_HOST_STEP_LIST_BEFORE_PLAN_ACTIVATION"
    assert window["items"] == []
    assert window["fixed_header"] is None
    assert window["host_update_plan_contract"] is None
    assert "plan_runtime_projection" not in window
    assert window["plan_runtime_receipt"]["full_runtime_projection_returned"] is False
    assert window["plan_runtime_receipt"]["raw_pv_payload_loaded"] is False
    assert window["plan_runtime_receipt"]["raw_chat_scrollback_loaded"] is False
    assert len(json.dumps(window, sort_keys=True).encode("utf-8")) < 8192


def test_legacy_backlog_statuses_migrate_without_dropping_history(service) -> None:
    planned_at = "2026-07-30T00:00:00Z"
    legacy_statuses = {
        "legacy-accepted": ("COMPLETED_ACCEPTED", "ACCEPTED"),
        "legacy-follow-up": ("FOLLOW_UP_PENDING", "DONE"),
        "legacy-rollback": ("ROLLED_BACK_UNACCEPTED", "ROLLED_BACK"),
    }
    legacy_tasks = []
    for sequence, (task_id, (legacy_status, _)) in enumerate(
        legacy_statuses.items(),
        start=1,
    ):
        legacy_tasks.append(
            {
                **_planned_task(task_id, f"Preserve {task_id}."),
                "sequence": sequence,
                "plan_id": "legacy-plan",
                "status": legacy_status,
                "planned_at": planned_at,
                "history": [{"event": "LEGACY_VISIBLE_HISTORY"}],
            }
        )
    backlog_path = service.store._backlog_path("book-faires")
    backlog_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.linear-task-backlog.v1",
                "project_id": "book-faires",
                "plans": [
                    {
                        "plan_id": "legacy-plan",
                        "planned_by": "legacy-human",
                        "planned_at": planned_at,
                        "task_ids": list(legacy_statuses),
                    }
                ],
                "tasks": legacy_tasks,
            }
        ),
        encoding="utf-8",
    )

    migrated = service.task_backlog("book-faires")
    assert migrated["counts"] == {
        "ACCEPTED": 1,
        "DONE": 1,
        "ROLLED_BACK": 1,
    }
    for task in migrated["tasks"]:
        assert task["status"] == legacy_statuses[task["task_id"]][1]
        assert task["history"] == [{"event": "LEGACY_VISIBLE_HISTORY"}]
        assert task["lifecycle_events"][0]["event_type"] == ("LEGACY_STATUS_IMPORTED")

    persisted = service.plan_tasks(
        "book-faires",
        tasks=[
            _planned_task(
                "post-migration-delta",
                "Append one Delta after the compatibility migration.",
            )
        ],
        planned_by="human-test",
        plan_id="post-migration-plan",
    )
    assert persisted["counts"] == {
        "ACCEPTED": 1,
        "DONE": 1,
        "QUEUED": 1,
        "ROLLED_BACK": 1,
    }
    persisted_json = json.loads(backlog_path.read_text(encoding="utf-8"))
    assert len(persisted_json["events"]) == 4
    assert persisted["plan_runtime_projection"]["status"] == "PASS"


def test_plan_rejects_unsupported_tool_before_backlog_persistence(service) -> None:
    invalid = _planned_task(
        "delta-invalid-tool",
        "Attempt to queue a tool that classification cannot claim.",
    )
    invalid["permitted_tools"] = ["git_sync_selected"]
    with pytest.raises(EvidenceLaneError) as error:
        service.plan_tasks(
            "book-faires",
            tasks=[invalid],
            planned_by="human-test",
            plan_id="plan-invalid-tool",
        )
    assert error.value.code == "TASK_TOOL_NOT_AUTHORIZED"
    backlog = service.task_backlog("book-faires")
    assert backlog["tasks"] == []
    assert backlog["plans"] == []
    assert backlog["counts"] == {}


def test_local_enrollment_adopts_without_cloning_or_booting(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    application = EvidenceLaneService(data_root=tmp_path / "enrollment-store")
    result = application.enroll_project(
        project_id="adopted-book-faires",
        display_name="Adopted Book Faires",
        source=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        branch="main",
    )
    assert result["enrollment_mode"] == "ADOPTED_LOCAL_PATH"
    assert result["project"]["project"]["source_lane"] == "local_code"
    assert application.store.config("adopted-book-faires").source_lane == "local_code"
    assert result["remote_write_performed"] is False
    assert result["cloned"] is False
    status = application.status("adopted-book-faires")
    assert status["pointer"]["accepted_pv"] is None
    assert status["active_session"] is None


def test_selected_git_sync_applies_only_clean_fast_forward(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    checkout = tmp_path / "selected-checkout"
    subprocess.run(
        ["git", "clone", "--no-local", str(source_repository), str(checkout)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "remote",
            "set-url",
            "origin",
            "https://github.com/example/book-faires.git",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    application = EvidenceLaneService(data_root=tmp_path / "sync-store")
    application.register_project(
        project_id="selected-checkout",
        display_name="Selected checkout",
        repository_path=str(checkout),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    readme = source_repository / "README.md"
    readme.write_text("# Book Faires\n\nSelected branch update.\n", encoding="utf-8")
    from .conftest import git

    git(source_repository, "add", "README.md")
    git(source_repository, "commit", "-m", "Selected update")
    expected_commit = git(source_repository, "rev-parse", "HEAD")
    result = application.sync_git_source(
        project_id="selected-checkout",
        source=str(source_repository),
        branch="main",
        expected_commit=expected_commit,
    )
    assert result["fast_forward_applied"] is True
    assert result["after"]["commit_sha"] == expected_commit
    assert result["changed_paths"] == ["README.md"]
    assert result["remote_write_performed"] is False
