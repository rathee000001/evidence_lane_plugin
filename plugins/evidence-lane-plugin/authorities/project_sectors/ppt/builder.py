"""ppt: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'ppt'
ACTION_LANES = {'presentation_convert': 'ppt', 'presentation_edit': 'ppt', 'presentation_enrich': 'ppt', 'presentation_export': 'ppt', 'presentation_generate': 'ppt', 'presentation_index': 'ppt', 'presentation_refresh': 'ppt', 'presentation_render': 'ppt', 'source_snapshot_retire': 'ppt'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
