"""Isolated development preview. No login registration or plugin installation."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE / "plugins/evidence-lane-plugin/src"))

from evidence_lane_plugin.optional_runtimes import OptionalRuntime
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.service import Service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.absolute()
    if not root.is_relative_to(WORKSPACE / ".work"):
        parser.error("Development preview state must be inside the ignored workspace .work folder")
    root.mkdir(parents=True, exist_ok=True)
    runtimes = tuple(OptionalRuntime.from_installation(path.parent, WORKSPACE / "contracts/optional-runtimes")
                     for path in (WORKSPACE / ".work/optional-envs").glob("*/environment.json"))

    def launch(url):
        (root / "launch-url.txt").write_text(url, encoding="utf-8")
        return True

    service = Service(root / "runtime", capabilities=CapabilityMonitor(optional_runtimes=runtimes), studio_launcher=launch)
    signal.signal(signal.SIGINT, lambda *_: service.stop_requested.set())
    signal.signal(signal.SIGTERM, lambda *_: service.stop_requested.set())
    try:
        service.start()
        print("PREVIEW_READY", flush=True)
        service.wait()
    finally:
        service.close()
        (root / "launch-url.txt").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
