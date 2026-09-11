"""data_excel: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import read_sector_source

LANE_ID = 'data_excel'
ACTION_LANES = {'source_snapshot_state': 'data_excel', 'spreadsheet_current': 'data_excel', 'spreadsheet_inspection_read': 'data_excel', 'spreadsheet_query': 'data_excel', 'spreadsheet_read': 'data_excel', 'spreadsheet_render_read': 'data_excel'}

def read_lane_source(client, **arguments):
    return read_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "read_lane_source"]
