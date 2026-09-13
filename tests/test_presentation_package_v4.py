"""Presentation package, public stdio routing and exact graph locators."""
import asyncio
import importlib.util
import json
import time
from pathlib import Path

from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sector_support import sector_package_contract

from .test_native_workflow_bindings import native
from .test_presentation_profile_v4 import call, execute, plan
from .test_presentation_profile_v4 import (
    presentation_system as presentation_system,  # noqa: PLC0414
)


def test_presentation_package_binds_its_own_reader_and_migrations(presentation_system):
    engine, store, _ = presentation_system
    path = Path('plugins/evidence-lane-plugin/authorities/project_sectors/ppt/runtime.py')
    spec = importlib.util.spec_from_file_location('fixture_ppt_runtime', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LANE_ID == 'ppt' and {value.owner for value in module.migrations()} == {'ppt', 'pptselector'}
    assert module.inspect(store)['initialized'] is False
    manifest = sector_package_contract('ppt', engine.registry)
    assert len(manifest['actions']) == 15
    assert manifest['database'] == 'ppt/ppt_sector_v001.sqlite'
    assert manifest['runtime_module'] == 'evidence_lane_plugin.presentation_profile'
    assert {item['name'] for item in manifest['actions']} >= {'presentation_generate', 'presentation_convert', 'presentation_enrich'}


def test_presentation_views_preserve_slide_and_note_locators(presentation_system):
    plan(presentation_system, ['presentation_index'])
    indexed = execute(presentation_system, 'presentation_index', {'filename': 'fixture.pptx'})
    arguments = {'view_id': 'ppt.structure', 'scope': {'query': indexed['presentation_id']}}
    response = call(presentation_system, 'lane_view_preview', arguments)
    assert response.status == 'ok', response.error
    preview = response.result
    slides = [node for node in preview['graph']['nodes'] if node['kind'] == 'presentation_slide']
    assert {node['locator']['slide_number'] for node in slides} == {1, 2}
    lookup = {node['id']: node for node in preview['graph']['nodes']}
    sequential = [edge for edge in preview['graph']['edges'] if edge['kind'] == 'NEXT']
    assert len(sequential) == 1
    assert (lookup[sequential[0]['source']]['locator']['slide_number'], lookup[sequential[0]['target']]['locator']['slide_number']) == (1, 2)
    assert any(node['kind'] == 'presentation_shape' for node in lookup.values())
    published = call(presentation_system, 'lane_view_refresh', {**arguments, 'scope': preview['scope'],
        'expected_generation': preview['generation'], 'contract_digest': preview['contract_digest'],
        'source_digest': preview['source_digest'], 'formats': ['mmd', 'dot'], 'include_pointer': True})
    assert published.status == 'ok', published.error
    assert {Path(row['path']).name for row in published.result['files']} == {'ppt.mmd', 'ppt.dot', 'ppt.pointer.json'}
    assert all('/ppt/' in row['path'].replace('\\', '/') for row in published.result['files'])
    read = call(presentation_system, 'lane_view_read', {'view_id': arguments['view_id'],
        'snapshot_digest': published.result['snapshot_digest'], 'include_content': True})
    assert read.status == 'ok', read.error
    pointer = json.loads(read.result['contents']['pointer'])
    assert not pointer['page_numbers_from_graph']
    assert {row['snapshot_id'] for row in pointer['locators'].values()} == {indexed['snapshot_id']}


def test_stdio_presentation_intake_and_query(presentation_system):
    engine, store, _ = presentation_system
    plan(presentation_system, ['presentation_index'])
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            task = PlanStore(store).task('ppt-0', expected_revision=1)
            admitted = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                    'action': 'presentation_index', 'arguments': {'filename': 'fixture.pptx'}}})
            body = admitted.structuredContent
            assert body['status'] == 'queued', body
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                with store.lane('plan').connection(read_only=True) as connection:
                    row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                if row['state'] in {'verified', 'blocked'}:
                    break
                await asyncio.sleep(.03)
            assert row['state'] == 'verified', row
            indexed = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
            read = await session.call_tool('presentation_query', {'project_id': store.project_id,
                'arguments': {'snapshot_id': indexed['snapshot_id'], 'collection': 'slide'}})
            assert read.structuredContent['status'] == 'ok', read.structuredContent
            assert [item['slide_number'] for item in read.structuredContent['result']['result']['rows']] == [1, 2]
    with LocalEndpoint(engine):
        asyncio.run(run())
