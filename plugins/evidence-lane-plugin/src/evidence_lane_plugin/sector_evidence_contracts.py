"""Bounded contracts for each separately owned evidence sector."""
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, create_model, model_validator

from .lanes import CUSTOM_INSTANCE_PATTERN
from .registry import Contract

DIGEST = r'^[0-9a-f]{64}$'
LANES = ('research', 'artifacts', 'custom')
PARSER_NAMES = Literal['auto', 'text', 'json', 'notebook', 'archive', 'sqlite', 'opaque',
    'document', 'presentation', 'spreadsheet', 'data', 'pdf_pymupdf', 'pdf_pypdf', 'pdf_pdfplumber', 'media']
DOCUMENT = {'.docx', '.dotx', '.odt', '.html', '.htm', '.xml'}
PRESENTATIONS = {'.pptx', '.pptm', '.potx', '.ppsx'}
TEXT = {'.txt', '.md', '.rst', '.yaml', '.yml', '.bib', '.mmd', '.dot', '.log'}
SHEETS = {'.xlsx', '.xlsm', '.xltx', '.xltm', '.xls', '.xlsb', '.ods'}
DATA = {'.csv', '.tsv', '.parquet', '.arrow', '.feather'}
RASTER = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.gif', '.webp'}
MEDIA = {'.mp3', '.wav', '.flac', '.m4a', '.ogg', '.mp4', '.mov', '.webm', '.mkv', '.avi'}
SQLITE = {'.db', '.sqlite', '.sqlite3'}


def parser_for(filename, requested):
    suffix = PurePosixPath(filename).suffix.lower()
    inferred = ('document' if suffix in DOCUMENT else 'presentation' if suffix in PRESENTATIONS else 'text' if suffix in TEXT else
        'spreadsheet' if suffix in SHEETS else 'data' if suffix in DATA else
        'json' if suffix in {'.json', '.jsonl'} else 'notebook' if suffix == '.ipynb' else
        'archive' if suffix == '.zip' else 'sqlite' if suffix in SQLITE else
        'pdf_pymupdf' if suffix == '.pdf' else 'media' if suffix in RASTER | MEDIA | {'.svg'} else 'opaque')
    if requested == 'auto':
        return inferred
    if (requested == inferred or requested == 'opaque'
            or (requested.startswith('pdf_') and suffix == '.pdf')
            or (requested == 'text' and inferred == 'opaque')):
        return requested
    raise ValueError('Select a parser which accepts this exact file format')


class Selection(Contract):
    lane_id: str


class Index(Selection):
    adapter_contract: str | None = Field(default=None, pattern=DIGEST)
    filename: str = Field(min_length=1, max_length=1000)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)
    parser: PARSER_NAMES = 'auto'
    sqlite_tables: list[str] = Field(default_factory=list, max_length=32)
    sqlite_rows: int = Field(default=100, ge=0, le=200)

    @model_validator(mode='after')
    def bounded(self):
        selected = parser_for(self.filename, self.parser)
        if (len(self.sqlite_tables) != len(set(self.sqlite_tables))
                or any(not name or len(name) > 1024 or '\x00' in name for name in self.sqlite_tables)
                or (self.sqlite_tables and selected != 'sqlite')
                or len(self.sqlite_tables) * self.sqlite_rows > 2000):
            raise ValueError('Use a bounded selection of exact SQLite table names')
        return self


class Snapshot(Selection):
    snapshot_id: str = Field(pattern=DIGEST)


class Query(Snapshot):
    query: str | None = Field(default=None, min_length=1, max_length=500)
    collection: str = 'text'
    match_mode: Literal['all', 'any'] = 'all'
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class Read(Snapshot):
    representation: Literal['snapshot', 'original_source'] = 'snapshot'
    offset: int = Field(default=0, ge=0, le=8_388_608)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)


def model_for(lane_id, model):
    if lane_id not in LANES:
        raise ValueError('Select one implemented evidence-sector owner')
    if lane_id == 'custom':
        return create_model('Custom' + model.__name__, __base__=model,
            lane_id=(str, Field(default='custom', pattern=r'^(?:custom|' + CUSTOM_INSTANCE_PATTERN + r')$')))
    return create_model(lane_id.title() + model.__name__, __base__=model,
        lane_id=(Literal[lane_id], lane_id))
