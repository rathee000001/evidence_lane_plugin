"""The recorded selection binds classification, capture, Git and edit refresh."""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.source_authority import (
    SourceAuthoritySpec,
    freeze_source_authority,
    register_source_batch,
    verify_source_batch_unchanged,
)
from evidence_lane_plugin.source_selection import registered_directory_selection

from .test_code_profile_v4 import call, code_system, create_plan, execute
from .test_source_routing_v4 import enter, finished, registered

__all__ = ['code_system']


def git_fixture(source):
    for arguments in (['init', '-b', 'main'], ['config', 'user.name', 'Fixture'],
                      ['config', 'user.email', 'fixture@example.invalid'], ['config', 'core.autocrlf', 'false']):
        subprocess.run(['git', '-C', str(source), *arguments], check=True, capture_output=True)
    (source / '.gitignore').write_text('ignored/\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), 'commit', '-m', 'Fixture checkpoint'], check=True, capture_output=True)
    for relative in ('ignored/generated.py', '.work/generated.py', '.cache/cache.py', '.env.fixture'):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'passive fixture bytes')
    (source / 'untracked.py').write_bytes(b'answer = 42\n')


def registered_members(system, route):
    body = call(system, 'source_routes_read', {'route_id': route['route_id']}).result['result']
    source_id = body['routes'][0]['object_id']
    with system[1].lane('sources').connection(read_only=True) as connection:
        rows = [dict(row) for row in connection.execute('SELECT * FROM source_member WHERE object_id=? ORDER BY member_path',
            (source_id,))]
    return source_id, rows


def quiescent(system):
    with system[0]._admission:
        assert system[0]._admission.wait_for(lambda: system[0]._background_jobs == 0, timeout=30)


def test_git_classification_capture_and_reverify_select_the_same_files(code_system):
    source = code_system[1].source_root
    git_fixture(source)
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    classified = call(code_system, 'source_classify', {'sources': [str(source)]})
    assert classified.status == 'ok', classified.error
    directory = classified.result['result']['sources'][0]['source_identity']
    receipt = registered(code_system, ['.'])
    source_id, rows = registered_members(code_system, receipt)
    assert {row['member_path'] for row in rows} == {'app.py', 'helper.py', 'package.json', '.gitignore', 'untracked.py'}
    assert directory['member_count'] == len(rows)
    assert directory['total_bytes'] == sum(row['size_bytes'] for row in rows)
    assert registered_directory_selection(code_system[1], source_id) == 'GIT_INDEX_AND_SAFE_UNTRACKED'
    verified = call(code_system, 'source_verify', {'batch_id': receipt['batch_id']})
    assert verified.status == 'ok', verified.error
    assert before == {path.relative_to(source): path.read_bytes() for path in source.rglob('*') if path.is_file()}
    (source / 'ignored/generated.py').write_bytes(b'ignored bytes changed')
    (source / '.work/generated.py').write_bytes(b'temporary bytes changed')
    assert call(code_system, 'source_verify', {'batch_id': receipt['batch_id']}).status == 'ok'
    (source / 'new.py').write_bytes(b'newly selected = True\n')
    assert call(code_system, 'source_verify', {'batch_id': receipt['batch_id']}).error.code == 'SOURCE_AUTHORITY_BATCH_CHANGED'


def test_recorded_filesystem_selection_preserves_dot_directory_exclusions(code_system):
    source = code_system[1].source_root
    for relative in ('.work/tmp.py', '.cache/tmp.py', '.venv/tmp.py', 'evidence/historical.py'):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'not selected')
    receipt = registered(code_system, ['.'])
    source_id, rows = registered_members(code_system, receipt)
    assert {row['member_path'] for row in rows} == {'app.py', 'helper.py', 'package.json'}
    assert registered_directory_selection(code_system[1], source_id) == 'BOUNDED_FILESYSTEM_FALLBACK'
    subprocess.run(['git', '-C', str(source), 'init', '-b', 'main'], check=True, capture_output=True)
    assert call(code_system, 'source_verify', {'batch_id': receipt['batch_id']}).error.code == 'SOURCE_SELECTION_CHANGED'


def test_git_selection_does_not_silently_fall_back_when_git_is_unavailable(code_system, monkeypatch):
    from evidence_lane_plugin import source_intake
    git_fixture(code_system[1].source_root)
    receipt = registered(code_system, ['.'])
    monkeypatch.setattr(source_intake, '_git_directory_candidates', lambda path: None)
    assert call(code_system, 'source_verify', {'batch_id': receipt['batch_id']}).error.code == 'SOURCE_SELECTION_CHANGED'


def test_legacy_unselected_directory_identity_is_preserved(code_system):
    engine, store, _ = code_system
    work = store.source_root / '.work'
    work.mkdir()
    (work / 'historical.txt').write_bytes(b'previously admitted metadata')
    spec = SourceAuthoritySpec(str(store.source_root), 1, 'local_code')
    previous = freeze_source_authority(spec)
    with engine.project_work.mutation(store) as lease:
        receipt = register_source_batch(store, [spec], writer=lease)
    assert registered_directory_selection(store, previous.object_id) is None
    assert verify_source_batch_unchanged(store, receipt['batch_id'])['status'] == 'PASS'
    assert any(row['member_path'] == '.work/historical.txt' for row in previous.members)
    current = registered(code_system, ['.'])
    assert current['batch_id'] != receipt['batch_id']
    assert verify_source_batch_unchanged(store, receipt['batch_id'])['status'] == 'PASS'


def test_membership_change_between_classification_and_capture_aborts_registration(code_system, monkeypatch):
    from evidence_lane_plugin import source_intake
    original = source_intake._bounded_directory_members
    calls = 0
    def change_after_classification(path, **kwargs):
        nonlocal calls
        result = original(path, **kwargs)
        calls += 1
        if calls == 1:
            (path / 'arrived.py').write_bytes(b'new source')
        return result
    monkeypatch.setattr(source_intake, '_bounded_directory_members', change_after_classification)
    result = call(code_system, 'source_register', {'sources': [str(code_system[1].source_root)]})
    assert result.error.code == 'SOURCE_SELECTION_CHANGED'
    with code_system[1].lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='intake_batch'").fetchone() is None


def test_recorded_policy_tampering_cannot_change_capture_semantics(code_system):
    engine, store, _ = code_system
    receipt = registered(code_system, ['.'])
    source_id, _ = registered_members(code_system, receipt)
    with engine.project_work.mutation(store) as lease, lease.transaction('sources') as connection:
        row = connection.execute('SELECT receipt_json FROM source_policy_receipt WHERE object_id=?', (source_id,)).fetchone()
        body = json.loads(row[0])
        body['directory_selection'] = 'GIT_INDEX_AND_SAFE_UNTRACKED'
        connection.execute('UPDATE source_policy_receipt SET receipt_json=? WHERE object_id=?', (json.dumps(body), source_id))
    with pytest.raises(LaneError, match='integrity'):
        registered_directory_selection(store, source_id)


def test_git_history_and_code_edit_refresh_keep_the_recorded_selection(code_system):
    source = code_system[1].source_root
    git_fixture(source)
    receipt = registered(code_system, ['.'])
    history = call(code_system, 'source_git_history', {'batch_id': receipt['batch_id'], 'occurrence_ordinal': 1})
    assert history.status == 'ok', history.error
    create_plan(code_system, ('code_index', 'code_apply'))
    indexed = finished(code_system, enter(code_system, receipt['route_id']))['result']['result']
    quiescent(code_system)
    before = (source / 'app.py').read_bytes()
    replacement = 'from helper import greeting\n\ndef run():\n    return greeting("updated")\n'
    result = execute(code_system, 'code_apply', {'snapshot_id': indexed['snapshot_id'], 'filename': 'app.py',
        'expected_sha256': hashlib.sha256(before).hexdigest(), 'replacement_utf8': replacement}, index=1)
    quiescent(code_system)
    updated = result['source_refresh']
    assert call(code_system, 'source_verify', {'batch_id': updated['batch_id']}).status == 'ok'
    source_id, rows = registered_members(code_system, updated)
    assert registered_directory_selection(code_system[1], source_id) == 'GIT_INDEX_AND_SAFE_UNTRACKED'
    assert not any(row['member_path'].startswith(('.work/', 'ignored/')) for row in rows)
    assert (source / 'app.py').read_bytes() == replacement.encode()


def test_narrow_consumer_does_not_stat_other_directory_members(code_system, monkeypatch):
    from pathlib import Path
    source = code_system[1].source_root
    other = source / 'other'
    other.mkdir()
    (other / 'outside.py').write_bytes(b'def unselected(): return 1\n')
    receipt = registered(code_system, ['.'])
    create_plan(code_system)
    original = Path.stat
    def scoped_stat(path, **kwargs):
        assert not path.is_relative_to(other), 'The narrow consumer read an unrelated source directory.'
        return original(path, **kwargs)
    monkeypatch.setattr(Path, 'stat', scoped_stat)
    result = finished(code_system, enter(code_system, receipt['route_id'], arguments={'paths': ['app.py']}))
    quiescent(code_system)
    assert result['source_route']['input_file_count'] == 1
    assert result['result']['result']['files'] == 1


def test_same_size_change_during_directory_capture_cannot_publish_mixed_bytes(code_system, monkeypatch):
    from evidence_lane_plugin import source_authority
    original = source_authority._stable_file_identity
    first = None
    changed = False
    def mutate_during_later_file(path, capture=None):
        nonlocal first, changed
        result = original(path, capture)
        if first is None:
            first = path
        elif path != first and not changed:
            raw = first.read_bytes()
            first.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
            changed = True
        return result
    monkeypatch.setattr(source_authority, '_stable_file_identity', mutate_during_later_file)
    result = call(code_system, 'source_register', {'sources': [str(code_system[1].source_root)]})
    assert result.error.code == 'SOURCE_SELECTION_CHANGED'
    with code_system[1].lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='intake_batch'").fetchone() is None
