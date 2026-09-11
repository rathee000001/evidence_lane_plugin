"""Bind business-intent skills to the current workflow and action registry."""

from evidence_lane_plugin.registry import WORKFLOWS
from evidence_lane_plugin.workflow_surface import skill_action_reference

__all__ = ["WORKFLOWS", "skill_action_reference"]
