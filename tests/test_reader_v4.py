"""Original reader routes exercised with real Code workers and published lanes."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.reader import Diff, Fetch, RootReference
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.universe_snapshot import root_reference
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import ValidationError

from tests.test_code_profile_v4 import call, code_system, create_plan, execute

__all__ = ['code_system']  # Import the existing actual-worker fixture.


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def indexed(code_system):
    store = code_system[1]
    (store.source_root / 'raw.bin').write_bytes(b'\x00' * 11 + b'\xff\x00\x01')
    (store.source_root / 'empty.txt').write_bytes(b'')
    (store.source_root / 'unicode.txt').write_bytes('alpha café\nsecond\nthird\n'.encode())
    (store.source_root / 'eighty.txt').write_bytes(b'row\n' * 80)
    create_plan(code_system)
    result = execute(code_system)
    return code_system, result['snapshot_id']


def read(indexed, action, **arguments):
    system, snapshot = indexed
    result = call(system, action, {'snapshot_id': snapshot, **arguments})
    assert result.status == 'ok', result.error
    return result.result['result']


def test_exact_text_fetch_survives_source_change_and_reads_no_live_payload(indexed):
    system, _ = indexed
    store = system[1]
    original = (store.source_root / 'unicode.txt').read_bytes()
    (store.source_root / 'unicode.txt').write_bytes(b'changed after indexing')
    before = hashes(store.root)
    value = read(indexed, 'fetch', ref_id='file:unicode.txt', start_line=2, max_lines=1)
    assert value['content'] == 'second\n' and value['start_line'] == value['end_line'] == 2
    assert value['truncated'] and value['file_sha256'] == hashlib.sha256(original).hexdigest()
    assert value['source_currentness'] == 'not_checked' and value['git_reference'] is None
    assert hashes(store.root) == before
    assert (store.source_root / 'unicode.txt').read_bytes() == b'changed after indexing'


def test_binary_pages_reconstruct_exact_bytes_and_report_hashes(indexed):
    expected = (indexed[0][1].source_root / 'raw.bin').read_bytes()
    offset, chunks = 0, []
    while True:
        value = read(indexed, 'fetch', ref_id='file:raw.bin', byte_offset=offset, max_bytes=3)
        assert value['representation'] == 'base64' and value['encoding'] is None
        raw = base64.b64decode(value['content'])
        assert value['content_sha256'] == hashlib.sha256(raw).hexdigest()
        chunks.append(raw)
        offset = value['next_byte_offset']
        if offset is None:
            break
    assert b''.join(chunks) == expected
    empty = read(indexed, 'fetch', ref_id='file:empty.txt')
    assert empty['content'] == '' and empty['start_line'] is empty['end_line'] is None


def test_fetch_unicode_budget_never_splits_codepoint_and_chunk_matches_source(indexed):
    value = read(indexed, 'fetch', ref_id='file:unicode.txt', max_bytes=10)
    assert value['content'] == 'alpha caf' and value['truncated']
    query = read(indexed, 'code_query', query='alpha')
    row = query['rows'][0]
    chunk = read(indexed, 'fetch', ref_id='chunk:' + row['chunk_id'])
    assert chunk['content'] == row['text'] and chunk['chunk_sha256'] == row['content_object']
    assert chunk['start_line'] == row['start_line'] and chunk['end_line'] == row['end_line']


@pytest.mark.parametrize('arguments,code', [
    ({'ref_id': 'file:../secret'}, 'CODE_PATH_INVALID'),
    ({'ref_id': 'file:C:/secret'}, 'CODE_PATH_INVALID'),
    ({'ref_id': 'file:raw.bin', 'start_line': 1}, 'BINARY_LINE_RANGE_UNSUPPORTED'),
    ({'ref_id': 'file:unicode.txt', 'byte_offset': 1}, 'TEXT_BYTE_OFFSET_UNSUPPORTED'),
    ({'ref_id': 'file:unicode.txt', 'start_line': 100}, 'FETCH_LINE_RANGE_INVALID'),
    ({'ref_id': 'file:raw.bin', 'byte_offset': 99}, 'FETCH_OFFSET_INVALID'),
    ({'ref_id': 'chunk:' + '0' * 64}, 'CHUNK_NOT_FOUND'),
])
def test_fetch_rejects_invalid_selectors_without_writing(indexed, arguments, code):
    system, snapshot = indexed
    before = hashes(system[1].root)
    result = call(system, 'fetch', {'snapshot_id': snapshot, **arguments})
    assert result.error.code == code
    assert hashes(system[1].root) == before


def test_summary_counts_bounded_exact_snapshot_and_or_search(indexed):
    before = hashes(indexed[0][1].root)
    summary = read(indexed, 'code_snapshot_summary')
    assert summary['counts']['files'] == 7
    assert summary['counts']['chunks'] == 5  # 80 lines is one overlapping chunk.
    assert summary['counts']['symbols'] == 2 and summary['counts']['dependencies'] == 1
    assert summary['counts']['receipts'] == 7
    assert summary['changes'] == {'added': 7, 'modified': 0, 'deleted': 0, 'unchanged': 0}
    assert summary['families'][0]['extension'] == '.txt'
    assert len(summary['largest_files']) == 7 and summary['source_currentness'] == 'not_checked'
    assert read(indexed, 'code_query', query='alpha greeting')['rows'] == []
    any_rows = read(indexed, 'code_query', query='alpha greeting', match_mode='any')['rows']
    assert {r['path'] for r in any_rows} == {'unicode.txt', 'helper.py', 'app.py'}
    receipts = read(indexed, 'code_query', collection='receipts')['rows']
    assert len(receipts) == 7 and all(r['fact']['offline_only'] for r in receipts)
    assert hashes(indexed[0][1].root) == before


def test_summary_read_budget_is_enforced(indexed):
    system, snapshot = indexed
    result = call(system, 'code_snapshot_summary', {'snapshot_id': snapshot, 'max_read_bytes': 1024})
    assert result.error.code == 'READER_BYTE_BUDGET'


def test_fetch_detects_corrupt_addressed_source(indexed):
    store = indexed[0][1]
    value = read(indexed, 'fetch', ref_id='file:unicode.txt')
    path = store.lane('local_code').object_path(value['file_sha256'])
    original = path.read_bytes()
    try:
        path.write_bytes(b'corrupted')
        result = call(indexed[0], 'fetch', {'snapshot_id': indexed[1], 'ref_id': 'file:unicode.txt'})
        assert result.error.code == 'OBJECT_INTEGRITY_FAILED'
    finally:
        path.write_bytes(original)


def test_root_history_and_diff_both_directions_are_coherent_read_only(code_system):
    store = code_system[1]
    left = root_reference(store)
    create_plan(code_system)
    right = root_reference(store)
    before = hashes(store.root)
    history = call(code_system, 'project_evidence_history')
    assert history.status == 'ok', history.error
    publications = history.result['result']['publications']
    assert publications[0]['root'] == right
    assert publications[-1]['root']['revision'] == 0
    forward = call(code_system, 'project_evidence_heads_compare', {'left_root': left, 'right_root': right})
    assert forward.status == 'ok', forward.error
    delta = forward.result['result']['lane_delta']
    # Initial work preflight also publishes the Sources schema before Plan.
    assert {r['lane_id'] for r in delta['added'] + delta['modified']} == {'plan', 'receipts', 'sources'}
    reverse = call(code_system, 'project_evidence_heads_compare', {'left_root': right, 'right_root': left})
    assert reverse.status == 'ok', reverse.error
    assert {r['lane_id'] for r in reverse.result['result']['lane_delta']['removed']} == {r['lane_id'] for r in delta['added']}
    same = call(code_system, 'project_evidence_heads_compare', {'left_root': right, 'right_root': right})
    assert same.status == 'ok' and not same.result['result']['lane_delta']['modified']
    assert same.result['result']['historical_contents_available'] is False
    short = call(code_system, 'project_evidence_history', {'max_commits': 1})
    assert short.status == 'ok' and short.result['result']['truncated']
    limited = call(code_system, 'project_evidence_heads_compare', {'left_root': publications[-1]['root'], 'right_root': right, 'max_commits': 1})
    assert limited.error.code == 'READER_HISTORY_BUDGET'
    wrong = call(code_system, 'project_evidence_heads_compare', {'left_root': left | {'head_digest': '0' * 64}, 'right_root': right})
    assert wrong.error.code == 'READER_ROOT_REFERENCE_MISMATCH'
    assert hashes(store.root) == before


def test_history_detects_corrupt_earlier_transition_even_when_current_root_is_valid(code_system):
    store = code_system[1]
    create_plan(code_system)
    current = root_reference(store)
    with sqlite3.connect(store.database) as connection:
        body = json.loads(connection.execute('SELECT body_json FROM root_transaction_journal WHERE commit_id=?',
            (current['commit_id'],)).fetchone()[0])
        parent = body['before_root']['commit_id']
        original = connection.execute('SELECT body_json FROM root_transaction_journal WHERE commit_id=?', (parent,)).fetchone()[0]
        damaged = json.loads(original)
        damaged['lanes'][0]['after_sha256'] = '0' * 64
        connection.execute('UPDATE root_transaction_journal SET body_json=? WHERE commit_id=?', (json.dumps(damaged), parent))
    assert root_reference(store) == current
    before = hashes(store.root)
    result = call(code_system, 'project_evidence_history')
    assert result.error.code == 'READER_HISTORY_INTEGRITY'
    assert hashes(store.root) == before


def test_no_content_change_is_reported_separately_from_lane_republication(code_system):
    engine, store, _ = code_system
    create_plan(code_system)
    with engine.project_work.mutation(store) as lease:
        left = root_reference(store)
        with lease.transaction('plan') as connection:
            connection.execute('SELECT 1').fetchone()
        right = root_reference(store)
    result = call(code_system, 'project_evidence_heads_compare', {'left_root': left, 'right_root': right})
    assert result.status == 'ok', result.error
    delta = result.result['result']['lane_delta']
    assert 'plan' in delta['unchanged']
    assert 'plan' in {r['lane_id'] for r in delta['republished']}
    assert 'plan' not in {r['lane_id'] for r in delta['modified']}


def test_fetch_checks_chunk_lines_even_when_the_changed_index_is_published(indexed):
    system, snapshot = indexed
    row = read(indexed, 'code_query', query='alpha')['rows'][0]
    engine, store, _ = system
    with engine.project_work.mutation(store) as lease, lease.transaction('local_code') as connection:
        connection.execute('UPDATE code_chunk SET start_line=start_line+1 WHERE chunk_id=?', (row['chunk_id'],))
    before = hashes(store.root)
    result = call(system, 'fetch', {'snapshot_id': snapshot, 'ref_id': 'chunk:' + row['chunk_id']})
    assert result.error.code == 'READER_CHUNK_INTEGRITY'
    assert hashes(store.root) == before


def test_reader_routes_reject_unselected_project_and_studio_remains_read_only(indexed):
    system, snapshot = indexed
    engine, store, _ = system
    _, stranger = engine.clients.connect(ConnectRequest())
    wrong = call((engine, store, stranger), 'fetch', {'snapshot_id': snapshot, 'ref_id': 'file:helper.py'})
    assert wrong.error is not None and wrong.status != 'ok'
    gateway = StudioGateway(engine)
    _, session = gateway.exchange(gateway.issue_ticket())
    before = hashes(store.root)
    value = gateway.command('read', {'project_id': store.project_id, 'action': 'code_snapshot_summary',
        'arguments': {'snapshot_id': snapshot}}, session)
    assert value['result']['counts']['files'] == 7
    assert hashes(store.root) == before


def test_stdio_reader_catalog_and_exact_binary_fetch(indexed):
    system, snapshot = indexed
    engine, store, _ = system
    before = hashes(store.root)
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--project-id', store.project_id], env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] /
                    'plugins/evidence-lane-plugin/src')})
            async with (stdio_client(parameters) as (r, w),
                        ClientSession(r, w, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                catalog = await session.list_tools()
                declared = {t.name: t for t in catalog.tools}
                for name in ('fetch', 'code_snapshot_summary', 'project_evidence_history', 'project_evidence_heads_compare'):
                    assert declared[name].annotations.readOnlyHint
                response = await session.call_tool('fetch', {'project_id': store.project_id,
                    'arguments': {'snapshot_id': snapshot, 'ref_id': 'file:raw.bin', 'max_bytes': 5}})
                assert not response.isError and response.structuredContent['status'] == 'ok', response
                assert base64.b64decode(response.structuredContent['result']['result']['content']) == b'\x00' * 5
                assert json.loads(response.content[0].text) == response.structuredContent
        asyncio.run(exercise())
    assert hashes(store.root) == before


@pytest.mark.parametrize('model,arguments', [
    (Fetch, {'snapshot_id': 'a' * 64, 'ref_id': 'chunk:123'}),
    (Fetch, {'snapshot_id': 'a' * 64, 'ref_id': 'file:foo', 'start_line': 3, 'end_line': 2}),
    (RootReference, {'revision': 1, 'head_digest': '', 'commit_id': None}),
    (Diff, {'left_pv': 'PV1', 'right_pv': 'PV2'}),
])
def test_strict_contracts_reject_legacy_or_invalid_references(model, arguments):
    with pytest.raises(ValidationError):
        model.model_validate(arguments)
