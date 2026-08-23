from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
INSTALLER = PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py"
GOAL_RECOVERY = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)
RETIRED_COMBINED_HELPER = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Update-EvidenceLaneCodexStableAndResume.ps1"
)
SLOT_SWITCH = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Switch-EvidenceLaneCodexSlot.ps1"
)
RELEASE_POLICY = PLUGIN / "scripts" / "codex-release-channel.json"


def _installer_module():
    spec = importlib.util.spec_from_file_location("row254_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_retired_branch_recovery_python_route_is_absent() -> None:
    module = _installer_module()
    assert module.LOCAL_TEST_RECOVERY_MAX_ATTEMPTS == 1
    assert not hasattr(module, "_fallback_selector_class")
    assert not hasattr(module, "_fallback_release_authority")
    assert not hasattr(module, "_load_byte_frozen_fallback_recovery_authority")
    assert not hasattr(module, "_activate_byte_frozen_recovery_fallback")


def test_release_and_recovery_routes_enforce_exact_main_local_slots() -> None:
    policy = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))
    live = policy["live_slot_policy"]
    assert live["exact_slot_count"] == 2
    assert live["allowed_slots"] == [
        "main-git-release",
        "versioned-local-testing",
    ]
    assert live["allowed_marketplaces"] == [
        "evidence-lane-github",
        "evidence-lane-v300-testing-new",
    ]
    assert live["max_enabled_plugin_count"] == 1
    assert policy["stable"]["install_source"] == (
        "GIT_MAIN_EXACT_COMMIT_AFTER_GOVERNED_MERGE"
    )
    assert policy["retired_branch_recovery"]["installation_allowed"] is False

    recovery = GOAL_RECOVERY.read_text(encoding="utf-8")
    switch = SLOT_SWITCH.read_text(encoding="utf-8")
    retired = RETIRED_COMBINED_HELPER.read_text(encoding="utf-8")
    assert "function Read-TwoSlotAuthority" in recovery
    assert "evidence-lane.codex-two-slot-main-local-registry.v1" in recovery
    assert "branch_recovery_selector_retired = $true" in recovery
    assert '"main-git-release" = "evidence-lane-plugin@evidence-lane-github"' in switch
    assert (
        '"versioned-local-testing" = '
        '"evidence-lane-plugin@evidence-lane-v300-testing-new"'
    ) in switch
    assert "branch-commit-recovery" not in switch
    assert "RETIRED_COMBINED_INSTALL_RESTART_HELPER" in retired


@pytest.mark.parametrize(
    "script",
    [GOAL_RECOVERY, RETIRED_COMBINED_HELPER, SLOT_SWITCH],
)
def test_two_slot_powershell_routes_parse(script: Path) -> None:
    command = (
        "$errors=$null; "
        f"[Management.Automation.Language.Parser]::ParseFile('{script}',"
        "[ref]$null,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
