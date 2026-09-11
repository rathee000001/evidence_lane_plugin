"""Lane views preserve source meaning, exact selectors and immutable exports."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.sector_evidence_workers import evidence_worker_operations
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from .test_evidence_sectors_v4 import call, execute, plan, sqlite_fixture
from .test_research_web_v4 import TOOLS, fixture_web_worker_operations
from .test_research_web_v4 import server as server  # noqa: PLC0414


@pytest.fixture
def system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'note.md').write_text('Evidence: local source evidence\n', encoding='utf-8')
    operations = (*fixture_web_worker_operations(), *evidence_worker_operations(),
        WorkerOperation('research_discover_sources', 'tests.research_discovery_fixtures', 'fixture_worker', max_output_bytes=16_777_216),
        WorkerOperation('render_lane_view', 'evidence_lane_plugin.artifact_contract', 'render_lane_view_worker'))
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(operations, workers=1)) as engine:
        record = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(record['project_id'], write=True)
        _, client = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin', 'network'])]))
        yield engine, store, client


def bytes_at(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}


def preview(system, lane_id, **scope):
    result = call(system, 'lane_view_preview', {'view_id': lane_id + '.structure', 'scope': scope})
    assert result.status == 'ok', result.error
    return result.result


def publish(system, lane_id, selected=None, *, formats=('mmd', 'dot'), pointer=True):
    selected = selected or preview(system, lane_id)
    response = call(system, 'lane_view_refresh', {'view_id': lane_id + '.structure',
        'scope': selected['scope'], 'contract_digest': selected['contract_digest'],
        'source_digest': selected['source_digest'], 'expected_generation': selected['generation'],
        'formats': list(formats), 'include_pointer': pointer})
    assert response.status == 'ok', response.error
    return response.result


@pytest.mark.parametrize('lane_id,kind,pointer_key', [
    ('research', 'research_finding', 'sources_and_assertions'),
    ('artifacts', 'artifact_text_extract', 'artifacts_and_parts'),
    ('custom', 'custom_decision', 'items_and_schema'),
])
def test_owned_views_export_exact_source_locators_and_keep_read_bytes(system, lane_id, kind, pointer_key):
    store = system[1]
    source = store.source_root / 'note.md'
    source.write_text('Finding: source claim only\nDecision: source decision only\nCitation: source A\n', encoding='utf-8')
    plan(system, lane_id, [lane_id + '_index'])
    indexed = execute(system, lane_id + '_index', {'filename': source.name})
    before = bytes_at(store.root)
    selected = preview(system, lane_id, query=indexed['source_id'])
    assert bytes_at(store.root) == before
    nodes = selected['graph']['nodes']
    assert any(node['kind'] == kind for node in nodes)
    assert all(node['locator']['snapshot_id'] == indexed['snapshot_id'] for node in nodes)
    if lane_id != 'artifacts':
        assertion = next(node for node in nodes if node['kind'] == kind)
        assert assertion['state'] == 'source_assertion_unvalidated'
    exported = publish(system, lane_id, selected)
    assert {Path(row['path']).name for row in exported['files']} == {lane_id + '.mmd', lane_id + '.dot', 'lane_pointer.json'}
    assert all('/sectors/' + lane_id + '/' in row['path'].replace('\\', '/') for row in exported['files'])
    before = bytes_at(store.root)
    read = call(system, 'lane_view_read', {'view_id': lane_id + '.structure', 'include_content': True})
    assert read.status == 'ok' and read.result['state'] == 'fresh', read.error
    assert bytes_at(store.root) == before
    pointer = json.loads(read.result['contents']['pointer'])
    assert {value['snapshot_id'] for value in pointer[pointer_key].values()} == {indexed['snapshot_id']}
    assert read.result['manifest']['binding']['source_digest'] == selected['source_digest']
    assert source.read_text(encoding='utf-8').startswith('Finding: source claim only')


def test_research_local_captured_and_discovered_sources_remain_distinct_without_network_on_view(system, server):
    url, events = server
    plan(system, 'research', ['research_index', 'research_web_capture', 'research_web_discover'],
        tools=(*TOOLS, 'DDGS'))
    local = execute(system, 'research_index', {'filename': 'note.md'})
    page = execute(system, 'research_web_capture', {'url': url + '/ok'}, index=1)
    discovery = execute(system, 'research_web_discover', {'query': 'SQLite', 'backends': ['duckduckgo']}, index=2)
    before, requests = bytes_at(system[1].root), list(events)
    selected = preview(system, 'research')
    nodes = selected['graph']['nodes']
    assert {row['kind'] for row in nodes} >= {'research_source', 'captured_page', 'discovery_query', 'research_citation'}
    assert {row['locator']['snapshot_id'] for row in nodes} == {local['snapshot_id'], page['snapshot_id'], discovery['snapshot_id']}
    assert {row['kind'] for row in selected['graph']['edges']} >= {'REPORTS_CITATION', 'REPORTS_SEARCH_RESULT', 'RECORDS_QUERY'}
    assert all(row['locator'].get('target_status') == 'unvisited_search_result'
        for row in nodes if row['locator'].get('url') == 'https://example.test/sqlite')
    assert bytes_at(system[1].root) == before and events == requests
    assert len(events) == 1


def test_custom_sqlite_graph_preserves_separate_foreign_keys_and_selected_rows(system):
    path = system[1].source_root / 'selected.sqlite'
    sqlite_fixture(path)
    before = path.read_bytes()
    plan(system, 'custom', ['custom_index'])
    execute(system, 'custom_index', {'filename': path.name, 'sqlite_tables': ['child']})
    selected = preview(system, 'custom')
    graph = selected['graph']
    nodes = {row['id']: row for row in graph['nodes']}
    relations = [row for row in graph['edges'] if row['kind'] == 'REFERENCES_TABLE']
    assert len(relations) == 1 and not relations[0]['evidence']['relationship_validated']
    assert nodes[relations[0]['target']]['locator']['name'] == 'parent'
    assert relations[0]['evidence']['foreign_key']['from'] == 'parent_id'
    rows = [row for row in nodes.values() if row['kind'] == 'sqlite_row']
    assert len(rows) == 1 and rows[0]['locator']['table'] == 'child'
    assert rows[0]['locator']['row_locator'] == 'ordinal_in_exact_image'
    assert any(row['kind'] == 'SELECTED_ROW' for row in graph['edges'])
    assert path.read_bytes() == before


def test_offline_extraction_view_retains_the_exact_saved_capture_reference(system, server):
    url, events = server
    plan(system, 'research', ['research_web_capture', 'research_web_extract'], tools=TOOLS)
    captured = execute(system, 'research_web_capture', {'url': url + '/ok'})
    extracted = execute(system, 'research_web_extract', {'snapshot_id': captured['snapshot_id'],
        'expected_snapshot': captured['snapshot_id'], 'extractor': 'stdlib'}, index=1)
    selected = preview(system, 'research')
    nodes = {row['id']: row for row in selected['graph']['nodes']}
    link = next(row for row in selected['graph']['edges'] if row['kind'] == 'EXTRACTED_FROM_SAVED_RESPONSE')
    assert nodes[link['source']]['kind'] == 'saved_page_extraction'
    assert nodes[link['source']]['locator']['snapshot_id'] == extracted['snapshot_id']
    assert nodes[link['target']]['locator']['snapshot_id'] == captured['snapshot_id']
    assert nodes[link['target']]['locator']['historical_reference']
    assert len(events) == 1


def test_custom_missing_foreign_key_target_is_explicit_unresolved_reference(system):
    path = system[1].source_root / 'missing.sqlite'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES absent(id))')
    plan(system, 'custom', ['custom_index'])
    execute(system, 'custom_index', {'filename': path.name})
    graph = preview(system, 'custom')['graph']
    references = [row for row in graph['nodes'] if row['kind'] == 'sqlite_table_reference']
    assert len(references) == 1 and references[0]['locator']['resolved'] is False
    assert references[0]['state'] == 'target_table_not_in_inspected_schema'


def test_artifact_archive_view_lists_passive_members_and_retains_unsafe_path_marker(system):
    path = system[1].source_root / 'archive.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('../outside.txt', 'never extract')
        archive.writestr('inside.txt', 'member evidence')
    plan(system, 'artifacts', ['artifacts_index'])
    execute(system, 'artifacts_index', {'filename': path.name})
    graph = preview(system, 'artifacts')['graph']
    members = [row for row in graph['nodes'] if row['kind'] == 'archive_member']
    assert len(members) == 2 and all(row['locator']['member_content_read'] is False for row in members)
    unsafe = next(row for row in members if row['locator']['name'] == '../outside.txt')
    assert unsafe['locator']['safe_path'] is False and unsafe['state'] == 'unsafe_archive_member_path'
    assert len([row for row in graph['edges'] if row['kind'] == 'LISTS_MEMBER']) == 2
    assert not (system[1].root / 'outside.txt').exists()


def test_source_refresh_invalidates_old_view_and_preserves_historical_pointer(system):
    plan(system, 'research', ['research_index', 'research_refresh'])
    first = execute(system, 'research_index', {'filename': 'note.md'})
    old_preview = preview(system, 'research')
    old = publish(system, 'research', old_preview, formats=(), pointer=True)
    assert {row['role'] for row in old['files']} == {'pointer'}
    (system[1].source_root / 'note.md').write_text('Finding: new source assertion\n', encoding='utf-8')
    execute(system, 'research_refresh', {'filename': 'note.md', 'expected_snapshot': first['snapshot_id']}, index=1)
    stale = call(system, 'lane_view_read', {'view_id': 'research.structure'})
    assert stale.status == 'ok' and stale.result['state'] == 'stale'
    new = publish(system, 'research', formats=('mmd',), pointer=False)
    assert {row['role'] for row in new['files']} == {'mmd'}
    assert old['snapshot_digest'] != new['snapshot_digest']
    historical = call(system, 'lane_view_read', {'view_id': 'research.structure',
        'snapshot_digest': old['snapshot_digest'], 'include_content': True})
    assert historical.status == 'ok' and historical.result['state'] == 'historical'
    assert 'pointer' in historical.result['contents']


@pytest.mark.parametrize('scope', [{'node_limit': 1}, {'edge_limit': 0}])
def test_scope_truncation_is_visible_and_has_no_dangling_edges(system, scope):
    plan(system, 'custom', ['custom_index'])
    execute(system, 'custom_index', {'filename': 'note.md'})
    graph = preview(system, 'custom', **scope)['graph']
    assert graph['truncated']
    ids = {row['id'] for row in graph['nodes']}
    assert all(row['source'] in ids and row['target'] in ids for row in graph['edges'])


def test_no_matching_scope_creates_no_placeholder_graph_files(system):
    plan(system, 'artifacts', ['artifacts_index'])
    execute(system, 'artifacts_index', {'filename': 'note.md'})
    selected = preview(system, 'artifacts', query='missing-source')
    assert selected['graph'] == {'nodes': [], 'edges': [], 'truncated': False}
    result = publish(system, 'artifacts', selected, formats=('mmd',), pointer=False)
    assert result['state'] == 'empty' and result['files'] == []


def test_entire_selector_digest_and_scoped_lookups_are_not_capped_by_view_source_limit(system):
    from evidence_lane_plugin.lane_contract import ViewScope
    from evidence_lane_plugin.sector_evidence_views import snapshots, source_heads
    plan(system, 'custom', ['custom_index'])
    indexed = execute(system, 'custom_index', {'filename': 'note.md'})
    # Synthetic selector-only fixtures exercise full head coverage. They do
    # not claim new parsed source snapshots or successful source exports.
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('custom') as connection:
        for i in range(141):
            connection.execute('INSERT INTO custom_file VALUES(?,?,?,?)', (f'zz{i:04}', f'fixture-{i}', f'fixture-{i}', 'fixture'))
        connection.executemany('INSERT INTO custom_current VALUES(?,?)',
            [(f'zz{i:04}', indexed['snapshot_id']) for i in range(140)])
    selected, truncated = snapshots(system[1], 'custom', ViewScope(query='zz0139'))
    assert not truncated and len(selected) == 1 and selected[0]['source_id'] == 'zz0139'
    head = source_heads(system[1], 'custom')
    assert head['custom']['current_selectors']['local_file']['current_sources'] == 141
    with system[0].project_work.mutation(system[1]) as lease, lease.transaction('custom') as connection:
        connection.execute('UPDATE custom_current SET source_id=? WHERE source_id=?', ('zz0140', 'zz0139'))
    new = source_heads(system[1], 'custom')
    assert new['custom']['current_selectors']['local_file']['current_sources'] == 141
    assert new['custom']['current_selectors']['local_file']['selector_sha256'] != head['custom']['current_selectors']['local_file']['selector_sha256']
    invalid = call(system, 'lane_view_preview', {'view_id': 'custom.structure', 'scope': {'query': 'zz0140'}})
    assert invalid.status == 'error' and invalid.error.code == 'SECTOR_EVIDENCE_VIEW_SELECTOR'


def test_pointer_tampering_is_rejected_without_silently_rebuilding(system):
    plan(system, 'artifacts', ['artifacts_index'])
    execute(system, 'artifacts_index', {'filename': 'note.md'})
    result = publish(system, 'artifacts', formats=())
    pointer = Path(result['files'][0]['path'])
    pointer.write_text('{"tampered":true}', encoding='utf-8')
    before = bytes_at(system[1].root)
    read = call(system, 'lane_view_read', {'view_id': 'artifacts.structure', 'include_content': True})
    assert read.status == 'error' and read.error.code == 'VIEW_ARTIFACT_CHANGED'
    assert bytes_at(system[1].root) == before
