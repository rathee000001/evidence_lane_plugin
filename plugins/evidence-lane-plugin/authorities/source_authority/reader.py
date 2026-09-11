"""sources: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'sources'
ACTIONS = {'custom_lanes_read': True, 'fetch': True, 'git_branch_authority': True, 'source_classify': True, 'source_materialization_read': True, 'source_preparation_read': True, 'source_read': True, 'source_routes_read': True, 'source_snapshot_state': True, 'source_verify': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
