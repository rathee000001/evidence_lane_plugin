from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.source_intake import classify_source_intake


def _git_code_project(path: Path, name: str) -> Path:
    path.mkdir()
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text(f"NAME = {name!r}\n", encoding="utf-8")
    (path / "pyproject.toml").write_text(
        f"[project]\nname = {name!r}\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(path), "init", "-b", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.invalid"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    return path


def test_one_primary_code_project_and_additional_lane_study_brains(
    tmp_path: Path,
) -> None:
    primary = _git_code_project(tmp_path / "primary", "primary")
    additional = _git_code_project(tmp_path / "additional", "additional")
    public_repo = "https://github.com/example/public-reference.git"

    result = classify_source_intake(
        [str(primary), str(additional), public_repo],
        code_mode="local_code",
        git_mode="AUTO",
        registered_repository_path=primary,
    )

    routing = result["code_source_routing"]
    assert routing["central_code_project_count"] == 1
    assert routing["code_source_count"] == 3
    assert routing["study_brain_source_count"] == 2
    assert routing["local_code_and_github_code_distinct"] is True
    assert routing["github_code_refresh_source"] == (
        "EXACT_GOVERNED_GIT_CHECKPOINT_ONLY"
    )
    by_source = {row["source"]: row for row in result["sources"]}
    assert by_source[str(primary)]["code_source_routing"]["role"] == (
        "PRIMARY_PROJECT_CODE"
    )
    assert by_source[str(primary)]["git_optional_arm"]["history_index_enabled"] is True
    assert by_source[str(additional)]["code_source_routing"]["role"] == (
        "LANE_SCOPED_STUDY_BRAIN"
    )
    assert by_source[str(additional)]["git_optional_arm"]["history_index_enabled"] is True
    assert by_source[public_repo]["canonical_lane_id"] == "github_code"
    assert by_source[public_repo]["code_source_routing"]["repository_access"] == (
        "PUBLIC_READ_ONLY_UNOWNED"
    )
    assert by_source[public_repo]["git_optional_arm"]["history_index_enabled"] is False
    assert by_source[public_repo]["code_source_routing"][
        "lane_scoped_study_brain"
    ] is True
    assert routing["source_intake_materializes_lanes"] is False
    assert routing["initial_build_materializes_lane_artifacts"] is True
    assert routing["delta_refresh_updates_changed_lane_artifacts"] is True


def test_source_intake_cannot_replace_registered_central_code_project(
    tmp_path: Path,
) -> None:
    primary = _git_code_project(tmp_path / "primary", "primary")
    replacement = _git_code_project(tmp_path / "replacement", "replacement")

    with pytest.raises(EvidenceLaneError) as raised:
        classify_source_intake(
            [str(replacement)],
            code_mode="local_code",
            git_mode="AUTO",
            registered_repository_path=primary,
            source_assertions={
                str(replacement): {"code_project_role": "PRIMARY_PROJECT_CODE"}
            },
        )

    assert raised.value.code == (
        "SOURCE_INTAKE_PRIMARY_CODE_CHANGE_REQUIRES_NEW_PROJECT_PV"
    )
    assert raised.value.status == "BLOCKED"


def test_required_public_git_history_fails_without_access_attestation() -> None:
    source = "https://github.com/example/public-reference.git"
    with pytest.raises(EvidenceLaneError) as raised:
        classify_source_intake(
            [source],
            code_mode="local_code",
            git_mode="REQUIRED",
            source_assertions={
                source: {
                    "repository_access": "PUBLIC_READ_ONLY_UNOWNED",
                    "code_project_role": "LANE_SCOPED_STUDY_BRAIN",
                }
            },
        )
    assert raised.value.code == "SOURCE_INTAKE_GIT_HISTORY_AUTHORIZATION_REQUIRED"
