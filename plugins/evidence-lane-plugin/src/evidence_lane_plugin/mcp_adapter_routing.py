"""Scoped outer-SDK routes for admitted third-party MCP servers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

MCP_ADAPTER_ROUTING_SCHEMA = "evidence-lane.mcp-adapter-routing.v1"

_MCP_ADAPTERS: dict[str, dict[str, Any]] = {
    "GitHub_MCP_Server": {
        "server_identity": "github/github-mcp-server",
        "transport": "stdio_or_streamable_http",
        "credential_names": [
            "EVIDENCE_LANE_GITHUB_APP_ID",
            "EVIDENCE_LANE_GITHUB_APP_INSTALLATION_ID",
            "EVIDENCE_LANE_GITHUB_APP_PRIVATE_KEY_PATH",
        ],
        "capabilities": {
            "repository_read": "repos",
            "repository_write": "repos",
            "checks": "actions",
            "pull_requests": "pull_requests",
            "issues": "issues",
            "security": "code_security",
        },
    },
    "Filesystem_MCP_Server": {
        "server_identity": "modelcontextprotocol/server-filesystem",
        "transport": "stdio",
        "credential_names": [],
        "capabilities": {
            "bounded_read": "read_file",
            "bounded_write": "write_file",
            "search": "search_files",
            "metadata": "get_file_info",
        },
    },
    "PostgreSQL_MCP_Server": {
        "server_identity": "configured/postgresql-mcp",
        "transport": "stdio_or_streamable_http",
        "credential_names": ["EVIDENCE_LANE_POSTGRES_DSN"],
        "capabilities": {
            "schema_inspect": "schema",
            "bounded_query": "query_read_only",
            "migration_evidence": "migration_plan",
        },
    },
    "Slack_MCP_Server": {
        "server_identity": "configured/slack-mcp",
        "transport": "stdio_or_streamable_http",
        "credential_names": ["EVIDENCE_LANE_SLACK_TOKEN"],
        "capabilities": {
            "channel_read": "channels_read",
            "message_draft": "message_draft",
            "approved_send": "message_send",
        },
    },
}


def _bounded_roots(values: Iterable[str]) -> list[str]:
    roots: list[str] = []
    for value in values:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            raise ValueError("Filesystem MCP roots must be exact absolute paths.")
        resolved = candidate.resolve(strict=False)
        if resolved == Path(resolved.anchor):
            raise ValueError("Filesystem MCP cannot receive a drive or filesystem root.")
        roots.append(str(resolved))
    if len(roots) != len(set(roots)):
        raise ValueError("Filesystem MCP roots must be unique.")
    return roots


def build_mcp_adapter_route(
    *,
    tool_id: str,
    project_id: str,
    task_id: str,
    requested_capabilities: Iterable[str],
    granted_capabilities: Iterable[str],
    allowed_roots: Iterable[str] = (),
    external_write_approved: bool = False,
) -> dict[str, Any]:
    """Build one fail-closed MCP route without reading credential values."""

    if tool_id not in _MCP_ADAPTERS:
        raise ValueError(f"Unknown admitted MCP adapter: {tool_id}")
    exact_project = project_id.strip()
    exact_task = task_id.strip()
    if not exact_project or not exact_task:
        raise ValueError("MCP adapter routing requires project and task identity.")
    contract = _MCP_ADAPTERS[tool_id]
    requested = list(dict.fromkeys(str(value).strip() for value in requested_capabilities))
    granted = {str(value).strip() for value in granted_capabilities}
    unknown = sorted(set(requested) - set(contract["capabilities"]))
    if unknown:
        raise ValueError(f"MCP adapter capabilities are not declared: {unknown}")
    selected = [value for value in requested if value in granted]
    if set(requested) - granted:
        status = "BLOCKED_CAPABILITY_NOT_GRANTED"
    elif tool_id == "Slack_MCP_Server" and "approved_send" in selected and not external_write_approved:
        status = "BLOCKED_EXTERNAL_WRITE_APPROVAL_REQUIRED"
    else:
        status = "PASS"
    roots = _bounded_roots(allowed_roots)
    if tool_id == "Filesystem_MCP_Server" and not roots:
        status = "BLOCKED_ROOT_SCOPE_REQUIRED"
    if tool_id != "Filesystem_MCP_Server" and roots:
        raise ValueError("Only Filesystem MCP accepts filesystem root grants.")
    body = {
        "schema": MCP_ADAPTER_ROUTING_SCHEMA,
        "status": status,
        "tool_id": tool_id,
        "server_identity": contract["server_identity"],
        "transport": contract["transport"],
        "composition_framework_order": ["FastMCP", "MCP_Python_SDK"],
        "composition_primary": "FastMCP",
        "compatibility_transport_fallback": "MCP_Python_SDK",
        "domain_server_is_not_framework_fallback": True,
        "project_id": exact_project,
        "task_id": exact_task,
        "requested_capabilities": requested,
        "granted_capabilities": sorted(granted),
        "selected_server_tools_or_toolsets": [
            contract["capabilities"][value] for value in selected
        ],
        "allowed_roots": roots,
        "credential_names": list(contract["credential_names"]),
        "credential_values_read": False,
        "toolset_allowlist_required": True,
        "root_scope_required": tool_id == "Filesystem_MCP_Server",
        "external_write_approved": bool(external_write_approved),
        "independent_agent_authority": False,
        "project_authority": False,
        "scheduler_authority": False,
        "lifecycle_authority": False,
        "run_every_server_tool": False,
    }
    return {**body, "route_sha256": sha256_bytes(canonical_json_bytes(body))}


def mcp_adapter_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.mcp-adapter-catalog.v1",
        "status": "PASS",
        "adapter_count": len(_MCP_ADAPTERS),
        "adapters": [
            {
                "tool_id": tool_id,
                "server_identity": contract["server_identity"],
                "transport": contract["transport"],
                "credential_names": list(contract["credential_names"]),
                "capabilities": dict(contract["capabilities"]),
            }
            for tool_id, contract in _MCP_ADAPTERS.items()
        ],
        "all_routes_owned_by_outer_sdk": True,
        "all_routes_fail_closed": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "MCP_ADAPTER_ROUTING_SCHEMA",
    "build_mcp_adapter_route",
    "mcp_adapter_catalog",
]
