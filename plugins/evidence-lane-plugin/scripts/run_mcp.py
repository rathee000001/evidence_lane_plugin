"""Release-aware launcher: provision once, then connect through the installed runtime."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def _first_detection(argv: list[str]) -> dict:
    source = PLUGIN / "scripts/first_detection.py"
    spec = importlib.util.spec_from_file_location("evidence_lane_first_detection", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("FIRST_DETECTION_LAUNCHER_MISSING")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        result = module.prepare_mcp(PLUGIN, argv)
    except module.FirstDetectionError as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": {"code": error.code, "message": str(error)},
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
    if result["reexec"]:
        os.execve(result["command"][0], result["command"], result["environment"])
    return result


def main(argv: list[str] | None = None) -> None:
    selected_argv = list(sys.argv[1:] if argv is None else argv)
    detection = _first_detection(selected_argv)
    active_plugin = Path(
        detection.get("plugin_root", PLUGIN)
    )
    sys.path.insert(0, str(active_plugin / "src"))
    from evidence_lane_plugin.host_routing import HOST_MATRIX
    from evidence_lane_plugin.launcher import ensure_local_engine, runtime_root
    from evidence_lane_plugin.mcp_adapter import add_selection_arguments, configured_selections
    from evidence_lane_plugin.mcp_server import run_server
    parser = argparse.ArgumentParser(description="Evidence Lane native engine connection")
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--runtime-root", type=Path)
    location.add_argument("--remote-config", type=Path)
    parser.add_argument("--transport", choices=["stdio"], default="stdio")
    parser.add_argument("--host-profile", choices=sorted(HOST_MATRIX), default=os.environ.get('EVIDENCE_LANE_HOST_PROFILE', 'unknown'))
    parser.add_argument('--local-project-administration', action='store_true',
        help='Grant project administration only when this packaged launcher selects the local owner route.')
    add_selection_arguments(parser)
    args = parser.parse_args(selected_argv)
    remote = os.environ.get('EVIDENCE_LANE_REMOTE_CONFIG')
    if remote and args.remote_config is None:
        if args.runtime_root is not None or os.environ.get('EVIDENCE_LANE_RUNTIME_ROOT'):
            parser.error('Select one explicit local runtime or remote configuration for both MCP and hooks.')
        args.remote_config = Path(remote)
    if args.remote_config is None and args.local_project_administration:
        args.manage_projects = True
    selections = configured_selections(args, parser)
    if args.remote_config is None:
        if args.host_profile in {'codex_vm_persistent', 'codex_vm_ephemeral'}:
            parser.error('Configured VM profiles require an explicitly verified remote engine before local startup.')
        args.runtime_root = runtime_root(args.runtime_root)
        ensure_local_engine(args.runtime_root)
    elif args.manage_projects:
        parser.error('Remote connections cannot request local project administration.')
    run_server(runtime_root=args.runtime_root, remote_config=args.remote_config,
               host_profile=args.host_profile, transport=args.transport, project_selections=selections,
               manage_projects=args.manage_projects)

if __name__ == "__main__":
    main()
