import struct
from pathlib import Path

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
    assert 'ProfileName = "evidence_lane_v300_stable_build_transport"' in boot
    assert 'ReleaseToken = "v300"' in boot
    assert "release-bound tunnel marker" in boot
    assert 'ProfileDir = "$env:APPDATA\\tunnel-client"' in boot
    assert "RECOVER_STALE_PROCESS" in boot
    assert "CONTROL_PLANE_ORGANIZATION_ID" not in boot
    assert "AppData\\Local\\Temp" not in boot
    assert "sk-" not in boot.lower()


def test_installer_uses_current_user_dpapi_and_resilient_host_task() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert 'Read-Host "Runtime API key" -AsSecureString' in installer
    assert "ConvertFrom-SecureString" in installer
    assert "New-ScheduledTaskTrigger -AtLogOn" in installer
    assert "Register-ScheduledTask" in installer
    assert 'windows_console_policy = "WINDOWS_GUI_HOST_CREATE_NO_WINDOW"' in installer
    assert 'scheduled_task_window_style = "HIDDEN"' in installer
    assert 'scheduled_task_launcher_subsystem = "WINDOWS_GUI_NO_VISIBLE_CONSOLE"' in installer
    assert "scheduled_task_launcher_create_no_window = $true" in installer
    assert "scheduled_task_transport_used = $true" in installer
    assert "runtime_key_plaintext_written = $false" in installer
    assert 'Read-Host "Tunnel ID from the OpenAI Platform tunnel page"' in installer
    assert "'^tunnel_[A-Za-z0-9]+$'" in installer
    assert 'EVIDENCE_LANE_MCP_EXPOSURE_PROFILE = "CODEX_INTERACTIVE_SUPPORT"' in installer
    assert "EVIDENCE_LANE_DATA_ROOT" not in installer
    assert "EVIDENCE_LANE_RUNTIME_CONTROL_ROOT" in installer
    assert 'project_binding = "NONE_TRANSPORT_ONLY"' in installer
    assert "host_wide_project_neutral = $true" in installer
    assert (
        'multi_project_and_task_routing = '
        '"EXPLICIT_PLUGIN_PROJECT_ID_AND_TASK_BINDINGS"'
    ) in installer
    assert "per_project_or_task_tunnel_allowed = $false" in installer
    assert 'project_route_argument = "project_id"' in installer
    assert "project_route_argument_required = $true" in installer
    assert "cross_project_fallback_allowed = $false" in installer
    assert "_INTERNAL_EVIDENCE_LANE_MCP_LAYER_DO_NOT_RUN.ps1" in installer
    assert "--control-plane-api-key-ref \"env:CONTROL_PLANE_API_KEY\"" in installer
    assert "--mcp-command $mcpCommand" in installer
    assert '"main-git-release"' in installer
    assert '"versioned-local-testing"' in installer
    assert 'TaskName = "EvidenceLane-Tunnel-$releaseToken-stable-build"' in installer
    assert "exact_visible_tool_count = $exactVisibleToolCount" in installer
    assert "exact_active_read_tool_count = $exactActiveReadToolCount" in installer
    assert "exact_fail_closed_write_tool_count = $exactFailClosedWriteToolCount" in installer
    assert "exact_command_count" not in installer
    assert "exact_hook_event_count = $exactHookEventCount" in installer
    assert "exact_provider_count = $exactProviderCount" in installer
    assert "codex_platform_tunnel_setup_required_once = $true" in installer
    assert (
        'Join-Path $RuntimeControlRoot '
        '"tunnel-runtime-$releaseToken-stable-build"'
    ) in installer
    assert '.codex\\plugins\\runtime\\evidence-lane-plugin' in installer
    assert 'project_authority_lookup = "HIDDEN_REGISTRY_BY_PROJECT_ID"' in installer
    assert "project_authority_root_hardcoded = $false" in installer
    assert "workspace_hardcoded = $false" in installer
    assert "[string]$DataRoot" not in installer
    assert "RuntimeKeyEnvelopeSource" in installer
    assert "active_tunnel_registration = $true" in installer
    assert "pre_3_0_fallback_allowed = $false" in installer
    assert "release_identity_source = \"CODEX_RELEASE_CHANNEL_CONTRACT\"" in installer
    assert "runtime_identity_matches_release = $true" in installer
    assert "prior_versioned_runtimes_retained = $false" in installer
    assert "prior_versioned_tasks_retained = $false" in installer
    assert "prior_versioned_runtime_deletion_required = $true" in installer
    assert "one_active_version_required = $true" in installer
    assert "Manage-EvidenceLaneTunnelVersions.ps1" not in installer
    assert 'active_tunnel_registration = $true' in installer
    assert 'registered_slot = $SlotRole' in installer
    assert "Remove-StoppedPriorTunnelRuntimes" in installer
    assert "Another Evidence Lane tunnel is active" in installer
    assert "could not be proven stopped; activation is blocked" in installer
    assert "$managerCommand.Parameters.ContainsKey($optionalParameter)" in installer
    assert '@("ProfileName", "TaskName", "ReleaseToken")' in installer
    assert '[string]::IsNullOrWhiteSpace($markerValue)' in installer
    assert "Remove-StoppedPriorTunnelTasks" in installer
    assert 'TaskName -like "EvidenceLane-Tunnel-*"' in installer
    assert "if ($Activate)" in installer
    assert "MigrateCurrentRuntime" not in installer
    assert "evidence-lane.versioned-secure-mcp-tunnel-installation.v2" in installer
    assert '"main-git-release" = "stable"' in installer
    assert '"versioned-local-testing" = "local_testing"' in installer
    assert "$release = $slotRelease" in installer
    assert "Google Drive" not in installer
    assert "GDrive" not in installer


def test_installer_classifies_api_persistent_and_ephemeral_host_lifetimes() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert (
        '[ValidateSet("CODEX_APP_INTERACTIVE", "CODEX_CLI_NATIVE", '
        '"HEADLESS_API", "DIRECT_CLI_API")]'
    ) in installer
    assert '[ValidateSet("NATIVE_MCP_AVAILABLE", "HOST_TOOL_GAP")]' in installer
    assert '$HostToolTransport = "NATIVE_MCP_AVAILABLE"' in installer
    assert 'tunnel_requirement = "NOT_REQUIRED_FOR_API_LAYER"' in installer
    assert 'host_tool_transport = "API_DIRECT"' in installer
    assert 'if ($HostToolTransport -eq "NATIVE_MCP_AVAILABLE")' in installer
    assert 'tunnel_requirement = "NOT_REQUIRED_NATIVE_MCP_AVAILABLE"' in installer
    assert 'native_mcp_available = $true' in installer
    assert 'tunnel_requirement = "REQUIRED_FOR_HOST_TOOL_GAP"' in installer
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
    assert 'runtime_selector_source = "CURRENT_ENABLED_PLUGIN_SELECTOR"' in installer
    assert "account_tier_affects_routing = $false" in installer
    assert "api_billing_affects_routing = $false" in installer


def test_installer_reuses_verified_dependencies_and_can_acquire_missing_client() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert "EVIDENCE_LANE_TUNNEL_CLIENT_DOWNLOAD_URI" in installer
    assert "Invoke-WebRequest -Uri $downloadUri" in installer
    assert '$downloadUri.Scheme -ne "https"' in installer
    assert "DOWNLOADED_FROM_CONFIGURED_HTTPS_AND_HASH_VERIFIED" in installer
    assert "does not match the pinned v0.0.10 SHA-256" in installer
    assert (
        'Get-ChildItem -LiteralPath '
        '([IO.Path]::GetFullPath($RuntimeControlRoot))'
    ) in installer
    assert 'Join-Path $_.FullName "bin\\tunnel-client-v0.0.10.exe"' in installer
    assert "Resolve-PriorRuntimeKeyEnvelope" in installer
    assert "tunnel_id_reused = $tunnelIdReused" in installer
    assert "runtime_key_envelope_reused = $runtimeKeyEnvelopeReused" in installer
    assert "profileTunnelId" in installer


def test_manager_exposes_start_status_repair_and_ready_gate() -> None:
    manager = _read("Manage-EvidenceLaneTunnel.ps1")
    assert '[ValidateSet("Start", "Stop", "Status", "Repair", "Remove")]' in manager
    assert "--require-control-plane-poll" in manager
    assert "stable_binary_hash_valid" in manager
    assert "control_plane_poll_ready" in manager
    assert "Get-ScheduledTask" in manager
    assert "Start-ScheduledTask" in manager
    assert 'transport_role = "HOST_NEUTRAL_VERSIONED_SECURE_MCP_TUNNEL"' in manager
    assert 'codex_native_lifecycle_route = "PACKAGE_LOCAL_NATIVE_MCP_ONLY"' in manager
    assert "codex_tunnel_lifecycle_proof_allowed = $false" in manager
    assert "[int]$marker.exact_visible_tool_count" in manager
    assert "[int]$marker.exact_active_read_tool_count" in manager
    assert "[int]$marker.exact_fail_closed_write_tool_count" in manager
    assert "exact_command_count" not in manager
    assert "[int]$marker.exact_hook_event_count" in manager
    assert "[int]$marker.exact_provider_count" in manager
    assert "runtime_key_plaintext_reported = $false" in manager
    assert 'ReleaseToken = "v300"' in manager
    assert "release_token = if ($null -ne $marker)" in manager
    assert (
        "management request does not match the exact release-bound host-wide tunnel marker"
        in manager
    )
    assert "slot_role = if ($null -ne $marker)" in manager
    assert "byte_frozen = if ($null -ne $marker)" in manager
    assert 'project_binding = "NONE_TRANSPORT_ONLY"' in manager
    assert 'project_route_argument = "project_id"' in manager
    assert "project_route_argument_required = $true" in manager
    assert "cross_project_fallback_allowed = $false" in manager
    assert "if (-not $ConfirmRemoval)" in manager
    assert (
        "managed tunnel runtime must remain inside Codex's hidden Evidence Lane runtime root"
        in manager
    )
    assert "installation marker" in manager
    assert "marker.profile_name" in manager
    assert "marker.task_name" in manager
    assert "host_wide_project_neutral" in manager
    assert "per_project_or_task_tunnel_allowed" in manager
    assert 'status = "STOPPED_SAVED"' in manager
    assert "reusable_without_reinstall = $true" in manager


def test_prior_tunnel_version_manager_is_physically_absent() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert not (TUNNEL_SCRIPTS / "Manage-EvidenceLaneTunnelVersions.ps1").exists()
    assert "Remove-StoppedPriorTunnelRuntimes" in installer
    assert "Remove-StoppedPriorTunnelTasks" in installer
    assert "prior_versioned_runtimes_retained = $false" in installer
    assert "prior_versioned_tasks_retained = $false" in installer
    assert "prior_versioned_runtime_deletion_required = $true" in installer



def test_all_tunnel_scripts_use_release_bound_runtime_names() -> None:
    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert '"v" + ($release -replace' in installer
    assert (
        'Join-Path $RuntimeControlRoot '
        '"tunnel-runtime-$releaseToken-stable-build"'
    ) in installer
    assert '"EvidenceLane-Tunnel-$releaseToken-stable-build"' in installer
    assert '"evidence_lane_${releaseToken}"' in installer

    for name in ("EvidenceLaneTunnel.Boot.ps1", "Manage-EvidenceLaneTunnel.ps1"):
        text = _read(name)
        assert 'ReleaseToken = "v300"' in text
        assert '"evidence_lane_${ReleaseToken}"' in text
        assert "release-bound" in text and "tunnel marker" in text
        assert "evidence_lane_v150" not in text
        assert "tunnel-runtime-v150" not in text
        assert "EvidenceLane-Tunnel-v150" not in text
        assert "tunnel-runtime-v140" not in text
        assert "evidence_lane_v140" not in text
        assert "EvidenceLane-Tunnel-v140" not in text

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
    ):
        text = _read(name)
        assert "Resolve-Path -LiteralPath $process.Path" in text
        assert (
            "Get-Sha256 -Path $processPath" in text
            or "Get-FileHash -LiteralPath $processPath -Algorithm SHA256" in text
        )


def test_tunnel_startup_uses_gui_subsystem_no_visible_console_host() -> None:
    host = TUNNEL_SCRIPTS / "EvidenceLaneTunnelHost.exe"
    source = (
        ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "scripts"
        / "codex_release"
        / "windows_tunnel_host"
        / "EvidenceLaneTunnelHost.cs"
    )
    build = (
        ROOT
        / "plugins"
        / "evidence-lane-plugin"
        / "scripts"
        / "codex_release"
        / "Build-EvidenceLaneTunnelHost.ps1"
    )
    assert host.is_file()
    assert "CreateNoWindow = true" in source.read_text(encoding="utf-8")
    assert "/target:winexe" in build.read_text(encoding="utf-8")
    raw = host.read_bytes()
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    optional_header = pe_offset + 24
    magic = struct.unpack_from("<H", raw, optional_header)[0]
    subsystem_offset = optional_header + (88 if magic == 0x20B else 68)
    assert struct.unpack_from("<H", raw, subsystem_offset)[0] == 2
