"""Complete-batch resource limits, owned execution and atomic Sources admission."""
from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from evidence_lane_plugin import bounded_io, source_sqlite
from evidence_lane_plugin.bounded_io import BoundedProcessResult
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.hashing import sha256_file
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.source_authority import SourceAuthoritySpec, register_source_batch
from evidence_lane_plugin.source_intake import SourceSQLiteRequest
from evidence_lane_plugin.storage import ProjectStore

from .test_native_workflow_bindings import call, native, projects


def database(path, *, seed=0, tables=1):
    connection = sqlite3.connect(path)
    try:
        for i in range(tables):
            connection.execute(f'CREATE TABLE t{i}(value INTEGER)')
            connection.execute(f'INSERT INTO t{i} VALUES(?)', (seed,))
        connection.commit()
    finally:
        connection.close()


def prepared(tmp_path, *, count=2, tables=1):
    source = tmp_path / 'source'
    source.mkdir()
    paths = [source / f'input-{i}.sqlite' for i in range(count)]
    for i, path in enumerate(paths):
        database(path, seed=i, tables=tables)
    lane = ProjectStore.create(tmp_path / 'state', source).lane('sources')
    batch = register_source_batch(lane, [SourceAuthoritySpec(str(source), 1, 'custom')])['batch_id']
    return lane, batch, paths


def published(lane):
    with lane.project.connection(read_only=True) as connection:
        head = tuple(connection.execute('SELECT revision,head_digest,commit_id FROM root_pv_head').fetchone())
    return sha256_file(lane.database), head


def test_aggregate_input_budget_rejects_before_any_worker_starts(tmp_path, monkeypatch):
    lane, batch, paths = prepared(tmp_path)
    before = published(lane)
    source_before = {path: sha256_file(path) for path in paths}
    def unexpected(*args, **kwargs):
        raise AssertionError('Over-budget inputs started a worker')
    monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', unexpected)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(lane, batch,
            max_total_input_bytes=sum(path.stat().st_size for path in paths) - 1)
    assert failure.value.code == 'SOURCE_SQLITE_BATCH_INPUT_BUDGET'
    assert published(lane) == before and all(sha256_file(path) == digest for path, digest in source_before.items())


def test_occurrences_are_counted_even_when_metadata_can_be_deduplicated(tmp_path):
    lane, batch, _ = prepared(tmp_path)
    before = published(lane)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(lane, batch, max_assets=1)
    assert failure.value.code == 'SOURCE_SQLITE_BATCH_ASSET_BUDGET'
    assert published(lane) == before


def test_complete_metadata_budget_discards_all_source_publication(tmp_path):
    lane, batch, _ = prepared(tmp_path, tables=8)
    before = published(lane)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(lane, batch, max_metadata_bytes=1024)
    assert failure.value.code == 'SOURCE_SQLITE_BATCH_METADATA_BUDGET'
    assert published(lane) == before


def test_vm_limit_covers_several_individually_admissible_images(tmp_path):
    lane, batch, paths = prepared(tmp_path, tables=30)
    individual_steps = []
    for path in paths:
        selected = register_source_batch(lane, [SourceAuthoritySpec(str(path), 1, 'custom')])['batch_id']
        result = source_sqlite.inspect_registered_sqlite_assets(lane, selected)
        assert result['status'] == 'PASS'
        individual_steps.append(result['batch_worker']['vm_steps'])
    limit = max(1000, max(individual_steps) + 1)
    assert sum(individual_steps) > limit
    before = published(lane)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(lane, batch, max_batch_vm_steps=limit)
    assert failure.value.code == 'SOURCE_SQLITE_BATCH_EXECUTION_BUDGET'
    assert published(lane) == before


def test_real_batch_worker_timeout_cleans_descendants_and_preserves_publication(tmp_path, monkeypatch):
    lane, batch, paths = prepared(tmp_path)
    before = published(lane)
    source_before = {path: sha256_file(path) for path in paths}
    original = bounded_io.run_owned_bounded_process
    directories = []
    def short_deadline(command, **kwargs):
        directories.append(Path(kwargs['cwd']))
        kwargs['timeout_seconds'] = 0.001
        return original(command, **kwargs)
    monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', short_deadline)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(lane, batch)
    assert failure.value.code == 'BOUNDED_PROCESS_TIMEOUT'
    assert directories and all(not directory.exists() for directory in directories)
    assert published(lane) == before and all(sha256_file(path) == digest for path, digest in source_before.items())


@pytest.mark.parametrize('damage', ['invalid_json', 'request_identity', 'source_identity'])
def test_invalid_worker_result_cannot_publish_source_metadata(tmp_path, monkeypatch, damage):
    lane, batch, _ = prepared(tmp_path)
    before = published(lane)
    original = bounded_io.run_owned_bounded_process
    directories = []
    def damaged(command, **kwargs):
        directories.append(Path(kwargs['cwd']))
        if damage == 'invalid_json':
            return BoundedProcessResult(0, b'not-json', b'')
        result = original(command, **kwargs)
        assert result.returncode == 0, result.stdout
        response = json.loads(result.stdout)
        if damage == 'request_identity':
            response['request_sha256'] = '0' * 64
        else:
            next(iter(response['result']['inspections'].values()))['receipt']['source_state_sha256'] = '0' * 64
        return BoundedProcessResult(0, json.dumps(response).encode(), b'')
    monkeypatch.setattr(bounded_io, 'run_owned_bounded_process', damaged)
    with pytest.raises(LaneError) as failure:
        source_sqlite.inspect_registered_sqlite_assets(lane, batch)
    assert failure.value.code in {'SOURCE_SQLITE_BATCH_RESULT_INVALID', 'SOURCE_SQLITE_BATCH_BINDING_MISMATCH',
        'SOURCE_SQLITE_BATCH_RESULT_MISMATCH'}
    assert published(lane) == before and directories and all(not directory.exists() for directory in directories)


@pytest.mark.parametrize('arguments', [
    {'max_assets': True}, {'max_assets': 513}, {'max_total_input_bytes': 0},
    {'max_total_input_bytes': 4 * 1024**3 + 1}, {'max_metadata_bytes': 1023},
    {'max_metadata_bytes': 32 * 1024 * 1024 + 1}, {'max_batch_vm_steps': True},
    {'max_batch_vm_steps': 999}, {'timeout_seconds': float('nan')}, {'timeout_seconds': float('inf')},
    {'timeout_seconds': 0}, {'timeout_seconds': 61},
])
def test_public_batch_budget_contract_rejects_invalid_arguments(arguments):
    with pytest.raises(ValueError):
        SourceSQLiteRequest(batch_id='intake_fixture', **arguments)


def test_packaged_mcp_advertises_and_executes_bounded_sqlite_batch(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        store, _ = projects(engine, tmp_path)
        path = store.source_root / 'source.sqlite'
        database(path)
        async def run():
            async with native(engine.root, store.project_id, permissions=('read', 'write'), entrypoint='package') as session:
                tools = await session.list_tools()
                tool = next(tool for tool in tools.tools if tool.name == 'source_inspect_sqlite')
                properties = tool.inputSchema['properties']['arguments']['properties']
                assert {'max_assets', 'max_total_input_bytes', 'max_metadata_bytes', 'max_batch_vm_steps', 'timeout_seconds'} <= set(properties)
                registration = await call(session, 'source_register', store.project_id, sources=[str(path)])
                batch = registration['result']['result']['source_authority']['batch_id']
                result = await call(session, 'source_inspect_sqlite', store.project_id, batch_id=batch)
                assert result['status'] == 'ok', result
                measured = result['result']['result']
                assert measured['status'] == 'PASS'
                assert measured['batch_worker']['owned_worker_joined'] and measured['batch_worker']['metadata_calls'] == 1
                assert not measured['batch_worker']['authority_database_path_supplied']
                assert not measured['batch_worker']['project_writer_supplied']
        asyncio.run(run())
