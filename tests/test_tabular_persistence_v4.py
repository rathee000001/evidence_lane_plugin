"""Actual lane failure, stale-write, publication and query-integrity boundaries."""
import base64
import hashlib

import pytest
from evidence_lane_plugin.storage import ProjectStore

from .test_tabular_profile_v4 import call, execute, plan, query
from .test_tabular_profile_v4 import tabular_system as tabular_system  # noqa: PLC0414


@pytest.mark.parametrize(('prefix', 'filename'), [('spreadsheet', 'fixture.xlsx'), ('data', 'values.json')])
def test_receipt_failure_cannot_publish_current_tabular_version(tabular_system, monkeypatch, prefix, filename):
    original = ProjectStore.append_receipt
    def failure(self, kind, *args, **kwargs):
        if kind == 'tabular_snapshot':
            raise RuntimeError('Injected tabular receipt failure')
        return original(self, kind, *args, **kwargs)
    monkeypatch.setattr(ProjectStore, 'append_receipt', failure)
    plan(tabular_system, [prefix + '_index'])
    source = tabular_system[1].source_root / filename
    before = source.read_bytes()
    execute(tabular_system, prefix + '_index', {'filename': filename}, expected='blocked')
    assert call(tabular_system, prefix + '_current').result['result']['files'] == []
    assert source.read_bytes() == before


@pytest.mark.parametrize(('prefix', 'filename'), [('spreadsheet', 'fixture.xlsx'), ('data', 'values.json')])
def test_source_change_after_worker_cannot_publish_old_bytes_as_current(tabular_system, monkeypatch, prefix, filename):
    from evidence_lane_plugin import tabular_profile
    source = tabular_system[1].source_root / filename
    original = tabular_profile.worker
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_bytes(source.read_bytes() + b'external change')
        return result
    monkeypatch.setattr(tabular_profile, 'worker', changed)
    plan(tabular_system, [prefix + '_index'])
    result = execute(tabular_system, prefix + '_index', {'filename': filename}, expected='blocked')
    assert result['error_code'] == 'TABULAR_SOURCE_CHANGED'
    assert call(tabular_system, prefix + '_current').result['result']['files'] == []
    assert source.read_bytes().endswith(b'external change')


@pytest.mark.parametrize('exists', [True, False])
def test_export_rechecks_destination_and_preserves_competing_bytes(tabular_system, monkeypatch, exists):
    from evidence_lane_plugin import tabular_profile
    source = tabular_system[1].source_root / 'export.json'
    if exists:
        source.write_bytes(b'old bytes')
    plan(tabular_system, ['data_index', 'data_export'])
    indexed = execute(tabular_system, 'data_index', {'filename': 'values.json'})
    original = tabular_profile._export_mkstemp
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        source.write_bytes(b'competing external bytes')
        return result
    monkeypatch.setattr(tabular_profile, '_export_mkstemp', changed)
    result = execute(tabular_system, 'data_export', {'snapshot_id': indexed['snapshot_id'], 'filename': source.name,
        'expected_sha256': hashlib.sha256(b'old bytes').hexdigest() if exists else None}, index=1, expected='blocked')
    assert result['error_code'] == 'TABULAR_EXPORT_DESTINATION_CHANGED'
    assert source.read_bytes() == b'competing external bytes'
    assert list(source.parent.glob('.evidence-lane-tabular-*.tmp')) == []
    if exists:
        assert tabular_system[1].lane('data').read_object(hashlib.sha256(b'old bytes').hexdigest()) == b'old bytes'


@pytest.mark.parametrize('table', ['data_sample', 'data_chunk_fts'])
def test_tampered_search_or_typed_records_cannot_be_reported_as_facts(tabular_system, table):
    plan(tabular_system, ['data_index'])
    indexed = execute(tabular_system, 'data_index', {'filename': 'values.json'})
    engine, store, _ = tabular_system
    with engine.project_work.mutation(store) as lease, lease.transaction('data') as db:
        if table == 'data_sample':
            db.execute("UPDATE data_sample SET payload_json=json_set(payload_json,'$.text','forged result') WHERE ordinal=0")
        else:
            db.execute("UPDATE data_chunk_fts SET text_content='forged result'")
    result = call(tabular_system, 'data_query', {'snapshot_id': indexed['snapshot_id'],
        'collection': 'row' if table == 'data_sample' else 'text', 'query': 'forged'})
    assert result.status == 'error' and result.error.code == 'TABULAR_QUERY_INTEGRITY'


def test_stale_spreadsheet_edit_does_not_replace_new_current_or_historical_bytes(tabular_system):
    plan(tabular_system, ['spreadsheet_index', 'spreadsheet_refresh', 'spreadsheet_edit'])
    first = execute(tabular_system, 'spreadsheet_index', {'filename': 'fixture.xlsx'})
    current = execute(tabular_system, 'spreadsheet_refresh', {'filename': 'fixture.xlsx', 'expected_snapshot': first['snapshot_id']}, index=1)
    blocked = execute(tabular_system, 'spreadsheet_edit', {'snapshot_id': first['snapshot_id'], 'expected_sha256': first['sha256'],
        'replacements': [{'sheet': 'Inputs', 'cell': 'B2', 'expected_value': {'type': 'number', 'value': '3'},
            'replacement_value': {'type': 'number', 'value': '5'}}]}, index=2, expected='blocked')
    assert blocked['error_code'] == 'TABULAR_EDIT_SNAPSHOT_CHANGED'
    assert call(tabular_system, 'spreadsheet_current').result['result']['files'][0]['snapshot_id'] == current['snapshot_id']
    read = call(tabular_system, 'spreadsheet_read', {'snapshot_id': first['snapshot_id'], 'max_bytes': 131072})
    assert hashlib.sha256(base64.b64decode(read.result['result']['content_base64'])).hexdigest() == first['sha256']


def test_zero_match_transformation_publishes_empty_json_with_original_column_evidence(tabular_system):
    plan(tabular_system, ['data_index', 'data_transform'])
    source = execute(tabular_system, 'data_index', {'filename': 'values.json'})
    output = execute(tabular_system, 'data_transform', {'snapshot_id': source['snapshot_id'], 'expected_sha256': source['sha256'],
        'logical_name': 'empty.json', 'filters': [{'column': 'id', 'operator': 'equals', 'value': {'type': 'text', 'value': 'absent'}}]}, index=1)
    assert output['tool_evidence']['selected_columns'] == ['active', 'id', 'value']
    assert query(tabular_system, 'data', output['snapshot_id'], collection='row')['rows'] == []
    assert query(tabular_system, 'data', output['snapshot_id'], collection='table')['rows'][0]['columns'] == []
    assert query(tabular_system, 'data', output['snapshot_id'], collection='lineage')['rows'][0]['input']['snapshot_id'] == source['snapshot_id']
