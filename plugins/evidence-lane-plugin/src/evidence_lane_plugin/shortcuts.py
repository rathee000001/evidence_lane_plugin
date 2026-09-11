"""Owned Windows launcher shortcuts. Registration belongs to the installer."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from .errors import LaneError
from .locking import RuntimeLock
from .projects import atomic_json
from .startup import LoginStartup
from .storage import reject_links

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$studioRequest = [Console]::In.ReadToEnd() | ConvertFrom-Json
$studioShell = New-Object -ComObject WScript.Shell
$studioLink = $studioShell.CreateShortcut([string]$studioRequest.path)
if ($studioRequest.mode -eq 'write') {
    $studioLink.TargetPath = [string]$studioRequest.target
    $studioLink.Arguments = [string]$studioRequest.arguments
    $studioLink.WorkingDirectory = [string]$studioRequest.working_directory
    $studioLink.Description = 'Open Evidence Lane Studio; start the local engine if needed.'
    $studioLink.WindowStyle = 1
    $studioLink.Save()
}
@{target=$studioLink.TargetPath;arguments=$studioLink.Arguments;working_directory=$studioLink.WorkingDirectory;window_style=$studioLink.WindowStyle} | ConvertTo-Json -Compress
"""


def shell_link(path: Path, *, specification: dict | None = None) -> dict:
    if os.name != "nt":
        raise LaneError("WINDOWS_SHORTCUT_UNAVAILABLE", "This launcher requires Windows.")
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    reject_links(executable, Path(executable.anchor))
    request = {"path": str(path), "mode": "write" if specification else "read"} | (specification or {})
    try:
        completed = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            input=json.dumps(request, ensure_ascii=True), capture_output=True, text=True, encoding="utf-8",
            timeout=15, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        if completed.returncode or len(completed.stdout) > 16_384:
            raise ValueError()
        value = json.loads(completed.stdout)
        if not isinstance(value, dict) or set(value) != {"target", "arguments", "working_directory", "window_style"}:
            raise ValueError()
        return value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise LaneError("SHORTCUT_OPERATION_FAILED", "Windows did not confirm the requested shortcut operation.") from None


class StudioShortcut:
    def __init__(
        self,
        interpreter: Path,
        runtime_root: Path,
        destination: Path,
        *,
        plugin_root: Path | None = None,
        launcher_executable: Path | None = None,
        backend=None,
    ):
        self.interpreter, self.root = interpreter.absolute(), runtime_root.absolute()
        self.plugin_root = plugin_root.absolute() if plugin_root is not None else None
        self.launcher_executable = (
            launcher_executable.absolute() if launcher_executable is not None else None
        )
        self.destination = destination.absolute()
        self.path = self.destination / "Evidence Lane Studio.lnk"
        self.receipt = self.destination / ".evidence-lane-studio-shortcut.json"
        self.backend = backend or shell_link

    def specification(self) -> dict:
        startup = LoginStartup(
            self.interpreter,
            self.root,
            plugin_root=self.plugin_root,
            launcher_executable=self.launcher_executable,
        )
        startup.command()  # Shared runtime/path validation.
        if self.launcher_executable is not None:
            target = self.launcher_executable
            arguments = ["--open"]
        elif self.plugin_root is None:
            target = self.interpreter
            arguments = [
                "-I",
                "-m",
                "evidence_lane_plugin.service",
                "--runtime-root",
                str(self.root),
            ]
        else:
            target = self.interpreter
            arguments = [
                "-B",
                str(self.plugin_root / "scripts/run_engine.py"),
                "--runtime-root",
                str(self.root),
            ]
        return {"target": str(target), "working_directory": str(self.root), "window_style": 1,
                "arguments": subprocess.list2cmdline(arguments)}

    def _owned(self) -> bool:
        reject_links(self.path, Path(self.path.anchor))
        reject_links(self.receipt, Path(self.receipt.anchor))
        if not self.path.exists():
            return False
        try:
            if self.path.stat().st_size > 65_536 or self.receipt.stat().st_size > 16_384:
                raise ValueError()
            receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
            if receipt.get("sha256") != hashlib.sha256(self.path.read_bytes()).hexdigest():
                raise ValueError()
            if receipt.get("specification") != self.backend(self.path):
                raise ValueError()
            return True
        except (OSError, ValueError, TypeError):
            raise LaneError("SHORTCUT_CHANGED", "The existing shortcut has no matching ownership receipt.") from None

    def install(self) -> dict:
        specification = self.specification()
        reject_links(self.destination, Path(self.destination.anchor))
        self.destination.mkdir(parents=True, exist_ok=True)
        with RuntimeLock(self.root / "shortcut.lock"):
            if self._owned():
                if self.backend(self.path) != specification:
                    raise LaneError("SHORTCUT_INSTALLATION_CHANGED", "Remove the previously selected shortcut before changing its installation.")
                return {"registered": True, "path": str(self.path), "changed": False}
            temporary = self.destination / (".pending-studio-" + str(uuid4()) + ".lnk")
            try:
                if self.backend(temporary, specification=specification) != specification:
                    raise LaneError("SHORTCUT_WRITE_UNVERIFIED", "Windows returned different shortcut settings.")
                if self.path.exists():
                    raise LaneError("SHORTCUT_CHANGED", "The shortcut path changed during registration.")
                os.replace(temporary, self.path)
                atomic_json(self.receipt, {"sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                                           "specification": specification})
            finally:
                temporary.unlink(missing_ok=True)
        return {"registered": True, "path": str(self.path), "changed": True}

    def uninstall(self) -> dict:
        with RuntimeLock(self.root / "shortcut.lock"):
            if self._owned():
                if self.backend(self.path) != self.specification():
                    raise LaneError("SHORTCUT_INSTALLATION_CHANGED", "The shortcut belongs to another installation.")
                self.path.unlink()
                self.receipt.unlink()
        return {"registered": False, "path": str(self.path)}
