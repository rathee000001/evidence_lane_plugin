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
    assert "Google Drive" not in installer
    assert "GDrive" not in installer


def test_manager_exposes_start_status_repair_and_ready_gate() -> None:
    manager = _read("Manage-EvidenceLaneTunnel.ps1")
    assert '[ValidateSet("Start", "Status", "Repair")]' in manager
    assert "--require-control-plane-poll" in manager
    assert "stable_binary_hash_valid" in manager
    assert "control_plane_poll_ready" in manager
    assert "runtime_key_plaintext_reported = $false" in manager
