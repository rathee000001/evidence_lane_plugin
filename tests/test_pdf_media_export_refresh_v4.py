"""PDF and media export acceptance across filesystem, Sources and separate owners."""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from concurrent.futures import Future
from importlib import import_module

import pytest
from evidence_lane_plugin.artifact_contract import LaneArtifacts
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.media_workers import media_worker_operations
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from evidence_lane_plugin.source_routing import load_route
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool

from .test_native_workflow_bindings import native
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414
from .test_pdf_profile_v4 import call
from .test_pdf_profile_v4 import pdf_system as pdf_system  # noqa: PLC0414
from .test_source_routing_v4 import registered
from .test_tabular_export_refresh_v4 import files, view


@pytest.fixture
def media_system(tmp_path, pdf_assets):
    import io

    from PIL import Image
    source = tmp_path / 'source'
    source.mkdir()
    buffer = io.BytesIO()
    Image.new('RGB', (24, 16), 'navy').save(buffer, format='PNG')
    (source / 'fixture.png').write_bytes(buffer.getvalue())
    operations = (*media_worker_operations(), WorkerOperation('render_lane_view',
        'evidence_lane_plugin.artifact_contract', 'render_lane_view_worker',
        dependencies=('langgraph', 'langchain_core', 'graphviz')))
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(operations, workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
        yield engine, store, session


@pytest.fixture(params=['pdf', 'media'])
def sector(request):
    family = request.param
    system = request.getfixturevalue(family + '_system')
    return {'family': family, 'system': system, 'lane': 'pdf_ocr' if family == 'pdf' else 'images_ocr',
        'prefix': family, 'filename': 'fixture.pdf' if family == 'pdf' else 'fixture.png',
        'current_key': 'pdfs' if family == 'pdf' else 'media',
        'module': import_module('evidence_lane_plugin.' + family + '_profile')}


def plan(sector, suffixes, *, max_input_bytes=33_554_432):
    engine, store, _ = sector['system']
    family = sector['family']
    actions = [family + '_' + ('transform' if family == 'media' and suffix == 'edit' else suffix) for suffix in suffixes]
    tools = ['Python', 'SQLite_FTS5_BM25', 'pypdf', 'PyMuPDF', 'pdfplumber', 'ReportLab', 'Pillow',
        'pypdfium2', 'RapidOCR_ONNX_Runtime', 'OpenCV', 'pytesseract_Tesseract', 'Poppler_pdftotext_pdfinfo',
        'Docling', 'defusedxml', 'FFmpeg', 'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx']
    tasks = [TaskDefinition(task_id=family + '-' + str(index), title=action,
        requested_outcome='Exact exported bytes and coherent destination evidence', profile=sector['lane'],
        allowed_actions=[action], permitted_paths=['.'], permitted_tools=tools,
        acceptance_checks=list(engine.registry.get(action).verification_checks),
        budget=TaskBudget(max_input_bytes=max_input_bytes, max_output_bytes=67_108_864, max_seconds=240))
        for index, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='PDF/media export', tasks=tasks), lease, actor_id='fixture')


def admit(sector, suffix, arguments, *, index=0, route=None):
    system = sector['system']
    task = PlanStore(system[1]).task(sector['prefix'] + '-' + str(index), expected_revision=1)
    return call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': sector['family'] + '_' + ('transform' if sector['family'] == 'media' and suffix == 'edit' else suffix),
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
            sector['system'][0].delta.owned_completion(admission.job_id).result(
                timeout=max(1, deadline - time.monotonic())
            )
            return row if expected == 'blocked' else json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
        time.sleep(.03)
    pytest.fail('PDF/media export did not reach a terminal state within 90 seconds')


def execute(sector, suffix, arguments, *, index=0, route=None, expected='verified'):
    return wait(sector, admit(sector, suffix, arguments, index=index, route=route), expected)


def routed(sector):
    store = sector['system'][1]
    (store.source_root / 'other.json').write_text('[1,2]', encoding='utf-8')
    selected = registered(sector['system'], ['.', 'other.json'], action='lane_configure_routes',
        overrides={str(store.source_root): sector['lane'], str(store.source_root / 'other.json'): 'data'},
        source_assertions={str(store.source_root): {'context': 'Original PDF/media folder'}})
    return selected['route_id']


def edit(sector, first, *, index=1):
    arguments = {'snapshot_id': first['snapshot_id'], 'expected_sha256': first['sha256']}
    if sector['family'] == 'pdf':
        arguments['fields'] = {'applicant': 'Updated destination evidence'}
    else:
        arguments.update(logical_name='fixture.png', width=12, height=8)
    return execute(sector, 'edit', arguments, index=index)


def test_create_and_replace_publish_exact_destination_history(sector):
    system, family, lane = sector['system'], sector['family'], sector['lane']
    engine, store, _ = system
    plan(sector, ['index', 'export', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    engine.workers.operations.pop('render_lane_view')
    other_lane = 'images_ocr' if lane == 'pdf_ocr' else 'pdf_ocr'
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
            (child['batch_id'],)).fetchone()[0] == '"Original PDF/media folder"'
    manifest, facts = sector['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert manifest['source_route'] == {'route_id': exported['source_refresh']['route_id'], 'occurrence_ordinals': [1]}
    assert manifest['previous_snapshot'] == edited['snapshot_id'] and store.lane(lane).read_object(manifest['source_object']) == original
    if sector['family'] == 'pdf':
        assert next(item for item in facts['items'] if item['kind'] == 'form_field' and item['name'] == 'applicant')['value'] == 'Updated destination evidence'
    else:
        frame = next(item for item in facts['items'] if item['kind'] == 'frame')
        assert (frame['width'], frame['height']) == (12, 8)
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
            if operation == sector['family'] + '_parse_bytes':
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
                'arguments': {'snapshot_id': exported['index_refresh']['result']['snapshot_id'], 'collection': 'table' if sector['family'] == 'pdf' else 'frame'}})
            assert queried.structuredContent['status'] == 'ok', queried.structuredContent
            assert len(queried.structuredContent['result']['result']['rows']) >= 1
    with LocalEndpoint(engine):
        asyncio.run(run())


@pytest.mark.parametrize('fits_destination', [True, False])
def test_existing_destination_keeps_its_parser_and_resource_limits(sector, fits_destination):
    import io

    from PIL import Image

    from .pdf_fixtures import pdf_bytes
    store = sector['system'][1]
    filename = 'narrow.' + sector['filename'].split('.')[-1]
    if sector['family'] == 'pdf':
        (store.source_root / filename).write_bytes(pdf_bytes())
        if fits_destination:
            (store.source_root / sector['filename']).write_bytes(pdf_bytes())
        arguments = {'max_pages': 1, 'text_backend': 'pypdf', 'extract_tables': False}
    else:
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4), 'cyan').save(buffer, format='PNG')
        (store.source_root / filename).write_bytes(buffer.getvalue())
        arguments = {'max_frames': 1, 'max_pixels_per_frame': 384 if fits_destination else 16}
    plan(sector, ['index', 'index', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    target = execute(sector, 'index', {'filename': filename, **arguments}, index=1)
    before = files(store.source_root), files(store.lane('sources').folder), files(store.lane(sector['lane']).folder)
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': filename,
        'expected_sha256': target['sha256']}, index=2, expected='verified' if fits_destination else 'blocked')
    if fits_destination:
        manifest, facts = sector['module'].read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
        assert manifest['previous_snapshot'] == target['snapshot_id']
        assert facts['parse_options'] == sector['module'].read_snapshot(store, target['snapshot_id'])[1]['parse_options']
        if sector['family'] == 'pdf':
            assert facts['native_evidence']['backend'] == 'pypdf' and facts['fidelity']['geometry'] is False
    else:
        assert before == (files(store.source_root), files(store.lane('sources').folder), files(store.lane(sector['lane']).folder))
        with store.lane('plan').connection(read_only=True) as connection:
            assert connection.execute('SELECT count(*) FROM jobs_effects').fetchone()[0] == 0


@pytest.mark.parametrize('backend', ['pymupdf', 'pypdf', 'pdfplumber', 'poppler'])
def test_pdf_export_uses_actual_selected_native_backend(pdf_system, backend):
    sector = {'family': 'pdf', 'system': pdf_system, 'lane': 'pdf_ocr', 'prefix': 'pdf'}
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': 'fixture.pdf', 'text_backend': backend})
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'exported.pdf'}, index=1)
    from evidence_lane_plugin.pdf_profile import read_snapshot
    _, original = read_snapshot(pdf_system[1], first['snapshot_id'])
    _, facts = read_snapshot(pdf_system[1], exported['index_refresh']['result']['snapshot_id'])
    assert facts == original and facts['native_evidence']['backend'] == backend


@pytest.mark.parametrize('format_name', ['PNG', 'JPEG', 'WEBP', 'TIFF', 'GIF', 'SVG', 'WAV', 'MP4'])
def test_media_native_families_export_exact_bytes_and_facts(media_system, format_name):
    import io
    from pathlib import Path

    from evidence_lane_plugin.media_profile import read_snapshot
    from PIL import Image
    store = media_system[1]
    extension = {'JPEG': 'jpg'}.get(format_name, format_name.lower())
    filename = 'family.' + extension
    if format_name in {'WAV', 'MP4'}:
        fixture = 'tone.wav' if format_name == 'WAV' else 'clip.mp4'
        raw = (Path(__file__).resolve().parents[1] / '.work/qualification/media-smoke' / fixture).read_bytes()
    elif format_name == 'SVG':
        raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="240" height="80"><text x="5" y="30">Distinctive media evidence</text></svg>'
    else:
        buffer = io.BytesIO()
        first = Image.new('RGB', (24, 16), 'navy')
        if format_name in {'TIFF', 'GIF'}:
            first.save(buffer, format=format_name, save_all=True, append_images=[Image.new('RGB', (24, 16), 'cyan')])
        else:
            first.save(buffer, format=format_name)
        raw = buffer.getvalue()
    (store.source_root / filename).write_bytes(raw)
    sector = {'family': 'media', 'system': media_system, 'lane': 'images_ocr', 'prefix': 'media'}
    plan(sector, ['index', 'export'])
    first = execute(sector, 'index', {'filename': filename})
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'out.' + extension}, index=1)
    _, original = read_snapshot(store, first['snapshot_id'])
    _, facts = read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert (store.source_root / ('out.' + extension)).read_bytes() == raw and facts == original
    assert facts['fidelity']['ocr_performed'] is False and facts['fidelity']['external_resources_followed'] is False
    if format_name in {'TIFF', 'GIF'}:
        assert len([row for row in facts['items'] if row['kind'] == 'frame']) == 2
    if format_name in {'WAV', 'MP4'}:
        assert facts['counts']['stream'] >= 1


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


def test_export_keeps_ocr_bound_to_its_original_snapshot(sector):
    from pathlib import Path
    store, family, lane = sector['system'][1], sector['family'], sector['lane']
    if family == 'media':
        raw = (Path(__file__).resolve().parents[1] / '.work/qualification/media-smoke/fixture.png').read_bytes()
        (store.source_root / sector['filename']).write_bytes(raw)
    plan(sector, ['index', 'ocr', 'export'])
    first = execute(sector, 'index', {'filename': sector['filename']})
    ocr = execute(sector, 'ocr', {'snapshot_id': first['snapshot_id'],
        **({'pages': [2], 'dpi': 160} if family == 'pdf' else {'frames': [1]})}, index=1)
    assert ocr['ocr_lines'] >= 1
    before = call(sector['system'], family + '_ocr_read', {'ocr_id': ocr['ocr_id']})
    assert before.status == 'ok', before.error
    initial = view(sector['system'], lane, [], True)
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': sector['filename'],
        'expected_sha256': first['sha256']}, index=2)
    assert exported['index_refresh']['result']['snapshot_id'] != first['snapshot_id']
    assert exported['view_refresh']['generation'] == initial['generation'] + 1
    after = call(sector['system'], family + '_ocr_read', {'ocr_id': ocr['ocr_id']})
    assert after.status == 'ok' and after.result == before.result
    with store.lane(lane).connection(read_only=True) as connection:
        rows = connection.execute('SELECT snapshot_id FROM ' + family + '_ocr_run').fetchall()
        assert [row[0] for row in rows] == [first['snapshot_id']]
    graph = call(sector['system'], 'lane_view_preview', {'view_id': lane + '.structure'})
    assert graph.status == 'ok', graph.error
    assert not any(row['kind'] == family + '_ocr_run' for row in graph.result['graph']['nodes'])


@pytest.mark.parametrize('kind', ['video_frame', 'audio_segment'])
def test_media_export_keeps_extracted_bytes_and_snapshot_binding(media_system, kind):
    from pathlib import Path

    from evidence_lane_plugin.media_derivatives import manifest
    store = media_system[1]
    raw = (Path(__file__).resolve().parents[1] / '.work/qualification/media-smoke/clip.mp4').read_bytes()
    (store.source_root / 'clip.mp4').write_bytes(raw)
    sector = {'family': 'media', 'system': media_system, 'lane': 'images_ocr', 'prefix': 'media'}
    plan(sector, ['index', 'extract', 'export'])
    first = execute(sector, 'index', {'filename': 'clip.mp4'})
    extracted = execute(sector, 'extract', {'snapshot_id': first['snapshot_id'], 'kind': kind,
        'start_seconds': .5, 'duration_seconds': 1}, index=1)
    before = manifest(store, extracted['extraction_id'], 'extraction')
    derivative = Path(extracted['natural_path']).read_bytes()
    view(media_system, 'images_ocr', [], True)
    exported = execute(sector, 'export', {'snapshot_id': first['snapshot_id'], 'filename': 'clip.mp4',
        'expected_sha256': first['sha256']}, index=2)
    assert manifest(store, extracted['extraction_id'], 'extraction') == before
    assert before['snapshot_id'] == first['snapshot_id'] != exported['index_refresh']['result']['snapshot_id']
    assert Path(extracted['natural_path']).read_bytes() == derivative
    assert call(media_system, 'media_extraction_read', {'extraction_id': extracted['extraction_id']}).status == 'ok'
    graph = call(media_system, 'lane_view_preview', {'view_id': 'images_ocr.structure'})
    assert graph.status == 'ok' and not any(row['kind'] == 'media_extraction' for row in graph.result['graph']['nodes'])

