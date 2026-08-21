"""Launch the plugin-local MCP runtime from a self-contained Git snapshot."""

from __future__ import annotations

import argparse
import os

# Fixed local venv launcher only; no shell command is constructed.
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path


def _venv_python(plugin_root: Path) -> Path:
    if os.name == "nt":
        return plugin_root / ".venv" / "Scripts" / "python.exe"
    return plugin_root / ".venv" / "bin" / "python"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def _expected_runtime_version(plugin_root: Path) -> str:
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(project["project"]["version"])


def _expected_dependency_version(plugin_root: Path, package: str) -> str:
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text(encoding="utf-8"))
    prefix = package.casefold() + "=="
    for dependency in project["project"]["dependencies"]:
        normalized = str(dependency).strip()
        if normalized.casefold().startswith(prefix):
            return normalized.split("==", 1)[1].split(";", 1)[0].strip()
    raise SystemExit(f"Missing exact {package} dependency pin in pyproject.toml.")


def _runtime_ready(
    python: Path,
    expected_version: str,
    expected_pydantic_version: str,
) -> bool:
    """Reject interrupted, partial, or stale plugin-local environments."""

    if not python.is_file():
        return False
    try:
        completed = subprocess.run(  # nosec B603
            [
                str(python),
                "-c",
                (
                    "import sys, mcp; "
                    "import pydantic; "
                    "from evidence_lane_plugin.constants import ENGINE_VERSION; "
                    "raise SystemExit(0 if (ENGINE_VERSION == sys.argv[1] and "
                    "pydantic.__version__ == sys.argv[2]) else 41)"
                ),
                expected_version,
                expected_pydantic_version,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _bootstrap_runtime(plugin_root: Path) -> None:
    bootstrap = plugin_root / "scripts" / "bootstrap.py"
    if not bootstrap.is_file():
        raise SystemExit(f"Missing plugin bootstrap: {bootstrap}")
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(bootstrap)],
        check=False,
        # MCP stdio reserves stdout exclusively for JSON-RPC. Bootstrap and
        # package-manager progress remain visible on the diagnostic stream.
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"Evidence Lane bootstrap failed with exit code {completed.returncode}."
        )


def main() -> int:
    args = _parser().parse_args()
    plugin_root = Path(__file__).resolve().parents[1]
    expected_version = _expected_runtime_version(plugin_root)
    expected_pydantic_version = _expected_dependency_version(plugin_root, "pydantic")
    python = _venv_python(plugin_root)
    if not _runtime_ready(python, expected_version, expected_pydantic_version):
        _bootstrap_runtime(plugin_root)
    if not _runtime_ready(python, expected_version, expected_pydantic_version):
        raise SystemExit(
            "Evidence Lane dependencies are unavailable after the governed bootstrap."
        )
    if Path(sys.executable).resolve() != python.resolve():
        completed = subprocess.run(  # nosec B603
            [
                str(python),
                str(Path(__file__).resolve()),
                "--transport",
                args.transport,
                "--host",
                args.host,
                "--port",
                str(args.port),
            ],
            check=False,
        )
        return completed.returncode
    source = plugin_root / "src"
    sys.path.insert(0, str(source))
    try:
        from evidence_lane_plugin.mcp_server import run_server
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Evidence Lane dependencies are unavailable after the governed bootstrap."
        ) from exc
    run_server(transport=args.transport, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
