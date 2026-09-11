from decimal import Decimal

import pytest

from .test_tabular_profile_v4 import execute, plan, query
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


@pytest.mark.parametrize('extension', ['csv', 'tsv', 'json', 'jsonl'])
def test_data_generation_and_export_use_the_owning_lane(tabular_system, extension):
    plan(tabular_system, ['data_generate', 'data_export'])
    generated = execute(tabular_system, 'data_generate', {'logical_name': 'created.' + extension,
        'columns': ['id', 'value'], 'rows': [[{'type': 'text', 'value': '0007'}, {'type': 'number', 'value': '1.25'}]]})
    rows = query(tabular_system, 'data', generated['snapshot_id'], collection='row')['rows']
    assert rows[0]['values'][0] == {'type': 'text', 'value': '0007'}
    assert rows[0]['values'][1] == {'type': 'text' if extension in {'csv', 'tsv'} else 'number', 'value': '1.25'}
    exported = execute(tabular_system, 'data_export', {'snapshot_id': generated['snapshot_id'], 'filename': 'exported.' + extension}, index=1)
    assert exported['after_sha256'] == generated['sha256']
    assert (tabular_system[1].source_root / ('exported.' + extension)).is_file()


@pytest.mark.parametrize('extension', ['parquet', 'arrow', 'feather'])
def test_arrow_public_intake_declares_and_executes_pyarrow(tabular_system, extension):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow import ipc
    table = pa.table({'id': ['0007'], 'value': pa.array([Decimal('1.2345')], type=pa.decimal128(18, 4))})
    path = tabular_system[1].source_root / ('values.' + extension)
    if extension == 'parquet':
        pq.write_table(table, path)
    else:
        with ipc.new_file(str(path), table.schema) as writer:
            writer.write_table(table)
    plan(tabular_system, ['data_index_arrow', 'data_export'])
    indexed = execute(tabular_system, 'data_index_arrow', {'filename': path.name})
    assert indexed['tool_evidence']['parser'] == 'pyarrow'
    assert indexed['tool_evidence']['parser_version'] == '25.0.0'
    rows = query(tabular_system, 'data', indexed['snapshot_id'], collection='row')['rows']
    assert rows[0]['values'][1] == {'type': 'number', 'value': '1.2345'}
    exported = execute(tabular_system, 'data_export', {'snapshot_id': indexed['snapshot_id'],
        'filename': 'exported.' + extension}, index=1)
    refreshed = exported['index_refresh']['result']
    assert not exported['source_index_refresh_required'] and refreshed['tool_evidence']['parser'] == 'pyarrow'
    assert query(tabular_system, 'data', refreshed['snapshot_id'], collection='row')['rows'][0]['values'][1] == {
        'type': 'number', 'value': '1.2345'}
    assert (tabular_system[1].source_root / ('exported.' + extension)).read_bytes() == path.read_bytes()
