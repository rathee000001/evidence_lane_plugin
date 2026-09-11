"""Retained evidence source semantics through owned workers and separate lanes."""
from __future__ import annotations

import base64
import io
import json
import sqlite3
import time
import zipfile

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.sector_evidence_parsers import parse_evidence
from evidence_lane_plugin.sector_evidence_workers import evidence_worker_operations
from evidence_lane_plugin.workers import WorkerPool


@pytest.fixture
def system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'note.md').write_text('Question: How does this work?\nEvidence: exact source text\nCitation: source A\nDecision: keep scope\n', encoding='utf-8')
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(evidence_worker_operations(), workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
        yield engine, store, session


def call(system, action, arguments=None, **kwargs):
    engine, store, session = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def plan(system, lane_id, actions, *, paths=('.',), tools=('Python', 'SQLite_FTS5_BM25')):
    engine, store, _ = system
    tasks = [TaskDefinition(task_id='evidence-' + str(index), title=action,
        requested_outcome='Verify exact retained source evidence', profile=lane_id,
        allowed_actions=[action], permitted_tools=list(tools), permitted_paths=list(paths),
        acceptance_checks=list(engine.registry.get(action).verification_checks)) for index, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Evidence source fixture', tasks=tasks), lease, actor_id='fixture')


def execute(system, action, arguments, *, index=0, expected='verified', source_route=None):
    store = system[1]
    task = PlanStore(store).task('evidence-' + str(index), expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments,
        'source_route': source_route}, expected_revision=1)
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + 30
    row = None
    while time.monotonic() < deadline:
        try:
            with store.lane('plan').connection(read_only=True) as connection:
                row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.02)
            continue
        if row['state'] in {'verified', 'blocked'}:
            break
        time.sleep(.02)
    assert row is not None and row['state'] == expected, row
    # The terminal row is committed before the driver releases its writer.
    # Await the owned driver's completion before another task or byte snapshot;
    # no fixed delay and no inference of quiescence from the row alone.
    engine = system[0]
    with engine._admission:
        assert engine._admission.wait_for(lambda: engine._background_jobs == 0,
            timeout=max(0, deadline - time.monotonic())), 'Delta driver did not finish after its terminal row.'
    return (json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
        if expected == 'verified' else row)


@pytest.mark.parametrize('lane_id,kind', [('research', 'research_question'), ('artifacts', 'artifact_text_extract'), ('custom', 'custom_decision')])
def test_each_lane_indexes_queries_and_keeps_exact_historical_bytes(system, lane_id, kind):
    store = system[1]
    before = (store.source_root / 'note.md').read_bytes()
    plan(system, lane_id, [lane_id + '_index', lane_id + '_refresh'])
    first = execute(system, lane_id + '_index', {'filename': 'note.md'})
    assert first['source_bytes_mutated'] is False
    typed = call(system, lane_id + '_query', {'snapshot_id': first['snapshot_id'], 'collection': kind})
    assert typed.status == 'ok', typed.error
    assert typed.result['result']['rows']
    searched = call(system, lane_id + '_query', {'snapshot_id': first['snapshot_id'], 'query': 'exact source'})
    assert searched.status == 'ok' and searched.result['result']['rows'], searched.error
    shared_search = call(system, 'lane_search', {'lane_id': lane_id, 'query': 'exact unmatchedterm'})
    assert shared_search.status == 'ok', shared_search.error
    assert shared_search.result['arms'][0]['state'] == 'hit'
    assert shared_search.result['arms'][0]['queries'][0]['action'] == lane_id + '_query'
    shared_read = call(system, 'lane_fetch', {'lane_id': lane_id, 'snapshot_id': first['snapshot_id'],
        'path': 'note.md', 'representation': 'original_source', 'max_bytes': 1024})
    assert shared_read.status == 'ok', shared_read.error
    assert base64.b64decode(shared_read.result['result']['read']['result']['content_base64']) == before
    read = call(system, lane_id + '_read', {'snapshot_id': first['snapshot_id']})
    assert base64.b64decode(read.result['result']['content_base64']) == before
    assert (store.source_root / 'note.md').read_bytes() == before
    (store.source_root / 'note.md').write_text('Finding: second source version\n', encoding='utf-8')
    second = execute(system, lane_id + '_refresh', {'filename': 'note.md', 'expected_snapshot': first['snapshot_id']}, index=1)
    assert second['snapshot_id'] != first['snapshot_id'] and second['previous_snapshot'] == first['snapshot_id']
    assert call(system, lane_id + '_current').result['result']['files'][0]['snapshot_id'] == second['snapshot_id']
    assert base64.b64decode(call(system, lane_id + '_read', {'snapshot_id': first['snapshot_id']}).result['result']['content_base64']) == before
    assert store.lane(lane_id).database.is_file()
    assert all(not (store.root / 'sectors' / other).exists() for other in {'research', 'artifacts', 'custom'} - {lane_id})
    assert system[0].workers.status()['succeeded_operations'] == 2


def test_labels_are_source_assertions_not_verified_findings():
    facts = parse_evidence('research', 'note.md', b'Finding: this is a source assertion\nCitation: evidence A', 'text')
    assertions = [item for item in facts['items'] if item['kind'] in {'research_finding', 'research_citation'}]
    assert len(assertions) == 2 and all(item['assertion_status'] == 'source_assertion_unvalidated' for item in assertions)
    assert not facts['fidelity']['source_assertions_validated']


def test_notebook_code_and_saved_outputs_are_passive():
    raw = json.dumps({'nbformat': 4, 'cells': [{'cell_type': 'code', 'source': ['raise RuntimeError("never execute")'],
        'execution_count': 999, 'outputs': [{'output_type': 'stream', 'name': 'stdout', 'text': 'unverified output'}]}]}).encode()
    facts = parse_evidence('artifacts', 'example.ipynb', raw, 'notebook')
    assert not facts['fidelity']['notebook_executed'] and not facts['fidelity']['saved_outputs_verified']
    assert any(item.get('output_verified') is False for item in facts['items'])


def test_archive_paths_are_inspected_without_extraction():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('../outside.txt', 'never extract')
        archive.writestr('note.txt', 'evidence')
    facts = parse_evidence('custom', 'sources.zip', stream.getvalue(), 'archive')
    assert facts['fidelity']['unsafe_members'] == 1 and facts['fidelity']['review_required']
    assert all(item.get('member_content_read') is False for item in facts['items'] if item['kind'] == 'archive_member')


def sqlite_fixture(path):
    with sqlite3.connect(path) as connection:
        connection.executescript('CREATE TABLE parent(id INTEGER PRIMARY KEY,name TEXT);'
            'CREATE TABLE child(id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id),payload BLOB);'
            'CREATE VIEW claim AS SELECT * FROM parent;'
            'CREATE TRIGGER unused AFTER INSERT ON parent BEGIN UPDATE parent SET name=name WHERE id=new.id; END;')
        connection.execute('INSERT INTO parent VALUES(?,?)', (1, 'source row'))
        connection.execute('INSERT INTO child VALUES(?,?,?)', (1, 1, b'\x00\x01'))


def test_selected_sqlite_retains_schema_relations_and_only_selected_rows(system):
    path = system[1].source_root / 'selected.sqlite'
    sqlite_fixture(path)
    before = path.read_bytes()
    plan(system, 'custom', ['custom_index'])
    indexed = execute(system, 'custom_index', {'filename': path.name, 'sqlite_tables': ['child']})
    query = call(system, 'custom_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'sqlite_row'})
    rows = query.result['result']['rows']
    assert len(rows) == 1 and rows[0]['table'] == 'child'
    assert rows[0]['values'][2] == {'type': 'binary', 'value': 'AAE='}
    related = call(system, 'custom_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'sqlite_relationship'})
    assert related.result['result']['rows'][0]['foreign_key']['table'] == 'parent'
    assert path.read_bytes() == before
    assert not path.with_name(path.name + '-wal').exists()


def test_sqlite_views_are_not_selected_as_executable_queries(tmp_path):
    path = tmp_path / 'selected.sqlite'
    sqlite_fixture(path)
    with pytest.raises(ValueError, match='TABLE_SELECTION_INVALID'):
        parse_evidence('custom', path.name, path.read_bytes(), 'sqlite', sqlite_tables=['claim'])


def test_invalid_known_format_blocks_without_publishing(system):
    (system[1].source_root / 'broken.json').write_bytes(b'{"missing":')
    plan(system, 'research', ['research_index'])
    failed = execute(system, 'research_index', {'filename': 'broken.json'}, expected='blocked')
    assert failed['error_code'] == 'SECTOR_EVIDENCE_WORKER_FAILED'
    assert call(system, 'research_current').result['result']['files'] == []


def test_live_sqlite_sidecar_prevents_stale_image_intake(system):
    path = system[1].source_root / 'selected.sqlite'
    sqlite_fixture(path)
    path.with_name(path.name + '-wal').write_bytes(b'active')
    plan(system, 'custom', ['custom_index'])
    execute(system, 'custom_index', {'filename': path.name}, expected='blocked')
    assert call(system, 'custom_current').result['result']['files'] == []


def test_foreign_lane_snapshot_cannot_be_read(system):
    plan(system, 'research', ['research_index'])
    indexed = execute(system, 'research_index', {'filename': 'note.md'})
    other = call(system, 'custom_read', {'snapshot_id': indexed['snapshot_id']})
    assert other.status == 'error' and other.error.code == 'SECTOR_EVIDENCE_SNAPSHOT_MISSING'


def test_read_operations_do_not_change_lane_bytes(system):
    plan(system, 'research', ['research_index'])
    indexed = execute(system, 'research_index', {'filename': 'note.md'})
    store = system[1]
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    for action, args in [('research_current', {}), ('research_query', {'snapshot_id': indexed['snapshot_id'], 'query': 'source'}),
            ('research_read', {'snapshot_id': indexed['snapshot_id']})]:
        response = call(system, action, args)
        assert response.status == 'ok', response.error
    assert before == {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}


@pytest.mark.parametrize('key,value', [('generation', 99), ('bytes', 0), ('parser', 'opaque'),
    ('source_bytes_mutated', True), ('fidelity', {'source_assertions_validated': True}),
    ('limitations', []), ('extra_success_claim', True)])
def test_changed_result_claims_cannot_complete_the_task(system, monkeypatch, key, value):
    from evidence_lane_plugin import sector_evidence_profile
    original = sector_evidence_profile.result

    def changed(store, lane_id, operation, body):
        if operation == 'research_index':
            body = {**body, key: value}
        return original(store, lane_id, operation, body)

    monkeypatch.setattr(sector_evidence_profile, 'result', changed)
    plan(system, 'research', ['research_index'])
    failed = execute(system, 'research_index', {'filename': 'note.md'}, expected='blocked')
    assert failed['error_code'] == 'DELTA_ACCEPTANCE_FAILED'
    assert PlanStore(system[1]).task('evidence-0', expected_revision=1).state != 'completed'


def test_routed_intake_keeps_exact_selected_source_occurrences(system):
    from evidence_lane_plugin.sector_evidence_profile import read_snapshot

    from .test_source_routing_v4 import registered
    path = str(system[1].source_root / 'note.md')
    route = registered(system, ['note.md'], action='lane_configure_routes', overrides={path: 'research'})
    plan(system, 'research', ['research_index'])
    selection = {'route_id': route['route_id'], 'occurrence_ordinals': [1]}
    indexed = execute(system, 'research_index', {'filename': 'note.md'}, source_route=selection)
    manifest, _ = read_snapshot(system[1], 'research', indexed['snapshot_id'])
    assert manifest['source_route'] == selection


def test_failed_publication_rolls_back_all_source_sector_selectors(system, monkeypatch):
    from evidence_lane_plugin.storage import ProjectStore
    original = ProjectStore.append_receipt

    def failed(store, kind, body, **kwargs):
        if kind == 'sector_evidence_snapshot':
            raise LaneError('FIXTURE_PUBLICATION_FAILED', 'Deliberate pre-publication failure')
        return original(store, kind, body, **kwargs)

    plan(system, 'artifacts', ['artifacts_index'])
    monkeypatch.setattr(ProjectStore, 'append_receipt', failed)
    execute(system, 'artifacts_index', {'filename': 'note.md'}, expected='blocked')
    assert call(system, 'artifacts_current').result['result']['files'] == []


def test_source_change_during_owned_parse_prevents_publication(system, monkeypatch):
    from concurrent.futures import Future
    engine, store, _ = system
    original = engine.workers.submit

    def changed(operation, arguments, **kwargs):
        response = original(operation, arguments, **kwargs).result()
        (store.source_root / 'note.md').write_text('changed while parsing', encoding='utf-8')
        future = Future()
        future.set_result(response)
        return future

    plan(system, 'research', ['research_index'])
    monkeypatch.setattr(engine.workers, 'submit', changed)
    execute(system, 'research_index', {'filename': 'note.md'}, expected='blocked')
    assert call(system, 'research_current').result['result']['files'] == []
