from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher, dispatch_authenticated
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.registry import ActionRegistry, ActionSpec, Contract
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool
from pydantic import Field


class Input(Contract):
    text: str = Field(default='evidence', max_length=500)
    gate: str | None = None
    repeat: int = Field(default=1, ge=1, le=3)
    wrong_worker: bool = False
    fail_worker: bool = False
    early_checkpoint: bool = False
    corrupt_output: bool = False


class Output(Contract):
    sha256: str
    bytes: int


_FLASH_FIXTURE_ASSETS = {}


def bind_fixture_flash(engine, tmp_path, monkeypatch):
    """Compile the real policy for this isolated fixture's exact action set."""
    import hashlib

    import evidence_lane_plugin.flash_authority as flash_module
    from evidence_lane_plugin.env_uop_graph import compile_assets
    from evidence_lane_plugin.storage import json_text

    source = Path(flash_module.__file__).resolve().parents[2]
    key = hashlib.sha256(json_text(engine.registry.schemas()).encode()).hexdigest()
    if key not in _FLASH_FIXTURE_ASSETS:
        assets = compile_assets(source, engine.registry)
        for name in ['toolchains/tool-catalog.v4.json']:
            assets[name] = (source / name).read_bytes()
        _FLASH_FIXTURE_ASSETS[key] = assets
    assets = _FLASH_FIXTURE_ASSETS[key]
    destination = tmp_path / 'fixture-policy'
    for name, raw in assets.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    pin = hashlib.sha256(assets['env/SESSION_FLASH_MANIFEST.json']).hexdigest()
    original_init = flash_module.SessionFlashAuthority.__init__

    def initialize(self, *, asset_root=None):
        original_init(self, asset_root=asset_root or destination)

    monkeypatch.setattr(flash_module, 'FLASH_MANIFEST_SHA256', pin)
    monkeypatch.setattr(flash_module.SessionFlashAuthority, '__init__', initialize)
    engine.sessions.flash = flash_module.SessionFlashAuthority()
    engine.registry.control_plane.flash = engine.sessions.flash
    assert engine.sessions.flash.verify(registry=engine.registry).action_set_digest == key


def gated_hash(arguments):
    import hashlib
    if arguments.get('fail_worker'):
        raise ValueError('worker failed')
    if arguments.get('gate'):
        deadline = time.monotonic() + 8
        while not Path(arguments['gate']).exists():
            if time.monotonic() > deadline:
                raise TimeoutError()
            time.sleep(0.005)
    data = arguments['text'].encode()
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


def handler(context, arguments):
    operation = 'ungranted' if arguments.wrong_worker else 'fixture_hash'
    result = None
    for _ in range(arguments.repeat):
        payload = arguments.model_dump()
        if payload['gate'] is None:
            payload['gate'] = context.execution.store.source_root / 'already-open'
            payload['gate'] = str(payload['gate'])
        result = context.execution.submit(operation, payload).result(timeout=10)
        if arguments.early_checkpoint:
            context.execution.checkpoint('intermediate', {})
    if result['status'] != 'ok':
        # Intentionally dishonest fixture handler: driver must still reject.
        return {'sha256': 'claimed', 'bytes': 0}
    return {**result['result'], 'sha256': 'wrong-but-typed'} if arguments.corrupt_output else result['result']


def verify_hash(context, arguments, output):
    import hashlib
    content = arguments.text.encode()
    observations = {'sha256_matches_input': output.sha256 == hashlib.sha256(content).hexdigest(),
                    'byte_count_matches_input': output.bytes == len(content)}
    return [{'check_id': name, 'passed': observations[name], 'evidence': {'input_bytes': len(content)}}
            for name in context.requested_checks]


@pytest.fixture
def system(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'already-open').touch()
    registry = ActionRegistry()
    registry.register(ActionSpec('fixture_hash', 'Hash a bounded text fixture.', Input, Output, handler,
        profile='fixture', permission='write', requires_delta=True, required_tools=('hashlib',),
        worker_operations=('fixture_hash',), verifier=verify_hash,
        verification_checks=('sha256_matches_input', 'byte_count_matches_input')))
    operation = WorkerOperation('fixture_hash', __name__, 'gated_hash', path_fields=('gate',))
    ungranted = WorkerOperation('ungranted', __name__, 'gated_hash')
    with Engine(tmp_path / 'runtime', registry=registry,
                worker_pool=WorkerPool((operation, ungranted,
                    WorkerOperation('render_lane_view','evidence_lane_plugin.artifact_contract','render_lane_view_worker',
                                    dependencies=('langgraph','langchain_core','graphviz'))), workers=1)) as engine:
        bind_fixture_flash(engine, tmp_path, monkeypatch)
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write'])]))
        yield engine, store, token, session


def plan(system, *, additional=None, **changes):
    engine, store, _, _ = system
    task = {'task_id': 'first', 'title': 'Hash input', 'requested_outcome': 'Get verified digest', 'profile': 'fixture',
            'allowed_actions': ['fixture_hash'], 'permitted_tools': ['hashlib'], 'permitted_paths': ['.'],
            'acceptance_checks': ['sha256_matches_input', 'byte_count_matches_input']}
    task.update(changes)
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Execution fixture', tasks=[TaskDefinition.model_validate(task), *(additional or [])]),
                               lease, actor_id='fixture')
    return PlanStore(store).task('first', expected_revision=1)


def request(system, view, **arguments):
    _, store, _, _ = system
    return ActionRequest(action='delta_enter', project_id=store.project_id, expected_revision=1, arguments={
        'task_id': 'first', 'plan_revision': 1, 'contract_digest': view.contract_digest,
        'action': 'fixture_hash', 'arguments': arguments})


def call(system, request):
    engine, _, _, session = system
    return PublicActionSDKDispatcher(engine).execute(request, session)


def finished(system, job_id):
    engine, store, _, _ = system
    deadline = time.monotonic() + 12
    while engine.health().accepted_requests:
        if time.monotonic() > deadline:
            pytest.fail('Delta driver did not finish')
        time.sleep(0.01)
    with store.lane('plan').connection(read_only=True) as connection, store.lane('receipts').connection(read_only=True) as _receipts:
        return dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (job_id,)).fetchone())


def test_queued_typed_result_is_pinned_and_completed_only_by_registered_checks(system):
    view = plan(system)
    entered = request(system, view, text='hello')
    response = call(system, entered)
    assert response.status == 'queued', response.error
    row = finished(system, response.job_id)
    assert row['state'] == 'verified', row
    _, store, _, _ = system
    body = json.loads(store.lane('plan').read_object(row['result_object']))
    assert body['result']['bytes'] == 5
    assert body['contract_digest'] == view.contract_digest
    assert body['tool_execution']['operation'] == 'fixture_hash'
    assert body['tool_execution']['plan_revision'] == entered.expected_revision
    assert body['tool_execution']['outcome'] == 'returned_validated_result'
    assert body['workers'][0]['worker_pid'] > 0
    assert body['usage']['tool_calls'] == 2
    assert PlanStore(store).snapshot().counts == {'completed': 1}
    assert JobQueue(store).get(response.job_id)['resumable'] == 0
    repeated = call(system, entered)
    assert repeated.job_id == response.job_id
    conflicting = entered.model_copy(update={'arguments': {**entered.arguments, 'arguments': {'text': 'changed'}}})
    assert call(system, conflicting).error.code == 'REQUEST_ID_CONFLICT'
    assert system[0].workers.status()['submitted'] == 1


@pytest.mark.parametrize('change,code', [
    ({'profile': 'other'}, 'DELTA_ACTION_SCOPE'),
    ({'allowed_actions': []}, 'DELTA_ACTION_SCOPE'),
    ({'permitted_tools': []}, 'DELTA_TOOL_SCOPE'),
    ({'budget': {'max_input_bytes': 1}}, 'DELTA_INPUT_BUDGET'),
])
def test_task_contract_rejects_before_activation_or_worker(system, change, code):
    view = plan(system, **change)
    response = call(system, request(system, view))
    assert response.error.code == code
    assert PlanStore(system[1]).snapshot().counts == {'queued': 1}
    assert system[0].workers.status()['submitted'] == 0


def test_no_direct_action_or_stale_revision_or_scope_escalation(system):
    view = plan(system)
    engine, store, _, session = system
    assert call(system, ActionRequest(action='fixture_hash', project_id=store.project_id)).error.code == 'DELTA_REQUIRED'
    entered = request(system, view)
    assert call(system, entered.model_copy(update={'expected_revision': 2})).error.code == 'DELTA_REVISION_REQUIRED'
    bad = entered.model_copy(update={'arguments': {**entered.arguments, 'contract_digest': '0' * 64}})
    assert call(system, bad).error.code == 'DELTA_CONTRACT_CHANGED'
    # Same restriction used by RemoteGateway; a wrapper grant cannot authorize its target.
    context = replace(engine.clients.context(session, store.project_id, 'write'), allowed_actions=frozenset({'delta_enter'}))
    assert dispatch_authenticated(engine, entered, context).error.code == 'ACTION_SCOPE_DENIED'
    assert engine.workers.status()['submitted'] == 0


@pytest.mark.parametrize('arguments,changes,code', [
    ({'wrong_worker': True}, {}, 'DELTA_WORKER_SCOPE'),
    ({'repeat': 2}, {'budget': {'max_tool_calls': 2}}, 'DELTA_TOOL_BUDGET'),
    ({}, {'budget': {'max_output_bytes': 10}}, 'DELTA_OUTPUT_BUDGET'),
    ({'fail_worker': True, 'early_checkpoint': True}, {}, 'WORKER_OPERATION_FAILED'),
    ({'gate': '../escape'}, {}, 'DELTA_PATH_SCOPE'),
])
def test_running_contract_failures_checkpoint_without_plan_success(system, arguments, changes, code):
    view = plan(system, **changes)
    response = call(system, request(system, view, **arguments))
    assert response.status == 'queued'
    row = finished(system, response.job_id)
    assert row['state'] == 'blocked' and row['error_code'] == code, row
    assert PlanStore(system[1]).snapshot().counts == {'blocked': 1}
    assert JobQueue(system[1]).get(response.job_id)['state'] != 'succeeded'


def test_disconnect_during_worker_prevents_next_effect_and_keeps_owned_drain(system):
    engine, store, token, _ = system
    view = plan(system)
    gate = store.source_root / 'release'
    response = call(system, request(system, view, gate=str(gate), repeat=2))
    try:
        deadline = time.monotonic() + 5
        while engine.workers.status()['submitted'] < 1:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        engine.clients.disconnect(token)
        drained = engine.quiesce(timeout=0.01)
        assert not drained.quiescent and drained.stage == 'requests'
    finally:
        gate.touch()
    row = finished(system, response.job_id)
    assert row['error_code'] == 'CLIENT_SESSION_EXPIRED', row
    assert engine.workers.status()['submitted'] == 1
    assert engine.quiesce(timeout=5).quiescent


def test_elapsed_budget_waits_for_owned_worker_then_refuses_success(system):
    view = plan(system, budget={'max_seconds': 1})
    gate = system[1].source_root / 'release'
    response = call(system, request(system, view, gate=str(gate)))
    try:
        time.sleep(1.15)
        assert system[0].health().accepted_requests > 0
    finally:
        gate.touch()
    row = finished(system, response.job_id)
    assert row['error_code'] == 'DELTA_TIME_BUDGET', row


def test_entry_sql_failure_rolls_back_plan_and_job(system, monkeypatch):
    view = plan(system)
    store = system[1]
    original = store.__class__.append_receipt
    def fail(self, kind, *args, **kwargs):
        if kind == 'delta_entered':
            raise LaneError('INJECTED_TRANSACTION_FAILURE', 'Injected fixture failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(store.__class__, 'append_receipt', fail)
    response = call(system, request(system, view))
    assert response.error.code == 'INJECTED_TRANSACTION_FAILURE'
    assert PlanStore(store).snapshot().counts == {'queued': 1}
    with store.lane('plan').connection(read_only=True) as connection, store.lane('receipts').connection(read_only=True) as _receipts:
        assert connection.execute('SELECT count(*) FROM jobs_jobs').fetchone()[0] == 0
        assert connection.execute('SELECT count(*) FROM delta_runs').fetchone()[0] == 0
