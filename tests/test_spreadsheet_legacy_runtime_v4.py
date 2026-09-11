import os

import pytest

from .test_tabular_profile_v4 import execute, plan, query
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


@pytest.fixture(autouse=True)
def office_assets(monkeypatch):
    root = os.environ.get('EVI_DOCUMENT_QUALIFICATION_ASSETS')
    if not root:
        pytest.skip('Requires isolated Office assets to construct actual legacy fixtures')
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', root)


@pytest.mark.parametrize('extension', ['xls', 'ods'])
def test_real_legacy_sheet_value_reader_preserves_original(tabular_system, tmp_path, extension):
    from evidence_lane_plugin.document_rendering import _convert
    source = tabular_system[1].source_root
    fixture = tmp_path / 'fixture-conversion'
    fixture.mkdir()
    output, _ = _convert(source / 'fixture.xlsx', fixture, timeout_seconds=45, output_format=extension, family='calc')
    content = output.read_bytes()
    path = source / ('legacy.' + extension)
    path.write_bytes(content)
    plan(tabular_system, ['spreadsheet_index_values'])
    indexed = execute(tabular_system, 'spreadsheet_index_values', {'filename': path.name})
    assert indexed['tool_evidence']['parser'] == 'python-calamine'
    assert indexed['tool_evidence']['parser_version'] == '0.8.2'
    assert indexed['fidelity']['formulas'] == 'expressions_not_extracted_values_may_be_cached'
    rows = query(tabular_system, 'spreadsheet', indexed['snapshot_id'], query='Alpha')['rows']
    assert rows[0]['text'] == 'Alpha'
    assert path.read_bytes() == content
