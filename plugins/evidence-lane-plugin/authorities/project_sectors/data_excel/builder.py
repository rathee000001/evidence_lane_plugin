"""data_excel: typed binding to the authenticated shared SDK and owning engine."""
from evidence_lane_plugin.sector_support import build_sector_source

LANE_ID = 'data_excel'
ACTION_LANES = {'source_snapshot_retire': 'data_excel', 'spreadsheet_edit': 'data_excel', 'spreadsheet_export': 'data_excel', 'spreadsheet_generate': 'data_excel', 'spreadsheet_index': 'data_excel', 'spreadsheet_index_values': 'data_excel', 'spreadsheet_inspect_openpyxl': 'data_excel', 'spreadsheet_inspect_pandas': 'data_excel', 'spreadsheet_recalculate': 'data_excel', 'spreadsheet_refresh': 'data_excel', 'spreadsheet_refresh_values': 'data_excel', 'spreadsheet_render': 'data_excel'}

def build_lane_sources(client, **arguments):
    return build_sector_source(client, LANE_ID, ACTION_LANES, **arguments)

__all__ = ["LANE_ID", "build_lane_sources"]
