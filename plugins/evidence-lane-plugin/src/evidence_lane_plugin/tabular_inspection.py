"""Retained DataFrame/tool inspections over immutable, bounded lane snapshots."""
from __future__ import annotations

import base64
import decimal
import importlib.metadata
import io
import json
from collections import Counter

from pydantic import Field

from . import tabular_contracts as contracts
from .document_parsers import digest
from .errors import LaneError
from .hashing import canonical_json_bytes
from .registry import ActionSpec
from .storage import now, project_snapshot
from .tabular_profile import TabularResult, check_calls, read_snapshot, result
from .tabular_schema import PREFIX, tabular_migrations
from .tabular_workers import parse
from .tool_routes import ToolRoute

ENGINES = {'pandas': ('pandas', 'pandas'), 'polars': ('Polars', 'polars'), 'duckdb': ('DuckDB', 'duckdb'),
           'openpyxl': ('openpyxl', 'openpyxl')}


class Inspection(contracts.Snapshot):
    max_rows: int = Field(default=200, ge=1, le=2000)
    max_columns: int = Field(default=64, ge=1, le=256)


class InspectionRead(contracts.Selection):
    derivative_id: str = Field(pattern=contracts.DIGEST)


def _value(value):
    kind, body = value['type'], value['value']
    if kind in {'null', 'missing'}:
        return None
    if kind == 'number':
        return decimal.Decimal(body)
    return body


def tables(facts, max_rows, max_columns):
    selected, cells = [], 0
    if facts['lane_id'] == 'data':
        table = next(item for item in facts['items'] if item['kind'] == 'table')
        columns = table['columns'][:max_columns]
        rows = [[_value(value) for value in item['values'][:max_columns]] for item in facts['items'] if item['kind'] == 'row'][:max_rows]
        selected.append({'name': table['part'], 'columns': columns, 'rows': rows,
            'complete': table['complete'] and len(columns) == len(table['columns']) and len(rows) == table['total_rows']})
    else:
        for sheet in (item for item in facts['items'] if item['kind'] == 'sheet'):
            items = [item for item in facts['items'] if item['kind'] == 'cell' and item['sheet'] == sheet['name']]
            row_ids = sorted({item['row'] for item in items})[:max_rows]
            column_ids = sorted({item['column'] for item in items})[:max_columns]
            lookup = {(item['row'], item['column']): '=' + item['formula'] if item.get('formula') is not None else _value(item['value']) for item in items}
            from .spreadsheet_workers import column_label
            rows = [[lookup.get((row, column)) for column in column_ids] for row in row_ids]
            selected.append({'name': sheet['name'], 'columns': [column_label(column) for column in column_ids], 'rows': rows,
                'row_numbers': row_ids, 'column_numbers': column_ids,
                'complete': sheet.get('complete', False) and len(row_ids) == len({item['row'] for item in items})
                    and len(column_ids) == len({item['column'] for item in items})})
    for table in selected:
        cells += len(table['rows']) * len(table['columns'])
        if cells > 32_768:
            raise LaneError('TABULAR_INSPECTION_CELL_BUDGET', 'Select fewer sampled rows or columns for this inspection.')
    return selected


def inspect_bytes(engine, lane_id, filename, content, *, max_rows=200, max_columns=64, facts=None):
    facts = facts if facts is not None else parse(lane_id, filename, content)
    selected = tables(facts, max_rows, max_columns)
    inspections = []
    for table in selected:
        name, columns, rows = table['name'], table['columns'], table['rows']
        expected_nulls = {column: sum(row[index] is None for row in rows) for index, column in enumerate(columns)}
        if engine == 'openpyxl':
            import openpyxl
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=False, data_only=False, keep_links=False)
            try:
                sheet = workbook[name]
                observed = [[sheet.cell(row_number, column_number).value for column_number in table['column_numbers']]
                            for row_number in table['row_numbers']]
                nulls = {column: sum(row[index] is None for row in observed) for index, column in enumerate(columns)}
                dtypes = {column: dict(Counter(type(row[index]).__name__ for row in observed)) for index, column in enumerate(columns)}
                count = len(observed)
            finally:
                workbook.close()
        elif engine == 'pandas':
            import pandas as pd
            frame = pd.DataFrame(rows, columns=columns)
            nulls = {key: int(value) for key, value in frame.isna().sum().items()}
            dtypes = {column: str(value) for column, value in frame.dtypes.items()}
            count = len(frame.index)
        elif engine == 'polars':
            import polars as pl
            frame = pl.DataFrame(rows, schema=columns, orient='row', strict=True)
            nulls = dict(zip(columns, frame.null_count().row(0), strict=True)) if columns else {}
            dtypes = {column: str(value) for column, value in frame.schema.items()}
            count = frame.height
        elif engine == 'duckdb':
            import duckdb
            import pyarrow as pa
            dataset = pa.Table.from_arrays([pa.array([row[index] for row in rows]) for index in range(len(columns))], names=columns)
            with duckdb.connect(':memory:', config={'enable_external_access': False, 'threads': 1, 'memory_limit': '128MB'}) as connection:
                connection.register('selected', dataset)
                count = connection.execute('SELECT count(*) FROM selected').fetchone()[0]
                nulls = {column: connection.execute('SELECT count(*) FROM selected WHERE "' + column.replace('"', '""') + '" IS NULL').fetchone()[0]
                         for column in columns}
                dtypes = {row[0]: row[1] for row in connection.execute('DESCRIBE selected').fetchall()}
        else:
            raise LaneError('TABULAR_INSPECTION_ENGINE_UNSUPPORTED', 'Select a registered inspection adapter.')
        if count != len(rows) or nulls != expected_nulls:
            raise LaneError('TABULAR_INSPECTION_RECONCILIATION', 'The selected library disagrees with the native sample shape or null positions.')
        inspections.append({'name': name, 'columns': columns, 'sampled_rows': count, 'null_counts': nulls,
            'library_dtypes': dtypes, 'complete': table['complete'], 'native_shape_and_nulls_reconciled': True})
    body = {'engine': engine, 'engine_version': importlib.metadata.version(ENGINES[engine][1]),
        'source_sha256': digest(content), 'lane_id': lane_id, 'tables': inspections,
        'scope': 'bounded_native_sample' if engine != 'openpyxl' else 'bounded_native_coordinates_and_original_openpyxl_values',
        'formula_evaluation': False, 'source_bytes_mutated': False, 'external_connections_opened': False,
        'null_semantics': 'Missing and null source values both count as null in DataFrame inspection; native facts retain their distinction.',
        'dtype_semantics': 'Observed library types for the selected sample; source schema remains separate.'}
    return {**body, 'inspection_sha256': digest(canonical_json_bytes(body))}


def inspect_worker(arguments):
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise LaneError('TABULAR_INSPECTION_BINDING', 'The selected inspection input bytes changed.')
    if digest(canonical_json_bytes(arguments['facts'])) != arguments['facts_sha256'] or arguments['facts']['lane_id'] != arguments['lane_id']:
        raise LaneError('TABULAR_INSPECTION_BINDING', 'The selected native sample differs from its snapshot facts.')
    return inspect_bytes(arguments['engine'], arguments['lane_id'], arguments['logical_name'], content,
        max_rows=arguments['max_rows'], max_columns=arguments['max_columns'], facts=arguments['facts'])


def inspect_snapshot(context, request):
    execution, store, lane_id = context.execution, context.execution.store, request.lane_id
    check_calls(execution)
    manifest, facts = read_snapshot(store, lane_id, request.snapshot_id)
    lane, prefix = store.lane(lane_id), PREFIX[lane_id]
    engine = execution.guard.spec.name.rsplit('_', 1)[1]
    response = execution.submit('tabular_inspect', request.model_dump(mode='json') | {'engine': engine,
        'expected_sha256': manifest['raw_object'], 'logical_name': manifest['logical_name'],
        'facts': facts, 'facts_sha256': manifest['facts_object'],
        'content_base64': base64.b64encode(lane.read_object(manifest['raw_object'])).decode()}).result()
    if response['status'] != 'ok':
        raise LaneError('TABULAR_INSPECTION_FAILED', 'The selected library inspection did not complete.')
    inspection = json.loads(json.dumps(response['result']))
    seal = inspection.pop('inspection_sha256')
    if (digest(canonical_json_bytes(inspection)) != seal or inspection['source_sha256'] != manifest['raw_object']
            or inspection['lane_id'] != lane_id or inspection['engine'] != engine):
        raise LaneError('TABULAR_INSPECTION_BINDING', 'The inspection result differs from its admitted input or engine.')
    body = {'schema': 'evidence-lane.tabular-inspection.v4', 'project_id': store.project_id, 'lane_id': lane_id,
        'snapshot_id': request.snapshot_id, 'inspection': inspection, 'created_at': now()}
    identity = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction([lane_id, 'receipts']):  # noqa: SIM117
        with lane.transaction() as connection:
            connection.execute(f'INSERT INTO {prefix}_derivative VALUES(?,?,?,?,?)',
                (identity, request.snapshot_id, 'inspection', lane.put_object(canonical_json_bytes(body)), body['created_at']))
            store.append_receipt('tabular_inspection', {'derivative_id': identity, 'lane_id': lane_id,
                'snapshot_id': request.snapshot_id, 'engine': engine, 'source_bytes_mutated': False})
    return result(store, lane_id, execution.guard.spec.name, {'derivative_id': identity, 'snapshot_id': request.snapshot_id, **inspection})


def read_inspection(store, lane_id, identity):
    lane, prefix = store.lane(lane_id), PREFIX[lane_id]
    with lane.connection(read_only=True) as connection:
        row = connection.execute(f"SELECT * FROM {prefix}_derivative WHERE derivative_id=? AND kind='inspection'", (identity,)).fetchone()
    if row is None:
        raise LaneError('TABULAR_INSPECTION_MISSING', 'Select an exact inspection from this lane.')
    body = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(body)) != identity or body['lane_id'] != lane_id or body['project_id'] != store.project_id
            or body['snapshot_id'] != row['snapshot_id']):
        raise LaneError('TABULAR_INSPECTION_INTEGRITY', 'The inspection manifest differs from its indexed identity.')
    return {'derivative_id': identity, **body}


def verify_inspection(context, request, output):
    body = read_inspection(context.store, request.lane_id, output.result['derivative_id'])
    manifest, facts = read_snapshot(context.store, request.lane_id, request.snapshot_id)
    expected = tables(facts, request.max_rows, request.max_columns)
    rows = body['inspection']['tables']
    valid = body['snapshot_id'] == request.snapshot_id and body['inspection']['source_sha256'] == manifest['raw_object'] and len(rows) == len(expected)
    for actual, table in zip(rows, expected):
        nulls = {column: sum(row[index] is None for row in table['rows']) for index, column in enumerate(table['columns'])}
        valid &= (actual['name'] == table['name'] and actual['columns'] == table['columns']
            and actual['sampled_rows'] == len(table['rows']) and actual['null_counts'] == nulls and actual['complete'] == table['complete'])
    return [{'check_id': name, 'passed': valid, 'evidence': {'derivative_id': output.result['derivative_id'],
        'native_shape_and_nulls_reconciled': valid}} for name in context.requested_checks]


def register_inspections(engine):
    for lane_id, prefix, adapters in (('data_excel', 'spreadsheet', ('openpyxl', 'pandas')), ('data', 'data', ('pandas', 'polars', 'duckdb'))):
        for adapter in adapters:
            action = prefix + '_inspect_' + adapter
            tools = ('Python', ENGINES[adapter][0], 'pyarrow') if adapter == 'duckdb' else ('Python', ENGINES[adapter][0])
            engine.registry.register(ActionSpec(action, 'Inspect a bounded immutable sample with an attributed library and reconcile its row shape and null positions.',
                contracts.model_for(lane_id, Inspection), TabularResult, inspect_snapshot, permission='write', mutates=True,
                requires_delta=True, profile=prefix, workflow='source-intake', worker_operations=('tabular_inspect',),
                tool_routes=(ToolRoute(action + '.library', inspect_snapshot, tools),),
                verification_checks=('tabular_inspection_reconciled',), verifier=verify_inspection))
        def reader(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, request.lane_id, 'inspection_read', read_inspection(store, request.lane_id, request.derivative_id))
        engine.registry.register(ActionSpec(prefix + '_inspection_read', 'Read an exact library inspection and its source binding.',
            contracts.model_for(lane_id, InspectionRead), TabularResult, reader, profile=prefix, workflow='source-intake',
            queryable_in_delta=True, cross_project_read=True, studio_read=True, read_migrations=tabular_migrations(lane_id)))
