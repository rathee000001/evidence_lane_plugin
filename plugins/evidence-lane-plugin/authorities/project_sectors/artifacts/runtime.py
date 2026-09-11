"""artifacts: owning evidence facts, immutable files and schema bindings."""
from evidence_lane_plugin.sector_evidence_contracts import Selection, model_for
from evidence_lane_plugin.sector_evidence_profile import current, query, read
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'artifacts'

def migrations(lane_id=LANE_ID):
    selected = model_for(LANE_ID, Selection)(lane_id=lane_id)
    return sector_migrations(selected.lane_id)

def inspect(project, lane_id=LANE_ID):
    return current(project, model_for(LANE_ID, Selection)(lane_id=lane_id))

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query', 'read']
