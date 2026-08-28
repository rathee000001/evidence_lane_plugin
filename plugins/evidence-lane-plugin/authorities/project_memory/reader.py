"""Bounded validation/traversal binding for this separate authority."""

from evidence_lane_plugin.authority_support import validate_authority_support

AUTHORITY_ID = "project_memory"

def validate(project_root):
    return validate_authority_support(project_root, AUTHORITY_ID)

__all__ = ["AUTHORITY_ID", "validate"]
