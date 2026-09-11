"""project_authority: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'project_authority'
ACTIONS = {'cross_project_query': True, 'git_restore_preview': True, 'lane_catalog': False, 'lane_fetch': True, 'lane_search': True, 'lane_status': True, 'project_backup_verify': True, 'project_catalog': False, 'project_recovery_inspect': True, 'project_status': True, 'pv_diff': True, 'pv_history': True, 'pv_summary': True, 'restoration_read': True, 'search': True, 'storage_status': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
