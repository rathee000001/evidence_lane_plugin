from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "fast_forward_github_app_feature_to_main.py"
)
TOMBSTONE = SCRIPT.with_name("merge_github_app_feature_to_main.py")
FEATURE = "agent/evi-v300-systemwide-release-hil-v3.0.0"


def _load_script():
    spec = importlib.util.spec_from_file_location("github_app_main_merge", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", FEATURE)
    _git(repository, "config", "user.name", "evidence-lane[bot]")
    _git(
        repository,
        "config",
        "user.email",
        "319574480+evidence-lane[bot]@users.noreply.github.com",
    )
    (repository / "README.md").write_text("Evidence Lane\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "feature checkpoint")
    return (
        repository,
        _git(repository, "rev-parse", "HEAD"),
        _git(repository, "rev-parse", "HEAD^{tree}"),
    )


def test_local_main_fast_forward_request_binds_feature_commit_tree_without_main_checkout(
    tmp_path,
) -> None:
    module = _load_script()
    repository, commit, tree = _repository(tmp_path)

    request = module.local_main_fast_forward_request(
        repository_root=repository,
        repository="owner/repo",
        source_branch=FEATURE,
        target_branch="main",
        source_commit="HEAD",
        expected_target_commit_sha="1" * 40,
        required_workflow_names=("Governed CI", "Preview"),
        project_id="project-a",
        task_id="task-a",
        request_id="main-merge-request-1",
        idempotency_key="main-merge-idem-1",
    )

    assert request.expected_source_commit_sha == commit
    assert request.expected_source_tree_sha == tree
    assert request.expected_target_commit_sha == "1" * 40
    assert request.source_branch == FEATURE
    assert request.target_branch == "main"
    assert _git(repository, "branch", "--show-current") == FEATURE


def test_local_main_fast_forward_request_rejects_wrong_invoking_branch(
    tmp_path,
) -> None:
    module = _load_script()
    repository, _, _ = _repository(tmp_path)

    with pytest.raises(
        module.GitHubAppMainFastForwardError,
        match="GITHUB_APP_MAIN_FAST_FORWARD_WRONG_LOCAL_BRANCH",
    ):
        module.local_main_fast_forward_request(
            repository_root=repository,
            repository="owner/repo",
            source_branch="other-feature",
            target_branch="main",
            source_commit="HEAD",
            expected_target_commit_sha="1" * 40,
            required_workflow_names=("Governed CI",),
            project_id="project-a",
            task_id="task-a",
            request_id="main-merge-request-2",
            idempotency_key="main-merge-idem-2",
        )


def test_main_fast_forward_cli_help_names_current_route() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Fast-forward" in result.stdout
    assert "--required-workflow" in result.stdout
    assert "--expected-target-commit-sha" in result.stdout
    assert "--commit-message" not in result.stdout


def test_superseded_repository_merge_command_is_a_non_executing_tombstone() -> None:
    result = subprocess.run(
        [sys.executable, str(TOMBSTONE)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert '"status": "OBSOLETE_ROUTE"' in result.stdout
    assert (
        '"required_current_route": "github_app_main_fast_forward_v3"' in result.stdout
    )
