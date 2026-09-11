"""SQLAlchemy uses exact private images and the current attributed lane worker."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.data_toolchain import (
    DataInspectionRequest,
    inspect_sqlalchemy_sqlite,
    inspect_sqlalchemy_sqlite_bytes,
)
from evidence_lane_plugin.sector_evidence_profile import read_snapshot

from tests.test_evidence_sectors_v4 import execute, plan, sqlite_fixture
from tests.test_evidence_sectors_v4 import (
    system as system,  # noqa: PLC0414 - pytest fixture registration
)


def hashes(root):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in root.iterdir() if path.is_file()}


def request(path, **values):
    return DataInspectionRequest(source_path=path, host_profile='CODEX_CLI', **values)


def test_expected_hash_and_byte_bound_are_checked_before_sqlalchemy_opens(tmp_path, monkeypatch):
    import sqlalchemy
    path = tmp_path / 'source.sqlite'
    sqlite_fixture(path)
    before = hashes(tmp_path)
    opened = []
    monkeypatch.setattr(sqlalchemy, 'create_engine', lambda *_a, **_k: opened.append(True))
    for values, code in [({'expected_sha256': '0' * 64}, 'DATA_SOURCE_HASH_MISMATCH'),
                         ({'max_file_bytes': path.stat().st_size - 1}, 'DATA_SOURCE_BYTE_BUDGET')]:
        with pytest.raises(ValueError, match=code):
            inspect_sqlalchemy_sqlite(request(path, **values))
    assert not opened and hashes(tmp_path) == before


def test_actual_column_budget_is_enforced_without_reading_rows(tmp_path):
    path = tmp_path / 'source.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE source(a,b,c)')
    before = hashes(tmp_path)
    with pytest.raises(ValueError, match='SQLALCHEMY_COLUMN_BUDGET'):
        inspect_sqlalchemy_sqlite(request(path, max_columns=2))
    result = inspect_sqlalchemy_sqlite(request(path, max_columns=3, max_rows=1))
    assert len(result['tables'][0]['columns']) == 3
    assert not result['row_data_read'] and not result['max_rows_applicable']
    assert hashes(tmp_path) == before


def test_real_wal_only_schema_uses_composite_identity_and_preserves_all_source_files(tmp_path):
    path = tmp_path / 'live#source.sqlite'
    writer = sqlite3.connect(path)
    try:
        writer.execute('CREATE TABLE checkpointed(id INTEGER PRIMARY KEY)')
        writer.commit()
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        main_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        writer.execute('CREATE TABLE only_in_wal(body TEXT)')
        writer.execute("INSERT INTO only_in_wal VALUES('must be visible')")
        writer.commit()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == main_sha
        before = hashes(tmp_path)
        first = inspect_sqlalchemy_sqlite(request(path, expected_sha256=main_sha))
        assert [table['name'] for table in first['tables']] == ['checkpointed', 'only_in_wal']
        assert first['wal_present'] and not first['shared_memory_copied']
        assert first['source_sha256'] == main_sha and first['source_hash_verified_before_and_after']
        assert {member['role'] for member in first['source_members']} == {'main', '-wal'}
        assert hashes(tmp_path) == before
        writer.execute('CREATE TABLE later_wal_schema(value INTEGER)')
        writer.commit()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == main_sha
        second = inspect_sqlalchemy_sqlite(request(path, expected_sha256=main_sha))
        assert second['source_state_sha256'] != first['source_state_sha256']
        assert [table['name'] for table in second['tables']] == ['checkpointed', 'later_wal_schema', 'only_in_wal']
    finally:
        writer.close()


def test_snapshot_byte_budget_includes_wal_and_shared_memory(tmp_path):
    path = tmp_path / 'source.sqlite'
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE source(body TEXT)')
        writer.commit()
        before = hashes(tmp_path)
        with pytest.raises(ValueError, match='DATA_SOURCE_BYTE_BUDGET'):
            inspect_sqlalchemy_sqlite(request(path, max_file_bytes=path.stat().st_size))
        assert hashes(tmp_path) == before
    finally:
        writer.close()


@pytest.mark.parametrize('mutation', ['main', 'new_wal', 'replace_main'])
def test_changed_source_cannot_receive_a_success_receipt(tmp_path, monkeypatch, mutation):
    import sqlalchemy
    path = tmp_path / 'source.sqlite'
    # This fixture must release its own writer before testing path replacement.
    # A sqlite3 connection context commits but does not close the connection.
    created = sqlite3.connect(path)
    try:
        created.execute('CREATE TABLE source(id INTEGER PRIMARY KEY)')
        created.commit()
    finally:
        created.close()
    original = sqlalchemy.inspect
    touched = []

    def inspect_and_change(connection):
        result = original(connection)
        if mutation == 'new_wal':
            path.with_name(path.name + '-wal').write_bytes(b'new unadmitted state')
        elif mutation == 'replace_main':
            replacement = path.with_name('replacement.sqlite')
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        else:
            content = bytearray(path.read_bytes())
            content[60:64] = (99).to_bytes(4, 'big')
            path.write_bytes(content)
        touched.append(True)
        return result

    monkeypatch.setattr(sqlalchemy, 'inspect', inspect_and_change)
    with pytest.raises(ValueError, match='DATA_SOURCE_CHANGED_DURING_INSPECTION'):
        inspect_sqlalchemy_sqlite(request(path))
    assert touched


def test_byte_image_reflects_real_foreign_keys_indexes_views_and_types(tmp_path):
    path = tmp_path / 'source.sqlite'
    sqlite_fixture(path)
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE INDEX child_parent ON child(parent_id)')
        connection.execute('CREATE VIEW selected_view AS SELECT id FROM parent')
    raw = path.read_bytes()
    result = inspect_sqlalchemy_sqlite_bytes(raw)
    child = next(table for table in result['tables'] if table['name'] == 'child')
    assert child['foreign_keys'][0]['referred_table'] == 'parent'
    assert child['indexes'][0]['name'] == 'child_parent'
    assert result['views'] == ['claim', 'selected_view']
    assert result['reflection_complete'] and result['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert not result['row_data_read'] and path.read_bytes() == raw


def test_wal_header_without_associated_state_is_not_a_complete_byte_image(tmp_path):
    path = tmp_path / 'source.sqlite'
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE source(id)')
        writer.commit()
        with pytest.raises(ValueError, match='SQLALCHEMY_COMPLETE_BYTE_IMAGE_REQUIRED'):
            inspect_sqlalchemy_sqlite_bytes(path.read_bytes())
    finally:
        writer.close()


@pytest.mark.parametrize('selection', ['sqlalchemy', 'native_scope', 'native_missing'])
def test_current_delta_worker_records_admitted_schema_engine(system, monkeypatch, selection):
    engine, store, _session = system
    with_sqlalchemy = selection == 'sqlalchemy'
    if selection == 'native_missing':
        original_observer = engine.registry.tool_router.observer
        def unavailable(identity, context):
            if identity == 'SQLAlchemy':
                return {'tool_id': identity, 'ready': False, 'reason': 'DEPENDENCY_UNAVAILABLE'}
            return original_observer(identity, context)
        monkeypatch.setattr(engine.registry.tool_router, 'observer', unavailable)
    path = store.source_root / 'source.sqlite'
    sqlite_fixture(path)
    raw = path.read_bytes()
    tools = ('Python', 'SQLite_FTS5_BM25', *(('SQLAlchemy',) if selection != 'native_scope' else ()))
    plan(system, 'custom', ['custom_index'], tools=tools)
    result = execute(system, 'custom_index', {'filename': path.name, 'sqlite_tables': ['parent'], 'sqlite_rows': 1})
    manifest, facts = read_snapshot(store, 'custom', result['snapshot_id'])
    schema_engine = 'sqlalchemy' if with_sqlalchemy else 'stdlib'
    assert manifest['parse_options']['sqlite_schema_engine'] == schema_engine
    assert manifest['worker_fields']['evidence']['sqlite_schema_engine'] == schema_engine
    reflection = facts['fidelity']['schema_reflection']
    assert reflection['engine'] == ('SQLAlchemy' if with_sqlalchemy else 'sqlite3')
    assert reflection['source_sha256'] == hashlib.sha256(raw).hexdigest()
    assert len([item for item in facts['items'] if item['kind'] == 'sqlite_row']) == 1
    assert ('sqlalchemy' in manifest['worker_fields']['evidence']['versions']) is with_sqlalchemy
    assert Path(result['natural_path']).read_bytes() == raw and path.read_bytes() == raw
    with store.lane('plan').connection(read_only=True) as connection:
        admitted = connection.execute('SELECT entry_object FROM delta_runs WHERE job_id=?', (manifest['job_id'],)).fetchone()[0]
    entry = json.loads(store.lane('plan').read_object(admitted))
    assert entry['tool_admission']['route_id'] == 'custom_index.' + ('sqlite_sqlalchemy' if with_sqlalchemy else 'native')
    assert engine.workers.status()['succeeded_operations'] == 1


@pytest.mark.parametrize('kind,count,code', [
    ('TABLE', 129, 'SQLALCHEMY_TABLE_BUDGET'), ('VIEW', 513, 'SQLALCHEMY_SCHEMA_BUDGET'),
])
def test_metadata_enumeration_is_bounded_before_reflection(tmp_path, monkeypatch, kind, count, code):
    import sqlalchemy
    path = tmp_path / 'large-schema.sqlite'
    connection = sqlite3.connect(path)
    try:
        for ordinal in range(count):
            definition = '(id INTEGER)' if kind == 'TABLE' else 'AS SELECT 1 AS id'
            connection.execute(f'CREATE {kind} entity{ordinal} {definition}')
        connection.commit()
    finally:
        connection.close()
    called = []
    monkeypatch.setattr(sqlalchemy, 'inspect', lambda *_a, **_k: called.append(True))
    with pytest.raises(ValueError, match=code):
        inspect_sqlalchemy_sqlite_bytes(path.read_bytes())
    assert not called


def test_unsupported_expression_index_reflection_is_visible(tmp_path):
    path = tmp_path / 'expression.sqlite'
    connection = sqlite3.connect(path)
    try:
        connection.executescript('CREATE TABLE source(body TEXT); CREATE INDEX expression_idx ON source(lower(body));')
        connection.commit()
    finally:
        connection.close()
    result = inspect_sqlalchemy_sqlite_bytes(path.read_bytes())
    assert result['reflection_warning_count'] > 0 and not result['reflection_complete']
    assert result['tables'][0]['name'] == 'source'
