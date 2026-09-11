from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from evidence_lane_plugin.service import EvidenceLaneService


def git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


@pytest.fixture
def source_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "book-faires"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Evidence Lane Test")
    git(repository, "config", "user.email", "evidence-lane@example.invalid")
    git(
        repository,
        "remote",
        "add",
        "origin",
        "https://github.com/example/book-faires.git",
    )
    (repository / "src").mkdir()
    (repository / "src" / "app.py").write_text(
        """
from flask import Flask

app = Flask(__name__)

@app.get("/books")
def list_books():
    return {"books": ["Dune"]}
""".lstrip(),
        encoding="utf-8",
    )
    (repository / "src" / "Counter.svelte").write_text(
        """
<script lang="ts">
  import { onMount } from "svelte";
  let count = 0;
  function increment() {
    count += 1;
  }
</script>

<button on:click={increment}>{count}</button>
""".lstrip(),
        encoding="utf-8",
    )
    (repository / "package.json").write_text(
        '{"dependencies":{"svelte":"^5.0.0"},"devDependencies":{"vite":"^7.0.0"}}\n',
        encoding="utf-8",
    )
    (repository / "README.md").write_text("# Book Faires\n", encoding="utf-8")
    (repository / "fixture.bin").write_bytes(b"\x00\x01\x02\xff")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "Initial fixture")
    return repository


@pytest.fixture
def service(tmp_path: Path, source_repository: Path) -> EvidenceLaneService:
    from evidence_lane_plugin.service import EvidenceLaneService

    application = EvidenceLaneService(data_root=tmp_path / "store")
    result = application.register_project(
        project_id="book-faires",
        display_name="Book Faires",
        repository_path=str(source_repository),
        expected_owner="example",
        expected_name="book-faires",
        allowed_branches=["main"],
        sensitivity="PRIVATE",
    )
    assert result["status"] == "PASS"
    return application


def boot_local(application: EvidenceLaneService) -> dict:
    return application.boot_session(
        project_id="book-faires",
        user_id="user-test",
        workspace_id="workspace-test",
        host="CODEX_DESKTOP",
        agent_id="codex-single-agent",
        sandbox_id="sandbox-local",
        ephemeral=False,
        runtime_context={"permission_mode": "test"},
        host_session_id="host-session-test",
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
    )


def work_handoff_destination_creation(
    source_task_id: str,
    destination_task_id: str,
) -> dict[str, object]:
    """Return the exact supported host creation receipt used by resume tests."""

    return {
        "schema": "evidence-lane.host-destination-creation.v1",
        "capability_status": "SUPPORTED",
        "host_action": "CONTINUE_IN_NEW_CHAT",
        "programmatic": True,
        "creation_count": 1,
        "source_task_id": source_task_id,
        "source_task_deep_link": f"codex://threads/{source_task_id}",
        "destination_task_id": destination_task_id,
        "destination_task_deep_link": (
            f"codex://threads/{destination_task_id}"
        ),
        "canonical_title_increment_verified": True,
        "host_continuity": {
            "schema": "evidence-lane.work-handoff-host-continuity.v1",
            "status": "PASS",
            "source_task_id": source_task_id,
            "source_task_deep_link": f"codex://threads/{source_task_id}",
            "destination_task_id": destination_task_id,
            "destination_task_deep_link": (
                f"codex://threads/{destination_task_id}"
            ),
            "initial_shell_source_task_id": source_task_id,
            "initial_shell_destination_task_id": destination_task_id,
            "host_creation_result_task_id": destination_task_id,
            "host_creation_result_deep_link": (
                f"codex://threads/{destination_task_id}"
            ),
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
            "plan_projection_source": (
                "CANONICAL_PLAN_LANE_NOT_THREAD_HISTORY"
            ),
            "collaboration_overlay_active": False,
            "observed_incidents": [],
            "live_canonical_title_task_ids": [destination_task_id],
            "title_used_as_identity": False,
            "cwd_used_as_identity": False,
        },
    }


def build_and_approve_pv1(application: EvidenceLaneService) -> tuple[str, dict]:
    boot = boot_local(application)
    session_id = boot["session"]["session_id"]
    candidate = application.build_initial("book-faires", session_id)
    decision = application.decide(
        "book-faires",
        session_id,
        decision="APPROVE",
        decided_by="human-test",
        decision_id="decision_pv1",
    )
    assert decision["pointer"]["accepted_pv"] == "PV1"
    assert "work_handoff" not in decision
    continued = application.sessions.begin_next_turn(
        "book-faires",
        session_id,
        continue_same_host=True,
        continuation_reason="EXPLICIT_USER_CONTINUATION",
    )
    assert continued["status"] == "PASS"
    return session_id, candidate
