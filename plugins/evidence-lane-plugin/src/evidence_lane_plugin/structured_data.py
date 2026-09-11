"""Bounded CSV/TSV, JSON and Arrow-family data extraction and transformations.

Data remains in its own lane. Sample completeness is a prerequisite for deriving
a replacement dataset; strings which look like formulas remain ordinary data.
"""
from __future__ import annotations

import csv
import decimal
import io
import json
from pathlib import PurePosixPath

from .tabular_values import (
    MAX_COLUMNS,
    MAX_FILE_BYTES,
    MAX_ROWS,
    Facts,
    json_value_bytes,
    table_facts,
    typed,
)

FORMATS = {'.csv', '.tsv', '.json', '.jsonl', '.parquet', '.arrow', '.feather'}


def _reject_constant(value):
    raise ValueError('DATA_JSON_NONFINITE_NUMBER')


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('DATA_JSON_DUPLICATE_KEY')
        value[key] = item
    return value


def load_json(text):
    value = json.loads(text, parse_float=decimal.Decimal, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    stack, visited = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        visited += 1
        if depth > 64 or visited > 100_000:
            raise ValueError('DATA_JSON_STRUCTURE_BUDGET')
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    return value


def _text(content):
    value = content.decode('utf-8-sig', errors='strict')
    if '\x00' in value:
        raise ValueError('DATA_TEXT_NUL_INVALID')
    return value


def _delimited(content, extension):
    reader = csv.reader(io.StringIO(_text(content), newline=''), delimiter='\t' if extension == '.tsv' else ',', strict=True)
    columns = next(reader, None)
    if not columns or len(columns) > MAX_COLUMNS:
        raise ValueError('DATA_HEADER_REQUIRED')
    rows, count = [], 0
    for row in reader:
        if len(row) != len(columns):
            raise ValueError('DATA_DELIMITED_ROW_WIDTH_MISMATCH')
        count += 1
        if len(rows) < MAX_ROWS:
            rows.append([typed(value) for value in row])
    return columns, rows, count, {}, {'encoding': 'UTF-8', 'header': 'first_record', 'type_inference': False}


def _records(content, extension):
    if extension == '.jsonl':
        records = [load_json(line) for line in _text(content).splitlines() if line.strip()]
    else:
        records = load_json(_text(content))
        if isinstance(records, dict):
            records = [records]
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ValueError('DATA_JSON_RECORD_OBJECTS_REQUIRED')
    columns = sorted({key for row in records for key in row})
    if len(columns) > MAX_COLUMNS:
        raise ValueError('DATA_JSON_COLUMNS_REQUIRED')
    rows = [[typed(row[column]) if column in row else {'type': 'missing', 'value': None} for column in columns]
            for row in records[:MAX_ROWS]]
    return columns, rows, len(records), {}, {'object_keys': 'sorted_union', 'missing_is_distinct_from_null': True,
        'column_names_available': bool(columns)}


def _arrow(content, extension):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from pyarrow import ipc
    if extension == '.parquet':
        reader = pq.ParquetFile(pa.BufferReader(content))
        schema, total = reader.schema_arrow, reader.metadata.num_rows
        batches = reader.iter_batches(batch_size=128)
    elif extension == '.arrow' and not content.startswith(b'ARROW1'):
        reader = ipc.open_stream(pa.BufferReader(content))
        schema, total, batches = reader.schema, None, iter(reader)
    else:
        reader = ipc.open_file(pa.BufferReader(content))
        schema, total = reader.schema, None
        batches = (reader.get_batch(index) for index in range(reader.num_record_batches))
    if len(schema.names) > MAX_COLUMNS or len(set(schema.names)) != len(schema.names):
        raise ValueError('DATA_ARROW_COLUMN_BUDGET_OR_DUPLICATE')
    rows, observed, complete = [], 0, True
    for batch in batches:
        if batch.nbytes > 33_554_432:
            raise ValueError('DATA_ARROW_BATCH_BYTE_BUDGET')
        observed += batch.num_rows
        remaining = MAX_ROWS - len(rows)
        selected = batch.slice(0, remaining)
        rows.extend([[typed(value) for value in row.values()] for row in selected.to_pylist()])
        if observed > MAX_ROWS:
            complete = False
            break
    if total is None and complete:
        total = observed
    return schema.names, rows, total, {field.name: str(field.type) for field in schema}, {
        'complete': complete and (total is None or total == len(rows)), 'reader': 'pyarrow',
        'container': 'parquet' if extension == '.parquet' else 'arrow_ipc_file' if content.startswith(b'ARROW1') else 'arrow_ipc_stream',
        'metadata': {key.decode('utf-8', 'replace'): value.decode('utf-8', 'replace')
                     for key, value in (schema.metadata or {}).items()}}


def parse_data(filename, content):
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('TABULAR_FILE_BYTE_BUDGET')
    extension = PurePosixPath(filename).suffix.lower()
    if extension in {'.csv', '.tsv'}:
        values = _delimited(content, extension)
    elif extension in {'.json', '.jsonl'}:
        values = _records(content, extension)
    elif extension in {'.parquet', '.arrow', '.feather'}:
        values = _arrow(content, extension)
    else:
        raise ValueError('DATA_FORMAT_UNSUPPORTED')
    columns, rows, total, column_types, metadata = values
    complete = metadata.pop('complete', total == len(rows))
    facts = Facts('data', extension)
    table_facts(facts, 'table', columns, rows, total_rows=total, complete=complete, column_types=column_types, **metadata)
    return facts.finish(fidelity={'structure': 'source_schema_and_typed_rows', 'complete': complete,
        'row_limit': MAX_ROWS, 'rows': 'full_dataset' if complete else 'bounded_prefix_sample'}, limitations=[
        'CSV/TSV values remain strings; no inferred numbers or dates.',
        'JSON missing keys, nulls and nested values are distinct. Nonfinite JSON numbers and duplicate keys are rejected.',
        'Empty JSON/JSONL and records with no keys contain no recoverable column names; generation inputs are recorded separately.',
        'Transformations require a complete dataset within the operation budgets.',
        'Arrow schema metadata is recorded; a data transformation may choose a new output schema.'])


def _plain(value):
    kind, body = value['type'], value['value']
    if kind in {'text', 'boolean', 'null'}:
        return body
    if kind == 'json':
        return load_json(body)
    if kind == 'number':
        return decimal.Decimal(body)
    if kind == 'missing':
        return None
    raise ValueError('DATA_OUTPUT_VALUE_TYPE_REQUIRES_EXPLICIT_CAST')


def encode_records(columns, rows, extension):
    if extension in {'.json', '.jsonl'}:
        records = [{column: _plain(value) for column, value in zip(columns, row) if value['type'] != 'missing'} for row in rows]
        result = json_value_bytes(records) if extension == '.json' else b''.join(json_value_bytes(record) + b'\n' for record in records)
    elif extension in {'.csv', '.tsv'}:
        if any(value['type'] not in {'text', 'number', 'boolean', 'null', 'missing'} for row in rows for value in row):
            raise ValueError('DATA_DELIMITED_VALUE_REQUIRES_EXPLICIT_CAST')
        output = io.StringIO(newline='')
        writer = csv.writer(output, delimiter='\t' if extension == '.tsv' else ',', lineterminator='\n')
        writer.writerow(columns)
        writer.writerows(['' if value['value'] is None else value['value'] for value in row] for row in rows)
        result = output.getvalue().encode('utf-8')
    else:
        raise ValueError('DATA_GENERATION_FORMAT_UNSUPPORTED')
    if len(result) > MAX_FILE_BYTES:
        raise ValueError('TABULAR_FILE_BYTE_BUDGET')
    return result


def _equal(left, right):
    if left['type'] == right['type'] == 'number':
        return decimal.Decimal(left['value']) == decimal.Decimal(right['value'])
    return left == right


def transform(facts, arguments):
    tables = [row for row in facts['items'] if row['kind'] == 'table']
    if len(tables) != 1 or tables[0]['complete'] is not True:
        raise ValueError('DATA_TRANSFORM_REQUIRES_COMPLETE_SOURCE')
    columns = tables[0]['columns']
    rows = [list(row['values']) for row in facts['items'] if row['kind'] == 'row']
    for condition in arguments.get('filters', []):
        if condition['column'] not in columns:
            raise ValueError('DATA_FILTER_COLUMN_MISSING')
        index = columns.index(condition['column'])
        if condition['operator'] == 'equals':
            rows = [row for row in rows if _equal(row[index], condition['value'])]
        elif condition['operator'] == 'not_equals':
            rows = [row for row in rows if not _equal(row[index], condition['value'])]
        elif condition['operator'] == 'is_null':
            rows = [row for row in rows if row[index]['type'] == 'null']
        else:
            raise ValueError('DATA_FILTER_OPERATOR_UNSUPPORTED')
    for order in reversed(arguments.get('sort', [])):
        if order['column'] not in columns:
            raise ValueError('DATA_SORT_COLUMN_MISSING')
        index = columns.index(order['column'])
        kinds = {row[index]['type'] for row in rows}
        if not kinds <= {'text'} and not kinds <= {'number'}:
            raise ValueError('DATA_SORT_REQUIRES_HOMOGENEOUS_VALUES')
        rows.sort(key=lambda row: decimal.Decimal(row[index]['value']) if row[index]['type'] == 'number' else row[index]['value'],
                  reverse=order.get('descending', False))
    selection = arguments.get('columns') or columns
    if len(selection) != len(set(selection)) or not set(selection) <= set(columns):
        raise ValueError('DATA_SELECT_COLUMNS_INVALID')
    rows = [[row[columns.index(name)] for name in selection] for row in rows]
    for cast in arguments.get('casts', []):
        if cast['column'] not in selection or cast['target'] != 'text':
            raise ValueError('DATA_CAST_UNSUPPORTED')
        index = selection.index(cast['column'])
        from .tabular_values import display
        for row in rows:
            row[index] = typed(display(row[index]))
    return selection, rows
