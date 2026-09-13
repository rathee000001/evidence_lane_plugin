"""Canonical lane discovery, stored status and exact immutable fetch boundaries."""
import asyncio
import base64
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.studio_gateway import StudioGateway
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.test_code_profile_v4 import call, code_system, create_plan, execute
from tests.test_project_search_v4 import bytes_digest

__all__ = ['code_system']


def test_catalog_is_distinct_from_workflows_and_does_not_require_project(code_system):
    engine, store, _ = code_system
    before = bytes_digest(store.root)
    _, unselected = engine.clients.connect(ConnectRequest())
    response = PublicActionSDKDispatcher(engine).execute(ActionRequest(action='lane_catalog'), unselected)
    assert response.status == 'ok', response.error
    assert response.result['lane_count'] == 22
    assert not response.result['installed_readiness_verified']
    lanes = {row['definition']['canonical_lane_id']: row for row in response.result['lanes']}
    assert lanes['plan']['definition']['kind'] == 'authority'
    assert lanes['docs']['definition']['kind'] == 'sector'
    assert lanes['docs']['fetch'][0]['action'] == 'document_read'
    assert lanes['research']['fetch'][0]['action'] == 'research_read'
    assert 'access' not in lanes and 'connector_brain' not in lanes
    selected = call(code_system, 'lane_catalog', {'lane_ids': ['images_ocr'], 'include_schema': True})
    assert selected.status == 'ok', selected.error
    assert selected.result['lanes'][0]['declared_schema']['owners'] == ['media', 'imagesocrselector']
    assert call(code_system, 'lane_catalog', {'max_bytes': 4096}).error.code == 'QUERY_OUTPUT_BUDGET'
    assert bytes_digest(store.root) == before


def test_status_reports_absent_lane_then_published_schema_and_unmaterialized_view(code_system):
    store = code_system[1]
    before = bytes_digest(store.root)
    absent = call(code_system, 'lane_status', {'lane_id': 'local_code'})
    assert absent.status == 'ok', absent.error
    assert not absent.result['result']['initialized']
    assert bytes_digest(store.root) == before
    create_plan(code_system)
    execute(code_system)
    before = bytes_digest(store.root)
    status = call(code_system, 'lane_status', {'lane_id': 'local_code'})
    assert status.status == 'ok', status.error
    body = status.result['result']
    assert body['initialized'] and body['database_head_verified'] and body['schema_history_verified']
    assert {row['owner'] for row in body['schema_history']} == {'localcode'}
    assert body['object_references'] > 0 and not body['objects_verified']
    assert body['views'][0]['state'] == 'not_materialized'
    assert status.result['source_currentness'] == 'not_rechecked'
    assert not status.result['mutation_performed'] and not status.result['refresh_performed']
    assert bytes_digest(store.root) == before


def test_fetch_uses_stored_code_text_and_binary_bytes_with_exact_path_and_snapshot(code_system):
    store = code_system[1]
    raw = b'\x00\xff' + bytes(range(256)) * 12
    (store.source_root / 'opaque.py').write_bytes(raw)
    original = (store.source_root / 'helper.py').read_bytes().decode('utf-8')
    create_plan(code_system)
    indexed = execute(code_system)
    (store.source_root / 'helper.py').write_text('later source edits', encoding='utf-8')
    before = bytes_digest(store.root)
    arguments = {'lane_id': 'local_code', 'snapshot_id': indexed['snapshot_id'], 'path': 'helper.py'}
    text = call(code_system, 'lane_fetch', arguments)
    assert text.status == 'ok', text.error
    assert text.result['result']['read']['result']['content'] == original
    assert text.result['result']['typed_facts_action'] == 'code_query'
    binary = call(code_system, 'lane_fetch', arguments | {'path': 'opaque.py', 'byte_offset': 128, 'max_bytes': 1024})
    assert binary.status == 'ok', binary.error
    returned = binary.result['result']['read']['result']
    assert returned['representation'] == 'base64' and returned['truncated']
    assert base64.b64decode(returned['content']) == raw[128:1152]
    assert bytes_digest(store.root) == before


def test_lane_read_arguments_permissions_and_missing_reader_do_not_mutate(code_system):
    create_plan(code_system)
    indexed = execute(code_system)
    engine, store, session = code_system
    before = bytes_digest(store.root)
    args = {'lane_id': 'local_code', 'snapshot_id': indexed['snapshot_id'], 'path': 'helper.py'}
    assert call(code_system, 'lane_fetch', args | {'path': '../helper.py'}).error.code == 'INVALID_ARGUMENTS'
    assert call(code_system, 'lane_fetch', args | {'lane_id': 'custom'}).error.code == 'SECTOR_EVIDENCE_SNAPSHOT_MISSING'
    assert call(code_system, 'lane_status', {'lane_id': 'connector_brain'}).error.code == 'SEARCH_LANES_INVALID'
    assert call(code_system, 'lane_status', {'lane_id': 'local_code'}, expected_revision=2).error.code == 'QUERY_PLAN_REVISION_MISMATCH'
    context = engine.clients.context(session, store.project_id, 'read')
    with pytest.raises(LaneError) as denied:
        engine.registry.execute('lane_fetch', args, replace(context, allowed_actions=frozenset({'lane_fetch'})))
    assert denied.value.code == 'ACTION_SCOPE_DENIED'
    assert bytes_digest(store.root) == before


def test_status_reports_stale_view_after_refresh_without_generating_replacement(code_system):
    create_plan(code_system, ('code_index', 'code_refresh'))
    indexed = execute(code_system)
    args = {'view_id': 'local_code.relationships', 'scope': {'query': indexed['scope_id']}}
    preview = call(code_system, 'lane_view_preview', args)
    assert preview.status == 'ok', preview.error
    generated = call(code_system, 'lane_view_refresh', args | {'expected_generation': preview.result['generation'],
        'contract_digest': preview.result['contract_digest'], 'source_digest': preview.result['source_digest'],
        'formats': ['mmd', 'dot'], 'include_pointer': True})
    assert generated.status == 'ok', generated.error
    status = call(code_system, 'lane_status', {'lane_id': 'local_code'})
    assert status.status == 'ok' and status.result['result']['views'][0]['state'] == 'fresh', status.error
    (code_system[1].source_root / 'helper.py').write_text('def greeting(name):\n    return "new " + name\n', encoding='utf-8')
    execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': indexed['snapshot_id']}, index=1)
    before = bytes_digest(code_system[1].root)
    status = call(code_system, 'lane_status', {'lane_id': 'local_code'})
    assert status.status == 'ok' and status.result['result']['views'][0]['state'] == 'stale', status.error
    assert bytes_digest(code_system[1].root) == before


def test_lane_fetch_and_status_use_production_stdio_and_readonly_studio(code_system):
    create_plan(code_system)
    indexed = execute(code_system)
    engine, store, _ = code_system
    arguments = {'lane_id': 'local_code', 'snapshot_id': indexed['snapshot_id'], 'path': 'helper.py'}
    before = bytes_digest(store.root)
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--project-id', store.project_id], env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] /
                    'plugins/evidence-lane-plugin/src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                for name in ('lane_catalog', 'lane_status', 'lane_fetch'):
                    assert tools[name].annotations.readOnlyHint
                response = await session.call_tool('lane_fetch', {'project_id': store.project_id, 'arguments': arguments})
                assert not response.isError and response.structuredContent['status'] == 'ok', response
                assert 'hello' in response.structuredContent['result']['result']['read']['result']['content']
                assert json.loads(response.content[0].text) == response.structuredContent
        asyncio.run(exercise())
    gateway = StudioGateway(engine)
    _, session = gateway.exchange(gateway.issue_ticket())
    result = gateway.command('read', {'project_id': store.project_id, 'action': 'lane_status',
        'arguments': {'lane_id': 'local_code'}}, session)
    assert result['result']['schema_history_verified']
    result = gateway.command('read', {'project_id': store.project_id, 'action': 'lane_fetch',
        'arguments': arguments}, session)
    assert result['result']['read']['result']['path'] == 'helper.py'
    assert bytes_digest(store.root) == before


def test_status_rejects_corrupt_schema_history_without_repair(code_system):
    create_plan(code_system)
    execute(code_system)
    store = code_system[1]
    lane = store.lane('local_code')
    history = lane.schema_history
    history.write_bytes(history.read_bytes() + b' ')
    before = bytes_digest(store.root)
    response = call(code_system, 'lane_status', {'lane_id': 'local_code', 'include_views': False})
    assert response.error.code == 'SCHEMA_HISTORY_INTEGRITY'
    assert bytes_digest(store.root) == before


def test_fetch_rejects_missing_registered_source_without_repair(code_system):
    create_plan(code_system)
    indexed = execute(code_system)
    store = code_system[1]
    arguments = {'lane_id': 'local_code', 'snapshot_id': indexed['snapshot_id'], 'path': 'helper.py'}
    fetched = call(code_system, 'lane_fetch', arguments)
    assert fetched.status == 'ok', fetched.error
    path = store.lane('local_code').object_path(fetched.result['result']['read']['result']['file_sha256'])
    path.unlink()
    before = bytes_digest(store.root)
    response = call(code_system, 'lane_fetch', arguments)
    assert response.error.code == 'OBJECT_MISSING'
    assert bytes_digest(store.root) == before
