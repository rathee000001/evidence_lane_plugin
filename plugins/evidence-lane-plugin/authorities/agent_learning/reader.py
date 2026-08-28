"""Bounded validation/traversal binding for this separate authority."""

from evidence_lane_plugin.authority_support import validate_authority_support

AUTHORITY_ID = "agent_learning"

def validate(project_root):
    return validate_authority_support(project_root, AUTHORITY_ID)

__all__ = ["AUTHORITY_ID", "validate"]
