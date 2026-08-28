"""Package binding for the canonical Evidence Lane MCP server."""

from evidence_lane_plugin.mcp_server import create_mcp_server


def create_server():
    return create_mcp_server()

__all__ = ["create_server"]
