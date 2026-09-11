"""docs: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'docs'
ACTION_LANES = {'document_convert': 'docs', 'document_edit': 'docs', 'document_enrich': 'docs', 'document_export': 'docs', 'document_generate': 'docs', 'document_index': 'docs', 'document_refresh': 'docs', 'document_render': 'docs', 'source_snapshot_retire': 'docs'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
