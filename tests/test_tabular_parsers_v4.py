import io
import zipfile

import pytest
from evidence_lane_plugin.spreadsheet_parsers import coordinates, dependencies, parse_openxml
from evidence_lane_plugin.structured_data import encode_records, parse_data, transform


def test_sparse_far_cell_does_not_expand_dimension():
    from openpyxl import Workbook
    book = Workbook()
    book.active['XFD1048576'] = 'Sparse boundary'
    stream = io.BytesIO()
    book.save(stream)
    facts = parse_openxml('sparse.xlsx', stream.getvalue())
    cells = [item for item in facts['items'] if item['kind'] == 'cell']
    assert len(cells) == 1
    assert cells[0]['cell'] == 'XFD1048576'
    with pytest.raises(ValueError):
        coordinates('XFE1')


def test_formula_strings_and_ranges_are_not_false_cell_edges():
    assert dependencies('IF(A1="B2",SUM(\'Other Sheet\'!$C$3:$D$8),LOG10(A2))', 'Here') == [
        {'sheet': 'Here', 'range': 'A1'}, {'sheet': 'Here', 'range': 'A2'}, {'sheet': 'Other Sheet', 'range': 'C3:D8'}]


def test_nested_json_numbers_null_and_missing_survive_exact_encoding():
    facts = parse_data('values.json', b'[{"value":{"a":0.123456789012345678901},"null":null},{"value":[true,"0007"]}]')
    columns, rows = transform(facts, {})
    output = encode_records(columns, rows, '.json')
    assert b'0.123456789012345678901' in output
    again = parse_data('roundtrip.json', output)
    assert [row['values'] for row in facts['items'] if row['kind'] == 'row'] == [row['values'] for row in again['items'] if row['kind'] == 'row']


@pytest.mark.parametrize('raw', [b'[{"a":1,"a":2}]', b'[{"a":NaN}]', b'[{"a":Infinity}]'])
def test_ambiguous_json_rejected(raw):
    with pytest.raises(ValueError):
        parse_data('values.json', raw)


def test_sample_is_explicit_and_refused_as_full_transform_input():
    facts = parse_data('large.csv', ('id\n' + '\n'.join(str(index) for index in range(2001))).encode())
    table = next(row for row in facts['items'] if row['kind'] == 'table')
    assert table['total_rows'] == 2001 and table['sampled_rows'] == 2000 and not table['complete']
    with pytest.raises(ValueError, match='COMPLETE_SOURCE'):
        transform(facts, {})


def test_arrow_family_preserves_schema_dates_decimal_and_bytes():
    import datetime as dt
    import decimal

    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow import ipc
    table = pa.table({'id': ['0007'], 'amount': pa.array([decimal.Decimal('1.23')], type=pa.decimal128(10, 2)),
        'day': [dt.date(2025, 1, 1)], 'binary': [b'abc']})
    for extension in ('.parquet', '.arrow', '.feather'):
        stream = io.BytesIO()
        if extension == '.parquet':
            pq.write_table(table, stream)
        else:
            with ipc.new_file(stream, table.schema) as writer:
                writer.write_table(table)
        facts = parse_data('table' + extension, stream.getvalue())
        assert facts['fidelity']['complete']
        row = next(row for row in facts['items'] if row['kind'] == 'row')
        assert [value['type'] for value in row['values']] == ['text', 'number', 'date', 'binary']


def test_csv_identifiers_formula_like_text_and_null_are_not_inferred():
    facts = parse_data('sample.csv', b'id,value,missing\n0007,=1+2,\n')
    row = next(row for row in facts['items'] if row['kind'] == 'row')
    assert row['values'] == [{'type': 'text', 'value': '0007'}, {'type': 'text', 'value': '=1+2'}, {'type': 'text', 'value': ''}]


def test_openxml_external_sheet_target_not_followed():
    files = {'[Content_Types].xml': '<Types/>',
        'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Bad" r:id="r1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="r1" Type="/worksheet" Target="file:///outside.xml" TargetMode="External"/></Relationships>'}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match='SHEET_RELATIONSHIP'):
        parse_openxml('bad.xlsx', output.getvalue())
