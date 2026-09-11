"""canon: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'canon'
ACTIONS = {'canon_classify': True, 'canon_graph': True, 'canon_inbox': True, 'canon_inspect': True, 'canon_packet_classify': True, 'canon_read': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
