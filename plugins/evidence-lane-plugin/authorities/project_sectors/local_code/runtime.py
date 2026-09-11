"""local_code: the canonical Code operation and schema bindings."""
from evidence_lane_plugin.code_profile import impact_code, query_code, read_code, read_current
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'local_code'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return read_current(project, LANE_ID)

# Mutations enter through the shared registry and exact Delta contract.
__all__ = ['LANE_ID', 'impact_code', 'inspect', 'migrations', 'query_code', 'read_code']
