"""Native MCP entrypoint over the persistent engine's registered actions."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .connections import ProjectSelection
from .errors import LaneError
from .mcp_adapter import serve


def run_server(*, runtime_root: Path | None = None, remote_config: Path | None = None,
               host_profile: str = "unknown", transport: str = "stdio",
               project_selections: tuple[ProjectSelection, ...] = (), manage_projects: bool = False) -> None:
    if transport != "stdio" or (runtime_root is None) == (remote_config is None):
        raise LaneError("MCP_CONNECTION_REQUIRED", "Select one local runtime or remote configuration for native stdio MCP.")
    asyncio.run(serve(runtime_root, host_profile=host_profile, remote_config=remote_config,
                      project_selections=project_selections, manage_projects=manage_projects))
