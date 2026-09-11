"""images_ocr: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'images_ocr'
ACTION_LANES = {'media_export': 'images_ocr', 'media_extract': 'images_ocr', 'media_index': 'images_ocr', 'media_ocr': 'images_ocr', 'media_refresh': 'images_ocr', 'media_transform': 'images_ocr', 'source_snapshot_retire': 'images_ocr'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
