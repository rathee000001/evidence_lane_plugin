"""Schema-valid hidden-slide booleans must retain their stored meaning."""
import xml.etree.ElementTree as ET

import pytest
from evidence_lane_plugin.document_parsers import package_members
from evidence_lane_plugin.presentation_parsers import parse_presentation

from .test_presentation_boundaries_v4 import alter
from .test_presentation_fidelity_v4 import reference


@pytest.mark.parametrize(('value', 'hidden'), [('0', True), ('false', True), ('1', False), ('true', False), (' false ', True)])
def test_hidden_flag_follows_xml_schema_boolean(value, hidden):
    raw = reference('python-pptx', 'sld-slides.pptx')
    part = 'ppt/slides/slide1.xml'
    root = ET.fromstring(package_members(raw)[part])
    root.set('show', value)
    modified = alter(raw, {part: ET.tostring(root)})
    facts = parse_presentation('hidden.pptx', modified)
    slides = [item for item in facts['items'] if item['kind'] == 'slide']
    assert slides[0]['hidden'] is hidden
    assert all(not item['hidden'] for item in slides[1:])


def test_invalid_show_boolean_is_not_silently_treated_as_visible():
    raw = reference('python-pptx', 'sld-slides.pptx')
    part = 'ppt/slides/slide1.xml'
    root = ET.fromstring(package_members(raw)[part])
    root.set('show', 'unexpected')
    with pytest.raises(ValueError, match='PRESENTATION_SHOW_ATTRIBUTE_INVALID'):
        parse_presentation('invalid.pptx', alter(raw, {part: ET.tostring(root)}))
