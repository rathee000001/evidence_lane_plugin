"""Finite typed values and locators shared by separately owned tabular lanes.

These helpers do not select a lane, open a project database, or execute formulas.
Numbers retain their decimal spelling; dates and identifiers stay distinguishable.
"""
from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import math
from collections import Counter

from .document_parsers import digest
from .hashing import canonical_json_bytes

MAX_FILE_BYTES = 8_388_608
MAX_FACT_BYTES = 16_777_216
MAX_ITEMS = 50_000
MAX_ROWS = 2000
MAX_COLUMNS = 256
MAX_TABLES = 128
MAX_VALUE_BYTES = 32_768


def json_value_bytes(value):
    """Serialize finite source JSON decimals without a binary-float conversion."""
    if isinstance(value, decimal.Decimal):
        return number(value)['value'].encode('ascii')
    if isinstance(value, dict):
        return b'{' + b','.join(json_value_bytes(key) + b':' + json_value_bytes(value[key]) for key in sorted(value)) + b'}'
    if isinstance(value, list):
        return b'[' + b','.join(json_value_bytes(item) for item in value) + b']'
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def number(value):
    result = str(value)
    parsed = decimal.Decimal(result)
    if not parsed.is_finite() or len(result) > 128:
        raise ValueError('TABULAR_NUMBER_INVALID')
    return {'type': 'number', 'value': result}


def typed(value):
    if value is None:
        result = {'type': 'null', 'value': None}
    elif isinstance(value, bool):
        result = {'type': 'boolean', 'value': value}
    elif isinstance(value, (int, decimal.Decimal)):
        result = number(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            result = {'type': 'nonfinite', 'value': str(value)}
        else:
            result = number(value)
    elif isinstance(value, (dt.datetime, dt.date, dt.time)):
        result = {'type': 'datetime' if isinstance(value, dt.datetime) else 'date' if isinstance(value, dt.date) else 'time',
                  'value': value.isoformat()}
    elif isinstance(value, dt.timedelta):
        result = {'type': 'duration_seconds', 'value': str(value.total_seconds())}
    elif isinstance(value, bytes):
        result = {'type': 'binary', 'value': base64.b64encode(value).decode('ascii')}
    elif isinstance(value, str):
        result = {'type': 'text', 'value': value}
    elif isinstance(value, (dict, list)):
        # JSON nested values are source data, never flattened into ambiguous strings.
        result = {'type': 'json', 'value': json_value_bytes(value).decode('utf-8')}
    else:
        raise ValueError('TABULAR_VALUE_TYPE_UNSUPPORTED')
    if len(canonical_json_bytes(result)) > MAX_VALUE_BYTES:
        raise ValueError('TABULAR_VALUE_BYTE_BUDGET')
    return result


def display(value):
    if value['type'] == 'null':
        return ''
    return str(value['value'])


class Facts:
    def __init__(self, lane_id, format_name):
        self.lane_id, self.format_name = lane_id, format_name
        self.items, self.identities, self.used = [], set(), 0

    def add(self, kind, part, ordinal, text='', **payload):
        item_id = digest(canonical_json_bytes([kind, part, ordinal]))
        if item_id in self.identities:
            raise ValueError('TABULAR_LOCATOR_DUPLICATE')
        item = {'item_id': item_id, 'kind': kind, 'part': part, 'ordinal': ordinal, 'text': text, **payload}
        self.used += len(canonical_json_bytes(item))
        if len(self.items) >= MAX_ITEMS or self.used > MAX_FACT_BYTES:
            raise ValueError('TABULAR_STRUCTURE_BUDGET')
        self.items.append(item)
        self.identities.add(item_id)
        return item

    def finish(self, *, fidelity, limitations, features=None):
        return {'schema': 'evidence-lane.tabular-facts.v4', 'lane_id': self.lane_id, 'format': self.format_name,
            'items': self.items, 'counts': dict(sorted(Counter(item['kind'] for item in self.items).items())),
            'fidelity': fidelity, 'limitations': limitations, 'features': features or {}, 'source_bytes_mutated': False}


def table_facts(facts, name, columns, rows, *, total_rows, complete, column_types=None, **metadata):
    if len(columns) > MAX_COLUMNS or len(set(columns)) != len(columns):
        raise ValueError('TABULAR_COLUMNS_INVALID')
    if any(not isinstance(name, str) or not name or len(name) > 256 for name in columns):
        raise ValueError('TABULAR_COLUMN_NAME_INVALID')
    if len(rows) > MAX_ROWS or any(len(row) != len(columns) for row in rows):
        raise ValueError('TABULAR_ROW_SHAPE_INVALID')
    facts.add('table', name, 0, name, name=name, columns=columns, sampled_rows=len(rows), total_rows=total_rows,
              complete=complete, **metadata)
    for ordinal, column in enumerate(columns):
        types = dict(sorted(Counter(row[ordinal]['type'] for row in rows).items()))
        facts.add('column', name, ordinal, column, name=column, declared_type=(column_types or {}).get(column),
                  sampled_types=types, sampled_nulls=types.get('null', 0))
    for ordinal, row in enumerate(rows):
        facts.add('row', name, ordinal, '\t'.join(display(value) for value in row), values=row)
