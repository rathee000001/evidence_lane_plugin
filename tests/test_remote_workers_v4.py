"""Actual scoped HTTPS Code work through the shared Windows runtime engine."""
import json
import secrets
import socket
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_lane_plugin.engine_runtime import create_runtime_engine, reconcile_jobs
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.remote_api import RemoteGateway, RemotePolicy, issue_remote_grant
from evidence_lane_plugin.remote_transport import RemoteClientConfig, RemoteTransport
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.sdk import EvidenceLaneClient

from tests.test_code_profile_v4 import create_plan
from tests.test_remote_api import listening
from tests.test_remote_api import tls as tls_fixture


@pytest.fixture
def tls(tmp_path):
    return tls_fixture.__wrapped__(tmp_path)


def test_https_code_delta_uses_shared_workers_and_separate_lane_state(tmp_path, tls):
    source = tmp_path / 'source'
    source.mkdir()
    path = source / 'sample.py'
    original = b'def remote_greeting():\n    return "preserved source"\n'
    path.write_bytes(original)
    environment = {'EL_REMOTE_WORKER_TEST': secrets.token_urlsafe(48)}
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        origin = f'https://127.0.0.1:{port.getsockname()[1]}'
    runtime_root = tmp_path / 'runtime'
    with create_runtime_engine(runtime_root, workers=1, capabilities=CapabilityMonitor(device_probe=list)) as seed:
        entry = seed.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = seed.directory.open(entry['project_id'], write=True)
        create_plan((seed, store, None))
        grant = issue_remote_grant(seed, project_id=store.project_id,
            actions=['delta_enter', 'code_index', 'code_query'], credential_env='EL_REMOTE_WORKER_TEST',
            purpose='Isolated actual remote Code operation', expires_at=datetime.now(UTC) + timedelta(hours=1),
            allow_storage_probe=True)
        policy = RemotePolicy(server_id=str(uuid4()), origin=origin,
            storage_class='persistent_operator_declared', volume_id='isolated-test', grants=[grant])
        config = RemoteClientConfig(origin=origin, server_id=policy.server_id, project_id=store.project_id,
            credential_env=grant.credential_env, ca_file=str(tls[0]), probe_file=str(tmp_path / 'probe.json'))
        with listening(RemoteGateway(seed, policy, environment=environment), tls):  # noqa: SIM117 - explicit server and client lifetimes
            with RemoteTransport(config, environment=environment) as transport:
                transport.seed_probe()
    assert seed.workers.status()['shutdown_complete']
    with create_runtime_engine(runtime_root, workers=1, capabilities=CapabilityMonitor(device_probe=list)) as engine:
        reconcile_jobs(engine)
        with listening(RemoteGateway(engine, policy, environment=environment), tls):  # noqa: SIM117 - explicit server and client lifetimes
            with RemoteTransport(config, environment=environment,
                                 hello=ClientHello(configured_profile='codex_vm_ephemeral')) as transport:
                task = PlanStore(store).task('code-0', expected_revision=1)
                client = EvidenceLaneClient(transport)
                response = client.call('delta_enter', project_id=store.project_id, expected_revision=1,
                    arguments={'task_id': 'code-0', 'plan_revision': 1, 'contract_digest': task.contract_digest,
                        'action': 'code_index', 'arguments': {'paths': ['.']}})
                assert response.status == 'queued', response
                deadline = time.monotonic() + 25
                row = None
                while time.monotonic() < deadline:
                    try:
                        with store.lane('plan').connection(read_only=True) as connection:
                            row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (response.job_id,)).fetchone())
                    except LaneError as error:
                        assert error.code == 'PROJECT_RECOVERY_REQUIRED'
                    if row and row['state'] in {'verified', 'blocked'}:
                        break
                    time.sleep(.02)
                assert row and row['state'] == 'verified', row
                result = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
                assert result['files'] == 1
                before = {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
                query = client.call('code_query', project_id=store.project_id,
                    arguments={'snapshot_id': result['snapshot_id'], 'query': 'remote_greeting'})
                assert query.status == 'ok', query
                assert query.result['result']['rows'][0]['path'] == 'sample.py'
                assert before == {p: p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
                assert engine.workers.status()['succeeded_operations'] >= 1
                with store.connection(read_only=True) as root:
                    assert root.execute("SELECT 1 FROM sqlite_schema WHERE name LIKE 'code_%'").fetchone() is None
                assert path.read_bytes() == original
    assert engine.workers.status()['shutdown_complete']


@pytest.mark.parametrize('system', ['Darwin', 'Linux'])
def test_remote_unix_engine_keeps_explicit_reduced_capabilities(tmp_path, monkeypatch, system):
    monkeypatch.setattr('evidence_lane_plugin.engine_runtime.platform.system', lambda: system)
    engine = create_runtime_engine(tmp_path / 'runtime')
    assert engine.workers is None
    assert engine.capabilities.runtimes == {}
    assert not engine.capabilities.installation_status['full_bundle_ready']
