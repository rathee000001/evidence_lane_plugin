"""Bind SDK workflow discovery to the current business-intent skill registry."""

from evidence_lane_plugin.workflow_surface import (
    WorkflowCatalog,
    WorkflowQuery,
    skill_action_reference,
)

from ..contracts import load_sdk_contract


def workflow_package_catalog():
    """Return all current per-skill JSON/MMD/DOT workflow packages."""

    return load_sdk_contract("workflows/workflow-package-registry.v4.json")


def skill_workflow(skill: str):
    """Read one current business-skill workflow package."""

    if not skill or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in skill):
        raise ValueError("Select one lower-case business skill name.")
    return load_sdk_contract(f"workflows/skills/{skill}/workflow.v4.json")

__all__ = [
    "WorkflowCatalog",
    "WorkflowQuery",
    "skill_action_reference",
    "skill_workflow",
    "workflow_package_catalog",
]
