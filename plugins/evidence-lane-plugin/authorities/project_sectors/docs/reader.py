"""docs: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'docs'
ACTION_LANES = {'document_current': 'docs', 'document_enrichment_read': 'docs', 'document_query': 'docs', 'document_read': 'docs', 'document_render_read': 'docs', 'source_snapshot_state': 'docs'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
