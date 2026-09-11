"""Tableau: separate native document/extract, retrieval and schema bindings."""
from evidence_lane_plugin.sector_support import sector_migrations
from evidence_lane_plugin.tableau_profile import current_tableaus, query_tableau, read_tableau

LANE_ID = 'tableau'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_tableaus(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_tableau', 'read_tableau']
