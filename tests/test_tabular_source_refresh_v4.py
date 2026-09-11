"""A source refresh binds newly observed original bytes without losing history."""
import hashlib

import pytest

from tests.test_tabular_profile_v4 import call, execute, plan, tabular_system

__all__ = ['tabular_system']


@pytest.mark.parametrize('lane,action,filename', [('data', 'data', 'values.json'),
    ('data_excel', 'spreadsheet', 'fixture.xlsx')])
def test_refresh_advances_original_source_pointer_and_retirement_preserves_both_versions(tabular_system, lane, action, filename):
    engine, store, _ = tabular_system
    plan(tabular_system, [action + '_index', action + '_refresh', 'source_snapshot_retire'])
    original = (store.source_root / filename).read_bytes()
    first = execute(tabular_system, action + '_index', {'filename': filename})['snapshot_id']
    if lane == 'data':
        (store.source_root / filename).write_bytes(b'[{"id":"changed","value":43}]')
    else:
        import io

        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(original))
        workbook['Inputs']['B2'] = 'Changed input'
        workbook.save(store.source_root / filename)
    changed = (store.source_root / filename).read_bytes()
    second = execute(tabular_system, action + '_refresh', {'filename': filename, 'expected_snapshot': first}, index=1)['snapshot_id']
    selected = call(tabular_system, action + '_read', {'snapshot_id': second, 'representation': 'original_source'})
    assert selected.status == 'ok', selected.error
    assert selected.result['result']['sha256'] == hashlib.sha256(changed).hexdigest()
    history = call(tabular_system, action + '_read', {'snapshot_id': first, 'representation': 'original_source'})
    assert history.result['result']['sha256'] == hashlib.sha256(original).hexdigest()
    (store.source_root / filename).unlink()
    execute(tabular_system, 'source_snapshot_retire', {'lane_id': lane, 'snapshot_id': second}, index=2)
    assert engine.registry.selector_owner(lane).snapshot(store, second)['retired']
    current = call(tabular_system, action + '_current')
    assert current.status == 'ok', current.error
    assert current.result['result']['files'] == []
