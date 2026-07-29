from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from evidence_lane_plugin.acceptance import run_acceptance_checks


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
        [f'cmd:"{sys.executable}" -c "print(123)"'],
        timeout_seconds=30,
    )
    assert stable["verdict"] == "ALL_EXECUTABLE_CHECKS_PASS"
    assert stable["source_unchanged"] is True

    mutating = run_acceptance_checks(
        repository,
        [
            (
                f'cmd:"{sys.executable}" -c '
                '"from pathlib import Path; '
                "Path('source.txt').write_text('changed')\""
            )
        ],
        timeout_seconds=30,
    )
    assert mutating["verdict"] == "SOURCE_MUTATED_BY_CHECKS"
    assert mutating["source_unchanged"] is False


def test_shell_operators_and_secret_shaped_commands_are_blocked(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = repository / "must-not-exist.txt"
    result = run_acceptance_checks(
        repository,
        [
            (
                f'cmd:"{sys.executable}" -c "print(123)" && '
                f'"{sys.executable}" -c '
                f"\"from pathlib import Path; Path(r'{marker}').touch()\""
            ),
            (
                f'cmd:"{sys.executable}" -c "print('
                "'sk-proj-abcdefghijklmnopqrstuvwxyz123456')\""
            ),
        ],
        timeout_seconds=30,
    )
    assert result["verdict"] == "INVALID_CHECK_DECLARATION"
    assert result["counts"]["BLOCKED_INVALID_COMMAND"] == 2
    assert result["executed"] == 0
    assert marker.exists() is False
