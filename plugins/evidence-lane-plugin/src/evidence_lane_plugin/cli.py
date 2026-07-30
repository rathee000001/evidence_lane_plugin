"""Command-line entrypoint for deterministic validation and MCP serving."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .mcp_server import run_server
from .pv_package import validate_pv_package
from .service import EvidenceLaneService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evidence-lane-plugin")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "activate-installation",
        help="record the installed engine version outside governed PV state",
    )
    subcommands.add_parser("doctor", help="validate local runtime prerequisites")
    serve = subcommands.add_parser("serve", help="run the MCP server")
    serve.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument(
        "--port",
        type=int,
        default=int(
            os.environ.get("EVIDENCE_LANE_MCP_PORT") or os.environ.get("PORT") or "8765"
        ),
    )
    validate = subcommands.add_parser("validate-pv", help="validate one PV directory")
    validate.add_argument("directory")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "activate-installation":
        service = EvidenceLaneService()
        result = service.sessions.ensure_installation()
        service.flash_authority.ensure_flashed()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "doctor":
        result = EvidenceLaneService().doctor()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    if args.command == "validate-pv":
        result = validate_pv_package(Path(args.directory))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    if args.command == "serve":
        run_server(transport=args.transport, host=args.host, port=args.port)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
