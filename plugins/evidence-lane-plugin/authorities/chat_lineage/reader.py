"""chat_lineage: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'chat_lineage'
ACTIONS = {'continuation_context': True, 'continuation_read': True, 'lineage_read': True, 'prompt_index_status': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
