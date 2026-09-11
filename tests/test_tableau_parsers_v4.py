"""Independent vendor fixtures and hostile format boundaries."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.tableau_parsers import digest, package_members, parse_tableau

FIXTURES = Path(__file__).parent / 'fixtures/tableau'


@pytest.mark.parametrize('name', ['datasource_test.tds', 'datasource_test.twb', 'filtering.twb',
    'multiple_connections.twb', 'shapes_test.twb', 'TABLEAU_10_TDS.tds', 'TABLEAU_10_TDSX.tdsx',
    'TABLEAU_10_TWB.twb', 'TABLEAU_10_TWBX.twbx', 'unicode.tds'])
def test_pinned_vendor_format_fixture(name):
    manifest = json.loads((FIXTURES / 'provenance.json').read_text())
    expected = next(row for row in manifest['files'] if row['path'] == name)
    raw = (FIXTURES / name).read_bytes()
    assert digest(raw) == expected['sha256'] and len(raw) == expected['bytes']
    facts = parse_tableau(name, raw)
    assert facts['counts']['datasource'] > 0
    assert all(row['xml_path'].startswith('/') for row in facts['items'] if 'xml_path' in row)
    assert not facts['fidelity']['layout_verified'] and not facts['fidelity']['live_data_read']


@pytest.mark.parametrize('name', ['../outside.twb', 'C:/outside.twb', '/absolute.twb', 'a/../../b.twb'])
def test_packages_reject_traversal(name):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr(name, '<workbook/>')
    with pytest.raises(LaneError) as caught:
        package_members(data.getvalue())
    assert caught.value.code == 'TABLEAU_PACKAGE_PATH_INVALID'


def test_utf16_entities_rejected_and_secret_attributes_redacted():
    xml = '<?xml version="1.0" encoding="utf-16"?><!DOCTYPE workbook [<!ENTITY x SYSTEM "file:///unused">]><workbook>&x;</workbook>'
    with pytest.raises(LaneError) as caught:
        parse_tableau('unsafe.twb', xml.encode('utf-16'))
    assert caught.value.code == 'TABLEAU_XML_ACTIVE_CONTENT'
    raw = b'<workbook><datasources><datasource name="Evidence"><connection server="https://invalid.example" password="test-private-value"/></datasource></datasources></workbook>'
    facts = parse_tableau('safe.twb', raw)
    assert 'test-private-value' not in json.dumps(facts)
    assert 'https://invalid.example' in json.dumps(facts)


def test_legacy_tde_has_no_invented_data_access():
    facts = parse_tableau('legacy.tde', b'opaque legacy fixture')
    assert facts['counts'] == {'opaque': 1}
    assert not facts['fidelity']['hyper_native_read'] and not facts['fidelity']['xml_metadata']


def test_duplicate_case_package_member_is_rejected():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as z:
        z.writestr('Book.twb', '<workbook/>')
        z.writestr('book.twb', '<workbook/>')
    with pytest.raises(LaneError) as caught:
        package_members(data.getvalue())
    assert caught.value.code == 'TABLEAU_PACKAGE_MEMBER_INVALID'
