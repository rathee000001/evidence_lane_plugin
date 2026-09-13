"""Custom instances through the packaged MCP adapter and canonical engine workers."""
import asyncio
import base64
import json
import os
from pathlib import Path

from evidence_lane_plugin.engine_runtime import create_runtime_engine
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import TaskDefinition
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.sector_evidence_profile import read_snapshot

from tests.test_custom_instances_v4 import database_bytes
from tests.test_evidence_sectors_v4 import sqlite_fixture
from tests.test_native_workflow_bindings import native


def join(engine):
    with engine._admission:
        assert engine._admission.wait_for(lambda: engine._background_jobs == 0, timeout=60)


def test_packaged_stdio_custom_registration_intake_refresh_and_exact_history(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    sqlite_fixture(source / 'measurements.sqlite')
    original_note = b'{"decision":"alpha","measurement":1.250}\n'
    (source / 'notes.json').write_bytes(original_note)
    source_bytes = {path.name: path.read_bytes() for path in source.iterdir()}
    with create_runtime_engine(tmp_path / 'runtime', workers=1, capabilities=CapabilityMonitor()) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)

        async def run():
            async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools'), entrypoint='package') as session:
                async def call(action, arguments=None, **envelope):
                    response = (await session.call_tool(action, {'project_id': store.project_id,
                        'arguments': arguments or {}, **envelope})).structuredContent
                    assert response['status'] == 'ok', response
                    return response['result']

                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert not tools['custom_lane_configure'].annotations.readOnlyHint
                assert tools['custom_lanes_read'].annotations.readOnlyHint
                assert (await call('client_context'))['native_task_attestation'] == 'not_provided'
                adapters = {}
                for lane, parser, extension, tables in (
                    ('custom__measurements', 'sqlite', '.sqlite', ['parent']),
                    ('custom__notes', 'json', '.json', []),
                ):
                    adapters[lane] = (await call('custom_lane_configure', {'lane_id': lane, 'display_name': lane,
                        'adapter': {'parser': parser, 'extensions': [extension],
                            'sqlite_tables': tables, 'sqlite_rows': 1}}))['result']
                assert {row['lane_id'] for row in (await call('custom_lanes_read'))['result']['rows']} == set(adapters)
                route = (await call('source_register', {'sources': [str(source / name) for name in source_bytes],
                    'overrides': {str(source / 'measurements.sqlite'): 'custom__measurements',
                        str(source / 'notes.json'): 'custom__notes'}}))['result']['source_routes']
                prepared = (await call('source_prepare_tasks', {'selection': {
                    'route_id': route['route_id'], 'occurrence_ordinals': [1, 2]},
                    'lane_options': {lane: {'parser': contract['adapter']['parser'],
                        'adapter_contract': contract['contract_sha256'],
                        'sqlite_tables': contract['adapter']['sqlite_tables'], 'sqlite_rows': 1}
                        for lane, contract in adapters.items()}}))['result']
                assert prepared['task_count'] == 2 and not prepared['materialized']
                await call('plan_create', {'title': 'Custom protocol fixture',
                    'tasks': prepared['tasks']})
                tasks = (await call('plan_read'))['tasks']

                async def execute(task, *, revision=1):
                    values = {'task_id': task['definition']['task_id'], 'plan_revision': revision,
                        'contract_digest': task['contract_digest']}
                    admitted = (await session.call_tool('delta_enter_planned', {'project_id': store.project_id,
                        'expected_revision': revision, 'arguments': values})).structuredContent
                    assert admitted['status'] == 'queued', admitted
                    await asyncio.to_thread(join, engine)
                    status = await call('delta_status', {'job_id': admitted['job_id'],
                        'include_result': True, 'include_verification': True})
                    assert status['state'] == 'verified' and status['exit'] is not None, status
                    return status['result']['result']['result'], admitted['job_id']

                produced = {}
                for task in tasks[:2]:
                    result, job_id = await execute(task)
                    lane = task['definition']['operation']['arguments']['lane_id']
                    manifest, facts = read_snapshot(store, lane, result['snapshot_id'])
                    assert manifest['adapter_contract'] == adapters[lane]['contract_sha256']
                    assert manifest['job_id'] == job_id and manifest['task_id'] == task['definition']['task_id']
                    assert manifest['source_route'] == task['definition']['operation']['source_route']
                    assert manifest['worker_envelope']['worker_pid'] != os.getpid()
                    assert not facts['fidelity']['imported_code_executed']
                    assert Path(result['natural_path']).read_bytes() == source_bytes[result['logical_name']]
                    produced[lane] = result
                rows = (await call('custom_query', {'lane_id': 'custom__measurements',
                    'snapshot_id': produced['custom__measurements']['snapshot_id'], 'collection': 'sqlite_row'}))['result']['rows']
                assert len(rows) == 1 and rows[0]['table'] == 'parent'
                selected = await call('lane_view_preview', {'view_id': 'custom__measurements.structure'})
                view = await call('lane_view_refresh', {'view_id': 'custom__measurements.structure',
                    'scope': selected['scope'], 'contract_digest': selected['contract_digest'],
                    'source_digest': selected['source_digest'], 'expected_generation': selected['generation'],
                    'formats': ['mmd', 'dot'], 'include_pointer': True})
                assert {Path(row['path']).name for row in view['files']} == {
                    'custom__measurements.mmd', 'custom__measurements.dot', 'lane_pointer.json'}
                assert all('/custom__measurements/' in row['path'].replace('\\', '/') for row in view['files'])
                measurements_before = store.lane('custom__measurements').database.read_bytes()
                (source / 'notes.json').write_bytes(b'{"decision":"beta","measurement":2.500}\n')
                contract = adapters['custom__notes']
                fresh_route = (await call('source_register', {'sources': [str(source / 'notes.json')],
                    'parent_route_id': route['route_id'],
                    'overrides': {str(source / 'notes.json'): 'custom__notes'}}))['result']['source_routes']
                refresh = (await call('source_prepare_refresh', {
                    'baseline_snapshots': [{'lane_id': 'custom__notes',
                        'snapshot_id': produced['custom__notes']['snapshot_id']}],
                    'selection': {'route_id': fresh_route['route_id'], 'occurrence_ordinals': [1]},
                    'lane_options': {'custom__notes': {'parser': 'json',
                        'adapter_contract': contract['contract_sha256'], 'sqlite_rows': 1}}}))['result']
                assert refresh['task_count'] == 1 and not refresh['plan_changed']
                prior_plan = await call('plan_read')
                message = 'Refresh the selected Custom notes from their current registered source bytes.'
                lineage = await call('lineage_record', {'kind': 'prompt', 'payload': {'text': message}})
                steer = await call('steer_submit', {'source_event_id': lineage['event_id'],
                    'source_cursor': lineage['cursor'], 'source_text': message,
                    'expected_revision': 1, 'intent': 'semantic', 'rationale': message})
                await call('plan_refresh', {'plan_id': prior_plan['plan_id'], 'title': 'Refresh selected Custom notes',
                    'expected_revision': 1, 'expected_document_digest': prior_plan['document_digest'],
                    'steer_request_id': steer['request_id'],
                    'tasks': [row['definition'] for row in prior_plan['tasks']] + refresh['tasks']}, expected_revision=1)
                current_plan = await call('plan_read')
                assert current_plan['revision'] == 2 and current_plan['tasks'][:2] == prior_plan['tasks']
                updated, _ = await execute(current_plan['tasks'][-1], revision=2)
                assert updated['previous_snapshot'] == produced['custom__notes']['snapshot_id']
                assert store.lane('custom__measurements').database.read_bytes() == measurements_before
                before = database_bytes(store)
                old = (await call('lane_fetch', {'lane_id': 'custom__notes',
                    'snapshot_id': produced['custom__notes']['snapshot_id'], 'path': 'notes.json',
                    'representation': 'original_source'}))['result']['read']['result']
                assert base64.b64decode(old['content_base64']) == original_note
                assert (await call('custom_current', {'lane_id': 'custom__notes'}))['result']['files'][0]['snapshot_id'] == updated['snapshot_id']
                searched = await call('search', {'query': 'beta', 'lane_ids': list(adapters), 'max_bytes': 262_144})
                assert {arm['lane_id'] for arm in searched['arms'] if arm['state'] == 'hit'} == {'custom__notes'}
                pointer = await call('lane_view_read', {'view_id': 'custom__measurements.structure', 'include_content': True})
                assert pointer['state'] == 'fresh'
                assert all(row['lane_id'] == 'custom__measurements'
                    for row in json.loads(pointer['contents']['pointer'])['items_and_schema'].values())
                assert (await call('plan_read'))['counts'] == {'completed': 3}
                assert database_bytes(store) == before
                assert (source / 'measurements.sqlite').read_bytes() == source_bytes['measurements.sqlite']
                assert not (store.root / 'custom').exists()
        with LocalEndpoint(engine, studio_enabled=False):
            asyncio.run(run())
        assert engine.workers.status()['succeeded_operations'] >= 3


def test_custom_media_missing_shared_dependency_cannot_start_a_job(tmp_path, monkeypatch):
    missing = tmp_path / 'unprovisioned-toolchain'
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(missing))
    source = tmp_path / 'source'
    source.mkdir()
    raw = b'No media worker should read this without its required FFmpeg asset.'
    (source / 'clip.wav').write_bytes(raw)
    with create_runtime_engine(tmp_path / 'runtime', workers=1, capabilities=CapabilityMonitor()) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        async def run():
            async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
                async def call(action, arguments=None, **envelope):
                    return (await session.call_tool(action, {'project_id': store.project_id,
                        'arguments': arguments or {}, **envelope})).structuredContent
                configured = await call('custom_lane_configure', {'lane_id': 'custom__recordings',
                    'display_name': 'Recordings', 'adapter': {'parser': 'media', 'extensions': ['.wav']}})
                assert configured['status'] == 'ok', configured
                contract = configured['result']['result']
                task = TaskDefinition(task_id='missing-media', title='Selected unavailable media parser',
                    requested_outcome='Reject an unprovided shared runtime before work starts', profile='custom',
                    allowed_actions=['custom_index_media'], permitted_paths=['clip.wav'],
                    permitted_tools=['Python', 'FFmpeg', 'SQLite_FTS5_BM25'],
                    acceptance_checks=list(engine.registry.get('custom_index_media').verification_checks))
                assert (await call('plan_create', {'title': 'Unavailable dependency fixture',
                    'tasks': [task.model_dump(mode='json')]}))['status'] == 'ok'
                selected = (await call('plan_read'))['result']['tasks'][0]
                arguments = {'lane_id': 'custom__recordings', 'filename': 'clip.wav',
                    'parser': 'media', 'adapter_contract': contract['contract_sha256']}
                before = database_bytes(store)
                resolution = await call('toolchain_resolve', {'action': 'custom_index_media', 'arguments': arguments})
                assert resolution['status'] == 'ok', resolution
                decision = resolution['result']['resolution']
                assert decision['selected_route'] is None
                assert any(row['reason'] == 'DEPENDENCIES_UNAVAILABLE' for row in decision['attempts'])
                admitted = await call('delta_enter', {'task_id': task.task_id, 'plan_revision': 1,
                    'contract_digest': selected['contract_digest'], 'action': 'custom_index_media',
                    'arguments': arguments}, expected_revision=1)
                assert admitted['status'] == 'error' and admitted['error']['code'] == 'TOOL_ROUTE_UNAVAILABLE', admitted
                assert (await call('plan_read'))['result']['counts'] == {'queued': 1}
                assert engine.workers.status()['submitted'] == 0
                assert database_bytes(store) == before
                assert (source / 'clip.wav').read_bytes() == raw and not missing.exists()
        with LocalEndpoint(engine, studio_enabled=False):
            asyncio.run(run())
