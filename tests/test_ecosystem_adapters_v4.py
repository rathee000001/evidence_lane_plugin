"""Real subordinate SDK tools and MCP composition over the current engine."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.ecosystem_toolchain import (
    build_fastmcp_gateway,
    build_openai_agents_function_tool,
    inspect_ecosystem_adapter_runtime,
    resolve_ecosystem_adapters,
)
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.mcp_adapter import tool_from_action
from evidence_lane_plugin.plan_runtime import TaskDefinition
from evidence_lane_plugin.sdk import EvidenceLaneClient
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from mcp import McpError

ROOT = Path(__file__).resolve().parents[1]


def action(transport, name):
    return next(row for row in transport.catalog() if row['name'] == name)


def invoke(tool, arguments):
    return json.loads(asyncio.run(tool.on_invoke_tool(None, json.dumps(arguments))))


def hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@pytest.fixture
def pair(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'original.txt').write_bytes(b'original fixture bytes')
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'])
        with (LocalTransport(engine.root, connection=ConnectRequest(projects=[
                ProjectSelection(project_id=store.project_id, permissions=['read', 'write'])])) as writer,
              LocalTransport(engine.root, connection=ConnectRequest(projects=[
                ProjectSelection(project_id=store.project_id, permissions=['read'])])) as reader):
            yield engine, store, writer, reader
        assert (source / 'original.txt').read_bytes() == b'original fixture bytes'


def create_arguments(project_id):
    task = TaskDefinition(task_id='adapter-task', title='Adapter fixture',
        requested_outcome='Read fixture bytes.', acceptance_checks=['Verify fixture bytes.'])
    return {'project_id': project_id, 'arguments': {'title': 'Adapter fixture', 'tasks': [task.model_dump(mode='json')]}}


def test_function_tool_reuses_current_schema_and_current_client_authorization(pair):
    _, store, writer, reader = pair
    current = action(reader, 'engine_health')
    original = copy.deepcopy(current)
    tool = build_openai_agents_function_tool(action=current, client=EvidenceLaneClient(reader))
    assert current == original
    assert tool.params_json_schema == tool_from_action(current).inputSchema
    assert tool.strict_json_schema is False and tool.output_json_schema is None
    assert not tool._is_agent_tool and not tool._is_codex_tool
    assert not tool._evidence_lane_contract['construction_authorizes_execution']
    assert invoke(tool, {'arguments': {}})['status'] == 'ok'
    before = hashes(store.root / 'authorities/plan')
    denied = build_openai_agents_function_tool(action=action(reader, 'plan_create'), client=EvidenceLaneClient(reader))
    result = invoke(denied, create_arguments(store.project_id))
    assert result['status'] == 'error' and result['error']['code'] == 'PROJECT_NOT_SELECTED'
    assert hashes(store.root / 'authorities/plan') == before
    allowed = build_openai_agents_function_tool(action=action(writer, 'plan_create'), client=EvidenceLaneClient(writer))
    assert invoke(allowed, create_arguments(store.project_id))['status'] == 'ok'
    before = hashes(store.root / 'authorities/plan')
    read = build_openai_agents_function_tool(action=action(reader, 'plan_read'), client=EvidenceLaneClient(reader))
    assert invoke(read, {'project_id': store.project_id, 'arguments': {}})['status'] == 'ok'
    result = invoke(read, {'project_id': '00000000-0000-0000-0000-000000000001', 'arguments': {}})
    assert result['status'] == 'error' and result['error']['code'] == 'PROJECT_NOT_SELECTED'
    assert hashes(store.root / 'authorities/plan') == before


@pytest.mark.parametrize('raw', [
    '{"arguments":{},"unexpected":true}', '{"arguments":{},"arguments":{}}',
    '{"arguments":{},"expected_revision":NaN}', '{"arguments":{},"expected_revision":true}',
    '{"arguments":{},"project_id":"invalid-uuid"}', '{"arguments":null}', '[]',
])
def test_function_tool_rejects_invalid_envelope_before_any_action_send(pair, raw):
    _, _, _, reader = pair
    changed = ObservedTransport(reader)
    tool = build_openai_agents_function_tool(action=action(reader, 'engine_health'), client=EvidenceLaneClient(changed))
    with pytest.raises(LaneError) as error:
        asyncio.run(tool.on_invoke_tool(None, raw))
    assert error.value.code == 'ECOSYSTEM_INPUT_INVALID'
    assert changed.sends == 0


class ObservedTransport:
    def __init__(self, transport):
        self.transport = transport
        self.change_schema = False
        self.change_result = False
        self.close_after_catalog = False
        self.sends = 0

    def catalog(self):
        rows = copy.deepcopy(self.transport.catalog())
        if self.change_schema:
            next(row for row in rows if row['name'] == 'engine_health')['description'] += ' changed fixture'
        if self.close_after_catalog:
            self.transport.close()
        return rows

    def send(self, request):
        self.sends += 1
        response = self.transport.send(request)
        return response.model_copy(update={'result': {'unexpected': True}}) if self.change_result else response


def test_function_tool_rechecks_catalog_and_validates_owning_result(pair):
    _, _, _, reader = pair
    observed = ObservedTransport(reader)
    current = action(reader, 'engine_health')
    tool = build_openai_agents_function_tool(action=current, client=EvidenceLaneClient(observed))
    observed.change_schema = True
    with pytest.raises(LaneError) as error:
        invoke(tool, {'arguments': {}})
    assert error.value.code == 'ECOSYSTEM_ACTION_SCHEMA_CHANGED' and observed.sends == 0
    observed.change_schema = False
    observed.change_result = True
    with pytest.raises(LaneError) as error:
        invoke(tool, {'arguments': {}})
    assert error.value.code == 'ECOSYSTEM_RESPONSE_INVALID' and observed.sends == 1
    changed = {**current, 'description': 'Caller-invented schema'}
    with pytest.raises(LaneError) as error:
        build_openai_agents_function_tool(action=changed, client=EvidenceLaneClient(reader))
    assert error.value.code == 'ECOSYSTEM_ACTION_SCHEMA_CHANGED'


def test_function_tool_does_not_reconnect_or_retry_disconnected_client(pair):
    _, _, _, reader = pair
    observed = ObservedTransport(reader)
    tool = build_openai_agents_function_tool(action=action(reader, 'engine_health'), client=EvidenceLaneClient(observed))
    reader.close()
    with pytest.raises(LaneError) as error:
        invoke(tool, {'arguments': {}})
    assert error.value.code == 'ECOSYSTEM_CATALOG_UNAVAILABLE'
    assert observed.sends == 0


def test_function_tool_does_not_retry_when_connection_closes_after_catalog(pair):
    _, _, _, reader = pair
    observed = ObservedTransport(reader)
    tool = build_openai_agents_function_tool(action=action(reader, 'engine_health'), client=EvidenceLaneClient(observed))
    observed.close_after_catalog = True
    with pytest.raises(LaneError) as error:
        invoke(tool, {'arguments': {}})
    assert error.value.code == 'ECOSYSTEM_DELIVERY_UNCONFIRMED'
    assert observed.sends == 1 and reader.http.is_closed


def backend_transport(engine, project_id):
    environment = {key: value for key, value in os.environ.items() if key.upper() in {
        'PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
    environment.update(PYTHONPATH=str(ROOT / 'plugins/evidence-lane-plugin/src'),
                       PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1', FASTMCP_CHECK_FOR_UPDATES='off')
    return StdioTransport(command=sys.executable, args=['-B', '-m', 'evidence_lane_plugin.mcp_adapter',
        '--runtime-root', str(engine.root), '--host-profile', 'codex_cli', '--project-id', project_id],
        env=environment, cwd=str(ROOT), keep_alive=False)


def test_fastmcp_preserves_every_native_schema_and_read_only_client_scope(pair):
    engine, store, writer, reader = pair
    assert EvidenceLaneClient(writer).call('plan_create', **create_arguments(store.project_id)).status == 'ok'
    before = hashes(store.root / 'authorities/plan')
    gateway = build_fastmcp_gateway(backend_transport(engine, store.project_id))
    assert gateway._evidence_lane_contract['status'] == 'COMPOSED_NOT_CONTACTED'
    assert gateway._evidence_lane_contract['native_action_schemas_unchanged'] is None
    expected = {row['name']: tool_from_action(row) for row in reader.catalog()}

    async def exercise():
        async with Client(gateway) as proxy:
            actual = {row.name: row for row in await proxy.list_tools()}
            assert actual.keys() == expected.keys()
            for name, row in actual.items():
                assert row.inputSchema == expected[name].inputSchema, name
                assert row.outputSchema == expected[name].outputSchema, name
            result = await proxy.call_tool('plan_read', {'project_id': store.project_id, 'arguments': {}})
            assert result.structured_content['status'] == 'ok'
            denied = await proxy.call_tool('plan_create', create_arguments(store.project_id), raise_on_error=False)
            assert denied.is_error and denied.structured_content['error']['code'] == 'PROJECT_NOT_SELECTED'

    asyncio.run(exercise())
    assert hashes(store.root / 'authorities/plan') == before


def test_connected_fastmcp_client_cannot_be_reused_across_proxy_sessions():
    from fastmcp import FastMCP

    async def exercise():
        async with Client(FastMCP('empty transport fixture')) as client:
            with pytest.raises(LaneError) as error:
                build_fastmcp_gateway(client)
            assert error.value.code == 'ECOSYSTEM_SHARED_CLIENT_UNSUPPORTED'

    asyncio.run(exercise())


def test_fastmcp_does_not_hide_unavailable_backend_as_empty_catalog(pair, tmp_path):
    engine, store, _, _ = pair
    missing = tmp_path / 'missing-engine-runtime'
    backend = backend_transport(engine, store.project_id)
    backend.args[backend.args.index('--runtime-root') + 1] = str(missing)
    gateway = build_fastmcp_gateway(backend)
    assert gateway._evidence_lane_contract['status'] == 'COMPOSED_NOT_CONTACTED'

    async def exercise():
        async with Client(gateway) as proxy:
            with pytest.raises(McpError):
                await proxy.list_tools()

    asyncio.run(exercise())
    assert not missing.exists()


def test_discovery_and_caller_lists_never_claim_runtime_or_permission():
    observed = inspect_ecosystem_adapter_runtime('OpenAI_Agents_SDK')
    assert observed['state'] == 'MODULES_FOUND'
    assert not observed['runtime_activated'] and not observed['service_readiness_verified']
    assert not observed['credential_values_read'] and not observed['network_probe_performed']
    proposal = resolve_ecosystem_adapters(capability='vector_query', action_class='RETRIEVAL',
        lane_id='research', project_id='supplied-project', task_id='supplied-task',
        granted_tools=['Pinecone'], available_tools=['Pinecone'])
    assert proposal['selected_tools'] == ['Pinecone'] and proposal['status'] == 'CANDIDATE_SELECTED'
    assert not proposal['permissions_verified'] and not proposal['runtime_readiness_verified']
    assert not proposal['execution_authorized']
