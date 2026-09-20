"""project_authority: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'project_authority'
ACTIONS = {'code_snapshot_summary': True, 'git_restore_preview': True, 'lane_catalog': False, 'lane_fetch': True, 'lane_search': True, 'lane_status': True, 'lane_view_catalog': False, 'lane_view_preview': True, 'lane_view_read': True, 'linked_project_evidence_query': True, 'project_backup_verify': True, 'project_catalog': False, 'project_evidence_heads_compare': True, 'project_evidence_history': True, 'project_evidence_select': True, 'project_recovery_inspect': True, 'project_status': True, 'render_project_panel': True, 'restoration_read': True, 'search': True, 'storage_status': True, 'toolchain_catalog': False, 'toolchain_resolve': False, 'workflow_catalog': False}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
