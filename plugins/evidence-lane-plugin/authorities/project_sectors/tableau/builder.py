"""tableau: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'tableau'
ACTION_LANES = {'source_snapshot_retire': 'tableau', 'tableau_edit': 'tableau', 'tableau_export': 'tableau', 'tableau_generate': 'tableau', 'tableau_index': 'tableau', 'tableau_refresh': 'tableau'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
