"""Normal per-user Windows application registration for Evidence Lane Studio."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from .errors import LaneError
from .storage import reject_links

APP_ID = "EvidenceLane.Studio"
DISPLAY_NAME = "Evidence Lane Studio"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"
APP_PATH_KEY = r"Software\Microsoft\Windows\CurrentVersion\App Paths\EvidenceLaneStudio.exe"
STUDIO_KEY = r"Software\Evidence Lane\Studio"


class WindowsApplicationBackend:
    def __init__(self, *, local_app_data: Path | None = None):
        self.local_app_data = (
            local_app_data
            or Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        ).absolute()

    @property
    def application_root(self) -> Path:
        return self.local_app_data / "Programs/Evidence Lane Studio"

    def install(self, setup: Path, runtime_root: Path) -> None:
        already_registered = self._values(UNINSTALL_KEY) is not None
        owned_receipts: list[Path] = []
        start_menu = (
            Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
            / "Microsoft/Windows/Start Menu/Programs"
        )
        for folder in (start_menu, self._desktop_root()):
            link = folder / "Evidence Lane Studio.lnk"
            receipt = folder / ".evidence-lane-studio-shortcut.json"
            if receipt.is_file():
                try:
                    value = json.loads(receipt.read_text(encoding="utf-8"))
                    expected = value.get("sha256")
                except (OSError, ValueError, TypeError) as error:
                    raise LaneError(
                        "SHORTCUT_CHANGED",
                        "The previous Evidence Lane Studio shortcut receipt is invalid.",
                    ) from error
                actual = (
                    hashlib.sha256(link.read_bytes()).hexdigest()
                    if link.is_file()
                    else None
                )
                if not already_registered and expected != actual:
                    raise LaneError(
                        "SHORTCUT_CHANGED",
                        "The previous Evidence Lane Studio shortcut no longer matches its ownership receipt.",
                    )
                owned_receipts.append(receipt)
            elif link.exists() and not already_registered:
                raise LaneError(
                    "SHORTCUT_CHANGED",
                    "The Evidence Lane Studio shortcut path belongs to an unrecognized application.",
                )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(
                [
                    str(setup),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/CURRENTUSER",
                    f"/RUNTIMEROOT={runtime_root}",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
                creationflags=flags,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LaneError(
                "STUDIO_WINDOWS_INSTALLER_FAILED",
                "Windows could not complete the Evidence Lane Studio application registration.",
            ) from error
        if completed.returncode:
            raise LaneError(
                "STUDIO_WINDOWS_INSTALLER_FAILED",
                "Windows did not accept the Evidence Lane Studio application registration.",
                details={"exit_code": completed.returncode},
            )
        for receipt in owned_receipts:
            receipt.unlink(missing_ok=True)
        self._mark_shortcuts_current()

    def _mark_shortcuts_current(self) -> None:
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        set_file_time = kernel32.SetFileTime
        set_file_time.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
            ctypes.POINTER(FileTime),
        ]
        system_time = kernel32.GetSystemTimeAsFileTime
        close_handle = kernel32.CloseHandle
        now = FileTime()
        system_time(ctypes.byref(now))
        start_menu = (
            Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
            / "Microsoft/Windows/Start Menu/Programs/Evidence Lane Studio.lnk"
        )
        for path in (start_menu, self._desktop_root() / "Evidence Lane Studio.lnk"):
            handle = create_file(str(path), 0x100, 7, None, 3, 0, None)
            if handle == wintypes.HANDLE(-1).value:
                raise LaneError(
                    "STUDIO_WINDOWS_SHORTCUT_TIMESTAMP_FAILED",
                    "Windows did not refresh the Evidence Lane Studio shortcut identity.",
                )
            try:
                if not set_file_time(
                    handle, ctypes.byref(now), ctypes.byref(now), ctypes.byref(now)
                ):
                    raise LaneError(
                        "STUDIO_WINDOWS_SHORTCUT_TIMESTAMP_FAILED",
                        "Windows did not refresh the Evidence Lane Studio shortcut identity.",
                    )
            finally:
                close_handle(handle)

    def _values(self, key_path: str) -> dict[str, Any] | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                result: dict[str, Any] = {}
                index = 0
                while True:
                    try:
                        name, value, _kind = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    result[name or "(default)"] = value
                    index += 1
                return result
        except FileNotFoundError:
            return None

    def _desktop_root(self) -> Path:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, "Desktop")
        return Path(os.path.expandvars(str(value))).absolute()

    def status(self, runtime_root: Path, version: str) -> dict[str, Any]:
        app_root = self.application_root
        executable = app_root / "EvidenceLaneStudio.exe"
        icon = app_root / "EvidenceLaneStudio.ico"
        start_menu = (
            Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
            / "Microsoft/Windows/Start Menu/Programs/Evidence Lane Studio.lnk"
        )
        desktop = self._desktop_root() / "Evidence Lane Studio.lnk"
        uninstall = self._values(UNINSTALL_KEY)
        app_path = self._values(APP_PATH_KEY)
        studio = self._values(STUDIO_KEY)
        return {
            "registered": bool(uninstall and app_path and studio),
            "app_id": APP_ID,
            "display_name": None if uninstall is None else uninstall.get("DisplayName"),
            "display_version": None if uninstall is None else uninstall.get("DisplayVersion"),
            "publisher": None if uninstall is None else uninstall.get("Publisher"),
            "install_location": None if uninstall is None else uninstall.get("InstallLocation"),
            "display_icon": None if uninstall is None else uninstall.get("DisplayIcon"),
            "uninstall_command": None if uninstall is None else uninstall.get("UninstallString"),
            "app_path": None if app_path is None else app_path.get("(default)"),
            "runtime_root": None if studio is None else studio.get("RuntimeRoot"),
            "expected_runtime_root": str(runtime_root),
            "expected_version": version,
            "application_root": str(app_root),
            "executable": str(executable),
            "icon": str(icon),
            "start_menu_shortcut": str(start_menu),
            "desktop_shortcut": str(desktop),
            "executable_present": executable.is_file(),
            "icon_present": icon.is_file(),
            "start_menu_present": start_menu.is_file(),
            "desktop_present": desktop.is_file(),
            "project_state_changed": False,
        }


class WindowsStudioApplication:
    def __init__(
        self,
        runtime_root: Path,
        setup: Path,
        version: str,
        *,
        backend=None,
        system=None,
    ):
        self.root = runtime_root.absolute()
        self.setup = setup.absolute()
        self.version = version
        self.backend = backend or WindowsApplicationBackend()
        self.system = system or platform.system

    def _validate(self, status: dict[str, Any], *, allow_missing: bool) -> dict[str, Any]:
        if not status.get("registered"):
            if allow_missing:
                return status
            raise LaneError(
                "STUDIO_WINDOWS_REGISTRATION_MISSING",
                "Evidence Lane Studio is not registered as a Windows application.",
            )
        expected_root = os.path.normcase(str(self.root))
        application_root = Path(str(status.get("application_root") or ""))
        expected_app = application_root / "EvidenceLaneStudio.exe"
        expected_icon = application_root / "EvidenceLaneStudio.ico"
        exact = (
            status.get("display_name") == DISPLAY_NAME
            and status.get("display_version") == self.version
            and status.get("publisher") == "Evidence Lane"
            and os.path.normcase(str(status.get("runtime_root") or "")) == expected_root
            and os.path.normcase(str(status.get("app_path") or ""))
            == os.path.normcase(str(expected_app))
            and os.path.normcase(str(status.get("display_icon") or "").removesuffix(",0"))
            == os.path.normcase(str(expected_icon))
            and bool(status.get("uninstall_command"))
            and status.get("executable_present") is True
            and status.get("icon_present") is True
            and status.get("start_menu_present") is True
            and status.get("desktop_present") is True
        )
        if not exact:
            raise LaneError(
                "STUDIO_WINDOWS_REGISTRATION_INVALID",
                "Windows returned incomplete Evidence Lane Studio application registration.",
            )
        return {**status, "registration_verified": True}

    def status(self, *, allow_missing: bool = False) -> dict[str, Any]:
        return self._validate(
            self.backend.status(self.root, self.version), allow_missing=allow_missing
        )

    def install(self) -> dict[str, Any]:
        if self.system() != "Windows":
            raise LaneError(
                "STUDIO_PLATFORM_UNSUPPORTED",
                "Evidence Lane Studio is available on Windows PCs only.",
            )
        reject_links(self.root, Path(self.root.anchor))
        reject_links(self.setup, Path(self.setup.anchor))
        if not self.root.is_dir() or not self.setup.is_file():
            raise LaneError(
                "STUDIO_WINDOWS_INSTALLER_MISSING",
                "The release-bound Windows Studio installer is unavailable.",
            )
        self.backend.install(self.setup, self.root)
        return self.status()
