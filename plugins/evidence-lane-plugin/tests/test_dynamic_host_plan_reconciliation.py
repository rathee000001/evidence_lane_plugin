from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.hashing import (
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from evidence_lane_plugin.host_plan_reconciliation import (
    HOST_PLAN_DRY_RUN_SCHEMA,
    HOST_PLAN_SNAPSHOT_SCHEMA,
    UNIVERSAL_DELTA_IMPLEMENTATION_PURGE_LAW,
    build_dynamic_reconciliation_candidate,
    build_task35_downstream_dry_run,
    create_dynamic_host_snapshot,
)
from evidence_lane_plugin.plan_runtime import (
    ensure_event_ledger,
    write_plan_runtime_projection,
)
from evidence_lane_plugin.store import ProjectStore

PROJECT_ID = "dynamic-plan-test"
RECORDED_AT = "2026-09-02T19:00:00Z"


def _task(
    task_id: str,
    sequence: int,
    status: str,
    *,
    panel_role: str = "STANDARD",
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "sequence": sequence,
        "plan_id": "SOURCE_PLAN",
        "task_class": "modify_code",
        "requested_outcome": f"Original contract for {task_id}",
        "permitted_paths": [],
        "permitted_tools": [],
        "acceptance_checks": ["SOURCE_BOUND"],
        "stop_condition": "Stop on mismatch.",
        "panel_role": panel_role,
        "plan_group": "SOURCE",
        "commit_batch_id": "NONE",
        "dependencies": [],
        "git_commit_stage": "NONE",
        "status": status,
        "planned_at": RECORDED_AT,
        "history": [],
    }


def _task35_backlog() -> dict[str, object]:
    tasks: list[dict[str, object]] = [_task("PREFIX", 1, "DONE")]
    tasks.append(_task("ACTIVE", 2, "ACTIVE"))
    for ordinal in range(1, 15):
        tasks.append(_task(f"OLD-PRE-{ordinal:02d}", len(tasks) + 1, "QUEUED"))
    tasks.append(_task("CURRENT-HIL", len(tasks) + 1, "QUEUED", panel_role="HIL_GATE"))
    for ordinal in range(15, 25):
        tasks.append(_task(f"OLD-POST-{ordinal:02d}", len(tasks) + 1, "QUEUED"))
    tasks.append(
        _task(
            "FINAL-HIL",
            len(tasks) + 1,
            "QUEUED",
            panel_role="PHYSICALLY_FINAL_HIL",
        )
    )
    backlog: dict[str, object] = {
        "schema": "evidence-lane.linear-task-backlog.v1",
        "project_id": PROJECT_ID,
        "goal_row_offset": 80,
        "plans": [
            {
                "plan_id": "SOURCE_PLAN",
                "planned_by": "test",
                "planned_at": RECORDED_AT,
                "input_sha256": "A" * 64,
                "task_ids": [str(item["task_id"]) for item in tasks],
            }
        ],
        "tasks": tasks,
    }
    ensure_event_ledger(backlog)
    return backlog


def _write_test_project(control_root: Path, backlog: dict[str, object]) -> Path:
    project_root = control_root / "projects" / PROJECT_ID
    plan_root = project_root / "sectors" / "plan"
    plan_root.mkdir(parents=True)
    atomic_write_json(
        project_root / "project.json",
        {
            "project_id": PROJECT_ID,
            "display_name": "Dynamic Plan Test",
            "repository_path": str(control_root / "repository"),
            "expected_owner": "owner",
            "expected_name": "repository",
            "allowed_branches": ["main"],
        },
    )
    atomic_write_json(plan_root / "task_backlog.json", backlog)
    write_plan_runtime_projection(plan_root / "plan_runtime_projection.sqlite", backlog)
    (project_root / "ai_learning").mkdir()
    (project_root / "ai_learning" / "agent-learning.sqlite").write_bytes(
        b"current-learning-authority"
    )
    (project_root / "active_pointer.json").write_text(
        '{"accepted_pv":"PV12","generation":12}\n', encoding="utf-8"
    )
    return project_root


def _three_row_snapshot() -> dict[str, object]:
    rows = [
        {
            "host_row_id": "DYN-001",
            "task_id": "ACTIVE",
            "display_text": "Continue the active boundary.",
            "status": "in_progress",
            "panel_role": "STANDARD",
            "phase": "ACTIVE",
            "path_scope": [],
            "workflow_gates": ["ACTIVE_ONLY"],
            "preserve_existing_identity": True,
        },
        {
            "host_row_id": "DYN-002",
            "task_id": "CURRENT-HIL",
            "display_text": "Display the exact current HIL projection.",
            "canonical_requested_outcome": "Original contract for CURRENT-HIL",
            "status": "pending",
            "panel_role": "HIL_GATE",
            "phase": "CURRENT_HIL",
            "path_scope": [],
            "workflow_gates": ["EXPLICIT_HIL"],
            "preserve_existing_identity": True,
            "preserve_canonical_contract": True,
        },
        {
            "host_row_id": "DYN-003",
            "task_id": "FINAL-HIL",
            "display_text": "Display the physical-final boundary.",
            "canonical_requested_outcome": "Original contract for FINAL-HIL",
            "status": "pending",
            "panel_role": "PHYSICALLY_FINAL_HIL",
            "phase": "FINAL_HIL",
            "path_scope": [],
            "workflow_gates": ["PHYSICALLY_FINAL"],
            "preserve_existing_identity": True,
            "preserve_canonical_contract": True,
        },
    ]
    return create_dynamic_host_snapshot(
        source_authority_id="PLAN_LANE:TEST",
        anchor_task_id="ACTIVE",
        rows=rows,
        captured_at=RECORDED_AT,
    )


def test_dynamic_task35_dry_run_is_plan_owned_and_has_no_state_travel() -> None:
    backlog = _task35_backlog()
    snapshot, candidate, receipt = build_task35_downstream_dry_run(
        backlog,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at=RECORDED_AT,
    )

    assert snapshot["schema"] == HOST_PLAN_SNAPSHOT_SCHEMA
    assert snapshot["canonical_authority"] == "PLAN_LANE"
    assert snapshot["host_projection_only"] is True
    assert snapshot["bootstrap_projection_mode"] == "FULL_NATIVE_BOOTSTRAP_LIST_ONLY"
    assert snapshot["post_activation_projection_mode"] == (
        "COMPACT_NATIVE_PLAN_WINDOW"
    )
    assert snapshot["native_changes_behavior"] == "ORDINARY_CODEX_CHANGES_UNMODIFIED"
    assert snapshot["goal_projection"]["duplicates_plan_rows"] is False
    assert "final effective Plan row" in snapshot["goal_projection"]["summary"]
    assert "ordinary or HIL" in snapshot["goal_projection"]["summary"]
    assert snapshot["row_count"] == 19
    assert snapshot["retained_host_row_count"] == 0
    assert snapshot["host_authority_total"] == 19
    assert receipt["schema"] == HOST_PLAN_DRY_RUN_SCHEMA
    assert receipt["host_row_count"] == 19
    assert receipt["dynamic_membership_count"] == 19
    full_projection = receipt["current_full_host_projection_contract"]
    assert full_projection["row_count"] == 19
    assert full_projection["available_immediately_after_plan_promotion"] is True
    assert full_projection["independent_of_plugin_install_or_restart"] is True
    assert full_projection["steps"][0]["status"] == "in_progress"
    assert all(
        row["status"] == "pending" for row in full_projection["steps"][1:]
    )
    assert full_projection["post_install_switch"].startswith("NATIVE_ONE_")
    assert receipt["replacement_count"] == 25
    assert receipt["drop_count"] == 0
    assert receipt["state_travel_executable"] is False
    assert receipt["post_activation_projection_contract"].startswith(
        "ONE_CONTINUITY_HEADER"
    )
    assert receipt["intermediate_publication_paths_changed"] is False
    assert receipt["intermediate_required_checks"] == [
        "CODE_CI",
        "PACKAGE_CI",
        "SECURITY",
        "CODEQL",
        "GITHUB_APP_CI",
    ]
    assert receipt["intermediate_path_scoped_not_triggered"] == [
        "GITHUB_DOCS_PUBLICATION",
        "GITHUB_PAGES",
        "VERCEL_PAGES",
    ]
    assert receipt["deferred_publication_workflows"] == [
        "GITHUB_DOCS_PUBLICATION",
        "GITHUB_PAGES",
        "VERCEL_PAGES",
    ]
    assert [row["canonical_row"] for row in receipt["proposed_rows"]] == list(
        range(82, 101)
    )
    assert receipt["proposed_rows"][-4]["task_id"] == (
        "T35-NEXT-GOAL_METRIC_RECONCILIATION"
    )
    assert "RESET_AWARE_SEGMENTS" in receipt["proposed_rows"][-4][
        "workflow_gates"
    ]
    assert receipt["proposed_rows"][-3]["panel_role"] == "HIL_GATE"
    assert receipt["proposed_rows"][-1]["panel_role"] == "STANDARD"
    assert receipt["proposed_rows"][-1]["task_id"] == (
        "T35-NEXT-POST_HIL_PUBLICATION_PLAN_INSERTION"
    )
    assert receipt["proposed_rows"][0]["supersedes_task_ids"] == []
    assert receipt["proposed_rows"][-3]["supersedes_task_ids"] == []
    assert len(receipt["proposed_rows"][-2]["supersedes_task_ids"]) == 1
    assert len(receipt["proposed_rows"][-1]["supersedes_task_ids"]) == 10
    assert "FINAL-HIL" in receipt["proposed_rows"][-1]["supersedes_task_ids"]
    assert snapshot["physical_final_task_id"] is None

    for row in receipt["proposed_rows"]:
        if row["panel_role"] == "HIL_GATE":
            assert UNIVERSAL_DELTA_IMPLEMENTATION_PURGE_LAW not in row["display_text"]
        else:
            assert UNIVERSAL_DELTA_IMPLEMENTATION_PURGE_LAW in row["display_text"]

    tasks = {str(task["task_id"]): task for task in candidate["tasks"]}
    for new_id in receipt["membership_task_ids"]:
        task = tasks[new_id]
        if task["panel_role"] == "HIL_GATE":
            assert "CURRENT_ROW_OWNER_ONLY" not in task["acceptance_checks"]
        else:
            assert "CURRENT_ROW_OWNER_ONLY" in task["acceptance_checks"]
            assert (
                "SUPERSEDED_EXECUTABLE_CONSUMERS_DIRECTLY_PURGED"
                in task["acceptance_checks"]
            )
        for old_id in task.get("supersedes_task_ids") or []:
            assert tasks[old_id]["status"] == "SUPERSEDED"
            assert tasks[old_id]["superseded_by_task_id"] == new_id
            assert old_id in tasks[new_id]["supersedes_task_ids"]


def test_manifest_membership_prevents_the_209_vs_213_style_undercount() -> None:
    rows: list[dict[str, object]] = []
    for ordinal in range(1, 8):
        mapped_role = {
            4: ("ACTIVE-ANCHOR", "STANDARD", "in_progress"),
            6: ("CURRENT-HIL", "HIL_GATE", "pending"),
            7: ("FINAL-HIL", "PHYSICALLY_FINAL_HIL", "pending"),
        }.get(ordinal)
        if mapped_role:
            task_id, panel_role, status = mapped_role
            preserve = True
        else:
            task_id = f"HOST-{ordinal:03d}"
            panel_role = "STANDARD"
            status = "completed" if ordinal < 4 else "pending"
            preserve = False
        rows.append(
            {
                "host_row_id": f"ROW-{ordinal:03d}",
                "task_id": task_id,
                "display_text": f"Projected Plan row {ordinal}",
                "status": status,
                "panel_role": panel_role,
                "phase": "BOOTSTRAP_PROJECTION",
                "path_scope": [],
                "workflow_gates": ["PLAN_MANIFEST_MEMBERSHIP"],
                "preserve_existing_identity": preserve,
                "preserve_canonical_contract": panel_role
                in {"HIL_GATE", "PHYSICALLY_FINAL_HIL"},
            }
        )
    snapshot = create_dynamic_host_snapshot(
        source_authority_id="PLAN_LANE:MEMBERSHIP_REGRESSION",
        anchor_task_id="ACTIVE-ANCHOR",
        rows=rows,
        captured_at=RECORDED_AT,
    )

    membership = list(snapshot["rows"])
    mapped_count = sum(row["preserve_existing_identity"] for row in membership)
    prefixed_count = sum(
        str(row["task_id"]).startswith("HOST-") for row in membership
    )
    prefixed_strictly_after_first_count = prefixed_count - 1

    assert len(membership) == snapshot["row_count"]
    assert prefixed_count == len(membership) - mapped_count
    assert prefixed_strictly_after_first_count == (
        len(membership) - mapped_count - 1
    )
    assert prefixed_strictly_after_first_count != snapshot["row_count"]
    assert snapshot["hil_mappings"] == [
        {
            "host_row_id": "ROW-006",
            "task_id": "CURRENT-HIL",
            "panel_role": "HIL_GATE",
        },
        {
            "host_row_id": "ROW-007",
            "task_id": "FINAL-HIL",
            "panel_role": "PHYSICALLY_FINAL_HIL",
        },
    ]
    assert [row["status"] for row in membership] == [
        "completed",
        "completed",
        "completed",
        "in_progress",
        "pending",
        "pending",
        "pending",
    ]


def test_task35_dynamic_plan_can_be_refreshed_without_self_supersession() -> None:
    backlog = _task35_backlog()
    _, first_candidate, first_receipt = build_task35_downstream_dry_run(
        backlog,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at=RECORDED_AT,
    )
    snapshot, second_candidate, second_receipt = build_task35_downstream_dry_run(
        first_candidate,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at="2026-09-02T20:00:00Z",
    )

    assert first_receipt["host_row_count"] == 19
    assert second_receipt["host_row_count"] == 19
    assert second_receipt["new_insertion_count"] == 0
    assert second_receipt["replacement_count"] == 0
    assert snapshot["physical_final_task_id"] is None
    effective = [
        task
        for task in second_candidate["tasks"]
        if task["status"] in {"DONE", "ACCEPTED", "ACTIVE", "QUEUED"}
    ]
    dynamic_ids = [
        str(task["task_id"])
        for task in effective
        if dict(task.get("host_step_projection") or {}).get("host_row_number")
    ]
    assert len(dynamic_ids) == len(set(dynamic_ids)) == 19
    assert not any(
        task_id in (task.get("supersedes_task_ids") or [])
        for task_id, task in (
            (str(item["task_id"]), item) for item in second_candidate["tasks"]
        )
    )


def test_verified_dynamic_checkpoint_preserves_existing_candidate(
    tmp_path: Path,
) -> None:
    backlog = _task35_backlog()
    _, candidate, _ = build_task35_downstream_dry_run(
        backlog,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at=RECORDED_AT,
    )
    effective = [
        task
        for task in sorted(candidate["tasks"], key=lambda item: int(item["sequence"]))
        if task["status"] in {"DONE", "ACCEPTED", "ACTIVE", "QUEUED"}
    ]
    active = next(task for task in effective if task["status"] == "ACTIVE")
    successor = effective[effective.index(active) + 1]
    session_id = "session_preserved_candidate"
    active["active_session_id"] = session_id
    active["runtime_task_id"] = "runtime_active"
    project_root = _write_test_project(tmp_path, candidate)
    atomic_write_json(project_root / "task_backlog.json", candidate)
    write_plan_runtime_projection(
        project_root / "plan_runtime_projection.sqlite", candidate
    )
    session_path = project_root / "sessions" / f"{session_id}.json"
    atomic_write_json(
        session_path,
        {
            "session_id": session_id,
            "state": "PVN1_CANDIDATE",
            "candidate_id": "PV13_PENDING",
            "metadata": {
                "active_backlog_task_id": active["task_id"],
                "active_backlog_task_status": "ACTIVE",
            },
        },
    )
    proof_body = {
        "schema": "evidence-lane.direct-source-delta-verification.v1",
        "status": "PASS",
        "project_id": PROJECT_ID,
        "active_task_id": active["task_id"],
        "successor_task_id": successor["task_id"],
        "source_tests": ["PASS"],
        "candidate_created": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    proof = {
        **proof_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(proof_body)),
    }
    receipt_body = {
        "schema": "evidence-lane.verified-task-checkpoint-advance.v1",
        "project_id": PROJECT_ID,
        "session_id": session_id,
        "verification_kind": "DIRECT_SOURCE_DELTA_VERIFICATION",
        "completed_backlog_task_id": active["task_id"],
        "replacement_backlog_task_id": successor["task_id"],
        "prior_runtime_task_id": "runtime_active",
        "replacement_runtime_task_id": "runtime_successor",
        "accepted_pv": "PV12",
        "pointer_generation": 12,
        "verification_proof": proof,
        "candidate_created": False,
        "pending_hil": True,
        "existing_candidate_preserved": {
            "preserved": True,
            "session_id": session_id,
            "candidate_id": "PV13_PENDING",
            "session_file_sha256": sha256_file(session_path),
        },
        "pointer_moved": False,
        "hil_inferred": False,
    }
    receipt = {
        **receipt_body,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt_body)),
    }
    replacement_contract = {
        key: successor[key]
        for key in (
            "task_class",
            "requested_outcome",
            "permitted_paths",
            "permitted_tools",
            "acceptance_checks",
            "stop_condition",
        )
    }
    replacement_contract["task_id"] = "runtime_successor"
    session_sha256 = sha256_file(session_path)
    pointer_sha256 = sha256_file(project_root / "active_pointer.json")
    persisted = json.loads(
        (project_root / "task_backlog.json").read_text(encoding="utf-8")
    )
    persisted_ids = {str(task["task_id"]) for task in persisted["tasks"]}
    assert str(active["task_id"]) in persisted_ids
    assert str(successor["task_id"]) in persisted_ids

    result = ProjectStore(tmp_path).advance_verified_task_checkpoint(
        PROJECT_ID,
        completed_backlog_task_id=str(active["task_id"]),
        replacement_backlog_task_id=str(successor["task_id"]),
        session_id=session_id,
        prior_runtime_task_id="runtime_active",
        replacement_contract=replacement_contract,
        completion_receipt=receipt,
    )

    assert result["status"] == "PASS"
    assert result["completed_task"]["status"] == "DONE"
    assert result["active_task"]["status"] == "ACTIVE"
    assert result["completed_task"]["host_step_projection"]["host_status"] == (
        "completed"
    )
    assert result["active_task"]["host_step_projection"]["host_status"] == (
        "in_progress"
    )
    assert result["host_projection_repaired"] is True
    assert result["sub_pv_acceptance"]["state"] == (
        "AUTO_ACCEPTED_DELTA_ROW_WORK"
    )
    assert sha256_file(session_path) != session_sha256
    assert sha256_file(project_root / "active_pointer.json") == pointer_sha256
    rebound_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert rebound_session["state"] == "PVN1_CANDIDATE"
    assert rebound_session["candidate_id"] == "PV13_PENDING"
    assert rebound_session["metadata"]["active_backlog_task_id"] == successor["task_id"]
    assert rebound_session["metadata"]["active_backlog_task_status"] == "ACTIVE"
    assert rebound_session["metadata"]["last_task_checkpoint_advance"] == receipt
    assert result["session_binding_repaired"] is True
    assert result["session_binding"]["candidate_preserved"] is True
    assert result["session_binding"]["candidate_id"] == "PV13_PENDING"
    assert result["host_plan_window_repaired"] is True
    assert successor["task_id"] in result["host_plan_window_task_ids"]
    assert rebound_session["metadata"]["host_plan_window"][
        "window_task_ids"
    ] == result["host_plan_window_task_ids"]
    assert rebound_session["metadata"]["host_plan_window"][
        "binding_source"
    ] == "VERIFIED_TASK_CHECKPOINT_TRANSITION"

    rebound_sha256 = sha256_file(session_path)
    replay = ProjectStore(tmp_path).advance_verified_task_checkpoint(
        PROJECT_ID,
        completed_backlog_task_id=str(active["task_id"]),
        replacement_backlog_task_id=str(successor["task_id"]),
        session_id=session_id,
        prior_runtime_task_id="runtime_active",
        replacement_contract=replacement_contract,
        completion_receipt=receipt,
    )
    assert replay["status"] == "PASS"
    assert replay["idempotent_reuse"] is True
    assert replay["session_binding_repaired"] is False
    assert replay["host_plan_window_repaired"] is False
    assert sha256_file(session_path) == rebound_sha256
    replay_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert len(replay_session["metadata"]["task_checkpoint_session_bindings"]) == 1
    assert len(replay_session["metadata"]["task_checkpoint_advances"]) == 1


def test_drop_requires_hash_bound_receipt_and_is_not_effective(tmp_path: Path) -> None:
    backlog = _task35_backlog()
    # Reduce the active suffix to active -> explicit drop -> current HIL -> final HIL.
    keep = {"PREFIX", "ACTIVE", "OLD-PRE-01", "CURRENT-HIL", "FINAL-HIL"}
    backlog["tasks"] = [
        task for task in backlog["tasks"] if str(task["task_id"]) in keep
    ]
    for sequence, task in enumerate(backlog["tasks"], start=1):
        task["sequence"] = sequence
    backlog["plans"][0]["task_ids"] = [
        str(task["task_id"]) for task in backlog["tasks"]
    ]
    backlog["events"] = []
    backlog["event_head_sha256"] = None
    ensure_event_ledger(backlog)
    snapshot = _three_row_snapshot()
    reason_sha256 = sha256_bytes(b"intentionally removed from executable work")
    candidate, receipt = build_dynamic_reconciliation_candidate(
        backlog,
        snapshot=snapshot,
        project_id=PROJECT_ID,
        actor="test",
        plan_id="DROP_TEST",
        drop_contracts=[
            {"task_id": "OLD-PRE-01", "reason_sha256": reason_sha256}
        ],
        recorded_at=RECORDED_AT,
    )
    assert receipt["drop_count"] == 1
    dropped = next(
        task for task in candidate["tasks"] if task["task_id"] == "OLD-PRE-01"
    )
    assert dropped["status"] == "DROPPED"
    assert len(str(dropped["drop_receipt_sha256"])) == 64

    runtime = tmp_path / "runtime.sqlite"
    write_plan_runtime_projection(runtime, candidate)
    with sqlite3.connect(runtime) as connection:
        row = connection.execute(
            """
            SELECT projection_lane,effective_for_execution,lifecycle_status
            FROM plan_execution_row WHERE task_id='OLD-PRE-01'
            """
        ).fetchone()
    assert row == ("HISTORY", 0, "DROPPED")


def test_dynamic_membership_can_increase_without_reconstructing_goal() -> None:
    backlog = _task35_backlog()
    keep = {"PREFIX", "ACTIVE", "CURRENT-HIL", "FINAL-HIL"}
    backlog["tasks"] = [
        task for task in backlog["tasks"] if str(task["task_id"]) in keep
    ]
    for sequence, task in enumerate(backlog["tasks"], start=1):
        task["sequence"] = sequence
    backlog["plans"][0]["task_ids"] = [
        str(task["task_id"]) for task in backlog["tasks"]
    ]
    backlog["events"] = []
    backlog["event_head_sha256"] = None
    ensure_event_ledger(backlog)
    base_rows = list(_three_row_snapshot()["rows"])
    base_rows.insert(
        2,
        {
            "host_row_id": "DYN-INSERTED",
            "task_id": "INSERTED",
            "display_text": "One Plan-owned atomic insertion.",
            "status": "pending",
            "panel_role": "STANDARD",
            "phase": "INSERTED",
            "path_scope": [],
            "workflow_gates": ["PLAN_ATOMIC_INSERT"],
            "preserve_existing_identity": False,
            "preserve_canonical_contract": False,
            "supersedes_task_ids": [],
        },
    )
    snapshot = create_dynamic_host_snapshot(
        source_authority_id="PLAN_LANE:INCREASE_TEST",
        anchor_task_id="ACTIVE",
        rows=base_rows,
        captured_at=RECORDED_AT,
    )
    candidate, receipt = build_dynamic_reconciliation_candidate(
        backlog,
        snapshot=snapshot,
        project_id=PROJECT_ID,
        actor="test",
        plan_id="INCREASE_TEST",
        recorded_at=RECORDED_AT,
    )

    assert receipt["host_row_count"] == 4
    assert receipt["new_insertion_count"] == 1
    assert receipt["goal_projection"]["duplicates_plan_rows"] is False
    assert any(task["task_id"] == "INSERTED" for task in candidate["tasks"])


def test_legacy_many_to_one_supersession_is_repaired_without_status_change() -> None:
    backlog = _task35_backlog()
    legacy_successor = _task("LEGACY-SUCCESSOR", 1, "DONE")
    legacy_successor["supersedes_task_id"] = "LEGACY-A"
    legacy_a = _task("LEGACY-A", 2, "SUPERSEDED")
    legacy_a["superseded_by_task_id"] = "LEGACY-SUCCESSOR"
    legacy_b = _task("LEGACY-B", 3, "SUPERSEDED")
    legacy_b["superseded_by_task_id"] = "LEGACY-SUCCESSOR"
    legacy_b["supersedes_task_id"] = "LEGACY-SUCCESSOR"
    backlog["tasks"] = [legacy_successor, legacy_a, legacy_b, *backlog["tasks"]]
    for sequence, task in enumerate(backlog["tasks"], start=1):
        task["sequence"] = sequence
    backlog["plans"][0]["task_ids"] = [
        str(task["task_id"]) for task in backlog["tasks"]
    ]
    backlog["events"] = []
    backlog["event_head_sha256"] = None
    ensure_event_ledger(backlog)

    _, candidate, receipt = build_task35_downstream_dry_run(
        backlog,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at=RECORDED_AT,
    )
    tasks = {str(task["task_id"]): task for task in candidate["tasks"]}

    assert receipt["status"] == "DRY_RUN_PASS"
    assert receipt["history_linkage_repair_count"] == 2
    assert receipt["supersession_link_issues"] == []
    assert tasks["LEGACY-A"]["status"] == "SUPERSEDED"
    assert tasks["LEGACY-B"]["status"] == "SUPERSEDED"
    assert tasks["LEGACY-SUCCESSOR"]["status"] == "DONE"
    assert set(tasks["LEGACY-SUCCESSOR"]["supersedes_task_ids"]) == {
        "LEGACY-A",
        "LEGACY-B",
    }
    assert tasks["LEGACY-B"]["supersedes_task_id"] is None


def test_store_dry_run_is_read_only_and_rebinds_current_protected_hashes(
    tmp_path: Path,
) -> None:
    backlog = _task35_backlog()
    project_root = _write_test_project(tmp_path, backlog)
    store = ProjectStore(tmp_path)
    snapshot, _, _ = build_task35_downstream_dry_run(
        backlog,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at=RECORDED_AT,
    )
    backlog_path = project_root / "sectors" / "plan" / "task_backlog.json"
    runtime_path = project_root / "sectors" / "plan" / "plan_runtime_projection.sqlite"
    before = (sha256_file(backlog_path), sha256_file(runtime_path))
    result = store.reconcile_dynamic_host_plan(
        PROJECT_ID,
        snapshot=snapshot,
        actor="test",
        expected_backlog_sha256=sha256_bytes(canonical_json_bytes(backlog)),
        expected_runtime_sha256=sha256_file(runtime_path),
        protected_paths=[
            "active_pointer.json",
            "ai_learning/agent-learning.sqlite",
        ],
        dry_run=True,
        recorded_at=RECORDED_AT,
    )
    after = (sha256_file(backlog_path), sha256_file(runtime_path))

    assert before == after
    assert result["protected_file_sha256s_rebound_at_start"] == {
        "active_pointer.json": sha256_file(project_root / "active_pointer.json"),
        "ai_learning/agent-learning.sqlite": sha256_file(
            project_root / "ai_learning" / "agent-learning.sqlite"
        ),
    }
    assert not (
        project_root
        / "sectors"
        / "plan"
        / "plan_normalization"
        / "dynamic_host_reconciliation"
    ).exists()


def test_store_transaction_recovers_after_backlog_promotion(tmp_path: Path) -> None:
    backlog = _task35_backlog()
    project_root = _write_test_project(tmp_path, backlog)
    store = ProjectStore(tmp_path)
    snapshot, _, _ = build_task35_downstream_dry_run(
        backlog,
        project_id=PROJECT_ID,
        source_authority_id="PLAN_LANE:TASK35",
        actor="test",
        captured_at=RECORDED_AT,
    )
    kwargs = {
        "snapshot": snapshot,
        "actor": "test",
        "expected_backlog_sha256": sha256_bytes(canonical_json_bytes(backlog)),
        "expected_runtime_sha256": sha256_file(
            project_root / "sectors" / "plan" / "plan_runtime_projection.sqlite"
        ),
        "protected_paths": [
            "active_pointer.json",
            "ai_learning/agent-learning.sqlite",
        ],
        "dry_run": False,
        "recorded_at": RECORDED_AT,
    }
    with pytest.raises(
        RuntimeError, match="INJECTED_HOST_PLAN_FAILURE_AFTER_BACKLOG_PROMOTED"
    ):
        store.reconcile_dynamic_host_plan(
            PROJECT_ID,
            **kwargs,
            _fault_after_phase="BACKLOG_PROMOTED",
        )

    result = store.reconcile_dynamic_host_plan(PROJECT_ID, **kwargs)
    assert result["journal_state"] == "COMMITTED"
    assert result["recovered"] is True
    assert sha256_file(
        project_root / "sectors" / "plan" / "task_backlog.json"
    ) == result["backlog_sha256"]
    assert sha256_file(
        project_root / "sectors" / "plan" / "plan_runtime_projection.sqlite"
    ) == result["runtime_sha256"]
    with sqlite3.connect(
        project_root / "sectors" / "plan" / "plan_runtime_projection.sqlite"
    ) as connection:
        invalid = connection.execute(
            """
            SELECT COUNT(*) FROM plan_execution_row
            WHERE lifecycle_status IN ('SUPERSEDED','DROPPED')
              AND effective_for_execution != 0
            """
        ).fetchone()[0]
        final = connection.execute(
            """
            SELECT panel_role FROM plan_execution_row
            WHERE effective_for_execution=1 ORDER BY row_number DESC LIMIT 1
            """
        ).fetchone()[0]
    assert invalid == 0
    assert final == "STANDARD"


def test_snapshot_rejects_host_authority_claim() -> None:
    with pytest.raises(Exception, match="canonical Plan Lane alone"):
        create_dynamic_host_snapshot(
            source_authority_id="HOST_UI",
            anchor_task_id="ACTIVE",
            rows=list(_three_row_snapshot()["rows"]),
            captured_at=RECORDED_AT,
        )
