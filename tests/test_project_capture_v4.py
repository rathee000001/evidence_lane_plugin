"""Immutable registration and sparse/full capture through current lane owners."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.capture_routing import (
    CAPTURE_ENVELOPE,
    CaptureRouteAuthority,
    HookEnvelope,
)
from evidence_lane_plugin.code_workers import code_worker_operations
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.lineage import ChatLineage, LineageRead, LineageRecord
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore, TaskDefinition
from evidence_lane_plugin.projects import ProjectDirectory
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.workers import WorkerPool
from evidence_lane_plugin.writers import WriterLease


def project_at(tmp_path, **options):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'original.txt').write_bytes(b'original source bytes')
    return ProjectStore.create(tmp_path / 'state', source, **options)


def append(project, record):
    with WriterLease(project, 'fixture-engine') as lease:
        return ChatLineage(project).append(record, lease, client_id='fixture-client')


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def assert_not_stored(project, text):
    for path in project.root.rglob('*'):
        if path.is_file():
            assert text.encode() not in path.read_bytes(), path


def test_registration_is_bound_before_capture_and_reopens_in_place(tmp_path):
    project = project_at(tmp_path, display_name='Solar evidence', sensitivity='CONFIDENTIAL', capture_route='env-builder-sparse')
    before = snapshot(project.root)
    directory = ProjectDirectory(tmp_path / 'engine')
    record = directory.register(project.root, display_name='Solar evidence')
    assert record['capture_route'] == 'ENV_BUILDER_SPARSE'
    assert record['sensitivity'] == 'CONFIDENTIAL' and record['registration_bound']
    assert record['sensitivity_enforcement'] == 'metadata_label_only'
    assert directory.record(project.project_id) == record
    assert snapshot(project.root) == before
    assert (project.source_root / 'original.txt').read_bytes() == b'original source bytes'


@pytest.mark.parametrize('option,value,code', [
    ('display_name', 'Changed', 'PROJECT_REGISTRATION_CONFLICT'),
    ('sensitivity', 'PUBLIC', 'PROJECT_REGISTRATION_CONFLICT'),
    ('capture_route', 'ENV_BUILDER_SPARSE', 'PROJECT_REGISTRATION_CONFLICT'),
])
def test_existing_registration_cannot_be_rebound(tmp_path, option, value, code):
    project = project_at(tmp_path)
    before = snapshot(project.root)
    with pytest.raises(LaneError) as error:
        ProjectDirectory(tmp_path / 'engine').register(project.root, **{option: value})
    assert error.value.code == code and snapshot(project.root) == before


@pytest.mark.parametrize('options', [
    {'display_name': ''}, {'display_name': ' '}, {'display_name': 'name\nline'}, {'display_name': 'x' * 161},
    {'sensitivity': 'probably safe'}, {'capture_route': 'unrestricted'},
])
def test_invalid_choices_create_no_project_storage(tmp_path, options):
    with pytest.raises(LaneError):
        project_at(tmp_path, **options)
    assert not (tmp_path / 'state').exists()


@pytest.mark.parametrize('mutation', [
    "UPDATE project_registration SET body_json='{}'", 'DELETE FROM project_registration', 'DROP TABLE project_registration',
])
def test_missing_or_changed_registration_is_not_treated_as_legacy(tmp_path, mutation):
    project = project_at(tmp_path)
    with sqlite3.connect(project.database) as db:
        db.execute(mutation)
    with pytest.raises(LaneError) as error:
        ProjectStore(project.root, read_only=True)
    assert error.value.code == 'PROJECT_REGISTRATION_INTEGRITY'


def test_earlier_v4_root_reads_without_invented_sensitivity_or_writes(tmp_path):
    project = project_at(tmp_path)
    # Reproduce the exact earlier v4 identity columns, with its other lanes intact.
    with sqlite3.connect(project.database) as db:
        db.execute('DROP TABLE project_registration')
        db.execute('ALTER TABLE project DROP COLUMN registration_digest')
    before = snapshot(project.root)
    reopened = ProjectStore(project.root, read_only=True)
    assert not reopened.registration['registration_bound']
    assert reopened.registration['sensitivity'] is None
    assert reopened.registration['capture_route'] == 'GOVERNED_PROJECT_FULL'
    directory = ProjectDirectory(tmp_path / 'engine')
    directory.register(project.root)
    with pytest.raises(LaneError) as error:
        directory.register(project.root, capture_route='ENV_BUILDER_SPARSE')
    assert error.value.code == 'PROJECT_REGISTRATION_UNBOUND'
    assert snapshot(project.root) == before


@pytest.mark.parametrize('kind', ['prompt', 'steer', 'assistant', 'tool_call', 'tool_result'])
def test_sparse_omits_payload_before_cas_fts_and_receipts(tmp_path, kind):
    project = project_at(tmp_path, capture_route='ENV_BUILDER_SPARSE')
    marker = 'uniquesparsesample7429'
    record = LineageRecord(kind=kind, payload={'text': marker, 'capture_kind': 'ACCEPTED_DELTA', 'accepted': True})
    result = append(project, record)
    payload = json.loads(project.lane('chat_lineage').read_object(result.payload_digest))
    assert set(payload) == {CAPTURE_ENVELOPE} and not payload[CAPTURE_ENVELOPE]['payload_included']
    assert ChatLineage(project).read(LineageRead(query=marker)).events == []
    assert ChatLineage(project).verify()['events_verified'] == 1
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1
    assert_not_stored(project, marker)


@pytest.mark.parametrize('kind', ['receipt', 'session', 'interrupt', 'handoff'])
def test_sparse_preserves_attributed_control_and_governed_unit_reports(tmp_path, kind):
    project = project_at(tmp_path, capture_route='ENV_BUILDER_SPARSE')
    append(project, LineageRecord(kind=kind, payload={'unit_kind': 'SCHEMA_DECISION', 'text': 'Attributed fixture report'}))
    item = ChatLineage(project).read().events[0]
    assert item['payload']['text'] == 'Attributed fixture report'
    assert item['provenance'] == 'agent_report' and item['native_task_attestation'] == 'not_provided'
    with project.lane('receipts').connection(read_only=True) as db:
        body = json.loads(db.execute('SELECT body_json FROM capture_decisions').fetchone()[0])
    assert body['payload_included'] and not body['event_content_authorizes_work']


def test_full_capture_redacts_and_replay_is_one_decision(tmp_path):
    project = project_at(tmp_path)
    record = LineageRecord(kind='prompt', payload={'text': 'Visible source', 'password': 'hidden-fixture-991'})
    first = append(project, record)
    assert append(project, record).duplicate
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1
    assert ChatLineage(project).read().events[0]['payload']['text'] == 'Visible source'
    assert_not_stored(project, 'hidden-fixture-991')
    with pytest.raises(LaneError) as error:
        append(project, record.model_copy(update={'payload': {'text': 'changed'}}))
    assert error.value.code == 'LINEAGE_EVENT_CONFLICT'
    assert first.sequence == 1


def test_untrusted_event_label_cannot_break_capture_or_become_control_metadata(tmp_path):
    project = project_at(tmp_path, capture_route='ENV_BUILDER_SPARSE')
    result = append(project, LineageRecord(kind='prompt', payload={'text': 'Visible input', 'hook_event_name': {'claim': 'Stop'}}))
    payload = json.loads(project.lane('chat_lineage').read_object(result.payload_digest))
    assert payload[CAPTURE_ENVELOPE]['hook_event_name'] is None
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1


def test_sparse_replay_hash_distinguishes_omitted_messages(tmp_path):
    project = project_at(tmp_path, capture_route='ENV_BUILDER_SPARSE')
    record = LineageRecord(kind='prompt', payload={'text': 'initial omitted text'})
    append(project, record)
    assert append(project, record).duplicate
    with pytest.raises(LaneError) as error:
        append(project, record.model_copy(update={'payload': {'text': 'different omitted text'}}))
    assert error.value.code == 'LINEAGE_EVENT_CONFLICT'
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1


def test_cross_project_claims_and_forged_capture_metadata_rejected(tmp_path):
    project = project_at(tmp_path)
    for payload, code in [({'nested': [{'project-id': str(uuid4())}]}, 'CAPTURE_PROJECT_MISMATCH'),
                          ({CAPTURE_ENVELOPE: {'text_digest': '0' * 64}}, 'CAPTURE_METADATA_RESERVED')]:
        with pytest.raises(LaneError) as error:
            append(project, LineageRecord(kind='receipt', payload=payload))
        assert error.value.code == code
    append(project, LineageRecord(kind='receipt', payload={'project_id': project.project_id, 'source_project_id': str(uuid4())}))
    assert ChatLineage(project).read().total_events == 1


def test_capture_decision_failure_rolls_back_lineage_and_receipt_rows(tmp_path, monkeypatch):
    project = project_at(tmp_path, capture_route='ENV_BUILDER_SPARSE')
    append(project, LineageRecord(kind='receipt', payload={'text': 'First'}))
    before = ChatLineage(project).read().current_cursor
    def fail(*args, **kwargs):
        raise LaneError('FIXTURE_CAPTURE_FAILURE', 'Injected before decision commit')
    monkeypatch.setattr(CaptureRouteAuthority, 'record', fail)
    with pytest.raises(LaneError):
        append(project, LineageRecord(kind='prompt', payload={'text': 'not committed'}))
    assert ChatLineage(project).read().current_cursor == before
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1


def test_capture_decisions_verify_chain_and_lineage_reference(tmp_path):
    project = project_at(tmp_path, capture_route='ENV_BUILDER_SPARSE')
    for text in ('one', 'two'):
        append(project, LineageRecord(kind='prompt', payload={'text': text}))
    authority = CaptureRouteAuthority(project)
    assert authority.verify()['decisions_verified'] == 2
    with pytest.raises(LaneError) as error:
        authority.verify(limit=1)
    assert error.value.code == 'CAPTURE_HISTORY_BUDGET'
    with project.lane('receipts').transaction() as db:
        db.execute("UPDATE capture_decisions SET previous_digest=NULL WHERE sequence=2")
    with pytest.raises(LaneError) as error:
        authority.verify()
    assert error.value.code == 'CAPTURE_DECISION_INTEGRITY'


def test_registration_has_no_runtime_update_route(tmp_path):
    project = project_at(tmp_path)
    with project.transaction() as db, pytest.raises(sqlite3.DatabaseError):
        db.execute("UPDATE project_registration SET body_json='{}'")
    assert ProjectStore(project.root).registration == project.registration


@pytest.fixture
def sparse_system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'small.py').write_text('number = 7\n')
    with Engine(tmp_path / 'engine', worker_pool=WorkerPool(code_worker_operations(), workers=1)) as engine:
        registered = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False,
                                              capture_route='ENV_BUILDER_SPARSE', display_name='Sparse project')
        project = engine.directory.open(registered['project_id'], write=True)
        _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id,
                                                                                    permissions=['read', 'write', 'tools'])]))
        yield engine, project, client


def call(system, action, arguments=None):
    engine, project, client = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=project.project_id,
                                                                  arguments=arguments or {}), client)


def test_sparse_hook_prompt_resupply_modes_and_steer_preserve_source_hash(sparse_system):
    engine, project, _client = sparse_system
    assert call(sparse_system, 'capture_bind', {'reported_session_id': 'sparse-session'}).result['capture_route'] == 'ENV_BUILDER_SPARSE'
    text = 'Sparseuniquevalue62941 classify the code work.'
    envelope = HookEnvelope(event_id=str(uuid4()), event={'hook_event_name': 'UserPromptSubmit',
        'session_id': 'sparse-session', 'turn_id': 'sparse-turn', 'prompt': text})
    result = engine.capture.capture(envelope)
    assert 'complete_visible_prompt_missing' not in result.turn_control['gaps']
    status = call(sparse_system, 'prompt_index_status').result['index']['entries'][0]
    assert status['classification_state'] == 'source_text_unavailable'
    args = {'classification_id': str(uuid4()), 'source_event_id': result.event_id, 'source_cursor': result.cursor,
            'intent': 'work', 'focus': 'Index a source', 'workflow': 'build', 'next_action': 'delta_enter',
            'lanes': ['local_code'], 'explicit_modes': ['CD']}
    assert call(sparse_system, 'task_classify', args).error.code == 'CAPTURE_SOURCE_RESUPPLY_REQUIRED'
    assert call(sparse_system, 'task_classify', {**args, 'source_text': 'wrong'}).error.code == 'CAPTURE_SOURCE_MISMATCH'
    classified = call(sparse_system, 'task_classify', {**args, 'source_text': text})
    assert classified.status == 'ok', classified.error
    assert classified.result['entry']['mode_selection']['request_omitted_by_capture_policy']
    spec = engine.registry.get('code_index')
    task = TaskDefinition(task_id='code-0', title='Index source', requested_outcome='Verified source index', profile='code',
        allowed_actions=['code_index'], permitted_paths=['.'], permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser'],
        acceptance_checks=list(spec.verification_checks), mode_binding=classified.result['task_mode_binding'])
    made = call(sparse_system, 'plan_create', {'title': 'Sparse work', 'tasks': [task.model_dump(mode='json')]})
    assert made.status == 'ok', made.error
    before = PlanStore(project).snapshot()
    steer = {'source_event_id': result.event_id, 'source_cursor': result.cursor, 'expected_revision': 1,
             'intent': 'semantic', 'rationale': 'Change pending source scope', 'affected_task_ids': ['code-0']}
    assert call(sparse_system, 'steer_preview', steer).error.code == 'CAPTURE_SOURCE_RESUPPLY_REQUIRED'
    preview = call(sparse_system, 'steer_preview', {**steer, 'source_text': text})
    assert preview.status == 'ok', preview.error
    assert preview.result['classification_basis'] == 'agent_interpretation_of_hash_verified_visible_source'
    queued = call(sparse_system, 'steer_submit', {**steer, 'source_text': text})
    assert queued.status == 'ok', queued.error
    assert PlanStore(project).snapshot().document_digest == before.document_digest
    assert_not_stored(project, text)
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1


def test_sparse_interrupt_remains_actionable_without_payload_resupply(sparse_system):
    engine, project, _ = sparse_system
    assert call(sparse_system, 'capture_bind', {'reported_session_id': 'stop-session'}).status == 'ok'
    result = engine.capture.capture(HookEnvelope(event_id=str(uuid4()), event={
        'hook_event_name': 'Interrupt', 'session_id': 'stop-session', 'turn_id': 'stop-turn'}))
    from evidence_lane_plugin.steering import Steering
    assert Steering(project).control()['paused']
    assert ChatLineage(project).read().events[0]['kind'] == 'interrupt'
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 1
    assert result.turn_control['terminal_exit'] is False


def test_sparse_mode_hash_survives_real_delta_execution_and_verified_exit(sparse_system):
    from tests.test_code_profile_v4 import execute
    from tests.test_task_mode_binding_v4 import task
    text = 'Sparseexecutionmarker624913 index the Python source.'
    source = call(sparse_system, 'lineage_record', {'kind': 'prompt', 'payload': {'text': text}})
    assert source.status == 'ok', source.error
    classified = call(sparse_system, 'task_classify', {'classification_id': str(uuid4()),
        'source_event_id': source.result['event_id'], 'source_cursor': source.result['cursor'],
        'source_text': text, 'intent': 'work', 'focus': 'Index selected code', 'workflow': 'build',
        'next_action': 'delta_enter', 'lanes': ['local_code'], 'explicit_modes': ['CD']})
    assert classified.status == 'ok', classified.error
    binding = classified.result['task_mode_binding']
    created = call(sparse_system, 'plan_create', {'title': 'Bound sparse execution',
        'tasks': [task(sparse_system, binding).model_dump(mode='json')]})
    assert created.status == 'ok', created.error
    result = execute(sparse_system)
    assert result['files'] == 1
    project = sparse_system[1]
    assert PlanStore(project).task('code-0', expected_revision=1).state == 'completed'
    with project.lane('receipts').connection(read_only=True) as db:
        exit_body = json.loads(db.execute("SELECT body_json FROM receipts WHERE kind='delta_exit_verified'").fetchone()[0])
    assert exit_body['task_mode']['classification_digest'] == binding['classification_digest']
    assert_not_stored(project, text)


def test_sparse_truncated_source_cannot_be_reclassified_as_complete(sparse_system):
    text = 'known bounded prefix'
    source = call(sparse_system, 'lineage_record', {'kind': 'prompt', 'payload': {'text': text, 'truncated': True}})
    assert source.status == 'ok', source.error
    response = call(sparse_system, 'task_classify', {'classification_id': str(uuid4()),
        'source_event_id': source.result['event_id'], 'source_cursor': source.result['cursor'], 'source_text': text,
        'intent': 'informational', 'focus': 'Inspect', 'workflow': 'plan', 'next_action': 'plan_read'})
    assert response.error.code == 'PROMPT_SOURCE_TRUNCATED'


def test_recovery_history_rejects_tampered_capture_decision(tmp_path):
    from evidence_lane_plugin.database_recovery import verify_authority_history
    project = project_at(tmp_path)
    append(project, LineageRecord(kind='prompt', payload={'text': 'read only recovery'}))
    verify_authority_history(project)
    with project.lane('receipts').transaction() as db:
        db.execute("UPDATE capture_decisions SET digest=?", ('0' * 64,))
    with pytest.raises(LaneError) as error:
        verify_authority_history(project)
    assert error.value.code == 'CAPTURE_DECISION_INTEGRITY'


def test_sparse_packaged_hook_process_uses_authenticated_delivery_without_storing_text(sparse_system, monkeypatch):
    engine, project, _ = sparse_system
    assert call(sparse_system, 'capture_bind', {'reported_session_id': 'packaged-sparse'}).status == 'ok'
    launcher = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/hooks/invoke_hook.py'
    environment = {**os.environ, 'EVIDENCE_LANE_RUNTIME_ROOT': str(engine.root)}
    environment.pop('EVIDENCE_LANE_REMOTE_CONFIG', None)
    marker = 'Sparsepackagedvisiblemessage926183'
    observed = []
    capture = engine.capture.capture
    def observe(*args, **kwargs):
        started = time.monotonic()
        try:
            value = capture(*args, **kwargs)
        except LaneError as error:
            observed.append({'code': error.code, 'seconds': time.monotonic() - started})
            raise
        observed.append({'event_id': value.event_id, 'seconds': time.monotonic() - started})
        return value
    monkeypatch.setattr(engine.capture, 'capture', observe)
    with LocalEndpoint(engine, studio_enabled=False):
        for event, field in [('UserPromptSubmit', 'prompt'), ('Stop', 'last_assistant_message')]:
            result = subprocess.run([sys.executable, str(launcher), '--event', event],
                input=json.dumps({'hook_event_name': event, 'session_id': 'packaged-sparse', 'turn_id': 'one', field: marker}),
                text=True, encoding='utf-8', capture_output=True, env=environment, timeout=10, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            assert result.returncode == 0, (result.stdout + result.stderr, observed)
            assert json.loads(result.stdout) == {}
    events = ChatLineage(project).read().events
    assert [row['kind'] for row in events] == ['prompt', 'assistant']
    assert all(row['provenance'] == 'owner_hook_channel' for row in events)
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 2
    assert_not_stored(project, marker)
