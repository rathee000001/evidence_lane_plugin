"""Bounded engine workers for immutable Tableau inputs and derivatives."""
import base64
from pathlib import Path

from .storage import reject_links
from .tableau_parsers import MAX_BYTES, digest, fail, parse_tableau


def encoded(filename, content, *, inspect_extracts=True, max_rows_per_table=100, evidence=None):
    if len(content) > MAX_BYTES:
        fail('FILE_BYTE_BUDGET')
    facts = parse_tableau(filename, content, inspect_extracts=inspect_extracts, max_rows_per_table=max_rows_per_table)
    return {'filename': filename, 'sha256': digest(content), 'bytes': len(content),
        'content_base64': base64.b64encode(content).decode('ascii'), 'facts': facts,
        'evidence': {'source_bytes_mutated': False, 'layout_verified': False,
            'hyper': facts['hyper_evidence'], **(evidence or {'operation': 'native_extraction'})}}


def parse_file(arguments):
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open('rb') as stream:
        content = stream.read(arguments['max_file_bytes'] + 1)
    after = path.stat()
    if len(content) > arguments['max_file_bytes']:
        fail('FILE_BYTE_BUDGET')
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        fail('SOURCE_CHANGED')
    return encoded(arguments['logical_name'], content, inspect_extracts=arguments['inspect_extracts'],
        max_rows_per_table=arguments['max_rows_per_table'])


def parse_content(arguments):
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if len(content) > arguments['max_file_bytes'] or len(content) > MAX_BYTES:
        fail('FILE_BYTE_BUDGET')
    if digest(content) != arguments['expected_sha256']:
        fail('SOURCE_CHANGED')
    return encoded(arguments['logical_name'], content, **arguments['parse_options'],
        evidence={'operation': 'proposed_export_extraction'})


def generate(arguments):
    from .tableau_hyper import invoke_hyper
    native = invoke_hyper({'operation': 'generate', 'spec': arguments})
    return encoded(arguments['logical_name'], base64.b64decode(native['content_base64'], validate=True),
        max_rows_per_table=1000, evidence={'operation': 'generate_hyper', 'native_generation': native['evidence']})


def edit(arguments):
    from .tableau_authoring import edit_xml
    content, evidence = edit_xml(arguments)
    return encoded(arguments['logical_name'], content, **arguments['parse_options'], evidence=evidence)


def tableau_worker_operations():
    from .workers import WorkerOperation
    return (
        WorkerOperation('tableau_parse_content', __name__, 'parse_content',
            max_input_bytes=16777216, max_output_bytes=33554432),
        WorkerOperation('tableau_parse_file', __name__, 'parse_file', dependencies=('lxml',),
            path_fields=('filename',), max_output_bytes=33_554_432),
        WorkerOperation('tableau_generate', __name__, 'generate', dependencies=('tableauhyperapi',), max_output_bytes=33_554_432),
        WorkerOperation('tableau_edit', __name__, 'edit', dependencies=('lxml',), max_output_bytes=33_554_432),
    )
