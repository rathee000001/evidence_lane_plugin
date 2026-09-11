"""memory: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'memory'
ACTIONS = {'memory_read': True, 'memory_rehydrate': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
