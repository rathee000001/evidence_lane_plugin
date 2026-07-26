"""Explicit, allowlisted installation into the plugin-local virtual environment."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    repository_root = plugin_root.parents[1]
    environment = plugin_root / ".venv"
    lock = plugin_root / "requirements.lock.txt"
    if not lock.is_file():
        raise SystemExit(f"Missing pinned dependency lock: {lock}")
    if not environment.exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(environment)
    python = (
        environment / "Scripts" / "python.exe"
        if os.name == "nt"
        else environment / "bin" / "python"
    )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--require-hashes",
            "-r",
            str(lock),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "-e",
            str(repository_root),
        ],
        check=True,
    )
    subprocess.run(
        [str(python), "-m", "evidence_lane_plugin.cli", "doctor"],
        check=True,
    )
    print(f"Evidence Lane Plugin ready: {environment}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
