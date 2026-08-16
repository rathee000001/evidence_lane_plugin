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


def test_goal_recovery_separates_shared_release_from_mutable_task_rows() -> None:
    text = GOAL_RECOVERY.read_text(encoding="utf-8")

    assert 'authority_scope = "SHARED_RELEASE_SLOT_AUTHORITY"' in text
    assert 'task_binding_scope = "SEPARATE_MUTABLE_REGISTRY"' in text
    assert "release_authority_sha256 = $registrySha256" in text
    assert "registry_origin_identity_used_for_authorization = $false" in text
    assert "function Assert-BindingReleaseAuthority" in text
    assert 'PSObject.Properties["release_authority_sha256"]' in text
    assert "[string]$Binding.slot_authority.registry_sha256" in text
    assert (
        "The mutable task binding must be refreshed against the current shared "
        "release and sealed fallback authority."
    ) in text
    assert 'PSObject.Properties["fallback_authority_sha256"]' in text
    assert "fallback_selector_used_for_authorization = $false" in text
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
