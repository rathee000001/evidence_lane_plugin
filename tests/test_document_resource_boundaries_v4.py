"""Package URI resolution cannot fall back to external filesystem resources."""
import pytest
from evidence_lane_plugin.document_parsers import package_members
from evidence_lane_plugin.document_rendering import safe_render_package
from evidence_lane_plugin.errors import LaneError

from .test_document_verification_v4 import odt_fixture, package, word_package


@pytest.mark.parametrize('target', ['%2e%2e/outside.png', '%252e%252e/outside.png',
    '%2foutside.png', 'file%3a/outside.png', 'missing.png', 'Pictures%5coutside.png'])
def test_odt_encoded_or_missing_resources_cannot_leave_the_package(target):
    members = package_members(odt_fixture())
    members['content.xml'] = members['content.xml'].replace(b'<text:p>Alpha', (
        '<text:p xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="' + target + '">Alpha').encode())
    with pytest.raises(LaneError, match='package member'):
        safe_render_package(package(members), '.odt')


@pytest.mark.parametrize('target', ['%2e%2e/%2e%2e/outside.png', 'missing.png'])
def test_word_internal_relationship_must_resolve_inside_its_package(target):
    rels = ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="image" Target="' + target + '"/></Relationships>').encode()
    with pytest.raises(LaneError, match='package member'):
        safe_render_package(word_package('', {'word/_rels/document.xml.rels': rels}), '.docx')


def test_escaped_member_name_and_internal_fragment_are_allowed_when_resolved():
    members = package_members(odt_fixture())
    members['Pictures/image space.png'] = b'opaque structure fixture'
    members['content.xml'] = members['content.xml'].replace(b'<text:p>Alpha',
        b'<text:p xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="Pictures/image%20space.png">Alpha')
    assert safe_render_package(package(members), '.odt')['Pictures/image space.png']
    members['content.xml'] = members['content.xml'].replace(b'Pictures/image%20space.png', b'#internal-anchor')
    assert safe_render_package(package(members), '.odt')
