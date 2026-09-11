"""Actual retained document, table, PDF and media adapters in evidence lanes."""
from __future__ import annotations

import json
import os

import pytest
from evidence_lane_plugin.sector_evidence_profile import read_snapshot

from .test_evidence_sectors_v4 import call, execute, plan
from .test_evidence_sectors_v4 import system as system  # noqa: PLC0414


@pytest.fixture(autouse=True)
def assets(monkeypatch):
    root = os.environ.get('EVI_EVIDENCE_QUALIFICATION_ASSETS')
    if root:
        monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', root)


def fixture_bytes(path, kind):
    if kind == 'docx':
        module = pytest.importorskip('docx')
        document = module.Document()
        document.add_paragraph('Evidence: document source alpha')
        document.save(path)
    elif kind == 'xlsx':
        module = pytest.importorskip('openpyxl')
        workbook = module.Workbook()
        workbook.active.append(['Evidence', 'Value'])
        workbook.active.append(['source alpha', 5])
        workbook.save(path)
    elif kind == 'parquet':
        arrow = pytest.importorskip('pyarrow')
        parquet = pytest.importorskip('pyarrow.parquet')
        parquet.write_table(arrow.table({'Evidence': ['source alpha'], 'Value': [5]}), path)
    elif kind == 'png':
        pillow = pytest.importorskip('PIL.Image')
        pillow.new('RGB', (8, 9), 'blue').save(path)
    elif kind.startswith('pdf_'):
        pytest.importorskip('pypdf')
        pytest.importorskip({'pdf_pymupdf': 'pymupdf', 'pdf_pypdf': 'pypdf', 'pdf_pdfplumber': 'pdfplumber'}[kind])
        canvas = pytest.importorskip('reportlab.pdfgen.canvas').Canvas(str(path))
        canvas.drawString(30, 700, 'Evidence: source alpha')
        canvas.save()
    elif kind == 'html':
        path.write_text('<html><script>throw new Error("never execute")</script><p>Evidence: source alpha</p></html>', encoding='utf-8')
    elif kind == 'csv':
        path.write_text('Evidence,Value\nsource alpha,5\n', encoding='utf-8')
    elif kind == 'json':
        path.write_text('{"evidence":"source alpha","value":1.250}', encoding='utf-8')
    elif kind == 'ipynb':
        path.write_text(json.dumps({'nbformat': 4, 'cells': [
            {'cell_type': 'markdown', 'source': 'Evidence: source alpha'}]}), encoding='utf-8')
    elif kind == 'svg':
        pytest.importorskip('defusedxml')
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="9"><text x="0" y="5">source alpha</text></svg>', encoding='utf-8')
    elif kind == 'wav':
        if not os.environ.get('EVI_EVIDENCE_QUALIFICATION_ASSETS'):
            pytest.skip('Requires the isolated verified FFmpeg qualification bundle')
        import wave
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\x00\x00' * 800)
    else:
        raise AssertionError(kind)


@pytest.mark.parametrize('lane_id,kind,tools', [
    ('research', 'docx', ('Python',)),
    ('research', 'xlsx', ('Python',)),
    ('research', 'csv', ('Python',)),
    ('research', 'parquet', ('Python', 'pyarrow')),
    ('research', 'pdf_pymupdf', ('Python', 'pypdf', 'PyMuPDF')),
    ('research', 'pdf_pypdf', ('Python', 'pypdf')),
    ('research', 'pdf_pdfplumber', ('Python', 'pypdf', 'pdfplumber')),
    ('artifacts', 'html', ('Python',)),
    ('artifacts', 'ipynb', ('Python',)),
    ('custom', 'json', ('Python',)),
    ('artifacts', 'png', ('Python', 'Pillow')),
    ('artifacts', 'svg', ('Python', 'defusedxml')),
    ('artifacts', 'wav', ('Python', 'FFmpeg')),
])
def test_format_runs_its_actual_worker_and_keeps_input_bytes(system, lane_id, kind, tools):
    path = system[1].source_root / ('fixture.' + ('pdf' if kind.startswith('pdf_') else kind))
    fixture_bytes(path, kind)
    before = path.read_bytes()
    action = lane_id + '_index' + ('_media' if kind in {'png', 'svg', 'wav'} else '')
    plan(system, lane_id, [action], tools=tools)
    arguments = {'filename': path.name}
    if kind.startswith('pdf_'):
        arguments['parser'] = kind
    indexed = execute(system, action, arguments)
    manifest, facts = read_snapshot(system[1], lane_id, indexed['snapshot_id'])
    assert path.read_bytes() == before
    assert manifest['worker_envelope']['worker_pid'] != os.getpid()
    assert manifest['worker_fields']['evidence']['parser'] == manifest['parser']
    assert facts['items'] and not facts['fidelity']['imported_code_executed']
    if kind not in {'png', 'wav'}:
        response = call(system, lane_id + '_query', {'snapshot_id': indexed['snapshot_id'], 'query': 'alpha'})
        assert response.status == 'ok' and response.result['result']['rows'], response.error
    if kind == 'html':
        assert not any('never execute' in item['text'] for item in facts['items'])
    if kind == 'json':
        assert any('1.250' in item['text'] for item in facts['items'])
    if kind == 'wav':
        assert manifest['worker_fields']['evidence']['native_asset_sha256']
        assert any(item['kind'] == 'artifact_media_probe' for item in facts['items'])


def test_declared_parser_tool_must_be_available_before_worker_dispatch(system):
    from evidence_lane_plugin.plan_runtime import PlanStore
    plan(system, 'research', ['research_index'], tools=('Python',))
    (system[1].source_root / 'fixture.pdf').write_bytes(b'not parsed without a grant')
    task = PlanStore(system[1]).task('evidence-0', expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': 'research_index',
        'arguments': {'filename': 'fixture.pdf', 'parser': 'pdf_pypdf'}}, expected_revision=1)
    assert admitted.status == 'error'
    assert system[0].workers.status()['succeeded_operations'] == 0


@pytest.mark.skipif(os.name != 'nt', reason='Studio service targets Windows only')
def test_service_can_run_its_registered_source_worker(tmp_path):
    from evidence_lane_plugin.service import Service
    source = tmp_path / 'note.md'
    source.write_text('Evidence: service worker source', encoding='utf-8')
    service = Service(tmp_path / 'runtime', workers=1)
    with service.engine:
        response = service.engine.workers.submit('sector_evidence_parse_file', {
            'lane_id': 'research', 'filename': str(source), 'logical_name': source.name,
            'parser': 'auto', 'max_file_bytes': 1024, 'sqlite_tables': [], 'sqlite_rows': 100}).result()
        assert response['status'] == 'ok'
        assert response['worker_pid'] != os.getpid()
        assert response['result']['facts']['lane_id'] == 'research'
