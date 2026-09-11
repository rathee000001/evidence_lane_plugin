"""Independent Hyper creation and native intake through bounded owned workers."""
import base64
import datetime
import decimal
import io
import json
import zipfile

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.tableau_authoring import edit_xml
from evidence_lane_plugin.tableau_parsers import digest, parse_tableau, xml_items

from .test_tableau_profile_v4 import call, execute, plan
from .test_tableau_profile_v4 import (
    tableau_system as tableau_system,  # noqa: PLC0414
)


@pytest.fixture(scope='module')
def independent_hyper(tmp_path_factory):
    from tableauhyperapi import (
        Connection,
        CreateMode,
        HyperProcess,
        Inserter,
        SqlType,
        TableDefinition,
        TableName,
        Telemetry,
    )
    path = tmp_path_factory.mktemp('native-tableau') / 'independent.hyper'
    with (
        HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
            parameters={'log_config': '', 'use_tcp_port': 'off'}) as hyper,
        Connection(hyper.endpoint, path, CreateMode.CREATE) as db,
    ):
        db.catalog.create_schema('Other Schema')
        first = TableDefinition(TableName('Other Schema', 'Quoted " Table'), [
            TableDefinition.Column('ID', SqlType.big_int()),
            TableDefinition.Column('Unicode', SqlType.text()),
            TableDefinition.Column('Amount', SqlType.numeric(18, 4)),
            TableDefinition.Column('Flag', SqlType.bool()),
            TableDefinition.Column('Day', SqlType.date()),
            TableDefinition.Column('Moment', SqlType.timestamp()),
            TableDefinition.Column('Ratio', SqlType.double()),
            TableDefinition.Column('Bytes', SqlType.bytes()),
        ])
        second = TableDefinition(TableName('public', 'Empty'), [TableDefinition.Column('ID', SqlType.big_int())])
        db.catalog.create_table(first)
        db.catalog.create_table(second)
        with Inserter(db, first) as insert:
            insert.add_rows([
                [1, 'café / 東京', decimal.Decimal('123.4500'), True, datetime.date(2026, 9, 6),
                 datetime.datetime(2026, 9, 6, 12, 34, 56), 1.5, b'\x00\xff'],  # noqa: DTZ001 - native timestamp has no timezone
                [2, None, None, False, None, None, -2.0, None],
                [3, 'third', decimal.Decimal('-4.2500'), None, None, None, None, b''],
            ])
            insert.execute()
    return path.read_bytes()


def test_native_hyper_intake_bounds_types_and_preserves_source(tableau_system, independent_hyper):
    store = tableau_system[1]
    path = store.source_root / 'data.hyper'
    path.write_bytes(independent_hyper)
    plan(tableau_system, ['tableau_index'])
    indexed = execute(tableau_system, 'tableau_index', {'filename': path.name, 'max_rows_per_table': 1})
    assert indexed['fidelity']['hyper_native_read']
    tables = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'hyper_table'})
    assert tables.status == 'ok', tables.error
    by_name = {item['table_name']: item for item in tables.result['result']['rows']}
    assert by_name['Quoted " Table']['row_count'] == 3
    assert by_name['Quoted " Table']['sample_rows'] == 1 and by_name['Quoted " Table']['rows_truncated']
    assert by_name['Empty']['row_count'] == 0 and not by_name['Empty']['rows_truncated']
    read = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'hyper_row'})
    values = read.result['result']['rows'][0]['values']
    assert values[:4] == [1, 'café / 東京', {'type': 'numeric', 'value': '123.45'}, True]
    assert values[4]['value'] == '2026-09-06' and values[5]['value'].startswith('2026-09-06 12:34:56')
    assert values[6:] == [1.5, {'type': 'binary', 'content_base64': 'AP8='}]
    assert path.read_bytes() == independent_hyper


def test_packaged_hyper_and_metadata_only_mode(tableau_system, independent_hyper):
    package = io.BytesIO()
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('source.tds', '<datasource name="Native"><connection class="hyper" dbname="Data/native.hyper"/></datasource>')
        archive.writestr('Data/native.hyper', independent_hyper)
        archive.writestr('Assets/untouched.dat', b'unchanged\x00bytes')
    raw = package.getvalue()
    store = tableau_system[1]
    (store.source_root / 'source.tdsx').write_bytes(raw)
    plan(tableau_system, ['tableau_index'])
    indexed = execute(tableau_system, 'tableau_index', {'filename': 'source.tdsx', 'max_rows_per_table': 0})
    metadata = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'metadata'})
    assert metadata.result['result']['features']['hyper_files_inspected'] == 1
    tables = call(tableau_system, 'tableau_query', {'snapshot_id': indexed['snapshot_id'], 'collection': 'hyper_table'})
    assert sum(item['row_count'] for item in tables.result['result']['rows']) == 3
    assert all(item['sample_rows'] == 0 for item in tables.result['result']['rows'])
    not_inspected = parse_tableau('source.tdsx', raw, inspect_extracts=False)
    assert not not_inspected['fidelity']['hyper_native_read']
    assert 'hyper_table' not in not_inspected['counts']
    assert (store.source_root / 'source.tdsx').read_bytes() == raw


def test_original_hyper_adapter_uses_all_schemas_without_source_mutation(tmp_path, independent_hyper):
    from evidence_lane_plugin.data_toolchain import DataInspectionRequest, inspect_tableau_hyper
    path = tmp_path / 'vendor.hyper'
    path.write_bytes(independent_hyper)
    result = inspect_tableau_hyper(DataInspectionRequest(source_path=path,
        host_profile='CODEX_DESKTOP', max_rows=1, expected_sha256=digest(independent_hyper)))
    assert result['status'] == 'PASS', result
    assert 'Other Schema' in {row['name'] for row in result['schemas']}
    assert result['source_hash_verified_before_and_after'] and not result['source_mutated']
    assert path.read_bytes() == independent_hyper


def test_invalid_hyper_native_error_is_bounded_and_redacted():
    from evidence_lane_plugin.tableau_hyper import inspect_hyper
    with pytest.raises(LaneError) as caught:
        inspect_hyper([('private-name.hyper', b'private input must not appear in error')])
    assert caught.value.code == 'TABLEAU_HYPER_OPERATION_FAILED'
    assert 'private' not in str(caught.value)


def test_xml_edit_with_locally_declared_namespace_preserves_exact_target():
    raw = b'<workbook><datasource><a:calculation xmlns:a="urn:fixture" formula="1"/></datasource></workbook>'
    item = next(item for item in xml_items('native.twb', raw) if item['kind'] == 'calculation')
    changed, evidence = edit_xml({'logical_name': 'native.twb', 'content_base64': base64.b64encode(raw).decode(),
        'expected_sha256': digest(raw), 'replacements': [{'part': 'native.twb', 'item_id': item['item_id'],
            'attribute': 'formula', 'expected_value': '1', 'value': '2'}]})
    assert next(item for item in xml_items('native.twb', changed) if item['kind'] == 'calculation')['attributes']['formula'] == '2'
    assert evidence['tableau_semantic_validation'] is False
    assert json.loads(json.dumps(evidence)) == evidence
