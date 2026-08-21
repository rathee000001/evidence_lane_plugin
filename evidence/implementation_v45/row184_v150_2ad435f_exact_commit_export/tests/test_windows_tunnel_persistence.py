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
    assert 'ProfileName = "evidence_lane_v150_chatgpt_read"' in boot
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
    assert "-StartWhenAvailable" in installer
    assert "-LogonType Interactive" in installer
    assert "runtime_key_plaintext_written = $false" in installer
    assert 'Read-Host "Tunnel ID from the OpenAI Platform tunnel page"' in installer
    assert "'^tunnel_[A-Za-z0-9]+$'" in installer
    assert 'EVIDENCE_LANE_MCP_EXPOSURE_PROFILE = "CHATGPT_PRO_GOVERNED"' in installer
    assert "EVIDENCE_LANE_DATA_ROOT" in installer
    assert 'project_binding = "NONE_TRANSPORT_ONLY"' in installer
    assert 'project_route_argument = "project_id"' in installer
    assert "project_route_argument_required = $true" in installer
    assert "cross_project_fallback_allowed = $false" in installer
    assert "_INTERNAL_CHATGPT_READ_MCP_DO_NOT_RUN.ps1" in installer
    assert "--control-plane-api-key-ref \"env:CONTROL_PLANE_API_KEY\"" in installer
    assert "--mcp-command $mcpCommand" in installer
    assert 'TaskName = "EvidenceLane-Tunnel-v150"' in installer
    assert "exact_visible_tool_count = 62" in installer
    assert "exact_active_read_tool_count = 21" in installer
    assert "exact_fail_closed_write_tool_count = 41" in installer
    assert "chatgpt_link_required_once = $true" in installer
    assert "Google Drive" not in installer
    assert "GDrive" not in installer


def test_manager_exposes_start_status_repair_and_ready_gate() -> None:
    manager = _read("Manage-EvidenceLaneTunnel.ps1")
    assert '[ValidateSet("Start", "Status", "Repair", "Remove")]' in manager
    assert "--require-control-plane-poll" in manager
    assert "stable_binary_hash_valid" in manager
    assert "control_plane_poll_ready" in manager
    assert "chatgpt_governed_profile_configured" in manager
    assert "exact_visible_tool_count = 62" in manager
    assert "exact_active_read_tool_count = 21" in manager
    assert "exact_fail_closed_write_tool_count = 41" in manager
    assert "runtime_key_plaintext_reported = $false" in manager
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


def test_all_tunnel_scripts_use_isolated_v150_runtime_names() -> None:
    for name in (
        "Install-EvidenceLaneTunnel.ps1",
        "EvidenceLaneTunnel.Boot.ps1",
        "Manage-EvidenceLaneTunnel.ps1",
    ):
        text = _read(name)
        assert "evidence_lane_v150" in text
        assert "tunnel-runtime-v150" in text
        assert "EvidenceLane-Tunnel-v150" in text or name == "EvidenceLaneTunnel.Boot.ps1"
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

    installer = _read("Install-EvidenceLaneTunnel.ps1")
    assert "Get-FileHash -LiteralPath $process.Path -Algorithm SHA256" in installer

    for name in (
        "EvidenceLaneTunnel.Boot.ps1",
        "Manage-EvidenceLaneTunnel.ps1",
    ):
        text = _read(name)
        assert "Resolve-Path -LiteralPath $process.Path" in text
        assert "Get-FileHash -LiteralPath $processPath -Algorithm SHA256" in text
