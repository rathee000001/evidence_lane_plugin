"""pdf_ocr: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'pdf_ocr'
ACTION_LANES = {'pdf_current': 'pdf_ocr', 'pdf_enrichment_read': 'pdf_ocr', 'pdf_ocr_read': 'pdf_ocr', 'pdf_query': 'pdf_ocr', 'pdf_read': 'pdf_ocr', 'pdf_render_read': 'pdf_ocr', 'source_snapshot_state': 'pdf_ocr'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
