from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from itertools import pairwise
from pathlib import Path

import pytest
from evidence_lane_plugin.capture_routing import HOOK_EVENT_ORDER
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hook_contract import (
    HOOK_PIPELINE,
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
    assert skipped['captured'] is False and skipped['reason'] == 'project_session_not_bound'
    assert skipped['handler_id'] == 'user-prompt.capture'
    assert skipped['transport'] == 'local_authenticated_capture'
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


def test_generated_hook_manifest_uses_current_events_and_four_visible_stage_handlers():
    assert json.loads((PLUGIN / 'hooks/hooks.json').read_text()) == hook_manifest()
    assert json.loads((PLUGIN / 'hooks/hook-event-registry.v4.json').read_text()) == hook_registry()
    assert tuple(hook_manifest()['hooks']) == HOOK_EVENT_ORDER
    assert {path.name for path in (PLUGIN / 'hooks/events').iterdir() if path.is_dir()} == set(HOOK_EVENT_ORDER)
    package_registry = json.loads((PLUGIN / 'hooks/events/event-package-registry.v4.json').read_text())
    assert package_registry['event_count'] == len(HOOK_EVENT_ORDER)
    visible_stages = ('VALIDATE', 'SEAL', 'TRANSPORT', 'EMIT')
    for name, definition in hook_manifest()['hooks'].items():
        assert len(definition) == 1 and len(definition[0]['hooks']) == 4
        for stage, command in zip(visible_stages, definition[0]['hooks'], strict=True):
            assert command['command'].endswith(f'hooks/runner.mjs" {name} {stage}')
            assert command['commandWindows'].endswith(f'hooks\\runner.mjs" {name} {stage}')
            assert 'subhook_' not in command['commandWindows'] and '.exe' not in command['commandWindows']
        folder = PLUGIN / 'hooks/events' / name
        stage_names = {f'{index:02d}-{stage["id"]}.stage.v4.json'
            for index, stage in enumerate(HOOK_PIPELINE, 1)}
        assert {path.name for path in folder.iterdir() if path.name != '__pycache__'} == {
            'README.md', 'event.schema.json', 'event.v4.json', 'handler.py', 'pipeline.v4.json', *stage_names}
        assert json.loads((folder / 'event.schema.json').read_text()) == hook_event_input_schema(name)
        contract = json.loads((folder / 'event.v4.json').read_text())
        assert all((PLUGIN / member['path']).is_file() for member in contract['members'])
        expected = hook_event_contract(name)
        assert all(contract[key] == value for key, value in expected.items())


def test_actual_host_visible_stage_processes_coordinate_one_delivery(capture, tmp_path):
    _engine, store = capture
    raw = json.dumps(event('UserPromptSubmit')).encode()
    environment = os.environ.copy()
    environment['EVIDENCE_LANE_RUNTIME_ROOT'] = str(_engine.root)
    environment['EVIDENCE_LANE_HOOK_PIPELINE_ROOT'] = str(tmp_path / 'hook-pipeline')
    stages = ('EMIT', 'TRANSPORT', 'SEAL', 'VALIDATE')
    processes = [subprocess.Popen(
        ['node', str(PLUGIN / 'hooks/runner.mjs'), 'UserPromptSubmit', stage],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
        for stage in stages]
    for process in processes:
        process.stdin.write(raw)
        process.stdin.close()
    outputs = {}
    for stage, process in zip(stages, processes, strict=True):
        assert process.wait(timeout=12) == 0, process.stderr.read().decode(errors='replace')
        outputs[stage] = json.loads(process.stdout.read())
    assert outputs['VALIDATE'] == outputs['SEAL'] == outputs['TRANSPORT'] == {}
    assert isinstance(outputs['EMIT'], dict)
    assert ChatLineage(store).read(LineageRead()).total_events == 1
    occurrence = next((tmp_path / 'hook-pipeline' / 'UserPromptSubmit').iterdir())
    receipts = [json.loads(path.read_text(encoding='utf-8')) for path in sorted(occurrence.glob('*.json'))]
    assert [row['stage'] for row in receipts] == ['VALIDATE', 'SEAL', 'TRANSPORT', 'EMIT']
    assert receipts[0]['previous_receipt_sha256'] is None
    assert all('raw_input_sha256' not in row and row['admitted_event_sha256'] for row in receipts)
    assert all(current['previous_receipt_sha256'] == prior['receipt_sha256']
        for prior, current in pairwise(receipts))
    for stage in ('VALIDATE', 'SEAL', 'TRANSPORT', 'EMIT'):
        replay = subprocess.run(['node', str(PLUGIN / 'hooks/runner.mjs'), 'UserPromptSubmit', stage],
            input=raw, capture_output=True, env=environment, timeout=12, check=False)
        assert replay.returncode == 0
    assert ChatLineage(store).read(LineageRead()).total_events == 1


def test_stage_identity_uses_redacted_event_and_handler_runs_once(tmp_path, monkeypatch):
    import evidence_lane_plugin.hook_stage_runtime as runtime
    from evidence_lane_plugin.hook_event_handlers import PreToolUseHandler

    control = tmp_path / 'hook-pipeline'
    monkeypatch.setenv('EVIDENCE_LANE_HOOK_PIPELINE_ROOT', str(control))
    calls = []
    original = PreToolUseHandler.handle.__func__

    def counted(cls, envelope, classification):
        calls.append(envelope.event_id)
        return original(cls, envelope, classification)

    monkeypatch.setattr(PreToolUseHandler, 'handle', classmethod(counted))
    monkeypatch.setattr(runtime, 'deliver_hook', lambda handled: {
        'captured': False, 'reason': 'project_session_not_bound',
        'transport': 'local_authenticated_capture', 'handler_id': handled.handler_id})
    first = event('PreToolUse')
    first['undocumented'] = 'private-one'
    first['tool_input']['api_key'] = 'secret-one'
    raw = json.dumps(first).encode()
    for stage in ('VALIDATE', 'SEAL', 'TRANSPORT', 'EMIT'):
        runtime.run_host_hook_stage(raw, 'PreToolUse', stage)
    assert len(calls) == 1
    occurrence_root = control / 'PreToolUse'
    assert len(list(occurrence_root.iterdir())) == 1
    receipts = [json.loads(path.read_text(encoding='utf-8'))
        for path in sorted(next(occurrence_root.iterdir()).glob('*.json'))]
    assert all('raw_input_sha256' not in row and row['admitted_event_sha256'] for row in receipts)

    same_admitted_event = event('PreToolUse')
    same_admitted_event['undocumented'] = 'private-two'
    same_admitted_event['tool_input']['api_key'] = 'secret-two'
    runtime.run_host_hook_stage(json.dumps(same_admitted_event).encode(), 'PreToolUse', 'VALIDATE')
    assert len(list(occurrence_root.iterdir())) == 1
    changed_visible_event = event('PreToolUse')
    changed_visible_event['tool_input']['command'] = 'different visible command'
    runtime.run_host_hook_stage(json.dumps(changed_visible_event).encode(), 'PreToolUse', 'VALIDATE')
    assert len(list(occurrence_root.iterdir())) == 2


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
