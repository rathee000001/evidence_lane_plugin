"""Confirmed exports refresh Sources, exact destination facts and lane views."""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import Future
from pathlib import Path

import pytest
from evidence_lane_plugin import tabular_profile
from evidence_lane_plugin.artifact_contract import LaneArtifacts
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.source_routing import load_route

from .test_source_routing_v4 import finished, registered
from .test_tabular_profile_v4 import call, execute, plan, query
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


def files(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def admit(system, action, arguments, *, index, route=None):
    task = PlanStore(system[1]).task('table-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments,
        **({'source_route': {'route_id': route, 'occurrence_ordinals': [1]}} if route else {})}, expected_revision=1)


def view(system, lane_id, formats, pointer):
    preview = call(system, 'lane_view_preview', {'view_id': lane_id + '.structure'})
    assert preview.status == 'ok', preview.error
    selected = call(system, 'lane_view_refresh', {'view_id': lane_id + '.structure',
        'expected_generation': preview.result['generation'], 'contract_digest': preview.result['contract_digest'],
        'source_digest': preview.result['source_digest'], 'formats': formats, 'include_pointer': pointer})
    assert selected.status == 'ok', selected.error
    return selected.result


@pytest.mark.parametrize(('prefix', 'lane_id', 'filename', 'destination'), [
    ('spreadsheet', 'data_excel', 'fixture.xlsx', 'exported.xlsx'), ('data', 'data', 'values.json', 'exported.json')])
def test_new_and_replaced_destination_have_separate_current_identity(tabular_system, prefix, lane_id, filename, destination):
    engine, store, _ = tabular_system
    plan(tabular_system, [prefix + '_index', prefix + '_export', prefix + '_export'])
    original = execute(tabular_system, prefix + '_index', {'filename': filename})
    engine.workers.operations.pop('render_lane_view')
    other_lane = 'data' if lane_id == 'data_excel' else 'data_excel'
    unrelated = files(store.root / 'sectors' / other_lane)
    created = execute(tabular_system, prefix + '_export', {'snapshot_id': original['snapshot_id'], 'filename': destination}, index=1)
    indexed = created['index_refresh']['result']
    assert not created['source_index_refresh_required'] and not created['automatic_replay'] and created['view_refresh'] is None
    assert indexed['source_id'] != original['source_id'] and indexed['previous_snapshot'] is None
    assert (store.source_root / destination).read_bytes() == (store.source_root / filename).read_bytes()
    route = load_route(store, created['source_refresh']['route_id'])
    assert [(row['resolved_pointer'], row['lane_id']) for row in route['routes']] == [(str(store.source_root / destination), lane_id)]
    assert route['parent_route_id'] is None
    replaced = execute(tabular_system, prefix + '_export', {'snapshot_id': original['snapshot_id'], 'filename': destination,
        'expected_sha256': created['after_sha256']}, index=2)
    second = replaced['index_refresh']['result']
    assert second['source_id'] == indexed['source_id'] and second['previous_snapshot'] == indexed['snapshot_id']
    assert second['generation'] == 2 and replaced['source_refresh']['parent_route_id'] == created['source_refresh']['route_id']
    current = call(tabular_system, prefix + '_current').result['result']['files']
    assert {row['snapshot_id'] for row in current} == {original['snapshot_id'], second['snapshot_id']}
    assert load_route(store, created['source_refresh']['route_id']) == route
    assert files(store.root / 'sectors' / other_lane) == unrelated
    heads = {row['lane_id']: row['commit_id'] for row in store.lane_catalog()}
    assert heads['sources'] == heads[lane_id]
    manifest, _ = tabular_profile.read_snapshot(store, lane_id, second['snapshot_id'])
    assert manifest['source_path'] == destination and manifest['source_observation_route_id'] == replaced['source_refresh']['route_id']
    assert call(tabular_system, prefix + '_read', {'snapshot_id': indexed['snapshot_id']}).status == 'ok'


@pytest.mark.parametrize(('formats', 'pointer'), [(['mmd', 'dot'], True), ([], True)])
@pytest.mark.parametrize(('prefix', 'lane', 'filename', 'destination'), [
    ('spreadsheet', 'data_excel', 'fixture.xlsx', 'output.xlsx'), ('data', 'data', 'values.json', 'output.json')])
def test_existing_view_formats_and_locators_follow_destination(tabular_system, formats, pointer, prefix, lane, filename, destination):
    plan(tabular_system, [prefix + '_index', prefix + '_export'])
    original = execute(tabular_system, prefix + '_index', {'filename': filename})
    initial = view(tabular_system, lane, formats, pointer)
    exported = execute(tabular_system, prefix + '_export', {'snapshot_id': original['snapshot_id'], 'filename': destination}, index=1)
    refreshed = exported['view_refresh']
    assert refreshed['generation'] == initial['generation'] + 1 and refreshed['snapshot_digest'] != initial['snapshot_digest']
    assert {row['role'] for row in refreshed['files']} == {*formats, 'pointer'}
    actual = call(tabular_system, 'lane_view_read', {'view_id': lane + '.structure'})
    assert actual.status == 'ok' and actual.result['state'] == 'fresh'
    manifest = actual.result['manifest']
    assert exported['index_refresh']['result']['snapshot_id'] in {
        row['snapshot_id'] for row in manifest['binding']['source_head']['owners'][lane]['files']}


def test_routed_replacement_preserves_parent_sources_assertions_and_history(tabular_system):
    _, store, _ = tabular_system
    root = str(store.source_root)
    selected = registered(tabular_system, ['.', 'values.json'], action='lane_configure_routes',
        overrides={root: 'data_excel', str(store.source_root / 'values.json'): 'data'},
        source_assertions={root: {'context': 'workbook folder'}})
    parent = load_route(store, selected['route_id'])
    plan(tabular_system, ['spreadsheet_index', 'spreadsheet_edit', 'spreadsheet_export'])
    first = finished(tabular_system, admit(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'},
        index=0, route=selected['route_id']))['result']['result']
    edited = execute(tabular_system, 'spreadsheet_edit', {'snapshot_id': first['snapshot_id'], 'expected_sha256': first['sha256'],
        'replacements': [{'sheet': 'Inputs', 'cell': 'B2', 'expected_value': {'type': 'number', 'value': '3'},
            'replacement_value': {'type': 'number', 'value': '7'}}]}, index=1)
    other = (store.source_root / 'values.json').read_bytes()
    output = execute(tabular_system, 'spreadsheet_export', {'snapshot_id': edited['snapshot_id'],
        'filename': 'fixture.xlsx', 'expected_sha256': first['sha256']}, index=2)
    child = load_route(store, output['source_refresh']['route_id'])
    assert child['parent_route_id'] == selected['route_id']
    assert [(row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in child['routes']] == [
        (row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in parent['routes']]
    assert child['routes'][1] == parent['routes'][1] and load_route(store, selected['route_id']) == parent
    assert (store.source_root / 'values.json').read_bytes() == other
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT claim_json FROM source_provenance WHERE batch_id=? AND claim_key='context'",
            (child['batch_id'],)).fetchone()[0] == '"workbook folder"'
    manifest, _ = tabular_profile.read_snapshot(store, 'data_excel', output['index_refresh']['result']['snapshot_id'])
    assert manifest['source_route'] == {'route_id': output['source_refresh']['route_id'], 'occurrence_ordinals': [1]}
    assert manifest['previous_snapshot'] == edited['snapshot_id']
    cells = query(tabular_system, 'spreadsheet', output['index_refresh']['result']['snapshot_id'], collection='cell')['rows']
    assert next(row for row in cells if row['sheet'] == 'Inputs' and row['cell'] == 'B2')['value']['value'] == '7'


@pytest.mark.parametrize('admitted_budget', [True, False])
def test_export_large_file_obeys_its_explicit_plan_budget(tabular_system, admitted_budget):
    store = tabular_system[1]
    path = store.source_root / 'values.json'
    path.write_bytes(b' ' * 1_100_000 + path.read_bytes())
    plan(tabular_system, ['data_index', 'data_export'], max_input_bytes=4_194_304 if admitted_budget else 1_048_576)
    first = execute(tabular_system, 'data_index', {'filename': 'values.json', 'max_file_bytes': 2_097_152})
    output = execute(tabular_system, 'data_export', {'snapshot_id': first['snapshot_id'], 'filename': 'large.json'},
        index=1, expected='verified' if admitted_budget else 'blocked')
    if not admitted_budget:
        assert output['error_code'] == 'DELTA_INPUT_BUDGET' and not (store.source_root / 'large.json').exists()
        with store.lane('plan').connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0
        return
    assert output['index_refresh']['result']['bytes'] > 1_048_576
    assert (store.source_root / 'large.json').read_bytes() == path.read_bytes()


@pytest.mark.parametrize('failure', ['parser', 'budget', 'parent_changed'])
def test_known_failure_precedes_export_and_source_publication(tabular_system, monkeypatch, failure):
    engine, store, _ = tabular_system
    selected = registered(tabular_system, ['.', 'values.json'], action='lane_configure_routes',
        overrides={str(store.source_root): 'data_excel', str(store.source_root / 'values.json'): 'data'})
    plan(tabular_system, ['spreadsheet_index', 'spreadsheet_export'])
    first = finished(tabular_system, admit(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'},
        index=0, route=selected['route_id']))['result']['result']
    if failure == 'parent_changed':
        (store.source_root / 'values.json').write_text('[]')
    elif failure == 'budget':
        monkeypatch.setattr(tabular_profile, 'MAX_FILE_BYTES', 2)
    else:
        original = engine.workers.submit
        def submit(operation, arguments):
            if operation == 'tabular_parse_content':
                future = Future()
                future.set_result({'status': 'error', 'code': 'fixture_parser_failure'})
                return future
            return original(operation, arguments)
        monkeypatch.setattr(engine.workers, 'submit', submit)
    before = files(store.source_root), files(store.lane('sources').folder), files(store.lane('data_excel').folder)
    execute(tabular_system, 'spreadsheet_export', {'snapshot_id': first['snapshot_id'], 'filename': 'fixture.xlsx',
        'expected_sha256': first['sha256']}, index=1, expected='blocked')
    assert (files(store.source_root), files(store.lane('sources').folder), files(store.lane('data_excel').folder)) == before
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['index', 'view', 'source_membership'])
def test_failure_after_write_retains_confirmed_effect_and_restores_selectors(tabular_system, monkeypatch, failure):
    _, store, _ = tabular_system
    selected = registered(tabular_system, ['.', 'values.json'], action='lane_configure_routes',
        overrides={str(store.source_root): 'data_excel', str(store.source_root / 'values.json'): 'data'})
    plan(tabular_system, ['spreadsheet_index', 'spreadsheet_export'])
    first = finished(tabular_system, admit(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'},
        index=0, route=selected['route_id']))['result']['result']
    initial_view = view(tabular_system, 'data_excel', [], True)
    before = files(store.lane('sources').folder)
    database = store.lane('sources').database.read_bytes()
    if failure == 'index':
        def fail(*args, **kwargs):
            raise LaneError('FIXTURE_INDEX_FAILURE', 'Injected after source staging.')
        monkeypatch.setattr(tabular_profile, 'natural_file', fail)
    elif failure == 'view':
        def fail(*args, **kwargs):
            raise LaneError('FIXTURE_VIEW_FAILURE', 'Injected after sector staging.')
        monkeypatch.setattr(LaneArtifacts, 'refresh', fail)
    else:
        original = tabular_profile.os.replace
        def replace_file(source, target):
            value = original(source, target)
            if target == store.source_root / 'fixture.xlsx':
                (store.source_root / 'extra.txt').write_text('another source changed')
            return value
        monkeypatch.setattr(tabular_profile.os, 'replace', replace_file)
    blocked = execute(tabular_system, 'spreadsheet_export', {'snapshot_id': first['snapshot_id'], 'filename': 'fixture.xlsx',
        'expected_sha256': first['sha256']}, index=1, expected='blocked')
    assert hashlib.sha256((store.source_root / 'fixture.xlsx').read_bytes()).hexdigest() == first['sha256']
    assert store.lane('sources').database.read_bytes() == database
    after = files(store.lane('sources').folder)
    assert all(after[key] == value for key, value in before.items())
    assert call(tabular_system, 'spreadsheet_current').result['result']['files'][0]['snapshot_id'] == first['snapshot_id']
    actual = call(tabular_system, 'lane_view_read', {'view_id': 'data_excel.structure'})
    assert actual.result['snapshot_digest'] == initial_view['snapshot_digest']
    with store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT state,evidence_object FROM jobs_effects WHERE job_id=?', (blocked['job_id'],)).fetchone()
        assert effect['state'] == 'confirmed'
        assert json.loads(store.lane('plan').read_object(effect['evidence_object']))['exact_bytes_verified']


@pytest.mark.parametrize('field', ['source_receipt', 'snapshot', 'effect'])
def test_tampered_completion_binding_blocks_acceptance(tabular_system, monkeypatch, field):
    plan(tabular_system, ['spreadsheet_index', 'spreadsheet_export'])
    first = execute(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'})
    original = tabular_profile.result
    def altered(store, lane_id, action, body):
        if 'source_refresh' in body:
            if field == 'source_receipt':
                body['source_refresh']['receipt_id'] = 'missing-fixture-receipt'
            elif field == 'snapshot':
                body['index_refresh']['result']['snapshot_id'] = first['snapshot_id']
            else:
                body['effect_id'] = 'missing-fixture-effect'
        return original(store, lane_id, action, body)
    monkeypatch.setattr(tabular_profile, 'result', altered)
    execute(tabular_system, 'spreadsheet_export', {'snapshot_id': first['snapshot_id'], 'filename': 'output.xlsx'}, index=1, expected='blocked')
    assert PlanStore(tabular_system[1]).task('table-1', expected_revision=1).state == 'blocked'


def test_missing_selected_graph_tool_has_no_mutation(tabular_system, monkeypatch):
    engine, store, _ = tabular_system
    plan(tabular_system, ['spreadsheet_index', 'spreadsheet_export'])
    first = execute(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'})
    view(tabular_system, 'data_excel', ['mmd'], True)
    original = engine.registry.tool_router.observer
    def observer(tool, context):
        return {'ready': False, 'reason': 'fixture exporter missing'} if tool == 'LangGraph_Mermaid_engine' else original(tool, context)
    monkeypatch.setattr(engine.registry.tool_router, 'observer', observer)
    before = files(store.root), files(store.source_root)
    response = admit(tabular_system, 'spreadsheet_export', {'snapshot_id': first['snapshot_id'], 'filename': 'output.xlsx'}, index=1)
    assert response.status == 'error' and response.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert (files(store.root), files(store.source_root)) == before


@pytest.mark.parametrize('extension', ['xls', 'xlsb', 'ods'])
def test_reference_legacy_export_keeps_exact_bytes_and_declared_value_fidelity(tabular_system, extension):
    fixture_root = Path(__file__).resolve().parent / 'fixtures/tabular/reference'
    receipt = json.loads((fixture_root / 'receipt.json').read_bytes())
    record = next(row for row in receipt['files'] if row['path'] == 'calamine/base.' + extension)
    raw = (fixture_root / record['path']).read_bytes()
    assert record['commit'] == '0a7998e50a7586f308a2d455170ec63c8135dfd3'
    assert hashlib.sha256(raw).hexdigest() == record['sha256'] and len(raw) == record['bytes']
    store = tabular_system[1]
    (store.source_root / ('legacy.' + extension)).write_bytes(raw)
    plan(tabular_system, ['spreadsheet_index_values', 'spreadsheet_export'])
    first = execute(tabular_system, 'spreadsheet_index_values', {'filename': 'legacy.' + extension})
    output = execute(tabular_system, 'spreadsheet_export', {'snapshot_id': first['snapshot_id'],
        'filename': 'exported.' + extension}, index=1)
    indexed = output['index_refresh']['result']
    assert (store.source_root / ('exported.' + extension)).read_bytes() == raw
    assert indexed['tool_evidence']['parser'] == 'python-calamine'
    assert indexed['tool_evidence']['parser_version'] == '0.8.2'
    assert indexed['fidelity']['formulas'] == 'expressions_not_extracted_values_may_be_cached'
    cells = query(tabular_system, 'spreadsheet', indexed['snapshot_id'], collection='cell', limit=100)['rows']
    assert next(row for row in cells if row['sheet'] == 'Sheet1' and row['cell'] == 'F2')['value'] == {
        'type': 'date', 'value': '2010-10-10'}
