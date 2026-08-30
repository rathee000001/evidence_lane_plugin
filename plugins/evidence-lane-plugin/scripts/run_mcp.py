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
    ACCELERATOR_PROFILES,
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
    parser.add_argument(
        "--accelerator-profile",
        choices=ACCELERATOR_PROFILES,
        default=os.environ.get("EVIDENCE_LANE_ACCELERATOR_PROFILE", "cpu").strip().lower(),
    )
    parser.add_argument(
        "--accelerator-memory-budget-percent",
        type=int,
        default=int(os.environ.get("EVIDENCE_LANE_ACCELERATOR_MEMORY_BUDGET_PERCENT", "80")),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    startup_mode = parser.add_mutually_exclusive_group()
    startup_mode.add_argument("--bootstrap-only", action="store_true")
    startup_mode.add_argument("--prewarm-only", action="store_true")
    parser.add_argument("--runtime-control-root")
    parser.add_argument(
        "--host-profile",
        choices=("CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"),
        default=os.environ.get("EVIDENCE_LANE_HOST_PROFILE", "CODEX_DESKTOP"),
    )
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


def _activate_installed_runtime_authority(plugin_root: Path) -> dict[str, object]:
    """Activate the exact installed build even when its dependency runtime is reused."""

    source = plugin_root / "src"
    sys.path.insert(0, str(source))
    from evidence_lane_plugin.service import EvidenceLaneService

    service = EvidenceLaneService()
    installation = service.sessions.ensure_installation()
    flash = service.flash_authority.ensure_flashed()
    receipt = dict(flash.get("receipt") or {})
    migration = dict(flash.get("build_migration") or {})
    plugin_manifest = json.loads(
        (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    expected_plugin_version = str(plugin_manifest.get("version") or "")
    if (
        flash.get("status") != "PASS"
        or not expected_plugin_version
        or receipt.get("plugin_version") != expected_plugin_version
    ):
        raise SystemExit(
            "Installed Evidence Lane Flash authority did not activate the exact package build."
        )
    return {
        "schema": "evidence-lane.codex-installed-runtime-authority-prewarm.v1",
        "status": "PASS",
        "installation_version": installation.get("version"),
        "flash_plugin_version": receipt.get("plugin_version"),
        "flash_action": flash.get("flash_action"),
        "flash_receipt_sha256": flash.get("receipt_sha256"),
        "flash_build_migration_receipt_sha256": migration.get("receipt_sha256"),
        "flash_migration_scope": migration.get("migration_scope"),
        "project_state_mutated": migration.get("project_state_mutated", False),
        "candidate_mutated": migration.get("candidate_mutated", False),
        "pointer_moved": migration.get("pointer_moved", False),
    }


def main() -> int:
    args = _parser().parse_args()
    plugin_root = Path(__file__).resolve().parents[1]
    # The launcher owns this binding. Never inherit a stale source, cache, or
    # donor-task package root into the public MCP/SDK/skill/toolchain chain.
    os.environ["EVIDENCE_LANE_PLUGIN_ROOT"] = str(plugin_root)
    if args.runtime_control_root:
        os.environ["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(
            Path(args.runtime_control_root).resolve()
        )
    os.environ["EVIDENCE_LANE_HOST_PROFILE"] = args.host_profile
    if not 1 <= args.accelerator_memory_budget_percent <= 95:
        raise SystemExit("Accelerator memory budget must be between 1 and 95 percent.")
    os.environ["EVIDENCE_LANE_ACCELERATOR_PROFILE"] = args.accelerator_profile
    os.environ["EVIDENCE_LANE_ACCELERATOR_MEMORY_BUDGET_PERCENT"] = str(
        args.accelerator_memory_budget_percent
    )
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
    if args.bootstrap_only and Path(sys.executable).resolve() != python.resolve():
        completed = subprocess.run(  # nosec B603
            [
                str(python),
                str(Path(__file__).resolve()),
                "--bootstrap-only",
                "--host-profile",
                args.host_profile,
                "--accelerator-profile",
                args.accelerator_profile,
                "--accelerator-memory-budget-percent",
                str(args.accelerator_memory_budget_percent),
                *(
                    ["--runtime-control-root", args.runtime_control_root]
                    if args.runtime_control_root
                    else []
                ),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode
    if args.bootstrap_only:
        runtime_authority = _activate_installed_runtime_authority(plugin_root)
        payload = {
            "schema": "evidence-lane.codex-runtime-bootstrap.v1",
            "status": "PASS",
            "runtime_identity": runtime_identity(plugin_root),
            "runtime_projection_root": str(runtime_projection_root(plugin_root)),
            "runtime_environment": str(environment),
            "runtime_python": str(python),
            "toolchain_inspected": False,
            "native_toolchain_required": False,
            "runtime_authority": runtime_authority,
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    if args.prewarm_only and Path(sys.executable).resolve() != python.resolve():
        completed = subprocess.run(  # nosec B603
            [
                str(python),
                str(Path(__file__).resolve()),
                "--prewarm-only",
                "--host-profile",
                args.host_profile,
                "--accelerator-profile",
                args.accelerator_profile,
                "--accelerator-memory-budget-percent",
                str(args.accelerator_memory_budget_percent),
                *(
                    ["--runtime-control-root", args.runtime_control_root]
                    if args.runtime_control_root
                    else []
                ),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode
    if args.prewarm_only:
        source = plugin_root / "src"
        sys.path.insert(0, str(source))
        from evidence_lane_plugin.hardware_acceleration import (
            resolve_hardware_acceleration,
        )
        from evidence_lane_plugin.runtime_toolchain import inspect_runtime_toolchain

        toolchain = inspect_runtime_toolchain(plugin_root, prewarm_native=True)
        acceleration = resolve_hardware_acceleration(
            action_classes=["RETRIEVAL", "OCR_MEDIA", "EVALUATION"],
            requested_profile=args.accelerator_profile,
            enabled_vendor_plugins=(
                [args.accelerator_profile]
                if args.accelerator_profile in {"nvidia", "amd"}
                else []
            ),
            memory_budget_percent=args.accelerator_memory_budget_percent,
        )
        payload = {
            "schema": "evidence-lane.codex-native-runtime-prewarm.v1",
            "status": "PASS" if toolchain["status"] == "PASS" else "FAIL",
            "runtime_identity": runtime_identity(plugin_root),
            "runtime_projection_root": str(runtime_projection_root(plugin_root)),
            "runtime_environment": str(environment),
            "runtime_python": str(python),
            "runtime_toolchain": toolchain,
            "hardware_acceleration": acceleration,
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
                "--host-profile",
                args.host_profile,
                "--accelerator-profile",
                args.accelerator_profile,
                "--accelerator-memory-budget-percent",
                str(args.accelerator_memory_budget_percent),
                *(
                    ["--runtime-control-root", args.runtime_control_root]
                    if args.runtime_control_root
                    else []
                ),
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
