import hashlib
import os
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.graph_pipeline import SemanticGraph
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.sdk import ActionRequest

from tests import test_lane_artifacts_v4
from tests.test_lane_artifacts_v4 import prepare, publish

views = test_lane_artifacts_v4.views


@pytest.fixture(autouse=True)
def graph_runtime(monkeypatch):
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    monkeypatch.setenv('EVIDENCE_LANE_HOST_PROFILE', 'UNSUPPORTED')


def connect(engine, store, profile):
    _, session = engine.clients.connect(ConnectRequest(
        hello=ClientHello(configured_profile=profile),
        projects=[ProjectSelection(project_id=store.project_id, permissions=['read', 'write'])]))

    def call(action, arguments=None):
        return PublicActionSDKDispatcher(engine).execute(ActionRequest(
            action=action, project_id=store.project_id, arguments=arguments or {}), session)
    return call, session


def test_static_projection_is_independent_of_native_configuration_and_optional_analysis(monkeypatch):
    import evidence_lane_plugin.graph_pipeline as pipeline

    def forbidden(*args, **kwargs):
        raise AssertionError('Static projection tried a runtime observation')

    graph = SemanticGraph('static')
    graph.add_node('first', 'First')
    graph.add_node('second', 'Second')
    graph.add_edge('first', 'second', 'PRECEDES')
    before = graph.render_pair()
    monkeypatch.setattr(pipeline, 'configured_runtime_root', forbidden)
    monkeypatch.setattr(pipeline, 'try_resolve_native_tool', forbidden)
    monkeypatch.setattr(pipeline, '_rustworkx_analysis', forbidden)
    monkeypatch.setenv('EVIDENCE_LANE_HOST_PROFILE', 'CODEX_DESKTOP')
    assert graph.render_pair() == before
    assert before[2]['graph_analysis']['status'] == 'NOT_REQUESTED'
    assert before[2]['native_graphviz_validation'] is None


@pytest.mark.parametrize(('profile', 'family'), [
    ('codex_desktop_stable', 'CODEX_DESKTOP'), ('codex_desktop_beta', 'CODEX_DESKTOP'),
    ('codex_cli', 'CODEX_CLI')])
def test_native_dot_worker_binds_current_client_and_exact_bytes(views, monkeypatch, profile, family):
    native_root = Path(os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(native_root))
    # This deliberately conflicting variable must not override the client.
    monkeypatch.setenv('EVIDENCE_LANE_HOST_PROFILE', 'UNSUPPORTED')
    engine, store, _ = views
    call, session = connect(engine, store, profile)
    _, request = prepare(call, formats=['mmd', 'dot'], dot_validation='native', include_pointer=True)
    published = publish(call, request)
    read = call('lane_view_read', {'view_id': request.view_id, 'include_content': True})
    assert read.status == 'ok', read.error
    manifest = read.result['manifest']
    native = manifest['tool_evidence']['dot']['native_graphviz_validation']
    assert native['status'] == 'PASS' and native['returncode'] == 0
    assert native['version'] == '15.1.1' and native['host_profile'] == family
    assert native['input_sha256'] == hashlib.sha256(read.result['contents']['dot'].encode()).hexdigest()
    assert native['host_profile_basis'] == 'configured_not_attested' and not native['shell_used']
    from evidence_lane_plugin.hashing import canonical_json_bytes
    assert hashlib.sha256(canonical_json_bytes({key: value for key, value in native.items()
        if key != 'receipt_sha256'})).hexdigest() == native['receipt_sha256']
    assert manifest['worker_execution']['worker_pid'] != os.getpid()
    assert manifest['worker_execution']['host_observation_id'] == session.host_observation.observation_id
    assert manifest['worker_execution']['native_task_attestation'] == 'not_provided'
    assert manifest['dot_validation'] == 'native'
    assert manifest['tool_evidence']['mmd']['native_graphviz_validation'] is None
    assert manifest['tool_evidence']['mmd']['semantic_topology_sha256'] == manifest['tool_evidence']['dot']['semantic_topology_sha256']
    assert manifest['tool_evidence']['dot']['graph_analysis']['weak_component_count'] == 1
    assert manifest['tool_evidence']['dot']['graph_analysis']['is_directed_acyclic']
    assert all(Path(row['path']).is_relative_to(store.root / 'authorities/plan') for row in published['files'])
    assert not any((store.root / lane / 'plan').exists() for lane in ('sectors',))


def test_native_request_cannot_use_unknown_client_or_unavailable_binary(views, monkeypatch, tmp_path):
    engine, store, unknown = views
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    call, _ = connect(engine, store, 'codex_cli')
    _, request = prepare(unknown, formats=['dot'], dot_validation='native')
    before = {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    failed = unknown('lane_view_refresh', request.model_dump(mode='json'))
    assert failed.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    native_attempt = failed.error.details['attempts'][-1]
    assert native_attempt['reason'] == 'HOST_PROFILE_UNSUPPORTED' and not native_attempt['adapter_invoked']
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(tmp_path / 'absent-native-installation'))
    failed = call('lane_view_refresh', request.model_dump(mode='json'))
    assert failed.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    native_attempt = failed.error.details['attempts'][-1]
    assert any(row['tool_id'] == 'Graphviz_dot' and not row['ready'] for row in native_attempt['observations'])
    assert {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('profile', ['codex_vm_persistent', 'codex_vm_ephemeral'])
def test_vm_profile_cannot_bypass_durable_remote_admission(views, profile):
    from evidence_lane_plugin.errors import LaneError
    engine, store, _ = views
    before = {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    with pytest.raises(LaneError) as error:
        connect(engine, store, profile)
    assert error.value.code == 'REMOTE_DURABILITY_UNVERIFIED'
    assert {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('profile', ['codex_vm_persistent', 'codex_vm_ephemeral'])
def test_vm_native_dot_uses_restarted_verified_https_engine(tmp_path, profile):
    import secrets
    import socket
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    from evidence_lane_plugin.engine_runtime import create_runtime_engine
    from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
    from evidence_lane_plugin.remote_api import RemoteGateway, RemotePolicy, issue_remote_grant
    from evidence_lane_plugin.remote_transport import RemoteClientConfig, RemoteTransport
    from evidence_lane_plugin.runtime_health import CapabilityMonitor
    from evidence_lane_plugin.sdk import EvidenceLaneClient

    from tests.test_remote_api import listening, tls

    certificates = tls.__wrapped__(tmp_path)
    source = tmp_path / 'source'
    source.mkdir()
    environment = {'EVI_GRAPH_REMOTE_TEST': secrets.token_urlsafe(48)}
    with socket.socket() as port:
        port.bind(('127.0.0.1', 0))
        origin = f'https://127.0.0.1:{port.getsockname()[1]}'
    with create_runtime_engine(tmp_path / 'runtime', workers=1, capabilities=CapabilityMonitor()) as seed:
        entry = seed.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = seed.directory.open(entry['project_id'], write=True)
        with seed.project_work.mutation(store) as lease:
            PlanStore(store).create(PlanCreate(title='Remote graph fixture', tasks=[TaskDefinition(
                task_id='graph-fixture', title='Read graph', requested_outcome='Keep graph source attribution')]), lease, actor_id='fixture')
        grant = issue_remote_grant(seed, project_id=store.project_id,
            actions=['lane_view_preview', 'lane_view_refresh', 'lane_view_read'], credential_env='EVI_GRAPH_REMOTE_TEST',
            purpose='Isolated native graph HTTPS qualification', expires_at=datetime.now(UTC) + timedelta(hours=1),
            allow_storage_probe=True)
        policy = RemotePolicy(server_id=str(uuid4()), origin=origin,
            storage_class='persistent_operator_declared', volume_id='isolated-graph-test', grants=[grant])
        config = RemoteClientConfig(origin=origin, server_id=policy.server_id, project_id=store.project_id,
            credential_env=grant.credential_env, ca_file=str(certificates[0]), probe_file=str(tmp_path / 'probe.json'))
        with (listening(RemoteGateway(seed, policy, environment=environment), certificates),
              RemoteTransport(config, environment=environment) as transport):
            transport.seed_probe()
    assert seed.workers.status()['shutdown_complete']
    with (create_runtime_engine(tmp_path / 'runtime', workers=1, capabilities=CapabilityMonitor()) as engine,
          listening(RemoteGateway(engine, policy, environment=environment), certificates),
          RemoteTransport(config, environment=environment, hello=ClientHello(configured_profile=profile)) as transport):
        client = EvidenceLaneClient(transport)

        def call(action, arguments=None):
            return client.call(action, project_id=store.project_id, arguments=arguments or {})

        _, request = prepare(call, formats=['dot'], dot_validation='native')
        publish(call, request)
        read = call('lane_view_read', {'view_id': request.view_id, 'include_content': True})
        assert read.status == 'ok', read.error
        native = read.result['manifest']['tool_evidence']['dot']['native_graphviz_validation']
        assert native['status'] == 'PASS' and native['host_profile'] == 'CODEX_VM'
        assert native['input_sha256'] == hashlib.sha256(read.result['contents']['dot'].encode()).hexdigest()
        assert read.result['manifest']['worker_execution']['native_task_attestation'] == 'not_provided'
    assert engine.workers.status()['shutdown_complete']


def test_pointer_and_source_exports_do_not_claim_native_execution(views, monkeypatch):
    engine, _store, call = views
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    monkeypatch.setenv('EVIDENCE_LANE_HOST_PROFILE', 'CODEX_DESKTOP')
    _, request = prepare(call, formats=['dot'])
    publish(call, request)
    read = call('lane_view_read', {'view_id': request.view_id})
    assert read.result['manifest']['tool_evidence']['dot']['native_graphviz_validation'] is None
    assert read.result['manifest']['tool_evidence']['dot']['graph_analysis']['status'] == 'PASS'
    engine.workers.operations.pop('render_lane_view')
    _, request = prepare(call, formats=[], include_pointer=True)
    publish(call, request)
    read = call('lane_view_read', {'view_id': request.view_id})
    assert read.result['manifest']['worker_execution'] is None
    assert read.result['manifest']['tool_evidence'] == {}


def test_native_failure_has_no_source_fallback_or_publication(views, monkeypatch):
    engine, store, _ = views
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    call, _ = connect(engine, store, 'codex_cli')
    _, request = prepare(call, formats=['dot'], dot_validation='native')
    original = engine.workers.submit

    def invalid_profile(operation, arguments):
        # A malformed child payload must fail; a successful source exporter must
        # never replace the selected native invocation after it has begun.
        return original(operation, {**arguments, 'native_host_profile': 'INVALID'})

    monkeypatch.setattr(engine.workers, 'submit', invalid_profile)
    result = call('lane_view_refresh', request.model_dump(mode='json'))
    assert result.error.code == 'VIEW_EXPORT_FAILED'
    assert not list(store.root.rglob('*.dot'))
    assert call('lane_view_read', {'view_id': request.view_id}).result['state'] == 'not_materialized'
