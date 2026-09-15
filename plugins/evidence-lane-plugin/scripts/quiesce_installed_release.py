"""Stop one exact installed engine and its private Studio browser before upgrade."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "src"))


def engine_lock_available(runtime_root: Path) -> bool:
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.locking import RuntimeLock

    lock = RuntimeLock(runtime_root / "engine.lock")
    try:
        lock.acquire()
    except LaneError as error:
        if error.code == "RUNTIME_IN_USE":
            return False
        raise
    else:
        lock.release()
        return True


def quiesce(runtime_root: Path, browser_profile: Path, *, timeout_seconds: float) -> dict:
    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.service import request_owner_control
    from evidence_lane_plugin.storage import reject_links
    from evidence_lane_plugin.studio_window import (
        close_dedicated_browser_processes,
        dedicated_browser_processes,
    )

    root = Path(os.path.abspath(runtime_root))
    profile = Path(os.path.abspath(browser_profile))
    if os.name != "nt":
        raise LaneError("STUDIO_PLATFORM_UNSUPPORTED", "Installed Studio upgrade requires Windows.")
    if profile != root / "studio-browser":
        raise LaneError(
            "STUDIO_PROFILE_MISMATCH",
            "The upgrade may close only the selected engine's private Studio profile.",
        )
    reject_links(root, Path(root.anchor))
    if profile.exists():
        reject_links(profile, root)
    browser = close_dedicated_browser_processes(profile, timeout=min(timeout_seconds, 15))
    endpoint = root / "endpoint.json"
    shutdown_requested = False
    if endpoint.exists():
        try:
            response = request_owner_control(root, "shutdown")
        except LaneError:
            if not engine_lock_available(root):
                raise
        else:
            if response.get("shutdown_requested") is not True:
                raise LaneError(
                    "ENGINE_SHUTDOWN_UNCONFIRMED",
                    "The installed engine did not confirm its shutdown request.",
                )
            shutdown_requested = True
    deadline = time.monotonic() + timeout_seconds
    while True:
        stopped = engine_lock_available(root)
        if stopped and not endpoint.exists():
            break
        if time.monotonic() >= deadline:
            raise LaneError(
                "ENGINE_SHUTDOWN_TIMEOUT",
                "The installed engine did not release its runtime before the upgrade deadline.",
            )
        time.sleep(0.1)
    if dedicated_browser_processes(profile):
        raise LaneError(
            "STUDIO_WINDOW_CLOSE_FAILED",
            "The exact Studio browser profile remained active after shutdown.",
        )
    return {
        "status": "PASS",
        "engine_shutdown_requested": shutdown_requested,
        "engine_stop_confirmed": True,
        "studio_profile": str(profile),
        "studio_processes_matched": browser["matched"],
        "studio_processes_closed": browser["closed"],
        "project_state_changed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--browser-profile", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60)
    args = parser.parse_args(argv)
    if not 1 <= args.timeout_seconds <= 120:
        print(json.dumps({"status": "FAIL", "error": "INVALID_TIMEOUT"}))
        return 2
    try:
        result = quiesce(
            args.runtime_root,
            args.browser_profile,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as error:  # noqa: BLE001 - emit only the public error code.
        code = getattr(error, "code", "INSTALLATION_QUIESCENCE_FAILED")
        print(json.dumps({"status": "FAIL", "error": code, "project_state_changed": False}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
