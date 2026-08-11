from __future__ import annotations

import json
import sqlite3
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
    assert goal["rows"] == [
        {
            "task_id": "delta-new",
            "step": "Replace the superseded bounded Delta.",
            "plan_sequence": 3,
            "lifecycle_status": "QUEUED",
            "steer_deltas": [],
            "number": 1,
            "status": "pending",
        }
    ]
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


def test_selected_git_sync_can_replace_but_never_broaden_branch_authority(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    checkout = tmp_path / "branch-replacement-checkout"
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
    application = EvidenceLaneService(data_root=tmp_path / "branch-replacement-store")
    application.register_project(
        project_id="branch-replacement",
        display_name="Branch replacement",
        repository_path=str(checkout),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    boot = application.boot_session(
        project_id="branch-replacement",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="branch-replacement-host-session",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    application.build_initial("branch-replacement", session_id)
    decision = application.decide(
        "branch-replacement",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="branch_replacement_pv1",
    )
    handoff = decision["state_travel_handoff"]["state_travel"]
    application.resume_state_travel(
        project_id="branch-replacement",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="branch-replacement-fresh-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-branch-replacement-task"},
    )
    application.sessions.classify(
        "branch-replacement",
        session_id,
        task_class="fix_bug",
        requested_outcome="Select one exact feature branch.",
        permitted_paths=["README.md"],
        permitted_tools=[
            "repository_read",
            "repository_write",
            "terminal",
            "test",
            "git_diff",
            "patch",
        ],
        acceptance_checks=["The exact feature branch becomes authoritative."],
        stop_condition="Stop at the next candidate HIL.",
    )

    from .conftest import git

    git(source_repository, "switch", "-c", "feature/exact-selection")
    readme = source_repository / "README.md"
    readme.write_text("# Book Faires\n\nExact branch selection.\n", encoding="utf-8")
    git(source_repository, "add", "README.md")
    git(source_repository, "commit", "-m", "Exact branch selection")
    expected_commit = git(source_repository, "rev-parse", "HEAD")
    git(
        checkout,
        "fetch",
        "--no-tags",
        str(source_repository),
        "feature/exact-selection",
    )
    git(checkout, "switch", "-c", "feature/exact-selection", "FETCH_HEAD")

    with pytest.raises(EvidenceLaneError) as blocked:
        application.sync_git_source(
            project_id="branch-replacement",
            source=str(source_repository),
            branch="feature/exact-selection",
            session_id=session_id,
            expected_commit=expected_commit,
        )
    assert blocked.value.code == "PROJECT_SYNC_BRANCH_NOT_AUTHORIZED"
    assert application.store.config("branch-replacement").allowed_branches == ["main"]

    # The explicit replacement flag is required. The public service envelope
    # converts the fail-closed exception only at MCP invocation time, so call
    # the service with the flag after proving the unflagged path did not mutate.
    result = application.sync_git_source(
        project_id="branch-replacement",
        source=str(source_repository),
        branch="feature/exact-selection",
        session_id=session_id,
        expected_commit=expected_commit,
        replace_registered_branch=True,
    )

    assert result["fast_forward_applied"] is False
    assert result["branch_authority"]["status"] == "REPLACED"
    assert result["branch_authority"]["authority_broadened"] is False
    assert result["branch_authority"]["prior_allowed_branches"] == ["main"]
    assert application.store.config("branch-replacement").allowed_branches == [
        "feature/exact-selection"
    ]
    assert result["branch_authority"]["receipt"]["pointer_generation"] == 1
    assert result["branch_authority"]["receipt"]["accepted_pv"] == "PV1"
    assert result["remote_write_performed"] is False


def test_dirty_local_branch_authority_replacement_preserves_exact_source(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    checkout = tmp_path / "dirty-branch-authority-checkout"
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
    application = EvidenceLaneService(data_root=tmp_path / "dirty-authority-store")
    application.register_project(
        project_id="dirty-branch-authority",
        display_name="Dirty branch authority",
        repository_path=str(checkout),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    boot = application.boot_session(
        project_id="dirty-branch-authority",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="dirty-authority-host-session",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )
    session_id = boot["session"]["session_id"]
    application.build_initial("dirty-branch-authority", session_id)
    decision = application.decide(
        "dirty-branch-authority",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="dirty_branch_authority_pv1",
    )
    handoff = decision["state_travel_handoff"]["state_travel"]
    application.resume_state_travel(
        project_id="dirty-branch-authority",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="dirty-authority-fresh-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "dirty-authority-test"},
    )
    application.sessions.classify(
        "dirty-branch-authority",
        session_id,
        task_class="modify_code",
        requested_outcome="Select the exact dirty continuity branch.",
        permitted_paths=["README.md", "scratch.txt"],
        permitted_tools=[
            "repository_read",
            "repository_write",
            "terminal",
            "test",
            "git_diff",
            "patch",
        ],
        acceptance_checks=[
            "The branch authority changes without changing source bytes."
        ],
        stop_condition="Stop at the next candidate HIL.",
    )

    from .conftest import git

    exact_branch = "feature/dirty-continuity"
    git(checkout, "switch", "-c", exact_branch)
    expected_commit = git(checkout, "rev-parse", "HEAD")
    readme = checkout / "README.md"
    readme.write_text("# Book Faires\n\nDirty continuity bytes.\n", encoding="utf-8")
    scratch = checkout / "scratch.txt"
    scratch.write_text("untracked continuity bytes\n", encoding="utf-8")
    before_status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    before_readme = readme.read_bytes()
    before_scratch = scratch.read_bytes()

    with pytest.raises(EvidenceLaneError) as missing_commit:
        application.sync_git_source(
            project_id="dirty-branch-authority",
            source=str(checkout),
            branch=exact_branch,
            session_id=session_id,
            replace_registered_branch=True,
        )
    assert (
        missing_commit.value.code
        == "DIRTY_BRANCH_AUTHORITY_EXPECTED_COMMIT_REQUIRED"
    )
    assert application.store.config("dirty-branch-authority").allowed_branches == [
        "main"
    ]

    result = application.sync_git_source(
        project_id="dirty-branch-authority",
        source=str(checkout),
        branch=exact_branch,
        session_id=session_id,
        expected_commit=expected_commit,
        replace_registered_branch=True,
    )

    after_status = subprocess.run(
        ["git", "-C", str(checkout), "status", "--porcelain=v1", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    assert result["operation"] == "DIRTY_LOCAL_BRANCH_AUTHORITY_ONLY"
    assert result["fast_forward_applied"] is False
    assert result["changed_paths"] == []
    assert result["dirty_worktree_preserved"] is True
    assert result["fetch_performed"] is False
    assert result["source_write_performed"] is False
    assert result["remote_write_performed"] is False
    assert result["merge_commit_created"] is False
    assert result["before"] == result["after"]
    assert before_status == after_status
    assert readme.read_bytes() == before_readme
    assert scratch.read_bytes() == before_scratch
    assert result["branch_authority"]["status"] == "REPLACED"
    receipt = result["branch_authority"]["receipt"]
    context = receipt["selection_context"]
    assert context["selection_mode"] == "DIRTY_LOCAL_BRANCH_AUTHORITY_ONLY"
    assert context["expected_commit"] == expected_commit
    assert context["dirty_worktree_preserved"] is True
    assert context["fetch_performed"] is False
    assert context["source_write_performed"] is False
    assert context["worktree_status_sha256"] == result["worktree_status_sha256"]
    assert application.store.config("dirty-branch-authority").allowed_branches == [
        exact_branch
    ]


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
