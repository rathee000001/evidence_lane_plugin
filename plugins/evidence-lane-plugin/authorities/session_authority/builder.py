"""Build/refresh binding for this separate authority support system."""

from evidence_lane_plugin.authority_support import refresh_authority_support

AUTHORITY_ID = "session_authority"

def refresh(project_root):
    return refresh_authority_support(project_root, AUTHORITY_ID)

__all__ = ["AUTHORITY_ID", "refresh"]
