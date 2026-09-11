"""Tableau's packaged reader, public stdio contract, locators and tamper checks."""
import asyncio
import base64
import importlib.util
import json
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sector_support import sector_package_contract
from evidence_lane_plugin.tableau_parsers import package_members

from .test_native_workflow_bindings import native
from .test_tableau_profile_v4 import call, execute, plan
from .test_tableau_profile_v4 import (
    tableau_system as tableau_system,  # noqa: PLC0414
)


def test_tableau_package_has_own_database_and_reader(tableau_system):
    engine, store, _ = tableau_system
    path = Path('plugins/evidence-lane-plugin/authorities/project_sectors/tableau/runtime.py')
    spec = importlib.util.spec_from_file_location('fixture_tableau_runtime', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.LANE_ID == 'tableau'
    assert {migration.owner for migration in module.migrations()} == {'tableau', 'tableauselector'}
    assert not module.inspect(store)['initialized']
    manifest = sector_package_contract('tableau', engine.registry)
    assert len(manifest['actions']) == 10
    assert manifest['database'] == 'sectors/tableau/tableau_sector_v001.sqlite'
    assert manifest['runtime_module'] == 'evidence_lane_plugin.tableau_profile'
    assert {item['name'] for item in manifest['actions']} == {
        'tableau_index', 'tableau_refresh', 'tableau_current', 'tableau_query',
        'tableau_read', 'tableau_generate', 'tableau_edit', 'tableau_export',
        'source_snapshot_state', 'source_snapshot_retire',
    }


def test_tableau_graph_preserves_exact_xml_locators(tableau_system):
    plan(tableau_system, ['tableau_index'])
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'datasource_test.twb'})
    arguments = {'view_id': 'tableau.structure', 'scope': {'query': indexed['tableau_id']}}
    response = call(tableau_system, 'lane_view_preview', arguments)
    assert response.status == 'ok', response.error
    preview = response.result
    sheets = [node for node in preview['graph']['nodes'] if node['kind'] == 'tableau_sheet']
    assert len(sheets) == 2
    assert all(node['locator']['xml_path'].startswith('/workbook/worksheets/worksheet') for node in sheets)
    assert {edge['kind'] for edge in preview['graph']['edges']} == {'CONTAINS'}
    published = call(tableau_system, 'lane_view_refresh', {**arguments, 'scope': preview['scope'],
        'expected_generation': preview['generation'], 'contract_digest': preview['contract_digest'],
        'source_digest': preview['source_digest'], 'formats': ['mmd', 'dot'], 'include_pointer': True})
    assert published.status == 'ok', published.error
    assert {Path(row['path']).name for row in published.result['files']} == {
        'tableau.mmd', 'tableau.dot', 'tableau.pointer.json',
    }
    assert all('/sectors/tableau/' in row['path'].replace('\\', '/') for row in published.result['files'])
    read = call(tableau_system, 'lane_view_read', {'view_id': arguments['view_id'],
        'snapshot_digest': published.result['snapshot_digest'], 'include_content': True})
    assert read.status == 'ok', read.error
    pointer = json.loads(read.result['contents']['pointer'])
    assert pointer['layout_equivalence'] is False
    assert pointer['hyper_rows_are_bounded_samples'] is True
    assert {row['snapshot_id'] for row in pointer['locators'].values()} == {indexed['snapshot_id']}


def test_member_reads_reassemble_exact_vendor_package_members(tableau_system):
    plan(tableau_system, ['tableau_index'])
    store = tableau_system[1]
    raw = (store.source_root / 'TABLEAU_10_TWBX.twbx').read_bytes()
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'TABLEAU_10_TWBX.twbx'})
    for name, expected in package_members(raw).items():
        actual, offset = bytearray(), 0
        while True:
            result = call(tableau_system, 'tableau_read', {'snapshot_id': indexed['snapshot_id'],
                'representation': 'member', 'member': name, 'offset': offset, 'max_bytes': 1024})
            assert result.status == 'ok', result.error
            body = result.result['result']
            actual.extend(base64.b64decode(body['content_base64']))
            offset = body['next_offset']
            if offset is None:
                break
        assert actual == expected
    absent = call(tableau_system, 'tableau_read', {'snapshot_id': indexed['snapshot_id'],
        'representation': 'member', 'member': 'not-admitted.hyper'})
    assert absent.error.code == 'TABLEAU_MEMBER_MISSING'
    assert (store.source_root / 'TABLEAU_10_TWBX.twbx').read_bytes() == raw


@pytest.mark.parametrize('tamper', ['typed', 'fts'])
def test_tableau_queries_reject_altered_index_evidence(tableau_system, tamper):
    engine, store, _ = tableau_system
    plan(tableau_system, ['tableau_index'])
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'datasource_test.twb'})
    with engine.project_work.mutation(store) as lease, lease.coordinated_transaction(['tableau']), store.lane('tableau').transaction() as connection:
        if tamper == 'typed':
            connection.execute("UPDATE tableau_sheet SET payload_json=json_set(payload_json,'$.caption','ALTERED')")
        else:
            connection.execute("UPDATE tableau_chunk_fts SET text_content='altered search evidence'")
    query = {'collection': 'sheet'} if tamper == 'typed' else {'query': 'altered'}
    response = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'], **query})
    assert response.status != 'ok'
    assert response.error.code == 'TABLEAU_QUERY_INTEGRITY'


def test_stdio_tableau_intake_and_read_only_access(tableau_system):
    engine, store, _ = tableau_system
    plan(tableau_system, ['tableau_index'])

    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            task = PlanStore(store).task('tableau-0', expected_revision=1)
            admitted = await session.call_tool('delta_enter', {'project_id': store.project_id,
                'expected_revision': 1, 'arguments': {'task_id': task.definition.task_id,
                    'plan_revision': 1, 'contract_digest': task.contract_digest,
                    'action': 'tableau_index', 'arguments': {'filename': 'datasource_test.twb'}}})
            body = admitted.structuredContent
            assert body['status'] == 'queued', body
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                try:
                    with store.lane('plan').connection(read_only=True) as connection:
                        row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                except LaneError as error:
                    if error.code != 'PROJECT_RECOVERY_REQUIRED':
                        raise
                    await asyncio.sleep(.03)
                    continue
                if row['state'] in {'verified', 'blocked'}:
                    break
                await asyncio.sleep(.03)
            assert row['state'] == 'verified', row
            indexed = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
        async with native(engine.root, store.project_id) as session:
            read = await session.call_tool('tableau_query', {'project_id': store.project_id,
                'arguments': {'snapshot_id': indexed['snapshot_id'], 'collection': 'sheet'}})
            assert read.structuredContent['status'] == 'ok', read.structuredContent
            assert len(read.structuredContent['result']['result']['rows']) == 2
            context = await session.call_tool('client_context', {'arguments': {}})
            assert context.structuredContent['result']['native_task_attestation'] == 'not_provided'
            denied = await session.call_tool('tableau_export', {'project_id': store.project_id,
                'arguments': {'snapshot_id': indexed['snapshot_id'], 'filename': 'forbidden.twb'}})
            assert denied.structuredContent['status'] != 'ok'
            assert not (store.source_root / 'forbidden.twb').exists()
    with LocalEndpoint(engine):
        asyncio.run(run())


def test_stale_tableau_export_preserves_existing_destination(tableau_system):
    plan(tableau_system, ['tableau_index', 'tableau_export'])
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'datasource_test.twb'})
    destination = tableau_system[1].source_root / 'copy.twb'
    destination.write_bytes(b'newer user content')
    execute(tableau_system, 'tableau_export', {'snapshot_id': indexed['snapshot_id'], 'filename': 'copy.twb'},
        index=1, failure='TABLEAU_EXPORT_DESTINATION_CHANGED')
    assert destination.read_bytes() == b'newer user content'


def test_stale_edit_hash_does_not_publish_a_new_tableau_head(tableau_system):
    plan(tableau_system, ['tableau_index', 'tableau_edit'])
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'datasource_test.twb'})
    row = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'],
        'collection': 'calculation'}).result['result']['rows'][0]
    execute(tableau_system, 'tableau_edit', {'snapshot_id': indexed['snapshot_id'],
        'expected_sha256': '0' * 64, 'replacements': [{'part': row['part'], 'item_id': row['item_id'],
            'attribute': 'formula', 'expected_value': row['attributes']['formula'], 'value': '2'}]},
        index=1, failure='TABLEAU_EDIT_SNAPSHOT_CHANGED')
    current = call(tableau_system, 'tableau_current')
    assert current.result['result']['tableaus'][0]['snapshot_id'] == indexed['snapshot_id']
