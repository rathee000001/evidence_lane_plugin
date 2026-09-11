from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.capture_routing import HOOK_EVENT_ORDER
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hook_contract import (
    hook_event_contract,
    hook_event_handler_path,
    hook_event_input_schema,
    hook_manifest,
    hook_registry,
    prepare_hook,
    submit_hook,
)
from evidence_lane_plugin.lineage import ChatLineage, LineageRead
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.sdk import EvidenceLaneClient

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'


def event(name):
    return {'hook_event_name': name, 'session_id': 'reported-session', 'turn_id': 'turn-1',
        'source': 'startup', 'reason': 'other', 'trigger': 'manual', 'prompt': 'A visible prompt',
        'agent_id': 'child-native-identity-fixture-8274', 'agent_type': 'worker',
        'agent_transcript_path': 'child-transcript-must-not-open',
        'tool_name': 'Bash', 'tool_use_id': 'call-1', 'tool_input': {'command': 'example', 'api_key': 'do-not-retain'},
        'tool_response': {'text': 'visible result'}, 'last_assistant_message': 'Visible response',
        'cwd': 'wrong-source-root', 'transcript_path': 'must-not-open', 'undocumented': 'must-not-retain'}


@pytest.fixture
def capture(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        source = tmp_path / 'source'
        source.mkdir()
        (source / 'unchanged.txt').write_text('keep source bytes')
        project = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(project['project_id'], write=True)
        with LocalTransport(engine.root, connection=ConnectRequest(projects=[
            ProjectSelection(project_id=store.project_id, permissions=['read', 'write'])])) as transport:
            response = EvidenceLaneClient(transport).call('capture_bind', project_id=store.project_id,
                arguments={'reported_session_id': 'reported-session'})
            assert response.status == 'ok'
            yield engine, store
        assert (source / 'unchanged.txt').read_text() == 'keep source bytes'


@pytest.mark.parametrize('name', HOOK_EVENT_ORDER)
def test_actual_packaged_hook_entrypoint_records_only_bound_visible_event(capture, name):
    engine, store = capture
    environment = os.environ.copy()
    environment['EVIDENCE_LANE_RUNTIME_ROOT'] = str(engine.root)
    deadline = hook_manifest()['hooks'][name][0]['hooks'][0]['timeout']
    process = subprocess.run([sys.executable, str(PLUGIN / hook_event_handler_path(name))],
        input=json.dumps(event(name)), env=environment, capture_output=True, text=True, timeout=deadline, check=False)
    assert process.returncode == 0, process.stdout + process.stderr
    assert json.loads(process.stdout) == {}
    page = ChatLineage(store).read(LineageRead())
    assert page.total_events == 1
    row = page.events[0]
    assert row['payload']['hook_event_name'] == name
    assert row['provenance'] == 'owner_hook_channel'
    content = json.dumps(row)
    if name in {'SubagentStart', 'SubagentStop'}:
        assert row['kind'] == 'session'
        assert row['reported_session_id'] == 'reported-session'
        assert row['payload']['agent_id_sha256'] == hashlib.sha256(event(name)['agent_id'].encode()).hexdigest()
        assert row['payload']['identity_provenance'] == 'reported_unverified'
        assert row['payload']['agent_type_reported'] == 'worker'
        assert 'child-native-identity-fixture-8274' not in content
        assert 'Visible response' not in content
    assert 'do-not-retain' not in content
    assert 'must-not-open' not in content and 'must-not-retain' not in content and 'wrong-source-root' not in content
    with store.connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='plan_current'").fetchone()


def test_unbound_hook_does_not_pick_a_project_and_delivery_identity_is_idempotent(capture):
    engine, store = capture
    before = store.database.read_bytes()
    raw = event('UserPromptSubmit')
    raw['session_id'] = 'not-bound'
    skipped = submit_hook(prepare_hook(json.dumps(raw).encode(), 'UserPromptSubmit'), engine.root)
    assert skipped == {'captured': False, 'reason': 'project_session_not_bound'}
    assert store.database.read_bytes() == before
    envelope = prepare_hook(json.dumps(event('UserPromptSubmit')).encode(), 'UserPromptSubmit')
    first = submit_hook(envelope, engine.root)
    repeated = submit_hook(envelope, engine.root)
    assert first['captured'] and repeated['result']['duplicate']
    assert ChatLineage(store).read(LineageRead()).total_events == 1


def test_hook_contract_rejects_mismatch_private_contents_and_excessive_payload():
    for data, expected in [(event('Stop'), 'SessionStart'), ({}, 'Stop')]:
        with pytest.raises(LaneError):
            prepare_hook(json.dumps(data).encode(), expected)
    with pytest.raises(LaneError) as error:
        prepare_hook(b' ' * 262145, 'Stop')
    assert error.value.code == 'HOOK_INPUT_BUDGET'
    data = event('PostToolUse')
    data['tool_response'] = {'hidden_reasoning': 'must never be captured'}
    with pytest.raises(LaneError) as error:
        prepare_hook(json.dumps(data).encode(), 'PostToolUse')
    assert error.value.code == 'VISIBLE_CONTENT_ONLY'


def test_generated_hook_manifest_uses_only_current_events_and_one_handler():
    assert json.loads((PLUGIN / 'hooks/hooks.json').read_text()) == hook_manifest()
    assert json.loads((PLUGIN / 'hooks/hook-event-registry.v4.json').read_text()) == hook_registry()
    assert tuple(hook_manifest()['hooks']) == HOOK_EVENT_ORDER
    assert {path.name for path in (PLUGIN / 'hooks/events').iterdir() if path.is_dir()} == set(HOOK_EVENT_ORDER)
    package_registry = json.loads((PLUGIN / 'hooks/events/event-package-registry.v4.json').read_text())
    assert package_registry['event_count'] == len(HOOK_EVENT_ORDER)
    for name, definition in hook_manifest()['hooks'].items():
        assert len(definition) == len(definition[0]['hooks']) == 1
        command = definition[0]['hooks'][0]
        assert hook_event_handler_path(name).replace('/', '\\') in command['commandWindows']
        assert 'subhook_' not in command['commandWindows'] and '.exe' not in command['commandWindows']
        folder = PLUGIN / 'hooks/events' / name
        assert {path.name for path in folder.iterdir()} == {
            'README.md', 'event.schema.json', 'event.v4.json', 'handler.py', 'pipeline.v4.json'}
        assert json.loads((folder / 'event.schema.json').read_text()) == hook_event_input_schema(name)
        contract = json.loads((folder / 'event.v4.json').read_text())
        assert all((PLUGIN / member['path']).is_file() for member in contract['members'])
        expected = hook_event_contract(name)
        assert all(contract[key] == value for key, value in expected.items())


@pytest.mark.parametrize('name', ['Stop', 'SubagentStart', 'SubagentStop', 'Interrupt', 'SessionEnd'])
def test_terminal_capture_checks_locked_policy_before_appending(capture, name, monkeypatch):
    engine, store = capture
    before = ChatLineage(store).read(LineageRead()).total_events

    def changed_policy(**kwargs):
        raise LaneError('SESSION_FLASH_MEMBER_HASH_MISMATCH', 'Injected policy change.')

    monkeypatch.setattr(engine.sessions.flash, 'verify', changed_policy)
    envelope = prepare_hook(json.dumps(event(name)).encode(), name)
    with pytest.raises(LaneError) as error:
        engine.capture.capture(envelope)
    assert error.value.code == 'SESSION_FLASH_MEMBER_HASH_MISMATCH'
    assert ChatLineage(store).read(LineageRead()).total_events == before
    with store.lane('plan').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone()
