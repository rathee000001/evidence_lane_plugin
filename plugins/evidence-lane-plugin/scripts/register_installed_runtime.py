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


def register(
    installation_root: Path,
    release_root: Path,
    *,
    startup_backend=None,
    shortcut_backend=None,
    appdata: Path | None = None,
    system: str | None = None,
) -> dict[str, Any]:
    from evidence_lane_plugin.projects import atomic_json
    from evidence_lane_plugin.shortcuts import StudioShortcut
    from evidence_lane_plugin.startup import LoginStartup

    current_system = platform.system() if system is None else system
    if current_system != "Windows":
        raise RuntimeError("WINDOWS_REGISTRATION_REQUIRED")
    installation = installation_root.resolve(strict=True)
    release = release_root.resolve(strict=True)
    if release.parent != installation / "releases":
        raise RuntimeError("RELEASE_ROOT_OUTSIDE_INSTALLATION")
    plugin_root = release / "app/plugin"
    runtime_root = release / "runtime/engine"
    pythonw = release / "runtime/engine/venv/Scripts/pythonw.exe"
    launcher = release / "app/EvidenceLaneStudio.exe"
    if (
        not plugin_root.is_dir()
        or not runtime_root.is_dir()
        or not pythonw.is_file()
        or not launcher.is_file()
    ):
        raise RuntimeError("INSTALLED_ENTRYPOINT_MISSING")
    destination = appdata or Path(
        os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming"))
    ) / "Microsoft/Windows/Start Menu/Programs/Evidence Lane"
    startup = LoginStartup(
        pythonw,
        runtime_root,
        plugin_root=plugin_root,
        launcher_executable=launcher,
        backend=startup_backend,
    ).install()
    shortcut = StudioShortcut(
        pythonw,
        runtime_root,
        destination,
        plugin_root=plugin_root,
        launcher_executable=launcher,
        backend=shortcut_backend,
    ).install()
    body = {
        "schema": "evidence-lane.installed-runtime-registration.v4",
        "status": "PASS",
        "installation_root": str(installation),
        "release_root": str(release),
        "startup": startup,
        "shortcut": shortcut,
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
