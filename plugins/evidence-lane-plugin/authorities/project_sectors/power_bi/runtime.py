"""Power BI: separate model/report/data, retrieval and schema bindings."""
from evidence_lane_plugin.powerbi_profile import (
    current_powerbis,
    query_powerbi,
    read_powerbi,
    read_powerbi_schema,
)
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'power_bi'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_powerbis(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_powerbi', 'read_powerbi', 'read_powerbi_schema']
