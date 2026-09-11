"""universe: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'universe'
ACTIONS = {'project_evidence_link': True, 'project_evidence_network_create': True, 'project_evidence_network_grant': True, 'project_evidence_network_link': True, 'project_evidence_network_register': True, 'project_evidence_network_revoke': True, 'project_evidence_unlink': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
