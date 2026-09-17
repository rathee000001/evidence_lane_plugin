"""A visible browser window with native minimize/restore/close controls.

The browser owns its window; the engine owns no window handles or debugging
port. Closing or minimizing Studio therefore cannot stop the engine or jobs.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import LaneError
from .storage import reject_links

_BROWSER_NAMES = {"msedge.exe", "chrome.exe"}


def _normalized_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value).strip('"')))


def dedicated_browser_processes(profile: Path, *, process_iter=None) -> tuple[Any, ...]:
    """Return only browser processes whose argv names this exact private profile."""
    if os.name != "nt":
        return ()
    try:
        import psutil
    except ImportError as error:
        raise LaneError(
            "STUDIO_PROCESS_RUNTIME_UNAVAILABLE",
            "The installed Studio runtime cannot inspect its owned browser process.",
        ) from error
    iterator = process_iter or psutil.process_iter
    expected = _normalized_path(profile)
    matches = []
    for process in iterator(("name", "exe", "cmdline")):
        try:
            info = process.info
            name = str(info.get("name") or Path(str(info.get("exe") or "")).name).casefold()
            arguments = info.get("cmdline")
            if name not in _BROWSER_NAMES or not isinstance(arguments, list):
                continue
            profiles = [
                argument.split("=", 1)[1]
                for argument in arguments
                if isinstance(argument, str) and argument.casefold().startswith("--user-data-dir=")
            ]
            if len(profiles) == 1 and _normalized_path(profiles[0]) == expected:
                matches.append(process)
        except (psutil.Error, OSError, ValueError, TypeError):
            continue
    return tuple(matches)


def restore_dedicated_browser_window(processes: tuple[Any, ...]) -> bool:
    """Restore and foreground an exact owned browser window through Win32."""
    if os.name != "nt" or not processes:
        return False
    import ctypes
    from ctypes import wintypes

    pids = {int(process.pid) for process in processes if getattr(process, "pid", None)}
    if not pids:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found = False

    @callback_type
    def visit(window, _parameter):
        nonlocal found
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(window):
            user32.ShowWindow(window, 9)  # SW_RESTORE; a normal visible window is unchanged.
            user32.SetForegroundWindow(window)
            found = bool(user32.IsWindowVisible(window))
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found


def close_dedicated_browser_processes(
    profile: Path,
    *,
    timeout: float = 10,
    process_iter=None,
    waiter=None,
) -> dict[str, int]:
    """Close only browser processes bound to the exact Studio profile."""
    if os.name != "nt":
        raise LaneError("STUDIO_PLATFORM_UNSUPPORTED", "Studio is available on Windows PCs only.")
    import psutil

    processes = list(dedicated_browser_processes(profile, process_iter=process_iter))
    for process in processes:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
        except (psutil.Error, OSError) as error:
            raise LaneError(
                "STUDIO_WINDOW_CLOSE_FAILED",
                "The owned Studio browser window could not be closed safely.",
            ) from error
    wait = waiter or psutil.wait_procs
    _, alive = wait(processes, timeout=timeout)
    for process in alive:
        try:
            process.kill()
        except psutil.NoSuchProcess:
            pass
        except (psutil.Error, OSError) as error:
            raise LaneError(
                "STUDIO_WINDOW_CLOSE_FAILED",
                "The owned Studio browser process did not stop safely.",
            ) from error
    if alive:
        _, alive = wait(alive, timeout=timeout)
    if alive:
        raise LaneError(
            "STUDIO_WINDOW_CLOSE_FAILED",
            "The owned Studio browser process remained active after the close deadline.",
        )
    return {"matched": len(processes), "closed": len(processes)}


def installed_browser() -> Path | None:
    if os.name != "nt":
        return None
    candidates: list[Path] = []
    for key in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        root = os.environ.get(key)
        if root:
            candidates.extend(Path(root) / relative for relative in (
                "Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"))
    for path in candidates:
        if path.is_absolute() and path.is_file():
            reject_links(path, Path(path.anchor))
            return path
    return None


class StudioWindow:
    def __init__(self, runtime_root: Path, *, browser: Path | None = None, spawn=None, fallback=None,
                 system=None, existing=None, restore=None, authenticated_session=None, close_existing=None):
        self.root = runtime_root.absolute()
        self.browser = browser if browser is not None else installed_browser()
        self.spawn = spawn or subprocess.Popen
        self.fallback = fallback or webbrowser.open
        self.system = system or platform.system
        self.existing = existing or dedicated_browser_processes
        self.restore = restore or restore_dedicated_browser_window
        self.authenticated_session = authenticated_session or (lambda: False)
        self.close_existing = close_existing or close_dedicated_browser_processes
        self.last_launch = {"state": "not_requested", "window_controls": "browser_native",
                            "minimize_stops_engine": False, "close_stops_engine": False}

    def __call__(self, url: str) -> bool:
        if self.system() != 'Windows':
            raise LaneError('STUDIO_PLATFORM_UNSUPPORTED',
                            'Studio is a Windows PC application. Use the supported MCP/SDK host route on this platform.')
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise LaneError("STUDIO_URL_INVALID", "The launcher requires an engine-issued local Studio link.") from None
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not port
                or parsed.username or parsed.password or parsed.path != "/studio/" or parsed.query
                or not re.fullmatch(r"ticket=[A-Za-z0-9_-]{32,128}", parsed.fragment)):
            raise LaneError("STUDIO_URL_INVALID", "The launcher requires an engine-issued local Studio link.")
        try:
            if self.browser:
                reject_links(self.browser, Path(self.browser.anchor))
                if not self.browser.is_file() or self.browser.name.lower() not in {"msedge.exe", "chrome.exe"}:
                    raise LaneError("STUDIO_BROWSER_UNAVAILABLE", "Select an installed supported browser.")
                profile = self.root / "studio-browser"
                reject_links(profile, Path(profile.anchor))
                profile.mkdir(parents=True, exist_ok=True)
                existing = tuple(self.existing(profile))
                authenticated = bool(existing and self.authenticated_session())
                restored = bool(authenticated and self.restore(existing))
                reconnected = bool(existing and not restored)
                if reconnected:
                    self.close_existing(profile)
                    if tuple(self.existing(profile)):
                        raise LaneError(
                            "STUDIO_WINDOW_CLOSE_FAILED",
                            "The previous Studio window must close before reconnecting.",
                        )
                if restored:
                    opened = True
                    mode = "existing_dedicated_browser_window"
                else:
                    arguments = [str(self.browser), "--new-window", "--app=" + url,
                                 "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check"]
                    # This flag suppresses an inherited console, not the GUI window.
                    # No debugging endpoint or shell is enabled.
                    process = self.spawn(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL, close_fds=True,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    code = process.poll()
                    opened = code is None or code == 0
                    mode = "reconnected_dedicated_browser_window" if reconnected else "dedicated_browser_window"
            else:
                opened = bool(self.fallback(url, new=1, autoraise=True))
                mode, restored, existing = "system_browser_window", False, ()
        except OSError:
            opened, mode, restored, existing = False, "browser_launch_failed", False, ()
        self.last_launch = self.last_launch | {"state": "requested" if opened else "unavailable",
                                              "mode": mode, "visible_window_verified": restored,
                                              "existing_profile_process_count": len(existing)}
        return opened
