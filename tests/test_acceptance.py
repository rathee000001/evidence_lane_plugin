from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from evidence_lane_plugin.acceptance import (
    _safe_subprocess_environment,
    run_acceptance_checks,
)


def _git(repository: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Acceptance Test")
    _git(repository, "config", "user.email", "acceptance@example.invalid")
    (repository / "source.txt").write_text("stable\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "fixture")
    return repository


def test_prose_is_never_guessed_as_an_executable_check(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    result = run_acceptance_checks(
        repository,
        ["The application behaves correctly."],
    )
    assert result["verdict"] == "PARTIAL_PROSE_CHECKS_NOT_EXECUTED"
    assert result["executed"] == 0
    assert result["checks"][0]["status"] == "PENDING_HUMAN_REVIEW"
    assert result["commands_inferred"] is False


def test_exact_command_passes_only_when_source_bytes_stay_fixed(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    stable = run_acceptance_checks(
        repository,
        [f'cmd:"{sys.executable}" --version'],
        timeout_seconds=30,
    )
    assert stable["verdict"] == "ALL_EXECUTABLE_CHECKS_PASS"
    assert stable["source_unchanged"] is True

    inline = run_acceptance_checks(
        repository,
        [f'cmd:"{sys.executable}" -c "print(123)"'],
        timeout_seconds=30,
    )
    assert inline["verdict"] == "INVALID_CHECK_DECLARATION"
    assert inline["counts"]["BLOCKED_INVALID_COMMAND"] == 1
    assert inline["executed"] == 0


def test_shell_operators_and_secret_shaped_commands_are_blocked(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = repository / "must-not-exist.txt"
    result = run_acceptance_checks(
        repository,
        [
            (f'cmd:"{sys.executable}" --version && "{sys.executable}" --version'),
            (
                f'cmd:"{sys.executable}" --version '
                "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
            ),
        ],
        timeout_seconds=30,
    )
    assert result["verdict"] == "INVALID_CHECK_DECLARATION"
    assert result["counts"]["BLOCKED_INVALID_COMMAND"] == 2
    assert result["executed"] == 0
    assert marker.exists() is False


def test_repository_manifest_is_declaration_only_and_never_executes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    declarations = [
        f"AC{index:02d} executable: exact check {index}." for index in range(1, 13)
    ]
    manifest_path = repository / "evidence" / "acceptance" / "commands.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    declaration: {
                        "argv": [
                            "$RUNTIME_PYTHON",
                            "-c",
                            f"print('AC{index:02d}:PASS')",
                        ],
                        "timeout_seconds": 30,
                    }
                    for index, declaration in enumerate(declarations, start=1)
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_acceptance_checks(repository, declarations, timeout_seconds=30)
    assert result["bounded_to"] == 12
    assert result["declared"] == 12
    assert result["executed"] == 0
    assert result["verdict"] == "REPOSITORY_COMMAND_APPROVAL_REQUIRED"
    assert result["counts"]["PENDING_EXPLICIT_COMMAND_APPROVAL"] == 12
    assert result["command_manifest"]["status"] == "PASS"
    assert len(result["command_manifest"]["sha256"]) == 64
    assert all(
        row["declared_via"] == "repository_manifest_declaration_only"
        for row in result["checks"]
    )
    assert result["commands_inferred"] is False


def test_postseal_manifest_never_executes_with_candidate_context(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    declaration = "AC12 executable: validate the immutable candidate."
    manifest_path = repository / "evidence" / "acceptance" / "commands.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    declaration: {
                        "argv": [
                            "$RUNTIME_PYTHON",
                            "-c",
                            (
                                "import os,sys; "
                                "sys.exit(0 if os.environ.get('CANDIDATE_TEST') "
                                "== 'sealed' else 9)"
                            ),
                        ],
                        "phase": "POSTSEAL",
                        "timeout_seconds": 30,
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    pending = run_acceptance_checks(repository, [declaration], timeout_seconds=30)
    assert pending["verdict"] == "REPOSITORY_COMMAND_APPROVAL_REQUIRED"
    assert pending["executed"] == 0
    assert pending["checks"][0]["status"] == "PENDING_EXPLICIT_COMMAND_APPROVAL"

    postseal = run_acceptance_checks(
        repository,
        [declaration],
        timeout_seconds=30,
        phase="POSTSEAL",
        environment={"CANDIDATE_TEST": "sealed"},
    )
    assert postseal["verdict"] == "REPOSITORY_COMMAND_APPROVAL_REQUIRED"
    assert postseal["executed"] == 0
    assert postseal["checks"][0]["status"] == "PENDING_EXPLICIT_COMMAND_APPROVAL"


def test_manifest_prebuild_and_postseal_both_require_external_approval(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    prebuild = "AC01 executable: run the bounded prebuild command."
    postseal = "AC12 executable: validate the immutable candidate."
    manifest_path = repository / "evidence" / "acceptance" / "commands.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    prebuild: {
                        "argv": ["$RUNTIME_PYTHON", "-c", "print('prebuild')"],
                        "timeout_seconds": 30,
                    },
                    postseal: {
                        "argv": ["$RUNTIME_PYTHON", "-c", "print('postseal')"],
                        "phase": "POSTSEAL",
                        "timeout_seconds": 30,
                    },
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_acceptance_checks(repository, [prebuild, postseal])
    assert result["status"] == "PARTIAL"
    assert result["verdict"] == "REPOSITORY_COMMAND_APPROVAL_REQUIRED"
    assert result["prebuild_status"] == "PARTIAL"
    assert result["prebuild_executed"] == 0
    assert result["postseal_pending"] == 0
    assert result["counts"]["PENDING_EXPLICIT_COMMAND_APPROVAL"] == 2


def test_manifest_registry_may_map_more_entries_than_one_bounded_task_executes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    declarations = [
        f"AC{index:02d} executable: registry check {index}." for index in range(1, 22)
    ]
    manifest_path = repository / "evidence" / "acceptance" / "commands.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.acceptance-command-manifest.v1",
                "commands": {
                    declaration: {
                        "argv": [
                            "$RUNTIME_PYTHON",
                            "-c",
                            f"print('AC{index:02d}:PASS')",
                        ],
                        "timeout_seconds": 30,
                    }
                    for index, declaration in enumerate(declarations, start=1)
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    exact_six = declarations[12:18]
    result = run_acceptance_checks(repository, exact_six, timeout_seconds=30)
    assert result["command_manifest"]["status"] == "PASS"
    assert result["command_manifest"]["entry_count"] == 21
    assert result["declared"] == 6
    assert result["executed"] == 0
    assert result["counts"]["PENDING_EXPLICIT_COMMAND_APPROVAL"] == 6
    assert result["verdict"] == "REPOSITORY_COMMAND_APPROVAL_REQUIRED"


def test_acceptance_subprocess_environment_is_minimal_and_secret_free(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-secret-value-01234567890123456789")
    monkeypatch.setenv("PATH", "safe-path")
    safe, error = _safe_subprocess_environment(
        {"EVIDENCE_LANE_PROJECT_ID": "project-a"}
    )
    assert error is None
    assert safe is not None
    assert safe["PATH"] == "safe-path"
    assert safe["EVIDENCE_LANE_PROJECT_ID"] == "project-a"
    assert safe["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "OPENAI_API_KEY" not in safe

    blocked, reason = _safe_subprocess_environment({"UNSCOPED_VALUE": "value"})
    assert blocked is None
    assert "Evidence Lane namespace" in str(reason)
