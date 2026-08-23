from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "evidence-lane-plugin" / "scripts" / "codex_release"
RESTART = SCRIPTS / "Restart-EvidenceLaneCodex.ps1"
MANAGER = SCRIPTS / "Manage-EvidenceLaneCodexGoalRecovery.ps1"


def _parse(path: Path) -> None:
    command = (
        "$errors=$null; "
        f"[Management.Automation.Language.Parser]::ParseFile('{path}',"
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


def test_restart_and_rehydration_helpers_parse() -> None:
    _parse(RESTART)
    _parse(MANAGER)


def test_restart_rehydrates_all_existing_tasks_before_sealing_receipt() -> None:
    text = RESTART.read_text(encoding="utf-8")
    call = text.index(
        "$globalTaskRehydration = Invoke-GlobalPluginUpdateTaskRehydration"
    )
    receipt = text.index("global_plugin_update_rehydration = $globalTaskRehydration")
    assert call < receipt
    assert "-Action RehydrateAll" in text
    assert "-TaskId $TaskId" in text
    assert "-ExpectedInstalledPluginVersion $InstalledVersion" in text
    assert "-ExpectedToolCount $RegistryDerivedToolCount" in text
    assert (
        "GLOBAL_PLUGIN_UPDATE_REHYDRATION_LAW requires one sealed current two-slot registry"
        in text
    )
    assert "-WindowStyle Hidden" in text
    assert (
        '"GLOBAL_PLUGIN_UPDATE_ATTACHMENT_METADATA_REBOUND_NATIVE_TASK_PROOF_PENDING"'
        in text
    )
    assert "[int]$receipt.native_catalog_rehydrated_count -ne 0" in text
    assert "[int]$receipt.native_task_proof_pending_count -lt 1" in text
    assert "GLOBAL_PLUGIN_UPDATE_EXISTING_TASK_ATTACHMENTS_REHYDRATED" not in text


def test_helper_never_uses_isolated_mcp_inventory_as_task_local_proof() -> None:
    text = MANAGER.read_text(encoding="utf-8")
    assert '-Method "mcpServerStatus/list"' not in text
    assert '-Method "mcpServer/resource/read"' not in text
    assert 'catalog_rehydrated = $false' in text
    assert 'task_local_native_proof_required = $true' in text
    assert 'native_catalog_rehydrated_count = 0' in text
    assert 'exact_host_app_server_resource_sha256' in text


def test_restart_opens_exact_invoking_task_once_and_never_navigates_other_tasks() -> None:
    text = RESTART.read_text(encoding="utf-8")
    assert (
        "Invoke-CodexHostActivation -HostProfile $hostProfile -Arguments $taskUri"
        in text
    )
    assert 'Invoke-CodexHostActivation -HostProfile $hostProfile -Arguments ""' not in text
    assert (
        'mode = "EXACT_INVOKING_TASK_FOREGROUND_START_THEN_NON_NAVIGATING_GLOBAL_REHYDRATION"'
        in text
    )
    assert "host_shell_launch_had_task_argument = $true" in text
    assert "task_uri_opened_by_initial_activation = $true" in text
    assert "task_uri_opened_by_global_rehydration = $false" in text
    assert "exact_task_reopen_count = 1" in text
    assert "non_invoking_task_navigation_count = 0" in text
    assert "-InvokingTaskForegroundActivated" in text


def test_installed_catalog_count_is_registry_derived_not_caller_guessed() -> None:
    text = RESTART.read_text(encoding="utf-8")
    assert (
        "$expectedNativeToolCount = [int]$installedReleaseChannel.stable.native_tool_count"
        in text
    )
    assert "native_read_tool_count" in text
    assert "native_write_tool_count" in text
    assert (
        "PUBLIC_SURFACE" not in text
    )  # the helper exposes no caller-supplied surface blob


@pytest.mark.parametrize(
    ("active_slot", "active_selector"),
    [
        ("stable-git-main", "evidence-lane-plugin@evidence-lane-github"),
        (
            "versioned-local-testing",
            "evidence-lane-plugin@evidence-lane-v300-testing-new",
        ),
    ],
)
def test_two_slot_authority_derives_active_state_locally(
    tmp_path: Path,
    active_slot: str,
    active_selector: str,
) -> None:
    selectors = {
        "stable-git-main": "evidence-lane-plugin@evidence-lane-github",
        "versioned-local-testing": (
            "evidence-lane-plugin@evidence-lane-v300-testing-new"
        ),
    }
    registry = {
        "schema": "evidence-lane.codex-two-slot-main-local-registry.v1",
        "status": "PASS",
        "exact_live_slot_count": 2,
        "max_enabled_plugin_count": 1,
        "max_active_native_mcp_count": 1,
        "active_slot": active_slot,
        "active_selector": active_selector,
        "failure_target_slot": "stable-git-main",
        "local_failure_targets_verified_main_only": True,
        "branch_recovery_selector_retired": True,
        "branch_recovery_install_allowed": False,
        "pre_3_0_fallback_allowed": False,
        "obsolete_live_selectors_allowed": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "slots": {
            slot: {
                "plugin_selector": selector,
                "plugin_version": "3.0.0+codex.fixture",
                "enabled": slot == active_slot,
                "native_mcp_enabled": slot == active_slot,
                "byte_frozen": slot == "stable-git-main",
            }
            for slot, selector in selectors.items()
        },
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    registry_sha256 = hashlib.sha256(registry_path.read_bytes()).hexdigest().upper()
    text = MANAGER.read_text(encoding="utf-8")

    def function_source(name: str, next_name: str) -> str:
        start = text.index(f"function {name}")
        end = text.index(f"\nfunction {next_name}", start)
        return text[start:end]

    source = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            (
                "function Get-FileHash([string]$Algorithm,[string]$LiteralPath){"
                "$stream=[IO.File]::OpenRead($LiteralPath);"
                "$sha=[Security.Cryptography.SHA256]::Create();"
                "try{$hash=([BitConverter]::ToString($sha.ComputeHash($stream)))."
                "Replace('-','');[pscustomobject]@{Hash=$hash}}"
                "finally{$sha.Dispose();$stream.Dispose()}}"
            ),
            (
                "$script:CanonicalStableSelector = "
                "'evidence-lane-plugin@evidence-lane-github'"
            ),
            (
                "$script:LocalTestingSelector = "
                "'evidence-lane-plugin@evidence-lane-v300-testing-new'"
            ),
            function_source("Get-Sha256", "Get-StringSha256"),
            function_source("Read-TwoSlotAuthority", "Install-RecoveryManager"),
            (
                "$result = Read-TwoSlotAuthority -Path '"
                + str(registry_path).replace("'", "''")
                + f"' -ExpectedSha256 '{registry_sha256}'"
            ),
            "$result | ConvertTo-Json -Compress -Depth 16",
        ]
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", source],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["active_slot"] == active_slot
    assert result["active_selector"] == active_selector
    assert result["active_version"] == "3.0.0+codex.fixture"
