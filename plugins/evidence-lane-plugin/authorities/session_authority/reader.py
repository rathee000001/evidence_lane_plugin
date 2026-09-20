"""sessions: authenticated public SDK read binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'sessions'
ACTIONS = {'client_context': False, 'engine_health': False, 'env_uop_inspect': False, 'lifecycle_transition_law': False, 'project_work_classify': False, 'render_runtime_panel': False, 'runtime_doctor': False, 'runtime_status': False, 'session_context': True, 'session_exit_boundary': True, 'session_flash_status': False, 'session_status': True}


def read_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "read_authority"]
