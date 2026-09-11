# ruff: noqa: F811 -- pytest resolves explicitly imported fixtures by name.
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from evidence_lane_plugin.agent_learning import HostMemoryImport, LearningStore
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.project_memory import MemoryRead, ProjectMemory

from tests.test_code_profile_v4 import code_system, create_plan, execute  # noqa: F401
from tests.test_session_v4 import call, connect, selected  # noqa: F401 - shared real-engine fixture


@pytest.fixture
def planned(selected):
    engine, project, _, client = selected
    created = call(engine, project, client, 'plan_create', {'title': 'Host reference', 'tasks': [
        {'task_id': 'T1', 'title': 'Source-backed work', 'requested_outcome': 'Use selected evidence.'}]})
    assert created.status == 'ok', created
    task = PlanStore(project).task('T1', expected_revision=1)
    arguments = {'request_id': str(uuid4()), 'source_kind': 'CODEX_LOCAL_MEMORY',
        'source_locator': 'codex-local-memory://explicit-note/reference-1',
        'source_record_sha256': hashlib.sha256(b'Not supplied to the engine').hexdigest(),
        'source_context_id_sha256': hashlib.sha256(b'Private context identifier').hexdigest(),
        'observed_at': '2026-01-01T12:00:00+00:00', 'purpose': 'Attribute the selected source reference.',
        'task_id': 'T1', 'plan_revision': 1, 'contract_digest': task.contract_digest}
    return engine, project, client, arguments


def import_reference(planned, **changes):
    engine, project, client, arguments = planned
    return call(engine, project, client, 'learning_record_host_memory_import', {**arguments, **changes})


def test_receipt_and_memory_link_are_atomic_separate_from_learning_and_plan(planned):
    _, project, _, arguments = planned
    before = {row['lane_id']: row['head_digest'] for row in project.lane_catalog()}
    response = import_reference(planned)
    assert response.status == 'ok', response
    result = response.result
    assert result['source_identity_basis'] == 'caller_reported_hashes'
    assert not any(result[key] for key in ('source_bytes_read_or_verified', 'raw_host_memory_stored', 'learning_mutated', 'plan_mutated'))
    assert result['native_task_attestation'] == 'not_provided'
    assert result['source']['locator_sha256'] == hashlib.sha256(arguments['source_locator'].encode()).hexdigest()
    assert LearningStore(project).read().lessons == []
    assert PlanStore(project).task('T1', expected_revision=1).state == 'queued'
    after = {row['lane_id']: row['head_digest'] for row in project.lane_catalog()}
    assert {key for key in before if before[key] != after[key]} == {'memory', 'receipts'}
    page = ProjectMemory(project).read(MemoryRead(query='host provenance'))
    assert len(page.locators) == 1
    assert page.locators[0]['edges'][0]['edge']['kind'] == 'EVIDENCES'
    assert page.locators[0]['edges'][0]['semantics_provenance'] == 'agent_report'
    with project.lane('receipts').connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM receipts WHERE receipt_id=?', (result['receipt_id'],)).fetchone()
        body = json.loads(row['body_json'])
        assert body['import_context']['imported_by'] == planned[2].client_id
        assert body['source']['record_sha256'] == arguments['source_record_sha256']
        assert 'Private context identifier' not in row['body_json']
        assert connection.execute("SELECT owner FROM schema_ownership WHERE object_name='hostmemory_imports'").fetchone()[0] == 'hostmemory'
    for lane_id in ('plan', 'memory', 'learning'):
        with project.lane(lane_id).connection(read_only=True) as connection:
            assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='hostmemory_imports'").fetchone()


def test_replay_is_exact_and_attributed(planned):
    first = import_reference(planned)
    assert first.status == 'ok', first
    replay = import_reference(planned)
    assert replay.status == 'ok', replay
    assert replay.result == {**first.result, 'duplicate': True}
    assert import_reference(planned, purpose='Different reported purpose').error.code == 'HOST_MEMORY_REQUEST_CONFLICT'
    engine, project, _, arguments = planned
    _, other = connect(engine, project)
    different_actor = call(engine, project, other, 'learning_record_host_memory_import', arguments)
    assert different_actor.error.code == 'HOST_MEMORY_REQUEST_CONFLICT'
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='host_memory_import'").fetchone()[0] == 1


@pytest.mark.parametrize(('change', 'code'), [
    ({'plan_revision': 2}, 'STALE_PLAN_REVISION'),
    ({'task_id': 'absent'}, 'PLAN_TASK_NOT_FOUND'),
    ({'contract_digest': '0' * 64}, 'HOST_MEMORY_PLAN_CONTRACT_CHANGED'),
    ({'delta_job_id': '00000000-0000-4000-8000-000000000000'}, 'HOST_MEMORY_DELTA_MISMATCH'),
    ({'observed_at': (datetime.now(UTC) + timedelta(days=1)).isoformat()}, 'HOST_MEMORY_IMPORT_TIME_INVALID'),
])
def test_unverified_task_and_time_fail_without_provenance_or_memory(planned, change, code):
    response = import_reference(planned, **change)
    assert response.error.code == code, response
    project = planned[1]
    for lane, table in [('receipts', 'hostmemory_imports'), ('memory', 'memory_locators')]:
        with project.lane(lane).connection(read_only=True) as connection:
            assert not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (table,)).fetchone()


@pytest.mark.parametrize('change', [
    {'source_locator': 'file:///private/MEMORY.md'},
    {'source_kind': 'CHATGPT_SAVED_MEMORY'},
    {'source_locator': 'codex-local-memory://name:password@reference'},
    {'source_locator': 'codex-local-memory://reference?token=private'},
    {'source_locator': 'codex-local-memory://'},
    {'observed_at': '2026-01-01T12:00:00'},
    {'source_record_sha256': 'not-a-hash'},
    {'purpose': '   '},
    {'raw_memory': 'Private memory text'},
])
def test_provenance_contract_rejects_unsupported_or_sensitive_inputs(planned, change):
    with pytest.raises(ValueError):
        HostMemoryImport(**{**planned[3], **change})


def test_memory_failure_rolls_back_import_and_schema(planned, monkeypatch):
    project = planned[1]
    original = ProjectMemory.ingest
    def fail_after_link(*args, **kwargs):
        original(*args, **kwargs)
        raise LaneError('INJECTED_HOST_MEMORY_LINK_FAILURE', 'Fail after provisional reference and link writes.')
    monkeypatch.setattr(ProjectMemory, 'ingest', fail_after_link)
    response = import_reference(planned)
    assert response.error.code == 'INJECTED_HOST_MEMORY_LINK_FAILURE', response
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='host_memory_import'").fetchone()[0] == 0
        assert not connection.execute("SELECT 1 FROM schema_migrations WHERE owner='hostmemory'").fetchone()
    with project.lane('memory').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='memory_locators'").fetchone()
    monkeypatch.setattr(ProjectMemory, 'ingest', original)
    assert import_reference(planned).status == 'ok'


def test_read_permission_cannot_record_provenance(planned):
    engine, project, _, arguments = planned
    _, read_client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
        project_id=project.project_id, permissions=['read'])]))
    before = project.pv_head()
    response = call(engine, project, read_client, 'learning_record_host_memory_import', arguments)
    assert response.status == 'error'
    assert project.pv_head() == before


def test_corrupted_receipt_is_rejected_on_replay(planned):
    engine, project, _, _ = planned
    first = import_reference(planned)
    assert first.status == 'ok', first
    with engine.project_work.mutation(project) as lease, lease.transaction('receipts') as connection:
        connection.execute('UPDATE receipts SET body_json=? WHERE receipt_id=?',
            ('{"tampered":true}', first.result['receipt_id']))
    assert import_reference(planned).error.code == 'HOST_MEMORY_IMPORT_INTEGRITY'


def test_reference_to_real_verified_delta_preserves_its_existing_learning(code_system):
    engine, project, client = code_system
    create_plan(code_system)
    execute(code_system)
    before = LearningStore(project).read().model_dump()
    assert len(before['lessons']) == 1
    with project.lane('plan').connection(read_only=True) as connection:
        delta = dict(connection.execute('SELECT * FROM delta_runs').fetchone())
    arguments = {'request_id': str(uuid4()), 'source_kind': 'CHATGPT_CHAT_HISTORY_MEMORY',
        'source_locator': 'chatgpt-memory://explicit/history-reference',
        'source_record_sha256': 'a' * 64, 'source_context_id_sha256': 'b' * 64,
        'observed_at': '2026-01-01T07:00:00-05:00', 'purpose': 'Record a reference used in this task.',
        'task_id': delta['task_id'], 'plan_revision': delta['plan_revision'],
        'contract_digest': delta['contract_digest'], 'delta_job_id': delta['job_id']}
    response = call(engine, project, client, 'learning_record_host_memory_import', arguments)
    assert response.status == 'ok', response
    assert response.result['source']['observed_at'] == '2026-01-01T12:00:00+00:00'
    assert response.result['import_context']['delta_job_id'] == delta['job_id']
    assert LearningStore(project).read().model_dump() == before


def test_provenance_action_through_real_mcp_stdio_backend(planned):
    import asyncio
    import sys
    from pathlib import Path

    from evidence_lane_plugin.local_transport import LocalEndpoint
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    engine, project, _, arguments = planned
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable,
            args=['-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                  '--project-id', project.project_id, '--permission', 'write'],
            env={'PYTHONPATH': str(plugin / 'src')})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
            await session.initialize()
            result = await session.call_tool('learning_record_host_memory_import', {
                'project_id': project.project_id, 'arguments': arguments})
            response = result.structuredContent
            assert response and response['status'] == 'ok', result
            assert response['result']['source_identity_basis'] == 'caller_reported_hashes'
            assert not response['result']['learning_mutated']
            assert response['tool_execution']['env_uop']['action_name'] == 'learning_record_host_memory_import'
            assert response['tool_execution']['native_host_tool_attested'] is False

    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(exercise())
