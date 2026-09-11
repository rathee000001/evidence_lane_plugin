"""artifacts: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'artifacts'
ACTION_LANES = {'artifacts_current': 'artifacts', 'artifacts_query': 'artifacts', 'artifacts_read': 'artifacts', 'source_snapshot_state': 'artifacts'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
