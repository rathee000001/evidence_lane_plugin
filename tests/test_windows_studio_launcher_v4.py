"""Stable-root Windows launcher keeps login and shortcut commands bounded."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from evidence_lane_plugin.shortcuts import StudioShortcut
from evidence_lane_plugin.startup import LoginStartup

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "plugins/evidence-lane-plugin/scripts/windows_studio_launcher/EvidenceLaneStudioLauncher.cs"
)
BINARY = SOURCE.with_name("EvidenceLaneStudioLauncher.exe")
BUILD = SOURCE.with_name("EvidenceLaneStudioLauncher.build.json")
ICON = SOURCE.parent.parent / "windows_studio_installer/EvidenceLaneStudio.ico"


def load_registration_script():
    path = ROOT / "plugins/evidence-lane-plugin/scripts/register_installed_runtime.py"
    spec = importlib.util.spec_from_file_location("register_installed_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ApplicationBackend:
    def __init__(self, root: Path, *, failure: Exception | None = None):
        self.root = root
        self.failure = failure
        self.installs = 0
        self.runtime = None

    def install(self, _setup: Path, runtime: Path) -> None:
        if self.failure is not None:
            raise self.failure
        self.installs += 1
        self.runtime = runtime

    def status(self, runtime: Path, version: str) -> dict:
        application = self.root / "Programs/Evidence Lane Studio"
        executable = application / "EvidenceLaneStudio.exe"
        icon = application / "EvidenceLaneStudio.ico"
        start = self.root / "Start/Evidence Lane Studio.lnk"
        desktop = self.root / "Desktop/Evidence Lane Studio.lnk"
        return {
            "registered": self.runtime is not None,
            "app_id": "EvidenceLane.Studio",
            "display_name": "Evidence Lane Studio",
            "display_version": version,
            "publisher": "Evidence Lane",
            "install_location": str(application),
            "display_icon": str(icon),
            "uninstall_command": str(application / "unins000.exe"),
            "app_path": str(executable),
            "runtime_root": str(runtime),
            "expected_runtime_root": str(runtime),
            "expected_version": version,
            "application_root": str(application),
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


def test_packaged_launcher_build_identity_and_read_only_layout_inspection(tmp_path: Path) -> None:
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    assert build["schema"] == "evidence-lane.windows-studio-launcher-build.v4"
    assert build["status"] == "PASS"
    assert build["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert build["icon_sha256"] == hashlib.sha256(ICON.read_bytes()).hexdigest()
    assert build["output_sha256"] == hashlib.sha256(BINARY.read_bytes()).hexdigest()
    assert build["output_bytes"] == BINARY.stat().st_size
    assert build["version"] == "4.0.10.0"
    install = tmp_path / "shared"
    release = install
    launcher = release / "app/EvidenceLaneStudio.exe"
    launcher.parent.mkdir(parents=True)
    shutil.copyfile(BINARY, launcher)
    plugin = release / "plugin"
    (plugin / "scripts").mkdir(parents=True)
    (plugin / "scripts/launch_studio.py").write_text("# fixture\n", encoding="utf-8")
    runtime = release / "engine"
    pythonw = runtime / "venv/Scripts/pythonw.exe"
    pythonw.parent.mkdir(parents=True)
    pythonw.write_bytes(b"fixture")
    (install / "installation.json").write_text("{}\n", encoding="utf-8")
    result = subprocess.run(
        [str(launcher), "--inspect"],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0
    inspection = json.loads(
        (install / "diagnostics/launcher-inspection.json").read_text()
    )
    assert inspection == {
        "status": "PASS",
        "installation_root": str(install),
        "release_root": str(release),
        "plugin_root": str(plugin),
        "runtime_root": str(runtime),
        "command_length": inspection["command_length"],
        "project_state_changed": False,
    }
    assert inspection["command_length"] > 0
    assert subprocess.run(
        [str(launcher), "--unknown"], timeout=15, check=False
    ).returncode == 64


def test_login_and_shortcut_target_the_stable_launcher(tmp_path: Path) -> None:
    release = tmp_path / "EvidenceLaneStudio"
    runtime = release / "engine"
    pythonw = runtime / "venv/Scripts/pythonw.exe"
    launcher = release / "app/EvidenceLaneStudio.exe"
    pythonw.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    pythonw.write_bytes(b"fixture")
    launcher.write_bytes(b"fixture")
    login = LoginStartup(
        pythonw,
        runtime,
        launcher_executable=launcher,
        backend=object(),
    )
    command = login.command()
    assert command == subprocess.list2cmdline([str(launcher), "--startup"])
    assert len(command) < 260
    shortcut = StudioShortcut(
        pythonw,
        runtime,
        tmp_path / "menu",
        launcher_executable=launcher,
        backend=lambda *_args, **_kwargs: {},
    )
    specification = shortcut.specification()
    assert specification["target"] == str(launcher)
    assert specification["arguments"] == "--open"
    assert specification["icon_location"] == f"{launcher},0"
    default_launcher = Path(
        "C:/Apps/EvidenceLaneStudio/app/EvidenceLaneStudio.exe"
    )
    assert len(subprocess.list2cmdline([str(default_launcher), "--startup"])) < 260


def test_stable_installation_registration_is_read_back_and_idempotent(tmp_path: Path) -> None:
    module = load_registration_script()
    installation = tmp_path / "shared"
    release = installation
    runtime = release / "engine"
    pythonw = runtime / "venv/Scripts/pythonw.exe"
    launcher = release / "app/EvidenceLaneStudio.exe"
    plugin = release / "plugin"
    pythonw.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    (plugin / "scripts").mkdir(parents=True)
    pythonw.write_bytes(b"fixture")
    launcher.write_bytes(b"fixture")
    (plugin / "scripts/run_engine.py").write_text("# fixture\n", encoding="utf-8")
    setup = plugin / "scripts/windows_studio_installer/EvidenceLaneStudioSetup.exe"
    setup.parent.mkdir(parents=True)
    setup.write_bytes(b"fixture")

    class RunValue:
        value = None

        def read(self):
            return self.value

        def write(self, value):
            self.value = value

        def remove(self):
            self.value = None

    run_value = RunValue()
    application = ApplicationBackend(tmp_path)
    first = module.register(
        installation,
        release,
        startup_backend=run_value,
        application_backend=application,
        system="Windows",
    )
    second = module.register(
        installation,
        release,
        startup_backend=run_value,
        application_backend=application,
        system="Windows",
    )
    assert first["status"] == second["status"] == "PASS"
    assert first["startup"]["registered"] is True
    assert first["windows_application"]["registration_verified"] is True
    assert first["windows_application"]["app_id"] == "EvidenceLane.Studio"
    assert first["shortcuts"]["start_menu"]["registered"] is True
    assert first["shortcuts"]["desktop"]["registered"] is True
    assert application.installs == 2
    assert len(run_value.value) < 260
    assert "--startup" in run_value.value
    persisted = json.loads((installation / "registration.json").read_text())
    body = dict(persisted)
    receipt = body.pop("receipt_sha256")
    encoded = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert receipt == hashlib.sha256(encoded).hexdigest()
    assert persisted["project_state_changed"] is False


def test_owned_pre_icon_shortcut_is_atomically_upgraded(tmp_path: Path) -> None:
    runtime = tmp_path / "engine"
    pythonw = runtime / "venv/Scripts/pythonw.exe"
    launcher = tmp_path / "app/EvidenceLaneStudio.exe"
    pythonw.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    pythonw.write_bytes(b"fixture")
    launcher.write_bytes(b"fixture")

    def shortcut_backend(path, *, specification=None):
        if specification is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(specification), encoding="utf-8")
        return json.loads(path.read_text(encoding="utf-8"))

    owner = StudioShortcut(
        pythonw,
        runtime,
        tmp_path / "desktop",
        launcher_executable=launcher,
        backend=shortcut_backend,
    )
    old_specification = dict(owner.specification())
    old_specification.pop("icon_location")
    owner.path.parent.mkdir(parents=True)
    owner.path.write_text(json.dumps(old_specification), encoding="utf-8")
    owner.receipt.write_text(
        json.dumps(
            {
                "sha256": hashlib.sha256(owner.path.read_bytes()).hexdigest(),
                "specification": old_specification,
            }
        ),
        encoding="utf-8",
    )
    result = owner.install()
    assert result["updated_owned_shortcut"] is True
    assert shortcut_backend(owner.path) == owner.specification()
    receipt = json.loads(owner.receipt.read_text(encoding="utf-8"))
    assert receipt["specification"]["icon_location"] == f"{launcher},0"


def test_default_registration_uses_normal_windows_application_installer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_registration_script()
    installation = tmp_path / "shared"
    runtime = installation / "engine"
    pythonw = runtime / "venv/Scripts/pythonw.exe"
    launcher = installation / "app/EvidenceLaneStudio.exe"
    plugin = installation / "plugin"
    pythonw.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    (plugin / "scripts").mkdir(parents=True)
    pythonw.write_bytes(b"fixture")
    launcher.write_bytes(b"fixture")
    (plugin / "scripts/run_engine.py").write_text("# fixture\n", encoding="utf-8")
    setup = plugin / "scripts/windows_studio_installer/EvidenceLaneStudioSetup.exe"
    setup.parent.mkdir(parents=True)
    setup.write_bytes(b"fixture")

    class RunValue:
        value = None

        def read(self):
            return self.value

        def write(self, value):
            self.value = value

        def remove(self):
            self.value = None

    application = ApplicationBackend(tmp_path)
    result = module.register(
        installation,
        installation,
        startup_backend=RunValue(),
        application_backend=application,
        system="Windows",
    )
    assert result["start_menu_scope"] == "direct_programs_root"
    assert result["desktop_resolution"] == "windows_user_shell_folder"
    assert result["windows_application"]["display_name"] == "Evidence Lane Studio"
    assert result["windows_application"]["display_version"] == "4.0.10"
    assert result["windows_application"]["app_path"].endswith("EvidenceLaneStudio.exe")
    assert application.installs == 1


def test_registration_failure_rolls_back_new_login_and_shortcut_entries(tmp_path: Path) -> None:
    module = load_registration_script()
    installation = tmp_path / "shared"
    runtime = installation / "engine"
    pythonw = runtime / "venv/Scripts/pythonw.exe"
    launcher = installation / "app/EvidenceLaneStudio.exe"
    plugin = installation / "plugin"
    pythonw.parent.mkdir(parents=True)
    launcher.parent.mkdir(parents=True)
    (plugin / "scripts").mkdir(parents=True)
    pythonw.write_bytes(b"fixture")
    launcher.write_bytes(b"fixture")
    (plugin / "scripts/run_engine.py").write_text("# fixture\n", encoding="utf-8")
    setup = plugin / "scripts/windows_studio_installer/EvidenceLaneStudioSetup.exe"
    setup.parent.mkdir(parents=True)
    setup.write_bytes(b"fixture")

    class RunValue:
        value = None

        def read(self):
            return self.value

        def write(self, value):
            self.value = value

        def remove(self):
            self.value = None

    run_value = RunValue()
    application = ApplicationBackend(
        tmp_path, failure=RuntimeError("fixture application failure")
    )
    with pytest.raises(RuntimeError, match="fixture application failure"):
        module.register(
            installation,
            installation,
            startup_backend=run_value,
            application_backend=application,
            system="Windows",
        )
    assert run_value.value is None
    assert not (installation / "registration.json").exists()
