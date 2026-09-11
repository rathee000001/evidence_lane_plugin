"""pdf_ocr: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'pdf_ocr'
ACTION_LANES = {'pdf_edit': 'pdf_ocr', 'pdf_enrich': 'pdf_ocr', 'pdf_export': 'pdf_ocr', 'pdf_generate': 'pdf_ocr', 'pdf_index': 'pdf_ocr', 'pdf_ocr': 'pdf_ocr', 'pdf_refresh': 'pdf_ocr', 'pdf_render': 'pdf_ocr', 'source_snapshot_retire': 'pdf_ocr'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
