"""data: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'data'
ACTION_LANES = {'data_current': 'data', 'data_inspection_read': 'data', 'data_query': 'data', 'data_read': 'data', 'source_snapshot_state': 'data'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
