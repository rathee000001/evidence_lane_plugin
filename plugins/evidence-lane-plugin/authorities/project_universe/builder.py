"""universe: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'universe'
ACTIONS = {'bigger_universe_create': True, 'bigger_universe_grant': True, 'bigger_universe_link': True, 'bigger_universe_register': True, 'bigger_universe_revoke': True, 'project_link': True, 'project_unlink': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
