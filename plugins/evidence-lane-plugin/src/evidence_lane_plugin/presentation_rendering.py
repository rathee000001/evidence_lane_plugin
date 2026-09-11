"""Exact presentation-to-PDF rendering with a verified shared Windows office runtime.

LibreOffice is a separate owned process. Every conversion uses a private profile;
macros, embedded executable objects and external resource loading are rejected.
"""
from __future__ import annotations

import base64
import io
import json
import tempfile
from pathlib import Path

from .document_rendering import _convert, _package_resource
from .errors import LaneError
from .hashing import canonical_json_bytes
from .presentation_parsers import digest, package_members, xml_root
from .presentation_schema import PPT_MIGRATIONS
from .storage import now, project_snapshot


def safe_render_package(content, extension):
    from .presentation_parsers import parse_presentation
    if extension not in {'.pptx', '.potx', '.ppsx'}:
        raise LaneError('PRESENTATION_RENDER_FORMAT_UNSUPPORTED', 'Select a supported macro-free presentation package.')
    facts = parse_presentation('presentation' + extension, content)
    if facts['features']['macros'] or facts['features']['embedded_objects']:
        raise LaneError('PRESENTATION_RENDER_ACTIVE_CONTENT', 'Rendering excludes macros, embedded objects and executable controls.')
    members = package_members(content)
    for name, raw in members.items():
        folded = name.casefold()
        if '/embeddings/' in folded or '/activex/' in folded or folded.endswith('vbaproject.bin'):
            raise LaneError('PRESENTATION_RENDER_ACTIVE_CONTENT', 'Rendering excludes embedded executables and macro parts.')
        if not name.endswith(('.xml', '.rels')):
            continue
        root = xml_root(raw)
        for node in root.iter():
            if node.tag.rsplit('}', 1)[-1] in {'oleObj', 'control', 'audioFile', 'videoFile', 'media', 'externalData'}:
                raise LaneError('PRESENTATION_RENDER_ACTIVE_CONTENT', 'Playback, embedded chart workbooks and external data need separate contracts.')
            if node.tag.endswith('}Relationship'):
                kind = node.get('Type', '')
                if node.get('TargetMode') == 'External':
                    if not kind.endswith('/hyperlink'):
                        raise LaneError('PRESENTATION_RENDER_EXTERNAL_RESOURCE', 'Rendering requires embedded package resources.')
                    continue
                base = name.rsplit('/_rels/', 1)[0] if '/_rels/' in name else ''
                _package_resource(node.get('Target', ''), base, members)
            if node.get('action', '').startswith(('ppaction://program', 'ppaction://macro', 'ppaction://ole')):
                raise LaneError('PRESENTATION_RENDER_ACTIVE_CONTENT', 'Rendering excludes program and macro actions.')
    return members


def render(arguments):
    import pypdfium2 as pdfium
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise LaneError('PRESENTATION_RENDER_INPUT_CHANGED', 'The render bytes differ from the requested snapshot.')
    extension = Path(arguments['logical_name']).suffix.lower()
    safe_render_package(content, extension)
    with tempfile.TemporaryDirectory(prefix='evidence-lane-presentation-render-') as temporary_name:
        temporary = Path(temporary_name)
        source = temporary / ('presentation' + extension)
        source.write_bytes(content)
        pdf_path, evidence = _convert(source, temporary, timeout_seconds=arguments['timeout_seconds'], family='impress')
        if pdf_path.stat().st_size > arguments['max_output_bytes']:
            raise LaneError('PRESENTATION_RENDER_OUTPUT_BUDGET', 'The rendered PDF exceeds its requested output budget.')
        pdf = pdf_path.read_bytes()
        files = [{'filename': 'presentation.pdf', 'role': 'pdf', 'content_base64': base64.b64encode(pdf).decode(),
                  'sha256': digest(pdf), 'bytes': len(pdf)}]
        used, pages = len(pdf), []
        presentation = pdfium.PdfDocument(pdf)
        try:
            if not 1 <= len(presentation) <= arguments['max_pages']:
                raise LaneError('PRESENTATION_RENDER_PAGE_BUDGET', 'Select a presentation within the requested page budget.')
            for index in range(len(presentation)):
                page = presentation[index]
                bitmap, text_page = None, None
                try:
                    width, height = page.get_size()
                    if width * height * (arguments['dpi'] / 72) ** 2 > 20_000_000:
                        raise LaneError('PRESENTATION_RENDER_PIXEL_BUDGET', 'A presentation page exceeds its finite pixel budget.')
                    bitmap = page.render(scale=arguments['dpi'] / 72)
                    image = bitmap.to_pil()
                    output = io.BytesIO()
                    image.save(output, format='PNG')
                    png = output.getvalue()
                    image.close()
                    used += len(png)
                    if used > arguments['max_output_bytes']:
                        raise LaneError('PRESENTATION_RENDER_OUTPUT_BUDGET', 'Rendered pages exceed the selected output byte budget.')
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
            presentation.close()
        if source.read_bytes() != content:
            raise LaneError('PRESENTATION_RENDER_SOURCE_CHANGED', 'The converter changed its private source copy.')
    return {'input_sha256': arguments['expected_sha256'], 'files': files, 'pages': pages,
        'evidence': {**evidence, 'raster_engine': 'pypdfium2', 'dpi': arguments['dpi'],
            'source_bytes_mutated': False, 'visual_review': 'required_not_performed_by_renderer',
            'powerpoint_layout_equivalence': False, 'font_substitution': 'possible',
            'animations_and_media_playback': 'not_rendered_as_interactive_content',
            'hidden_slide_behavior': 'converter_default'}}


def render_presentation(context, request):
    from .presentation_profile import _calls, _natural, read_snapshot, result
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    lane = store.lane('ppt')
    content = lane.read_object(manifest['raw_object'])
    response = execution.submit('presentation_render', {**request.model_dump(mode='json'),
        'logical_name': manifest['logical_name'], 'expected_sha256': manifest['raw_object'],
        'content_base64': base64.b64encode(content).decode()}).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'PRESENTATION_RENDER_FAILED'), 'The presentation render worker did not complete.')
    generated = json.loads(json.dumps(response['result']))
    if generated['input_sha256'] != manifest['raw_object']:
        raise LaneError('PRESENTATION_RENDER_BINDING', 'The render worker returned another source binding.')
    contents = {}
    for row in generated['files']:
        raw = base64.b64decode(row.pop('content_base64'), validate=True)
        if digest(raw) != row['sha256'] or len(raw) != row['bytes']:
            raise LaneError('PRESENTATION_RENDER_BINDING', 'The rendered artifact failed byte verification.')
        contents[row['filename']] = raw
    expected_names = {'presentation.pdf', *(f'page-{i + 1}.png' for i in range(len(generated['pages'])))}
    if (set(contents) != expected_names or len(contents) != len(generated['files'])
            or not 1 <= len(generated['pages']) <= request.max_pages
            or sum(map(len, contents.values())) > request.max_output_bytes):
        raise LaneError('PRESENTATION_RENDER_BINDING', 'The render worker returned a different bounded page set.')
    body = {'schema': 'evidence-lane.presentation-render.v4', 'project_id': store.project_id, 'lane_id': 'ppt',
        'snapshot_id': request.snapshot_id, **generated, 'created_at': now()}
    render_id = digest(canonical_json_bytes(body))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['ppt', 'receipts']):  # noqa: SIM117 - coordination must precede the lane connection
        with lane.transaction() as connection:
            for name, raw in contents.items():
                lane.put_object(raw)
                _natural(lane, render_id, name, raw)
            manifest_object = lane.put_object(canonical_json_bytes(body))
            connection.execute('INSERT INTO ppt_render VALUES(?,?,?,?)', (render_id, request.snapshot_id, manifest_object, body['created_at']))
            store.append_receipt('presentation_render', {'render_id': render_id, 'snapshot_id': request.snapshot_id,
                'page_count': len(generated['pages']), 'source_bytes_mutated': False})
    return result(store, 'presentation_render', {'render_id': render_id, 'snapshot_id': request.snapshot_id,
        'page_count': len(generated['pages']), 'files': [{**row, 'path': str(lane.files / 'natural' / render_id / row['filename'])}
            for row in generated['files']], 'evidence': generated['evidence'], 'source_bytes_mutated': False})


def read_render(store, render_id):
    from .presentation_profile import _bytes, _relative
    lane = store.lane('ppt')
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT * FROM ppt_render WHERE render_id=?', (render_id,)).fetchone()
    if row is None:
        raise LaneError('PRESENTATION_RENDER_MISSING', 'Select an exact presentation rendering from this PPT lane.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(manifest)) != render_id or manifest['snapshot_id'] != row['snapshot_id']
            or manifest['project_id'] != store.project_id or manifest['lane_id'] != 'ppt'):
        raise LaneError('PRESENTATION_RENDER_INTEGRITY', 'The render manifest differs from its indexed identity.')
    for item in manifest['files']:
        if '/' in _relative(item['filename']):
            raise LaneError('PRESENTATION_RENDER_INTEGRITY', 'A render artifact name points outside its exact version.')
        path = lane.files / 'natural' / render_id / item['filename']
        if digest(_bytes(path, 16_777_216)) != item['sha256'] or digest(lane.read_object(item['sha256'])) != item['sha256']:
            raise LaneError('PRESENTATION_RENDER_INTEGRITY', 'A rendered artifact differs from its immutable byte record.')
    return {'render_id': render_id, **manifest}


def verify_render(context, request, output):
    manifest = read_render(context.store, output.result['render_id'])
    valid = manifest['snapshot_id'] == request.snapshot_id and len(manifest['pages']) == output.result['page_count']
    return [{'check_id': name, 'passed': valid, 'evidence': {'render_id': output.result['render_id'],
        'page_count': len(manifest['pages']), 'visual_review': 'not_inferred'}} for name in context.requested_checks]


def register_render_actions(engine):
    from .presentation_contracts import PresentationRender, PresentationRenderRead
    from .presentation_profile import PresentationResult, result
    from .registry import ActionSpec
    from .tool_routes import ToolRoute
    engine.registry.register(ActionSpec('presentation_render', 'Render exact presentation bytes to PDF and page PNGs using the verified shared office runtime.',
        PresentationRender, PresentationResult, render_presentation, permission='write', mutates=True, requires_delta=True,
        profile='presentation', workflow='execute-project-plan', worker_operations=('presentation_render',),
        verification_checks=('presentation_render_bytes_verified',), verifier=verify_render,
        tool_routes=(ToolRoute('presentation_render.shared_office', render_presentation, ('Python', 'LibreOffice', 'pypdfium2'), systems=('Windows',)),)))

    def read(context, request):
        store = engine.directory.open(context.project_id)
        with project_snapshot(store.root):
            return result(store, 'presentation_render_read', read_render(store, request.render_id))
    engine.registry.register(ActionSpec('presentation_render_read', 'Read and verify one immutable presentation-render manifest and its artifacts.',
        PresentationRenderRead, PresentationResult, read, profile='presentation', workflow='manage-project-sources', queryable_in_delta=True,
        cross_project_read=True, studio_read=True, read_migrations=PPT_MIGRATIONS))
