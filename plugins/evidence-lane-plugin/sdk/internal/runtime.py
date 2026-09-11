"""Bind the internal SDK plane to the one current typed action registry."""

from evidence_lane_plugin.registry import ActionContext, ActionRegistry
from evidence_lane_plugin.sdk import dispatch

from ..contracts import load_sdk_contract


def internal_module_registry():
    """Return exact bindings for every current canonical Python module."""

    return load_sdk_contract("internal/module-registry.v4.json")


def public_action_registry():
    """Return the full typed action registry with SDK/MCP/schema bindings."""

    return load_sdk_contract("internal/public-action-registry.v4.json")


def workflow_registry():
    """Return business workflow to current action/package mappings."""

    return load_sdk_contract("internal/workflow-registry.v4.json")

__all__ = [
    "ActionContext",
    "ActionRegistry",
    "dispatch",
    "internal_module_registry",
    "public_action_registry",
    "workflow_registry",
]
