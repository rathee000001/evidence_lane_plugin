from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOAL_RECOVERY = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)
INSTALLER = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "install_codex_stable.py"
)
RESTART = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "Restart-EvidenceLaneCodex.ps1"
)


def test_goal_recovery_separates_shared_release_from_mutable_task_rows() -> None:
    text = GOAL_RECOVERY.read_text(encoding="utf-8")

    assert 'authority_scope = "SHARED_TWO_SLOT_MAIN_LOCAL_RELEASE_AUTHORITY"' in text
    assert 'task_binding_scope = "SEPARATE_MUTABLE_EXACT_TASK_REGISTRY"' in text
    assert "release_authority_sha256 = $registrySha256" in text
    assert "registry_origin_identity_used_for_authorization = $false" in text
    assert "function Assert-BindingReleaseAuthority" in text
    assert 'PSObject.Properties["release_authority_sha256"]' in text
    assert "[string]$Binding.slot_authority.registry_sha256" in text
    assert (
        "The mutable task binding must be refreshed against the current sealed "
        "two-slot authority."
    ) in text
    assert "main_git_selector = [string]$main.plugin_selector" in text
    assert "mutable_local_selector = [string]$local.plugin_selector" in text
    assert "mutable_local_failure_targets_verified_main_only = $true" in text
    assert "branch_recovery_selector_retired = $true" in text
    assert "pre_3_0_automatic_recovery_allowed = $false" in text
    assert "[string]$registry.project_id -ne [string]$Binding.project_id" not in text
    assert (
        "[string]$registry.evidence_session_id -ne "
        "[string]$Binding.evidence_session_id" not in text
    )
    assert "[string]$registry.task_id -ne [string]$Binding.task_id" not in text
    assert text.count("Assert-BindingReleaseAuthority -Binding") == 2
    assert (
        'scope = "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_ON_THIS_WINDOWS_USER"'
        in text
    )


def test_goal_recovery_registry_separation_parses_as_powershell() -> None:
    command = (
        "$errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{GOAL_RECOVERY}',"
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


def test_retired_local_recovery_convergence_has_no_live_route() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")

    assert '"stable-git-main": PLUGIN_SELECTOR' in installer
    assert '"versioned-local-testing"' in installer
    assert "LOCAL_RECOVERY_SELECTOR," in installer
    assert "result = _materialize_local_recovery_copy(args)" not in installer
    assert '"EXPLICIT_BYTE_IDENTICAL_LOCAL_3_0_RECOVERY"' not in installer
    assert "The recovery implementation and all of its side effects are gone." in installer


def test_restart_helper_reads_the_sealed_local_recovery_state() -> None:
    restart = RESTART.read_text(encoding="utf-8")

    assert "$twoSlotRegistryBody.local_failure_targets_verified_main_only" in restart
    assert "$twoSlotRegistryBody.branch_recovery_selector_retired" in restart
    assert "$twoSlotRegistryBody.branch_recovery_install_allowed" in restart
    assert "$restartAuthority.versioned_local_failure_targets_verified_main_only = $true" in restart
    assert "$restartAuthority.branch_recovery_selector_retired = $true" in restart
