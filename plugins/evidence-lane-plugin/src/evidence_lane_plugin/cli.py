"""Imported command entrypoint corrected for the persistent v4 workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .errors import LaneError
from .host_routing import HOST_MATRIX


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evidence-lane-plugin")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    engine = commands.add_parser("engine", help="Run the persistent local engine and visible Studio")
    engine.add_argument("--runtime-root", type=Path)
    studio = commands.add_parser('studio', help='Open Studio connected to the plugin-owned engine')
    studio.add_argument('--runtime-root', type=Path)
    serve = commands.add_parser("serve", help="Connect native stdio MCP to one existing engine")
    location = serve.add_mutually_exclusive_group()
    location.add_argument("--runtime-root", type=Path)
    location.add_argument("--remote-config", type=Path)
    serve.add_argument("--host-profile", choices=sorted(HOST_MATRIX), default="unknown")
    from .mcp_adapter import add_selection_arguments
    add_selection_arguments(serve)
    doctor = commands.add_parser("doctor", help="Read readiness from the selected running local engine")
    doctor.add_argument("--runtime-root", type=Path)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        from .launcher import ensure_local_engine, runtime_root
        if getattr(args, 'remote_config', None) is None:
            args.runtime_root = runtime_root(args.runtime_root)
        if args.command == "serve":
            from .mcp_adapter import configured_selections
            from .mcp_server import run_server
            selections = configured_selections(args, parser)
            if args.remote_config is None:
                ensure_local_engine(args.runtime_root)
            run_server(runtime_root=args.runtime_root, remote_config=args.remote_config,
                       host_profile=args.host_profile, project_selections=selections, manage_projects=args.manage_projects)
        elif args.command == "engine":
            from .service import main as service_main
            service_main(["--runtime-root", str(args.runtime_root)])
        elif args.command == 'studio':
            from .service import request_owner_control
            ensure_local_engine(args.runtime_root)
            print(json.dumps(request_owner_control(args.runtime_root, 'open_studio')))
        elif args.command == "doctor":
            from .local_transport import LocalTransport
            from .sdk import EvidenceLaneClient
            with LocalTransport(args.runtime_root) as transport:
                response = EvidenceLaneClient(transport).call("runtime_status")
                print(response.model_dump_json())
                return 0 if response.status == "ok" else 1
        return 0
    except LaneError as error:
        print(json.dumps({"status": "error", "error": error.public()}))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
