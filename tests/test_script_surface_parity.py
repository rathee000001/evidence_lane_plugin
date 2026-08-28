from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SCRIPTS = PLUGIN / "scripts"
RELEASE = SCRIPTS / "codex_release"


def test_obsolete_script_routes_are_purged_and_pocs_are_repository_only() -> None:
    assert not (RELEASE / "Update-EvidenceLaneCodexStableAndResume.ps1").exists()
    assert not (RELEASE / "merge_github_app_feature_to_main.py").exists()
    assert not (SCRIPTS / "build_one_shot_dummy_poc.py").exists()
    assert not (SCRIPTS / "build_public_dummy_lane_packages.py").exists()
    assert not (SCRIPTS / "build_real_git_poc.py").exists()
    assert not (PLUGIN / "tests" / "tools" / "build_one_shot_dummy_poc.py").exists()
    for name in (
        "build_one_shot_dummy_poc.py",
        "build_public_dummy_lane_packages.py",
        "build_real_git_poc.py",
    ):
        assert (ROOT / "tests" / "tools" / name).is_file()


def test_local_update_uses_terminal_safe_restart_preparation_only() -> None:
    assert not (RELEASE / "drain_codex_task_turns.py").exists()
    assert not (RELEASE / "Restart-EvidenceLaneCodex.ps1").exists()
    prepare = (RELEASE / "Prepare-EvidenceLaneCodexRestart.ps1").read_text(
        encoding="utf-8"
    )
    assert 'current_turn_terminal_event_required_before_app_close = $true' in prepare
    assert 'drain_utility_allowed = $false' in prepare
    assert 'programmatic_process_stop_allowed = $false' in prepare
    assert 'scheduled_restart_child_allowed = $false' in prepare
    assert '"turn/interrupt"' not in prepare
    assert "Stop-Process" not in prepare


def test_package_builder_excludes_binary_caches_and_maintainer_tools() -> None:
    builder = (SCRIPTS / "build_release_candidate_rehearsal.py").read_text(
        encoding="utf-8"
    )
    assert '"__pycache__"' in builder
    assert 'MAINTAINER_TEST_PREFIXES = ("tests/tools/",)' in builder
    assert '"Update-EvidenceLaneCodexStableAndResume.ps1"' not in builder
