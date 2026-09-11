from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.writers import WriterLease


@pytest.fixture
def system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    with Engine(tmp_path / 'runtime') as engine:
        registered = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project = engine.directory.open(registered['project_id'], write=True)
        _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=project.project_id, permissions=['read', 'write', 'admin'])]))
        with WriterLease(project, engine.instance_id) as lease:
            PlanStore(project).create(PlanCreate(title='Recover exact effect', tasks=[TaskDefinition(
                task_id='one', title='Recover source', requested_outcome='Verified file')]), lease, actor_id=client.client_id)
            JobQueue(project).initialize(lease)
        yield engine, project, client


def call(system, action, arguments, revision=None):
    engine, project, client = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=project.project_id,
        expected_revision=revision, arguments=arguments), client)


def prepare(system, *, content='after', before=b'before', confirmed=False, action='code_apply'):
    engine, project, client = system
    path = project.source_root / 'result.txt'
    after = b'after'
    if content != 'missing':
        path.write_bytes({'after': after, 'before': before, 'conflict': b'unrelated'}[content])
    before_hash = hashlib.sha256(before).hexdigest() if before is not None else None
    queue = JobQueue(project)
    with WriterLease(project, engine.instance_id) as lease:
        job = queue.enqueue(ActionRequest(action=action, project_id=project.project_id, expected_revision=1,
            arguments={'filename': str(path), 'expected_sha256': before_hash}), client.client_id, lease)
        claim = queue.claim(job, lease)
        effect = queue.prepare_effect(job, 'code:' + str(uuid4()), 'Replace selected bytes', lease, execution_id=claim.execution_id)
        with lease.transaction('receipts'):
            project.append_receipt('code_mutation_prepared' if action == 'code_apply' else 'document_export_prepared', {
                'effect_id': effect, 'job_id': job, 'path': 'result.txt', 'destination': 'result.txt',
                'before_sha256': before_hash, 'after_sha256': hashlib.sha256(after).hexdigest()})
        if confirmed:
            with lease.transaction('plan'):
                proof = queue.store.put_object(b'{"observed_remote_commit":"fixture"}')
                queue.confirm_effect(job, effect, proof, lease, execution_id=claim.execution_id)
    with WriterLease(project, engine.instance_id) as lease:
        assert queue.reconcile_interrupted(lease)[0]['state'] == 'uncertain'
    inspected = call(system, 'job_recovery_inspect', {'job_id': job})
    assert inspected.status == 'ok', inspected
    return path, job, effect, inspected.result['effects'][0]['effect_digest']


@pytest.mark.parametrize('content,before,action,outcome', [
    ('after', b'before', 'code_apply', 'confirmed'),
    ('before', b'before', 'code_apply', 'absent'),
    ('missing', None, 'document_export', 'absent'),
])
def test_recovery_observes_exact_bytes_without_replay_or_success(system, content, before, action, outcome):
    path, job, effect, digest = prepare(system, content=content, before=before, action=action)
    original = path.read_bytes() if path.exists() else None
    result = call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1}, 1)
    assert result.status == 'ok', result
    assert result.result['outcome'] == outcome and result.result['job_state'] == 'failed'
    assert result.result['plan_refresh_required'] and not result.result['job_success_inferred']
    assert not result.result['automatic_replay'] and not result.result['source_bytes_mutated']
    assert (path.read_bytes() if path.exists() else None) == original
    project = system[1]
    evidence = json.loads(project.lane('plan').read_object(result.result['evidence_object']))
    assert evidence['job_id'] == job and evidence['effect_id'] == effect
    assert evidence['basis'] == 'observed_local_preparation'
    assert PlanStore(project).snapshot().revision == 1


def test_committed_confirmation_resolves_without_claiming_current_remote_state(system):
    _, job, effect, digest = prepare(system, confirmed=True, action='git_push')
    response = call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1}, 1)
    assert response.status == 'ok', response
    proof = json.loads(system[1].lane('plan').read_object(response.result['evidence_object']))
    assert proof['basis'] == 'recorded_execution_confirmation' and proof['source_currentness'] == 'not_rechecked'


@pytest.mark.parametrize('content,action,error', [
    ('conflict', 'code_apply', 'JOB_EFFECT_SOURCE_CONFLICT'),
    ('missing', 'code_apply', 'JOB_EFFECT_SOURCE_CONFLICT'),
    ('after', 'git_push', 'JOB_EFFECT_OBSERVATION_REQUIRED'),
])
def test_ambiguous_effect_preserves_uncertainty(system, content, action, error):
    _, job, effect, digest = prepare(system, content=content, action=action)
    response = call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1}, 1)
    assert response.error.code == error
    assert JobQueue(system[1]).get(job)['state'] == 'uncertain'


@pytest.mark.parametrize('change,error', [
    ({'expected_effect_digest': '0' * 64}, 'JOB_EFFECT_CHANGED'),
    ({'plan_revision': 2}, 'JOB_RECOVERY_REVISION_REQUIRED'),
    ({'max_file_bytes': 1}, 'BOUNDED_IO_FILE_BYTES_EXCEEDED'),
    ({'effect_id': str(uuid4())}, 'EFFECT_NOT_FOUND'),
])
def test_recovery_rejects_stale_or_unbounded_selection(system, change, error):
    _, job, effect, digest = prepare(system)
    response = call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1, **change}, 1)
    assert response.error.code == error
    assert JobQueue(system[1]).get(job)['state'] == 'uncertain'


def test_read_only_client_cannot_reconcile_and_inspection_is_immutable(system):
    _, job, effect, digest = prepare(system)
    engine, project, _ = system
    _, reader = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id)]))
    before = project.pv_head()
    selected = engine, project, reader
    assert call(selected, 'job_recovery_inspect', {'job_id': job}).status == 'ok'
    assert project.pv_head() == before
    denied = call(selected, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1}, 1)
    assert denied.error.code == 'PROJECT_NOT_SELECTED'
    assert project.pv_head() == before


def test_reconciliation_failure_rolls_back_effect_and_receipt(system, monkeypatch):
    _, job, effect, digest = prepare(system)
    original = JobQueue.reconcile_effect

    def fail_after_effect(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise LaneError('INJECTED_RECONCILIATION_FAILURE', 'Injected after effect update.')

    monkeypatch.setattr(JobQueue, 'reconcile_effect', fail_after_effect)
    result = call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1}, 1)
    assert result.error.code == 'INJECTED_RECONCILIATION_FAILURE'
    current = JobQueue(system[1]).get(job)
    assert current['state'] == 'uncertain' and current['effects'][0]['state'] == 'prepared'
    with system[1].lane('receipts').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM receipts WHERE kind='effect_reconciled'").fetchone()


@pytest.mark.parametrize('fail_commit', [False, True])
def test_recovery_blocks_the_owning_delta_atomically(system, monkeypatch, fail_commit):
    from evidence_lane_plugin.adaptive_delta_entry import DELTA_MIGRATIONS
    from evidence_lane_plugin.migrations import apply_migrations
    from evidence_lane_plugin.storage import now

    _, job, effect, digest = prepare(system)
    engine, project, client = system
    plan = PlanStore(project)
    with WriterLease(project, engine.instance_id) as lease, lease.transaction('plan') as connection:
        apply_migrations(project.lane('plan'), DELTA_MIGRATIONS, writer=lease)
        plan.transition('one', 'active', lease, expected_revision=1, actor_id=client.client_id, transaction=connection)
        task = PlanStore._task(connection, 1, 'one')
        entry = project.lane('plan').put_object(b'{}')
        request_id = connection.execute('SELECT request_id FROM jobs_jobs WHERE job_id=?', (job,)).fetchone()[0]
        connection.execute("INSERT INTO delta_runs VALUES(?,?,?,?,?,?,?,?, 'running',NULL,NULL,?,?)",
            (job, request_id, client.client_id, 'one', 1, task['contract_digest'], '0'*64, entry, now(), now()))
    if fail_commit:
        original = PlanStore.transition

        def fail_after_transition(self, *args, **kwargs):
            original(self, *args, **kwargs)
            raise LaneError('INJECTED_RECOVERY_PLAN_FAILURE', 'Injected after Plan transition.')

        monkeypatch.setattr(PlanStore, 'transition', fail_after_transition)
    response = call(system, 'job_reconcile_effect', {'job_id': job, 'effect_id': effect,
        'expected_effect_digest': digest, 'plan_revision': 1}, 1)
    if fail_commit:
        assert response.error.code == 'INJECTED_RECOVERY_PLAN_FAILURE'
    else:
        assert response.status == 'ok', response
    assert plan.snapshot().revision == 1
    assert plan.task('one', expected_revision=1).state == ('active' if fail_commit else 'blocked')
    assert JobQueue(project).get(job)['state'] == ('uncertain' if fail_commit else 'failed')
    with project.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT state FROM delta_runs WHERE job_id=?', (job,)).fetchone()[0] == ('running' if fail_commit else 'blocked')
        assert not connection.execute('SELECT 1 FROM delta_exits').fetchone()


def test_stdio_adapter_runs_the_exact_registered_recovery_actions(system):
    import asyncio
    import sys
    from datetime import timedelta
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _, job, effect, _ = prepare(system)
    engine, project, _ = system

    async def exercise():
        source = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src'
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                  '--project-id', project.project_id, '--permission', 'read', '--permission', 'admin'],
            env={'PYTHONPATH': str(source)})
        async with stdio_client(parameters) as (read, write), ClientSession(read, write,
                read_timeout_seconds=timedelta(seconds=15)) as client:
            await client.initialize()
            catalog = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert catalog['job_recovery_inspect'].annotations.readOnlyHint
            assert not catalog['job_reconcile_effect'].annotations.readOnlyHint
            inspected = await client.call_tool('job_recovery_inspect', {'project_id': project.project_id,
                                                                       'arguments': {'job_id': job}})
            assert not inspected.isError
            digest = inspected.structuredContent['result']['effects'][0]['effect_digest']
            recovered = await client.call_tool('job_reconcile_effect', {'project_id': project.project_id,
                'expected_revision': 1, 'arguments': {'job_id': job, 'effect_id': effect,
                    'expected_effect_digest': digest, 'plan_revision': 1}})
            assert not recovered.isError, recovered
            assert recovered.structuredContent['result']['job_state'] == 'failed'
    with LocalEndpoint(engine):
        asyncio.run(exercise())
