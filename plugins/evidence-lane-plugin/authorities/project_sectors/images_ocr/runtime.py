"""Images/media: owning native structure, derivatives and schema bindings."""
from evidence_lane_plugin.media_profile import current_media, query_media, read_media
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'images_ocr'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_media(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_media', 'read_media']
