"""data_excel: the owning tabular operations and schema bindings."""
from evidence_lane_plugin.sector_support import sector_migrations
from evidence_lane_plugin.tabular_contracts import Selection, model_for
from evidence_lane_plugin.tabular_profile import current, query, read

LANE_ID = 'data_excel'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current(project, model_for(LANE_ID, Selection)())

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query', 'read']
