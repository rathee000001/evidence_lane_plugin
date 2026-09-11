"""Real Delta/worker execution through exact optional adapter grants."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.connector_governance import PluginRegistration, connector_service
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.extension_routes import ExtensionBinding
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.registry import ActionRegistry, ActionSpec, Contract
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.tool_routes import ToolRoute
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool


class ReadInput(Contract):
    filename: str


class ReadOutput(Contract):
    source_hash: str
    bytes: int


def read_worker(arguments):
    content = Path(arguments['filename']).read_bytes()
    return {'source_hash': hashlib.sha256(content).hexdigest(), 'bytes': len(content)}


def read_adapter(context, request):
    response = context.execution.submit('fixture_read', request.model_dump()).result(timeout=10)
    if response['status'] != 'ok':
        raise LaneError('FIXTURE_READ_FAILED', 'The bounded fixture read failed.')
    return response['result']


def verify_read(context, request, output):
    return [{'check_id': name, 'passed': output.bytes == 5 and output.source_hash == hashlib.sha256(b'hello').hexdigest(),
             'evidence': {'expected_fixture_bytes': 5}} for name in context.requested_checks]


@pytest.fixture
def system(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    (source / 'allowed').mkdir(parents=True)
    (source / 'allowed/input.md').write_bytes(b'hello')
    (source / 'outside.md').write_bytes(b'separate')
    runtime = {'ready': True, 'backend_version': '1.0.0'}

    def ready(context, registration):
        if runtime.get('probe_error'):
            raise RuntimeError('private-vendor-secret')
        return dict(runtime)

    def registered_handler(context, request):
        output = read_adapter(context, request)
        if runtime.get('invalid_output'):
            output['source_hash'] = 'invalid-role-value'
        if runtime.get('adapter_error'):
            raise RuntimeError('private-vendor-secret')
        if runtime.get('revoke_between_workers'):
            service = connector_service(engine, context.execution.store)
            service.revoke('source-reader', context, context.execution.lease)
            read_adapter(context, request)
        return output

    binding = ExtensionBinding('fixture.file-reader', '1.0.0', 'python', 'bounded_read', 'local_code',
        'source_reader', (('source_hash', 'blob_hash'), ('bytes', 'integer')), ready, read_path_fields=('filename',))
    spec = ActionSpec('fixture_extension_read', 'Read one scoped source using an optional fixture adapter.',
        ReadInput, ReadOutput, registered_handler, profile='code', requires_delta=True,
        path_fields=('filename',), worker_operations=('fixture_read',), verifier=verify_read,
        verification_checks=('fixture_hash_matches',),
        tool_routes=(ToolRoute('fixture.read', registered_handler, extension=binding),))
    registry = ActionRegistry()
    registry.register(spec)
    operations = (WorkerOperation('fixture_read', __name__, 'read_worker', path_fields=('filename',)),
        WorkerOperation('render_lane_view', 'evidence_lane_plugin.artifact_contract', 'render_lane_view_worker',
                        dependencies=('langgraph', 'langchain_core', 'graphviz')))
    with Engine(tmp_path / 'runtime', registry=registry, worker_pool=WorkerPool(operations, workers=1)) as engine:
        from tests.test_delta_entry import bind_fixture_flash
        bind_fixture_flash(engine, tmp_path, monkeypatch)
        project = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(project['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(hello=ClientHello(configured_profile='codex_cli'),
            projects=[ProjectSelection(project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
        yield engine, store, session, spec, runtime


def registration(system, **changes):
    _, store, _, _, _ = system
    return PluginRegistration.model_validate({
        'plugin_id': 'source-reader', 'name': 'Source reader', 'plugin_kind': 'toolchain',
        'description': 'Read one bounded project file.', 'purpose': 'Read the selected source for this project.',
        'capabilities': ['bounded_read'], 'allowed_lanes': ['local_code'],
        'allowed_actions': ['fixture_extension_read'], 'read_roots': [str(store.source_root / 'allowed')],
        'expires_at': 'NO_EXPIRY', 'role': 'source_reader', 'role_schema': {'source_hash': 'blob_hash', 'bytes': 'integer'},
        'host_profiles': ['codex_cli'], 'backend_runtime': 'python', 'backend_id': 'fixture.file-reader',
        'backend_version': '1.0.0',
    } | changes)


def configure(system, *, expected=None, **changes):
    return call(system, 'connector_configure', {'registration': registration(system, **changes).model_dump(),
                                             'expected_version': expected})


def call(system, action, arguments=None, **kwargs):
    engine, store, session, _, _ = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def resolved(system, **arguments):
    result = call(system, 'toolchain_resolve', {'action': 'fixture_extension_read', 'arguments': {'filename': 'allowed/input.md'} | arguments})
    assert result.status == 'ok', result.error
    return result.result['resolution']


def plan(system):
    engine, store, _, _, _ = system
    task = TaskDefinition(task_id='read-source', title='Read source', requested_outcome='Verify selected source digest',
        profile='code', allowed_actions=['fixture_extension_read'], permitted_tools=['Python'],
        permitted_paths=['allowed'], acceptance_checks=['fixture_hash_matches'])
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Scoped adapter', tasks=[task]), lease, actor_id='fixture')
    return PlanStore(store).task('read-source', expected_revision=1)


def enter(system, view):
    return call(system, 'delta_enter', {'task_id': 'read-source', 'plan_revision': 1,
        'contract_digest': view.contract_digest, 'action': 'fixture_extension_read',
        'arguments': {'filename': 'allowed/input.md'}}, expected_revision=1)


def finished(system, job):
    engine, store, _, _, _ = system
    deadline = time.monotonic() + 15
    while engine.health().accepted_requests:
        if time.monotonic() > deadline:
            pytest.fail('The owned Delta did not finish')
        time.sleep(0.01)
    with store.lane('plan').connection(read_only=True) as connection:
        return dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (job,)).fetchone())


def test_real_worker_read_is_bound_to_grant_and_receipts_lane(system):
    response = configure(system)
    assert response.status == 'ok', response.error
    view = plan(system)
    admitted = enter(system, view)
    assert admitted.status == 'queued', admitted.error
    row = finished(system, admitted.job_id)
    assert row['state'] == 'verified', row
    store = system[1]
    body = json.loads(store.lane('plan').read_object(row['result_object']))
    proof = body['tool_execution']['extension_binding']
    assert proof['registration_digest'] == response.result['digest']
    assert proof['backend_version'] == '1.0.0' and proof['configured_host'] == 'codex_cli'
    assert body['workers'][0]['worker_pid'] > 0
    entry = json.loads(store.lane('plan').read_object(row['entry_object']))
    assert entry['tool_admission']['extension'] == proof
    with store.lane('receipts').connection(read_only=True) as connection:
        events = connection.execute('SELECT event_type,details_json FROM extensions_events ORDER BY sequence').fetchall()
    assert [event['event_type'] for event in events] == ['configured', 'execution_prepared', 'execution_returned']
    assert all('hello' not in event['details_json'] for event in events)


def test_initial_grant_event_failure_rolls_back_schema_and_registration(system, monkeypatch):
    from evidence_lane_plugin.connector_governance import ConnectorGovernance
    from evidence_lane_plugin.storage import LaneStore

    store = system[1]
    baseline = {}
    original = LaneStore.append_receipt
    initialize = ConnectorGovernance.initialize

    def capture(self, lease):
        # Writer acquisition has its own valid receipt. Capture the boundary
        # after that acquisition and before this grant's first schema change.
        baseline['head'] = store.pv_head()
        baseline['database'] = store.lane('receipts').database.read_bytes()
        return initialize(self, lease)

    def append(self, kind, body, **kwargs):
        if kind == 'plugin_configured':
            raise LaneError('INJECTED_GRANT_RECEIPT', 'Controlled publication failure.')
        return original(self, kind, body, **kwargs)

    monkeypatch.setattr(LaneStore, 'append_receipt', append)
    monkeypatch.setattr(ConnectorGovernance, 'initialize', capture)
    result = configure(system)
    assert result.error.code == 'INJECTED_GRANT_RECEIPT'
    assert store.pv_head() == baseline['head']
    assert store.lane('receipts').database.read_bytes() == baseline['database']
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='extensions_registration'").fetchone() is None


@pytest.mark.parametrize('changes', [
    {'backend_id': 'absent.adapter'}, {'backend_version': '2.0.0'}, {'backend_id': None, 'backend_version': None},
    {'backend_runtime': 'external_mcp'}, {'role': 'different_role'}, {'role_schema': {'source_hash': 'text', 'bytes': 'integer'}},
    {'allowed_lanes': ['research']}, {'host_profiles': ['codex_desktop']}, {'capabilities': ['other_read']},
    {'allowed_actions': ['connector_read']},
])
def test_mismatched_grant_cannot_select_or_run(system, changes):
    assert configure(system, **changes).status == 'ok'
    assert resolved(system)['selected_route'] is None
    view = plan(system)
    assert enter(system, view).error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert system[0].workers.status()['submitted'] == 0
    assert PlanStore(system[1]).snapshot().counts == {'queued': 1}


def test_runtime_unavailable_and_probe_secret_are_not_executed_or_disclosed(system):
    configure(system)
    system[4].update(ready=False, secret='private-vendor-secret')
    route = resolved(system)
    assert route['selected_route'] is None
    assert route['attempts'][0]['extension']['reason'] == 'PLUGIN_RUNTIME_UNAVAILABLE'
    assert 'private-vendor-secret' not in json.dumps(route)
    system[4].update(ready=True, backend_version='2.0.0')
    assert resolved(system)['selected_route'] is None
    system[4].update(backend_version='1.0.0', probe_error=True)
    route = resolved(system)
    assert route['selected_route'] is None and 'private-vendor-secret' not in json.dumps(route)


@pytest.mark.parametrize('filename', ['outside.md', '../escape.md', 'allowed/input.md:stream'])
def test_read_root_is_checked_before_worker_submission(system, filename):
    configure(system)
    assert resolved(system, filename=filename)['selected_route'] is None
    assert system[0].workers.status()['submitted'] == 0


@pytest.mark.parametrize('change', ['version', 'revocation', 'expiry', 'client_disconnect'])
def test_grant_change_after_admission_stops_queued_execution(system, change, monkeypatch):
    engine, store, session, _, _ = system
    configured = configure(system)
    view = plan(system)
    callbacks = []
    start = engine.start_job
    monkeypatch.setattr(engine, 'start_job', callbacks.append)
    admitted = enter(system, view)
    assert admitted.status == 'queued', admitted.error
    if change == 'version':
        assert configure(system, expected=1, purpose='A different reviewed purpose.').status == 'ok'
    elif change == 'revocation':
        assert call(system, 'connector_revoke', {'plugin_id': 'source-reader', 'expected_version': 1,
            'expected_digest': configured.result['digest']}).status == 'ok'
    elif change == 'expiry':
        monkeypatch.setattr('evidence_lane_plugin.connector_governance.ConnectorGovernance._live', lambda *args: False)
    else:
        # Revoke the exact durable client grant without replacing its in-memory identity.
        from evidence_lane_plugin.projects import ProjectAccess
        with engine.project_work.mutation(store) as lease:
            ProjectAccess(store).revoke(session.projects[store.project_id].grant_id, writer=lease)
    monkeypatch.setattr(engine, 'start_job', start)
    with engine.admit():
        start(callbacks[0])
    row = finished(system, admitted.job_id)
    assert row['state'] == 'blocked', row
    assert system[0].workers.status()['submitted'] == 0


def test_catalog_reads_are_side_effect_free_and_separate_lanes_from_profiles(system):
    configure(system)
    store = system[1]
    paths = [store.database, *(store.lane(row['lane_id']).database for row in store.lane_catalog())]
    before = {path: path.read_bytes() for path in paths}
    result = call(system, 'connector_read')
    assert result.status == 'ok', result.error
    scopes = result.result['scopes']
    from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
    assert set(scopes['lanes']) == set(CANONICAL_LANE_IDS) and 'local_code' in scopes['lanes'] and 'code' not in scopes['lanes']
    assert 'code' in scopes['action_profiles']
    adapter = next(row for row in scopes['adapters'] if row['backend_id'] == 'fixture.file-reader')
    assert adapter['lane'] == 'local_code' and adapter['lane_field'] is None
    assert resolved(system)['selected_route'] == 'fixture.read'
    assert before == {path: path.read_bytes() for path in paths}


def test_ambiguous_matching_registrations_fail_closed(system):
    configure(system)
    assert configure(system, plugin_id='another-reader').status == 'ok'
    route = resolved(system)
    assert route['attempts'][0]['extension']['reason'] == 'PLUGIN_ROUTE_AMBIGUOUS'


@pytest.mark.parametrize('flag,code', [
    ('invalid_output', 'PLUGIN_ROLE_OUTPUT_INVALID'), ('adapter_error', 'TOOL_ADAPTER_FAILED'),
    ('revoke_between_workers', 'PLUGIN_ROUTE_DENIED'),
])
def test_worker_failure_or_revocation_preserves_failed_receipt_without_retry(system, flag, code):
    configure(system)
    system[4][flag] = True
    admitted = enter(system, plan(system))
    assert admitted.status == 'queued', admitted.error
    row = finished(system, admitted.job_id)
    assert row['state'] == 'blocked' and row['error_code'] == code, row
    assert system[0].workers.status()['submitted'] == 1
    with system[1].lane('receipts').connection(read_only=True) as connection:
        events = connection.execute('SELECT * FROM extensions_events ORDER BY sequence').fetchall()
    assert events[-1]['event_type'] == 'execution_failed'
    assert all('private-vendor-secret' not in event['details_json'] and 'invalid-role-value' not in event['details_json'] for event in events)
    assert json.loads(events[-1]['details_json'])['automatic_replay'] is False


def test_external_resource_scope_checks_exact_ids(system):
    engine, store, session, spec, _ = system
    service = connector_service(engine, store)
    context = engine.clients.context(session, store.project_id, 'tools')
    binding = replace(spec.tool_routes[0].extension, read_path_fields=(), resource_fields=('filename',))
    router = engine.registry.tool_router.extensions
    plugin = registration(system, resource_ids=['repo:allowed']).model_dump()
    router._scope(service, plugin, binding, context, ReadInput(filename='repo:allowed'))
    with pytest.raises(LaneError, match='resource'):
        router._scope(service, plugin, binding, context, ReadInput(filename='repo:another'))


def test_untyped_adapter_and_scope_fields_cannot_enter_registry(system):
    spec = system[3]
    for binding in (object(), replace(spec.tool_routes[0].extension, lane='extensions'),
                    replace(spec.tool_routes[0].extension, read_path_fields=('undeclared',))):
        with pytest.raises(LaneError):
            ActionRegistry().register(replace(spec, tool_routes=(ToolRoute('broken.route', read_adapter, extension=binding),)))
