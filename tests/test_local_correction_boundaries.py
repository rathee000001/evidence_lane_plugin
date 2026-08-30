from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.bounded_io import (
    IOBudget,
    bounded_existing_path,
    bounded_file_identity,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_adapter import resolve_git_executable
from evidence_lane_plugin.ingest import governed_source_files
from evidence_lane_plugin.redaction import contains_secret, redact_text
from evidence_lane_plugin.source_intake import _bounded_directory_members
from evidence_lane_plugin.source_policy import content_exclusion_reason


def _credential_samples() -> dict[str, str]:
    varied = "A7b9C2d4E6f8G0h2J4k6L8m0N2p4R6t8"
    return {
        "slack": "xoxb-" + varied,
        "aws": "AKIA" + "A7B9C2D4E6F8G0H2",
        "jwt": "eyJ" + varied + "." + varied + "." + varied,
        "gitlab": "glpat-" + varied,
        "hugging_face": "hf_" + varied,
    }


@pytest.mark.parametrize("credential", _credential_samples().values())
def test_common_credential_families_are_excluded_and_redacted(
    credential: str,
) -> None:
    assert content_exclusion_reason(credential.encode("utf-8")) == (
        "TOKEN_SHAPED_MATERIAL_EXCLUDED"
    )
    assert redact_text(credential) != credential
    assert contains_secret(credential) is True
    assert contains_secret(redact_text(credential)) is False


def test_bounded_paths_reject_intermediate_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    source = outside / "source.txt"
    source.write_text("outside", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")

    with pytest.raises(EvidenceLaneError, match="REPARSE_PATH_BLOCKED"):
        bounded_existing_path(link / source.name, root=root)
    with pytest.raises(EvidenceLaneError, match="REPARSE_PATH_BLOCKED"):
        bounded_file_identity(
            link / source.name,
            root=root,
            budget=IOBudget(max_file_bytes=64),
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows junction contract")
def test_source_intake_excludes_intermediate_windows_junction(
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "outside.txt").write_text("outside", encoding="utf-8")
    junction = root / "junction"
    completed = subprocess.run(
        [
            os.environ["COMSPEC"],
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(outside),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        pytest.skip("junction creation unavailable")

    members, receipt = _bounded_directory_members(root)
    selection, included, _ = governed_source_files(root, include_untracked=True)

    assert junction.is_junction() is True
    assert members == []
    assert receipt["excluded_class_counts"] == {
        "REPARSE_POINT_PATH_EXCLUDED": 1
    }
    assert selection == "FILESYSTEM_GOVERNED"
    assert included == []


@pytest.mark.skipif(os.name != "nt", reason="Windows executable-search contract")
def test_git_resolution_rejects_repository_local_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    fake = repository / "git.exe"
    fake.write_bytes(Path(os.environ["COMSPEC"]).read_bytes())
    monkeypatch.chdir(repository)
    monkeypatch.setenv("PATH", str(repository) + os.pathsep + os.environ["PATH"])

    selected = Path(resolve_git_executable(repository)).resolve()

    assert selected != fake.resolve()
    assert not selected.is_relative_to(repository.resolve())
