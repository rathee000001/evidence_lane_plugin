"""Owned tabular worker entrypoints, bounded bytes, exact format selection."""
import base64
import importlib.metadata
from pathlib import Path, PurePosixPath

from .document_parsers import digest
from .storage import reject_links
from .tabular_values import MAX_FILE_BYTES


def parse(lane_id, filename, content):
    if lane_id == 'data_excel':
        from .spreadsheet_workers import parse_spreadsheet
        return parse_spreadsheet(filename, content)
    if lane_id == 'data':
        from .structured_data import parse_data
        return parse_data(filename, content)
    raise ValueError('TABULAR_LANE_UNSUPPORTED')


def encoded(lane_id, filename, content, *, evidence=None):
    if len(content) > MAX_FILE_BYTES:
        raise ValueError('TABULAR_FILE_BYTE_BUDGET')
    suffix = PurePosixPath(filename).suffix.lower()
    package = 'python-calamine' if lane_id == 'data_excel' and suffix in {'.xls', '.xlsb', '.ods'} else 'pyarrow' if lane_id == 'data' and suffix in {'.parquet', '.arrow', '.feather'} else None
    return {'lane_id': lane_id, 'filename': filename, 'sha256': digest(content), 'bytes': len(content),
        'content_base64': base64.b64encode(content).decode('ascii'), 'facts': parse(lane_id, filename, content),
        'evidence': evidence or {'operation': 'native_intake', 'source_bytes_mutated': False,
            'parser': package or 'native_structure', 'parser_version': importlib.metadata.version(package) if package else 'v4'}}


def parse_file(arguments):
    if arguments['lane_id'] not in {'data_excel', 'data'}:
        raise ValueError('TABULAR_LANE_UNSUPPORTED')
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    limit = min(arguments['max_file_bytes'], MAX_FILE_BYTES)
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ValueError('TABULAR_FILE_BYTE_BUDGET')
    return encoded(arguments['lane_id'], arguments['logical_name'], content)


def parse_content(arguments):
    """Parse proposed bytes before exporting them to the granted destination."""
    if arguments['lane_id'] not in {'data_excel', 'data'}:
        raise ValueError('TABULAR_LANE_UNSUPPORTED')
    limit = min(arguments['max_file_bytes'], MAX_FILE_BYTES)
    encoded_content = arguments['content_base64']
    if len(encoded_content) > 4 * ((limit + 2) // 3):
        raise ValueError('TABULAR_FILE_BYTE_BUDGET')
    content = base64.b64decode(encoded_content, validate=True)
    if len(content) > limit:
        raise ValueError('TABULAR_FILE_BYTE_BUDGET')
    return encoded(arguments['lane_id'], arguments['logical_name'], content)


def generate_data(arguments):
    from .structured_data import encode_records
    content = encode_records(arguments['columns'], arguments['rows'], PurePosixPath(arguments['logical_name']).suffix.lower())
    return encoded('data', arguments['logical_name'], content, evidence={'tool': 'native_structured_data',
        'operation': 'generation', 'requested_columns': arguments['columns'], 'source_bytes_mutated': False})


def transform_data(arguments):
    from .hashing import canonical_json_bytes
    from .structured_data import encode_records, transform
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise ValueError('DATA_TRANSFORM_SOURCE_HASH_MISMATCH')
    facts = arguments['facts']
    if facts['lane_id'] != 'data' or digest(canonical_json_bytes(facts)) != arguments['facts_sha256']:
        raise ValueError('DATA_TRANSFORM_FACTS_HASH_MISMATCH')
    columns, rows = transform(facts, arguments)
    raw = encode_records(columns, rows, PurePosixPath(arguments['logical_name']).suffix.lower())
    result = encoded('data', arguments['logical_name'], raw, evidence={'tool': 'native_structured_data',
        'operation': 'select_filter_sort_cast', 'input_sha256': digest(content),
        'input_snapshot': arguments['snapshot_id'], 'output_rows': len(rows), 'selected_columns': columns, 'source_bytes_mutated': False})
    return result


def tabular_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('tabular_parse_file', __name__, 'parse_file', path_fields=('filename',), max_output_bytes=33_554_432),
        WorkerOperation('tabular_parse_content', __name__, 'parse_content', path_fields=('filename',),
            max_input_bytes=16_777_216, max_output_bytes=33_554_432),
        WorkerOperation('spreadsheet_generate', 'evidence_lane_plugin.spreadsheet_workers', 'generate',
            dependencies=('openpyxl',), max_output_bytes=33_554_432),
        WorkerOperation('spreadsheet_edit', 'evidence_lane_plugin.spreadsheet_workers', 'edit',
            dependencies=('lxml',), max_output_bytes=33_554_432),
        WorkerOperation('data_generate', __name__, 'generate_data', max_output_bytes=33_554_432),
        WorkerOperation('data_transform', __name__, 'transform_data', max_output_bytes=33_554_432),
        WorkerOperation('spreadsheet_render', 'evidence_lane_plugin.spreadsheet_rendering', 'render_worker',
            dependencies=('pypdfium2',), max_output_bytes=33_554_432),
        WorkerOperation('spreadsheet_recalculate', 'evidence_lane_plugin.spreadsheet_rendering', 'recalculate_worker',
            max_output_bytes=33_554_432),
        WorkerOperation('tabular_inspect', 'evidence_lane_plugin.tabular_inspection', 'inspect_worker', max_output_bytes=2_097_152))
