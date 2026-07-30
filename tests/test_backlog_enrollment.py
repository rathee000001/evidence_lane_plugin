from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
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

    idempotent = service.plan_tasks(
        "book-faires",
        tasks=tasks,
        planned_by="human-test",
        plan_id="plan-linear-001",
    )
    assert len(idempotent["tasks"]) == 2

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

    outcome = service.store.record_backlog_outcome(
        "book-faires",
        backlog_task_id="delta-001",
        session_id="session_test",
        decision="APPROVE",
        candidate_id="PV1_CANDIDATE__RUN_TEST",
        accepted_pv="PV1",
    )
    assert outcome["status"] == "COMPLETED_ACCEPTED"
    assert service.task_backlog("book-faires")["counts"] == {
        "COMPLETED_ACCEPTED": 1,
        "QUEUED": 1,
    }


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


def test_selected_git_sync_records_active_session_lineage(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    checkout = tmp_path / "active-selected-checkout"
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
    application = EvidenceLaneService(data_root=tmp_path / "active-sync-store")
    application.register_project(
        project_id="active-selected-checkout",
        display_name="Active selected checkout",
        repository_path=str(checkout),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    boot = application.boot_session(
        project_id="active-selected-checkout",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="active-sync-host-session",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    application.build_initial("active-selected-checkout", session_id)
    decision = application.decide(
        "active-selected-checkout",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="active_sync_pv1",
    )
    handoff = decision["state_travel_handoff"]["state_travel"]
    application.resume_state_travel(
        project_id="active-selected-checkout",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="active-sync-fresh-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-active-sync-task"},
    )
    application.sessions.classify(
        "active-selected-checkout",
        session_id,
        task_class="fix_bug",
        requested_outcome="Apply the selected branch update.",
        permitted_paths=["README.md"],
        permitted_tools=[
            "repository_read",
            "repository_write",
            "terminal",
            "test",
            "git_diff",
            "patch",
        ],
        acceptance_checks=["The selected README change is present."],
        stop_condition="Stop at the next candidate HIL.",
    )

    readme = source_repository / "README.md"
    readme.write_text(
        "# Book Faires\n\nActive selected branch update.\n",
        encoding="utf-8",
    )
    from .conftest import git

    git(source_repository, "add", "README.md")
    git(source_repository, "commit", "-m", "Active selected update")
    expected_commit = git(source_repository, "rev-parse", "HEAD")

    result = application.sync_git_source(
        project_id="active-selected-checkout",
        source=str(source_repository),
        branch="main",
        session_id=session_id,
        expected_commit=expected_commit,
    )

    assert result["fast_forward_applied"] is True
    assert result["activity"]["event_type"] == "task.git.fast_forward"
    assert (
        application.sessions.load(
            "active-selected-checkout",
            session_id,
        ).metadata["source_state"]
        == "MUTATED_AFTER_ENTRY"
    )
