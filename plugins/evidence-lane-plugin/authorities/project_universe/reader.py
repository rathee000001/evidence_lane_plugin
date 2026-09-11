"""universe: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'universe'
ACTIONS = {'bigger_universe_read': True, 'bigger_universe_verify': True, 'linked_projects_read': True, 'universe_inspect': True, 'universe_links_verify': True, 'universe_query': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
