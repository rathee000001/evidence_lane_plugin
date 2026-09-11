"""Owned workers for the retained evidence-sector byte parsers."""
from __future__ import annotations

import base64
import importlib.metadata
import stat
from pathlib import Path, PurePosixPath

from .document_parsers import digest
from .sector_evidence_contracts import MEDIA, RASTER, SQLITE, parser_for
from .sector_evidence_parsers import parse_evidence
from .storage import reject_links


def parser_dependencies(parser, filename, sqlite_schema_engine='stdlib'):
    suffix = PurePosixPath(filename).suffix.lower()
    if parser == 'sqlite' and sqlite_schema_engine == 'sqlalchemy':
        return ('sqlalchemy',)
    if parser == 'spreadsheet' and suffix in {'.xls', '.xlsb', '.ods'}:
        return ('python-calamine',)
    if parser == 'data' and suffix in {'.parquet', '.arrow', '.feather'}:
        return ('pyarrow',)
    if parser.startswith('pdf_'):
        return ('pypdf', *(('PyMuPDF',) if parser == 'pdf_pymupdf' else
            ('pdfplumber',) if parser == 'pdf_pdfplumber' else ()))
    if parser == 'media':
        return ('Pillow',) if suffix in RASTER else ('defusedxml',) if suffix == '.svg' else ()
    return ()


def check_database_sidecars(path):
    if path.suffix.lower() not in SQLITE:
        return
    for suffix in ('-wal', '-journal'):
        sidecar = path.with_name(path.name + suffix)
        reject_links(sidecar, Path(path.anchor))
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError('SELECTED_SQLITE_ACTIVE_SIDECAR')


def read_source(path, limit):
    reject_links(path, Path(path.anchor))
    check_database_sidecars(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError('SECTOR_EVIDENCE_REGULAR_FILE_REQUIRED')
    with path.open('rb') as stream:
        content = stream.read(min(limit, 8_388_608) + 1)
    after = path.stat()
    check_database_sidecars(path)
    if len(content) > min(limit, 8_388_608):
        raise ValueError('SECTOR_EVIDENCE_SOURCE_BYTE_BUDGET')
    fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise ValueError('SECTOR_EVIDENCE_SOURCE_CHANGED')
    return content


def parse_file(arguments):
    path = Path(arguments['filename'])
    content = read_source(path, arguments['max_file_bytes'])
    parser = parser_for(arguments['logical_name'], arguments['parser'])
    schema_engine = arguments.get('sqlite_schema_engine', 'stdlib')
    if schema_engine not in {'stdlib', 'sqlalchemy'} or (parser != 'sqlite' and schema_engine != 'stdlib'):
        raise ValueError('SECTOR_EVIDENCE_SCHEMA_ENGINE_INVALID')
    if parser == 'media' and PurePosixPath(arguments['logical_name']).suffix.lower() in MEDIA:
        # The established FFmpeg parser supplies its own owned-process receipt.
        from .shared_tool_assets import resolve_shared_asset
        _, asset = resolve_shared_asset('ffmpeg_runtime')
        native_asset = asset['files_sha256']
    else:
        native_asset = None
    versions = {name: importlib.metadata.version(name) for name in parser_dependencies(parser, arguments['logical_name'], schema_engine)}
    facts = parse_evidence(arguments['lane_id'], arguments['logical_name'], content, parser,
        sqlite_tables=arguments['sqlite_tables'], sqlite_rows=arguments['sqlite_rows'], sqlite_schema_engine=schema_engine)
    if read_source(path, arguments['max_file_bytes']) != content:
        raise ValueError('SECTOR_EVIDENCE_SOURCE_CHANGED')
    return {'lane_id': arguments['lane_id'], 'filename': arguments['logical_name'],
        'sha256': digest(content), 'bytes': len(content), 'content_base64': base64.b64encode(content).decode('ascii'),
        'facts': facts, 'evidence': {'operation': 'source_intake', 'parser': parser,
            'sqlite_schema_engine': schema_engine,
            'versions': versions, 'native_asset_sha256': native_asset, 'source_bytes_mutated': False}}


def evidence_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('sector_evidence_parse_file', __name__, 'parse_file',
        path_fields=('filename',), max_output_bytes=25_165_824),)
