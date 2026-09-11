"""data: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'data'
ACTION_LANES = {'data_export': 'data', 'data_generate': 'data', 'data_index': 'data', 'data_index_arrow': 'data', 'data_inspect_duckdb': 'data', 'data_inspect_pandas': 'data', 'data_inspect_polars': 'data', 'data_refresh': 'data', 'data_refresh_arrow': 'data', 'data_transform': 'data', 'source_snapshot_retire': 'data'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
