"""Explicit legacy PPT/ODP conversion, preserving exact originals as evidence."""
from __future__ import annotations

import base64
import io
import re
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import Field

from .document_parsers import package_members, xml_root
from .document_rendering import _convert, _package_resource
from .errors import LaneError
from .hashing import canonical_json_bytes
from .presentation_parsers import digest
from .presentation_profile import (
    PresentationIndex,
    PresentationResult,
    _bytes,
    _calls,
    _relative,
    _worker,
    current_snapshot,
    publish,
    read_snapshot,
)
from .presentation_rendering import safe_render_package
from .projects import ProjectAccess
from .registry import ActionSpec
from .storage import reject_links
from .tool_routes import ToolRoute


class PresentationConvert(PresentationIndex):
    timeout_seconds: int = Field(default=45, ge=5, le=120)


def safe_legacy(content, extension):
    if extension == '.odp':
        members = package_members(content)
        if members.get('mimetype') != b'application/vnd.oasis.opendocument.presentation' or 'content.xml' not in members:
            raise LaneError('PRESENTATION_ODP_INVALID', 'Select an unencrypted OpenDocument presentation.')
        for name, raw in members.items():
            if name.casefold().startswith(('basic/', 'scripts/')):
                raise LaneError('PRESENTATION_LEGACY_ACTIVE_CONTENT', 'Conversion excludes script and macro packages.')
            if not name.endswith('.xml'):
                continue
            for node in xml_root(raw).iter():
                if node.tag.rsplit('}', 1)[-1] in {'object', 'object-ole', 'plugin', 'applet', 'event-listener', 'encryption-data'}:
                    raise LaneError('PRESENTATION_LEGACY_ACTIVE_CONTENT', 'Embedded objects, plugins, script events and encrypted parts need separate contracts.')
                for key, value in node.attrib.items():
                    if key.endswith('}href') and node.tag.rsplit('}', 1)[-1] != 'a':
                        _package_resource(value, '', members)
    elif extension == '.ppt':
        import olefile
        with olefile.OleFileIO(io.BytesIO(content), raise_defects=olefile.DEFECT_INCORRECT) as package:
            names = package.listdir()
            if not package.exists('PowerPoint Document') or len(names) > 2048:
                raise LaneError('PRESENTATION_LEGACY_STRUCTURE_INVALID', 'Select a bounded unencrypted binary PowerPoint presentation.')
            total = 0
            for name in names:
                if any(part.casefold() in {'vba', '_vba_project', 'macros', 'objectpool', 'encryptedpackage', 'encryptedsummary'}
                        or part.casefold().startswith(('vba', '_vba', 'mbd')) for part in name):
                    raise LaneError('PRESENTATION_LEGACY_ACTIVE_CONTENT', 'Legacy conversion excludes macros, embedded OLE storages and encrypted content.')
                size = package.get_size(name)
                total += size
                if size > 8_388_608 or total > 33_554_432:
                    raise LaneError('PRESENTATION_LEGACY_STREAM_BUDGET', 'Legacy presentation streams exceed their byte budget.')
                raw = package.openstream(name).read(size + 1)
                for decoded in (raw.decode('latin1'), raw.decode('utf-16le', errors='ignore')):
                    if re.search(r'(?i)(?:https?://|file:|ftp://|ppaction://(?:program|macro|ole)|DDEAUTO|INCLUDEPICTURE)', decoded):
                        raise LaneError('PRESENTATION_LEGACY_EXTERNAL_CONTENT', 'This bounded legacy converter excludes external-resource and executable-action signatures.')
    else:
        raise LaneError('PRESENTATION_CONVERSION_FORMAT_UNSUPPORTED', 'This explicit legacy converter accepts PPT and ODP.')


def conversion_worker(arguments):
    from .presentation_workers import encoded_presentation
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    original = _bytes(path, arguments['max_file_bytes'])
    extension = path.suffix.lower()
    safe_legacy(original, extension)
    with tempfile.TemporaryDirectory(prefix='evidence-lane-presentation-conversion-') as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / ('source' + extension)
        source.write_bytes(original)
        target, evidence = _convert(source, temporary, timeout_seconds=arguments['timeout_seconds'], output_format='pptx', family='impress')
        converted = _bytes(target)
        safe_render_package(converted, '.pptx')
        if source.read_bytes() != original or _bytes(path, arguments['max_file_bytes']) != original:
            raise LaneError('PRESENTATION_SOURCE_CHANGED', 'The legacy presentation changed while converting its private copy.')
    return {**encoded_presentation(arguments['logical_name'], converted, evidence={**evidence,
        'operation': 'legacy_conversion', 'original_sha256': digest(original), 'original_extension': extension,
        'source_bytes_mutated': False, 'legacy_inspection': 'bounded_package_or_ole_stream_signatures',
        'legacy_structure_equivalence': False, 'conversion_fidelity': 'derived_pptx_not_lossless_round_trip'}),
        'original_base64': base64.b64encode(original).decode()}


def convert_presentation(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() not in {'.ppt', '.odp'}:
        raise LaneError('PRESENTATION_CONVERSION_FORMAT_UNSUPPORTED', 'This explicit conversion operation accepts PPT or ODP.')
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    presentation_id = digest(canonical_json_bytes([store.project_id, 'converted_source', relative]))
    if current_snapshot(store, presentation_id) != request.expected_snapshot:
        raise LaneError('PRESENTATION_SNAPSHOT_CHANGED', 'Conversion refresh requires the exact current derivative snapshot.')
    name = relative + '.pptx'
    converted, parsed = _worker(execution, 'presentation_convert', {'filename': str(path), 'logical_name': name,
        'max_file_bytes': request.max_file_bytes, 'timeout_seconds': request.timeout_seconds})
    original = base64.b64decode(parsed.pop('original_base64'), validate=True)
    if _bytes(path, request.max_file_bytes) != original or digest(original) != parsed['evidence']['original_sha256']:
        raise LaneError('PRESENTATION_CONVERSION_BINDING', 'The converter returned a different original source identity.')
    return publish(context, presentation_id=presentation_id, logical_name=name, source_path=relative, origin='converted_source',
        previous=request.expected_snapshot, content=converted, source_content=original, parsed=parsed, operation='presentation_convert')


def verify_conversion(context, request, output):
    from .presentation_profile import verify_presentation
    # Structural verification compares the converted PPTX, while source fidelity
    # compares the preserved original. They must never be conflated.
    checks = verify_presentation(context, None, output)
    manifest, _ = read_snapshot(context.store, output.result['snapshot_id'])
    original = context.store.lane('ppt').read_object(manifest['source_object'])
    valid = _bytes(context.source_path(request.filename), request.max_file_bytes) == original
    for check in checks:
        if check['check_id'] == 'presentation_source_hash_unchanged':
            check['passed'] = valid
    return checks


def register_conversion_actions(engine):
    engine.registry.register(ActionSpec('presentation_convert', 'Convert exact legacy PPT/ODP bytes into a separate versioned PPTX while retaining the original.',
        PresentationConvert, PresentationResult, convert_presentation, permission='write', mutates=True, requires_delta=True,
        profile='presentation', workflow='source-intake', path_fields=('filename',), worker_operations=('presentation_convert',),
        verification_checks=('presentation_snapshot_integrity', 'presentation_source_hash_unchanged'), verifier=verify_conversion,
        tool_routes=(ToolRoute('presentation_convert.shared_office', convert_presentation, ('Python', 'LibreOffice', 'PPTX_OpenXML'), systems=('Windows',)),)))
