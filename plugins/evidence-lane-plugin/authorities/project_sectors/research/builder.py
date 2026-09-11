"""research: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'research'
ACTION_LANES = {'research_index': 'research', 'research_index_media': 'research', 'research_refresh': 'research', 'research_refresh_media': 'research', 'research_web_capture': 'research', 'research_web_discover': 'research', 'research_web_extract': 'research', 'source_snapshot_retire': 'research'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
