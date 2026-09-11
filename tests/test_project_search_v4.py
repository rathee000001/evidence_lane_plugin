"""Original public search routes over current separate owners and immutable bytes."""
import asyncio
import hashlib
import json
import sys
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.registry import ActionRegistry, ActionSpec, Contract, SearchRoute
from evidence_lane_plugin.studio_gateway import StudioGateway
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.test_code_profile_v4 import call, code_system, create_plan, execute

__all__ = ['code_system']


def bytes_digest(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def test_project_search_separates_content_coverage_and_preserves_all_bytes(code_system):
    create_plan(code_system)
    indexed = execute(code_system)
    engine, store, _ = code_system
    before = bytes_digest(store.root)
    worker_count = engine.workers.status()['submitted']
    response = call(code_system, 'search', {'query': 'greeting improbablemissingword'})
    assert response.status == 'ok', response.error
    page = response.result
    arms = {arm['lane_id']: arm for arm in page['arms']}
    assert arms['local_code']['state'] == 'hit'
    assert arms['local_code']['queries'][0]['snapshot_id'] == indexed['snapshot_id']
    assert {row['path'] for row in arms['local_code']['queries'][0]['result']['result']['rows']} == {'app.py', 'helper.py'}
    assert arms['github_code']['state'] == 'uninitialized'
    assert arms['docs']['state'] == 'uninitialized'
    assert arms['canon']['state'] == 'unavailable'
    assert arms['research']['state'] == 'uninitialized'
    assert not page['complete_within_selected_scope']
    assert page['source_currentness'] == 'not_rechecked'
    assert page['ranking_scope'] == 'within_each_owner_snapshot_only'
    assert not page['mutation_performed'] and not page['refresh_performed']
    assert not page['databases_merged']
    assert bytes_digest(store.root) == before
    assert engine.workers.status()['submitted'] == worker_count


def test_single_lane_search_keeps_direct_all_match_and_snapshot_source_boundary(code_system):
    create_plan(code_system)
    indexed = execute(code_system)
    store = code_system[1]
    (store.source_root / 'helper.py').write_text('later changed source', encoding='utf-8')
    before = bytes_digest(store.root)
    args = {'query': 'greeting unmatchedword'}
    direct = call(code_system, 'code_query', args | {'snapshot_id': indexed['snapshot_id']})
    assert direct.status == 'ok' and direct.result['result']['rows'] == []
    page = call(code_system, 'lane_search', args | {'lane_id': 'local_code'})
    assert page.status == 'ok', page.error
    assert page.result['arms'][0]['hit_count'] == 2
    assert page.result['source_currentness'] == 'not_rechecked'
    assert page.result['complete_within_selected_scope']
    missing = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'unmatchedword'})
    assert missing.status == 'ok' and missing.result['arms'][0]['state'] == 'no_hit'
    assert bytes_digest(store.root) == before


def test_search_denies_wrong_lanes_permissions_revision_and_output_budget(code_system):
    create_plan(code_system)
    execute(code_system)
    engine, store, session = code_system
    before = bytes_digest(store.root)
    for arguments in ({'query': 'greeting', 'lane_ids': ['local_code', 'local_code']},
                      {'query': 'greeting', 'lane_ids': ['connector_brain']},
                      {'query': 'greeting', 'lane_ids': ['access']}):
        assert call(code_system, 'search', arguments).error.code == 'SEARCH_LANES_INVALID'
    assert call(code_system, 'search', {'query': '!!!'}).error.code == 'INVALID_ARGUMENTS'
    assert call(code_system, 'search', {'query': 'greeting', 'lane_ids': ['local_code']}, expected_revision=2).error.code == 'QUERY_PLAN_REVISION_MISMATCH'
    assert call(code_system, 'search', {'query': 'greeting', 'max_bytes': 4096}).error.code == 'QUERY_OUTPUT_BUDGET'
    context = engine.clients.context(session, store.project_id, 'read')
    limited = replace(context, allowed_actions=frozenset({'search'}))
    with pytest.raises(LaneError) as denied:
        engine.registry.execute('search', {'query': 'greeting', 'lane_ids': ['local_code']}, limited)
    assert denied.value.code == 'ACTION_SCOPE_DENIED'
    assert bytes_digest(store.root) == before


def test_search_rejects_changed_current_snapshot_and_timeout_without_writes(code_system, monkeypatch):
    create_plan(code_system)
    execute(code_system)
    engine, store, _ = code_system
    before = bytes_digest(store.root)
    original = engine.registry.execute
    current_calls = 0

    def changed(name, arguments, context):
        nonlocal current_calls
        value = original(name, arguments, context)
        if name == 'code_current':
            current_calls += 1
            if current_calls == 2:
                value['result']['scopes'][0]['snapshot_id'] = '0' * 64
        return value

    monkeypatch.setattr(engine.registry, 'execute', changed)
    assert call(code_system, 'lane_search', {'query': 'greeting', 'lane_id': 'local_code'}).error.code == 'SEARCH_CURRENT_CHANGED'

    def slow(name, arguments, context):
        value = original(name, arguments, context)
        if name == 'code_current':
            time.sleep(.12)
        return value

    monkeypatch.setattr(engine.registry, 'execute', slow)
    assert call(code_system, 'lane_search', {'query': 'greeting', 'lane_id': 'local_code', 'timeout_ms': 100}).error.code == 'QUERY_TIMEOUT'
    assert bytes_digest(store.root) == before


def test_original_search_tools_run_through_production_stdio_and_studio(code_system):
    create_plan(code_system)
    execute(code_system)
    engine, store, _ = code_system
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
                for name in ('search', 'lane_search'):
                    assert tools[name].annotations.readOnlyHint
                response = await session.call_tool('search', {'project_id': store.project_id,
                    'arguments': {'query': 'greeting', 'lane_ids': ['local_code'], 'retrieval': 'hybrid'}})
                assert not response.isError and response.structuredContent['status'] == 'ok', response
                assert response.structuredContent['result']['arms'][0]['hit_count'] == 2
                assert response.structuredContent['result']['arms'][0]['queries'][0]['ranking']['method'] == 'hybrid'
                assert json.loads(response.content[0].text) == response.structuredContent
        asyncio.run(exercise())
    gateway = StudioGateway(engine)
    _, session = gateway.exchange(gateway.issue_ticket())
    result = gateway.command('read', {'project_id': store.project_id, 'action': 'lane_search',
        'arguments': {'query': 'greeting', 'lane_id': 'local_code'}}, session)
    assert result['arms'][0]['hit_count'] == 2
    assert bytes_digest(store.root) == before


def test_search_registration_rejects_unbounded_mutation_and_wrong_typed_route():
    class Query(Contract):
        query: str
        limit: int

    registry = ActionRegistry()
    with pytest.raises(LaneError) as invalid:
        registry.register(ActionSpec('bad_search', 'bad', Query, Query, lambda c, r: r,
            permission='write', mutates=True, search=SearchRoute(('local_code',), 'rows')))
    assert invalid.value.code == 'INVALID_SEARCH_ROUTE'
    registry.register(ActionSpec('declared_search', 'declared', Query, Query, lambda c, r: r,
        queryable_in_delta=True, search=SearchRoute(('local_code',), 'rows', match_mode='any')))
    with pytest.raises(LaneError) as invalid:
        registry.freeze()
    assert invalid.value.code == 'INVALID_SEARCH_ROUTE'


def test_search_pages_current_scopes_and_does_not_substitute_old_snapshot(code_system):
    create_plan(code_system, ('code_index', 'code_index'))
    first = execute(code_system, arguments={'paths': ['helper.py']})
    second = execute(code_system, arguments={'paths': ['app.py']}, index=1)
    before = bytes_digest(code_system[1].root)
    arguments = {'lane_id': 'local_code', 'query': 'greeting', 'snapshot_limit': 1}
    page = call(code_system, 'lane_search', arguments)
    assert page.status == 'ok', page.error
    arm = page.result['arms'][0]
    assert arm['truncated'] and arm['next_snapshot_offset'] == 1
    assert not page.result['complete_within_selected_scope']
    next_page = call(code_system, 'lane_search', arguments | {'snapshot_offset': 1})
    assert next_page.status == 'ok', next_page.error
    next_arm = next_page.result['arms'][0]
    assert next_arm['next_snapshot_offset'] is None
    assert {arm['queries'][0]['snapshot_id'], next_arm['queries'][0]['snapshot_id']} == {first['snapshot_id'], second['snapshot_id']}
    assert bytes_digest(code_system[1].root) == before


def test_authority_search_preserves_learning_memory_and_lineage_provenance(code_system):
    create_plan(code_system)
    execute(code_system)
    store = code_system[1]
    task = PlanStore(store).task('code-0', expected_revision=1)
    memory = call(code_system, 'memory_ingest', {'locators': [{'label': 'greeting reference',
        'search_terms': ['greeting'], 'reference': {'kind': 'plan_task', 'key': 'code-0',
            'revision': 1, 'digest': task.contract_digest}}]})
    assert memory.status == 'ok', memory.error
    lineage = call(code_system, 'lineage_record', {'kind': 'assistant', 'payload': {'text': 'greeting visible evidence'}})
    assert lineage.status == 'ok', lineage.error
    before = bytes_digest(store.root)
    response = call(code_system, 'search', {'query': 'greeting code_index',
        'lane_ids': ['learning', 'memory', 'chat_lineage']})
    assert response.status == 'ok', response.error
    arms = {arm['lane_id']: arm for arm in response.result['arms']}
    for arm in arms.values():
        assert arm['hit_count'] == 1 and arm['state'] == 'hit'
    assert arms['memory']['queries'][0]['result']['locators'][0]['locator']['reference']['kind'] == 'plan_task'
    assert arms['learning']['queries'][0]['result']['authority'] == 'project_learning'
    assert arms['chat_lineage']['queries'][0]['result']['events'][0]['provenance'] == 'agent_report'
    direct = call(code_system, 'lineage_read', {'query': 'greeting code_index'})
    assert direct.status == 'ok' and direct.result['events'] == []
    assert bytes_digest(store.root) == before


def test_search_rejects_corrupt_immutable_source_without_repair(code_system):
    create_plan(code_system)
    execute(code_system)
    store = code_system[1]
    current = call(code_system, 'code_current')
    manifest = current.result['result']['scopes'][0]['manifest_object']
    path = store.lane('local_code').object_path(manifest)
    path.write_bytes(path.read_bytes() + b' ')
    before = bytes_digest(store.root)
    result = call(code_system, 'search', {'query': 'greeting', 'lane_ids': ['local_code']})
    assert result.error.code == 'CODE_SNAPSHOT_INTEGRITY'
    assert bytes_digest(store.root) == before
