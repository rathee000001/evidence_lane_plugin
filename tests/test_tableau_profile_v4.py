"""Real Tableau fixtures through engine workers, Delta and own-lane persistence."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.tableau_parsers import digest, package_members
from evidence_lane_plugin.tableau_workers import tableau_worker_operations
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

FIXTURES=Path(__file__).parent/'fixtures/tableau'

@pytest.fixture
def tableau_system(tmp_path):
    source=tmp_path/'source'; source.mkdir()
    for name in ('datasource_test.twb', 'TABLEAU_10_TWBX.twbx'):
        (source/name).write_bytes((FIXTURES/name).read_bytes())
    operations = (*tableau_worker_operations(), WorkerOperation('render_lane_view',
        'evidence_lane_plugin.artifact_contract', 'render_lane_view_worker',
        dependencies=('langgraph', 'langchain_core', 'graphviz')))
    with Engine(tmp_path/'runtime',worker_pool=WorkerPool(operations,workers=1)) as engine:
        entry=engine.directory.register(tmp_path/'state',source_root=source,create=True,read_only=False)
        store=engine.directory.open(entry['project_id'],write=True)
        _,session=engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,permissions=['read','write','tools','admin'])]))
        yield engine,store,session


def call(system, action, arguments=None, **kwargs):
    engine, store, session = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def plan(system, actions):
    engine, store, _ = system
    tasks = [TaskDefinition(task_id='tableau-' + str(index), title=action, requested_outcome='Verify the exact tableau operation',
        profile='tableau', allowed_actions=[action], permitted_tools=['Python', 'SQLite_FTS5_BM25',
            'lxml', 'Tableau_Hyper_API', 'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine'], permitted_paths=['.'],
        acceptance_checks=list(engine.registry.get(action).verification_checks),
        budget=TaskBudget(max_output_bytes=33_554_432, max_seconds=180)) for index, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Tableau fixture', tasks=tasks), lease, actor_id='fixture')


def execute(system, action, arguments, *, index=0, timeout=60, failure=None):
    store = system[1]
    task = PlanStore(store).task('tableau-' + str(index), expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments}, expected_revision=1)
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with store.lane('plan').connection(read_only=True) as connection:
                row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.03)
            continue
        if row['state'] in {'verified', 'blocked'}:
            break
        time.sleep(.02)
    if failure:
        assert row['state'] == 'blocked' and row['error_code'] == failure, row
        return row
    assert row['state'] == 'verified', {key: row[key] for key in ('state', 'error_code', 'result_object')}
    return json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']


def test_native_workbook_snapshot_retrieval_and_read_only_queries(tableau_system):
    plan(tableau_system, ['tableau_index'])
    store = tableau_system[1]
    raw = (store.source_root / 'datasource_test.twb').read_bytes()
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'datasource_test.twb'})
    assert indexed['sha256'] == digest(raw) and '/sectors/tableau/files/natural/' in indexed['natural_path'].replace('\\', '/')
    assert not indexed['fidelity']['layout_verified']
    arguments = {'snapshot_id': indexed['snapshot_id']}
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    for collection, count in [('sheet', 2), ('calculation', 1), ('relationship', 3)]:
        response = call(tableau_system, 'tableau_query', arguments | {'collection': collection})
        assert response.status == 'ok', response.error
        assert len(response.result['result']['rows']) == count
    response = call(tableau_system, 'tableau_query', arguments | {'query': 'calculation'})
    assert response.status == 'ok' and len(response.result['result']['rows']) == 1
    response = call(tableau_system, 'tableau_read', arguments | {'max_bytes': 131072})
    assert base64.b64decode(response.result['result']['content_base64']) == raw
    assert before == {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    with store.connection(read_only=True) as db:
        assert db.execute("SELECT count(*) FROM sqlite_schema WHERE name GLOB 'tableau_*'").fetchone()[0] == 0
    with store.lane('tableau').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM tableau_calculation').fetchone()[0] == 1
    assert (store.source_root / 'datasource_test.twb').read_bytes() == raw


def test_xml_edit_export_and_refresh_preserve_history_and_package_members(tableau_system):
    from lxml import etree
    plan(tableau_system, ['tableau_index', 'tableau_edit', 'tableau_export', 'tableau_refresh'])
    store = tableau_system[1]
    name = 'TABLEAU_10_TWBX.twbx'
    raw = (store.source_root / name).read_bytes()
    original = package_members(raw)
    indexed = execute(tableau_system, 'tableau_index', {'filename': name})
    response = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'calculation'})
    item = response.result['result']['rows'][0]
    edited = execute(tableau_system, 'tableau_edit', {'snapshot_id': indexed['snapshot_id'],
        'expected_sha256': indexed['sha256'], 'replacements': [{'part': item['part'], 'item_id': item['item_id'],
            'attribute': 'formula', 'expected_value': item['attributes']['formula'], 'value': '1 + 2'}]}, index=1)
    changed = package_members(store.lane('tableau').read_object(edited['sha256']))
    assert all(changed[k] == v for k, v in original.items() if k != item['part'])
    assert etree.fromstring(changed[item['part']]).getroottree().xpath(item['xml_path'])[0].get('formula') == '1 + 2'
    assert (store.source_root / name).read_bytes() == raw
    exported = execute(tableau_system, 'tableau_export', {'snapshot_id': edited['snapshot_id'], 'filename': name,
        'expected_sha256': indexed['sha256']}, index=2)
    destination = exported['index_refresh']['result']
    assert destination['generation'] == 3 and destination['previous_snapshot'] == edited['snapshot_id']
    refreshed = execute(tableau_system, 'tableau_refresh', {'filename': name, 'expected_snapshot': destination['snapshot_id']}, index=3)
    assert refreshed['generation'] == 4 and refreshed['sha256'] == edited['sha256']
    old = call(tableau_system, 'tableau_read', {'snapshot_id': indexed['snapshot_id'], 'max_bytes': 131072})
    assert base64.b64decode(old.result['result']['content_base64']) == raw


def test_native_hyper_generation_and_typed_reads(tableau_system, tmp_path):
    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry
    plan(tableau_system, ['tableau_generate'])
    generated = execute(tableau_system, 'tableau_generate', {'logical_name': 'generated.hyper', 'tables': [
        {'schema_name': 'Other', 'name': 'Quoted " Table', 'columns': [
            {'name': 'ID', 'type': 'big_int'}, {'name': 'Amount', 'type': 'numeric'},
            {'name': 'Nullable text', 'type': 'text'}], 'rows': [[1, '123.4500', 'café'], [2, None, None]]}]})
    response = call(tableau_system, 'tableau_query', {'snapshot_id': generated['snapshot_id'], 'collection': 'hyper_row'})
    assert response.status == 'ok', response.error
    values = [row['values'] for row in response.result['result']['rows']]
    assert values == [[1, {'type': 'numeric', 'value': '123.45'}, 'café'], [2, None, None]]
    path = tmp_path / 'independent.hyper'
    path.write_bytes(tableau_system[1].lane('tableau').read_object(generated['sha256']))
    with (
        HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU, parameters={'log_config': ''}) as hyper,
        Connection(hyper.endpoint, path, CreateMode.NONE) as db,
    ):
        assert db.execute_scalar_query('SELECT COUNT(*) FROM "Other"."Quoted "" Table"') == 2
    assert not list(tableau_system[1].source_root.glob('*.hyper'))
