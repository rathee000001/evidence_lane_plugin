"""Actual office rendering, legacy conversion and attributed Docling execution."""
import json
import os

import pytest

from .qualification_outputs_v4 import qualification_output
from .test_presentation_profile_v4 import call, execute, generation, plan
from .test_presentation_profile_v4 import (
    presentation_system as presentation_system,  # noqa: PLC0414
)


@pytest.fixture(autouse=True)
def office_assets(monkeypatch):
    installation = os.environ.get('EVI_DOCUMENT_QUALIFICATION_ASSETS')
    if not installation:
        pytest.skip('Requires the isolated hash-verified shared office runtime')
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', installation)


def test_real_impress_render_generated_slides(presentation_system, tmp_path):
    plan(presentation_system, ['presentation_generate', 'presentation_render'])
    generated = execute(presentation_system, 'presentation_generate', generation())
    rendered = execute(presentation_system, 'presentation_render', {'snapshot_id': generated['snapshot_id'], 'max_pages': 2}, index=1, timeout=185)
    assert rendered['page_count'] == 2
    assert rendered['evidence']['engine'] == 'LibreOffice' and rendered['evidence']['raster_engine'] == 'pypdfium2'
    assert not rendered['evidence']['powerpoint_layout_equivalence']
    pdf = next(item for item in rendered['files'] if item['role'] == 'pdf')
    import pypdfium2 as pdfium
    document = pdfium.PdfDocument(pdf['path'])
    try:
        for index in range(2):
            page = document[index]
            text = page.get_textpage()
            content = text.get_text_range()
            text.close()
            page.close()
            assert f'Versioned presentation {index + 1}' in content
            assert 'New immutable version' in content
    finally:
        document.close()
    read = call(presentation_system, 'presentation_render_read', {'render_id': rendered['render_id']})
    assert read.status == 'ok', read.error
    qualification_output('presentation-render-fixture.json', tmp_path).write_text(json.dumps({
        'generated': generated, 'rendered': rendered, 'visual_review_performed': False}, indent=2) + '\n')


def test_real_pptx_docling_enrichment(presentation_system):
    plan(presentation_system, ['presentation_index', 'presentation_enrich'])
    store = presentation_system[1]
    original = (store.source_root / 'fixture.pptx').read_bytes()
    indexed = execute(presentation_system, 'presentation_index', {'filename': 'fixture.pptx'})
    enriched = execute(presentation_system, 'presentation_enrich', {'snapshot_id': indexed['snapshot_id']}, index=1, timeout=185)
    assert enriched['converter_status'] == 'success'
    read = call(presentation_system, 'presentation_enrichment_read', {'enrichment_id': enriched['enrichment_id'], 'include_structure': True})
    assert read.status == 'ok', read.error
    assert 'Versioned presentation 1' in read.result['result']['markdown']
    assert read.result['result']['document']['texts']
    assert (store.source_root / 'fixture.pptx').read_bytes() == original


@pytest.mark.parametrize('extension', ['ppt', 'odp'])
def test_real_legacy_presentation_conversion_retains_original(presentation_system, tmp_path, extension):
    from evidence_lane_plugin.document_rendering import _convert
    from evidence_lane_plugin.presentation_profile import read_snapshot
    staging = tmp_path / 'legacy-reference'
    staging.mkdir()
    store = presentation_system[1]
    path, _ = _convert(store.source_root / 'fixture.pptx', staging, timeout_seconds=45, output_format=extension, family='impress')
    original = path.read_bytes()
    source = store.source_root / ('legacy.' + extension)
    source.write_bytes(original)
    plan(presentation_system, ['presentation_convert'])
    converted = execute(presentation_system, 'presentation_convert', {'filename': source.name}, timeout=185)
    assert converted['tool_evidence']['conversion_fidelity'] == 'derived_pptx_not_lossless_round_trip'
    manifest, facts = read_snapshot(store, converted['snapshot_id'])
    assert store.lane('ppt').read_object(manifest['source_object']) == original == source.read_bytes()
    assert any(item['kind'] == 'slide' and 'Versioned presentation' in item['text'] for item in facts['items'])
    assert manifest['source_object'] != manifest['raw_object']
