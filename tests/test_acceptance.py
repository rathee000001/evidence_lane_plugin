from __future__ import annotations

import json
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


def test_exact_manifest_binds_and_executes_all_twelve_prose_checks(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    declarations = [f"AC{index:02d} executable: exact check {index}." for index in range(1, 13)]
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
    assert result["executed"] == 12
    assert result["verdict"] == "ALL_EXECUTABLE_CHECKS_PASS"
    assert result["counts"]["PASS"] == 12
    assert result["command_manifest"]["status"] == "PASS"
    assert len(result["command_manifest"]["sha256"]) == 64
    assert all(
        row["declared_via"] == "repository_manifest_exact_match"
        for row in result["checks"]
    )
    assert result["commands_inferred"] is False


def test_postseal_manifest_check_waits_then_executes_with_candidate_context(
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
    assert pending["verdict"] == "POSTSEAL_CHECKS_PENDING"
    assert pending["executed"] == 1
    assert pending["checks"][0]["status"] == "PENDING_POSTSEAL"

    postseal = run_acceptance_checks(
        repository,
        [declaration],
        timeout_seconds=30,
        phase="POSTSEAL",
        environment={"CANDIDATE_TEST": "sealed"},
    )
    assert postseal["verdict"] == "ALL_EXECUTABLE_CHECKS_PASS"
    assert postseal["checks"][0]["status"] == "PASS"


def test_prebuild_summary_passes_when_exact_prebuild_runs_before_postseal(
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
    assert result["verdict"] == "POSTSEAL_CHECKS_PENDING"
    assert result["prebuild_status"] == "PASS"
    assert (
        result["prebuild_verdict"]
        == "ALL_PREBUILD_EXECUTABLE_CHECKS_PASS_POSTSEAL_PENDING"
    )
    assert result["prebuild_executed"] == 1
    assert result["postseal_pending"] == 1


def test_repository_manifest_binds_exactly_twelve_v130_checks() -> None:
    repository = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (repository / "evidence" / "acceptance" / "commands.json").read_text(
            encoding="utf-8"
        )
    )
    commands = manifest["commands"]
    assert manifest["schema"] == "evidence-lane.acceptance-command-manifest.v1"
    assert len(commands) == 12
    assert [key[:4] for key in commands] == [f"AC{index:02d}" for index in range(1, 13)]
    for index, entry in enumerate(commands.values(), start=1):
        assert entry["argv"] == [
            "$RUNTIME_PYTHON",
            "plugins/evidence-lane-plugin/scripts/run_acceptance_check.py",
            f"AC{index:02d}",
        ]
    assert commands[next(reversed(commands))]["phase"] == "POSTSEAL"
