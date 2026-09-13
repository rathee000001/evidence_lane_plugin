"""Synthetic hook observations preserve child metadata without agent control."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from evidence_lane_plugin.capture_routing import CaptureRouteAuthority, normalize_hook
from evidence_lane_plugin.codex_turn_control import TurnControl
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hook_contract import context_hook_output, prepare_hook
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.lineage import ChatLineage, LineageRead
from evidence_lane_plugin.plan_runtime import PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.writers import WriterLease

from tests.storage_fixtures_v4 import declare_local_storage


def native_event(name='SubagentStart', **extra):
    return {'hook_event_name': name, 'session_id': 'bound-parent-session',
            'turn_id': 'reported-parent-turn', 'agent_id': 'child-agent-identifier-4621',
            'agent_type': 'explorer', 'agent_transcript_path': 'child-private-transcript-5931',
            'last_assistant_message': 'child-visible-message-not-retained-4917',
            'stop_hook_active': True, 'private_reasoning': 'discarded-unselected-field-1051',
            **extra}


def envelope(value):
    return prepare_hook(json.dumps(value).encode(), value['hook_event_name'])


def hashes(root: Path):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


@pytest.fixture(params=['GOVERNED_PROJECT_FULL', 'ENV_BUILDER_SPARSE'])
def bound_observer(tmp_path, request):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_bytes(b'original source bytes')
    declare_local_storage(tmp_path / 'runtime', tmp_path / 'state')
    with Engine(tmp_path / 'runtime') as engine:
        registered = engine.directory.register(tmp_path / 'state', source_root=source,
            create=True, read_only=False, capture_route=request.param)
        project = engine.directory.open(registered['project_id'], write=True)
        _, client = engine.clients.connect(ConnectRequest(projects=[
            ProjectSelection(project_id=project.project_id, permissions=['read', 'write'])]))
        dispatcher = PublicActionSDKDispatcher(engine)
        task = TaskDefinition(task_id='retained-work', title='Retain active work',
            requested_outcome='Inspect original bytes.', acceptance_checks=['Verify original bytes.'])
        created = dispatcher.execute(ActionRequest(action='plan_create', project_id=project.project_id,
            arguments={'title': 'Observer boundary', 'tasks': [task.model_dump(mode='json')]}), client)
        assert created.status == 'ok', created.error
        with WriterLease(project, engine.instance_id) as lease:
            PlanStore(project).transition('retained-work', 'active', lease,
                expected_revision=1, actor_id=client.client_id)
        booted = dispatcher.execute(ActionRequest(action='session_boot', project_id=project.project_id,
            arguments={'reported_session_id': 'bound-parent-session',
                       'expected_root_pv_digest': project.pv_head()['head_digest']}), client)
        assert booted.status == 'ok', booted.error
        before = hashes(source)
        yield engine, project, client
        assert hashes(source) == before


def test_child_observations_remain_distinct_inert_and_bound_to_parent(bound_observer):
    engine, project, client = bound_observer
    before = hashes(project.root / 'plan')
    envelopes = [envelope(native_event(name, agent_id=agent))
                 for agent in ('child-one-identity-8271', 'child-two-identity-8271')
                 for name in ('SubagentStart', 'SubagentStop')]
    for entry in envelopes:
        assert 'agent_id' not in entry.event
        assert 'agent_transcript_path' not in entry.event
        assert 'last_assistant_message' not in entry.event
        result = engine.capture.capture(entry)
        assert not result.duplicate
        assert result.native_task_attestation == 'not_provided'
        assert result.turn_control['bounded_context'] is None
        assert result.turn_control['prepared_turn'] is None
        assert result.turn_control['compact'] is None
        assert not result.turn_control['terminal_exit']
        assert context_hook_output(entry, {'captured': True, 'result': result.model_dump(mode='json')}) == {}
        repeated = engine.capture.capture(entry)
        assert repeated.duplicate and repeated.cursor == result.cursor
        with project.lane('chat_lineage').connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM turn_events WHERE source_event_id=?', (entry.event_id,)).fetchone()
        body = TurnControl(engine, project)._row(row)
        assert not body['plan_changed'] and not body['execution_authorized']
        assert body['client_id'] == client.client_id
        assert body['reported_session_id'] == 'bound-parent-session'
    page = ChatLineage(project).read(LineageRead())
    assert page.total_events == 4
    assert [item['payload']['agent_id_sha256'] for item in page.events] == [
        hashlib.sha256(agent.encode()).hexdigest()
        for agent in ('child-one-identity-8271', 'child-two-identity-8271') for _ in range(2)]
    assert all(item['kind'] == 'session' and item['reported_session_id'] == 'bound-parent-session'
               and item['reported_turn_id'] == 'reported-parent-turn' for item in page.events)
    assert all(item['payload']['identity_provenance'] == 'reported_unverified'
               and not item['payload']['execution_authorized'] for item in page.events)
    assert hashes(project.root / 'plan') == before
    assert PlanStore(project).task('retained-work', expected_revision=1).state == 'active'
    assert CaptureRouteAuthority(project).verify()['decisions_verified'] == 4
    assert TurnControl(engine, project).verify_history()['events_verified'] == 4
    forbidden = ('child-one-identity-8271', 'child-two-identity-8271', 'child-private-transcript-5931',
                 'child-visible-message-not-retained-4917', 'discarded-unselected-field-1051')
    for path in project.root.rglob('*'):
        if path.is_file():
            data = path.read_bytes()
            assert all(value.encode() not in data for value in forbidden), path


def test_child_identity_never_selects_or_rebinds_a_parent_project(bound_observer):
    engine, project, client = bound_observer
    before = hashes(project.root / 'plan')
    changed = envelope(native_event(session_id='child-agent-identifier-4621'))
    with pytest.raises(LaneError) as error:
        engine.capture.capture(changed)
    assert error.value.code == 'CAPTURE_BINDING_REQUIRED'
    entry = envelope(native_event())
    with pytest.raises(LaneError) as error:
        engine.capture.capture(entry, expected_project_id='another-project')
    assert error.value.code == 'CAPTURE_OWNER_MISMATCH'
    with pytest.raises(LaneError) as error:
        engine.capture.capture(entry, expected_client_id='another-client')
    assert error.value.code == 'CAPTURE_OWNER_MISMATCH'
    assert engine.capture.session_bound(client.client_id, project.project_id, 'bound-parent-session')
    assert not engine.capture.session_bound(client.client_id, project.project_id, 'child-agent-identifier-4621')
    assert ChatLineage(project).read().total_events == 0
    assert hashes(project.root / 'plan') == before


@pytest.mark.parametrize('name', ['SubagentStart', 'SubagentStop'])
@pytest.mark.parametrize('field,value', [
    ('agent_id', None), ('agent_id', ''), ('agent_id', 'x' * 201), ('agent_id', 12), ('agent_id', 'a\x00b'),
    ('agent_type', None), ('agent_type', ''), ('agent_type', 'x' * 201), ('agent_type', []),
])
def test_observer_rejects_missing_or_unbounded_native_identity(name, field, value):
    with pytest.raises(LaneError) as error:
        envelope(native_event(name, **{field: value}, agent_id_sha256='a' * 64))
    assert error.value.code == 'HOOK_INPUT_INVALID'


def test_hash_only_native_input_cannot_forge_the_derived_transport_digest():
    value = native_event(agent_id_sha256='a' * 64)
    del value['agent_id']
    with pytest.raises(LaneError) as error:
        envelope(value)
    assert error.value.code == 'HOOK_INPUT_INVALID'


def test_identity_is_hashed_before_text_redaction_and_type_remains_redacted():
    first = 'api_key=synthetic-observer-identifier-001'
    second = 'api_key=synthetic-observer-identifier-002'
    rows = [envelope(native_event(agent_id=value, agent_type='password=synthetic-type-marker'))
            for value in (first, second)]
    assert rows[0].event['agent_id_sha256'] != rows[1].event['agent_id_sha256']
    for raw, entry in zip((first, second), rows, strict=True):
        assert entry.event['agent_id_sha256'] == hashlib.sha256(raw.encode()).hexdigest()
        assert 'synthetic-type-marker' not in json.dumps(entry.model_dump())
        normalized = normalize_hook(entry)
        assert normalized.payload['agent_id_sha256'] == entry.event['agent_id_sha256']


@pytest.mark.parametrize('digest', ['a' * 63, 'g' * 64, 'A' * 64, '', None, 1])
def test_capture_rejects_malformed_observer_transport(digest):
    entry = envelope(native_event())
    entry.event['agent_id_sha256'] = digest
    with pytest.raises(LaneError) as error:
        normalize_hook(entry)
    assert error.value.code == 'HOOK_INPUT_INVALID'
