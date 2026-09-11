"""Read retained SQLite history through current source SDK/MCP contracts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.sdk import ActionRequest, dispatch

from .test_native_workflow_bindings import call, native, projects
from .test_source_actions_v4 import invoke
from .test_source_actions_v4 import selected as selected  # noqa: PLC0414 - pytest fixture import

COLLECTIONS = ['sqlite_assets', 'sqlite_receipts', 'sqlite_schema', 'sqlite_tables', 'sqlite_foreign_keys']


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}


def writer(path):
    connection = sqlite3.connect(path)
    connection.executescript('''PRAGMA journal_mode=WAL;
        PRAGMA wal_autocheckpoint=0;
        CREATE TABLE parent(id INTEGER PRIMARY KEY);
        CREATE TABLE wal_child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id));
        INSERT INTO parent VALUES(1);
        INSERT INTO wal_child VALUES(1,1);
    ''')
    connection.commit()
    return connection


def read(selected, **kwargs):
    engine, store, context, _ = selected
    readonly = ActionContext(context.client_id, store.project_id, frozenset({'read'}), authorize=context.authorize)
    response = dispatch(engine.registry, ActionRequest(action='source_read', project_id=store.project_id,
        arguments=kwargs), readonly)
    assert response.status == 'ok', response
    return response.result['result']


def test_sqlite_history_reads_do_not_initialize_sources(selected):
    store = selected[1]
    before = snapshot(store.root)
    for collection in COLLECTIONS:
        value = read(selected, collection=collection)
        assert value['rows'] == [] and value['initialized'] is False
    assert snapshot(store.root) == before


def test_actual_wal_inspections_and_exact_ids_are_read_without_writes(selected):
    store = selected[1]
    database = store.source_root / 'history.sqlite'
    connection = writer(database)
    try:
        registered = invoke(selected, 'source_register', sources=[str(database), str(database) + '-wal'])
        assert registered.status == 'ok', registered
        batch = registered.result['result']['source_authority']['batch_id']
        assert invoke(selected, 'source_inspect_sqlite', batch_id=batch, exact_count_max_database_bytes=1).status == 'ok'
        assert invoke(selected, 'source_inspect_sqlite', batch_id=batch).status == 'ok'
        before = snapshot(store.root)
        first = read(selected, collection='sqlite_receipts', limit=1)
        second = read(selected, collection='sqlite_receipts', offset=first['next_offset'], limit=1)
        assert len(first['rows']) == len(second['rows']) == 1 and second['next_offset'] is None
        ids = [value['rows'][0]['canonical_asset_id'] for value in (first, second)]
        assert ids[0] != ids[1]
        for inspection_id in ids:
            measured = read(selected, collection='sqlite_receipts', inspection_id=inspection_id)
            assert len(measured['rows']) == 1
            receipt = json.loads(measured['rows'][0]['receipt_json'])
            assert receipt['status'] == 'PASS' and any(row['role'] == '-wal' for row in receipt['source_members'])
            schemas = read(selected, collection='sqlite_schema', inspection_id=inspection_id)['rows']
            assert {row['object_name'] for row in schemas} == {'parent', 'wal_child'}
            table_rows = read(selected, collection='sqlite_tables', inspection_id=inspection_id)['rows']
            if receipt['exact_count_max_database_bytes'] == 1:
                assert all(row['row_count'] is None for row in table_rows)
            else:
                assert all(row['row_count'] == 1 for row in table_rows)
            assert len(read(selected, collection='sqlite_foreign_keys', inspection_id=inspection_id)['rows']) == 1
            assert len(read(selected, collection='sqlite_assets', inspection_id=inspection_id)['rows']) == 1
        assert read(selected, collection='sqlite_schema', inspection_id='sqlite_v4_absent')['rows'] == []
        assert snapshot(store.root) == before
    finally:
        connection.close()


@pytest.mark.parametrize('collection', COLLECTIONS)
def test_sqlite_project_history_does_not_infer_batch_associations(selected, collection):
    response = invoke(selected, 'source_read', collection=collection, batch_id='intake_fixture')
    assert response.status == 'error' and response.error.code == 'SOURCE_COLLECTION_SCOPE'


def test_history_filter_and_collection_validation_preserve_existing_scopes(selected):
    invalid = invoke(selected, 'source_read', collection='batches', inspection_id='sqlite_v4_fixture')
    assert invalid.status == 'error' and invalid.error.code == 'SOURCE_COLLECTION_SCOPE'
    unknown = invoke(selected, 'source_read', collection='sqlite_master')
    assert unknown.status == 'error' and unknown.error.code == 'INVALID_ARGUMENTS'
    before = snapshot(selected[1].root)
    for collection in ('batches', 'occurrences', 'relations', 'provenance', 'graphs', 'schemas', 'identities'):
        assert read(selected, collection=collection)['rows'] == []
    assert snapshot(selected[1].root) == before


def test_history_row_byte_limit_is_visible_and_read_only(selected):
    store = selected[1]
    database = store.source_root / 'history.sqlite'
    connection = writer(database)
    try:
        registered = invoke(selected, 'source_register', sources=[str(database), str(database) + '-wal'])
        batch = registered.result['result']['source_authority']['batch_id']
        assert invoke(selected, 'source_inspect_sqlite', batch_id=batch).status == 'ok'
        before = snapshot(store.root)
        limited = invoke(selected, 'source_read', collection='sqlite_receipts', max_bytes=1024)
        assert limited.status == 'error' and limited.error.code == 'SOURCE_ROW_BUDGET'
        assert snapshot(store.root) == before
    finally:
        connection.close()


def test_packaged_mcp_source_read_exposes_actual_wal_history(tmp_path):
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        store, _ = projects(engine, tmp_path)
        database = store.source_root / 'protocol-history.sqlite'
        connection = writer(database)
        try:
            async def run():
                async with native(engine.root, store.project_id, permissions=('read', 'write'), entrypoint='package') as session:
                    tools = await session.list_tools()
                    tool = next(tool for tool in tools.tools if tool.name == 'source_read')
                    properties = tool.inputSchema['properties']['arguments']['properties']
                    assert set(COLLECTIONS) <= set(properties['collection']['enum'])
                    assert 'inspection_id' in properties
                    registered = await call(session, 'source_register', store.project_id,
                        sources=[str(database), str(database) + '-wal'])
                    batch = registered['result']['result']['source_authority']['batch_id']
                    measured = await call(session, 'source_inspect_sqlite', store.project_id, batch_id=batch)
                    assert measured['status'] == 'ok', measured
                    before = snapshot(store.root)
                    page = await call(session, 'source_read', store.project_id, collection='sqlite_receipts')
                    receipt = page['result']['result']['rows'][0]
                    for collection in COLLECTIONS:
                        result = await call(session, 'source_read', store.project_id,
                            collection=collection, inspection_id=receipt['canonical_asset_id'])
                        assert result['status'] == 'ok' and result['result']['result']['rows'], result
                    assert snapshot(store.root) == before
            asyncio.run(run())
        finally:
            connection.close()
