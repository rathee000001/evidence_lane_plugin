"""Explicit per-user Windows login registration with ownership-preserving updates."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import LaneError
from .locking import RuntimeLock

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "EvidenceLaneV4"


class WindowsRunValue:
    def read(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, VALUE_NAME)
                if kind != winreg.REG_SZ:
                    raise LaneError("STARTUP_ENTRY_INVALID", "The existing login entry has an unexpected type.")
                return value
        except FileNotFoundError:
            return None

    def write(self, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, value)

    def remove(self) -> None:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)


class LoginStartup:
    def __init__(
        self,
        interpreter: Path,
        runtime_root: Path,
        *,
        plugin_root: Path | None = None,
        launcher_executable: Path | None = None,
        backend=None,
    ):
        self.interpreter = interpreter.absolute()
        self.root = runtime_root.absolute()
        self.plugin_root = plugin_root.absolute() if plugin_root is not None else None
        self.launcher_executable = (
            launcher_executable.absolute() if launcher_executable is not None else None
        )
        self.backend = backend or WindowsRunValue()

    def command(self) -> str:
        if os.name != "nt" or self.interpreter.name.lower() != "pythonw.exe" or not self.interpreter.is_file():
            raise LaneError("STARTUP_INTERPRETER_INVALID", "Select the installed Windows windowless Python interpreter.")
        if self.launcher_executable is not None:
            if (
                self.launcher_executable.name != "EvidenceLaneStudio.exe"
                or not self.launcher_executable.is_file()
            ):
                raise LaneError(
                    "STARTUP_LAUNCHER_INVALID",
                    "The release-bound Windows Studio launcher is missing.",
                )
            arguments = [str(self.launcher_executable), "--startup"]
        elif self.plugin_root is None:
            arguments = [
                str(self.interpreter),
                "-I",
                "-m",
                "evidence_lane_plugin.service",
                "--runtime-root",
                str(self.root),
            ]
        else:
            script = self.plugin_root / "scripts/run_engine.py"
            if not script.is_file():
                raise LaneError(
                    "STARTUP_PLUGIN_INVALID",
                    "The immutable installed engine launcher is missing.",
                )
            arguments = [
                str(self.interpreter),
                "-B",
                str(script),
                "--runtime-root",
                str(self.root),
            ]
        command = subprocess.list2cmdline(arguments)
        if len(command) > 260 or any(character in command for character in "\r\n\0"):
            raise LaneError("STARTUP_COMMAND_TOO_LONG", "Use a shorter installed runtime path for Windows login startup.")
        return command

    def status(self) -> dict:
        actual = self.backend.read()
        expected = self.command()
        return {"registered": actual == expected, "entry_present": actual is not None,
                "matches_selected_installation": actual == expected, "scope": "current_windows_user",
                "studio_on_start": "open", "engine_console": "hidden"}

    def install(self, *, expected_previous: str | None = None) -> dict:
        command = self.command()
        with RuntimeLock(self.root / "startup.lock"):
            actual = self.backend.read()
            if actual not in {None, command} and actual != expected_previous:
                raise LaneError("STARTUP_ENTRY_CHANGED", "Inspect the existing login entry before replacing it.")
            self.backend.write(command)
            if self.backend.read() != command:
                raise LaneError("STARTUP_WRITE_UNVERIFIED", "Windows did not return the requested login registration.")
        return self.status()

    def uninstall(self) -> dict:
        command = self.command()
        with RuntimeLock(self.root / "startup.lock"):
            actual = self.backend.read()
            if actual is not None and actual != command:
                raise LaneError("STARTUP_ENTRY_CHANGED", "The login entry belongs to a different selected installation.")
            if actual is not None:
                self.backend.remove()
            if self.backend.read() is not None:
                raise LaneError("STARTUP_REMOVAL_UNVERIFIED", "The login entry still exists.")
        return {"registered": False, "scope": "current_windows_user"}
