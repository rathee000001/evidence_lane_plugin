"""Word and PowerPoint export acceptance across filesystem, Sources and owners."""
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
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.source_routing import load_route

from .test_document_profile_v4 import call, create_plan
from .test_document_profile_v4 import document_system as document_system  # noqa: PLC0414
from .test_document_verification_v4 import odt_fixture
from .test_native_workflow_bindings import native
from .test_presentation_profile_v4 import plan as ppt_plan
from .test_presentation_profile_v4 import (
    presentation_system as presentation_system,  # noqa: PLC0414
)
from .test_source_routing_v4 import registered
from .test_tabular_export_refresh_v4 import files, view


@pytest.fixture(params=['document', 'presentation'])
def office(request):
    family = request.param
    system = request.getfixturevalue(family + '_system')
    return {'family': family, 'system': system, 'lane': 'docs' if family == 'document' else 'ppt',
        'prefix': 'doc' if family == 'document' else 'ppt',
        'filename': 'fixture.docx' if family == 'document' else 'fixture.pptx',
        'module': import_module('evidence_lane_plugin.' + family + '_profile')}


def plan(office, suffixes):
    function = create_plan if office['family'] == 'document' else ppt_plan
    function(office['system'], [office['family'] + '_' + suffix for suffix in suffixes])


def admit(office, suffix, arguments, *, index=0, route=None):
    system = office['system']
    task = PlanStore(system[1]).task(office['prefix'] + '-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': office['family'] + '_' + suffix,
        'arguments': arguments,
        **({'source_route': {'route_id': route, 'occurrence_ordinals': [1]}} if route else {})}, expected_revision=1)


def wait(office, admission, expected='verified'):
    assert admission.status == 'queued', admission.error
    store = office['system'][1]
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
            office['system'][0].delta.owned_completion(admission.job_id).result(
                timeout=max(1, deadline - time.monotonic())
            )
            return row if expected == 'blocked' else json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
        time.sleep(.03)
    pytest.fail('Office export did not reach a terminal state within 90 seconds')


def execute(office, suffix, arguments, *, index=0, route=None, expected='verified'):
    return wait(office, admit(office, suffix, arguments, index=index, route=route), expected)


def routed(office):
    store = office['system'][1]
    (store.source_root / 'other.json').write_text('[1,2]', encoding='utf-8')
    selected = registered(office['system'], ['.', 'other.json'], action='lane_configure_routes',
        overrides={str(store.source_root): office['lane'], str(store.source_root / 'other.json'): 'data'},
        source_assertions={str(store.source_root): {'context': 'Original Office folder'}})
    return selected['route_id']


def edit(office, first, *, index=1):
    replacement = {'paragraph_index': 2 if office['family'] == 'document' else 1,
        'expected_text': 'The selected operation preserves the source document.' if office['family'] == 'document'
            else 'The selected operation preserves the original source.',
        'replacement_text': 'Updated destination evidence.'}
    if office['family'] == 'presentation':
        replacement['part'] = 'ppt/slides/slide1.xml'
    return execute(office, 'edit', {'snapshot_id': first['snapshot_id'], 'expected_sha256': first['sha256'],
        'replacements': [replacement]}, index=index)


def test_create_and_replace_publish_exact_destination_history(office):
    system, family, lane = office['system'], office['family'], office['lane']
    engine, store, _ = system
    plan(office, ['index', 'export', 'export'])
    first = execute(office, 'index', {'filename': office['filename']})
    engine.workers.operations.pop('render_lane_view')
    other_lane = 'ppt' if lane == 'docs' else 'docs'
    unchanged = files(store.root / 'sectors' / other_lane)
    destination = 'exported.' + office['filename'].split('.')[-1]
    created = execute(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': destination}, index=1)
    indexed = created['index_refresh']['result']
    assert not created['source_index_refresh_required'] and created['view_refresh'] is None and not created['automatic_replay']
    assert indexed[family + '_id'] != first[family + '_id'] and indexed['previous_snapshot'] is None
    route = load_route(store, created['source_refresh']['route_id'])
    assert [(row['resolved_pointer'], row['lane_id']) for row in route['routes']] == [(str(store.source_root / destination), lane)]
    replaced = execute(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': destination,
        'expected_sha256': created['after_sha256']}, index=2)
    latest = replaced['index_refresh']['result']
    assert latest['generation'] == 2 and latest['previous_snapshot'] == indexed['snapshot_id']
    assert replaced['source_refresh']['parent_route_id'] == created['source_refresh']['route_id']
    current = call(system, family + '_current').result['result'][family + 's']
    assert {row['snapshot_id'] for row in current} == {first['snapshot_id'], latest['snapshot_id']}
    assert load_route(store, created['source_refresh']['route_id']) == route
    assert call(system, family + '_read', {'snapshot_id': indexed['snapshot_id']}).status == 'ok'
    assert files(store.root / 'sectors' / other_lane) == unchanged
    heads = {row['lane_id']: row['commit_id'] for row in store.lane_catalog()}
    assert heads['sources'] == heads[lane]
    assert (store.source_root / destination).read_bytes() == (store.source_root / office['filename']).read_bytes()


@pytest.mark.parametrize('formats', [['mmd', 'dot'], []])
def test_existing_lane_view_selection_follows_exact_destination(office, formats):
    system, lane = office['system'], office['lane']
    plan(office, ['index', 'export'])
    first = execute(office, 'index', {'filename': office['filename']})
    initial = view(system, lane, formats, True)
    exported = execute(office, 'export', {'snapshot_id': first['snapshot_id'],
        'filename': office['filename'], 'expected_sha256': first['sha256']}, index=1)
    refreshed = exported['view_refresh']
    assert refreshed['generation'] == initial['generation'] + 1
    assert refreshed['snapshot_digest'] != initial['snapshot_digest'] and {row['role'] for row in refreshed['files']} == {*formats, 'pointer'}
    actual = call(system, 'lane_view_read', {'view_id': lane + '.structure'})
    assert actual.status == 'ok' and actual.result['state'] == 'fresh'
    current = actual.result['manifest']['binding']['source_head']['owners'][lane][office['family'] + 's']
    assert current[0]['snapshot_id'] == exported['index_refresh']['result']['snapshot_id']


def test_routed_edit_keeps_parent_order_assertions_and_original(office):
    system, lane = office['system'], office['lane']
    store = system[1]
    selected = routed(office)
    parent = load_route(store, selected)
    original = (store.source_root / office['filename']).read_bytes()
    plan(office, ['index', 'edit', 'export'])
    first = execute(office, 'index', {'filename': office['filename']}, route=selected)
    edited = edit(office, first)
    exported = execute(office, 'export', {'snapshot_id': edited['snapshot_id'], 'filename': office['filename'],
        'expected_sha256': first['sha256']}, index=2)
    child = load_route(store, exported['source_refresh']['route_id'])
    assert child['parent_route_id'] == selected and load_route(store, selected) == parent
    assert [(row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in child['routes']] == [
        (row['ordinal'], row['supplied_pointer'], row['lane_id']) for row in parent['routes']]
    assert child['routes'][1] == parent['routes'][1]
    with store.lane('sources').connection(read_only=True) as connection:
        assert connection.execute("SELECT claim_json FROM source_provenance WHERE batch_id=? AND claim_key='context'",
            (child['batch_id'],)).fetchone()[0] == '"Original Office folder"'
    manifest, facts = office['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert manifest['source_route'] == {'route_id': exported['source_refresh']['route_id'], 'occurrence_ordinals': [1]}
    assert manifest['previous_snapshot'] == edited['snapshot_id'] and store.lane(lane).read_object(manifest['source_object']) == original
    assert any(item.get('text') == 'Updated destination evidence.' for item in facts['items'])
    assert (store.source_root / 'other.json').read_text() == '[1,2]'


@pytest.mark.parametrize('failure', ['parser', 'parent_changed'])
def test_known_failure_precedes_file_effect_and_lane_writes(office, monkeypatch, failure):
    system = office['system']
    engine, store, _ = system
    selected = routed(office)
    plan(office, ['index', 'export'])
    first = execute(office, 'index', {'filename': office['filename']}, route=selected)
    if failure == 'parent_changed':
        (store.source_root / 'other.json').write_text('[]')
    else:
        original = engine.workers.submit
        def submit(operation, arguments):
            if operation == office['family'] + '_parse_content':
                future = Future()
                future.set_result({'status': 'error', 'code': 'fixture_parser_failure'})
                return future
            return original(operation, arguments)
        monkeypatch.setattr(engine.workers, 'submit', submit)
    before = files(store.source_root), files(store.lane('sources').folder), files(store.lane(office['lane']).folder)
    execute(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': office['filename'],
        'expected_sha256': first['sha256']}, index=1, expected='blocked')
    assert (files(store.source_root), files(store.lane('sources').folder), files(store.lane(office['lane']).folder)) == before
    with store.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


@pytest.mark.parametrize('failure', ['index', 'view', 'source_membership'])
def test_postwrite_failure_keeps_effect_and_restores_owner_selectors(office, monkeypatch, failure):
    system, lane, module = office['system'], office['lane'], office['module']
    store = system[1]
    selected = routed(office)
    plan(office, ['index', 'edit', 'export'])
    first = execute(office, 'index', {'filename': office['filename']}, route=selected)
    edited = edit(office, first)
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
            if target == store.source_root / office['filename']:
                (store.source_root / 'extra.txt').write_text('Unrelated concurrent source change')
            return value
        monkeypatch.setattr(module.os, 'replace', replace_file)
    blocked = execute(office, 'export', {'snapshot_id': edited['snapshot_id'], 'filename': office['filename'],
        'expected_sha256': first['sha256']}, index=2, expected='blocked')
    assert hashlib.sha256((store.source_root / office['filename']).read_bytes()).hexdigest() == edited['sha256']
    assert store.lane('sources').database.read_bytes() == database
    after = files(store.lane('sources').folder)
    assert all(after[key] == value for key, value in before.items())
    assert module.current_snapshot(store, first[office['family'] + '_id']) == edited['snapshot_id']
    actual = call(system, 'lane_view_read', {'view_id': lane + '.structure'})
    assert actual.result['snapshot_digest'] == initial_view['snapshot_digest']
    with store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT state,evidence_object FROM jobs_effects WHERE job_id=?', (blocked['job_id'],)).fetchone()
        assert effect['state'] == 'confirmed'
        assert json.loads(store.lane('plan').read_object(effect['evidence_object']))['exact_bytes_verified']


@pytest.mark.parametrize('field', ['source_receipt', 'snapshot', 'effect'])
def test_completion_requires_exact_job_effect_source_and_snapshot(office, monkeypatch, field):
    plan(office, ['index', 'export'])
    first = execute(office, 'index', {'filename': office['filename']})
    original = office['module'].result
    def altered(store, action, body):
        if 'source_refresh' in body:
            if field == 'source_receipt':
                body['source_refresh']['receipt_id'] = 'missing-fixture-receipt'
            elif field == 'snapshot':
                body['index_refresh']['result']['snapshot_id'] = first['snapshot_id']
            else:
                body['effect_id'] = 'missing-fixture-effect'
        return original(store, action, body)
    monkeypatch.setattr(office['module'], 'result', altered)
    execute(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'out.' + office['filename'].split('.')[-1]},
        index=1, expected='blocked')


def test_unavailable_selected_graph_tool_rejects_before_admission(office, monkeypatch):
    engine, store, _ = office['system']
    plan(office, ['index', 'export'])
    first = execute(office, 'index', {'filename': office['filename']})
    view(office['system'], office['lane'], ['mmd'], True)
    original = engine.registry.tool_router.observer
    def observer(tool, context):
        return {'ready': False, 'reason': 'fixture graph provider unavailable'} if tool == 'LangGraph_Mermaid_engine' else original(tool, context)
    monkeypatch.setattr(engine.registry.tool_router, 'observer', observer)
    before = files(store.root), files(store.source_root)
    response = admit(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'out.' + office['filename'].split('.')[-1]}, index=1)
    assert response.status == 'error' and response.error.code == 'TOOL_ROUTE_UNAVAILABLE'
    assert (files(store.root), files(store.source_root)) == before


def test_stdio_export_returns_readable_sources_and_destination(office):
    engine, store, _ = office['system']
    plan(office, ['index', 'export'])
    first = execute(office, 'index', {'filename': office['filename']})
    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            task = PlanStore(store).task(office['prefix'] + '-1', expected_revision=1)
            response = await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                    'action': office['family'] + '_export', 'arguments': {'snapshot_id': first['snapshot_id'],
                        'filename': 'stdio.' + office['filename'].split('.')[-1]}}})
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
            queried = await session.call_tool(office['family'] + '_query', {'project_id': store.project_id,
                'arguments': {'snapshot_id': exported['index_refresh']['result']['snapshot_id'], 'collection': 'table'}})
            assert queried.structuredContent['status'] == 'ok', queried.structuredContent
            assert len(queried.structuredContent['result']['result']['rows']) >= 1
    with LocalEndpoint(engine):
        asyncio.run(run())


@pytest.mark.parametrize(('extension', 'raw'), [
    ('odt', odt_fixture()), ('md', b'# Evidence\nExact text retained'),
    ('html', b'<h1>Evidence</h1><script>never_execute()</script><p>Exact text retained</p>')])
def test_other_word_formats_export_without_conversion_or_rendering(document_system, extension, raw):
    office = {'family': 'document', 'prefix': 'doc', 'system': document_system}
    store = document_system[1]
    (store.source_root / ('input.' + extension)).write_bytes(raw)
    plan(office, ['index', 'export'])
    first = execute(office, 'index', {'filename': 'input.' + extension})
    exported = execute(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'out.' + extension}, index=1)
    assert exported['index_refresh']['result']['fidelity']['layout'] == 'not_rendered'
    assert (store.source_root / ('out.' + extension)).read_bytes() == raw


@pytest.mark.parametrize('enough_budget', [True, False])
def test_large_office_export_obeys_explicit_plan_input_budget(document_system, enough_budget):
    office = {'family': 'document', 'prefix': 'doc', 'system': document_system}
    store = document_system[1]
    raw = b' ' * 1_100_000 + b'<h1>Bounded document</h1><p>Exact source preserved.</p>'
    (store.source_root / 'large.html').write_bytes(raw)
    create_plan(document_system, ['document_index', 'document_export'],
        max_input_bytes=4_194_304 if enough_budget else 1_048_576)
    first = execute(office, 'index', {'filename': 'large.html', 'max_file_bytes': 2_097_152})
    output = execute(office, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'output.html'}, index=1,
        expected='verified' if enough_budget else 'blocked')
    if enough_budget:
        assert output['index_refresh']['result']['bytes'] == len(raw)
        assert (store.source_root / 'output.html').read_bytes() == raw
    else:
        assert output['error_code'] == 'DELTA_INPUT_BUDGET' and not (store.source_root / 'output.html').exists()
        with store.lane('plan').connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


def test_routed_refresh_requires_an_explicit_current_route(office):
    selected = routed(office)
    plan(office, ['index', 'refresh'])
    first = execute(office, 'index', {'filename': office['filename']}, route=selected)
    before = files(office['system'][1].lane(office['lane']).folder)
    output = execute(office, 'refresh', {'filename': office['filename'], 'expected_snapshot': first['snapshot_id']},
        index=1, expected='blocked')
    assert output['error_code'] == 'SOURCE_ROUTE_SELECTION_REQUIRED'
    assert files(office['system'][1].lane(office['lane']).folder) == before
