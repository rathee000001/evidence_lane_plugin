"""power_bi: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'power_bi'
ACTION_LANES = {'powerbi_current': 'power_bi', 'powerbi_query': 'power_bi', 'powerbi_read': 'power_bi', 'powerbi_schema': 'power_bi', 'source_snapshot_state': 'power_bi'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
