"""images_ocr: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'images_ocr'
ACTION_LANES = {'media_current': 'images_ocr', 'media_extraction_read': 'images_ocr', 'media_ocr_read': 'images_ocr', 'media_query': 'images_ocr', 'media_read': 'images_ocr', 'source_snapshot_state': 'images_ocr'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
