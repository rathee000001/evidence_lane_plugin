"""project_authority: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'project_authority'
ACTIONS = {'continuation_recovery_cancel': True, 'continuation_recovery_offer': True, 'git_restore': True, 'git_restore_abandon': True, 'git_restore_reconcile': True, 'project_backup': True, 'project_deselect': False, 'project_register': False, 'project_select': False, 'restoration_reindex': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
