"""Complete registered SQLite states, alias verification and append-only history."""
from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from contextlib import contextmanager

import pytest
from evidence_lane_plugin import source_authority, source_sqlite
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.source_authority import SourceAuthoritySpec, register_source_batch
from evidence_lane_plugin.storage import ProjectStore


def database(path):
    connection = sqlite3.connect(path)
    try:
        connection.execute('CREATE TABLE checkpointed(id INTEGER PRIMARY KEY)')
        connection.commit()
    finally:
        connection.close()


def registry(tmp_path):
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    return ProjectStore.create(tmp_path / 'state', worktree).lane('sources')


def register(lane, *paths):
    return register_source_batch(lane, [SourceAuthoritySpec(source=str(path), ordinal=i, lane_id='custom')
        for i, path in enumerate(paths, 1)])['batch_id']


def hashes(directory):
    return {path.name: sha256_file(path) for path in directory.iterdir() if path.is_file()}


@contextmanager
def wal_pair(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    first, second = source / 'first.sqlite', source / 'second.sqlite'
    database(first)
    shutil.copyfile(first, second)
    writers = [sqlite3.connect(path) for path in (first, second)]
    try:
        for i, writer in enumerate(writers, 1):
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('PRAGMA wal_autocheckpoint=0')
            writer.execute(f'CREATE TABLE only_in_wal_{i}(body TEXT)')
            writer.execute(f"INSERT INTO only_in_wal_{i} VALUES('retained')")
            writer.commit()
        assert sha256_file(first) == sha256_file(second)
        yield source, first, second, writers
    finally:
        for writer in writers:
            writer.close()


def inspect(lane, batch, **kwargs):
    return source_sqlite.inspect_registered_sqlite_assets(lane, batch, **kwargs)


def receipts(lane):
    with lane.connection(read_only=True) as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT receipt_json FROM source_sqlite_receipt')]


def test_same_main_different_registered_wal_has_two_real_states(tmp_path):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (source, _, _, _):
        batch = register(lane, source)
        before = hashes(source)
        result = inspect(lane, batch)
        assert result['status'] == 'PASS', result
        assert result['unique_byte_authority_count'] == 1
        assert result['unique_source_state_count'] == result['canonical_inspection_count'] == 2
        with lane.connection(read_only=True) as connection:
            schemas = [(row[0], row[1]) for row in connection.execute(
                'SELECT member_path,object_name FROM source_sqlite_schema_object ORDER BY member_path,object_name')]
            counts = connection.execute("SELECT row_count FROM source_sqlite_table_stat WHERE table_name LIKE 'only_in_wal_%'").fetchall()
        assert schemas == [('first.sqlite', 'checkpointed'), ('first.sqlite', 'only_in_wal_1'),
            ('second.sqlite', 'checkpointed'), ('second.sqlite', 'only_in_wal_2')]
        assert [row[0] for row in counts] == [1, 1]
        assert hashes(source) == before
        measured = receipts(lane)
        assert len({row['source_state_sha256'] for row in measured}) == 2
        assert all(row['registered_members_verified'] and not row['shared_memory_copied'] for row in measured)
        assert inspect(lane, batch)['append_status'] == 'IDEMPOTENT_REUSE'


def test_identical_complete_state_deduplicates_only_metadata_work(tmp_path):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (_, first, _, _):
        copies = tmp_path / 'copies'
        copies.mkdir()
        for name in ('one.sqlite', 'two.sqlite'):
            shutil.copyfile(first, copies / name)
            shutil.copyfile(str(first) + '-wal', copies / (name + '-wal'))
        before = hashes(copies)
        batch = register(lane, copies)
        result = inspect(lane, batch)
        assert result['status'] == 'PASS' and result['canonical_inspection_count'] == 1
        assert result['batch_worker']['metadata_calls'] == 1 and hashes(copies) == before
        with lane.connection(read_only=True) as connection:
            assert {row[0] for row in connection.execute('SELECT inspection_state FROM source_sqlite_asset')} == {
                'CANONICAL_PASS', 'DEDUP_REUSE_PASS'}


@pytest.mark.parametrize('change', ['main', 'wal'])
def test_stale_alias_never_inherits_canonical_pass(tmp_path, change):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (_, first, _, _):
        copies = tmp_path / 'copies'
        copies.mkdir()
        for name in ('a.sqlite', 'z.sqlite'):
            shutil.copyfile(first, copies / name)
            shutil.copyfile(str(first) + '-wal', copies / (name + '-wal'))
        batch = register(lane, copies)
        target = copies / ('z.sqlite' + ('-wal' if change == 'wal' else ''))
        raw = target.read_bytes()
        target.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        before = hashes(copies)
        result = inspect(lane, batch)
        assert result['status'] == 'MISMATCH' and result['canonical_failure_count'] == 1
        assert result['canonical_pass_count'] == 1
        assert result['failure_samples'][0]['member_path'] == 'z.sqlite'
        assert hashes(copies) == before


def test_unregistered_adjacent_wal_is_rejected_without_reading_its_payload(tmp_path, monkeypatch):
    from evidence_lane_plugin import sqlite_execution
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (_, first, _, _):
        batch = register(lane, first)
        opened = []
        original = sqlite_execution.os.open
        def observed(path, *args, **kwargs):
            opened.append(str(path))
            return original(path, *args, **kwargs)
        monkeypatch.setattr(sqlite_execution.os, 'open', observed)
        asset = source_sqlite._registered_assets(lane, batch)[0]
        with pytest.raises(EvidenceLaneError, match='SOURCE_SQLITE_UNREGISTERED_SIDECAR'):
            source_sqlite._inspect_asset(asset, max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608)
        assert str(first) in opened
        assert str(first) + '-wal' not in opened


def test_explicit_file_sidecars_and_changed_wal_preserve_both_inspections(tmp_path):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (_, first, _, writers):
        from pathlib import Path
        wal = Path(str(first) + '-wal')
        first_batch = register(lane, first, wal)
        assert inspect(lane, first_batch)['status'] == 'PASS'
        previous = receipts(lane)
        main_sha = sha256_file(first)
        writers[0].execute('CREATE TABLE next_wal_revision(id INTEGER)')
        writers[0].commit()
        assert sha256_file(first) == main_sha
        second_batch = register(lane, first, wal)
        result = inspect(lane, second_batch)
        assert result['status'] == 'PASS' and result['append_status'] == 'APPENDED', result
        current = receipts(lane)
        assert len(current) == 2 and all(row in current for row in previous)
        with lane.connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM source_sqlite_asset').fetchone()[0] == 2
            assert connection.execute("SELECT count(*) FROM source_sqlite_schema_object WHERE object_name='next_wal_revision'").fetchone()[0] == 1


def test_registered_wal_missing_after_intake_is_a_visible_failure(tmp_path):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (_, first, _, _):
        copies = tmp_path / 'copies'
        copies.mkdir()
        shutil.copyfile(first, copies / 'one.sqlite')
        target = copies / 'one.sqlite-wal'
        shutil.copyfile(str(first) + '-wal', target)
        batch = register(lane, copies)
        target.unlink()
        result = inspect(lane, batch)
        assert result['canonical_pass_count'] == 0
        assert result['failure_samples'][0]['error_code'] == 'SOURCE_SQLITE_REGISTERED_MEMBER_MISSING'


def test_archive_wal_bundle_reads_current_schema_and_records_private_staging(tmp_path):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (_, first, _, _):
        archive = tmp_path / 'bundle.zip'
        with zipfile.ZipFile(archive, 'w') as output:
            output.write(first, 'nested/one.sqlite')
            output.write(str(first) + '-wal', 'nested/one.sqlite-wal')
        batch = register(lane, archive)
        before = sha256_file(archive)
        result = inspect(lane, batch)
        assert result['status'] == 'PASS', result
        assert result['source_payloads_extracted_to_filesystem']
        assert receipts(lane)[0]['temporary_source_image']
        with lane.connection(read_only=True) as connection:
            assert connection.execute("SELECT 1 FROM source_sqlite_schema_object WHERE object_name='only_in_wal_1'").fetchone()
        assert sha256_file(archive) == before and not (tmp_path / 'nested').exists()


def test_count_contract_change_appends_without_overwriting_earlier_receipt(tmp_path):
    lane = registry(tmp_path)
    path = tmp_path / 'source.sqlite'
    database(path)
    batch = register(lane, path)
    assert inspect(lane, batch, exact_count_max_database_bytes=1)['status'] == 'PASS'
    previous = receipts(lane)
    assert inspect(lane, batch)['status'] == 'PASS'
    assert len(receipts(lane)) == 2 and previous[0] in receipts(lane)
    with lane.connection(read_only=True) as connection:
        assert {row[0] for row in connection.execute('SELECT count_state FROM source_sqlite_table_stat')} == {
            'EXACT', 'DEFERRED_TO_DELTA075_SIZE_BOUND'}


def test_source_change_during_inspection_never_publishes_partial_schema(tmp_path, monkeypatch):
    lane = registry(tmp_path)
    path = tmp_path / 'source.sqlite'
    database(path)
    batch = register(lane, path)
    original = source_sqlite._inspect_connection
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
        return result
    monkeypatch.setattr(source_sqlite, '_inspect_connection', changed)
    result = source_sqlite._collect_sqlite_inspections(source_sqlite._registered_assets(lane, batch),
        max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608,
        batch_budget=source_sqlite.SQLiteBatchBudget())
    assert all(row['receipt']['status'] != 'PASS' and row['schema_objects'] == [] for row in result['inspections'].values())


def test_wal_bytes_count_toward_complete_source_budget(tmp_path):
    lane = registry(tmp_path)
    with wal_pair(tmp_path) as (source, first, _, _):
        batch = register(lane, source)
        before = hashes(source)
        result = inspect(lane, batch, max_embedded_member_bytes=first.stat().st_size)
        assert result['canonical_pass_count'] == 0
        assert all(row['error_code'] == 'DATA_SOURCE_BYTE_BUDGET' for row in result['failure_samples'])
        assert hashes(source) == before


def test_migration_preserves_legacy_receipt_and_all_original_row_values(tmp_path, monkeypatch):
    lane = registry(tmp_path)
    path = tmp_path / 'source.sqlite'
    database(path)
    with monkeypatch.context() as patch:
        patch.setattr(source_authority, 'SOURCES_MIGRATIONS', source_authority.SOURCES_MIGRATIONS[:1])
        batch = register(lane, path)
    with lane.transaction() as connection:
        object_id = connection.execute('SELECT object_id FROM source_object').fetchone()[0]
        connection.execute('INSERT INTO source_sqlite_receipt VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('sqlite_legacy', sha256_file(path), path.stat().st_size, 'legacy-fixture', 'PASS', '["ok"]',
                0, 0, 2, 4096, 'legacy-schema', 1, 1, 0, '{"fixture":"historical"}', 'legacy-receipt', 'historical'))
        connection.execute('INSERT INTO source_sqlite_asset VALUES (?,?,?,?,?,?,?,?)',
            (object_id, '<direct-file>', 'CANONICAL_PASS', 'legacy-schema', sha256_file(path),
                path.stat().st_size, 'sqlite_legacy', 'legacy-receipt'))
        connection.execute('INSERT INTO source_sqlite_schema_object VALUES (?,?,?,?,?)',
            (object_id, '<direct-file>', 'table', 'checkpointed', 'legacy-sql'))
        connection.execute('INSERT INTO source_sqlite_table_stat VALUES (?,?,?,?,?)',
            (object_id, '<direct-file>', 'checkpointed', 0, 'EXACT'))
        tables = ['source_sqlite_receipt', 'source_sqlite_asset', 'source_sqlite_schema_object', 'source_sqlite_table_stat']
        before = {table: [dict(row) for row in connection.execute('SELECT * FROM ' + table)] for table in tables}
    assert inspect(lane, batch)['status'] == 'PASS'
    with lane.connection(read_only=True) as connection:
        for table in tables:
            after = [dict(row) for row in connection.execute('SELECT * FROM ' + table)]
            assert all(any(all(new[key] == value for key, value in old.items()) for new in after) for old in before[table])
        assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
        assert connection.execute("SELECT count(*) FROM sqlite_schema WHERE name LIKE '%_previous'").fetchone()[0] == 0
