"""custom: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'custom'
ACTION_LANES = {'custom_current': 'custom', 'custom_query': 'custom', 'custom_read': 'custom', 'source_snapshot_state': 'custom'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
