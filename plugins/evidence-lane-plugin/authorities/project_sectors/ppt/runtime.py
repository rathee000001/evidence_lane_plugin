"""PPT: separate native presentation, retrieval and schema bindings."""
from evidence_lane_plugin.presentation_profile import (
    current_presentations,
    query_presentation,
    read_presentation,
)
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'ppt'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_presentations(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_presentation', 'read_presentation']
