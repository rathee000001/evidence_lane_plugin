"""Multi-project, multi-task tunnel routing without scheduling authority."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .ai_toolchain import resolve_lane_toolchain
from .ecosystem_toolchain import resolve_ecosystem_adapters
from .hashing import canonical_json_bytes, sha256_bytes

TUNNEL_IDENTITY_ROUTING_SCHEMA = "evidence-lane.tunnel-identity-routing.v1"


def build_tunnel_identity_route(
    *,
    project_id: str,
    task_id: str,
    session_id: str,
    action_name: str,
    action_schema_sha256: str,
    lane_id: str,
    host_profile: str,
    capability: str | None,
    granted_tools: Iterable[str],
    available_tools: Iterable[str],
) -> dict[str, Any]:
    identities = {
        "project_id": project_id.strip(),
        "task_id": task_id.strip(),
        "session_id": session_id.strip(),
        "action_name": action_name.strip(),
        "action_schema_sha256": action_schema_sha256.strip().upper(),
    }
    if not all(identities.values()):
        raise ValueError("Tunnel routing requires exact project/task/session/action/schema identity.")
    available = set(available_tools)
    lane_route = resolve_lane_toolchain(
        lane_id=lane_id,
        host_profile=host_profile,
        available_tools=available,
    )
    adapter_route: dict[str, Any] | None = None
    if capability:
        for action_class in lane_route["action_classes"]:
            candidate = resolve_ecosystem_adapters(
                capability=capability,
                action_class=action_class,
                lane_id=lane_id,
                project_id=identities["project_id"],
                task_id=identities["task_id"],
                granted_tools=granted_tools,
                available_tools=available,
            )
            if candidate["candidate_tools"]:
                adapter_route = candidate
                break
    identity_core = {
        **identities,
        "lane_id": lane_id.strip().lower(),
        "host_profile": host_profile.strip().upper(),
    }
    body = {
        "schema": TUNNEL_IDENTITY_ROUTING_SCHEMA,
        "status": lane_route["status"],
        **identity_core,
        "correlation_identity_sha256": sha256_bytes(
            canonical_json_bytes(identity_core)
        ),
        "lane_toolchain": lane_route,
        "ecosystem_adapter_route": adapter_route,
        "mcp_composition_framework_order": ["FastMCP", "MCP_Python_SDK"],
        "mcp_composition_selection": (
            "FAST_MCP_PRIMARY_WHEN_TRANSPORT_COMPATIBILITY_PASSES_ELSE_NATIVE_SDK"
        ),
        "shared_process_allowed": True,
        "cross_task_state_allowed": False,
        "cross_project_state_allowed": False,
        "per_action_schema_binding_required": True,
        "prewarm_is_capability_availability_not_execution": True,
        "scheduled_task_owner": False,
        "plan_owner": False,
        "goal_owner": False,
        "project_registration_owner": False,
        "run_every_tool": False,
    }
    return {**body, "route_sha256": sha256_bytes(canonical_json_bytes(body))}


def tunnel_identity_routing_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.tunnel-identity-routing-catalog.v1",
        "status": "PASS",
        "identity_fields": [
            "project_id",
            "task_id",
            "session_id",
            "action_name",
            "action_schema_sha256",
            "lane_id",
            "host_profile",
        ],
        "unbounded_project_or_task_count_supported_by_identity_isolation": True,
        "count_is_fixed_ceiling": False,
        "scheduled_task_owner": False,
        "prewarm_executes_tools": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "TUNNEL_IDENTITY_ROUTING_SCHEMA",
    "build_tunnel_identity_route",
    "tunnel_identity_routing_catalog",
]
