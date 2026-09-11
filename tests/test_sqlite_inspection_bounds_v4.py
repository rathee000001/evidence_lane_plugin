"""Exercise retained SQLite inspection limits through actual owned connections."""
from __future__ import annotations

import sqlite3
import zipfile
from contextlib import contextmanager

import pytest
from evidence_lane_plugin import source_sqlite
from evidence_lane_plugin.errors import EvidenceLaneError, LaneError
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.source_authority import SourceAuthoritySpec, register_source_batch
from evidence_lane_plugin.sqlite_execution import SQLiteInspectionBudget
from evidence_lane_plugin.storage import ProjectStore


def database(path, *, tables=1):
    connection = sqlite3.connect(path)
    try:
        for i in range(tables):
            connection.execute(f'CREATE TABLE t{i}(id INTEGER PRIMARY KEY, body TEXT)')
            connection.execute(f"INSERT INTO t{i} VALUES(1,'preserved')")
        connection.commit()
    finally:
        connection.close()


def registered(tmp_path, source):
    worktree = tmp_path / 'worktree'
    worktree.mkdir()
    registry = ProjectStore.create(tmp_path / 'state', worktree).lane('sources')
    batch = register_source_batch(registry, [SourceAuthoritySpec(source=str(source), ordinal=1, lane_id='custom')])
    return registry, batch['batch_id']


def test_vm_budget_interrupts_actual_sqlite_and_stays_exhausted():
    connection = sqlite3.connect(':memory:')
    budget = SQLiteInspectionBudget(max_vm_steps=1000)
    try:
        budget.configure(connection)
        with pytest.raises(sqlite3.OperationalError, match='interrupted'):
            connection.execute('WITH RECURSIVE r(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM r WHERE x<1000000000) SELECT sum(x) FROM r').fetchone()
        with pytest.raises(EvidenceLaneError, match='SQLITE_INSPECTION_EXECUTION_BUDGET'):
            budget.check()
        assert budget.exhausted and budget.vm_steps == 1000
    finally:
        connection.close()


def test_deadline_is_checked_even_without_a_thousand_vm_steps(monkeypatch):
    from evidence_lane_plugin import sqlite_execution
    clock = [100.0]
    monkeypatch.setattr(sqlite_execution.time, 'monotonic', lambda: clock[0])
    connection = sqlite3.connect(':memory:')
    budget = SQLiteInspectionBudget(timeout_seconds=1)
    try:
        budget.configure(connection)
        assert connection.execute('SELECT 1').fetchone()[0] == 1
        assert 0 < budget.vm_steps < 1000
        clock[0] = 101.0
        with pytest.raises(EvidenceLaneError, match='SQLITE_INSPECTION_EXECUTION_BUDGET'):
            budget.check()
    finally:
        connection.close()


@pytest.mark.parametrize('kwargs', [
    {'max_vm_steps': True}, {'max_vm_steps': 0}, {'max_vm_steps': 20_000_001},
    {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')},
    {'timeout_seconds': 0}, {'timeout_seconds': 16}, {'timeout_seconds': True},
])
def test_invalid_execution_budget_is_rejected(kwargs):
    with pytest.raises(ValueError, match='SQLITE_INSPECTION_BUDGET_INVALID'):
        SQLiteInspectionBudget(**kwargs)


def test_connection_controls_block_writes_attaches_and_extensions(tmp_path):
    connection = sqlite3.connect(':memory:')
    try:
        SQLiteInspectionBudget().configure(connection)
        assert connection.execute('PRAGMA query_only').fetchone()[0] == 1
        assert connection.execute('PRAGMA trusted_schema').fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('CREATE TABLE forbidden(id INTEGER)')
        with pytest.raises(sqlite3.OperationalError):
            connection.execute('ATTACH DATABASE ? AS forbidden', (str(tmp_path / 'forbidden.sqlite'),))
        with pytest.raises(sqlite3.OperationalError, match='not authorized'):
            connection.execute("SELECT load_extension('never-loaded')")
        assert not (tmp_path / 'forbidden.sqlite').exists()
    finally:
        connection.close()


def test_larger_replaced_archive_member_is_not_decompressed(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite'
    database(path)
    archive_path = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.write(path, 'source.sqlite')
    registry, batch = registered(tmp_path, archive_path)
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('source.sqlite', path.read_bytes() + bytes(1_048_576))
    before = sha256_file(archive_path)
    opened = []
    original = zipfile.ZipFile.open

    def observed_open(self, name, *args, **kwargs):
        opened.append(name)
        return original(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, 'open', observed_open)
    asset = source_sqlite._registered_assets(registry, batch)[0]
    with pytest.raises(EvidenceLaneError, match='SOURCE_SQLITE_ARCHIVE_CONTAINER_CHANGED'):
        source_sqlite._inspect_asset(asset, max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608)
    assert not opened and sha256_file(archive_path) == before


def test_unchanged_archive_uses_an_explicitly_bounded_stream(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite'
    database(path)
    archive_path = tmp_path / 'source.zip'
    with zipfile.ZipFile(archive_path, 'w') as archive:
        archive.write(path, 'source.sqlite')
    registry, batch = registered(tmp_path, archive_path)
    original = zipfile.ZipExtFile.read
    sizes = []

    def bounded_read(self, n=-1):
        sizes.append(n)
        assert 0 <= n <= path.stat().st_size + 1
        return original(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, 'read', bounded_read)
    asset = source_sqlite._registered_assets(registry, batch)[0]
    result = source_sqlite._inspect_asset(asset, max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608)
    assert result['receipt']['status'] == 'PASS' and sizes == [path.stat().st_size + 1]


def test_schema_exhaustion_is_a_failure_with_no_partial_schema_published(tmp_path):
    path = tmp_path / 'source.sqlite'
    database(path, tables=source_sqlite.MAX_SCHEMA_OBJECTS + 1)
    before = sha256_file(path)
    registry, batch = registered(tmp_path, path)
    result = source_sqlite.inspect_registered_sqlite_assets(registry, batch)
    assert result['canonical_pass_count'] == 0 and result['canonical_failure_count'] == 1
    assert result['failure_samples'][0]['error_code'] == 'SOURCE_SQLITE_SCHEMA_BUDGET'
    with registry.connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM source_sqlite_schema_object').fetchone()[0] == 0
    assert sha256_file(path) == before


def test_count_interrupt_cannot_be_labeled_a_successful_inspection(tmp_path, monkeypatch):
    path = tmp_path / 'source.sqlite'
    database(path)
    before = sha256_file(path)
    registry, batch = registered(tmp_path, path)

    class InterruptedCount(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith('SELECT COUNT(*)'):
                self.set_progress_handler(lambda: 1, 100)
                sql = 'WITH RECURSIVE r(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM r WHERE x<1000000000) SELECT sum(x) FROM r'
            return super().execute(sql, *args, **kwargs)

    @contextmanager
    def opened(_asset, **kwargs):
        connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, factory=InterruptedCount)
        try:
            yield connection, 'fixture_read_only'
        finally:
            connection.close()

    monkeypatch.setattr(source_sqlite, '_open_direct', opened)
    asset = source_sqlite._registered_assets(registry, batch)[0]
    with pytest.raises(sqlite3.OperationalError, match='interrupted'):
        source_sqlite._inspect_asset(asset, max_embedded_member_bytes=8_388_608, exact_count_max_database_bytes=8_388_608)
    assert sha256_file(path) == before


def test_registered_owner_reports_cumulative_execution_budget(tmp_path):
    path = tmp_path / 'source.sqlite'
    database(path, tables=40)
    before = sha256_file(path)
    registry, batch = registered(tmp_path, path)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(registry, batch, max_batch_vm_steps=1000)
    assert failure.value.code == 'SOURCE_SQLITE_BATCH_EXECUTION_BUDGET'
    assert sha256_file(path) == before
