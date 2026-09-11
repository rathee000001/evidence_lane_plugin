"""sources: authenticated public SDK mutation binding."""
from evidence_lane_plugin.authority_support import call_authority_action

OWNER_ID = 'sources'
ACTIONS = {'custom_lane_configure': True, 'lane_configure_routes': True, 'source_crosswalk': True, 'source_git_history': True, 'source_git_impact': True, 'source_graph': True, 'source_graph_diff': True, 'source_graph_impact': True, 'source_identity': True, 'source_inspect_sqlite': True, 'source_materialization_reconcile': True, 'source_materialize': True, 'source_prepare_refresh': True, 'source_prepare_tasks': True, 'source_reconcile_archives': True, 'source_register': True, 'source_schema_configure': True, 'source_schema_map': True}


def build_authority(client, **request):
    return call_authority_action(client, ACTIONS, **request)

__all__ = ["OWNER_ID", "build_authority"]
