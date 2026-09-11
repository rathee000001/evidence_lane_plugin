"""Real APSW verification on committed lanes and the existing writer/undo boundary."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing, contextmanager

import apsw
import pytest
from evidence_lane_plugin import sqlite_execution
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import EvidenceLaneError, LaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes, sha256_file
from evidence_lane_plugin.lane_transactions import coordinated_transaction
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.storage import ProjectStore

from .test_native_workflow_bindings import call, native, projects


def prepared(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    store = ProjectStore.create(tmp_path / 'state', source)
    with coordinated_transaction(store, ['memory']) as commit:
        connection = commit.connection('memory')
        connection.execute('CREATE TABLE owned_items(value INTEGER)')
        connection.execute('CREATE INDEX owned_items_idx ON owned_items(value)')
        connection.execute('INSERT INTO owned_items VALUES(1)')
    return store


def journal(store, commit_id):
    with store.connection(read_only=True) as connection:
        row = connection.execute('SELECT phase,body_json FROM root_transaction_journal WHERE commit_id=?', (commit_id,)).fetchone()
    return row[0], json.loads(row[1])


def published(store):
    with store.connection(read_only=True) as connection:
        root = tuple(connection.execute('SELECT revision,head_digest,commit_id FROM root_pv_head').fetchone())
        heads = [tuple(row) for row in connection.execute('SELECT * FROM root_lane_heads ORDER BY lane_id')]
    return root, heads, {lane_id: sha256_file(store.lane(lane_id).database) for lane_id in ('memory', 'receipts')}


def test_actual_apsw_and_owning_optimizer_produce_exact_published_lane_evidence(tmp_path, monkeypatch):
    store = prepared(tmp_path)
    original = sqlite_execution.optimize_owned_sqlite_lane
    observations = []
    def observed(commit, lane_id):
        connection = commit.connection(lane_id)
        statements = []
        connection.set_trace_callback(statements.append)
        assert connection.in_transaction
        result = original(commit, lane_id)
        assert commit.connection(lane_id) is connection and connection.in_transaction
        assert any(statement == 'PRAGMA optimize' for statement in statements)
        observations.append(lane_id)
        return result
    monkeypatch.setattr(sqlite_execution, 'optimize_owned_sqlite_lane', observed)
    with coordinated_transaction(store, ['memory']) as commit:
        commit.connection('memory').execute('INSERT INTO owned_items VALUES(2)')
    phase, body = journal(store, commit.commit_id)
    assert phase == commit.phase == 'published' and set(observations) == {'memory', 'receipts'}
    for lane_id, execution in body['sqlite_execution'].items():
        result = execution['validation']
        assert result['engine'] == 'APSW' and result['engine_version'] == apsw.apswversion()
        assert result['sqlite_version'] == apsw.sqlitelibversion()
        assert result['native_read_only'] and result['phase'] == 'COMMITTED_UNPUBLISHED'
        assert result['project_id'] == store.project_id and result['lane_id'] == lane_id
        assert result['commit_id'] == commit.commit_id and result['status'] == 'PASS'
        assert result['database_sha256'] == sha256_file(store.lane(lane_id).database).lower()
        assert result['receipt_sha256'] == sha256_bytes(canonical_json_bytes({
            key: value for key, value in result.items() if key != 'receipt_sha256'})).lower()
        assert execution['optimization']['existing_write_connection']
        assert not execution['optimization']['transaction_committed_by_helper']
        assert result['vm_steps'] > 0 and result['vm_steps'] < result['max_vm_steps']


@pytest.mark.parametrize('fallback', [False, True])
def test_committed_reader_stays_native_read_only_after_query_only_is_disabled(tmp_path, monkeypatch, fallback):
    store = prepared(tmp_path)
    if fallback:
        monkeypatch.setattr(sqlite_execution, 'apsw_available', lambda: False)
    path = store.lane('memory').database
    before = sha256_file(path)
    with sqlite_execution._committed_lane_reader(path, sqlite_execution.SQLiteInspectionBudget()) as (reader, engine):
        assert engine['engine'] == ('STDLIB_SQLITE3' if fallback else 'APSW')
        reader.execute('PRAGMA query_only=OFF')
        with pytest.raises((apsw.ReadOnlyError, sqlite3.OperationalError)):
            reader.execute('INSERT INTO owned_items VALUES(99)')
    assert sha256_file(path) == before


def test_actual_stdlib_fallback_is_attributed_in_published_evidence(tmp_path, monkeypatch):
    store = prepared(tmp_path)
    monkeypatch.setattr(sqlite_execution, 'apsw_available', lambda: False)
    with coordinated_transaction(store, ['memory']) as commit:
        commit.connection('memory').execute('INSERT INTO owned_items VALUES(2)')
    phase, body = journal(store, commit.commit_id)
    assert phase == 'published'
    assert all(row['validation']['engine'] == 'STDLIB_SQLITE3' for row in body['sqlite_execution'].values())


def test_helpers_reject_missing_commit_or_wrong_phase_without_publication(tmp_path):
    store = prepared(tmp_path)
    with pytest.raises(LaneError) as failure:
        sqlite_execution.optimize_owned_sqlite_lane(None, 'memory')
    assert failure.value.code == 'SQLITE_COMMIT_OWNER_REQUIRED'
    with coordinated_transaction(store, ['memory']) as commit:
        with pytest.raises(LaneError) as failure:
            sqlite_execution.verify_committed_sqlite_lane(commit, 'memory')
        assert failure.value.code == 'SQLITE_COMMIT_OWNER_REQUIRED'
        with pytest.raises(LaneError) as failure:
            sqlite_execution.optimize_owned_sqlite_lane(commit, 'canon')
        assert failure.value.code == 'LANE_NOT_ENLISTED'
    with pytest.raises(LaneError) as failure:
        sqlite_execution.optimize_owned_sqlite_lane(commit, 'memory')
    assert failure.value.code == 'SQLITE_COMMIT_OWNER_REQUIRED'


@pytest.mark.parametrize('when', ['committed:memory', 'validated:memory', 'before_root_publish'])
def test_failure_at_each_validation_publication_boundary_restores_exact_lane_bytes(tmp_path, when):
    store = prepared(tmp_path)
    before = published(store)
    def fail(phase):
        if phase == when:
            raise RuntimeError('injected failure at ' + when)
    with pytest.raises(RuntimeError, match='injected failure'), coordinated_transaction(store, ['memory'], fault=fail) as commit:
        commit.connection('memory').execute('INSERT INTO owned_items VALUES(2)')
    assert commit.phase == 'aborted' and published(store) == before
    assert journal(store, commit.commit_id)[0] == 'aborted'


def test_apsw_failure_cannot_fall_back_or_publish(tmp_path, monkeypatch):
    store = prepared(tmp_path)
    before = published(store)
    @contextmanager
    def failed_reader(*args, **kwargs):
        raise apsw.CorruptError('actual reader failure')
        yield
    monkeypatch.setattr(sqlite_execution, '_committed_lane_reader', failed_reader)
    with pytest.raises(apsw.CorruptError), coordinated_transaction(store, ['memory']):
        pass
    assert published(store) == before


@pytest.mark.parametrize('damage', ['identity', 'after_validation'])
def test_changed_committed_bytes_are_rejected_and_recovered(tmp_path, monkeypatch, damage):
    store = prepared(tmp_path)
    before = published(store)
    original = sqlite_execution.verify_committed_sqlite_lane
    def damaged(commit, lane_id):
        if lane_id != 'memory':
            return original(commit, lane_id)
        path = commit.lanes[lane_id].database
        if damage == 'after_validation':
            result = original(commit, lane_id)
        with closing(sqlite3.connect(path)) as unowned:
            if damage == 'identity':
                unowned.execute("UPDATE lane_identity SET lane_id='canon'")
            else:
                unowned.execute('INSERT INTO owned_items VALUES(999)')
            unowned.commit()
        return original(commit, lane_id) if damage == 'identity' else result
    monkeypatch.setattr(sqlite_execution, 'verify_committed_sqlite_lane', damaged)
    with pytest.raises(LaneError) as failure, coordinated_transaction(store, ['memory']):
        pass
    assert failure.value.code == ('LANE_IDENTITY_MISMATCH' if damage == 'identity' else 'SQLITE_COMMITTED_LANE_CHANGED')
    assert published(store) == before


def test_apsw_vm_budget_failure_recovers_without_publishing(tmp_path, monkeypatch):
    store = prepared(tmp_path)
    before = published(store)
    original = sqlite_execution.verify_committed_sqlite_lane
    budget_class = sqlite_execution.SQLiteInspectionBudget
    def limited(commit, lane_id):
        with monkeypatch.context() as patch:
            patch.setattr(sqlite_execution, 'SQLiteInspectionBudget', lambda: budget_class(max_vm_steps=1000))
            return original(commit, lane_id)
    monkeypatch.setattr(sqlite_execution, 'verify_committed_sqlite_lane', limited)
    with pytest.raises(EvidenceLaneError) as failure, coordinated_transaction(store, ['memory']) as commit:
        connection = commit.connection('memory')
        connection.executemany('INSERT INTO owned_items VALUES(?)', [(i,) for i in range(2000)])
    assert failure.value.code == 'SQLITE_INSPECTION_EXECUTION_BUDGET'
    assert published(store) == before


def test_packaged_mcp_source_write_records_actual_apsw_lane_validation(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        store, _ = projects(engine, tmp_path)
        path = store.source_root / 'source.txt'
        path.write_text('Registered through packaged MCP')
        async def run():
            async with native(engine.root, store.project_id, permissions=('read', 'write'), entrypoint='package') as session:
                result = await call(session, 'source_register', store.project_id, sources=[str(path)])
                assert result['status'] == 'ok', result
        asyncio.run(run())
        with store.connection(read_only=True) as connection:
            records = [json.loads(row[0]) for row in connection.execute(
                "SELECT body_json FROM root_transaction_journal WHERE phase='published'")]
        executions = [body['sqlite_execution']['sources']['validation'] for body in records
                      if 'sources' in body.get('sqlite_execution', {})]
        assert executions and all(row['engine'] == 'APSW' and row['native_read_only'] for row in executions)
