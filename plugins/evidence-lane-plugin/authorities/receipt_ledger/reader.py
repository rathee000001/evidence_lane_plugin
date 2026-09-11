"""receipts: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'receipts'
ACTIONS = {'accelerator_read': True, 'connector_read': True, 'remote_git_action_read': True, 'session_context': True, 'session_exit_boundary': True, 'session_status': True, 'storage_connector_inspect': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
