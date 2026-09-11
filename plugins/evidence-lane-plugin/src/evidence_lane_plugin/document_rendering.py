"""Exact document-to-PDF rendering with a verified shared Windows office runtime.

LibreOffice is a separate owned process. Every conversion uses a private profile;
macros, embedded executable objects and external resource loading are rejected.
"""
from __future__ import annotations

import base64
import io
import json
import os
import posixpath
import re
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urldefrag

from .document_parsers import W, digest, package_members, xml_root
from .document_schema import DOC_MIGRATIONS
from .errors import LaneError
from .hashing import canonical_json_bytes
from .process_ownership import ChildProcessGroup, kernel
from .shared_native_tools import resolve_native_tool
from .shared_tool_assets import resolve_shared_asset
from .storage import now, project_snapshot


def _safe_word_fields(root):
    # One logical Word field can span many instrText runs and paragraphs.
    # Checking each run independently permits a split INCLUDE + TEXT code.
    fields, orphan = [], []
    def check(values):
        instruction = ''.join(values)
        if re.search(r'\b(DDE|DDEAUTO|INCLUDETEXT|INCLUDEPICTURE|LINK|DATABASE|RD)\b', instruction, re.IGNORECASE):
            raise LaneError('DOCUMENT_RENDER_ACTIVE_FIELD', 'External or executable field instructions are outside this render contract.')
    for node in root.iter():
        if node.tag == W + 'fldSimple':
            check([node.get(W + 'instr', '')])
        elif node.tag == W + 'fldChar':
            kind = node.get(W + 'fldCharType')
            if kind == 'begin':
                fields.append([])
            elif kind in {'separate', 'end'} and fields:
                check(fields[-1])
                if kind == 'end':
                    fields.pop()
        elif node.tag == W + 'instrText':
            (fields[-1] if fields else orphan).append(node.text or '')
    for values in [*fields, orphan]:
        check(values)


def _package_resource(target, base, members):
    # Decode URI syntax before checking paths. Escaped dot segments are still
    # paths, and a missing relative member must not become a filesystem lookup.
    target = unquote(urldefrag(target)[0], errors='strict')
    if not target:
        return
    if (re.match(r'(?i)^(?:[a-z][a-z0-9+.-]*:|//|/|\\)', target)
            or '\\' in target or '\x00' in target or re.search(r'%[0-9a-fA-F]{2}', target)):
        raise LaneError('DOCUMENT_RENDER_EXTERNAL_RESOURCE', 'Document resources must resolve to an exact package member.')
    name = posixpath.normpath(posixpath.join(base, target))
    if name == '..' or name.startswith('../') or name not in members:
        raise LaneError('DOCUMENT_RENDER_EXTERNAL_RESOURCE', 'Document resources must resolve to an exact package member.')


def safe_render_package(content, extension):
    if extension not in {'.docx', '.dotx', '.odt'}:
        raise LaneError('DOCUMENT_RENDER_FORMAT_UNSUPPORTED', 'Select a supported native document package.')
    members = package_members(content)
    for name, raw in members.items():
        folded = name.lower()
        if (folded.endswith('vbaproject.bin') or '/embeddings/' in folded or folded.startswith(('basic/', 'scripts/'))):
            raise LaneError('DOCUMENT_RENDER_ACTIVE_CONTENT', 'Rendering requires a document without macros or embedded executable objects.')
        if not name.endswith(('.xml', '.rels')):
            continue
        root = xml_root(raw)
        _safe_word_fields(root)
        for node in root.iter():
            if node.tag.rsplit('}', 1)[-1] in {'altChunk', 'object', 'object-ole', 'OLEObject'}:
                raise LaneError('DOCUMENT_RENDER_ACTIVE_CONTENT', 'Embedded objects and alternate imported document content require separate contracts.')
            if (node.tag.endswith('}Relationship') and node.get('TargetMode') == 'External'
                    and not node.get('Type', '').endswith('/hyperlink')):
                raise LaneError('DOCUMENT_RENDER_EXTERNAL_RESOURCE', 'External resource references must be removed before rendering.')
            if node.tag.endswith('}Relationship') and node.get('TargetMode') != 'External':
                if '/_rels/' in name:
                    base = name.rsplit('/_rels/', 1)[0]
                elif name.startswith('_rels/'):
                    base = ''
                else:
                    raise LaneError('DOCUMENT_RENDER_EXTERNAL_RESOURCE', 'Package relationships require their owning part locator.')
                _package_resource(node.get('Target', ''), base, members)
            for key, value in node.attrib.items():
                if key.endswith('}href') and not node.tag.endswith('}a'):
                    _package_resource(value, posixpath.dirname(name), members)
    return members


def _convert(source, temporary, *, timeout_seconds, output_format='pdf', family='writer'):
    began = time.monotonic()
    folder, asset = resolve_shared_asset('libreoffice_runtime')
    from .installation_layout import studio_installation
    resolution = resolve_native_tool(
        'libreoffice', runtime_root=studio_installation().active_root
    )
    if not resolution.executable.is_relative_to(folder):
        raise LaneError('DOCUMENT_RENDER_RUNTIME_MISMATCH', 'The selected office executable is outside its verified runtime asset.')
    runtime_verified = time.monotonic()
    profile, output = temporary / 'profile', temporary / 'output'
    (profile / 'user').mkdir(parents=True)
    output.mkdir()
    (profile / 'user/registrymodifications.xcu').write_text(
        '<?xml version="1.0" encoding="UTF-8"?><oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop></item></oor:items>', encoding='utf-8')
    formats = {'writer': {'pdf': 'pdf:writer_pdf_Export', 'docx': 'docx:Office Open XML Text',
        'doc': 'doc:MS Word 97', 'rtf': 'rtf:Rich Text Format'},
        'calc': {'pdf': 'pdf:calc_pdf_Export', 'xlsx': 'xlsx:Calc MS Excel 2007 XML', 'ods': 'ods:calc8', 'xls': 'xls:MS Excel 97'},
        'impress': {'pdf': 'pdf:impress_pdf_Export', 'pptx': 'pptx:Impress MS PowerPoint 2007 XML',
                    'odp': 'odp:impress8', 'ppt': 'ppt:MS PowerPoint 97'},
        'draw': {'pdf': 'pdf:draw_pdf_Export', 'fodg': 'fodg:OpenDocument Drawing Flat XML', 'odg': 'odg:draw8'}}
    conversion = formats[family][output_format]
    arguments = [str(resolution.executable), '-env:UserInstallation=' + profile.as_uri(), '--headless',
        '--nologo', '--nodefault', '--norestore', '--nolockcheck', '--convert-to', conversion,
        '--outdir', str(output), str(source)]
    group = ChildProcessGroup()
    group.start()
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith('PYTHON')}
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    converter_started = time.monotonic()
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(arguments, cwd=temporary, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                shell=False, env=environment, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if group.handle and not kernel().AssignProcessToJobObject(group.handle, int(process._handle)):
                process.kill()
                process.wait()
                raise LaneError('DOCUMENT_RENDER_OWNER_UNAVAILABLE', 'The office child could not join its owned process group.')
            try:
                process.wait(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                group.close()
                process.kill()
                process.wait(timeout=5)
                raise LaneError('DOCUMENT_RENDER_TIMEOUT', 'The owned office conversion exceeded its time budget.') from None
            stdout.seek(0, 2)
            stderr.seek(0, 2)
            if stdout.tell() + stderr.tell() > 1_048_576:
                raise LaneError('DOCUMENT_RENDER_LOG_BUDGET', 'The converter exceeded its bounded diagnostic output.')
            if process.returncode != 0:
                raise LaneError('DOCUMENT_RENDER_CONVERTER_FAILED', 'The office converter did not complete successfully.')
    finally:
        group.close()
    converter_finished = time.monotonic()
    path = output / (source.stem + '.' + output_format)
    if not path.is_file() or path.stat().st_size == 0:
        raise LaneError('DOCUMENT_RENDER_OUTPUT_MISSING', 'The converter returned without producing the requested artifact.')
    _, after_asset = resolve_shared_asset('libreoffice_runtime')
    runtime_reverified = time.monotonic()
    if after_asset['files_sha256'] != asset['files_sha256']:
        raise LaneError('DOCUMENT_RENDER_RUNTIME_CHANGED', 'The shared office runtime changed during this conversion.')
    return path, {'engine': 'LibreOffice', 'version': resolution.version,
        'office_family': family,
        'executable_sha256': resolution.executable_sha256, 'runtime_files_sha256': asset['files_sha256'],
        'installation_manifest_sha256': asset['installation_manifest_sha256'],
        'profile': 'private_per_operation', 'owner': 'engine_os_worker_and_owned_office_child',
        'host_identity': 'not_attested', 'word_layout_equivalence': False,
        'runtime_files_unchanged': True, 'host_python_environment_inherited': False,
        'timings_seconds': {'runtime_verification_before': round(runtime_verified - began, 6),
            'office_conversion': round(converter_finished - converter_started, 6),
            'runtime_verification_after': round(runtime_reverified - converter_finished, 6),
            'total': round(runtime_reverified - began, 6)}}


def render(arguments):
    import pypdfium2 as pdfium
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise LaneError('DOCUMENT_RENDER_INPUT_CHANGED', 'The render bytes differ from the requested snapshot.')
    extension = Path(arguments['logical_name']).suffix.lower()
    safe_render_package(content, extension)
    with tempfile.TemporaryDirectory(prefix='evidence-lane-document-render-') as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / ('document' + extension)
        source.write_bytes(content)
        pdf_path, evidence = _convert(source, temporary, timeout_seconds=arguments['timeout_seconds'])
        if pdf_path.stat().st_size > arguments['max_output_bytes']:
            raise LaneError('DOCUMENT_RENDER_OUTPUT_BUDGET', 'The rendered PDF exceeds its requested output budget.')
        pdf = pdf_path.read_bytes()
        files = [{'filename': 'document.pdf', 'role': 'pdf', 'content_base64': base64.b64encode(pdf).decode(),
                  'sha256': digest(pdf), 'bytes': len(pdf)}]
        used, pages = len(pdf), []
        document = pdfium.PdfDocument(pdf)
        try:
            if not 1 <= len(document) <= arguments['max_pages']:
                raise LaneError('DOCUMENT_RENDER_PAGE_BUDGET', 'Select a document within the requested page budget.')
            for index in range(len(document)):
                page = document[index]
                bitmap, text_page = None, None
                try:
                    width, height = page.get_size()
                    if width * height * (arguments['dpi'] / 72) ** 2 > 20_000_000:
                        raise LaneError('DOCUMENT_RENDER_PIXEL_BUDGET', 'A document page exceeds its finite pixel budget.')
                    bitmap = page.render(scale=arguments['dpi'] / 72)
                    image = bitmap.to_pil()
                    output = io.BytesIO()
                    image.save(output, format='PNG')
                    png = output.getvalue()
                    image.close()
                    used += len(png)
                    if used > arguments['max_output_bytes']:
                        raise LaneError('DOCUMENT_RENDER_OUTPUT_BUDGET', 'Rendered pages exceed the selected output byte budget.')
                    text_page = page.get_textpage()
                    page_text = text_page.get_text_range()
                    files.append({'filename': f'page-{index + 1}.png', 'role': 'page_png', 'page_number': index + 1,
                        'content_base64': base64.b64encode(png).decode(), 'sha256': digest(png), 'bytes': len(png)})
                    pages.append({'page_number': index + 1, 'width_points': width, 'height_points': height,
                        'text_sha256': digest(page_text.encode()), 'text_characters': len(page_text)})
                finally:
                    if text_page is not None:
                        text_page.close()
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
        finally:
            document.close()
        if source.read_bytes() != content:
            raise LaneError('DOCUMENT_RENDER_SOURCE_CHANGED', 'The converter changed its private source copy.')
    return {'input_sha256': arguments['expected_sha256'], 'files': files, 'pages': pages,
        'evidence': {**evidence, 'raster_engine': 'pypdfium2', 'dpi': arguments['dpi'],
            'source_bytes_mutated': False, 'visual_review': 'required_not_performed_by_renderer',
            'field_values': 'converter_evaluated_no_word_equivalence_claim', 'font_substitution': 'possible',
            'tracked_changes_display': 'converter_default_revision_markup_may_be_visible',
            'odt_repeated_table_layout': 'converter_dependent_logical_repeats_not_guaranteed' if extension == '.odt' else 'not_applicable'}}


def render_document(context, request):
    from .document_profile import _calls, _natural, read_snapshot, result
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    lane = store.lane('docs')
    content = lane.read_object(manifest['raw_object'])
    response = execution.submit('document_render', {**request.model_dump(mode='json'),
        'logical_name': manifest['logical_name'], 'expected_sha256': manifest['raw_object'],
        'content_base64': base64.b64encode(content).decode()}).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'DOCUMENT_RENDER_FAILED'), 'The document render worker did not complete.')
    generated = json.loads(json.dumps(response['result']))
    if generated['input_sha256'] != manifest['raw_object']:
        raise LaneError('DOCUMENT_RENDER_BINDING', 'The render worker returned another source binding.')
    contents = {}
    for row in generated['files']:
        raw = base64.b64decode(row.pop('content_base64'), validate=True)
        if digest(raw) != row['sha256'] or len(raw) != row['bytes']:
            raise LaneError('DOCUMENT_RENDER_BINDING', 'The rendered artifact failed byte verification.')
        contents[row['filename']] = raw
    expected_names = {'document.pdf', *(f'page-{i + 1}.png' for i in range(len(generated['pages'])))}
    if (set(contents) != expected_names or len(contents) != len(generated['files'])
            or not 1 <= len(generated['pages']) <= request.max_pages
            or sum(map(len, contents.values())) > request.max_output_bytes):
        raise LaneError('DOCUMENT_RENDER_BINDING', 'The render worker returned a different bounded page set.')
    body = {'schema': 'evidence-lane.document-render.v4', 'project_id': store.project_id, 'lane_id': 'docs',
        'snapshot_id': request.snapshot_id, **generated, 'created_at': now()}
    render_id = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['docs', 'receipts']):  # noqa: SIM117 - coordination must precede the lane connection
        with lane.transaction() as connection:
            for name, raw in contents.items():
                lane.put_object(raw)
                _natural(lane, render_id, name, raw)
            manifest_object = lane.put_object(canonical_json_bytes(body))
            connection.execute('INSERT INTO doc_render VALUES(?,?,?,?)', (render_id, request.snapshot_id, manifest_object, body['created_at']))
            store.append_receipt('document_render', {'render_id': render_id, 'snapshot_id': request.snapshot_id,
                'page_count': len(generated['pages']), 'source_bytes_mutated': False})
    return result(store, 'document_render', {'render_id': render_id, 'snapshot_id': request.snapshot_id,
        'page_count': len(generated['pages']), 'files': [{**row, 'path': str(lane.files / 'natural' / render_id / row['filename'])}
            for row in generated['files']], 'evidence': generated['evidence'], 'source_bytes_mutated': False})


def read_render(store, render_id):
    from .document_profile import _bytes, _relative
    lane = store.lane('docs')
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM doc_render WHERE render_id=?', (render_id,)).fetchone()
    if row is None:
        raise LaneError('DOCUMENT_RENDER_MISSING', 'Select an exact document rendering from this Docs lane.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(manifest)) != render_id or manifest['snapshot_id'] != row['snapshot_id']
            or manifest['project_id'] != store.project_id or manifest['lane_id'] != 'docs'):
        raise LaneError('DOCUMENT_RENDER_INTEGRITY', 'The render manifest differs from its indexed identity.')
    for item in manifest['files']:
        if '/' in _relative(item['filename']):
            raise LaneError('DOCUMENT_RENDER_INTEGRITY', 'A render artifact name points outside its exact version.')
        path = lane.files / 'natural' / render_id / item['filename']
        if digest(_bytes(path, 16_777_216)) != item['sha256'] or digest(lane.read_object(item['sha256'])) != item['sha256']:
            raise LaneError('DOCUMENT_RENDER_INTEGRITY', 'A rendered artifact differs from its immutable byte record.')
    return {'render_id': render_id, **manifest}


def verify_render(context, request, output):
    manifest = read_render(context.store, output.result['render_id'])
    valid = manifest['snapshot_id'] == request.snapshot_id and len(manifest['pages']) == output.result['page_count']
    return [{'check_id': name, 'passed': valid, 'evidence': {'render_id': output.result['render_id'],
        'page_count': len(manifest['pages']), 'visual_review': 'not_inferred'}} for name in context.requested_checks]


def register_render_actions(engine):
    from .document_profile import DocumentRender, DocumentRenderRead, DocumentResult, result
    from .registry import ActionSpec
    from .tool_routes import ToolRoute
    engine.registry.register(ActionSpec('document_render', 'Render exact document bytes to PDF and page PNGs using the verified shared office runtime.',
        DocumentRender, DocumentResult, render_document, permission='write', mutates=True, requires_delta=True,
        profile='document', workflow='build', worker_operations=('document_render',),
        verification_checks=('document_render_bytes_verified',), verifier=verify_render,
        tool_routes=(ToolRoute('document_render.shared_office', render_document, ('Python', 'LibreOffice', 'pypdfium2'), systems=('Windows',)),)))

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return result(store, 'document_render_read', read_render(store, request.render_id))
    engine.registry.register(ActionSpec('document_render_read', 'Read and verify one immutable document-render manifest and its artifacts.',
        DocumentRenderRead, DocumentResult, read, profile='document', workflow='source-intake', queryable_in_delta=True,
        cross_project_read=True, studio_read=True, read_migrations=DOC_MIGRATIONS))
