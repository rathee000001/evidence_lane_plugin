"""universe: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'universe'
ACTIONS = {'project_evidence_links_read': True, 'project_evidence_links_verify': True, 'project_evidence_map_inspect': True, 'project_evidence_map_query': True, 'project_evidence_network_read': True, 'project_evidence_network_verify': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
