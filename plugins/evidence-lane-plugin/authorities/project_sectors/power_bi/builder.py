"""power_bi: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'power_bi'
ACTION_LANES = {'powerbi_edit': 'power_bi', 'powerbi_export': 'power_bi', 'powerbi_generate': 'power_bi', 'powerbi_index': 'power_bi', 'powerbi_refresh': 'power_bi', 'source_snapshot_retire': 'power_bi'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
