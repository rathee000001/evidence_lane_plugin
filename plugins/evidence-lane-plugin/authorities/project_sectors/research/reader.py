"""research: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'research'
ACTION_LANES = {'research_current': 'research', 'research_query': 'research', 'research_read': 'research', 'source_snapshot_state': 'research'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
