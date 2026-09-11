"""Real stdio MCP Research routes; isolated engine, no installed-host claim."""
import asyncio
import base64
import json

from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore

from .test_evidence_sectors_v4 import plan
from .test_native_workflow_bindings import native
from .test_research_web_v4 import TOOLS
from .test_research_web_v4 import server as server  # noqa: PLC0414
from .test_research_web_v4 import system as system  # noqa: PLC0414


def test_stdio_capture_refresh_citations_and_historical_fetch(system, server, monkeypatch):
    engine, store, _ = system
    url, events = server
    first_bytes = b'<base href="https://reference.example/first/"><p>Recorded evidence</p><a href="trial">Source</a>'
    second_bytes = first_bytes.replace(b'/first/', b'/second/')
    monkeypatch.setattr('tests.test_research_web_v4.HTML', first_bytes)
    plan(system, 'research', ['research_web_capture', 'research_web_capture'], tools=TOOLS)

    def join():
        with engine._admission:
            assert engine._admission.wait_for(lambda: engine._background_jobs == 0, timeout=60)

    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools', 'network')) as session:
            async def read(action, arguments=None):
                response = (await session.call_tool(action, {'project_id': store.project_id,
                    'arguments': arguments or {}})).structuredContent
                assert response['status'] == 'ok', response
                return response['result']

            context = await read('client_context')
            assert context['native_task_attestation'] == 'not_provided'
            snapshots = []
            for index, raw in enumerate([first_bytes, second_bytes]):
                monkeypatch.setattr('tests.test_research_web_v4.HTML', raw)
                task = PlanStore(store).task('evidence-' + str(index), expected_revision=1)
                response = (await session.call_tool('delta_enter', {'project_id': store.project_id,
                    'expected_revision': 1, 'arguments': {'task_id': task.definition.task_id,
                        'plan_revision': 1, 'contract_digest': task.contract_digest, 'action': 'research_web_capture',
                        'arguments': {'url': url + '/ok', 'expected_snapshot': snapshots[-1]['snapshot_id'] if snapshots else None}}})).structuredContent
                assert response['status'] == 'queued', response
                await asyncio.to_thread(join)
                with store.lane('plan').connection(read_only=True) as connection:
                    row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (response['job_id'],)).fetchone())
                assert row['state'] == 'verified', row
                produced = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
                assert produced['generation'] == index + 1
                snapshots.append(produced)
            before = {str(path): path.read_bytes() for path in store.root.rglob('*.sqlite')}
            for snapshot, raw, base in zip(snapshots, [first_bytes, second_bytes], ['first', 'second']):
                citations = (await read('research_query', {'snapshot_id': snapshot['snapshot_id'], 'collection': 'research_citation'}))['result']
                assert [item['url'] for item in citations['rows']] == [f'https://reference.example/{base}/trial']
                assert all(item['assertion_status'] == 'source_assertion_unvalidated' for item in citations['rows'])
                fetched = (await read('lane_fetch', {'lane_id': 'research', 'snapshot_id': snapshot['snapshot_id'],
                    'path': snapshot['logical_name'], 'representation': 'original_source'}))['result']['read']['result']
                assert base64.b64decode(fetched['content_base64']) == raw
                metadata = (await read('research_query', {'snapshot_id': snapshot['snapshot_id'], 'collection': 'metadata'}))['result']
                assert metadata['fidelity']['citation_resolution']['base_url'] == f'https://reference.example/{base}/'
            assert (await read('research_current'))['result']['files'][0]['snapshot_id'] == snapshots[-1]['snapshot_id']
            forged = await session.call_tool('research_web_capture', {'project_id': store.project_id,
                'arguments': {'url': url + '/ok', 'network_allowed': True}})
            assert forged.isError
            assert before == {str(path): path.read_bytes() for path in store.root.rglob('*.sqlite')}
    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(run())
    assert len(events) == 2 and all(event['path'] == '/ok' for event in events)
