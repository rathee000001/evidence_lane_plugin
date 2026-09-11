"""Actual pinned office conversion and page rasterization; opt-in qualification assets."""
from __future__ import annotations

import json
import os

import pytest

from .qualification_outputs_v4 import qualification_output
from .test_document_profile_v4 import call, create_plan, execute
from .test_document_profile_v4 import (
    document_system as document_system,  # noqa: PLC0414 - fixture re-export
)


@pytest.fixture(autouse=True)
def office_assets(monkeypatch):
    installation = os.environ.get('EVI_DOCUMENT_QUALIFICATION_ASSETS')
    if not installation:
        pytest.skip('Requires the separately hash-verified isolated office runtime')
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', installation)


def test_real_office_render_of_generated_two_page_document(document_system, tmp_path):
    create_plan(document_system, ['document_generate', 'document_render'])
    generated = execute(document_system, 'document_generate', {'logical_name': 'document-operations.docx',
        'title': 'Document operations', 'blocks': [
            {'kind': 'heading', 'text': 'Versioned document work'},
            {'kind': 'paragraph', 'text': 'This fixture checks document structure and page rendering through the registered Docs operations.'},
            {'kind': 'table', 'rows': [['Operation', 'Result'], ['Intake', 'Exact source version'], ['Edit', 'New document version']]},
            {'kind': 'page_break'}, {'kind': 'heading', 'text': 'Export and review'},
            {'kind': 'paragraph', 'text': 'Export verifies the destination hash. Page images support a separate visual review.'}]})
    rendered = execute(document_system, 'document_render', {'snapshot_id': generated['snapshot_id'], 'max_pages': 2}, index=1, timeout=185)
    assert rendered['page_count'] == 2
    assert rendered['evidence']['engine'] == 'LibreOffice' and rendered['evidence']['raster_engine'] == 'pypdfium2'
    assert rendered['evidence']['visual_review'] == 'required_not_performed_by_renderer'
    assert not rendered['evidence']['word_layout_equivalence']
    pdf = next(row for row in rendered['files'] if row['role'] == 'pdf')
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(pdf['path'])
    try:
        contents = []
        for i in range(len(document)):
            page = document[i]
            text = page.get_textpage()
            contents.append(text.get_text_range())
            text.close()
            page.close()
        assert 'Versioned document work' in contents[0]
        assert 'Export and review' in contents[1]
    finally:
        document.close()
    readback = call(document_system, 'document_render_read', {'render_id': rendered['render_id']})
    assert readback.status == 'ok', readback.error
    record = {'generated': generated, 'rendered': rendered, 'visual_review_performed': False}
    qualification_output('document-render-fixture.json', tmp_path).write_text(json.dumps(record, indent=2) + '\n')


def test_real_docling_enrichment_keeps_native_snapshot_and_original_separate(document_system):
    create_plan(document_system, ['document_index', 'document_enrich'])
    store = document_system[1]
    original = (store.source_root / 'fixture.docx').read_bytes()
    indexed = execute(document_system, 'document_index', {'filename': 'fixture.docx'})
    enriched = execute(document_system, 'document_enrich', {'snapshot_id': indexed['snapshot_id']}, index=1, timeout=185)
    assert enriched['converter_status'] == 'success' and not enriched['model_assets_required']
    excerpt = call(document_system, 'document_enrichment_read', {'enrichment_id': enriched['enrichment_id'], 'include_structure': True})
    assert excerpt.status == 'ok', excerpt.error
    assert 'selected operation preserves' in excerpt.result['result']['markdown']
    assert excerpt.result['result']['document']['texts']
    from evidence_lane_plugin.document_enrichment import enrichment_manifest
    assert enrichment_manifest(store, enriched['enrichment_id'])['conversion']['host_profile'] == 'STUDIO_WORKER'
    assert (store.source_root / 'fixture.docx').read_bytes() == original
    current = call(document_system, 'document_current')
    assert current.result['result']['documents'][0]['snapshot_id'] == indexed['snapshot_id']


@pytest.mark.parametrize('extension', ['.doc', '.rtf'])
def test_real_legacy_conversion_retains_original_and_publishes_derived_docx(document_system, extension, tmp_path):
    from evidence_lane_plugin.document_rendering import _convert
    source = document_system[1].source_root
    if extension == '.rtf':
        original = b'{\\rtf1\\ansi Legacy document conversion.\\par The original is retained.}'
    else:
        staging = tmp_path / 'fixture-creation'
        staging.mkdir()
        converted, _ = _convert(source / 'fixture.docx', staging, timeout_seconds=45, output_format='doc')
        original = converted.read_bytes()
    path = source / ('legacy' + extension)
    path.write_bytes(original)
    create_plan(document_system, ['document_convert', 'document_export'])
    converted = execute(document_system, 'document_convert', {'filename': path.name}, timeout=185)
    assert converted['logical_name'] == path.name + '.docx'
    assert converted['tool_evidence']['conversion_fidelity'] == 'derived_docx_not_lossless_round_trip'
    assert path.read_bytes() == original
    from evidence_lane_plugin.document_profile import read_snapshot
    manifest, facts = read_snapshot(document_system[1], converted['snapshot_id'])
    assert document_system[1].lane('docs').read_object(manifest['source_object']) == original
    assert facts['items'] and manifest['source_object'] != manifest['raw_object']
    readback = call(document_system, 'document_read', {'snapshot_id': converted['snapshot_id'], 'representation': 'original_source',
        'max_bytes': 131072})
    import base64
    assert base64.b64decode(readback.result['result']['content_base64']) == original
    exported = execute(document_system, 'document_export', {'snapshot_id': converted['snapshot_id'],
        'filename': 'exported.docx'}, index=1)
    destination, destination_facts = read_snapshot(document_system[1], exported['index_refresh']['result']['snapshot_id'])
    assert destination_facts == facts and destination['raw_object'] == manifest['raw_object']
    assert destination['source_object'] == manifest['raw_object'] and destination['source_path'] == 'exported.docx'
    assert document_system[1].lane('docs').read_object(manifest['source_object']) == original == path.read_bytes()
