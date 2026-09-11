import pytest

from .test_tabular_profile_v4 import call, execute, plan
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


@pytest.mark.parametrize('adapter', ['pandas', 'polars', 'duckdb'])
def test_actual_dataframe_inspection_on_immutable_data(tabular_system, adapter):
    action = 'data_inspect_' + adapter
    plan(tabular_system, ['data_index', action])
    indexed = execute(tabular_system, 'data_index', {'filename': 'values.json'})
    inspected = execute(tabular_system, action, {'snapshot_id': indexed['snapshot_id']}, index=1)
    assert inspected['engine'] == adapter
    assert inspected['tables'][0]['sampled_rows'] == 3
    assert inspected['tables'][0]['null_counts'] == {'active': 0, 'id': 0, 'value': 1}
    assert inspected['tables'][0]['native_shape_and_nulls_reconciled']
    assert inspected['engine_version']
    read = call(tabular_system, 'data_inspection_read', {'derivative_id': inspected['derivative_id']})
    assert read.status == 'ok', read.error
    assert read.result['result']['inspection']['source_sha256'] == indexed['sha256']


@pytest.mark.parametrize('adapter', ['openpyxl', 'pandas'])
def test_original_workbook_and_dataframe_inspection_are_attributed(tabular_system, adapter):
    action = 'spreadsheet_inspect_' + adapter
    plan(tabular_system, ['spreadsheet_index', action])
    indexed = execute(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'})
    inspected = execute(tabular_system, action, {'snapshot_id': indexed['snapshot_id']}, index=1)
    assert inspected['engine'] == adapter
    assert len(inspected['tables']) == 2
    assert inspected['formula_evaluation'] is False
    assert all(item['complete'] for item in inspected['tables'])
