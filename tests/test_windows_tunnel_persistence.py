import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TUNNEL_SCRIPTS = (
    ROOT / "plugins" / "evidence-lane-plugin" / "scripts" / "windows_tunnel"
)


def _read(name: str) -> str:
    return (TUNNEL_SCRIPTS / name).read_text(encoding="utf-8")


def test_boot_launcher_pins_binary_and_dpapi_envelope() -> None:
    boot = _read("EvidenceLaneTunnel.Boot.ps1")
    assert "D893D8127EEE35070D265C1BE29BFE008F8D9FCB476E7FEBF56C8FDC6C0615C8" in boot
    assert "ConvertTo-SecureString" in boot
    assert "ZeroFreeBSTR" in boot
    assert "--require-control-plane-poll" in boot
    assert 'ProfileName = "evidence_lane_v200_transport"' in boot
    assert 'ProfileDir = "$env:APPDATA\\tunnel-client"' in boot
    assert "RECOVER_STALE_PROCESS" in boot
    assert "CONTROL_PLANE_ORGANIZATION_ID" not in boot
    assert "AppData\\Local\\Temp" not in boot
    assert "sk-" not in boot.lower()


def test_installer_uses_current_user_dpapi_and_resilient_task() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert 'Read-Host "Runtime API key" -AsSecureString' in installer
    assert "ConvertFrom-SecureString" in installer
    assert "New-ScheduledTaskTrigger -AtLogOn" in installer
    assert "-RestartCount 999" in installer
    assert "-WindowStyle Hidden" in installer
    assert 'windows_console_policy = "PERSISTENT_OR_HIDDEN_NO_TRANSIENT_CONSOLE"' in installer
    assert 'scheduled_task_window_style = "HIDDEN"' in installer
    assert "-StartWhenAvailable" in installer
    assert "-LogonType Interactive" in installer
    assert "runtime_key_plaintext_written = $false" in installer
    assert 'Read-Host "Tunnel ID from the OpenAI Platform tunnel page"' in installer
    assert "'^tunnel_[A-Za-z0-9]+$'" in installer
    assert 'EVIDENCE_LANE_MCP_EXPOSURE_PROFILE = "CODEX_INTERACTIVE_SUPPORT"' in installer
    assert "EVIDENCE_LANE_DATA_ROOT" in installer
    assert 'project_binding = "NONE_TRANSPORT_ONLY"' in installer
    assert 'project_route_argument = "project_id"' in installer
    assert "project_route_argument_required = $true" in installer
    assert "cross_project_fallback_allowed = $false" in installer
    assert "_INTERNAL_EVIDENCE_LANE_MCP_LAYER_DO_NOT_RUN.ps1" in installer
    assert "--control-plane-api-key-ref \"env:CONTROL_PLANE_API_KEY\"" in installer
    assert "--mcp-command $mcpCommand" in installer
    assert '[ValidateSet("stable-build", "fallback")]' in installer
    assert 'TaskName = "EvidenceLane-Tunnel-v200-$SlotRole"' in installer
    assert "exact_visible_tool_count = 62" in installer
    assert "exact_active_read_tool_count = 21" in installer
    assert "exact_fail_closed_write_tool_count = 41" in installer
    assert "codex_platform_tunnel_setup_required_once = $true" in installer
    assert r'"EvidenceLanePV\tunnel-runtime-v200-$SlotRole"' in installer
    assert "RuntimeKeyEnvelopeSource" in installer
    assert "saved_slot = $true" in installer
    assert "accepted_fallback_preserved = $true" in installer
    assert "Manage-EvidenceLaneTunnelVersions.ps1" not in installer
    assert "legacy_version_manager_authoritative = $false" in installer
    assert 'registered_slot = $SlotRole' in installer
    assert 'The fallback tunnel cannot be activated by the installer' in installer
    assert "Assert-NoOtherActiveTunnel" in installer
    assert "Another Evidence Lane tunnel is active" in installer
    assert "could not be proven stopped; activation is blocked" in installer
    assert "Disable-ScheduledTask -TaskName $TaskName" in installer
    assert "if ($Activate)" in installer
    assert "MigrateCurrentRuntime" not in installer
    assert "evidence-lane.versioned-secure-mcp-tunnel-installation.v1" in installer
    assert "Pinned Evidence Lane $release $SlotRole secure MCP tunnel" in installer
    assert 'if ($SlotRole -eq "fallback") { "2.0.0" } else { "2.2.0" }' in installer
    assert "Google Drive" not in installer
    assert "GDrive" not in installer


def test_installer_classifies_api_persistent_and_ephemeral_host_lifetimes() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert '[ValidateSet("CODEX_APP_INTERACTIVE", "HEADLESS_API", "DIRECT_CLI_API")]' in installer
    assert 'tunnel_requirement = "NOT_REQUIRED_FOR_API_LAYER"' in installer
    assert 'local_pv_storage_allowed_when_durable = $true' in installer
    assert '[ValidateSet("Auto", "Persistent", "Ephemeral")]' in installer
    assert 'tunnel_setup_frequency = if ($exactHostLifetime -eq "Ephemeral")' in installer
    assert '"ONCE_PER_EPHEMERAL_VM_INSTANCE"' in installer
    assert '"ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"' in installer
    assert '"CURRENT_VM_LIFETIME_ONLY"' in installer
    assert '"CURRENT_WINDOWS_USER_DPAPI_PROFILE"' in installer
    assert "EVIDENCE_LANE_VM_INSTANCE_ID" in installer
    assert "vm_instance_id_sha256 = $vmInstanceIdSha256" in installer
    assert "raw_vm_instance_id_stored = $false" in installer
    assert "cannot import a Runtime key envelope from durable storage" in installer
    assert '$exactHostLifetime -ne "Ephemeral"' in installer
    assert 'two_slot_registry_authority = "SEALED_POST_PV11_TWO_SLOT_REGISTRY"' in installer
    assert "account_tier_affects_routing = $false" in installer
    assert "api_billing_affects_routing = $false" in installer


def test_installer_reuses_verified_dependencies_and_can_acquire_missing_client() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert "EVIDENCE_LANE_TUNNEL_CLIENT_DOWNLOAD_URI" in installer
    assert "Invoke-WebRequest -Uri $downloadUri" in installer
    assert '$downloadUri.Scheme -ne "https"' in installer
    assert "DOWNLOADED_FROM_CONFIGURED_HTTPS_AND_HASH_VERIFIED" in installer
    assert "does not match the pinned v0.0.10 SHA-256" in installer
    assert 'Get-ChildItem -LiteralPath ([IO.Path]::GetFullPath($DataRoot))' in installer
    assert 'Join-Path $_.FullName "bin\\tunnel-client-v0.0.10.exe"' in installer
    assert "Resolve-PriorRuntimeKeyEnvelope" in installer
    assert "tunnel_id_reused = $tunnelIdReused" in installer
    assert "runtime_key_envelope_reused = $runtimeKeyEnvelopeReused" in installer


def test_manager_exposes_start_status_repair_and_ready_gate() -> None:
    manager = _read("Manage-EvidenceLaneTunnel.ps1")
    assert '[ValidateSet("Start", "Stop", "Status", "Repair", "Remove")]' in manager
    assert "--require-control-plane-poll" in manager
    assert "stable_binary_hash_valid" in manager
    assert "control_plane_poll_ready" in manager
    assert "evidence_lane_layer_launcher_configured" in manager
    assert 'transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"' in manager
    assert 'codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"' in manager
    assert "codex_tunnel_lifecycle_proof_allowed = $false" in manager
    assert "exact_visible_tool_count = 62" in manager
    assert "exact_active_read_tool_count = 21" in manager
    assert "exact_fail_closed_write_tool_count = 41" in manager
    assert "runtime_key_plaintext_reported = $false" in manager
    assert "slot_role = if ($null -ne $marker)" in manager
    assert "byte_frozen = if ($null -ne $marker)" in manager
    assert 'project_binding = "NONE_TRANSPORT_ONLY"' in manager
    assert 'project_route_argument = "project_id"' in manager
    assert "project_route_argument_required = $true" in manager
    assert "cross_project_fallback_allowed = $false" in manager
    assert "if (-not $ConfirmRemoval)" in manager
    assert "EvidenceLanePV directory" in manager
    assert "installation marker" in manager
    assert "marker.profile_name" in manager
    assert "marker.profile_file" in manager
    assert "$markerProfileFile -ne $exactProfileFile" in manager
    assert "Unregister-ScheduledTask" in manager
    assert 'status = "STOPPED_SAVED"' in manager
    assert "reusable_without_reinstall = $true" in manager


def test_legacy_version_manager_remains_migration_only_not_live_slot_authority() -> None:
    manager = _read("Manage-EvidenceLaneTunnelVersions.ps1")
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert '[ValidateSet("Register", "List", "VerifyCandidate", "Promote", "Activate")]' in manager
    assert 'evidence-lane.tunnel-version-registry.v1' in manager
    assert "evidence-lane-tunnel-installation.json" in manager
    assert "stable_client_sha256" in manager
    assert "secret_material_in_registry = $false" in manager
    assert "reusable_without_reinstall = $true" in manager
    assert "Disable-ScheduledTask" in manager
    assert "Enable-ScheduledTask" in manager
    assert "Stop-SavedVersion" in manager
    assert "Start-SavedVersion" in manager
    assert 'EventType "FUTURE_TEST_FAILED_STABLE_UNTOUCHED"' in manager
    assert 'EventType "PROMOTION_STARTED_STABLE_STILL_READY"' in manager
    assert 'EventType "PROMOTED_TO_STABLE"' in manager
    assert 'EventType "MOVED_TO_ARCHIVE"' in manager
    assert "HealthReceiptSha256" in manager
    assert "PublicRouteReceiptSha256" in manager
    assert "HostProofReceiptSha256" in manager
    assert "Write-VersionRegistry -Registry $channelRegistry" in manager
    assert "--require-control-plane-poll" in manager
    assert "Remove-Item -LiteralPath $exactRuntimeRoot -Recurse" not in manager
    assert "evidence-lane.versioned-secure-mcp-tunnel-installation.v1" in manager
    assert "Manage-EvidenceLaneTunnelVersions.ps1" not in installer
    assert "legacy_version_manager_authoritative = $false" in installer


def test_version_manager_reads_legacy_registry_without_history_fields(
    tmp_path: Path,
) -> None:
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        pytest.skip("PowerShell is required for the Windows tunnel registry test.")
    data_root = tmp_path / "EvidenceLanePV"
    registry_root = data_root / "tunnel-versions"
    registry_root.mkdir(parents=True)
    (registry_root / "registry.json").write_text(
        json.dumps(
            {
                "schema": "evidence-lane.tunnel-version-registry.v1",
                "active_release": None,
                "versions": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            powershell,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TUNNEL_SCRIPTS / "Manage-EvidenceLaneTunnelVersions.ps1"),
            "-Action",
            "List",
            "-DataRoot",
            str(data_root),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["event_sequence"] == 0
    assert payload["event_head_sha256"] is None
    assert payload["versions"] == []


def test_all_tunnel_scripts_use_isolated_v200_runtime_names() -> None:
    for name in (
        "Install-EvidenceLaneTunnel.ps1",
        "EvidenceLaneTunnel.Boot.ps1",
        "Manage-EvidenceLaneTunnel.ps1",
    ):
        text = _read(name)
        assert "evidence_lane_v200" in text
        assert "tunnel-runtime-v200" in text
        assert "EvidenceLane-Tunnel-v200" in text or name == "EvidenceLaneTunnel.Boot.ps1"
        assert "evidence_lane_v150" not in text
        assert "tunnel-runtime-v150" not in text
        assert "EvidenceLane-Tunnel-v150" not in text
        assert "tunnel-runtime-v140" not in text
        assert "evidence_lane_v140" not in text
        assert "EvidenceLane-Tunnel-v140" not in text

    version_manager = _read("Manage-EvidenceLaneTunnelVersions.ps1")
    assert "tunnel-runtime-v200" not in version_manager
    assert "tunnel-runtime-v150" not in version_manager
    assert "tunnel-runtime-v140" not in version_manager
    assert "tunnel-runtime-v130" not in version_manager


def test_pinned_process_identity_uses_exact_path_and_hash_not_executable_stem() -> None:
    for name in (
        "Install-EvidenceLaneTunnel.ps1",
        "EvidenceLaneTunnel.Boot.ps1",
        "Manage-EvidenceLaneTunnel.ps1",
    ):
        text = _read(name)
        assert 'ProcessName -ne "tunnel-client"' not in text

    for name in (
        "EvidenceLaneTunnel.Boot.ps1",
        "Manage-EvidenceLaneTunnel.ps1",
        "Manage-EvidenceLaneTunnelVersions.ps1",
    ):
        text = _read(name)
        assert "Resolve-Path -LiteralPath $process.Path" in text
        assert "Get-FileHash -LiteralPath $processPath -Algorithm SHA256" in text
