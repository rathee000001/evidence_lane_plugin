"""Owned OS workers for native editable presentation operations."""
from __future__ import annotations

import base64
from pathlib import Path

from .presentation_parsers import digest, parse_presentation
from .storage import reject_links


def encoded_presentation(filename, content, evidence=None):
    if len(content) > 8_388_608:
        raise ValueError('PRESENTATION_FILE_BYTE_BUDGET')
    return {'filename': filename, 'sha256': digest(content), 'bytes': len(content),
        'content_base64': base64.b64encode(content).decode('ascii'), 'facts': parse_presentation(filename, content),
        'evidence': {'source_bytes_mutated': False, 'layout_verified': False, **(evidence or {'operation': 'native_extraction'})}}


def parse_file(arguments):
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open('rb') as stream:
        content = stream.read(arguments['max_file_bytes'] + 1)
    after = path.stat()
    if len(content) > arguments['max_file_bytes']:
        raise ValueError('PRESENTATION_FILE_BYTE_BUDGET')
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('PRESENTATION_SOURCE_CHANGED')
    return encoded_presentation(arguments['logical_name'], content)


def parse_content(arguments):
    limit = min(arguments['max_file_bytes'], 8_388_608)
    encoded = arguments['content_base64']
    if len(encoded) > 4 * ((limit + 2) // 3):
        raise ValueError('PRESENTATION_FILE_BYTE_BUDGET')
    content = base64.b64decode(encoded, validate=True)
    if len(content) > limit:
        raise ValueError('PRESENTATION_FILE_BYTE_BUDGET')
    return encoded_presentation(arguments['logical_name'], content)


def generate(arguments):
    from .presentation_authoring import generate_package
    return encoded_presentation(arguments['logical_name'], generate_package(arguments),
        {'operation': 'generate', 'engine': 'PPTX_OpenXML', 'editable_native_shapes_and_tables': True})


def edit(arguments):
    from .presentation_authoring import edit_package
    content, evidence = edit_package(arguments)
    return encoded_presentation(arguments['logical_name'], content, {'operation': 'edit', **evidence})


def presentation_worker_operations():
    from .workers import WorkerOperation
    return (
        WorkerOperation('presentation_parse_file', __name__, 'parse_file', path_fields=('filename',), max_output_bytes=25_165_824),
        WorkerOperation('presentation_parse_content', __name__, 'parse_content', path_fields=('filename',),
            max_input_bytes=16_777_216, max_output_bytes=25_165_824),
        WorkerOperation('presentation_generate', __name__, 'generate', dependencies=('PIL', 'lxml'), max_output_bytes=25_165_824),
        WorkerOperation('presentation_edit', __name__, 'edit', dependencies=('lxml',), max_output_bytes=25_165_824),
        WorkerOperation('presentation_enrich', 'evidence_lane_plugin.presentation_enrichment', 'enrichment_worker', dependencies=('docling',)),
        WorkerOperation('presentation_convert', 'evidence_lane_plugin.presentation_conversion', 'conversion_worker',
            dependencies=('olefile',), path_fields=('filename',), max_output_bytes=33_554_432),
        WorkerOperation('presentation_render', 'evidence_lane_plugin.presentation_rendering', 'render',
            dependencies=('pypdfium2',), max_output_bytes=25_165_824, error_codes=(
                'PRESENTATION_RENDER_FORMAT_UNSUPPORTED', 'PRESENTATION_RENDER_ACTIVE_CONTENT',
                'PRESENTATION_RENDER_EXTERNAL_RESOURCE', 'PRESENTATION_RENDER_INPUT_CHANGED',
                'PRESENTATION_RENDER_PAGE_BUDGET', 'PRESENTATION_RENDER_OUTPUT_BUDGET',
                'PRESENTATION_RENDER_PIXEL_BUDGET', 'PRESENTATION_RENDER_SOURCE_CHANGED',
                'DOCUMENT_RENDER_TIMEOUT', 'DOCUMENT_RENDER_RUNTIME_CHANGED', 'DOCUMENT_RENDER_RUNTIME_MISMATCH',
                'DOCUMENT_RENDER_OWNER_UNAVAILABLE', 'DOCUMENT_RENDER_CONVERTER_FAILED', 'DOCUMENT_RENDER_OUTPUT_MISSING')),
    )
