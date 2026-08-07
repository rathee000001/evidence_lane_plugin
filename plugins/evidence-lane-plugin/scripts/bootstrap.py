"""Explicit, allowlisted installation into the plugin-local virtual environment."""

from __future__ import annotations

import os

# Fixed local executable and argument lists only; no shell command is constructed.
import shutil
import subprocess  # nosec B404
import sys
import venv
from pathlib import Path


def _authoritative_runtime_environment(plugin_root: Path) -> dict[str, str]:
    """Make bootstrap diagnostics resolve the same source as the MCP runner.

    The plugin is deliberately installed non-editably into its private virtual
    environment, but a Codex marketplace cache carries release provenance in
    the copied ``src`` tree.  Prefixing that tree for activation and doctor
    keeps their engine identity aligned with ``run_mcp.py`` without weakening
    the self-contained wheel installation.
    """

    environment = os.environ.copy()
    source = str((plugin_root / "src").resolve())
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source, existing) if part
    )
    return environment


def _cleanup_generated_build_artifacts(plugin_root: Path) -> None:
    for target in (
        plugin_root / "build",
        plugin_root / "src" / "evidence_lane_plugin.egg-info",
    ):
        if not target.exists():
            continue
        resolved = target.resolve()
        try:
            resolved.relative_to(plugin_root)
        except ValueError as exc:
            raise SystemExit(
                f"Refusing to clean generated metadata outside plugin root: {target}"
            ) from exc
        if target.is_symlink():
            raise SystemExit(f"Refusing to clean generated metadata symlink: {target}")
        shutil.rmtree(target)


def main() -> int:
    plugin_root = Path(__file__).resolve().parents[1]
    environment = plugin_root / ".venv"
    lock = plugin_root / "requirements.lock.txt"
    project = plugin_root / "pyproject.toml"
    if not lock.is_file():
        raise SystemExit(f"Missing pinned dependency lock: {lock}")
    if not project.is_file():
        raise SystemExit(f"Missing self-contained plugin project: {project}")
    if not environment.exists():
        venv.EnvBuilder(with_pip=True, clear=False).create(environment)
    python = (
        environment / "Scripts" / "python.exe"
        if os.name == "nt"
        else environment / "bin" / "python"
    )
    subprocess.run(  # nosec B603
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
    _cleanup_generated_build_artifacts(plugin_root)
    try:
        subprocess.run(  # nosec B603
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--force-reinstall",
                "--no-build-isolation",
                "--no-deps",
                str(plugin_root),
            ],
            check=True,
        )
    finally:
        _cleanup_generated_build_artifacts(plugin_root)
    runtime_environment = _authoritative_runtime_environment(plugin_root)
    subprocess.run(  # nosec B603
        [
            str(python),
            "-m",
            "evidence_lane_plugin.cli",
            "activate-installation",
        ],
        check=True,
        env=runtime_environment,
    )
    subprocess.run(  # nosec B603
        [str(python), "-m", "evidence_lane_plugin.cli", "doctor"],
        check=True,
        env=runtime_environment,
    )
    print(f"Evidence Lane ready: {environment}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
