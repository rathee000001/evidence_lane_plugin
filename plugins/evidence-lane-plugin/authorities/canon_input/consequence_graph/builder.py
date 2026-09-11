"""canon_consequence_graph: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'canon_consequence_graph'
ACTIONS = {'task_evidence_edge_bind': True, 'task_evidence_edge_register': True, 'task_evidence_input_request': True, 'task_evidence_result': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
