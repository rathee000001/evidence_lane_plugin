"""Failure boundaries and nontrivial presentation fidelity distinctions."""
import base64
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from evidence_lane_plugin.document_parsers import REL, R, digest, package_members
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.presentation_authoring import edit_package, generate_package
from evidence_lane_plugin.presentation_contracts import PresentationGenerate
from evidence_lane_plugin.presentation_parsers import A, parse_presentation
from evidence_lane_plugin.presentation_rendering import safe_render_package

from .test_presentation_profile_v4 import generation


def alter(raw, members):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as before, zipfile.ZipFile(output, 'w') as after:
        for info in before.infolist():
            after.writestr(info, members.get(info.filename, before.read(info)))
        for name, value in members.items():
            if name not in before.namelist():
                after.writestr(name, value)
    return output.getvalue()


def test_native_intake_records_external_relationship_without_fetching_it():
    raw = generate_package(generation(1))
    name = 'ppt/slides/_rels/slide1.xml.rels'
    root = ET.fromstring(package_members(raw)[name])
    ET.SubElement(root, REL + 'Relationship', {'Id': 'external', 'Target': 'https://example.invalid/asset.png',
        'Type': R[1:-1] + '/image', 'TargetMode': 'External'})
    original = alter(raw, {name: ET.tostring(root)})
    facts = parse_presentation('fixture.pptx', original)
    assert facts['features']['external_resources']
    assert any(item['kind'] == 'relationship' and item['external'] and item['member'] is None for item in facts['items'])
    with pytest.raises(LaneError, match='External|embedded|resources'):
        safe_render_package(original, '.pptx')
    assert original == alter(raw, {name: ET.tostring(root)})


def test_complex_text_edit_does_not_flatten_mixed_formatting():
    raw = generate_package(generation(1))
    part = 'ppt/slides/slide1.xml'
    root = ET.fromstring(package_members(raw)[part])
    paragraph = next(root.iter(A + 'p'))
    run = ET.SubElement(paragraph, A + 'r')
    ET.SubElement(run, A + 'rPr', {'i': '1'})
    ET.SubElement(run, A + 't').text = ' additional italic text'
    content = alter(raw, {part: ET.tostring(root)})
    with pytest.raises(ValueError, match='PRESENTATION_EDIT_COMPLEX_PARAGRAPH'):
        edit_package({'content_base64': base64.b64encode(content).decode(), 'expected_sha256': digest(content),
            'replacements': [{'part': part, 'paragraph_index': 0,
                'expected_text': 'Versioned presentation 1 additional italic text', 'replacement_text': 'flattened'}]})


def test_partial_or_duplicate_slide_order_cannot_drop_slides():
    raw = generate_package(generation(2))
    for order in [['ppt/slides/slide1.xml'], ['ppt/slides/slide1.xml', 'ppt/slides/slide1.xml']]:
        with pytest.raises(ValueError, match='PRESENTATION_EDIT_SLIDE_ORDER_INVALID'):
            edit_package({'content_base64': base64.b64encode(raw).decode(), 'expected_sha256': digest(raw),
                'replacements': [], 'slide_order': order})


def test_generation_refuses_objects_outside_slide_bounds():
    request = generation(1)
    request['slides'][0]['objects'][0]['x'] = 13
    with pytest.raises(ValueError, match='fit within'):
        PresentationGenerate.model_validate(request)


def test_xml_entities_and_macro_rendering_are_refused_but_original_macros_can_be_indexed():
    raw = generate_package(generation(1))
    macro = alter(raw, {'ppt/vbaProject.bin': b'unexecuted opaque macro evidence'})
    assert parse_presentation('fixture.pptm', macro)['features']['macros']
    with pytest.raises(LaneError):
        safe_render_package(macro, '.pptx')
    malicious = alter(raw, {'ppt/slides/slide1.xml': b'<!DOCTYPE a [<!ENTITY x SYSTEM "file:///invalid">]><a>&x;</a>'})
    with pytest.raises(ValueError, match='XML_UNSAFE'):
        parse_presentation('fixture.pptx', malicious)
