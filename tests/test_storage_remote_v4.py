"""Storage selection through real HTTPS, scoped clients and owned process restart."""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.projects import atomic_json
from evidence_lane_plugin.remote_api import RemoteGateway, RemotePolicy, issue_remote_grant
from evidence_lane_plugin.remote_transport import RemoteClientConfig, RemoteTransport
from evidence_lane_plugin.sdk import ActionRequest, EvidenceLaneClient

from tests.test_remote_api import listening
from tests.test_remote_api import tls as tls_fixture
from tests.test_remote_sessions_v4 import boot, resume


@pytest.fixture
def tls(tmp_path):
    return tls_fixture.__wrapped__(tmp_path)


@pytest.fixture
def prepared(tmp_path, tls):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_bytes(b'Original remote storage fixture\r\n')
    runtime = tmp_path / 'runtime'
    environment = {'EL_STORAGE_REMOTE_TEST': secrets.token_urlsafe(48)}
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        origin = f'https://127.0.0.1:{port.getsockname()[1]}'
    with Engine(runtime) as engine:
        project = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        grant = issue_remote_grant(engine, project_id=project['project_id'], actions=[
            'storage_connector_inspect', 'storage_connector_select', 'storage_status',
            'session_status', 'session_boot', 'session_resume', 'session_exit', 'capture_bind'],
            credential_env='EL_STORAGE_REMOTE_TEST', purpose='Isolated storage selection qualification',
            expires_at=datetime.now(UTC) + timedelta(hours=1), allow_storage_probe=True)
        policy = RemotePolicy(server_id=str(uuid4()), origin=origin,
            storage_class='persistent_operator_declared', volume_id='isolated-remote-volume', grants=[grant])
        config = RemoteClientConfig(origin=origin, server_id=policy.server_id, project_id=project['project_id'],
            credential_env=grant.credential_env, ca_file=str(tls[0]), probe_file=str(tmp_path / 'probe.json'),
            hook_binding_directory=str(tmp_path / 'hook-bindings'))
        with listening(RemoteGateway(engine, policy, environment=environment), tls), RemoteTransport(config, environment=environment) as transport:
            transport.seed_probe()
            assert transport.route()['route'] is None
            with pytest.raises(LaneError, match='restart'):
                transport.catalog()
    yield runtime, policy, config, environment
    assert (source / 'input.txt').read_bytes() == b'Original remote storage fixture\r\n'


def call(transport, action, arguments=None):
    return EvidenceLaneClient(transport).call(action, project_id=transport.config.project_id, arguments=arguments)


def choose(transport, mode, *, head=None, connector_id=None):
    return call(transport, 'storage_connector_select', {'mode': mode, 'expected_event_digest': head,
        'connector_id': connector_id, 'reason': 'Use this explicitly configured remote runtime.',
        'confirmation': 'SELECT_STORAGE:' + mode + (':' + connector_id if connector_id else '')})


def test_configured_remote_selection_binds_server_and_controls_resume(prepared, tls):
    runtime, policy, config, environment = prepared
    with Engine(runtime) as engine, listening(RemoteGateway(engine, policy, environment=environment), tls):  # noqa: SIM117
        with RemoteTransport(config, environment=environment, hello=ClientHello(configured_profile='codex_vm_ephemeral')) as transport:
            current = call(transport, 'storage_connector_inspect')
            assert current.status == 'ok', current
            assert current.result['backend']['connection'] == 'remote_api' and current.result['policy_satisfied']
            assert current.result['backend']['restart_recovery_verified']
            assert not current.result['backend']['physical_durability_attested']
            selected = choose(transport, 'CONFIGURED_DURABLE_CONNECTOR', connector_id=policy.server_id)
            assert selected.status == 'ok' and selected.result['policy_satisfied']
            opened = boot(transport)
            assert opened.status == 'ok', opened
            assert opened.result['storage_route']['event_digest'] == selected.result['event_digest']
            wrong = choose(transport, 'CONFIGURED_DURABLE_CONNECTOR', head=selected.result['event_digest'], connector_id=str(uuid4()))
            assert wrong.status == 'ok' and not wrong.result['policy_satisfied']
            assert resume(transport, opened.result).error.code == 'STORAGE_ROUTE_UNAVAILABLE'
            local = choose(transport, 'LOCAL_SQLITE', head=wrong.result['event_digest'])
            assert local.status == 'ok' and not local.result['policy_satisfied']
            assert resume(transport, opened.result).error.code == 'STORAGE_ROUTE_UNAVAILABLE'
            automatic = choose(transport, 'AUTO', head=local.result['event_digest'])
            assert automatic.status == 'ok' and automatic.result['policy_satisfied']
            assert resume(transport, opened.result).status == 'ok'


def test_storage_observer_rechecks_expiry_even_on_internal_dispatch(prepared, tls):
    runtime, policy, config, environment = prepared
    instant = [datetime.now(UTC)]
    with Engine(runtime) as engine:
        gateway = RemoteGateway(engine, policy, environment=environment, clock=lambda: instant[0])
        with listening(gateway, tls), RemoteTransport(config, environment=environment) as transport:
            assert call(transport, 'storage_connector_inspect').status == 'ok'
            client = engine.clients.session(transport.connection['client_id'])
            instant[0] += timedelta(seconds=301)
            response = PublicActionSDKDispatcher(engine).execute(ActionRequest(action='storage_connector_inspect',
                project_id=config.project_id), client)
            assert response.error.code == 'REMOTE_DURABILITY_UNVERIFIED'
            transport.verify_storage(transport._ticket)
            assert call(transport, 'storage_connector_inspect').result['backend']['restart_recovery_verified']


def test_selection_and_session_resume_survive_abrupt_server_process_restart(prepared, tls, tmp_path):
    runtime, policy, config, environment = prepared
    policy_file = tmp_path / 'policy.json'
    atomic_json(policy_file, policy.model_dump())
    source = Path(__file__).parents[1] / 'plugins/evidence-lane-plugin/src'

    @contextmanager
    def process():
        child = subprocess.Popen([sys.executable, '-m', 'evidence_lane_plugin.remote_server',
            '--runtime-root', str(runtime), '--policy', str(policy_file),
            '--tls-certificate', str(tls[0]), '--tls-private-key', str(tls[1])],
            env={**os.environ, 'PYTHONPATH': str(source), **environment}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert child.poll() is None
                try:
                    client = RemoteTransport(config, environment=environment, timeout=1)
                    client.close()
                    break
                except LaneError:
                    time.sleep(0.02)
            else:
                pytest.fail('The owned remote test server did not start.')
            yield child
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)

    with process() as child, RemoteTransport(config, environment=environment) as first:
        selected = choose(first, 'CONFIGURED_DURABLE_CONNECTOR', connector_id=policy.server_id)
        assert selected.status == 'ok', selected
        opened = boot(first)
        assert opened.status == 'ok', opened
        engine_id = first.instance_id
        child.kill()
        child.wait(timeout=10)
    with process(), RemoteTransport(config, environment=environment) as second:
        assert second.instance_id != engine_id
        selection = call(second, 'storage_connector_inspect')
        assert selection.result['event_digest'] == selected.result['event_digest']
        assert selection.result['backend']['engine_instance_id'] == second.instance_id
        resumed = resume(second, opened.result)
        assert resumed.status == 'ok', resumed
        assert resumed.result['generation'] == 2
        assert resumed.result['storage_route']['backend']['restart_recovery_verified']
