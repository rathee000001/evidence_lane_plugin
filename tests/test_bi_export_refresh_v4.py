"""Tableau and Power BI export acceptance across filesystem, Sources and separate owners."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from concurrent.futures import Future
from importlib import import_module

import pytest
from evidence_lane_plugin.artifact_contract import LaneArtifacts
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from evidence_lane_plugin.source_routing import load_route

from .test_native_workflow_bindings import native
from .test_powerbi_parsers_v4 import powerbi_assets as powerbi_assets  # noqa: PLC0414
from .test_powerbi_profile_v4 import call
from .test_powerbi_profile_v4 import powerbi_system as powerbi_system  # noqa: PLC0414
from .test_source_routing_v4 import registered
from .test_tableau_profile_v4 import tableau_system as tableau_system  # noqa: PLC0414
from .test_tabular_export_refresh_v4 import files, view


@pytest.fixture(params=['tableau', 'powerbi'])
def sector(request):
    family = request.param
    return {'family': family, 'system': request.getfixturevalue(family + '_system'),
        'lane': 'tableau' if family == 'tableau' else 'power_bi', 'prefix': family,
        'filename': 'datasource_test.twb' if family == 'tableau' else 'model.bim',
        'current_key': 'tableaus' if family == 'tableau' else 'powerbis',
        'module': import_module('evidence_lane_plugin.' + family + '_profile')}


def plan(sector, suffixes, *, max_input_bytes=33_554_432):
    engine, store, _ = sector['system']
    family = sector['family']
    actions = [family + '_' + suffix for suffix in suffixes]
    tools = ['Python', 'SQLite_FTS5_BM25', 'lxml', 'Tableau_Hyper_API',
        'PowerBI_TOM', 'PBIXRay', 'JSONSchema', 'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx']
    tasks = [TaskDefinition(task_id=family + '-' + str(index), title=action,
        requested_outcome='Exact exported bytes and coherent destination evidence', profile=sector['lane'],
        allowed_actions=[action], permitted_paths=['.'], permitted_tools=tools,
        acceptance_checks=list(engine.registry.get(action).verification_checks),
        budget=TaskBudget(max_input_bytes=max_input_bytes, max_output_bytes=67_108_864, max_seconds=240))
        for index, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='BI export', tasks=tasks), lease, actor_id='fixture')


def admit(sector, suffix, arguments, *, index=0, route=None):
    system = sector['system']
    task = PlanStore(system[1]).task(sector['prefix'] + '-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': sector['family'] + '_' + suffix,
        'arguments': arguments,
        **({'source_route': {'route_id': route, 'occurrence_ordinals': [1]}} if route else {})}, expected_revision=1)


def wait(sector, admission, expected='verified'):
    assert admission.status == 'queued', admission.error
    store = sector['system'][1]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            with store.lane('plan').connection(read_only=True) as connection:
                row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (admission.job_id,)).fetchone())
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            time.sleep(.03)
            continue
        if row['state'] in {'verified', 'blocked'}:
            assert row['state'] == expected, row
            return row if expected == 'blocked' else json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
        time.sleep(.03)
    pytest.fail('BI export did not reach a terminal state within 90 seconds')


def execute(sector, suffix, arguments, *, index=0, route=None, expected='verified'):
    return wait(sector, admit(sector, suffix, arguments, index=index, route=route), expected)


def routed(sector):
    store = sector['system'][1]
    (store.source_root / 'other.json').write_text('[1,2]', encoding='utf-8')
    selected = registered(sector['system'], ['.', 'other.json'], action='lane_configure_routes',
        overrides={str(store.source_root): sector['lane'], str(store.source_root / 'other.json'): 'data'},
        source_assertions={str(store.source_root): {'context': 'Original BI folder'}})
    return selected['route_id']


def edit(sector, first, *, index=1):
    arguments = {'snapshot_id': first['snapshot_id'], 'expected_sha256': first['sha256']}
    if sector['family'] == 'tableau':
        response = call(sector['system'], 'tableau_query', {'snapshot_id': first['snapshot_id'], 'collection': 'calculation'})
        item = response.result['result']['rows'][0]
        arguments['replacements'] = [{'part': item['part'], 'item_id': item['item_id'],
            'attribute': 'formula', 'expected_value': item['attributes']['formula'], 'value': '1 + 2'}]
    else:
        raw = sector['system'][1].lane('power_bi').read_object(first['sha256'])
        document = json.loads(raw)
        document['model']['tables'][0]['measures'][0]['expression'] = 'SUM(Facts[Value]) + 1'
        arguments['replacements'] = [{'part': sector['filename'], 'expected_sha256': first['sha256'],
            'content_utf8': json.dumps(document)}]
    return execute(sector, 'edit', arguments, index=index)


def test_create_and_replace_publish_exact_destination_history(sector):
    system, family, lane = sector['system'], sector['family'], sector['lane']
    engine, store, _ = system
    plan(sector, ['index', 'export', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    engine.workers.operations.pop('render_lane_view')
    other_lane = 'power_bi' if lane == 'tableau' else 'tableau'
    unchanged = files(store.root / 'sectors' / other_lane)
    destination = 'exported.' + sector['filename'].split('.')[-1]
    created = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': destination}, index=1)
    indexed = created['index_refresh']['result']
    assert not created['source_index_refresh_required'] and created['view_refresh'] is None and not created['automatic_replay']
    assert indexed[family + '_id'] != first[family + '_id'] and indexed['previous_snapshot'] is None
    route = load_route(store, created['source_refresh']['route_id'])
    assert [(row['resolved_pointer'], row['lane_id']) for row in route['routes']] == [(str(store.source_root / destination), lane)]
    replaced = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': destination,
        'expected_sha256': created['after_sha256']}, index=2)
    latest = replaced['index_refresh']['result']
    assert latest['generation'] == 2 and latest['previous_snapshot'] == indexed['snapshot_id']
    assert replaced['source_refresh']['parent_route_id'] == created['source_refresh']['route_id']
    current = call(system, family + '_current').result['result'][sector['current_key']]
    assert {row['snapshot_id'] for row in current} == {first['snapshot_id'], latest['snapshot_id']}
    assert load_route(store, created['source_refresh']['route_id']) == route
    assert call(system, family + '_read', {'snapshot_id': indexed['snapshot_id']}).status == 'ok'
    assert files(store.root / 'sectors' / other_lane) == unchanged
    heads = {row['lane_id']: row['commit_id'] for row in store.lane_catalog()}
    assert heads['sources'] == heads[lane]
    assert (store.source_root / destination).read_bytes() == (store.source_root / sector['filename']).read_bytes()


@pytest.mark.parametrize('formats', [['mmd', 'dot'], []])
def test_existing_lane_view_selection_follows_exact_destination(sector, formats):
    system, lane = sector['system'], sector['lane']
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    initial = view(system, lane, formats, True)
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'],
        'filename': sector['filename'], 'expected_sha256': first['sha256']}, index=1)
    refreshed = exported['view_refresh']
    assert refreshed['generation'] == initial['generation'] + 1
    assert refreshed['snapshot_digest'] != initial['snapshot_digest'] and {row['role'] for row in refreshed['files']} == {*formats, 'pointer'}
    actual = call(system, 'lane_view_read', {'view_id': lane + '.structure'})
    assert actual.status == 'ok' and actual.result['state'] == 'fresh'
    current = actual.result['manifest']['binding']['source_head']['owners'][lane][sector['current_key']]
    assert current[0]['snapshot_id'] == exported['index_refresh']['result']['snapshot_id']


def test_routed_edit_keeps_parent_order_assertions_and_original(sector):
    system, lane = sector['system'], sector['lane']
    store = system[1]
    selected = routed(sector)
    parent = load_route(store, selected)
    original = (store.source_root / sector['filename']).read_bytes()
    plan(sector, ['index', 'edit', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']}, route=selected)
    edited = edit(sector, first)
    exported = execute(sector, 'export', {'snapshot_id': edited['snapshot_id'], 'filename': sector['filename'],
        'expected_sha256': first['sha256']}, index=2)
    child = load_route(store, exported['source_refresh']['route_id'])
    assert child['parent_route_id'] == selected and load_route(store, selected) == parent
    assert [(row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in child['routes']] == [
        (row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in parent['routes']]
    assert child['routes'][1] == parent['routes'][1]
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT claim_json FROM source_provenance WHERE batch_id=? AND claim_key='context'",
            (child['batch_id'],)).fetchone()[0] == '"Original BI folder"'
    manifest, facts = sector['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert manifest['source_route'] == {'route_id': exported['source_refresh']['route_id'], 'occurrence_ordinals': [1]}
    assert manifest['previous_snapshot'] == edited['snapshot_id'] and store.lane(lane).read_object(manifest['source_object']) == original
    if sector['family'] == 'tableau':
        assert next(item for item in facts['items'] if item['kind'] == 'calculation')['attributes']['formula'] == '1 + 2'
    else:
        assert next(item for item in facts['items'] if item['kind'] == 'measure')['data']['expression'] == 'SUM(Facts[Value]) + 1'
    assert (store.source_root / 'other.json').read_text() == '[1,2]'


@pytest.mark.parametrize('failure', ['parser', 'parent_changed'])
def test_known_failure_precedes_file_effect_and_lane_writes(sector, monkeypatch, failure):
    system = sector['system']
    engine, store, _ = system
    selected = routed(sector)
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']}, route=selected)
    if failure == 'parent_changed':
        (store.source_root / 'other.json').write_text('[]')
    else:
        original = engine.workers.submit
        def submit(operation, arguments):
            if operation == sector['family'] + '_parse_content':
                future = Future()
                future.set_result({'status': 'error', 'code': 'fixture_parser_failure'})
                return future
            return original(operation, arguments)
        monkeypatch.setattr(engine.workers, 'submit', submit)
    before = files(store.source_root), files(store.lane('sources').folder), files(store.lane(sector['lane']).folder)
    execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': sector['filename'],
        'expected_sha256': first['sha256']}, index=1, expected='blocked')
    assert (files(store.source_root), files(store.lane('sources').folder), files(store.lane(sector['lane']).folder)) == before
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['index', 'view', 'source_membership'])
def test_postwrite_failure_keeps_effect_and_restores_owner_selectors(sector, monkeypatch, failure):
    system, lane, module = sector['system'], sector['lane'], sector['module']
    store = system[1]
    selected = routed(sector)
    plan(sector, ['index', 'edit', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']}, route=selected)
    edited = edit(sector, first)
    initial_view = view(system, lane, [], True)
    before = files(store.lane('sources').folder)
    database = store.lane('sources').database.read_bytes()
    if failure in {'index', 'view'}:
        def fail(*args, **kwargs):
            raise LaneError('FIXTURE_REFRESH_FAILURE', 'Injected after file write and confirmed effect.')
        monkeypatch.setattr(module if failure == 'index' else LaneArtifacts,
            '_natural' if failure == 'index' else 'refresh', fail)
    else:
        original = module.os.replace
        def replace_file(source, target):
            value = original(source, target)
            if target == store.source_root / sector['filename']:
                (store.source_root / 'extra.txt').write_text('Unrelated concurrent source change')
            return value
        monkeypatch.setattr(module.os, 'replace', replace_file)
    blocked = execute(sector, 'export', {'snapshot_id': edited['snapshot_id'], 'filename': sector['filename'],
        'expected_sha256': first['sha256']}, index=2, expected='blocked')
    assert hashlib.sha256((store.source_root / sector['filename']).read_bytes()).hexdigest() == edited['sha256']
    assert store.lane('sources').database.read_bytes() == database
    after = files(store.lane('sources').folder)
    assert all(after[key] == value for key, value in before.items())
    assert module.current_snapshot(store, first[sector['family'] + '_id']) == edited['snapshot_id']
    actual = call(system, 'lane_view_read', {'view_id': lane + '.structure'})
    assert actual.result['snapshot_digest'] == initial_view['snapshot_digest']
    with store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT state,evidence_object FROM jobs_effects WHERE job_id=?', (blocked['job_id'],)).fetchone()
        assert effect['state'] == 'confirmed'
        assert json.loads(store.lane('plan').read_object(effect['evidence_object']))['exact_bytes_verified']


@pytest.mark.parametrize('field', ['source_receipt', 'snapshot', 'effect'])
def test_completion_requires_exact_job_effect_source_and_snapshot(sector, monkeypatch, field):
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    original = sector['module'].result
    def altered(store, action, body):
        if 'source_refresh' in body:
            if field == 'source_receipt':
                body['source_refresh']['receipt_id'] = 'missing-fixture-receipt'
            elif field == 'snapshot':
                body['index_refresh']['result']['snapshot_id'] = first['snapshot_id']
            else:
                body['effect_id'] = 'missing-fixture-effect'
        return original(store, action, body)
    monkeypatch.setattr(sector['module'], 'result', altered)
    execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'out.' + sector['filename'].split('.')[-1]},
        index=1, expected='blocked')


def test_unavailable_selected_graph_tool_rejects_before_admission(sector, monkeypatch):
    engine, store, _ = sector['system']
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    view(sector['system'], sector['lane'], ['mmd'], True)
    original = engine.registry.tool_router.observer
    def observer(tool, context):
        return {'ready': False, 'reason': 'fixture graph provider unavailable'} if tool == 'LangGraph_Mermaid_engine' else original(tool, context)
    monkeypatch.setattr(engine.registry.tool_router, 'observer', observer)
    before = files(store.root), files(store.source_root)
    response = admit(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'out.' + sector['filename'].split('.')[-1]}, index=1)
    assert response.status == 'error' and response.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert (files(store.root), files(store.source_root)) == before


def test_stdio_export_returns_readable_sources_and_destination(sector):
    engine, store, _ = sector['system']
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            task = PlanStore(store).task(sector['prefix'] + '-1', expected_revision=1)
            response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                    'action': sector['family'] + '_export', 'arguments': {'snapshot_id': first['snapshot_id'],
                        'filename': 'stdio.' + sector['filename'].split('.')[-1]}}})
            body = response.structuredContent
            assert body['status'] == 'queued', body
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    with store.lane('plan').connection(read_only=True) as connection:
                        row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (body['job_id'],)).fetchone())
                except LaneError as error:
                    if error.code != 'PROJECT_RECOVERY_REQUIRED':
                        raise
                    await asyncio.sleep(.03)
                    continue
                if row['state'] in {'verified', 'blocked'}:
                    break
                await asyncio.sleep(.03)
            assert row['state'] == 'verified', row
            exported = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
            assert exported['source_index_refresh_required'] is False
            sources = await session.call_tool('source_routes_read', {'project_id': store.project_id,
                'arguments': {'route_id': exported['source_refresh']['route_id']}})
            assert sources.structuredContent['status'] == 'ok', sources.structuredContent
            queried = await session.call_tool(sector['family'] + '_query', {'project_id': store.project_id,
                'arguments': {'snapshot_id': exported['index_refresh']['result']['snapshot_id'], 'collection': 'calculation' if sector['family'] == 'tableau' else 'measure'}})
            assert queried.structuredContent['status'] == 'ok', queried.structuredContent
            assert len(queried.structuredContent['result']['result']['rows']) >= 1
    with LocalEndpoint(engine):
        asyncio.run(run())


def test_routed_refresh_requires_explicit_current_selection(sector):
    selected = routed(sector)
    plan(sector, ['index', 'refresh'])
    first = execute(sector, 'index', {'filename': sector['filename']}, route=selected)
    store = sector['system'][1]
    before = files(store.lane(sector['lane']).folder), files(store.source_root)
    blocked = execute(sector, 'refresh', {'filename': sector['filename'], 'expected_snapshot': first['snapshot_id']},
        index=1, expected='blocked')
    assert blocked['error_code'] == 'SOURCE_ROUTE_SELECTION_REQUIRED'
    assert before == (files(store.lane(sector['lane']).folder), files(store.source_root))

@pytest.mark.parametrize('fits', [True, False])
def test_destination_parser_options_and_file_bound_are_preserved(sector, fits):
    store = sector['system'][1]
    name = 'destination.' + sector['filename'].split('.')[-1]
    if sector['family'] == 'tableau':
        raw = b'<workbook><datasources/></workbook>'
        options = {'inspect_extracts': False, 'max_rows_per_table': 1}
    else:
        from .powerbi_fixtures import model_document
        model = model_document()
        model['model']['tables'] = []
        raw = json.dumps(model).encode()
        options = {'inspect_models': False, 'max_rows_per_table': 1}
    (store.source_root / name).write_bytes(raw)
    plan(sector, ['index', 'index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    assert len(store.lane(sector['lane']).read_object(first['sha256'])) > len(raw)
    destination = execute(sector, 'index', {'filename': name,
        'max_file_bytes': 8_388_608 if fits else len(raw), **options}, index=1)
    previous = sector['module'].read_snapshot(store, destination['snapshot_id'])
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': name,
        'expected_sha256': destination['sha256']}, index=2, expected='verified' if fits else 'blocked')
    if fits:
        manifest, facts = sector['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
        assert facts['parse_options'] == previous[1]['parse_options']
        assert manifest['capture_limits']['max_file_bytes'] == 8_388_608
    else:
        assert exported['error_code'] == sector['family'].upper() + '_FILE_BYTE_BUDGET'
        assert (store.source_root / name).read_bytes() == raw
        with store.lane('plan').connection(read_only=True) as connection:
            assert connection.execute('SELECT COUNT(*) FROM jobs_effects').fetchone()[0] == 0


def bi_sector(system, family='powerbi', filename='model.bim'):
    return {'family': family, 'prefix': family, 'system': system, 'filename': filename,
        'lane': 'tableau' if family == 'tableau' else 'power_bi',
        'current_key': 'tableaus' if family == 'tableau' else 'powerbis',
        'module': import_module('evidence_lane_plugin.' + family + '_profile')}


@pytest.mark.parametrize('name', ['abc.pbix', 'old-schema17-DataTable.pbix', 'live-connection-pbiservice.pbix'])
def test_native_pbix_export_preserves_measured_facts(powerbi_system, name):
    from .test_powerbi_parsers_v4 import SAMPLES
    sector = bi_sector(powerbi_system, filename=name)
    store = powerbi_system[1]
    (store.source_root / name).write_bytes((SAMPLES / name).read_bytes())
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': name, 'max_rows_per_table': 2})
    original_facts = sector['module'].read_snapshot(store, first['snapshot_id'])[1]
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'exported.pbix'}, index=1)
    manifest, facts = sector['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert manifest['raw_object'] == first['sha256'] and facts == original_facts
    assert not facts['fidelity']['dax_evaluated'] and not facts['fidelity']['layout_verified']


def test_native_hyper_export_preserves_quoted_tables_and_samples(tableau_system):
    sector = bi_sector(tableau_system, 'tableau', 'fixture.hyper')
    plan(sector, ['generate', 'export'])
    created = execute(sector, 'generate', {'logical_name': 'fixture.hyper', 'tables': [
        {'schema_name': 'Other', 'name': 'Quoted " Table', 'columns': [{'name': 'Value', 'type': 'big_int'}],
            'rows': [[1], [2]]}]})
    exported = execute(sector, 'export', {'snapshot_id': created['snapshot_id'], 'filename': 'exported.hyper'}, index=1)
    queried = call(tableau_system, 'tableau_query', {'snapshot_id': exported['index_refresh']['result']['snapshot_id'],
        'collection': 'hyper_row'})
    assert [row['values'] for row in queried.result['result']['rows']] == [[1], [2]]
    route = load_route(tableau_system[1], exported['source_refresh']['route_id'])
    assert route['routes'][0]['kind'] == 'file'


@pytest.mark.parametrize('kind', ['twbx', 'tds', 'tdsx', 'tde'])
def test_tableau_package_datasource_and_opaque_export_paths(tableau_system, kind):
    from .test_source_archive_capture_v4 import archive_bytes
    store = tableau_system[1]
    name = 'fixture.' + kind
    xml = b'<datasource name="Fixture"><column name="[Calc]"><calculation formula="1 + 2"/></column></datasource>'
    raw = ((store.source_root / 'TABLEAU_10_TWBX.twbx').read_bytes() if kind == 'twbx'
        else archive_bytes({'fixture.tds': xml}) if kind == 'tdsx' else xml if kind == 'tds' else b'opaque legacy extract fixture')
    (store.source_root / name).write_bytes(raw)
    sector = bi_sector(tableau_system, 'tableau', name)
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': name})
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'exported.' + kind}, index=1)
    assert (store.source_root / ('exported.' + kind)).read_bytes() == raw
    assert exported['index_refresh']['result']['sha256'] == first['sha256']


@pytest.mark.parametrize('reject_budget', [False, True])
def test_project_zip_export_preserves_companions_and_bounded_archive_provenance(powerbi_system, monkeypatch, reject_budget):
    from evidence_lane_plugin.source_routing import SourceMutationRefresh

    from .powerbi_fixtures import project_documents
    sector = bi_sector(powerbi_system)
    store = powerbi_system[1]
    documents = project_documents()
    original = {name: (store.source_root / name).read_bytes() for name in documents}
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': 'Example.pbip',
        'companion_files': [name for name in documents if name != 'Example.pbip']})
    if reject_budget:
        original_capture = SourceMutationRefresh.capture
        def small_capture(self):
            budget = original_capture(self)
            budget.max_archive_members = 1
            return budget
        monkeypatch.setattr(SourceMutationRefresh, 'capture', small_capture)
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'exported.zip'},
        index=1, expected='blocked' if reject_budget else 'verified')
    assert {name: (store.source_root / name).read_bytes() for name in documents} == original
    if reject_budget:
        assert exported['error_code'] == 'SOURCE_CAPTURE_ARCHIVE_BUDGET'
        assert not (store.source_root / 'exported.zip').exists()
        with store.lane('plan').connection(read_only=True) as connection:
            assert connection.execute('SELECT COUNT(*) FROM jobs_effects').fetchone()[0] == 0
        return
    manifest, facts = sector['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert manifest['source_members'] == {'exported.zip': first['sha256']}
    assert facts['parse_options']['entrypoint'] == 'Example.pbip'
    assert sector['module'].read_snapshot(store, first['snapshot_id'])[0]['source_members'] == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in original.items()}
    route = load_route(store, exported['source_refresh']['route_id'])
    assert len(route['routes']) == 1 and route['routes'][0]['kind'] == 'zip'
    assert route['routes'][0]['included_member_count'] == len(documents)
    with store.lane('receipts').connection(read_only=True) as connection:
        receipt = json.loads(connection.execute('SELECT body_json FROM receipts WHERE receipt_id=?',
            (exported['source_refresh']['receipt_id'],)).fetchone()[0])
    assert receipt['archive_capture'] == 'bounded_members_v1'


def test_large_companion_package_exports_and_refreshes_with_explicit_zip_bound(powerbi_system):
    import io
    import random

    from PIL import Image

    from .powerbi_fixtures import project_documents
    sector = bi_sector(powerbi_system)
    store = powerbi_system[1]
    names = list(project_documents())
    for index in range(2):
        name = f'Example.Report/StaticResources/RegisteredResources/noise{index}.png'
        output = io.BytesIO()
        Image.frombytes('RGB', (2048, 768), random.Random(index).randbytes(2048 * 768 * 3)).save(output, 'PNG')
        path = store.source_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(output.getvalue())
        assert path.stat().st_size < 8_388_608
        names.append(name)
    plan(sector, ['index', 'export', 'refresh'])
    first = execute(sector, 'index', {'filename': 'Example.pbip',
        'companion_files': [name for name in names if name != 'Example.pbip']})
    assert 8_388_608 < first['bytes'] < 16_777_216
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'large.zip'}, index=1)
    destination = exported['index_refresh']['result']
    manifest = sector['module'].read_snapshot(store, destination['snapshot_id'])[0]
    assert manifest['capture_limits']['max_file_bytes'] == 16_777_216
    refreshed = execute(sector, 'refresh', {'filename': 'large.zip', 'max_file_bytes': 16_777_216,
        'expected_snapshot': destination['snapshot_id']}, index=2)
    assert refreshed['sha256'] == first['sha256'] and refreshed['generation'] == 2


def test_zip_bound_does_not_expand_companion_or_plain_model_limits():
    from evidence_lane_plugin.powerbi_contracts import PowerBiIndex
    from pydantic import ValidationError
    assert PowerBiIndex(filename='package.zip', max_file_bytes=16_777_216).max_file_bytes == 16_777_216
    for arguments in [{'filename': 'model.bim'}, {'filename': 'Example.pbip', 'companion_files': ['model.bim']}]:
        with pytest.raises(ValidationError):
            PowerBiIndex(**arguments, max_file_bytes=8_388_609)

