"""Bind SDK workflow discovery to the current business-intent skill registry."""

from evidence_lane_plugin.workflow_surface import (
    WorkflowCatalog,
    WorkflowQuery,
    skill_action_reference,
)

__all__ = ["WorkflowCatalog", "WorkflowQuery", "skill_action_reference"]
