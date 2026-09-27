from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.windows_application import (
    APP_ID,
    DISPLAY_NAME,
    WindowsStudioApplication,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/evidence-lane-plugin"
INSTALLER = PLUGIN / "scripts/windows_studio_installer"


def test_windows_setup_build_is_bound_to_icon_shell_and_installer() -> None:
    receipt = json.loads(
        (INSTALLER / "EvidenceLaneStudioSetup.build.json").read_text(encoding="utf-8")
    )
    assert receipt["schema"] == "evidence-lane.windows-studio-setup-build.v4"
    assert receipt["status"] == "PASS"
    assert receipt["version"] == "4.0.10"
    assert receipt["app_user_model_id"] == APP_ID
    expected = {
        "source_sha256": "EvidenceLaneStudioShell.cs",
        "icon_sha256": "EvidenceLaneStudio.ico",
        "definition_sha256": "EvidenceLaneStudio.iss",
        "shell_sha256": "EvidenceLaneStudioShell.exe",
        "setup_sha256": "EvidenceLaneStudioSetup.exe",
    }
    for key, name in expected.items():
        assert receipt[key] == hashlib.sha256((INSTALLER / name).read_bytes()).hexdigest()
    assert receipt["shell_bytes"] == (INSTALLER / "EvidenceLaneStudioShell.exe").stat().st_size
    assert receipt["setup_bytes"] == (INSTALLER / "EvidenceLaneStudioSetup.exe").stat().st_size


def test_windows_setup_declares_normal_application_identity() -> None:
    definition = (INSTALLER / "EvidenceLaneStudio.iss").read_text(encoding="utf-8")
    for required in (
        'AppId=EvidenceLane.Studio',
        'AppName={#MyAppName}',
        'UninstallDisplayName={#MyAppName}',
        'DefaultDirName={localappdata}\\Programs\\Evidence Lane Studio',
        'AppUserModelID: "{#MyAppUserModelId}"',
        'Software\\Microsoft\\Windows\\CurrentVersion\\App Paths\\EvidenceLaneStudio.exe',
        'Software\\RegisteredApplications',
        'EvidenceLaneStudioSetup',
    ):
        assert required in definition
    assert 'Evidence Lane Studio' in definition
    assert 'Evidence Lane.lnk' not in definition


class FakeApplicationBackend:
    def __init__(self, root: Path):
        self.application_root = root / "application"
        self.installs = []
        self.value = {"registered": False}

    def install(self, setup: Path, runtime_root: Path) -> None:
        self.installs.append((setup, runtime_root))
        self.application_root.mkdir(parents=True)
        executable = self.application_root / "EvidenceLaneStudio.exe"
        icon = self.application_root / "EvidenceLaneStudio.ico"
        start = self.application_root / "Evidence Lane Studio.lnk"
        desktop = self.application_root / "Evidence Lane Studio desktop.lnk"
        for path in (executable, icon, start, desktop):
            path.write_bytes(b"fixture")
        self.value = {
            "registered": True,
            "app_id": APP_ID,
            "display_name": DISPLAY_NAME,
            "display_version": "4.0.10",
            "publisher": "Evidence Lane",
            "install_location": str(self.application_root),
            "display_icon": str(icon),
            "uninstall_command": str(self.application_root / "uninstall.exe"),
            "app_path": str(executable),
            "runtime_root": str(runtime_root),
            "expected_runtime_root": str(runtime_root),
            "expected_version": "4.0.10",
            "application_root": str(self.application_root),
            "executable": str(executable),
            "icon": str(icon),
            "start_menu_shortcut": str(start),
            "desktop_shortcut": str(desktop),
            "executable_present": True,
            "icon_present": True,
            "start_menu_present": True,
            "desktop_present": True,
            "project_state_changed": False,
        }

    def status(self, _runtime_root: Path, _version: str):
        return dict(self.value)


def test_windows_application_install_is_exact_and_idempotently_readable(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    setup = tmp_path / "EvidenceLaneStudioSetup.exe"
    setup.write_bytes(b"setup")
    backend = FakeApplicationBackend(tmp_path)
    owner = WindowsStudioApplication(
        runtime, setup, "4.0.10", backend=backend, system=lambda: "Windows"
    )
    result = owner.install()
    assert result["registration_verified"] is True
    assert result["display_name"] == DISPLAY_NAME
    assert result["app_id"] == APP_ID
    assert result["project_state_changed"] is False
    assert backend.installs == [(setup, runtime)]
    assert owner.status()["display_version"] == "4.0.10"


def test_windows_application_rejects_partial_registration(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    setup = tmp_path / "EvidenceLaneStudioSetup.exe"
    setup.write_bytes(b"setup")
    backend = FakeApplicationBackend(tmp_path)
    backend.value = {"registered": True, "display_name": "Wrong application"}
    owner = WindowsStudioApplication(
        runtime, setup, "4.0.10", backend=backend, system=lambda: "Windows"
    )
    with pytest.raises(LaneError) as failure:
        owner.status()
    assert failure.value.code == "STUDIO_WINDOWS_REGISTRATION_INVALID"


def test_plugin_mcp_namespace_does_not_shadow_native_codex_tools() -> None:
    manifest = json.loads((PLUGIN / ".mcp.json").read_text(encoding="utf-8"))
    assert set(manifest["mcpServers"]) == {"evidence-lane"}
    server = manifest["mcpServers"]["evidence-lane"]
    assert server["required"] is False
    encoded = json.dumps(manifest).casefold()
    for forbidden in ("codex_app", "send_message_to_thread", "list_threads"):
        assert forbidden not in encoded


def test_current_retained_tool_catalog_is_registry_derived_and_complete() -> None:
    definitions = json.loads(
        (PLUGIN / "toolchains/tool-definitions.v4.json").read_text(encoding="utf-8")
    )
    catalog = json.loads(
        (PLUGIN / "toolchains/tool-catalog.v4.json").read_text(encoding="utf-8")
    )
    assert definitions["entry_count"] == len(definitions["entries"]) == 103
    assert catalog["base_entry_count"] + catalog["additional_entry_count"] == 103
    assert len(catalog["entries"]) == 103
