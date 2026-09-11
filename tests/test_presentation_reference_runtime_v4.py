"""Actual shared-office qualification of independently sourced presentation bytes."""
import json
import os
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.presentation_profile import read_snapshot
from evidence_lane_plugin.presentation_rendering import safe_render_package

from .qualification_outputs_v4 import qualification_output
from .test_presentation_fidelity_v4 import reference
from .test_presentation_profile_v4 import call, execute, plan
from .test_presentation_profile_v4 import (
    presentation_system as presentation_system,  # noqa: PLC0414
)


@pytest.fixture(autouse=True)
def shared_office(monkeypatch):
    installation = os.environ.get('EVI_DOCUMENT_QUALIFICATION_ASSETS')
    if not installation:
        pytest.skip('Requires the isolated verified office runtime')
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', installation)


@pytest.mark.parametrize(('vendor', 'name', 'pages'), [
    ('python-pptx', 'font-color.pptx', 1), ('python-pptx', 'shp-picture.pptx', 2),
    ('python-pptx', 'tbl-cell.pptx', 3), ('python-pptx', 'shp-groupshape.pptx', 1),
    ('apache-poi', 'testPPT.ppsx', 3), ('apache-poi', 'bug59273.potx', 1),
])
def test_shared_impress_renders_independent_presentation(presentation_system, shared_office, vendor, name, pages, tmp_path):
    original = reference(vendor, name)
    store = presentation_system[1]
    (store.source_root / name).write_bytes(original)
    plan(presentation_system, ['presentation_index', 'presentation_render'])
    indexed = execute(presentation_system, 'presentation_index', {'filename': name})
    rendered = execute(presentation_system, 'presentation_render', {'snapshot_id': indexed['snapshot_id'], 'max_pages': 20},
                       index=1, timeout=185)
    assert rendered['page_count'] == pages
    assert not rendered['evidence']['powerpoint_layout_equivalence']
    assert rendered['evidence']['engine'] == 'LibreOffice'
    assert rendered['evidence']['visual_review'] == 'required_not_performed_by_renderer'
    read = call(presentation_system, 'presentation_render_read', {'render_id': rendered['render_id']})
    assert read.status == 'ok', read.error
    assert (store.source_root / name).read_bytes() == original
    target = qualification_output('presentation-reference-renders/' + name + '.json', tmp_path)
    target.write_text(json.dumps({'indexed': indexed, 'rendered': rendered}, indent=2) + '\n')


def test_independent_binary_ppt_conversion_preserves_original(presentation_system, shared_office, tmp_path):
    original = reference('apache-poi', 'basic_test_ppt_file.ppt')
    store = presentation_system[1]
    (store.source_root / 'independent.ppt').write_bytes(original)
    plan(presentation_system, ['presentation_convert', 'presentation_export'])
    converted = execute(presentation_system, 'presentation_convert', {'filename': 'independent.ppt'}, timeout=185)
    manifest, facts = read_snapshot(store, converted['snapshot_id'])
    assert store.lane('ppt').read_object(manifest['source_object']) == original
    assert (store.source_root / 'independent.ppt').read_bytes() == original
    assert converted['tool_evidence']['conversion_fidelity'] == 'derived_pptx_not_lossless_round_trip'
    assert any('test' in item['text'].lower() for item in facts['items'] if item['kind'] == 'slide')
    qualification_output('office-export-refresh-presentation-legacy-conversion-current.json', tmp_path).write_text(json.dumps(converted, indent=2) + '\n')
    exported = execute(presentation_system, 'presentation_export', {'snapshot_id': converted['snapshot_id'],
        'filename': 'exported.pptx'}, index=1)
    destination, destination_facts = read_snapshot(store, exported['index_refresh']['result']['snapshot_id'])
    assert destination_facts == facts and destination['raw_object'] == manifest['raw_object']
    assert destination['source_object'] == manifest['raw_object'] and destination['source_path'] == 'exported.pptx'
    assert store.lane('ppt').read_object(manifest['source_object']) == original == (store.source_root / 'independent.ppt').read_bytes()


@pytest.mark.parametrize(('name', 'reason'), [
    ('act-props.pptm', 'PRESENTATION_RENDER_FORMAT_UNSUPPORTED'),
    ('cht-charts.pptx', 'PRESENTATION_RENDER_ACTIVE_CONTENT'),
    ('ph-populated-placeholders.pptx', 'PRESENTATION_RENDER_ACTIVE_CONTENT'),
    ('shp-access-ole-object.pptx', 'PRESENTATION_RENDER_ACTIVE_CONTENT'),
])
def test_independent_active_content_is_retained_but_refused_by_renderer(name, reason):
    raw = reference('python-pptx', name)
    with pytest.raises(LaneError) as error:
        safe_render_package(raw, Path(name).suffix)
    assert error.value.code == reason
