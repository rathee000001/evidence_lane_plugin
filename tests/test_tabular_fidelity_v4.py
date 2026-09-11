"""Regression cases for real tabular output loss and unsafe cell replacement."""
import base64
import decimal
import io
import zipfile

import pytest
from evidence_lane_plugin.document_parsers import digest, package_members
from evidence_lane_plugin.spreadsheet_parsers import S, parse_openxml
from evidence_lane_plugin.spreadsheet_workers import edit, generate
from evidence_lane_plugin.structured_data import encode_records, parse_data, transform

from .test_tabular_profile_v4 import spreadsheet_bytes


def number(value):
    return {'type': 'number', 'value': value}


def replacement(content, cell='B2'):
    facts = parse_openxml('fixture.xlsx', content)
    value = next(item for item in facts['items'] if item['kind'] == 'cell' and item['sheet'] == 'Inputs' and item['cell'] == cell)
    return {'logical_name': 'fixture.xlsx', 'content_base64': base64.b64encode(content).decode(),
        'expected_sha256': digest(content), 'replacements': [{'sheet': 'Inputs', 'cell': cell,
            'expected_value': value['value'], 'expected_formula': value['formula'], 'replacement_value': number('5')}]}


def altered_workbook(kind):
    from lxml import etree as ET
    members = package_members(spreadsheet_bytes())
    part = 'xl/worksheets/sheet1.xml'
    root = ET.fromstring(members[part])
    if kind == 'protection':
        ET.SubElement(root, S + 'sheetProtection', sheet='1')
    elif kind == 'merged':
        ET.SubElement(ET.SubElement(root, S + 'mergeCells'), S + 'mergeCell', ref='A2:B2')
    elif kind in {'array', 'shared', 'dataTable'}:
        cell = next(node for node in root.iter(S + 'c') if node.get('r') == 'A2')
        for child in list(cell):
            cell.remove(child)
        cell.attrib.pop('t', None)
        ET.SubElement(cell, S + 'f', t=kind, ref='A2:B2').text = 'ROW(A2:B2)'
    elif kind == 'validation':
        ET.SubElement(ET.SubElement(root, S + 'dataValidations'), S + 'dataValidation', sqref='B2:B3', type='whole')
    elif kind == 'metadata':
        next(node for node in root.iter(S + 'c') if node.get('r') == 'B2').set('cm', '1')
    elif kind in {'inline_rich_text', 'shared_rich_text'}:
        node = next(node for node in root.iter(S + 'c') if node.get('r') == 'B2')
        for child in list(node):
            node.remove(child)
        container = ET.Element(S + 'si') if kind == 'shared_rich_text' else ET.SubElement(node, S + 'is')
        run = ET.SubElement(container, S + 'r')
        ET.SubElement(ET.SubElement(run, S + 'rPr'), S + 'b')
        ET.SubElement(run, S + 't').text = '3'
        node.set('t', 's' if kind == 'shared_rich_text' else 'inlineStr')
        if kind == 'shared_rich_text':
            ET.SubElement(node, S + 'v').text = '0'
            strings = ET.Element(S + 'sst')
            strings.append(container)
            members['xl/sharedStrings.xml'] = ET.tostring(strings)
    elif kind == 'workbook_protection':
        workbook = ET.fromstring(members['xl/workbook.xml'])
        workbook.find(S + 'workbookProtection').set('lockStructure', '1')
        members['xl/workbook.xml'] = ET.tostring(workbook)
    members[part] = ET.tostring(root)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize('kind', ['protection', 'merged', 'array', 'shared', 'dataTable', 'validation', 'metadata',
    'inline_rich_text', 'shared_rich_text', 'workbook_protection'])
def test_cell_replacement_refuses_interdependent_or_protected_cells(kind):
    content = altered_workbook(kind)
    expected = {'protection': 'PROTECTED_SHEET', 'merged': 'MERGED_OR_VALIDATED_RANGE_UNSUPPORTED',
        'validation': 'MERGED_OR_VALIDATED_RANGE_UNSUPPORTED', 'metadata': 'EXTENDED_CELL_METADATA_UNSUPPORTED',
        'array': 'COMPLEX_FORMULA_RANGE_UNSUPPORTED', 'shared': 'COMPLEX_FORMULA_RANGE_UNSUPPORTED',
        'dataTable': 'COMPLEX_FORMULA_RANGE_UNSUPPORTED', 'inline_rich_text': 'RICH_TEXT_UNSUPPORTED',
        'shared_rich_text': 'RICH_TEXT_UNSUPPORTED', 'workbook_protection': 'PROTECTED_WORKBOOK'}[kind]
    with pytest.raises(ValueError, match='^SPREADSHEET_EDIT_' + expected + '$'):
        edit(replacement(content))


@pytest.mark.parametrize('value', ['0.1234567890123456789', '12345678901234567', '1e400', '1e-400'])
def test_new_excel_number_cannot_silently_round_or_overflow(value):
    with pytest.raises(ValueError, match='SPREADSHEET_.*NUMBER'):
        generate({'logical_name': 'precision.xlsx', 'sheets': [{'name': 'Values', 'cells': [{'cell': 'A1', 'value': number(value)}]}]})


def test_number_filter_uses_numeric_equality_and_keeps_spelling():
    facts = parse_data('values.json', b'[{"x":1.0},{"x":1.00},{"x":"1"},{"x":true}]')
    columns, rows = transform(facts, {'filters': [{'column': 'x', 'operator': 'equals', 'value': number('1')}]})
    assert columns == ['x']
    assert [row[0]['value'] for row in rows] == ['1.0', '1.00']
    assert all(decimal.Decimal(row[0]['value']) == 1 for row in rows)


@pytest.mark.parametrize('extension', ['.json', '.jsonl'])
def test_zero_row_output_can_be_reparsed_without_invented_records(extension):
    raw = encode_records(['Id', 'Amount'], [], extension)
    facts = parse_data('empty' + extension, raw)
    table = next(item for item in facts['items'] if item['kind'] == 'table')
    assert table['total_rows'] == 0 and table['complete']
    assert table['columns'] == []
    assert not any(item['kind'] == 'row' for item in facts['items'])


def test_json_number_serialization_is_valid_for_all_accepted_spellings():
    for value in ['+1', '01', '1_0', '1.', '.1', ' 1 ']:
        raw = encode_records(['x'], [[number(value)]], '.json')
        facts = parse_data('values.json', raw)
        parsed = next(item for item in facts['items'] if item['kind'] == 'row')['values'][0]
        assert decimal.Decimal(parsed['value']) == decimal.Decimal(value)


@pytest.mark.parametrize('rows', [0, 2, 2001])
def test_arrow_stream_intake_is_bounded_and_reports_completeness(rows):
    import pyarrow as pa
    from pyarrow import ipc
    source = pa.table({'Id': list(range(rows))})
    output = pa.BufferOutputStream()
    with ipc.new_stream(output, source.schema) as writer:
        writer.write_table(source, max_chunksize=128)
    facts = parse_data('values.arrow', output.getvalue().to_pybytes())
    table = next(item for item in facts['items'] if item['kind'] == 'table')
    assert table['container'] == 'arrow_ipc_stream'
    assert table['sampled_rows'] == min(rows, 2000) and table['complete'] == (rows <= 2000)


def test_excel_generation_preserves_unicode_literal_text_and_iso_dates():
    from openpyxl import load_workbook
    source = [{'cell': 'A1', 'value': {'type': 'text', 'value': '= literal café 東京\nnext'}},
        {'cell': 'A2', 'value': {'type': 'date', 'value': '2026-09-06'}},
        {'cell': 'B2', 'value': {'type': 'datetime', 'value': '2026-09-06T01:02:03.125'}},
        {'cell': 'C2', 'value': {'type': 'time', 'value': '01:02:03.125'}}]
    result = generate({'logical_name': 'dates.xlsx', 'sheets': [{'name': 'Values', 'cells': source}]})
    workbook = load_workbook(io.BytesIO(base64.b64decode(result['content_base64'])), data_only=False)
    try:
        assert workbook.active['A1'].value == source[0]['value']['value'] and workbook.active['A1'].data_type == 's'
        assert workbook.active['A2'].value.isoformat() == source[1]['value']['value']
        assert workbook.active['B2'].value.isoformat(timespec='milliseconds') == source[2]['value']['value']
        assert workbook.active['C2'].value.isoformat(timespec='milliseconds') == source[3]['value']['value']
    finally:
        workbook.close()


def test_arrow_transform_consumes_bound_facts_without_importing_pyarrow(monkeypatch):
    import sys

    import pyarrow as pa
    from evidence_lane_plugin.hashing import canonical_json_bytes
    from evidence_lane_plugin.tabular_workers import transform_data
    from pyarrow import ipc
    dataset = pa.table({'Id': ['0007'], 'Amount': [2]})
    output = pa.BufferOutputStream()
    with ipc.new_file(output, dataset.schema) as writer:
        writer.write_table(dataset)
    raw = output.getvalue().to_pybytes()
    facts = parse_data('values.arrow', raw)
    monkeypatch.setitem(sys.modules, 'pyarrow', None)
    result = transform_data({'lane_id': 'data', 'logical_name': 'result.json', 'source_name': 'values.arrow',
        'snapshot_id': '1' * 64, 'expected_sha256': digest(raw), 'content_base64': base64.b64encode(raw).decode(),
        'facts': facts, 'facts_sha256': digest(canonical_json_bytes(facts)), 'columns': ['Id']})
    assert next(item for item in result['facts']['items'] if item['kind'] == 'row')['values'] == [{'type': 'text', 'value': '0007'}]
