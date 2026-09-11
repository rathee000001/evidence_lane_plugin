"""Actual Calc formula recalculation and page rendering on pinned isolated assets."""
import json
import os
from pathlib import Path

import pytest

from .qualification_outputs_v4 import qualification_output
from .test_tabular_profile_v4 import call, execute, plan, query
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


@pytest.fixture(autouse=True)
def office_assets(monkeypatch):
    root = os.environ.get('EVI_DOCUMENT_QUALIFICATION_ASSETS')
    if not root:
        pytest.skip('Requires the separately pinned isolated Office runtime')
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', root)


def test_actual_calc_recalculation_and_render(tabular_system, tmp_path):
    plan(tabular_system, ['spreadsheet_generate', 'spreadsheet_recalculate', 'spreadsheet_render'])
    generated = execute(tabular_system, 'spreadsheet_generate', {'logical_name': 'quantities.xlsx', 'sheets': [
        {'name': 'Quantities', 'freeze_panes': 'A2', 'cells': [
            {'cell': 'A1', 'value': {'type': 'text', 'value': 'Item'}},
            {'cell': 'B1', 'value': {'type': 'text', 'value': 'Quantity'}},
            {'cell': 'C1', 'value': {'type': 'text', 'value': 'Unit cost'}},
            {'cell': 'D1', 'value': {'type': 'text', 'value': 'Total'}},
            {'cell': 'A2', 'value': {'type': 'text', 'value': 'Alpha'}},
            {'cell': 'B2', 'value': {'type': 'number', 'value': '3'}},
            {'cell': 'C2', 'value': {'type': 'number', 'value': '2.5'}, 'number_format': '0.00'},
            {'cell': 'D2', 'value': {'type': 'null', 'value': None}, 'formula': 'B2*C2', 'number_format': '0.00'},
            {'cell': 'A3', 'value': {'type': 'text', 'value': 'Beta'}},
            {'cell': 'B3', 'value': {'type': 'number', 'value': '2'}},
            {'cell': 'C3', 'value': {'type': 'number', 'value': '4'}, 'number_format': '0.00'},
            {'cell': 'D3', 'value': {'type': 'null', 'value': None}, 'formula': 'B3*C3', 'number_format': '0.00'},
            {'cell': 'A5', 'value': {'type': 'text', 'value': 'Grand total'}},
            {'cell': 'D5', 'value': {'type': 'null', 'value': None}, 'formula': 'SUM(D2:D3)', 'number_format': '0.00'}]}]})
    recalculated = execute(tabular_system, 'spreadsheet_recalculate', {'snapshot_id': generated['snapshot_id'],
        'expected_sha256': generated['sha256']}, index=1)
    formulas = query(tabular_system, 'spreadsheet', recalculated['snapshot_id'], collection='formula')['rows']
    actual = {row['cell']: row['cached_value']['value'] for row in formulas}
    assert actual == {'D2': '7.5', 'D3': '8', 'D5': '15.5'}
    rendered = execute(tabular_system, 'spreadsheet_render', {'snapshot_id': recalculated['snapshot_id'], 'max_pages': 1}, index=2)
    assert rendered['page_count'] == 1
    assert rendered['evidence']['family'] == 'Calc'
    assert not rendered['evidence']['excel_layout_equivalence']
    import pypdfium2 as pdfium
    pdf = next(row for row in rendered['files'] if row['role'] == 'pdf')
    document = pdfium.PdfDocument(pdf['path'])
    page = document[0]
    text = page.get_textpage()
    try:
        content = text.get_text_range()
        assert 'Grand total' in content and '15.50' in content
    finally:
        text.close()
        page.close()
        document.close()
    readback = call(tabular_system, 'spreadsheet_render_read', {'derivative_id': rendered['derivative_id']})
    assert readback.status == 'ok', readback.error
    destination = qualification_output('spreadsheet-render-review', tmp_path)
    destination.mkdir(parents=True, exist_ok=True)
    for row in rendered['files']:
        raw = Path(row['path']).read_bytes()
        (destination / row['filename']).write_bytes(raw)
    qualification_output('spreadsheet-render-fixture.json', tmp_path).write_text(json.dumps({
        'generated': generated, 'recalculated': recalculated, 'rendered': rendered,
        'visual_review_performed': False, 'review_copy': str(destination.resolve())}, indent=2) + '\n')


@pytest.mark.parametrize('formula', ['WEBSERVICE("https://example.invalid/")', 'RTD("x",,"y")', "'[other.xlsx]Sheet1'!A1"])
def test_external_formula_render_is_rejected(formula):
    import io

    from evidence_lane_plugin.errors import LaneError
    from evidence_lane_plugin.spreadsheet_rendering import safe_calc
    from openpyxl import Workbook
    book = Workbook()
    book.active['A1'] = '=' + formula
    stream = io.BytesIO()
    book.save(stream)
    with pytest.raises(LaneError):
        safe_calc(stream.getvalue(), 'external.xlsx')
