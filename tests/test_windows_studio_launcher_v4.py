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


def load_registration_script():
    path = ROOT / "plugins/evidence-lane-plugin/scripts/register_installed_runtime.py"
    spec = importlib.util.spec_from_file_location("register_installed_runtime", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_packaged_launcher_build_identity_and_read_only_layout_inspection(tmp_path: Path) -> None:
    build = json.loads(BUILD.read_text(encoding="utf-8"))
    assert build["schema"] == "evidence-lane.windows-studio-launcher-build.v4"
    assert build["status"] == "PASS"
    assert build["source_sha256"] == hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert build["output_sha256"] == hashlib.sha256(BINARY.read_bytes()).hexdigest()
    assert build["output_bytes"] == BINARY.stat().st_size
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

    class RunValue:
        value = None

        def read(self):
            return self.value

        def write(self, value):
            self.value = value

        def remove(self):
            self.value = None

    run_value = RunValue()
    shortcut_specification = None

    def shortcut(path, *, specification=None):
        nonlocal shortcut_specification
        if specification is not None:
            path.write_bytes(b"shortcut")
            shortcut_specification = specification
        assert shortcut_specification is not None
        return shortcut_specification

    menu = tmp_path / "menu"
    first = module.register(
        installation,
        release,
        startup_backend=run_value,
        shortcut_backend=shortcut,
        appdata=menu,
        desktop=tmp_path / "desktop",
        system="Windows",
    )
    second = module.register(
        installation,
        release,
        startup_backend=run_value,
        shortcut_backend=shortcut,
        appdata=menu,
        desktop=tmp_path / "desktop",
        system="Windows",
    )
    assert first["status"] == second["status"] == "PASS"
    assert first["startup"]["registered"] is True
    assert first["shortcuts"]["start_menu"]["changed"] is True
    assert first["shortcuts"]["desktop"]["changed"] is True
    assert second["shortcuts"]["start_menu"]["changed"] is False
    assert second["shortcuts"]["desktop"]["changed"] is False
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

    class RunValue:
        value = None

        def read(self):
            return self.value

        def write(self, value):
            self.value = value

        def remove(self):
            self.value = None

    run_value = RunValue()
    specifications = {}

    def shortcut(path, *, specification=None):
        key = str(path.parent)
        if specification is not None:
            if "desktop" in path.parts:
                raise RuntimeError("fixture desktop failure")
            path.write_bytes(b"shortcut")
            specifications[key] = specification
        return specifications[key]

    menu = tmp_path / "menu"
    desktop = tmp_path / "desktop"
    with pytest.raises(RuntimeError, match="fixture desktop failure"):
        module.register(
            installation,
            installation,
            startup_backend=run_value,
            shortcut_backend=shortcut,
            appdata=menu,
            desktop=desktop,
            system="Windows",
        )
    assert run_value.value is None
    assert not (menu / "Evidence Lane Studio.lnk").exists()
    assert not (menu / ".evidence-lane-studio-shortcut.json").exists()
    assert not (installation / "registration.json").exists()
