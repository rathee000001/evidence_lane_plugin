"""github_code: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'github_code'
ACTION_LANES = {'code_current': 'local_code', 'code_impact': 'local_code', 'code_query': 'local_code', 'code_read': 'local_code', 'code_semantic_query': 'local_code', 'code_semantic_query_faiss': 'local_code', 'code_semantic_query_vec': 'local_code', 'fetch': 'local_code', 'pv_summary': 'local_code', 'source_snapshot_state': 'github_code'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
