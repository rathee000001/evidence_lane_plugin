from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
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
    )


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
    return session_id, candidate
