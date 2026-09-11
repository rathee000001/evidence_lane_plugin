"""tableau: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'tableau'
ACTION_LANES = {'source_snapshot_state': 'tableau', 'tableau_current': 'tableau', 'tableau_query': 'tableau', 'tableau_read': 'tableau'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
