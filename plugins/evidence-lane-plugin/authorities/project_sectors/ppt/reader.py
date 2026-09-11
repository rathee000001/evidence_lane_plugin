"""ppt: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'ppt'
ACTION_LANES = {'presentation_current': 'ppt', 'presentation_enrichment_read': 'ppt', 'presentation_query': 'ppt', 'presentation_read': 'ppt', 'presentation_render_read': 'ppt', 'source_snapshot_state': 'ppt'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
