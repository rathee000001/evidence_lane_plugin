"""Launch the plugin-local MCP runtime from a self-contained Git snapshot."""

from __future__ import annotations

import argparse
import os

# Fixed local venv launcher only; no shell command is constructed.
import subprocess  # nosec B404
import sys
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


def _bootstrap_runtime(plugin_root: Path) -> None:
    bootstrap = plugin_root / "scripts" / "bootstrap.py"
    if not bootstrap.is_file():
        raise SystemExit(f"Missing plugin bootstrap: {bootstrap}")
    completed = subprocess.run(  # nosec B603
        [sys.executable, str(bootstrap)],
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(
            f"Evidence Lane bootstrap failed with exit code {completed.returncode}."
        )


def main() -> int:
    args = _parser().parse_args()
    plugin_root = Path(__file__).resolve().parents[1]
    python = _venv_python(plugin_root)
    if not python.is_file():
        _bootstrap_runtime(plugin_root)
    if python.is_file() and Path(sys.executable).resolve() != python.resolve():
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
