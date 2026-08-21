"""Launch the plugin MCP from exact source plus a durable derived runtime."""

from __future__ import annotations

import argparse
import json
import os

# Fixed derived-runtime launcher only; no shell command is constructed.
import subprocess  # nosec B404
import sys
import tomllib
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))
from runtime_contract import (
    marker_is_valid,
    runtime_environment,
    runtime_identity,
    runtime_marker,
    runtime_projection_root,
)


def _venv_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--prewarm-only", action="store_true")
    return parser


def _expected_runtime_version(plugin_root: Path) -> str:
    project = tomllib.loads(
        (plugin_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    return str(project["project"]["version"])


def _expected_dependency_version(plugin_root: Path, package: str) -> str:
    project = tomllib.loads(
        (plugin_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    prefix = package.casefold() + "=="
    for dependency in project["project"]["dependencies"]:
        normalized = str(dependency).strip()
        if normalized.casefold().startswith(prefix):
            return normalized.split("==", 1)[1].split(";", 1)[0].strip()
    raise SystemExit(f"Missing exact {package} dependency pin in pyproject.toml.")


def _runtime_ready(
    python: Path,
    plugin_root: Path,
    marker: Path,
    expected_version: str,
    expected_pydantic_version: str,
) -> bool:
    """Reject interrupted, partial, or stale durable environments."""

    if not python.is_file() or not marker_is_valid(plugin_root, marker):
        return False
    source = plugin_root / "src"
    try:
        completed = subprocess.run(  # nosec B603
            [
                str(python),
                "-c",
                (
                    "import sys; sys.path.insert(0, sys.argv[3]); import mcp; "
                    "import pydantic; "
                    "from evidence_lane_plugin.constants import ENGINE_VERSION; "
                    "raise SystemExit(0 if (ENGINE_VERSION == sys.argv[1] and "
                    "pydantic.__version__ == sys.argv[2]) else 41)"
                ),
                expected_version,
                expected_pydantic_version,
                str(source),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _bootstrap_runtime(
    plugin_root: Path,
    environment: Path,
    marker: Path,
) -> None:
    bootstrap = plugin_root / "scripts" / "bootstrap.py"
    if not bootstrap.is_file():
        raise SystemExit(f"Missing plugin bootstrap: {bootstrap}")
    completed = subprocess.run(  # nosec B603
        [
            sys.executable,
            str(bootstrap),
            "--environment",
            str(environment),
            "--identity-file",
            str(marker),
        ],
        check=False,
        # MCP stdio reserves stdout exclusively for JSON-RPC. Bootstrap and
        # package-manager progress remain visible on the diagnostic stream.
        stdout=sys.stderr,
        stderr=sys.stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
    environment = runtime_environment(plugin_root)
    marker = runtime_marker(plugin_root)
    python = _venv_python(environment)
    if not _runtime_ready(
        python,
        plugin_root,
        marker,
        expected_version,
        expected_pydantic_version,
    ):
        _bootstrap_runtime(plugin_root, environment, marker)
    if not _runtime_ready(
        python,
        plugin_root,
        marker,
        expected_version,
        expected_pydantic_version,
    ):
        raise SystemExit(
            "Evidence Lane dependencies are unavailable after the governed bootstrap."
        )
    if args.prewarm_only:
        payload = {
            "schema": "evidence-lane.codex-native-runtime-prewarm.v1",
            "status": "PASS",
            "runtime_identity": runtime_identity(plugin_root),
            "runtime_projection_root": str(runtime_projection_root(plugin_root)),
            "runtime_environment": str(environment),
            "runtime_python": str(python),
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
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
            # The stdio relay must remain in the MCP client's process group so
            # client termination reaches the whole relay. Its caller owns the
            # hidden console contract. Non-stdio relays have no such transport
            # coupling and receive the Windows no-console flag directly.
            creationflags=(
                0
                if args.transport == "stdio"
                else getattr(subprocess, "CREATE_NO_WINDOW", 0)
            ),
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
