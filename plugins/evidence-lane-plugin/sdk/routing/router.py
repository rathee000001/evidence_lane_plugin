"""Binding to the canonical outer current-route SDK."""

from evidence_lane_plugin.current_route_registry import (
    current_implementation_registry,
)

from ..contracts import load_sdk_contract


def action_route(action: str):
    """Read one generated outer and toolchain route for a current action."""

    if not action or not action.replace("_", "").isalnum() or not action.islower():
        raise ValueError("Select one lower-case registered action name.")
    return load_sdk_contract(f"routing/actions/{action}.route.v4.json")


def action_route_registry():
    """Return the complete per-action route manifest."""

    return load_sdk_contract("routing/mcp-action-routing.v4.json")

__all__ = ["action_route", "action_route_registry", "current_implementation_registry"]
