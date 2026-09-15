"""Register login startup and the visible Studio shortcut for one exact active release."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def windows_desktop_root() -> Path:
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, "Desktop")
    except OSError as exc:
        raise RuntimeError("WINDOWS_DESKTOP_FOLDER_UNAVAILABLE") from exc
    if kind not in {winreg.REG_SZ, winreg.REG_EXPAND_SZ} or not isinstance(value, str):
        raise RuntimeError("WINDOWS_DESKTOP_FOLDER_INVALID")
    selected = Path(os.path.expandvars(value)).absolute()
    if not selected.is_absolute() or any(character in str(selected) for character in "\r\n\0"):
        raise RuntimeError("WINDOWS_DESKTOP_FOLDER_INVALID")
    return selected


def register(
    installation_root: Path,
    release_root: Path,
    *,
    startup_backend=None,
    shortcut_backend=None,
    appdata: Path | None = None,
    desktop: Path | None = None,
    system: str | None = None,
) -> dict[str, Any]:
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.projects import atomic_json
    from evidence_lane_plugin.shortcuts import StudioShortcut
    from evidence_lane_plugin.startup import LoginStartup

    current_system = platform.system() if system is None else system
    if current_system != "Windows":
        raise RuntimeError("WINDOWS_REGISTRATION_REQUIRED")
    installation = installation_root.resolve(strict=True)
    release = release_root.resolve(strict=True)
    if release != installation:
        raise RuntimeError("RELEASE_ROOT_OUTSIDE_INSTALLATION")
    plugin_root = release / "plugin"
    runtime_root = release / "engine"
    pythonw = release / "engine/venv/Scripts/pythonw.exe"
    launcher = release / "app/EvidenceLaneStudio.exe"
    if (
        not plugin_root.is_dir()
        or not runtime_root.is_dir()
        or not pythonw.is_file()
        or not launcher.is_file()
    ):
        raise RuntimeError("INSTALLED_ENTRYPOINT_MISSING")
    programs = Path(
        os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))
    ) / "Microsoft/Windows/Start Menu/Programs"
    start_menu = appdata or programs
    legacy_start_menu = None if appdata is not None else programs / "Evidence Lane"
    desktop_root = desktop or windows_desktop_root()
    startup_owner = LoginStartup(
        pythonw,
        runtime_root,
        plugin_root=plugin_root,
        launcher_executable=launcher,
        backend=startup_backend,
    )
    start_menu_owner = StudioShortcut(
        pythonw,
        runtime_root,
        start_menu,
        plugin_root=plugin_root,
        launcher_executable=launcher,
        backend=shortcut_backend,
    )
    desktop_owner = StudioShortcut(
        pythonw,
        runtime_root,
        desktop_root,
        plugin_root=plugin_root,
        launcher_executable=launcher,
        backend=shortcut_backend,
    )
    startup_before = startup_owner.status()
    start_menu_shortcut = None
    desktop_shortcut = None
    legacy_shortcut = None
    try:
        startup = startup_owner.install()
        start_menu_shortcut = start_menu_owner.install()
        desktop_shortcut = desktop_owner.install()
        if legacy_start_menu is not None:
            legacy_owner = StudioShortcut(
                pythonw,
                runtime_root,
                legacy_start_menu,
                plugin_root=plugin_root,
                launcher_executable=launcher,
                backend=shortcut_backend,
            )
            if legacy_owner.path.exists():
                legacy_shortcut = legacy_owner.uninstall()
            else:
                legacy_shortcut = {
                    "registered": False,
                    "path": str(legacy_owner.path),
                }
    except Exception as reason:
        rollback_errors = []
        for owner, result in (
            (desktop_owner, desktop_shortcut),
            (start_menu_owner, start_menu_shortcut),
        ):
            if result is not None and result.get("changed") is True:
                try:
                    owner.uninstall()
                except (LaneError, OSError, RuntimeError) as rollback_error:
                    rollback_errors.append(type(rollback_error).__name__)
        if startup_before.get("entry_present") is False:
            try:
                startup_owner.uninstall()
            except (LaneError, OSError, RuntimeError) as rollback_error:
                rollback_errors.append(type(rollback_error).__name__)
        if rollback_errors:
            raise RuntimeError("INSTALLATION_REGISTRATION_ROLLBACK_FAILED") from reason
        raise
    body = {
        "schema": "evidence-lane.installed-runtime-registration.v4",
        "status": "PASS",
        "installation_root": str(installation),
        "release_root": str(release),
        "startup": startup,
        "shortcuts": {
            "start_menu": start_menu_shortcut,
            "desktop": desktop_shortcut,
            "legacy_nested_start_menu": legacy_shortcut,
        },
        "start_menu_scope": "direct_programs_root",
        "desktop_resolution": "windows_user_shell_folder",
        "current_windows_user": True,
        "studio_visible": True,
        "engine_console_hidden": True,
        "project_state_changed": False,
    }
    receipt = {
        **body,
        "receipt_sha256": hashlib.sha256(_canonical(body)).hexdigest(),
    }
    atomic_json(installation / "registration.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installation-root", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(register(args.installation_root, args.release_root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
