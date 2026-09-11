"""Finite request schemas. Every public action receives one exact owning lane."""
from typing import Literal

from pydantic import Field, create_model, model_validator

from .hashing import canonical_json_bytes
from .registry import Contract
from .tabular_schema import LANE_TABLES
from .tabular_values import MAX_COLUMNS, MAX_FILE_BYTES, MAX_ROWS, MAX_VALUE_BYTES, number

DIGEST = r'^[0-9a-f]{64}$'


class Selection(Contract):
    lane_id: str


class Index(Selection):
    filename: str = Field(min_length=1, max_length=1000)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=MAX_FILE_BYTES)


class Snapshot(Selection):
    snapshot_id: str = Field(pattern=DIGEST)


class Query(Snapshot):
    match_mode: Literal['all', 'any'] = 'all'
    collection: str = 'text'
    part: str | None = Field(default=None, max_length=500)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class Read(Snapshot):
    representation: Literal['snapshot', 'original_source'] = 'snapshot'
    offset: int = Field(default=0, ge=0, le=MAX_FILE_BYTES)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)


class Export(Snapshot):
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)


class Value(Contract):
    type: Literal['text', 'number', 'boolean', 'null', 'date', 'datetime', 'time', 'iso_date',
                  'error', 'duration_seconds', 'binary', 'json', 'missing', 'nonfinite']
    value: str | bool | None

    @model_validator(mode='after')
    def exact_type(self):
        if self.type in {'null', 'missing'}:
            valid = self.value is None
        elif self.type == 'boolean':
            valid = isinstance(self.value, bool)
        else:
            valid = isinstance(self.value, str)
        if not valid or len(canonical_json_bytes(self.model_dump())) > MAX_VALUE_BYTES:
            raise ValueError('Use one bounded typed value')
        if self.type == 'number':
            number(self.value)
        if self.type == 'json':
            from .structured_data import load_json
            load_json(self.value)
        return self


class Cell(Contract):
    cell: str = Field(pattern=r'^[A-Z]{1,3}[1-9][0-9]{0,6}$')
    value: Value
    formula: str | None = Field(default=None, min_length=1, max_length=8192)
    number_format: str | None = Field(default=None, min_length=1, max_length=128)


class Sheet(Contract):
    name: str = Field(min_length=1, max_length=31, pattern=r'^[^\\/?*\[\]:]+$')
    cells: list[Cell] = Field(min_length=1, max_length=2000)
    freeze_panes: str | None = Field(default=None, pattern=r'^[A-Z]{1,3}[1-9][0-9]{0,6}$')
    autofilter: str | None = Field(default=None, max_length=80)


class SpreadsheetGenerate(Selection):
    logical_name: str = Field(min_length=1, max_length=160, pattern=r'^[^/\\:]+\.xlsx$')
    sheets: list[Sheet] = Field(min_length=1, max_length=16)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)

    @model_validator(mode='after')
    def bounded(self):
        if len({sheet.name.casefold() for sheet in self.sheets}) != len(self.sheets):
            raise ValueError('Worksheet names must be distinct')
        if len(canonical_json_bytes(self.model_dump())) > 524_288:
            raise ValueError('Select a smaller workbook generation batch')
        return self


class CellReplacement(Contract):
    sheet: str = Field(min_length=1, max_length=31)
    cell: str = Field(pattern=r'^[A-Z]{1,3}[1-9][0-9]{0,6}$')
    expected_value: Value
    expected_formula: str | None = Field(default=None, max_length=8192)
    replacement_value: Value
    replacement_formula: str | None = Field(default=None, min_length=1, max_length=8192)


class SpreadsheetEdit(Snapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    replacements: list[CellReplacement] = Field(min_length=1, max_length=128)


class DataGenerate(Selection):
    logical_name: str = Field(min_length=1, max_length=160, pattern=r'^[^/\\:]+\.(json|jsonl|csv|tsv)$')
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[Value]] = Field(max_length=MAX_ROWS)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)

    @model_validator(mode='after')
    def bounded(self):
        if (len(set(self.columns)) != len(self.columns) or any(not name or len(name) > 256 for name in self.columns)
                or any(len(row) != len(self.columns) for row in self.rows)
                or len(canonical_json_bytes(self.model_dump())) > 524_288):
            raise ValueError('Use a bounded rectangular dataset with distinct column names')
        return self


class Filter(Contract):
    column: str = Field(min_length=1, max_length=256)
    operator: Literal['equals', 'not_equals', 'is_null']
    value: Value = Field(default_factory=lambda: Value(type='null', value=None))


class Sort(Contract):
    column: str = Field(min_length=1, max_length=256)
    descending: bool = False


class Cast(Contract):
    column: str = Field(min_length=1, max_length=256)
    target: Literal['text'] = 'text'


class DataTransform(Snapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    logical_name: str = Field(min_length=1, max_length=160, pattern=r'^[^/\\:]+\.(json|jsonl|csv|tsv)$')
    columns: list[str] = Field(default_factory=list, max_length=MAX_COLUMNS)
    filters: list[Filter] = Field(default_factory=list, max_length=16)
    sort: list[Sort] = Field(default_factory=list, max_length=8)
    casts: list[Cast] = Field(default_factory=list, max_length=MAX_COLUMNS)
    expected_output_snapshot: str | None = Field(default=None, pattern=DIGEST)


def model_for(lane_id, base, *, name=None, extensions=None):
    fields = {'lane_id': (Literal[lane_id], lane_id)}
    if base is Query:
        fields['collection'] = (Literal['metadata', 'text', *LANE_TABLES[lane_id]], 'text')
    if extensions:
        fields['filename'] = (str, Field(min_length=1, max_length=1000, pattern=r'(?i)\.(' + '|'.join(extensions) + ')$'))
    return create_model(name or lane_id.title().replace('_', '') + base.__name__, __base__=base, **fields)
