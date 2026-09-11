"""Real stdio MCP and SDK routing, not installed-native host attestation."""
import asyncio
import json
import time

from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore

from .test_native_workflow_bindings import native
from .test_tabular_profile_v4 import plan
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


def test_stdio_routes_both_retained_tabular_lanes_and_export_refresh(tabular_system):
    engine, store, _ = tabular_system
    plan(tabular_system, ['spreadsheet_index', 'data_index', 'spreadsheet_export', 'data_export'])
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            snapshots = {}
            for index, action, arguments, query_name in (
                (0, 'spreadsheet_index', {'filename': 'fixture.xlsx'}, 'spreadsheet_query'),
                (1, 'data_index', {'filename': 'values.json'}, 'data_query'),
                (2, 'spreadsheet_export', {'filename': 'exported.xlsx'}, 'spreadsheet_query'),
                (3, 'data_export', {'filename': 'exported.json'}, 'data_query')):
                if action.endswith('_export'):
                    arguments['snapshot_id'] = snapshots[query_name]
                task = PlanStore(store).task('table-' + str(index), expected_revision=1)
                response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                        'action': action, 'arguments': arguments}})
                body = response.structuredContent
                assert body['status'] == 'queued', body
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    with store.lane('plan').connection(read_only=True) as connection:
                        row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                    if row['state'] in {'verified', 'blocked'}:
                        break
                    await asyncio.sleep(.03)
                assert row['state'] == 'verified', row
                generated = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
                if action.endswith('_export'):
                    assert generated['source_index_refresh_required'] is False
                    sources = await session.call_tool('source_routes_read', {'project_id': store.project_id,
                        'arguments': {'route_id': generated['source_refresh']['route_id']}})
                    assert sources.structuredContent['status'] == 'ok', sources.structuredContent
                    generated = generated['index_refresh']['result']
                snapshots[query_name] = generated['snapshot_id']
                read = await session.call_tool(query_name, {'project_id': store.project_id,
                    'arguments': {'snapshot_id': generated['snapshot_id'], 'collection': 'metadata'}})
                assert read.structuredContent['status'] == 'ok', read.structuredContent
                assert read.structuredContent['result']['result']['sha256'] == generated['sha256']
    with LocalEndpoint(engine):
        asyncio.run(run())
