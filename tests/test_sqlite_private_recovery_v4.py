"""Real crash recovery stays in bounded private images and preserves sources."""
from __future__ import annotations

import json
import sqlite3
import struct
import sys
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin import bounded_io, source_sqlite
from evidence_lane_plugin.bounded_io import BoundedProcessResult, run_owned_bounded_process
from evidence_lane_plugin.data_toolchain import DataInspectionRequest, inspect_sqlalchemy_sqlite
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.source_authority import SourceAuthoritySpec, register_source_batch
from evidence_lane_plugin.sqlite_execution import private_sqlite_source
from evidence_lane_plugin.storage import ProjectStore


@pytest.fixture(scope='module')
def hot_parts(tmp_path_factory):
    directory = tmp_path_factory.mktemp('real-crash-journal')
    script = directory / 'fixture_writer.py'
    script.write_text('''import os, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA journal_mode=DELETE')
connection.execute('PRAGMA synchronous=FULL')
connection.execute('PRAGMA cache_size=4')
connection.execute('CREATE TABLE committed_payload(id INTEGER PRIMARY KEY, body BLOB)')
connection.executemany('INSERT INTO committed_payload VALUES(?,?)', [(i, b'committed'*180) for i in range(400)])
connection.commit()
connection.execute('BEGIN IMMEDIATE')
connection.execute('PRAGMA user_version=99')
connection.execute('CREATE TABLE uncommitted_table(value INTEGER)')
connection.execute('UPDATE committed_payload SET body=?', (b'uncommitted'*180,))
os._exit(0)
''')
    path = directory / 'fixture.sqlite'
    result = run_owned_bounded_process([sys.executable, '-I', '-S', '-B', str(script), str(path)],
        cwd=directory, timeout_seconds=30, max_stdout_bytes=16_384, max_stderr_bytes=16_384)
    assert result.returncode == 0, result.stderr
    journal = path.with_name(path.name + '-journal')
    assert journal.stat().st_size > 512
    return {'': path.read_bytes(), '-journal': journal.read_bytes()}


def source_fixture(tmp_path, parts):
    source = tmp_path / 'source'
    source.mkdir()
    path = source / 'selected.sqlite'
    for suffix, content in parts.items():
        path.with_name(path.name + suffix).write_bytes(content)
    return path


def hashes(path):
    return {member.name: sha256_file(member) for member in path.parent.iterdir() if member.is_file()}


@pytest.mark.parametrize('owner', ['private', 'sources', 'archive', 'sqlalchemy'])
def test_real_hot_journal_returns_committed_state_and_preserves_source(tmp_path, hot_parts, owner):
    path = source_fixture(tmp_path, hot_parts)
    if owner == 'archive':
        archive = path.parent / 'registered.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.write(path, 'bundle/selected.sqlite')
            output.write(str(path) + '-journal', 'bundle/selected.sqlite-journal')
    before = hashes(path)
    if owner == 'private':
        with private_sqlite_source(path, max_bytes=8_388_608) as (image, identity):
            connection = sqlite3.connect(image.as_uri() + '?mode=ro', uri=True)
            try:
                assert connection.execute('PRAGMA user_version').fetchone()[0] == 0
                assert connection.execute('SELECT body FROM committed_payload LIMIT 1').fetchone()[0] == b'committed' * 180
                assert connection.execute("SELECT name FROM sqlite_schema WHERE type='table'").fetchall() == [('committed_payload',)]
                connection.execute('PRAGMA query_only=OFF')
                with pytest.raises(sqlite3.OperationalError, match='readonly'):
                    connection.execute('CREATE TABLE forbidden(value INTEGER)')
            finally:
                connection.close()
            recovery = identity['private_recovery']
            assert recovery['recovered_sha256'] == sha256_file(image).lower()
        assert not image.exists()
    elif owner == 'sqlalchemy':
        result = inspect_sqlalchemy_sqlite(DataInspectionRequest(source_path=path, host_profile='CODEX_CLI', max_file_bytes=8_388_608))
        assert result['status'] == 'PASS'
        assert [table['name'] for table in result['tables']] == ['committed_payload']
        recovery = result['private_recovery']
    else:
        lane = ProjectStore.create(tmp_path / 'state', path.parent).lane('sources')
        selected = archive if owner == 'archive' else path.parent
        batch = register_source_batch(lane, [SourceAuthoritySpec(str(selected), 1, 'custom')])['batch_id']
        result = source_sqlite.inspect_registered_sqlite_assets(lane, batch)
        assert result['status'] == 'PASS', result
        with lane.connection(read_only=True) as connection:
            receipt = json.loads(connection.execute('SELECT receipt_json FROM source_sqlite_receipt').fetchone()[0])
            assert receipt['user_version'] == 0
            assert connection.execute('SELECT table_name,row_count FROM source_sqlite_table_stat').fetchone()[:] == ('committed_payload', 400)
            assert connection.execute("SELECT 1 FROM source_sqlite_schema_object WHERE object_name='uncommitted_table'").fetchone() is None
        recovery = receipt['private_recovery']
    assert recovery['status'] == 'ROLLED_BACK_PRIVATE_COPY'
    assert recovery['journal_pages'] > 0 and recovery['owned_worker_joined']
    assert not recovery['source_paths_received'] and not recovery['external_journal_reference_followed']
    assert hashes(path) == before


def test_hot_journal_restores_damaged_main_header_on_private_copy(tmp_path, hot_parts):
    parts = {**hot_parts, '': b'X' + hot_parts[''][1:]}
    path = source_fixture(tmp_path, parts)
    before = hashes(path)
    with private_sqlite_source(path, max_bytes=8_388_608) as (image, identity):
        assert image.read_bytes().startswith(b'SQLite format 3\x00')
        assert identity['source_sha256'] == before[path.name].lower()
        assert identity['private_recovery']['status'] == 'ROLLED_BACK_PRIVATE_COPY'
    assert hashes(path) == before


@pytest.mark.parametrize('damage,expected', [
    ('original_size', 'SQLITE_RECOVERY_JOURNAL_BOUNDS'),
    ('sector', 'SQLITE_RECOVERY_JOURNAL_BOUNDS'),
    ('page', 'SQLITE_RECOVERY_JOURNAL_BOUNDS'),
    ('page_reference', 'SQLITE_RECOVERY_JOURNAL_PAGE_BUDGET'),
    ('checksum', 'SQLITE_RECOVERY_JOURNAL_CHECKSUM'),
    ('truncation', 'SQLITE_RECOVERY_JOURNAL_TRUNCATED'),
    ('external', 'SQLITE_RECOVERY_EXTERNAL_JOURNAL_REFERENCE'),
])
def test_malformed_hot_journal_never_returns_a_recovered_image(tmp_path, hot_parts, damage, expected):
    journal = bytearray(hot_parts['-journal'])
    if damage == 'original_size':
        struct.pack_into('>I', journal, 16, 0xFFFFFFFE)
    elif damage == 'sector':
        struct.pack_into('>I', journal, 20, 0x40000000)
    elif damage == 'page':
        struct.pack_into('>I', journal, 24, 0x40000000)
    elif damage == 'page_reference':
        struct.pack_into('>I', journal, 512, 0xFFFFFFFE)
    elif damage == 'checksum':
        journal[512 + 4 + 4096] ^= 1
    elif damage == 'truncation':
        journal = journal[:700]
    else:
        sentinel = tmp_path / 'outside-mj1234569ab'
        sentinel.write_bytes(b'preserved external file')
        name = str(sentinel).encode()
        journal += name + struct.pack('>II', len(name), sum(name)) + bytes.fromhex('d9d505f920a163d7')
    path = source_fixture(tmp_path, {**hot_parts, '-journal': bytes(journal)})
    before = hashes(path)
    with pytest.raises(ValueError, match=expected), private_sqlite_source(path, max_bytes=8_388_608):
        raise AssertionError('Malformed journal was admitted')
    assert hashes(path) == before
    if damage == 'external':
        assert sentinel.read_bytes() == b'preserved external file'


def test_malformed_archive_journal_publishes_failure_without_partial_schema(tmp_path, hot_parts):
    journal = bytearray(hot_parts['-journal'])
    struct.pack_into('>I', journal, 16, 0xFFFFFFFE)
    source = tmp_path / 'source'
    source.mkdir()
    archive = source / 'invalid.zip'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('selected.sqlite', hot_parts[''])
        output.writestr('selected.sqlite-journal', journal)
    before = sha256_file(archive)
    lane = ProjectStore.create(tmp_path / 'state', source).lane('sources')
    batch = register_source_batch(lane, [SourceAuthoritySpec(str(archive), 1, 'custom')])['batch_id']
    result = source_sqlite.inspect_registered_sqlite_assets(lane, batch)
    assert result['canonical_pass_count'] == 0 and result['canonical_failure_count'] == 1
    assert result['failure_samples'][0]['error_code'] == 'SQLITE_RECOVERY_JOURNAL_BOUNDS'
    with lane.connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM source_sqlite_schema_object').fetchone()[0] == 0
    assert sha256_file(archive) == before


def test_real_owned_worker_timeout_cleans_private_directory(tmp_path, hot_parts, monkeypatch):
    path = source_fixture(tmp_path, hot_parts)
    before = hashes(path)
    original = bounded_io.run_owned_bounded_process
    directories = []
    def short_deadline(command, **kwargs):
        directories.append(Path(kwargs['cwd']))
        kwargs['timeout_seconds'] = 0.001
        return original(command, **kwargs)
    monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', short_deadline)
    with pytest.raises(LaneError) as failure, private_sqlite_source(path, max_bytes=8_388_608):
        raise AssertionError('Timed-out recovery was admitted')
    assert failure.value.code == 'BOUNDED_PROCESS_TIMEOUT'
    assert directories and all(not directory.exists() for directory in directories)
    assert hashes(path) == before


@pytest.mark.parametrize('result', [b'not-json', b'{"status":"ROLLED_BACK_PRIVATE_COPY","recovered_sha256":"wrong"}'])
def test_worker_output_must_match_actual_private_image(tmp_path, hot_parts, monkeypatch, result):
    path = source_fixture(tmp_path, hot_parts)
    before = hashes(path)
    directories = []
    def invalid_result(command, **kwargs):
        directories.append(Path(kwargs['cwd']))
        return BoundedProcessResult(0, result, b'')
    monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', invalid_result)
    with pytest.raises(ValueError, match='SQLITE_RECOVERY_RESULT_'), private_sqlite_source(path, max_bytes=8_388_608):
        raise AssertionError('Unverified worker output was admitted')
    assert directories and all(not directory.exists() for directory in directories)
    assert hashes(path) == before


def test_conflicting_wal_and_hot_journal_is_not_recovered(tmp_path, hot_parts):
    path = source_fixture(tmp_path, {**hot_parts, '-wal': b'conflicting fixture'})
    before = hashes(path)
    with pytest.raises(ValueError, match='SQLITE_RECOVERY_CONFLICTING_JOURNALS'), private_sqlite_source(path, max_bytes=8_388_608):
        raise AssertionError('Conflicting journal modes were admitted')
    assert hashes(path) == before


def test_committed_persist_journal_does_not_start_recovery(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    path = source / 'persist.sqlite'
    connection = sqlite3.connect(path)
    try:
        assert connection.execute('PRAGMA journal_mode=PERSIST').fetchone()[0] == 'persist'
        connection.execute('CREATE TABLE committed(value INTEGER)')
        connection.execute('INSERT INTO committed VALUES(1)')
        connection.commit()
    finally:
        connection.close()
    journal = path.with_name(path.name + '-journal')
    assert journal.is_file() and journal.read_bytes()[:1] == b'\x00'
    before = hashes(path)
    def unexpected(*args, **kwargs):
        raise AssertionError('A committed PERSIST journal started a recovery worker.')
    monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', unexpected)
    with private_sqlite_source(path, max_bytes=8_388_608) as (image, identity):
        assert identity['private_recovery'] == {'status': 'NOT_REQUIRED'}
        connection = sqlite3.connect(image.as_uri() + '?mode=ro', uri=True)
        try:
            assert connection.execute('SELECT value FROM committed').fetchone()[0] == 1
        finally:
            connection.close()
    assert hashes(path) == before
