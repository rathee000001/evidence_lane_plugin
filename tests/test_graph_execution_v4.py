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
    ('codex_desktop_stable', 'CODEX_DESKTOP'), ('codex_desktop_beta', 'CODEX_DESKTOP')])
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
    assert all(Path(row['path']).is_relative_to(store.root / 'plan') for row in published['files'])
    assert not any((store.root / lane / 'plan').exists() for lane in ('sectors',))


def test_native_request_cannot_use_an_unavailable_binary(views, monkeypatch, tmp_path):
    engine, store, _ = views
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])
    call, _ = connect(engine, store, 'codex_desktop_stable')
    _, request = prepare(call, formats=['dot'], dot_validation='native')
    before = {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(tmp_path / 'absent-native-installation'))
    failed = call('lane_view_refresh', request.model_dump(mode='json'))
    assert failed.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    native_attempt = failed.error.details['attempts'][-1]
    assert any(row['tool_id'] == 'Graphviz_dot' and not row['ready'] for row in native_attempt['observations'])
    assert {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()} == before


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
    call, _ = connect(engine, store, 'codex_desktop_beta')
    _, request = prepare(call, formats=['dot'], dot_validation='native')
    original = engine.workers.submit

    def invalid_profile(operation, arguments):
        # A malformed child payload must fail; a successful source exporter must
        # never replace the selected native invocation after it has begun.
        return original(operation, {**arguments, 'native_host_profile': 'INVALID'})

    monkeypatch.setattr(engine.workers, 'submit', invalid_profile)
    before_dot = {
        path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*.dot')
    }
    result = call('lane_view_refresh', request.model_dump(mode='json'))
    assert result.error.code == 'VIEW_EXPORT_FAILED'
    assert {
        path.relative_to(store.root): path.read_bytes() for path in store.root.rglob('*.dot')
    } == before_dot
    assert call('lane_view_read', {'view_id': request.view_id}).result['state'] == 'not_materialized'
