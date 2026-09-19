"""Owned Windows launcher shortcuts. Registration belongs to the installer."""

from __future__ import annotations

import base64
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
[Console]::Error.WriteLine('EL_SHORTCUT_PHASE=started')
$ErrorActionPreference = 'Stop'
function Read-EvidenceLaneShortcutValue([string]$name) {
    $encoded = [Environment]::GetEnvironmentVariable($name, 'Process')
    if ($null -eq $encoded) { return '' }
    return [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
}
function Write-EvidenceLaneShortcutValue([string]$value) {
    return [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($value))
}
$studioPath = Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_PATH_B64'
$studioMode = Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_MODE_B64'
[Console]::Error.WriteLine('EL_SHORTCUT_PHASE=request_loaded')
$studioShell = New-Object -ComObject WScript.Shell
[Console]::Error.WriteLine('EL_SHORTCUT_PHASE=shell_created')
$studioLink = $studioShell.CreateShortcut($studioPath)
[Console]::Error.WriteLine('EL_SHORTCUT_PHASE=link_loaded')
if ($studioMode -eq 'write') {
    $studioLink.TargetPath = Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_TARGET_B64'
    $studioLink.Arguments = Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_ARGUMENTS_B64'
    $studioLink.WorkingDirectory = Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_WORKING_DIRECTORY_B64'
    $studioLink.Description = 'Open Evidence Lane Studio; start the local engine if needed.'
    $studioLink.IconLocation = Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_ICON_LOCATION_B64'
    $studioLink.WindowStyle = [int](Read-EvidenceLaneShortcutValue 'EVIDENCE_LANE_SHORTCUT_WINDOW_STYLE_B64')
    $studioLink.Save()
    [Console]::Error.WriteLine('EL_SHORTCUT_PHASE=link_saved')
}
[Console]::Error.WriteLine('EL_SHORTCUT_PHASE=readback_ready')
$studioReadback = '{"target":"' + (Write-EvidenceLaneShortcutValue $studioLink.TargetPath) + '","arguments":"' + (Write-EvidenceLaneShortcutValue $studioLink.Arguments) + '","working_directory":"' + (Write-EvidenceLaneShortcutValue $studioLink.WorkingDirectory) + '","window_style":' + ([string]$studioLink.WindowStyle) + ',"icon_location":"' + (Write-EvidenceLaneShortcutValue $studioLink.IconLocation) + '"}'
[Console]::Out.WriteLine($studioReadback)
"""

_REQUEST_ENVIRONMENT = {
    "path": "EVIDENCE_LANE_SHORTCUT_PATH_B64",
    "mode": "EVIDENCE_LANE_SHORTCUT_MODE_B64",
    "target": "EVIDENCE_LANE_SHORTCUT_TARGET_B64",
    "arguments": "EVIDENCE_LANE_SHORTCUT_ARGUMENTS_B64",
    "working_directory": "EVIDENCE_LANE_SHORTCUT_WORKING_DIRECTORY_B64",
    "window_style": "EVIDENCE_LANE_SHORTCUT_WINDOW_STYLE_B64",
    "icon_location": "EVIDENCE_LANE_SHORTCUT_ICON_LOCATION_B64",
}


def shell_link(path: Path, *, specification: dict | None = None) -> dict:
    if os.name != "nt":
        raise LaneError("WINDOWS_SHORTCUT_UNAVAILABLE", "This launcher requires Windows.")
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    executable = root / "System32/WindowsPowerShell/v1.0/powershell.exe"
    reject_links(executable, Path(executable.anchor))
    request = {"path": str(path), "mode": "write" if specification else "read"} | (specification or {})
    encoded_request = {
        _REQUEST_ENVIRONMENT[key]: base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        for key, value in request.items()
    }
    if sum(len(value) for value in encoded_request.values()) > 24_000:
        raise LaneError("SHORTCUT_OPERATION_FAILED", "The shortcut request exceeded its supported size.",
                        details={"reason": "oversized_request"})

    def safe_phase(output) -> dict:
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        allowed = {"started", "request_loaded", "shell_created", "link_loaded", "link_saved", "readback_ready"}
        phases = [line.removeprefix("EL_SHORTCUT_PHASE=") for line in (output or "").splitlines()
                  if line.startswith("EL_SHORTCUT_PHASE=") and line.removeprefix("EL_SHORTCUT_PHASE=") in allowed]
        return {"phase": phases[-1]} if phases else {}

    phase = {}
    try:
        command = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
        # Keep the Windows PowerShell child independent from its PowerShell 7
        # parent and pass only fixed Base64 fields; the helper never loads the
        # JSON cmdlets or interpolates request values into its command text.
        environment = {key: value for key, value in os.environ.items()
                       if key.casefold() != "psmodulepath"
                       and not key.casefold().startswith("evidence_lane_shortcut_")}
        environment.update(encoded_request)
        completed = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", command],
            stdin=subprocess.DEVNULL, env=environment,
            capture_output=True, text=True, encoding="utf-8",
            timeout=30, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
        phase = safe_phase(completed.stderr)
        if completed.returncode or len(completed.stdout) > 24_000:
            raise LaneError("SHORTCUT_OPERATION_FAILED", "Windows did not confirm the requested shortcut operation.",
                            details={"reason": "process_exit" if completed.returncode else "oversized_readback",
                                     "exit_code": completed.returncode, **phase})
        encoded = json.loads(completed.stdout.strip())
        if not isinstance(encoded, dict) or set(encoded) != {
            "target", "arguments", "working_directory", "window_style", "icon_location"
        }:
            raise ValueError()
        if not isinstance(encoded["window_style"], int):
            raise TypeError()
        value: dict[str, str | int] = {"window_style": encoded["window_style"]}
        for key in ("target", "arguments", "working_directory", "icon_location"):
            if not isinstance(encoded[key], str):
                raise TypeError()
            decoded = base64.b64decode(encoded[key], validate=True)
            if len(decoded) > 16_384:
                raise ValueError()
            value[key] = decoded.decode("utf-8")
        return value
    except subprocess.TimeoutExpired as error:
        raise LaneError("SHORTCUT_OPERATION_FAILED", "Windows did not confirm the shortcut operation within 30 seconds.",
                        details={"reason": "deadline_exceeded", "timeout_seconds": 30, **safe_phase(error.stderr)}) from None
    except (OSError, TypeError, ValueError) as error:
        raise LaneError("SHORTCUT_OPERATION_FAILED", "Windows did not confirm the requested shortcut operation.",
                        details={"reason": "process_unavailable" if isinstance(error, OSError) else "invalid_readback", **phase}) from None


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
        return {
            "target": str(target),
            "working_directory": str(self.root),
            "window_style": 1,
            "arguments": subprocess.list2cmdline(arguments),
            "icon_location": f"{target},0",
        }

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
            recorded = receipt.get("specification")
            actual = self.backend(self.path)
            if not isinstance(recorded, dict) or not isinstance(actual, dict):
                raise TypeError()
            # v4.0.3 receipts predate explicit icon metadata. Accept that exact
            # owned four-field receipt once so install() can replace it with the
            # five-field current specification.
            if any(actual.get(key) != value for key, value in recorded.items()):
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
                    previous_link = self.path.read_bytes()
                    previous_receipt = self.receipt.read_bytes()
                    temporary = self.destination / (".pending-studio-" + str(uuid4()) + ".lnk")
                    try:
                        if self.backend(temporary, specification=specification) != specification:
                            raise LaneError("SHORTCUT_WRITE_UNVERIFIED", "Windows returned different shortcut settings.")
                        os.replace(temporary, self.path)
                        atomic_json(
                            self.receipt,
                            {
                                "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                                "specification": specification,
                            },
                        )
                    except Exception:
                        self.path.write_bytes(previous_link)
                        self.receipt.write_bytes(previous_receipt)
                        raise
                    finally:
                        temporary.unlink(missing_ok=True)
                    return {
                        "registered": True,
                        "path": str(self.path),
                        "changed": True,
                        "updated_owned_shortcut": True,
                    }
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
                self.path.unlink()
                self.receipt.unlink()
        return {"registered": False, "path": str(self.path)}
