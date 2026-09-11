"""Packaged stdio routing is protocol proof, not installed Codex attestation."""
import asyncio

from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore

from tests.test_code_profile_v4 import code_system
from tests.test_native_workflow_bindings import call, native
from tests.test_source_selectors_v4 import plan
from tests.test_tabular_profile_v4 import execute

__all__ = ['code_system']


def test_packaged_mcp_exposes_exact_refresh_contracts_and_preserves_delta_admission(code_system):
    engine, store, _ = code_system
    plan(code_system, ['code_index'])
    snapshot = execute(code_system, 'code_index', {'paths': ['helper.py']})['snapshot_id']
    (store.source_root / 'helper.py').unlink()
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools'), entrypoint='package') as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            assert {'source_snapshot_state', 'source_prepare_refresh', 'source_snapshot_retire'} <= set(tools)
            assert tools['source_snapshot_state'].annotations.readOnlyHint is True
            assert tools['source_snapshot_retire'].annotations.readOnlyHint is False
            state = await call(session, 'source_snapshot_state', store.project_id,
                lane_id='local_code', snapshot_id=snapshot)
            assert state['status'] == 'ok' and state['result']['result']['active'], state
            denied = await call(session, 'source_snapshot_retire', store.project_id,
                lane_id='local_code', snapshot_id=snapshot)
            assert denied['error']['code'] == 'DELTA_REQUIRED'
            prepared = await call(session, 'source_prepare_refresh', store.project_id,
                baseline_snapshots=[{'lane_id': 'local_code', 'snapshot_id': snapshot}])
            assert prepared['status'] == 'ok', prepared
            assert prepared['result']['result']['task_count'] == 1
            assert not prepared['result']['result']['plan_changed']
            assert PlanStore(store).snapshot().revision == 1
            assert engine.registry.selector_owner('local_code').snapshot(store, snapshot)['active']
    with LocalEndpoint(engine):
        asyncio.run(run())
