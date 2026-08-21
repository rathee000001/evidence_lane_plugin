from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "push_github_app_exact_commit.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location("github_app_exact_push", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.stdout.strip()


def test_local_push_request_reproduces_exact_commit_tree_and_blob_delta(
    tmp_path,
) -> None:
    module = _load_script()
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Evidence Lane App")
    _git(
        repository,
        "config",
        "user.email",
        "evidence-lane@users.noreply.github.com",
    )
    (repository / "delete-me.txt").write_text("remove me\n", encoding="utf-8")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "delete-me.txt", "README.md")
    commit_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": "2026-08-21T12:00:00-04:00",
        "GIT_COMMITTER_DATE": "2026-08-21T12:00:00-04:00",
    }
    _git(repository, "commit", "-m", "base", env=commit_env)
    parent = _git(repository, "rev-parse", "HEAD")

    (repository / "delete-me.txt").unlink()
    workflow = repository / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: CI\n", encoding="utf-8")
    _git(repository, "add", "-A")
    _git(repository, "commit", "-m", "R249 exact test", env=commit_env)
    commit = _git(repository, "rev-parse", "HEAD")

    request = module.local_push_request(
        repository_root=repository,
        repository="owner/repo",
        branch="agent/evi-v300-systemwide-release-hil-v3.0.0",
        commit="HEAD",
        project_id="project-a",
        task_id="task-a",
        request_id="request-r249",
        idempotency_key="request-r249-idem",
    )

    assert request.expected_parent_commit_sha == parent
    assert request.expected_commit_sha == commit
    assert request.expected_parent_tree_sha == _git(
        repository, "rev-parse", f"{parent}^{{tree}}"
    )
    assert request.expected_tree_sha == _git(
        repository, "rev-parse", f"{commit}^{{tree}}"
    )
    assert request.author.date == "2026-08-21T12:00:00-04:00"
    assert request.committer.date == "2026-08-21T12:00:00-04:00"
    assert request.commit_message == "R249 exact test\n"
    assert [change.path for change in request.changes] == [
        ".github/workflows/ci.yml",
        "delete-me.txt",
    ]
    assert request.changes[0].operation == "UPSERT"
    assert request.changes[0].blob_sha == _git(
        repository,
        "rev-parse",
        f"{commit}:.github/workflows/ci.yml",
    )
    assert request.changes[1].operation == "DELETE"
    assert request.changes[1].blob_sha is None
