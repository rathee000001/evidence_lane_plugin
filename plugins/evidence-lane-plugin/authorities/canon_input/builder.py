"""canon: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'canon'
ACTIONS = {'task_evidence_decide': True, 'task_evidence_edge_bind': True, 'task_evidence_edge_register': True, 'task_evidence_expect': True, 'task_evidence_input_request': True, 'task_evidence_participant_register': True, 'task_evidence_receive': True, 'task_evidence_result': True, 'task_evidence_send': True, 'task_evidence_supersede': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
