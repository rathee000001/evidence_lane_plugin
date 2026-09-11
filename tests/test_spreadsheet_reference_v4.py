"""Retained upstream XLS/XLSB/ODS cases without removed Access dependencies."""
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from .test_tabular_profile_v4 import call, execute, plan, query
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


def reference_bytes(name):
    root = Path(os.environ.get('EVI_TABULAR_REFERENCE_FIXTURES', '.work/qualification/tabular-reference-fixtures'))
    receipt = json.loads((root / 'receipt.json').read_text(encoding='utf-8'))
    row = next(row for row in receipt['files'] if row['path'] == 'calamine/' + name)
    assert row['commit'] == '0a7998e50a7586f308a2d455170ec63c8135dfd3'
    raw = (root / row['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row['sha256'] and len(raw) == row['bytes']
    return raw


@pytest.mark.parametrize('extension', ['xlsb', 'xls', 'ods'])
def test_upstream_legacy_intake_retains_typed_dates_values_and_source_bytes(tabular_system, extension):
    raw = reference_bytes('base.' + extension)
    source = tabular_system[1].source_root / ('reference.' + extension)
    source.write_bytes(raw)
    plan(tabular_system, ['spreadsheet_index_values'])
    result = execute(tabular_system, 'spreadsheet_index_values', {'filename': source.name})
    cells = query(tabular_system, 'spreadsheet', result['snapshot_id'], collection='cell', limit=100)['rows']
    first = {row['cell']: row['value'] for row in cells if row['sheet'] == 'Sheet1'}
    assert first['A2'] == {'type': 'text', 'value': 'String'}
    assert first['C2'] == {'type': 'number', 'value': '1.1'}
    assert first['D2'] == {'type': 'boolean', 'value': True}
    assert first['F2'] == {'type': 'date', 'value': '2010-10-10'}
    assert first['G2'] == {'type': 'datetime', 'value': '2010-10-10T10:10:10'}
    read = call(tabular_system, 'spreadsheet_read', {'snapshot_id': result['snapshot_id'], 'max_bytes': 131072})
    assert base64.b64decode(read.result['result']['content_base64']) == raw == source.read_bytes()


def test_encrypted_upstream_xlsb_blocks_without_publishing(tabular_system):
    source = tabular_system[1].source_root / 'encrypted.xlsb'
    source.write_bytes(reference_bytes('password.xlsb'))
    plan(tabular_system, ['spreadsheet_index_values'])
    execute(tabular_system, 'spreadsheet_index_values', {'filename': source.name}, expected='blocked')
    assert call(tabular_system, 'spreadsheet_current').result['result']['files'] == []
