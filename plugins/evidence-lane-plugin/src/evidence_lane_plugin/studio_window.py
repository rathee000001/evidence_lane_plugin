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
from urllib.parse import urlsplit

from .errors import LaneError
from .storage import reject_links


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
    def __init__(self, runtime_root: Path, *, browser: Path | None = None, spawn=None, fallback=None, system=None):
        self.root = runtime_root.absolute()
        self.browser = browser if browser is not None else installed_browser()
        self.spawn = spawn or subprocess.Popen
        self.fallback = fallback or webbrowser.open
        self.system = system or platform.system
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
                arguments = [str(self.browser), "--new-window", "--app=" + url,
                             "--user-data-dir=" + str(profile), "--no-first-run", "--no-default-browser-check"]
                # This flag suppresses an inherited console, not the GUI window.
                # No debugging endpoint, shell, URL log or PID ownership claim.
                process = self.spawn(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, close_fds=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                code = process.poll()
                opened = code is None or code == 0
                mode = "dedicated_browser_window"
            else:
                opened = bool(self.fallback(url, new=1, autoraise=True))
                mode = "system_browser_window"
        except OSError:
            opened, mode = False, "browser_launch_failed"
        self.last_launch = self.last_launch | {"state": "requested" if opened else "unavailable",
                                              "mode": mode, "visible_window_verified": False}
        return opened
