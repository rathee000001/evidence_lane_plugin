"""Four service adapters through current grants, Delta, verifier and package."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from evidence_lane_plugin import bounded_io
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.connector_governance import PluginRegistration
from evidence_lane_plugin.context_index_routing import (
    CONTEXT_INDEX_QUERY,
    CONTEXT_INDEX_SYNC,
    REMOTE_INDEX_BACKENDS,
)
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest

from .context_index_http_fixture import (
    BACKENDS,
    CONFIG_KEYS,
    ENVIRONMENT,
    HASH,
    RECORDS,
    RESOURCES,
    SOURCE,
)
from .test_delta_entry import bind_fixture_flash
from .test_native_workflow_bindings import native


@pytest.fixture
def system(tmp_path, monkeypatch):
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    source = tmp_path / 'source'
    source.mkdir()
    state = {'configuration': {}, 'directories': [], 'request_counts': []}
    original = bounded_io.run_owned_bounded_process
    wrapper = Path(__file__).with_name('context_index_http_fixture_worker.py')
    with Engine(tmp_path / 'runtime') as engine:
        bind_fixture_flash(engine, tmp_path, monkeypatch)
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(hello=ClientHello(configured_profile='codex_cli'),
            projects=[ProjectSelection(project_id=store.project_id,
                permissions=['read', 'write', 'publish', 'tools', 'admin'])]))
        def controlled(command, **kwargs):
            if len(command) < 2 or Path(command[-2]).name != '_context_index_worker.py':
                return original(command, **kwargs)
            directory = Path(kwargs['cwd'])
            body = json.loads((directory / 'request.json').read_bytes())
            tool_id, operation = body['arguments']['tool_id'], body['operation']
            marker = directory / 'started.marker'
            (directory / 'fixture.json').write_text(json.dumps({'tool_id': tool_id, 'operation': operation,
                'configuration': dict(state['configuration'], marker=str(marker))}))
            request_text = (directory / 'request.json').read_text()
            assert all(value not in request_text for key, value in ENVIRONMENT.items() if key not in {
                'PINECONE_HOST', 'WEAVIATE_URL', 'MILVUS_URI', 'OPENSEARCH_URL'})
            assert all(value not in command for value in ENVIRONMENT.values())
            state['directories'].append(directory)
            try:
                return original([*command[:-2], str(wrapper), command[-1]], **kwargs)
            finally:
                state['request_counts'].append(int(marker.read_text()) if marker.exists() else 0)
        monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', controlled)
        yield engine, store, session, state


def call(system, action, arguments=None, **kwargs):
    engine, store, session, _ = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def configure(system, tool_id, **changes):
    backend_id, version = BACKENDS[tool_id]
    registration = PluginRegistration.model_validate({
        'plugin_id': tool_id.casefold() + '-index', 'name': tool_id + ' context index', 'plugin_kind': 'connector',
        'description': 'Access one rebuildable context index.', 'purpose': 'Query and synchronize selected evidence locators.',
        'config_env_keys': CONFIG_KEYS[tool_id], 'capabilities': ['context_index'], 'allowed_lanes': ['memory'],
        'allowed_actions': [CONTEXT_INDEX_QUERY, CONTEXT_INDEX_SYNC], 'resource_ids': [RESOURCES[tool_id]],
        'expires_at': 'NO_EXPIRY', 'role': 'context_index_result',
        'role_schema': {'tool_id': 'text', 'operation': 'text', 'result': 'json', 'receipt_sha256': 'blob_hash'},
        'host_profiles': ['codex_cli'], 'backend_runtime': 'python', 'backend_id': backend_id,
        'backend_version': version,
    } | changes)
    result = call(system, 'connector_configure', {'registration': registration.model_dump()})
    assert result.status == 'ok', result.error
    return result


def plan(system, tool_id, action):
    engine, store, _, _ = system
    definition = TaskDefinition(task_id='context-index', title='Use rebuildable context index',
        requested_outcome='Execute one bounded external index operation', profile='memory',
        allowed_actions=[action], permitted_tools=['Python', 'HTTPX', tool_id],
        acceptance_checks=['context_index_result_binding'])
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Context index', tasks=[definition]), lease, actor_id='fixture')
    return PlanStore(store).task('context-index', expected_revision=1)


def arguments(tool_id, action, operation=None):
    common = {'tool_id': tool_id, 'lane_id': 'memory', 'authority_id': 'memory',
        'resource_id': RESOURCES[tool_id], 'sqlite_identity_sha256': HASH, 'source_sha256': SOURCE}
    if action == CONTEXT_INDEX_QUERY:
        return common | {'query_vector': [0.1, 0.2, 0.3], 'query_text': 'evidence', 'top_k': 5}
    return common | {'operation': operation or 'upsert_changed',
        'records': RECORDS if (operation or 'upsert_changed') == 'upsert_changed' else []}


def enter(system, task, tool_id, action, operation=None):
    return call(system, 'delta_enter', {'task_id': 'context-index', 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action,
        'arguments': arguments(tool_id, action, operation)}, expected_revision=1)


def finished(system, job_id):
    engine, store, _, _ = system
    deadline = time.monotonic() + 25
    while engine.health().accepted_requests:
        if time.monotonic() >= deadline:
            pytest.fail('The context-index Delta did not finish')
        time.sleep(0.01)
    with store.lane('plan').connection(read_only=True) as connection:
        return dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (job_id,)).fetchone())


@pytest.mark.parametrize('tool_id', ['Pinecone', 'Weaviate', 'Milvus', 'OpenSearch'])
@pytest.mark.parametrize(('action', 'operation'), [(CONTEXT_INDEX_QUERY, None),
    (CONTEXT_INDEX_SYNC, 'upsert_changed'), (CONTEXT_INDEX_SYNC, 'delete_identity')])
def test_each_registered_service_and_operation_runs_under_exact_dynamic_lane_grant(system, tool_id, action, operation):
    configured = configure(system, tool_id)
    task = plan(system, tool_id, action)
    entered = enter(system, task, tool_id, action, operation)
    assert entered.status == 'queued', entered.error
    row = finished(system, entered.job_id)
    assert row['state'] == 'verified', row
    body = json.loads(system[1].lane('plan').read_object(row['result_object']))
    proof = body['tool_execution']['extension_binding']
    assert proof['lane'] == 'memory' and proof['registration_digest'] == configured.result['digest']
    assert proof['backend_id'] == REMOTE_INDEX_BACKENDS[tool_id]['backend_id']
    assert body['result']['result']['sqlite_remains_authority']
    serialized = json.dumps(body)
    assert all(value not in serialized for key, value in ENVIRONMENT.items() if key not in {
        'PINECONE_HOST', 'WEAVIATE_URL', 'MILVUS_URI', 'OPENSEARCH_URL'})
    assert system[3]['request_counts'] == [1] and all(not path.exists() for path in system[3]['directories'])


@pytest.mark.parametrize('change', [
    {'resource_ids': ['Pinecone:other']}, {'allowed_lanes': ['research']},
    {'backend_id': 'different.backend'}, {'backend_version': 'other'},
    {'config_env_keys': ['PINECONE_API_KEY']},
])
def test_scope_backend_lane_and_configuration_mismatch_stop_before_io(system, change):
    configure(system, 'Pinecone', **change)
    task = plan(system, 'Pinecone', CONTEXT_INDEX_QUERY)
    result = enter(system, task, 'Pinecone', CONTEXT_INDEX_QUERY)
    assert result.error.code == 'TOOL_ROUTE_UNAVAILABLE' and system[3]['directories'] == []


def test_provider_partial_failure_blocks_delta_result(system):
    configure(system, 'OpenSearch')
    system[3]['configuration'] = {'provider_failure': True}
    task = plan(system, 'OpenSearch', CONTEXT_INDEX_SYNC)
    entered = enter(system, task, 'OpenSearch', CONTEXT_INDEX_SYNC)
    assert entered.status == 'queued'
    row = finished(system, entered.job_id)
    assert row['state'] == 'blocked' and row['result_object'] is None
    assert row['error_code'] == 'CONTEXT_INDEX_PROVIDER_FAILURE'


def test_packaged_mcp_declares_four_routes_and_executes_real_httpx_adapter(system):
    engine, store, _, _ = system
    configure(system, 'Pinecone', host_profiles=['codex_cli', 'codex_desktop'])
    task = plan(system, 'Pinecone', CONTEXT_INDEX_QUERY)
    with LocalEndpoint(engine):
        async def exercise():
            async with native(engine.root, store.project_id,
                    permissions=('read', 'write', 'publish', 'tools', 'admin'), entrypoint='package') as session:
                tools = await session.list_tools()
                tool = next(row for row in tools.tools if row.name == CONTEXT_INDEX_QUERY)
                assert 'network_allowed' not in tool.inputSchema['properties']['arguments']['properties']
                contract = engine.registry.get(CONTEXT_INDEX_QUERY).schema()['toolchain']
                assert len(contract['routes']) == 4 and {row['extension']['backend_id'] for row in contract['routes']} == {
                    value['backend_id'] for value in REMOTE_INDEX_BACKENDS.values()}
                response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': 'context-index', 'plan_revision': 1,
                        'contract_digest': task.contract_digest, 'action': CONTEXT_INDEX_QUERY,
                        'arguments': arguments('Pinecone', CONTEXT_INDEX_QUERY)}})
                result = response.structuredContent
                assert result['status'] == 'queued', result
                assert finished(system, result['job_id'])['state'] == 'verified'
        asyncio.run(exercise())


def test_external_service_tool_cannot_be_registered_without_connector_binding(system):
    from evidence_lane_plugin.registry import ActionSpec, Contract
    from evidence_lane_plugin.tool_routes import ToolRoute, validate_routes

    def handler(context, request):
        return {}
    class Empty(Contract):
        pass
    spec = ActionSpec('unbound_external_fixture', 'Invalid unbound external service.', Empty, Empty,
        handler, requires_delta=True, tool_routes=(ToolRoute('invalid', handler, ('Python', 'Pinecone')),))
    with pytest.raises(LaneError) as failure:
        validate_routes(spec)
    assert getattr(failure.value, 'code', None) == 'EXTENSION_BINDING_INVALID'
