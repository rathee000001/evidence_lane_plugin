"""Explicit legacy Word/RTF conversion, preserving exact originals as evidence."""
from __future__ import annotations

import base64
import io
import re
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import Field

from .document_parsers import digest
from .document_profile import (
    DocumentIndex,
    DocumentResult,
    _bytes,
    _calls,
    _relative,
    _worker,
    current_snapshot,
    publish,
    read_snapshot,
)
from .document_rendering import _convert, safe_render_package
from .errors import LaneError
from .hashing import canonical_json_bytes
from .projects import ProjectAccess
from .registry import ActionSpec
from .storage import reject_links
from .tool_routes import ToolRoute


class DocumentConvert(DocumentIndex):
    timeout_seconds: int = Field(default=45, ge=5, le=120)


def safe_legacy(content, extension):
    if extension == '.rtf':
        if not content.lstrip().startswith(b'{\\rtf'):
            raise LaneError('DOCUMENT_RTF_INVALID', 'Select a valid RTF document.')
        if re.search(rb'\\(?:object|objdata|field|filetbl|datastore|includepicture|includetext)(?![A-Za-z])', content, re.IGNORECASE):
            raise LaneError('DOCUMENT_LEGACY_ACTIVE_CONTENT', 'Legacy conversion requires an RTF document without embedded objects or executable/external fields.')
    elif extension == '.doc':
        import olefile
        with olefile.OleFileIO(io.BytesIO(content), raise_defects=olefile.DEFECT_INCORRECT) as document:
            names = document.listdir()
            if not document.exists('WordDocument') or len(names) > 2048:
                raise LaneError('DOCUMENT_LEGACY_STRUCTURE_INVALID', 'Select a bounded unencrypted binary Word document.')
            total = 0
            for name in names:
                if any(part.casefold() in {'vba', '_vba_project', 'macros', 'objectpool', 'encryptedpackage'} for part in name):
                    raise LaneError('DOCUMENT_LEGACY_ACTIVE_CONTENT', 'Legacy Word conversion excludes macros, embedded objects and encrypted packages.')
                size = document.get_size(name)
                total += size
                if size > 8_388_608 or total > 33_554_432:
                    raise LaneError('DOCUMENT_LEGACY_STREAM_BUDGET', 'The legacy Word stream set exceeds its byte budget.')
                raw = document.openstream(name).read(size + 1)
                for decoded in (raw.decode('latin1'), raw.decode('utf-16le', errors='ignore')):
                    if re.search(r'\b(?:DDEAUTO|DDE|INCLUDETEXT|INCLUDEPICTURE)\b', decoded, re.IGNORECASE):
                        raise LaneError('DOCUMENT_LEGACY_ACTIVE_FIELD', 'Legacy conversion excludes external and executable field instructions.')
    else:
        raise LaneError('DOCUMENT_CONVERSION_FORMAT_UNSUPPORTED', 'Use native intake for modern packages; legacy conversion accepts DOC and RTF.')


def conversion_worker(arguments):
    from .document_workers import encoded_document
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    original = _bytes(path, arguments['max_file_bytes'])
    extension = path.suffix.lower()
    safe_legacy(original, extension)
    with tempfile.TemporaryDirectory(prefix='evidence-lane-document-conversion-') as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / ('source' + extension)
        source.write_bytes(original)
        target, evidence = _convert(source, temporary, timeout_seconds=arguments['timeout_seconds'], output_format='docx')
        converted = _bytes(target)
        safe_render_package(converted, '.docx')
        if source.read_bytes() != original or _bytes(path, arguments['max_file_bytes']) != original:
            raise LaneError('DOCUMENT_SOURCE_CHANGED', 'The legacy document changed while converting its private copy.')
    return {**encoded_document(arguments['logical_name'], converted, evidence={**evidence,
        'operation': 'legacy_conversion', 'original_sha256': digest(original), 'original_extension': extension,
        'source_bytes_mutated': False, 'conversion_fidelity': 'derived_docx_not_lossless_round_trip'}),
        'original_base64': base64.b64encode(original).decode()}


def convert_document(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() not in {'.doc', '.rtf'}:
        raise LaneError('DOCUMENT_CONVERSION_FORMAT_UNSUPPORTED', 'This explicit conversion operation accepts DOC or RTF.')
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    document_id = digest(canonical_json_bytes([store.project_id, 'converted_source', relative]))
    if current_snapshot(store, document_id) != request.expected_snapshot:
        raise LaneError('DOCUMENT_SNAPSHOT_CHANGED', 'Conversion refresh requires the exact current derivative snapshot.')
    name = relative + '.docx'
    converted, parsed = _worker(execution, 'document_convert', {'filename': str(path), 'logical_name': name,
        'max_file_bytes': request.max_file_bytes, 'timeout_seconds': request.timeout_seconds})
    original = base64.b64decode(parsed.pop('original_base64'), validate=True)
    if _bytes(path, request.max_file_bytes) != original or digest(original) != parsed['evidence']['original_sha256']:
        raise LaneError('DOCUMENT_CONVERSION_BINDING', 'The converter returned a different original source identity.')
    return publish(context, document_id=document_id, logical_name=name, source_path=relative, origin='converted_source',
        previous=request.expected_snapshot, content=converted, source_content=original, parsed=parsed, operation='document_convert')


def verify_conversion(context, request, output):
    from .document_profile import verify_document
    # Structural verification compares the converted DOCX, while source fidelity
    # compares the preserved original. They must never be conflated.
    checks = verify_document(context, None, output)
    manifest, _ = read_snapshot(context.store, output.result['snapshot_id'])
    original = context.store.lane('docs').read_object(manifest['source_object'])
    valid = _bytes(context.source_path(request.filename), request.max_file_bytes) == original
    for check in checks:
        if check['check_id'] == 'document_source_hash_unchanged':
            check['passed'] = valid
    return checks


def register_conversion_actions(engine):
    engine.registry.register(ActionSpec('document_convert', 'Convert exact legacy DOC/RTF bytes into a separate versioned DOCX while retaining the original.',
        DocumentConvert, DocumentResult, convert_document, permission='write', mutates=True, requires_delta=True,
        profile='document', workflow='manage-project-sources', path_fields=('filename',), worker_operations=('document_convert',),
        verification_checks=('document_snapshot_integrity', 'document_source_hash_unchanged'), verifier=verify_conversion,
        tool_routes=(ToolRoute('document_convert.shared_office', convert_document, ('Python', 'LibreOffice', 'DOCX_OpenXML'), systems=('Windows',)),)))
