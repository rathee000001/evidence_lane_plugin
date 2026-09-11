"""memory: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'memory'
ACTIONS = {'memory_checkpoint': True, 'memory_ingest': True, 'project_memory_record_link': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
