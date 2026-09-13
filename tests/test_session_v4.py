from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.flash_authority import SessionFlashAuthority
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.session_authority import SessionAuthority

from tests.storage_fixtures_v4 import declare_local_storage


@pytest.fixture
def selected(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_text('Immutable input for session tests.')
    declare_local_storage(tmp_path / 'runtime', tmp_path / 'state')
    with Engine(tmp_path / 'runtime') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project = engine.directory.open(entry['project_id'], write=True)
        token, client = connect(engine, project)
        yield engine, project, token, client


def connect(engine, project):
    return engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=project.project_id,
        permissions=['read', 'write'])]))


def call(engine, project, client, action, arguments=None, *, request_id=None):
    values = {'action': action, 'project_id': project.project_id, 'arguments': arguments or {}}
    if request_id:
        values['request_id'] = request_id
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(**values), client)


def boot(engine, project, client):
    return call(engine, project, client, 'session_boot', {'reported_session_id': 'host-reported-session',
        'expected_root_pv_digest': project.pv_head()['head_digest']})


def resume_args(project, result, **extra):
    return {'session_id': result['session_id'], 'expected_generation': result['generation'],
        'expected_event_digest': result['event_digest'], 'expected_root_pv_digest': project.pv_head()['head_digest'],
        'reported_session_id': 'resumed-host-session', **extra}


def test_boot_resume_exit_preserve_separate_state_and_exact_head(selected):
    engine, project, _, client = selected
    source_bytes = (project.source_root / 'input.txt').read_bytes()
    first = boot(engine, project, client)
    assert first.status == 'ok', first
    result = first.result
    assert result['state'] == 'active' and result['generation'] == 1 and result['capture_bound']
    assert result['native_task_attestation'] == 'not_provided' and not result['hook_execution_attested']
    assert result['root_pv']['reference_scope'] == 'before_session_writer_acquisition'
    assert boot(engine, project, client).error.code == 'ONE_SESSION_RULE_ACTIVE'
    replay = call(engine, project, client, 'session_boot', {'reported_session_id': 'host-reported-session',
        'expected_root_pv_digest': result['root_pv']['head_digest']}, request_id=first.request_id)
    assert replay.result == result
    resumed = call(engine, project, client, 'session_resume', resume_args(project, result))
    assert resumed.status == 'ok', resumed
    assert resumed.result['session_id'] == result['session_id'] and resumed.result['generation'] == 2
    assert not engine.capture.session_bound(client.client_id, project.project_id, 'host-reported-session')
    assert engine.capture.session_bound(client.client_id, project.project_id, 'resumed-host-session')
    assert call(engine, project, client, 'session_resume', resume_args(project, result)).error.code == 'SESSION_HEAD_CHANGED'
    current = resumed.result
    closed = call(engine, project, client, 'session_exit', {key: value for key, value in {
        'session_id': current['session_id'], 'expected_generation': current['generation'],
        'expected_event_digest': current['event_digest'], 'reason': 'Finished this session.'}.items()})
    assert closed.status == 'ok', closed
    assert closed.result['state'] == 'closed'
    assert not engine.capture.session_bound(client.client_id, project.project_id, 'resumed-host-session')
    assert (project.source_root / 'input.txt').read_bytes() == source_bytes
    with project.connection(read_only=True) as root:
        assert not root.execute("SELECT 1 FROM sqlite_schema WHERE name='sessions_records'").fetchone()
    with project.lane('sessions').connection(read_only=True) as sessions:
        assert sessions.execute('SELECT COUNT(*) FROM sessions_events').fetchone()[0] == 3
        assert sessions.execute("SELECT COUNT(*) FROM schema_migrations WHERE owner='sessions'").fetchone()[0] == 1
    assert boot(engine, project, client).status == 'ok'


def test_live_client_cannot_be_silently_taken_over_but_disconnected_owner_can_resume(selected):
    engine, project, token, client = selected
    first = boot(engine, project, client).result
    _, other = connect(engine, project)
    denied = call(engine, project, other, 'session_resume', resume_args(project, first))
    assert denied.error.code == 'SESSION_OWNER_ACTIVE'
    engine.clients.disconnect(token)
    accepted = call(engine, project, other, 'session_resume', resume_args(project, first))
    assert accepted.status == 'ok', accepted
    assert accepted.result['owner_client_id'] == other.client_id
    assert accepted.result['native_task_attestation'] == 'not_provided'


def test_stale_root_and_native_attestation_fail_before_session_or_capture(selected):
    engine, project, _, client = selected
    for arguments, code in [({'reported_session_id': 'test', 'expected_root_pv_digest': '0' * 64}, 'SESSION_ROOT_PV_CHANGED'),
        ({'reported_session_id': 'test', 'expected_root_pv_digest': project.pv_head()['head_digest'],
          'require_native_attestation': True}, 'NATIVE_TASK_ATTESTATION_UNAVAILABLE')]:
        assert call(engine, project, client, 'session_boot', arguments).error.code == code
    with project.lane('sessions').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sessions_records'").fetchone()
    assert not engine.capture.session_bound(client.client_id, project.project_id, 'test')


def test_failed_session_commit_does_not_publish_capture_or_schema(selected, monkeypatch):
    engine, project, _, client = selected
    original = SessionAuthority.append
    def fail_after_write(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise LaneError('INJECTED_SESSION_COMMIT_FAILURE', 'Injected failure after all provisional writes.')
    monkeypatch.setattr(SessionAuthority, 'append', fail_after_write)
    response = boot(engine, project, client)
    assert response.error.code == 'INJECTED_SESSION_COMMIT_FAILURE'
    assert not engine.capture.session_bound(client.client_id, project.project_id, 'host-reported-session')
    with project.lane('sessions').connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='sessions_records'").fetchone()
        history_table = connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='schema_history_files'"
        ).fetchone()
        assert history_table is None or not connection.execute(
            "SELECT 1 FROM schema_history_files WHERE owner='sessions'"
        ).fetchone()
    # The coordinator deliberately preserves immutable unregistered files after
    # abort. They are not admitted schema history; an exact retry can reuse them.
    monkeypatch.setattr(SessionAuthority, 'append', original)
    assert boot(engine, project, client).status == 'ok'


def test_locked_flash_rejects_changed_law_and_registry(tmp_path, selected):
    engine, _, _, _ = selected
    original = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    for role in ('env', 'uop'):
        shutil.copytree(original / role, tmp_path / 'copy' / role)
    manifest = json.loads((original / 'env/SESSION_FLASH_MANIFEST.json').read_bytes())
    for member in manifest['members']:
        if member['path'].startswith('toolchains/'):
            target = tmp_path / 'copy' / member['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original / member['path'], target)
    authority = SessionFlashAuthority(asset_root=tmp_path / 'copy')
    verified = authority.verify(registry=engine.registry)
    assert verified.member_count == len(json.loads((tmp_path / 'copy/env/SESSION_FLASH_MANIFEST.json').read_text())['members'])
    assert verified.context
    with (tmp_path / 'copy/env/env_law.md').open('a') as stream:
        stream.write('Changed runtime policy')
    with pytest.raises(LaneError) as error:
        authority.verify(registry=engine.registry)
    assert error.value.code == 'SESSION_FLASH_MEMBER_HASH_MISMATCH'
    assert hashlib.sha256((original / 'env/env_law.md').read_bytes()).hexdigest() != hashlib.sha256((tmp_path / 'copy/env/env_law.md').read_bytes()).hexdigest()


def test_session_status_is_readonly(selected):
    engine, project, _, client = selected
    assert boot(engine, project, client).status == 'ok'
    with project.lane('receipts').connection(read_only=True) as connection:
        count = connection.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
    before = project.pv_head()
    result = call(engine, project, client, 'session_status')
    assert result.status == 'ok' and result.result['capture_bound'] and result.result['flash_current']
    assert project.pv_head() == before
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] == count


def test_pending_semantic_steer_prevents_session_exit(selected):
    engine, project, _, client = selected
    opened = boot(engine, project, client).result
    created = call(engine, project, client, 'plan_create', {'title': 'Session boundary',
        'tasks': [{'task_id': 'one', 'title': 'One', 'requested_outcome': 'Preserve the pending steer.'}]})
    assert created.status == 'ok', created
    prompt = call(engine, project, client, 'lineage_record', {'kind': 'prompt',
        'payload': {'text': 'Change the requested output before proceeding.'}})
    assert prompt.status == 'ok', prompt
    pending = call(engine, project, client, 'steer_submit', {'source_event_id': prompt.result['event_id'],
        'source_cursor': prompt.result['cursor'], 'expected_revision': 1, 'intent': 'semantic',
        'affected_task_ids': ['one'], 'rationale': 'The requested output changed.'})
    assert pending.status == 'ok', pending
    refused = call(engine, project, client, 'session_exit', {'session_id': opened['session_id'],
        'expected_generation': opened['generation'], 'expected_event_digest': opened['event_digest'],
        'reason': 'Attempted close with pending work.'})
    assert refused.error.code == 'SESSION_STEER_PENDING'
    current = call(engine, project, client, 'session_status').result
    assert current['state'] == 'active' and current['event_digest'] == opened['event_digest']
