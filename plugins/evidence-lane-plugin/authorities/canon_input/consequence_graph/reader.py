"""canon_consequence_graph: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'canon_consequence_graph'
ACTIONS = {'task_evidence_graph': True, 'task_evidence_inspect': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
