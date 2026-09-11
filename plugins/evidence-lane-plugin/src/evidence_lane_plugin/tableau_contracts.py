"""Bounded Tableau snapshots, native extract generation and exact XML edits."""
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .registry import Contract

DIGEST = r'^[0-9a-f]{64}$'


class TableauSelection(Contract):
    lane_id: Literal['tableau'] = 'tableau'


class TableauIndex(TableauSelection):
    filename: str = Field(min_length=1, max_length=1000)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=8_388_608, ge=1, le=8_388_608)
    inspect_extracts: bool = True
    max_rows_per_table: int = Field(default=100, ge=0, le=1000)


class TableauSnapshot(TableauSelection):
    snapshot_id: str = Field(pattern=DIGEST)


class TableauQuery(TableauSnapshot):
    match_mode: Literal['all', 'any'] = 'all'
    collection: Literal['metadata', 'text', 'workbook', 'datasource', 'sheet', 'dashboard', 'story',
        'column', 'calculation', 'relationship', 'connection', 'filter', 'parameter', 'mark', 'layout',
        'package_member', 'hyper_schema', 'hyper_table', 'hyper_column', 'hyper_row', 'opaque'] = 'text'
    query: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class TableauRead(TableauSnapshot):
    representation: Literal['tableau', 'original_source', 'member'] = 'tableau'
    member: str | None = Field(default=None, min_length=1, max_length=1000)
    offset: int = Field(default=0, ge=0, le=33_554_432)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)

    @model_validator(mode='after')
    def selected_member(self):
        if (self.representation == 'member') != (self.member is not None):
            raise ValueError('Only a package member read requires its exact member name')
        return self


class TableauExport(TableauSnapshot):
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)


class HyperColumn(Contract):
    name: str = Field(min_length=1, max_length=200)
    type: Literal['text', 'big_int', 'double', 'bool', 'date', 'timestamp', 'numeric']
    nullable: bool = True
    precision: int = Field(default=18, ge=1, le=38)
    scale: int = Field(default=4, ge=0, le=38)

    @model_validator(mode='after')
    def decimal_bounds(self):
        if self.scale > self.precision:
            raise ValueError('Decimal scale cannot exceed precision')
        return self


class HyperTable(Contract):
    schema_name: str = Field(default='Extract', min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=200)
    columns: list[HyperColumn] = Field(min_length=1, max_length=128)
    rows: list[list[JsonValue]] = Field(default_factory=list, max_length=1000)

    @model_validator(mode='after')
    def rectangular(self):
        if len({column.name for column in self.columns}) != len(self.columns):
            raise ValueError('Column names must be distinct')
        if any(len(row) != len(self.columns) for row in self.rows):
            raise ValueError('Every row must match the selected columns')
        return self


class TableauGenerate(TableauSelection):
    logical_name: str = Field(pattern=r'^[^/\\:]+\.hyper$', max_length=180)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    tables: list[HyperTable] = Field(min_length=1, max_length=16)

    @model_validator(mode='after')
    def table_bounds(self):
        if len({(table.schema_name, table.name) for table in self.tables}) != len(self.tables):
            raise ValueError('Each schema/table pair must be distinct')
        if sum(len(table.rows) * len(table.columns) for table in self.tables) > 50_000:
            raise ValueError('The extract exceeds its cell budget')
        return self


class TableauXmlEdit(Contract):
    part: str = Field(min_length=1, max_length=1000)
    item_id: str = Field(pattern=DIGEST)
    attribute: Literal['caption', 'formula']
    expected_value: str | None = Field(default=None, max_length=32_768)
    value: str = Field(max_length=32_768)


class TableauEdit(TableauSnapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    replacements: list[TableauXmlEdit] = Field(min_length=1, max_length=100)
