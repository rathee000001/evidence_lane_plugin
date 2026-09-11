"""Current Delta, connector and owned-process integration with the real SDK."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin import bounded_io, github_toolchain
from evidence_lane_plugin.bounded_io import BoundedProcessResult
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.connector_governance import (
    ConnectorGovernance,
    PluginRegistration,
    connector_service,
)
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.github_toolchain import (
    GITHUB_BACKEND_ID,
    GITHUB_BACKEND_VERSION,
    GITHUB_OPERATION,
)
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest

from .github_sdk_fixture import TEST_CREDENTIAL
from .test_delta_entry import bind_fixture_flash
from .test_native_workflow_bindings import native

REPOSITORY = 'acme/fixture'


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setenv('EVI_GITHUB_TEST', TEST_CREDENTIAL)
    source = tmp_path / 'source'
    source.mkdir()
    state = {'configuration': {}, 'directories': [], 'request_counts': [], 'signal': None, 'damage': None,
        'clock_offset': 0, 'timing': {}}
    original_service = ConnectorGovernance.__init__
    def governed_clock(self, *args, clock=None, **kwargs):
        original_service(self, *args, clock=clock or (lambda: datetime.now(UTC) + timedelta(seconds=state['clock_offset'])), **kwargs)
    monkeypatch.setattr(ConnectorGovernance, '__init__', governed_clock)
    original = bounded_io.run_owned_bounded_process
    wrapper = Path(__file__).with_name('github_sdk_fixture_worker.py')
    with Engine(tmp_path / 'runtime') as engine:
        bind_fixture_flash(engine, tmp_path, monkeypatch)
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(hello=ClientHello(configured_profile='codex_cli'),
            projects=[ProjectSelection(project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))

        def controlled(command, **kwargs):
            if len(command) < 2 or Path(command[-2]).name != '_github_inspection_worker.py':
                return original(command, **kwargs)
            directory = Path(kwargs['cwd'])
            state['directories'].append(directory)
            assert kwargs['env']['_EVI_GITHUB_TOKEN'] == TEST_CREDENTIAL
            assert TEST_CREDENTIAL not in (directory / 'request.json').read_text()
            assert all(TEST_CREDENTIAL not in item for item in command)
            marker = directory / 'started.marker'
            configuration = dict(state['configuration'], marker=str(marker))
            (directory / 'fixture.json').write_text(json.dumps(configuration))
            checked = kwargs['check']
            execution = checked.__self__
            signalled = False
            def check():
                nonlocal signalled
                if marker.exists() and state['signal'] and not signalled:
                    signalled = True
                    state['timing']['signal_started'] = time.monotonic()
                    with execution.slot.mutex:
                        if state['signal'] == 'cancel':
                            execution.queue.request_cancel(execution.claim.job_id, 'Controlled cancellation', execution.lease)
                        elif state['signal'] == 'expire':
                            state['clock_offset'] = 3600
                        else:
                            connector_service(engine, store).revoke('github-reader', execution.guard.context, execution.lease)
                    state['timing']['signal_recorded'] = time.monotonic()
                checked()
            kwargs['check'] = check
            try:
                state['timing']['worker_started'] = time.monotonic()
                result = original([*command[:-2], str(wrapper), command[-1]], **kwargs)
                if state['damage']:
                    body = json.loads(result.stdout)
                    assert result.returncode == 0, body
                    if state['damage'] == 'binding':
                        body['request_sha256'] = '0' * 64
                    else:
                        body['inspection']['repository'] = 'acme/another'
                    result = BoundedProcessResult(0, json.dumps(body).encode(), result.stderr)
                return result
            finally:
                state['timing']['worker_joined'] = time.monotonic()
                state['request_counts'].append(int(marker.read_text()) if marker.exists() else 0)
        monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', controlled)
        yield engine, store, session, state


def invoke(system, action, arguments=None, **kwargs):
    engine, store, session, _ = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def configure(system, **changes):
    registration = PluginRegistration.model_validate({
        'plugin_id': 'github-reader', 'name': 'GitHub repository reader', 'plugin_kind': 'connector',
        'description': 'Inspect bounded repository metadata.', 'purpose': 'Read this selected test repository.',
        'config_env_keys': ['EVI_GITHUB_TEST'], 'capabilities': ['repository_metadata_read'],
        'allowed_lanes': ['github_code'], 'allowed_actions': [GITHUB_OPERATION],
        'resource_ids': [REPOSITORY], 'expires_at': 'NO_EXPIRY', 'role': 'github_repository_snapshot',
        'role_schema': {'repository': 'text', 'snapshot': 'json', 'receipt_sha256': 'blob_hash'},
        'host_profiles': ['codex_cli'], 'backend_runtime': 'python',
        'backend_id': GITHUB_BACKEND_ID, 'backend_version': GITHUB_BACKEND_VERSION,
    } | changes)
    result = invoke(system, 'connector_configure', {'registration': registration.model_dump()})
    assert result.status == 'ok', result.error
    return result


def plan(system):
    engine, store, _, _ = system
    definition = TaskDefinition(task_id='inspect-repository', title='Inspect GitHub repository',
        requested_outcome='Read bounded repository metadata under the selected grant', profile='code',
        allowed_actions=[GITHUB_OPERATION], permitted_tools=['Python', 'PyGithub'],
        acceptance_checks=['github_snapshot_integrity'])
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='GitHub inspection', tasks=[definition]), lease, actor_id='fixture')
    return PlanStore(store).task('inspect-repository', expected_revision=1)


def enter(system, task, **arguments):
    return invoke(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': GITHUB_OPERATION,
        'arguments': {'repository': REPOSITORY} | arguments}, expected_revision=1)


def finished(system, job_id):
    engine, store, _, _ = system
    deadline = time.monotonic() + 25
    while engine.health().accepted_requests:
        if time.monotonic() > deadline:
            pytest.fail('The owned GitHub Delta did not finish')
        time.sleep(0.01)
    with store.lane('plan').connection(read_only=True) as connection:
        return dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (job_id,)).fetchone())


def test_actual_sdk_worker_delta_and_verification_are_bound_to_exact_grant(system):
    configured = configure(system)
    task = plan(system)
    entered = enter(system, task, max_branches=3, max_workflows=2)
    assert entered.status == 'queued', entered.error
    row = finished(system, entered.job_id)
    assert row['state'] == 'verified', row
    store, state = system[1], system[3]
    body = json.loads(store.lane('plan').read_object(row['result_object']))
    assert body['tool_execution']['extension_binding']['registration_digest'] == configured.result['digest']
    text = json.dumps(body)
    assert 'PyGithub' in text and TEST_CREDENTIAL not in text
    assert state['request_counts'] == [4] and all(not path.exists() for path in state['directories'])
    with store.lane('receipts').connection(read_only=True) as connection:
        events = connection.execute('SELECT event_type,details_json FROM extensions_events ORDER BY sequence').fetchall()
    assert [row[0] for row in events] == ['configured', 'execution_prepared', 'execution_returned']
    assert all(TEST_CREDENTIAL not in row[1] for row in events)


@pytest.mark.parametrize('changes', [{'backend_version': '2.9.0'}, {'allowed_lanes': ['local_code']},
    {'resource_ids': ['acme/another']}, {'config_env_keys': []}, {'host_profiles': ['codex_desktop']}])
def test_mismatched_or_unconfigured_grant_stops_before_worker(system, changes):
    configure(system, **changes)
    task = plan(system)
    result = enter(system, task)
    assert result.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert system[3]['directories'] == [] and PlanStore(system[1]).snapshot().counts == {'queued': 1}


def test_out_of_scope_resource_is_rejected_before_credential_readiness(system, monkeypatch):
    configure(system)
    def unexpected(*args):
        raise AssertionError('An out-of-scope request read credential configuration')
    monkeypatch.setattr(github_toolchain, '_configured_token', unexpected)
    result = invoke(system, 'toolchain_resolve', {'action': GITHUB_OPERATION, 'arguments': {'repository': 'acme/another'}})
    assert result.status == 'ok'
    assert result.result['resolution']['attempts'][0]['extension']['reason'] == 'PLUGIN_RESOURCE_SCOPE_DENIED'
    assert system[3]['directories'] == []


def test_readiness_uses_only_the_explicitly_selected_registration(system, monkeypatch):
    configure(system)
    configure(system, plugin_id='github-other', config_env_keys=['EVI_GITHUB_MISSING'])
    monkeypatch.delenv('EVI_GITHUB_MISSING', raising=False)
    good = invoke(system, 'toolchain_resolve', {'action': GITHUB_OPERATION,
        'arguments': {'repository': REPOSITORY, 'plugin_id': 'github-reader'}})
    bad = invoke(system, 'toolchain_resolve', {'action': GITHUB_OPERATION,
        'arguments': {'repository': REPOSITORY, 'plugin_id': 'github-other'}})
    assert good.result['resolution']['selected_route'] == GITHUB_OPERATION + '.pygithub'
    assert bad.result['resolution']['selected_route'] is None and system[3]['directories'] == []


@pytest.mark.parametrize('signal', ['cancel', 'revoke', 'expire'])
def test_current_cancellation_or_grant_revocation_terminates_running_io(system, signal):
    configure(system, **({'expires_at': (datetime.now(UTC) + timedelta(minutes=5)).isoformat()} if signal == 'expire' else {}))
    system[3].update(signal=signal, configuration={'delay_seconds': 5})
    task = plan(system)
    entered = enter(system, task)
    assert entered.status == 'queued', entered.error
    row = finished(system, entered.job_id)
    assert row['state'] == 'blocked' and row['result_object'] is None, row
    timing = system[3]['timing']
    assert timing['worker_joined'] - timing['signal_started'] < 5, timing
    assert system[3]['request_counts'] == [1] and all(not path.exists() for path in system[3]['directories'])
    assert row['error_code'] in {'JOB_CHECKPOINT_REQUIRED', 'PLUGIN_ROUTE_DENIED'}


@pytest.mark.parametrize('damage', ['binding', 'repository'])
def test_invalid_worker_result_is_not_admitted_to_the_delta(system, damage):
    configure(system)
    system[3]['damage'] = damage
    task = plan(system)
    entered = enter(system, task)
    assert entered.status == 'queued', entered.error
    row = finished(system, entered.job_id)
    assert row['state'] == 'blocked' and row['result_object'] is None
    assert row['error_code'] in {'GITHUB_WORKER_BINDING_CHANGED', 'GITHUB_RESULT_INVALID'}
    assert all(not path.exists() for path in system[3]['directories'])


def test_packaged_mcp_advertises_exact_binding_and_runs_the_actual_sdk_worker(system):
    engine, store, _, _ = system
    configure(system, host_profiles=['codex_cli', 'codex_desktop'])
    task = plan(system)
    with LocalEndpoint(engine):
        async def exercise():
            async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools', 'admin'), entrypoint='package') as session:
                tools = await session.list_tools()
                tool = next(tool for tool in tools.tools if tool.name == GITHUB_OPERATION)
                properties = tool.inputSchema['properties']['arguments']['properties']
                assert 'repository' in properties and 'max_http_requests' in properties and 'network_allowed' not in properties
                response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1,
                        'contract_digest': task.contract_digest, 'action': GITHUB_OPERATION,
                        'arguments': {'repository': REPOSITORY, 'include_workflows': False}}})
                result = response.structuredContent
                assert result['status'] == 'queued', result
                row = finished(system, result['job_id'])
                assert row['state'] == 'verified', row
        asyncio.run(exercise())
