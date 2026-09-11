"""Bind business-intent skills to the current workflow and action registry."""

from evidence_lane_plugin.registry import WORKFLOWS
from evidence_lane_plugin.workflow_surface import skill_action_reference

from ..contracts import load_sdk_contract


def skill_binding_catalog():
    """Return every business skill and its current action/workflow package."""

    return load_sdk_contract("skills/skill-bindings.v4.json")

__all__ = ["WORKFLOWS", "skill_action_reference", "skill_binding_catalog"]
