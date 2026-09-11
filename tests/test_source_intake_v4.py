"""Retained intake classifier cases, using the current sector contract."""
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.git_optional import probe_git_arm
from evidence_lane_plugin.source_intake import classify_source_intake


@pytest.mark.parametrize('lane', ['plan', 'chat_lineage', 'memory', 'project_engulf', 'brain_loader', 'sqlite_brain'])
def test_source_overrides_cannot_select_authorities_or_retired_sectors(tmp_path, lane):
    source = tmp_path / 'input.txt'
    source.write_text('Fixture evidence.')
    with pytest.raises(EvidenceLaneError) as error:
        classify_source_intake([str(source)], code_mode='local_code', overrides={str(source): lane})
    assert error.value.code == 'SOURCE_INTAKE_SECTOR_REQUIRED'


def test_intake_reports_conditional_artifacts_and_no_capture_or_overlay(tmp_path):
    source = tmp_path / 'code'
    source.mkdir()
    (source / 'pyproject.toml').write_text('[project]\nname="fixture"\n')
    result = classify_source_intake([str(source)], code_mode='local_code')
    assert result['ordered_canonical_lanes'] == ['local_code']
    assert result['chat_lineage_included'] is False
    assert 'plan' not in result['all_canonical_lanes_supported']
    routing = result['sources'][0]['code_source_routing']
    assert routing['repository_access'] == 'LOCAL_READ_ONLY_UNVERIFIED'
    assert routing['git_history_authorized'] is False
    assert routing['lane_artifact_contract']['graph_exports']['both_required'] is False
    assert 'hil_refresh_adds_project_overlay' not in result['code_source_routing']
    with pytest.raises(EvidenceLaneError) as error:
        classify_source_intake([str(source)], code_mode='local_code', source_assertions={str(source): {'repository_access': 'REGISTERED_PROJECT_AUTHORITY'}})
    assert error.value.code == 'SOURCE_INTAKE_REGISTERED_AUTHORITY_MISMATCH'


def test_code_archive_routes_to_retained_code_sector(tmp_path):
    source = tmp_path / 'code.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('project/pyproject.toml', '[project]\nname="fixture"\n')
        archive.writestr('project/src/app.py', 'VALUE = 1\n')
    result = classify_source_intake([str(source)], code_mode='local_code')
    assert result['sources'][0]['canonical_lane_id'] == 'local_code'


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()

def test_optional_git_arm_falls_back_for_plain_directory(tmp_path: Path, monkeypatch) -> None:
    # Keep this genuinely outside Git discovery even when pytest's selected
    # basetemp lives inside the development repository. Parent-root behavior
    # has its own regression and must not be confused with this plain folder.
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    source = tmp_path / "plain-evidence"
    source.mkdir()
    (source / "notes.md").write_text("independent evidence\n", encoding="utf-8")
    receipt = probe_git_arm(source, requested_mode="AUTO")
    assert receipt["state"] == "NOT_A_GIT_WORKTREE_FALLBACK"
    assert receipt["history_index_enabled"] is False
    assert receipt["fallback_content_index_enabled"] is True
    result = classify_source_intake(
        [str(source)], code_mode="local_code", git_mode="AUTO"
    )
    assert result["sources"][0]["canonical_lane_id"] == "custom"
    assert result["sources"][0]["git_optional_arm"]["state"] == (
        "NOT_A_GIT_WORKTREE_FALLBACK"
    )
    identity = result["sources"][0]["source_identity"]
    assert identity["identity_scope"] == "MEMBER_PATHS_AND_SIZES_ONLY"
    assert identity["content_bytes_hashed"] is False
    assert identity["content_identity_proven"] is False
    assert "member_path_size_sha256" in identity

def test_required_git_arm_rejects_plain_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('GIT_CEILING_DIRECTORIES', str(tmp_path))
    source = tmp_path / "plain-evidence"
    source.mkdir()
    with pytest.raises(EvidenceLaneError) as blocked:
        probe_git_arm(source, requested_mode="REQUIRED")
    assert blocked.value.code == "GIT_ARM_REQUIRED_WORKTREE_MISSING"

def test_optional_git_arm_detects_linked_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    linked = tmp_path / "linked"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Evidence Lane Test")
    _git(repository, "config", "user.email", "evidence-lane@example.invalid")
    (repository / "README.md").write_text("# Evidence Lane\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-m", "initial")
    _git(repository, "worktree", "add", "-b", "linked-test", str(linked))
    assert (linked / ".git").is_file()
    receipt = probe_git_arm(linked, requested_mode="AUTO")
    assert receipt["state"] == "ENABLED"
    assert receipt["history_index_enabled"] is True
    assert receipt["head_commit"] == _git(linked, "rev-parse", "HEAD")
    assert receipt["head_tree"] == _git(linked, "rev-parse", "HEAD^{tree}")
    assert receipt["branch"] == "linked-test"
    assert receipt["detached_head"] is False
    assert receipt["worktree_clean"] is True
    assert receipt["remote_identity_included"] is False

def test_directory_path_size_identity_never_claims_content_hash(
    tmp_path: Path,
) -> None:
    source = tmp_path / "extracted-source"
    source.mkdir()
    payload = source / "module.py"
    payload.write_text("alpha\n", encoding="utf-8")
    first = classify_source_intake([str(source)], code_mode="local_code")
    first_identity = first["sources"][0]["source_identity"]
    payload.write_text("bravo\n", encoding="utf-8")
    same_size = classify_source_intake([str(source)], code_mode="local_code")
    same_size_identity = same_size["sources"][0]["source_identity"]
    assert (
        same_size_identity["member_path_size_sha256"]
        == first_identity["member_path_size_sha256"]
    )
    assert same_size_identity["content_identity_proven"] is False
    payload.write_text("longer-content\n", encoding="utf-8")
    changed_size = classify_source_intake([str(source)], code_mode="local_code")
    assert (
        changed_size["sources"][0]["source_identity"]["member_path_size_sha256"]
        != first_identity["member_path_size_sha256"]
    )

def test_git_directory_identity_excludes_ignored_and_local_history(
    tmp_path: Path,
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    _git(source, "init", "-b", "main")
    _git(source, "config", "user.name", "Evidence Lane Test")
    _git(source, "config", "user.email", "evidence-lane@example.invalid")
    (source / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    evidence = source / "evidence" / "local-history"
    evidence.mkdir(parents=True)
    (evidence / "receipt.json").write_text("{}\n", encoding="utf-8")
    ignored = source / "node_modules" / "dependency"
    ignored.mkdir(parents=True)
    (ignored / "large.bin").write_bytes(b"x" * 4096)
    _git(source, "add", ".gitignore", "module.py")
    _git(source, "commit", "-m", "tracked source")

    result = classify_source_intake([str(source)], code_mode="local_code")
    identity = result["sources"][0]["source_identity"]

    assert identity["bounded_io"]["source_selection"] == (
        "GIT_INDEX_AND_SAFE_UNTRACKED"
    )
    assert identity["bounded_io"]["ignored_paths_traversed"] is False
    assert identity["bounded_io"]["local_history_content_read"] is False
    assert identity["bounded_io"]["excluded_class_counts"] == {
        "LOCAL_HISTORY_PATH_EXCLUDED": 1
    }
    assert identity["member_count"] == 2

@pytest.mark.parametrize("code_mode", ["local_code", "github_code"])
def test_code_config_path_context_overrides_generic_json_lane(
    tmp_path: Path,
    code_mode: str,
) -> None:
    manifest = tmp_path / "plugins" / "example" / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"example"}\n', encoding="utf-8")

    result = classify_source_intake([str(manifest)], code_mode=code_mode)
    source = result["sources"][0]

    assert source["canonical_lane_id"] == code_mode
    assert source["classification_reason"] == (
        "authoritative_code_config_path_context"
    )
    assert source["source_identity"]["bounded_io"]["consumed_file_count"] == 1

def test_archive_profile_separates_package_format_from_generator_identity(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "SQLite-Brain-Builder-V5.9-output.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "project/manifest.json",
            json.dumps(
                {
                    "package_type": (
                        "EvidenceOS_V2_FULL_VERTICAL_project_mini_brain"
                    )
                }
            ),
        )
        archive.writestr("project/sql/mini_brain_graph.sqlite", b"not-opened")
    result = classify_source_intake([str(archive_path)], code_mode="local_code")
    source = result["sources"][0]
    profile = source["archive_profile"]
    assert source["canonical_lane_id"] == "custom"
    assert profile["package_format"] == (
        "EVIDENCEOS_V2_FULL_VERTICAL_MINI_BRAIN"
    )
    assert profile["generator_identity_status"] == "UNPROVEN_BY_ARCHIVE"
    assert profile["filename_used_as_generator_evidence"] is False
    assert profile["declared_generator_versions"] == []

def test_archive_profile_detects_uepc_sector_layout_without_version_inference(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "sector-package.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("env/env_sqlite.sqlite", b"environment")
        archive.writestr(
            "project/sectors/local_code/local_code_sector_v001.sqlite", b"sector"
        )
        archive.writestr(
            "manifests/manifest.json", json.dumps({"schema": "uepc-sector.v1"})
        )
    result = classify_source_intake([str(archive_path)], code_mode="local_code")
    profile = result["sources"][0]["archive_profile"]
    assert profile["package_format"] == "UEPC_SECTOR_PACKAGE"
    assert profile["sqlite_member_count"] == 2
    assert profile["generator_identity_status"] == "UNPROVEN_BY_ARCHIVE"
