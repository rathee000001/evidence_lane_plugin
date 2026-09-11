"""custom: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'custom'
ACTION_LANES = {'custom_index': 'custom', 'custom_index_media': 'custom', 'custom_refresh': 'custom', 'custom_refresh_media': 'custom', 'source_snapshot_retire': 'custom'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
