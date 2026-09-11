"""plan: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'plan'
ACTIONS = {'delta_enter': True, 'delta_enter_planned': True, 'job_reconcile_effect': True, 'plan_create': True, 'plan_host_bind': True, 'plan_host_sync': True, 'plan_refresh': True, 'steer_submit': True, 'validation_policy_set': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
