"""SQLite inspectors must open the selected filename and preserve its neighbors."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin.data_toolchain import DataInspectionRequest, inspect_sqlalchemy_sqlite
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.source_authority import SourceAuthoritySpec, register_source_batch
from evidence_lane_plugin.source_sqlite import (
    RegisteredSQLiteAsset,
    _open_direct,
    inspect_registered_sqlite_assets,
)
from evidence_lane_plugin.storage import ProjectStore


def database(path, table='expected_table'):
    with sqlite3.connect(path) as connection:
        connection.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY, body TEXT NOT NULL)')
        connection.execute(f"INSERT INTO {table} VALUES(1,'expected source contents')")


def snapshot(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in directory.iterdir() if path.is_file()}


@pytest.mark.parametrize('owner', ['sqlalchemy', 'registered_sources'])
@pytest.mark.parametrize('filename', ['literal#selected.sqlite', 'literal%23selected.sqlite', 'space name.sqlite', 'unicode-α.sqlite'])
def test_inspection_uses_exact_filename_and_preserves_every_source_file(tmp_path, owner, filename):
    source = tmp_path / 'source'
    source.mkdir()
    selected = source / filename
    database(selected)
    before = snapshot(source)
    if owner == 'sqlalchemy':
        result = inspect_sqlalchemy_sqlite(DataInspectionRequest(source_path=selected, host_profile='CODEX_CLI'))
        assert result['status'] == 'PASS' and result['read_only_uri']
        assert [row['name'] for row in result['tables']] == ['expected_table']
        assert not result['source_mutated']
    else:
        registry = ProjectStore.create(tmp_path / 'state', source).lane('sources')
        batch = register_source_batch(registry, [SourceAuthoritySpec(source=str(selected), ordinal=1, lane_id='custom')])
        result = inspect_registered_sqlite_assets(registry, batch['batch_id'])
        assert result['status'] == 'PASS' and result['canonical_pass_count'] == 1
        with registry.connection(read_only=True) as connection:
            names = [row[0] for row in connection.execute("SELECT object_name FROM source_sqlite_schema_object WHERE object_type='table'")]
        assert names == ['expected_table']
    assert snapshot(source) == before


@pytest.mark.parametrize('owner', ['sqlalchemy', 'registered_sources'])
@pytest.mark.parametrize('filename,decoy', [
    ('literal#selected.sqlite', 'literal'), ('literal%23selected.sqlite', 'literal#selected.sqlite'),
])
def test_existing_decoy_cannot_supply_schema_for_the_selected_byte_identity(tmp_path, owner, filename, decoy):
    source = tmp_path / 'source'
    source.mkdir()
    selected = source / filename
    database(selected)
    database(source / decoy, 'wrong_database_table')
    before = snapshot(source)
    if owner == 'sqlalchemy':
        result = inspect_sqlalchemy_sqlite(DataInspectionRequest(source_path=selected, host_profile='CODEX_CLI'))
        names = [row['name'] for row in result['tables']]
        assert result['source_sha256'] == sha256_file(selected).lower()
    else:
        registry = ProjectStore.create(tmp_path / 'state', source).lane('sources')
        batch = register_source_batch(registry, [SourceAuthoritySpec(source=str(selected), ordinal=1, lane_id='custom')])
        assert inspect_registered_sqlite_assets(registry, batch['batch_id'])['status'] == 'PASS'
        with registry.connection(read_only=True) as connection:
            names = [row[0] for row in connection.execute("SELECT object_name FROM source_sqlite_schema_object WHERE object_type='table'")]
    assert names == ['expected_table']
    assert snapshot(source) == before


def test_direct_connection_is_native_read_only_even_if_query_only_is_disabled(tmp_path):
    selected = tmp_path / 'literal#selected.sqlite'
    database(selected)
    before = snapshot(tmp_path)
    asset = RegisteredSQLiteAsset(ordinal=1, object_id='fixture-object', source_pointer=str(selected),
        source_kind='file', member_path='<direct-file>', size_bytes=selected.stat().st_size,
        byte_sha256=sha256_file(selected), policy_state='INCLUDED', policy_reason='fixture')
    with _open_direct(asset) as (connection, _):
        opened = connection.execute('PRAGMA database_list').fetchone()[2]
        assert Path(opened).resolve(strict=True) != selected.resolve(strict=True)
        assert Path(opened).read_bytes() == selected.read_bytes()
        connection.execute('PRAGMA query_only=OFF')
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            connection.execute('CREATE TABLE forbidden_write(id INTEGER)')
    assert snapshot(tmp_path) == before


def test_sqlalchemy_private_connection_is_native_read_only_even_without_query_only(tmp_path, monkeypatch):
    import sqlalchemy

    selected = tmp_path / 'literal#selected.sqlite'
    database(selected)
    before = snapshot(tmp_path)
    original = sqlalchemy.create_engine
    observed = []

    def checked_engine(*args, **kwargs):
        engine = original(*args, **kwargs)

        @sqlalchemy.event.listens_for(engine, 'connect')
        def inspect_connection(connection, _record):
            opened = connection.execute('PRAGMA database_list').fetchone()[2]
            assert Path(opened).resolve(strict=True) != selected.resolve(strict=True)
            assert sha256_file(Path(opened)) == sha256_file(selected)
            connection.execute('PRAGMA query_only=OFF')
            with pytest.raises(sqlite3.OperationalError, match='readonly'):
                connection.execute('CREATE TABLE forbidden_write(id INTEGER)')
            observed.append(opened)

        return engine

    monkeypatch.setattr(sqlalchemy, 'create_engine', checked_engine)
    result = inspect_sqlalchemy_sqlite(DataInspectionRequest(source_path=selected, host_profile='CODEX_CLI'))
    assert observed and [row['name'] for row in result['tables']] == ['expected_table']
    assert result['source_hash_verified_before_and_after']
    assert all(not Path(path).exists() for path in observed)
    assert snapshot(tmp_path) == before
