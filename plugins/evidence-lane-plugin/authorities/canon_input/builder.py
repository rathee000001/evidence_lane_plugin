"""canon: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'canon'
ACTIONS = {'canon_backfire': True, 'canon_decide': True, 'canon_expect': True, 'canon_join': True, 'canon_receive': True, 'canon_send': True, 'canon_supersede': True, 'canon_task_edge_bind': True, 'canon_task_edge_register': True, 'canon_task_result': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
