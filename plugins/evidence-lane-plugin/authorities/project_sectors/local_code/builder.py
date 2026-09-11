"""local_code: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'local_code'
ACTION_LANES = {'code_apply': 'local_code', 'code_index': 'local_code', 'code_index_syntax': 'local_code', 'code_refresh': 'local_code', 'code_semantic_index': 'local_code', 'source_snapshot_retire': 'local_code'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
