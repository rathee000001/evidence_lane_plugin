"""plan: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'plan'
ACTIONS = {'delta_query': True, 'delta_status': True, 'job_recovery_inspect': True, 'plan_host_status': True, 'plan_read': True, 'steer_preview': True, 'validation_policy_read': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
