"""The shared request's lowercase digest contract binds real input bytes."""
import hashlib

import pytest
from evidence_lane_plugin.data_toolchain import (
    DataInspectionRequest,
    inspect_excel_openpyxl,
    inspect_tabular_pandas,
)


@pytest.mark.parametrize('inspect', [inspect_excel_openpyxl, inspect_tabular_pandas])
def test_data_inspection_accepts_exact_hash_and_rejects_changed_source(tmp_path, inspect):
    from openpyxl import Workbook
    path = tmp_path / 'source.xlsx'
    workbook = Workbook()
    workbook.active.append(['Label', 'Value'])
    workbook.active.append(['Evidence', 42])
    workbook.save(path)
    before = path.read_bytes()
    request = DataInspectionRequest(source_path=path, host_profile='CODEX_DESKTOP',
        expected_sha256=hashlib.sha256(before).hexdigest())
    result = inspect(request)
    assert result['source_hash_verified_before_and_after']
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match='^DATA_SOURCE_HASH_MISMATCH$'):
        inspect(request.model_copy(update={'expected_sha256': '0' * 64}))
    assert path.read_bytes() == before
