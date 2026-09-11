"""receipts: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'receipts'
ACTIONS = {'accelerator_configure': True, 'capture_bind': True, 'connector_configure': True, 'connector_revoke': True, 'session_boot': True, 'session_exit': True, 'session_resume': True, 'storage_connector_select': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
