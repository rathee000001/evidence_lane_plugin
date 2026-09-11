from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import LaneStore
from evidence_lane_plugin.storage_selection import LocalStoragePolicy
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.writers import WriterLease
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.storage_fixtures_v4 import declare_local_storage


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def connect(engine, project, permissions=('read', 'write', 'admin')):
    return engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
        project_id=project.project_id, permissions=list(permissions))]))[1]


@pytest.fixture
def system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_bytes(b'Exact original source\r\n')
    declare_local_storage(tmp_path / 'runtime', tmp_path / 'state')
    with Engine(tmp_path / 'runtime') as engine:
        record = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project = engine.directory.open(record['project_id'], write=True)
        yield engine, project, connect(engine, project)
    assert (source / 'input.txt').read_bytes() == b'Exact original source\r\n'


def call(system, action, arguments=None, *, request_id=None):
    engine, project, client = system
    values = {'action': action, 'project_id': project.project_id, 'arguments': arguments or {}}
    if request_id:
        values['request_id'] = request_id
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(**values), client)


def choose(system, mode, *, head=None, connector_id=None, request_id=None):
    return call(system, 'storage_connector_select', {'mode': mode, 'expected_event_digest': head,
        'connector_id': connector_id, 'reason': 'Explicit isolated storage selection.',
        'confirmation': 'SELECT_STORAGE:' + mode + (':' + connector_id if connector_id else '')}, request_id=request_id)


def boot(system):
    return call(system, 'session_boot', {'reported_session_id': 'fixture-session',
        'expected_root_pv_digest': system[1].pv_head()['head_digest']})


def resume(system, current):
    return call(system, 'session_resume', {'reported_session_id': 'resumed-fixture',
        'expected_root_pv_digest': system[1].pv_head()['head_digest'], 'session_id': current['session_id'],
        'expected_generation': current['generation'], 'expected_event_digest': current['event_digest']})


def test_inspection_is_byte_preserving_and_default_does_not_initialize_ledger(system):
    engine, project, _ = system
    before = hashes(project.root)
    result = call(system, 'storage_connector_inspect')
    assert result.status == 'ok', result
    body = result.result
    assert body['mode'] == 'AUTO' and not body['selection_recorded'] and body['history_count'] == 0
    assert body['backend']['connection'] == 'local_api' and body['policy_satisfied']
    assert not body['backend']['physical_durability_attested'] and not body['backend']['restart_recovery_verified']
    assert call(system, 'storage_status').result['storage_selection']['mode'] == 'AUTO'
    gateway = StudioGateway(engine)
    _, studio = gateway.exchange(gateway.issue_ticket())
    observed = gateway.command('read', {'action': 'storage_connector_inspect', 'project_id': project.project_id,
        'arguments': {}}, studio)
    assert observed['mode'] == 'AUTO'
    assert hashes(project.root) == before
    with project.lane('receipts').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='storage_selection_events'").fetchone()


@pytest.mark.parametrize('storage_class', [None, 'unverified', 'ephemeral'])
def test_unknown_or_ephemeral_local_storage_blocks_boot_without_capture(tmp_path, storage_class):
    runtime, state, source = tmp_path / 'runtime', tmp_path / 'state', tmp_path / 'source'
    source.mkdir()
    if storage_class:
        declare_local_storage(runtime, state, storage_class)
    with Engine(runtime) as engine:
        record = engine.directory.register(state, source_root=source, create=True, read_only=False)
        project = engine.directory.open(record['project_id'], write=True)
        system = engine, project, connect(engine, project)
        before = hashes(project.root)
        assert not call(system, 'storage_connector_inspect').result['policy_satisfied']
        assert hashes(project.root) == before
        assert boot(system).error.code == 'STORAGE_ROUTE_UNAVAILABLE'
        assert not engine.capture.session_bound(system[2].client_id, project.project_id, 'fixture-session')
        assert call(system, 'session_status').result['state'] == 'none'


def test_all_modes_gate_resume_and_replay_does_not_restore_historical_selection(system):
    engine, project, client = system
    first = choose(system, 'LOCAL_SQLITE')
    assert first.status == 'ok', first
    opened = boot(system)
    assert opened.status == 'ok', opened
    assert opened.result['storage_route']['event_digest'] == first.result['event_digest']
    head = first.result['event_digest']
    remote_id = str(uuid4())
    switched = choose(system, 'CONFIGURED_DURABLE_CONNECTOR', head=head, connector_id=remote_id)
    assert switched.status == 'ok' and not switched.result['policy_satisfied']
    assert not switched.result['connection_redirected'] and not switched.result['project_migrated']
    refused = resume(system, opened.result)
    assert refused.error.code == 'STORAGE_ROUTE_UNAVAILABLE'
    assert call(system, 'session_status').result['generation'] == 1
    assert engine.capture.session_bound(client.client_id, project.project_id, 'fixture-session')
    automatic = choose(system, 'AUTO', head=switched.result['event_digest'])
    assert automatic.status == 'ok' and automatic.result['policy_satisfied']
    assert resume(system, opened.result).status == 'ok'
    replay = choose(system, 'LOCAL_SQLITE', request_id=first.request_id)
    assert replay.result == first.result
    assert call(system, 'storage_connector_inspect').result['mode'] == 'AUTO'
    reused = choose(system, 'AUTO', request_id=first.request_id)
    assert reused.error.code == 'STORAGE_REQUEST_ID_REUSED'
    with project.connection(read_only=True) as root:
        assert not root.execute("SELECT 1 FROM sqlite_schema WHERE name='storage_selection_events'").fetchone()
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM storage_selection_events').fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations WHERE owner='storage'").fetchone()[0] == 1


def test_exact_confirmation_head_noop_and_permission_boundaries(system):
    engine, project, _ = system
    refused = call(system, 'storage_connector_select', {'mode': 'AUTO', 'reason': 'Fixture', 'confirmation': 'yes'})
    assert refused.error.code == 'STORAGE_SELECTION_CONFIRMATION_INVALID'
    assert choose(system, 'CONFIGURED_DURABLE_CONNECTOR', connector_id='google-drive').error.code == 'GOOGLE_DRIVE_PRIMARY_RUNTIME_FORBIDDEN'
    first = choose(system, 'LOCAL_SQLITE')
    assert first.status == 'ok', first
    assert choose(system, 'AUTO').error.code == 'STORAGE_SELECTION_HEAD_CHANGED'
    repeated = choose(system, 'LOCAL_SQLITE', head=first.result['event_digest'])
    assert repeated.result == first.result
    reader = connect(engine, project, permissions=('read',))
    assert choose((engine, project, reader), 'AUTO', head=first.result['event_digest']).error.code == 'PROJECT_NOT_SELECTED'
    assert call(system, 'storage_connector_inspect').result['history_count'] == 1


def test_failed_selection_rolls_back_owning_schema_and_events(system, monkeypatch):
    _, project, _ = system
    before = hashes(project.root / 'authorities' / 'plan')
    original = LaneStore.append_receipt
    injected = []
    def failure(self, kind, *args, **kwargs):
        result = original(self, kind, *args, **kwargs)
        if kind == 'storage_selection':
            injected.append(self.lane_id)
            raise LaneError('INJECTED_STORAGE_FAILURE', 'Injected failure after the storage receipt.')
        return result
    monkeypatch.setattr(LaneStore, 'append_receipt', failure)
    response = choose(system, 'LOCAL_SQLITE')
    assert response.error.code == 'INJECTED_STORAGE_FAILURE'
    assert injected == ['receipts']
    assert hashes(project.root / 'authorities' / 'plan') == before
    with project.lane('receipts').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='storage_selection_events'").fetchone()
        assert not connection.execute("SELECT 1 FROM schema_history_files WHERE owner='storage'").fetchone()
    assert not call(system, 'storage_connector_inspect').result['selection_recorded']


def test_published_corrupt_selection_chain_is_rejected_without_repair(system):
    engine, project, _ = system
    assert choose(system, 'LOCAL_SQLITE').status == 'ok'
    with WriterLease(project, engine.instance_id) as lease, lease.transaction('receipts') as connection:
        connection.execute("UPDATE storage_selection_events SET body_json=replace(body_json, 'Explicit', 'Changed')")
    before = hashes(project.root)
    assert call(system, 'storage_connector_inspect').error.code == 'STORAGE_HISTORY_INTEGRITY'
    assert hashes(project.root) == before


def test_local_policy_is_bound_to_root_and_only_reloaded_by_a_new_engine(tmp_path):
    runtime, state, source = tmp_path / 'runtime', tmp_path / 'state', tmp_path / 'source'
    source.mkdir()
    declare_local_storage(runtime, state)
    with Engine(runtime) as engine:
        record = engine.directory.register(state, source_root=source, create=True, read_only=False)
        project = engine.directory.open(record['project_id'], write=True)
        system = engine, project, connect(engine, project)
        selected = choose(system, 'LOCAL_SQLITE').result
        opened = boot(system)
        assert opened.status == 'ok', opened
        declared_digest = opened.result['storage_route']['backend']['policy_digest']
        declare_local_storage(runtime, state, 'ephemeral')
        assert call(system, 'storage_connector_inspect').result['policy_satisfied']
    with Engine(runtime) as engine:
        project = engine.directory.open(record['project_id'], write=True)
        system = engine, project, connect(engine, project)
        current = call(system, 'storage_connector_inspect').result
        assert current['event_digest'] == selected['event_digest'] and current['backend']['storage_class'] == 'ephemeral'
        assert current['backend']['policy_digest'] != declared_digest and not current['policy_satisfied']
        assert resume(system, opened.result).error.code == 'STORAGE_ROUTE_UNAVAILABLE'
    declare_local_storage(runtime, state)
    with Engine(runtime) as engine:
        project = engine.directory.open(record['project_id'], write=True)
        system = engine, project, connect(engine, project)
        assert resume(system, opened.result).status == 'ok'


def test_local_policy_requires_disjoint_explicit_roots_and_bounded_file(tmp_path):
    root = str(tmp_path / 'state')
    with pytest.raises(ValueError):
        LocalStoragePolicy.model_validate({'declarations': [
            {'state_root': root, 'volume_id': 'a', 'storage_class': 'persistent_operator_declared'},
            {'state_root': str(tmp_path / 'state' / 'nested'), 'volume_id': 'b', 'storage_class': 'ephemeral'}]})
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    policy = runtime / 'storage-policy.json'
    policy.write_text(json.dumps({'ready': True}))
    with pytest.raises(LaneError, match='owner storage policy is invalid'):
        Engine(runtime)
    policy.write_bytes(b' ' * 65537)
    with pytest.raises(LaneError, match='file budget'):
        Engine(runtime)


def test_observer_binding_and_history_budget_do_not_create_false_availability(system, monkeypatch):
    engine, project, client = system
    context = engine.clients.context(client, project.project_id, 'read')
    before = hashes(project.root)
    unavailable = engine.registry.execute('storage_connector_inspect', {}, replace(context, storage_observer=None))
    assert not unavailable['policy_satisfied'] and unavailable['backend']['connection'] == 'unavailable'
    original = context.storage_observer
    wrong = replace(context, storage_observer=lambda project_id: original(project_id).model_copy(update={'project_id': str(uuid4())}))
    with pytest.raises(LaneError) as error:
        engine.registry.execute('storage_connector_inspect', {}, wrong)
    assert error.value.code == 'STORAGE_BACKEND_BINDING_CHANGED'
    assert hashes(project.root) == before
    monkeypatch.setattr('evidence_lane_plugin.storage_selection.STORAGE_HISTORY_MAX_BYTES', 1)
    assert choose(system, 'LOCAL_SQLITE').error.code == 'STORAGE_HISTORY_BUDGET'
    assert call(system, 'storage_connector_inspect').result['history_count'] == 0


def test_other_root_does_not_inherit_the_local_declaration(system, tmp_path):
    engine, _, _ = system
    source = tmp_path / 'other-source'
    source.mkdir()
    row = engine.directory.register(tmp_path / 'other-state', source_root=source, create=True, read_only=False)
    project = engine.directory.open(row['project_id'], write=True)
    other = engine, project, connect(engine, project)
    observed = call(other, 'storage_connector_inspect')
    assert observed.status == 'ok' and not observed.result['policy_satisfied']
    assert observed.result['backend']['storage_class'] == 'unverified'
    assert boot(other).error.code == 'STORAGE_ROUTE_UNAVAILABLE'


def test_actual_stdio_selection_and_studio_mutation_denial(system):
    engine, project, _ = system
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--project-id', project.project_id, '--permission', 'read', '--permission', 'admin'],
                env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert tools['storage_connector_inspect'].annotations.readOnlyHint
                assert not tools['storage_connector_select'].annotations.readOnlyHint
                result = await session.call_tool('storage_connector_select', {'project_id': project.project_id,
                    'arguments': {'mode': 'LOCAL_SQLITE', 'reason': 'Explicit native protocol fixture.',
                        'confirmation': 'SELECT_STORAGE:LOCAL_SQLITE'}})
                assert not result.isError and result.structuredContent['status'] == 'ok', result
                assert result.structuredContent['result']['policy_satisfied']
                assert json.loads(result.content[0].text) == result.structuredContent
                return result.structuredContent['result']['event_digest']
        selected_head = asyncio.run(exercise())
    assert call(system, 'storage_connector_inspect').result['event_digest'] == selected_head
    gateway = StudioGateway(engine)
    _, studio = gateway.exchange(gateway.issue_ticket())
    before = hashes(project.root)
    with pytest.raises(LaneError) as error:
        gateway.command('read', {'project_id': project.project_id, 'action': 'storage_connector_select',
            'arguments': {'mode': 'AUTO', 'reason': 'Blocked human mutation', 'confirmation': 'SELECT_STORAGE:AUTO'}}, studio)
    assert error.value.code == 'NOT_A_STUDIO_QUERY' and hashes(project.root) == before
