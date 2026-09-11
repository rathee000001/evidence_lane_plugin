"""Real TLS session ownership, parent revocation and remote hook delivery."""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.capture_routing import HookEnvelope
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.projects import ProjectAccess, atomic_json
from evidence_lane_plugin.remote_api import RemoteGateway, RemotePolicy, issue_remote_grant
from evidence_lane_plugin.remote_transport import (
    RemoteClientConfig,
    RemoteTransport,
    submit_remote_hook,
)
from evidence_lane_plugin.sdk import ActionRequest, EvidenceLaneClient
from evidence_lane_plugin.writers import WriterLease
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.test_remote_api import listening
from tests.test_remote_api import tls as tls_fixture


@pytest.fixture
def tls(tmp_path):
    return tls_fixture.__wrapped__(tmp_path)


@pytest.fixture
def prepared(tmp_path, tls):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_text('Preserved remote source bytes.')
    runtime = tmp_path / 'runtime'
    environment = {'EL_REMOTE_SESSION_TEST': secrets.token_urlsafe(48)}
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        origin = f'https://127.0.0.1:{port.getsockname()[1]}'
    with Engine(runtime) as engine:
        project = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        grant = issue_remote_grant(engine, project_id=project['project_id'], actions=[
            'session_context', 'session_status', 'session_boot', 'session_resume', 'session_exit',
            'capture_bind', 'lineage_read', 'source_register'], credential_env='EL_REMOTE_SESSION_TEST',
            purpose='Isolated remote session qualification', expires_at=datetime.now(UTC) + timedelta(hours=1),
            allow_storage_probe=True)
        policy = RemotePolicy(server_id=str(uuid4()), origin=origin,
            storage_class='persistent_operator_declared', volume_id='isolated-test', grants=[grant])
        config = RemoteClientConfig(origin=origin, server_id=policy.server_id, project_id=project['project_id'],
            credential_env=grant.credential_env, ca_file=str(tls[0]), probe_file=str(tmp_path / 'probe.json'),
            hook_binding_directory=str(tmp_path / 'hook-bindings'))
        gateway = RemoteGateway(engine, policy, environment=environment)
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as transport:
            transport.seed_probe()
            # The server enforces this too; skipping the client route check
            # cannot open a connection before restart verification.
            with pytest.raises(LaneError) as error:
                gateway.handle('connect', json.dumps({'instance_id': engine.instance_id,
                    'policy_digest': gateway.policy_digest(grant), 'hello': {}}).encode(), grant)
            assert error.value.code == 'REMOTE_DURABILITY_UNVERIFIED'
            assert not engine.clients.status()
    return runtime, policy, config, environment


def call(transport, action, arguments=None):
    return EvidenceLaneClient(transport).call(action, project_id=transport.config.project_id, arguments=arguments)


def resume(transport, head):
    current = call(transport, 'session_status').result
    return call(transport, 'session_resume', {'reported_session_id': 'remote-successor',
        'session_id': head['session_id'], 'expected_generation': head['generation'],
        'expected_event_digest': head['event_digest'],
        'expected_root_pv_digest': current['root_pv']['head_digest']})


def boot(transport):
    current = call(transport, 'session_status')
    return call(transport, 'session_boot', {'reported_session_id': 'remote-host-session',
        'expected_root_pv_digest': current.result['root_pv']['head_digest']})


def test_same_grant_connections_cannot_take_over_or_capture_each_other(prepared, tls):
    runtime, policy, config, environment = prepared
    with Engine(runtime) as engine, listening(RemoteGateway(engine, policy, environment=environment), tls):  # noqa: SIM117 - keep the server and client scopes explicit
        with (RemoteTransport(config, environment=environment,
                hello=ClientHello(configured_profile='codex_vm_ephemeral')) as first,
              RemoteTransport(config, environment=environment,
                hello=ClientHello(configured_profile='codex_vm_persistent')) as second):
            first.catalog()
            second.catalog()
            assert first.connection['client_id'] != second.connection['client_id'] != policy.grants[0].principal_id
            diagnostic = call(first, 'session_context')
            assert diagnostic.status == 'ok', diagnostic
            assert diagnostic.result['flash']['state'] == 'verified'
            assert diagnostic.result['native_task_attestation'] == 'not_provided'
            started = boot(first)
            assert started.status == 'ok', started
            assert started.result['owner_client_id'] == first.connection['client_id']
            assert resume(second, started.result).error.code == 'SESSION_OWNER_ACTIVE'
            event = HookEnvelope(event_id=str(uuid4()), event={'hook_event_name': 'UserPromptSubmit',
                'session_id': 'remote-host-session', 'prompt': 'Remote visible prompt.'})
            with pytest.raises(LaneError):
                second.capture(event)
            captured = submit_remote_hook(config, event, environment=environment)
            assert captured['captured']
            assert captured['result']['provenance'] == 'authenticated_remote_hook_report'
            assert len(engine.clients.status()) == 2  # Hook did not create or disconnect a client.
            local_use = PublicActionSDKDispatcher(engine).execute(ActionRequest(action='project_recipe',
                project_id=config.project_id, arguments={}), engine.clients.authenticate(first.connection['connection_token']))
            assert local_use.error.code == 'ACTION_SCOPE_DENIED'
            first.close()
            resumed = resume(second, started.result)
            assert resumed.status == 'ok', resumed
            assert resumed.result['owner_client_id'] == second.connection['client_id']
            assert not resumed.result['jobs_replayed']
            assert submit_remote_hook(config, event, environment=environment) == {
                'captured': False, 'reason': 'project_session_not_bound'}
            head = resumed.result
            closed = call(second, 'session_exit', {'session_id': head['session_id'],
                'expected_generation': head['generation'], 'expected_event_digest': head['event_digest'],
                'reason': 'Verified remote closeout.'})
            assert closed.status == 'ok' and closed.result['exit_reason'] == 'Verified remote closeout.'
            assert not closed.result['capture_bound']


def test_parent_revocation_invalidates_child_grant_and_bound_capture(prepared, tls):
    runtime, policy, config, environment = prepared
    with Engine(runtime) as engine, listening(RemoteGateway(engine, policy, environment=environment), tls):  # noqa: SIM117 - keep the server and client scopes explicit
        with RemoteTransport(config, environment=environment) as client:
            started = boot(client)
            assert started.status == 'ok', started
            project = engine.directory.open(config.project_id, write=True)
            # A real scoped source read uses the child grant's inherited path,
            # while every receipt continues to identify this connection.
            registered = call(client, 'source_register', {'sources': [str(project.source_root / 'input.txt')]})
            assert registered.status == 'ok', registered
            child = client.connection['client_id']
            with project.lane('receipts').connection(read_only=True) as connection:
                derived = connection.execute('SELECT * FROM access_grants WHERE principal_id=?', (child,)).fetchone()
                assert derived['parent_grant_id'] == policy.grants[0].access_grant_id
                dump = '\n'.join(connection.iterdump())
                assert client.connection['connection_token'] not in dump
                assert environment[config.credential_env] not in dump
            with WriterLease(project, engine.instance_id) as lease:
                ProjectAccess(project).revoke(policy.grants[0].access_grant_id, writer=lease)
            with pytest.raises(LaneError):
                ProjectAccess(project).authorize(child, 'read', path=project.source_root / 'input.txt')
            with pytest.raises(LaneError):
                call(client, 'session_status')
            with pytest.raises(LaneError):
                engine.clients.session(child)


def test_server_rechecks_durability_on_existing_connection(prepared, tls):
    runtime, policy, config, environment = prepared
    instant = [datetime.now(UTC)]
    with Engine(runtime) as engine:
        gateway = RemoteGateway(engine, policy, environment=environment, clock=lambda: instant[0])
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as client:
            assert boot(client).status == 'ok'
            instant[0] += timedelta(seconds=301)
            request = ActionRequest(action='session_status', project_id=config.project_id)
            with pytest.raises(LaneError) as error:
                gateway.handle('action', json.dumps({**client._connection_envelope(), 'request': request.model_dump()}).encode(), policy.grants[0])
            assert error.value.code == 'REMOTE_DURABILITY_UNVERIFIED'
            client.verify_storage(client._ticket)
            assert client.send(request).status == 'ok'


def test_packaged_manifest_selects_remote_without_local_administration(prepared, tls, tmp_path):
    runtime, policy, config, environment = prepared
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    manifest = json.loads((plugin / '.mcp.json').read_bytes())['mcpServers']['evidence-lane']
    assert './scripts/run_mcp.py' in manifest['args']
    assert '--local-project-administration' in manifest['args']
    selected = tmp_path / 'remote-client.json'
    atomic_json(selected, config.model_dump())
    unused_studio = tmp_path / 'must-not-create-studio'

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-B', '-m', 'evidence_lane_plugin.mcp_adapter',
                  '--remote-config', str(selected), '--host-profile', 'codex_desktop'],
            env={**environment, 'EVIDENCE_LANE_REMOTE_CONFIG': str(selected),
                 'EVIDENCE_LANE_STUDIO_ROOT': str(unused_studio),
                 'PYTHONPATH': str(plugin / 'src')})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert 'session_boot' in names and not names.intersection({'project_register', 'project_select', 'engine_health'})
            status = await session.call_tool('session_status', {'project_id': config.project_id, 'arguments': {}})
            assert not status.isError
            root = status.structuredContent['result']['root_pv']['head_digest']
            result = await session.call_tool('session_boot', {'project_id': config.project_id,
                'arguments': {'reported_session_id': 'remote-native-protocol', 'expected_root_pv_digest': root}})
            assert not result.isError, result
            assert result.structuredContent['result']['native_task_attestation'] == 'not_provided'

    with Engine(runtime) as engine, listening(RemoteGateway(engine, policy, environment=environment), tls):
        asyncio.run(exercise())
        deadline = time.monotonic() + 2
        while engine.clients.status() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not engine.clients.status()
        assert not unused_studio.exists()


def test_read_only_client_can_verify_owner_prepared_probe_without_write_grant(prepared, tls, tmp_path):
    runtime, original_policy, original_config, environment = prepared
    environment = {**environment, 'EL_READ_ONLY_SESSION_TEST': secrets.token_urlsafe(48)}
    with Engine(runtime) as engine:
        grant = issue_remote_grant(engine, project_id=original_config.project_id,
            actions=['session_status', 'session_context'], credential_env='EL_READ_ONLY_SESSION_TEST',
            purpose='Read-only remote observer', expires_at=datetime.now(UTC) + timedelta(hours=1))
        policy = original_policy.model_copy(update={'grants': [grant]})
        config = original_config.model_copy(update={'credential_env': grant.credential_env,
            'probe_file': str(tmp_path / 'read-probe.json'), 'hook_binding_directory': None})
        gateway = RemoteGateway(engine, policy, environment=environment)
        ticket = gateway.prepare_probe(grant.principal_id)
        atomic_json(Path(config.probe_file), ticket.model_dump())
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as client:
            assert client.route()['route'] is None
            with pytest.raises(LaneError):
                client.seed_probe()
    with (Engine(runtime) as engine,
          listening(RemoteGateway(engine, policy, environment=environment), tls),
          RemoteTransport(config, environment=environment) as client):
        assert client.route()['route'] == 'remote_api'
        assert call(client, 'session_context').status == 'ok'
        assert call(client, 'session_status').result['state'] == 'none'
        project = engine.directory.open(config.project_id)
        with project.lane('receipts').connection(read_only=True) as connection:
            grants = connection.execute('SELECT permissions_json FROM access_grants WHERE revoked_at IS NULL').fetchall()
            selected = connection.execute('SELECT permissions_json FROM access_grants WHERE principal_id IN (?,?)',
                (grant.principal_id, client.connection['client_id'])).fetchall()
        assert grants and len(selected) == 2 and all(json.loads(row[0]) == ['read'] for row in selected)
        with pytest.raises(LaneError):
            ProjectAccess(project).authorize(client.connection['client_id'], 'write')


@pytest.mark.parametrize('event_name', ['SessionEnd', 'Interrupt'])
def test_remote_terminal_hook_process_uses_existing_connection_within_deadline(prepared, tls, tmp_path, event_name):
    runtime, policy, config, environment = prepared
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    selected = tmp_path / 'hook-remote-config.json'
    atomic_json(selected, config.model_dump())
    with (Engine(runtime) as engine,
          listening(RemoteGateway(engine, policy, environment=environment), tls),
          RemoteTransport(config, environment=environment) as client):
        assert boot(client).status == 'ok'
        payload = {'hook_event_name': event_name, 'session_id': 'remote-host-session', 'reason': 'other'}
        completed = subprocess.run([sys.executable, str(plugin / 'hooks/invoke_hook.py'), '--event', event_name],
            input=json.dumps(payload), text=True, capture_output=True, timeout=3, check=False,
            env={**os.environ, **environment, 'EVIDENCE_LANE_REMOTE_CONFIG': str(selected)})
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert json.loads(completed.stdout) == {}
        assert len(engine.clients.status()) == 1
        lineage = call(client, 'lineage_read')
        assert lineage.status == 'ok' and lineage.result['total_events'] == 1
        assert lineage.result['events'][0]['provenance'] == 'authenticated_remote_hook_report'
