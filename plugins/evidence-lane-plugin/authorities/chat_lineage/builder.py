"""chat_lineage: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'chat_lineage'
ACTIONS = {'capture_bind': True, 'continuation_accept': True, 'continuation_cancel': True, 'continuation_offer': True, 'lineage_record': True, 'task_classify': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
