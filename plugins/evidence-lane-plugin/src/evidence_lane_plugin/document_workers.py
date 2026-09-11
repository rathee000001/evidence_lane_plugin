"""Pure bounded document parsing and authoring in owned OS tool workers."""
from __future__ import annotations

import base64
import copy
import io
import re
import zipfile
from pathlib import Path

from .document_parsers import W, digest, package_members, parse_document, word_text, xml_root
from .storage import reject_links


def encoded_document(filename, content, *, evidence=None):
    facts = parse_document(filename, content)
    return {'filename': filename, 'sha256': digest(content), 'bytes': len(content),
        'content_base64': base64.b64encode(content).decode('ascii'), 'facts': facts,
        'evidence': evidence or {'operation': 'native_extraction', 'source_bytes_mutated': False}}


def parse_file(arguments):
    path = Path(arguments['filename'])
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open('rb') as stream:
        content = stream.read(arguments['max_file_bytes'] + 1)
    after = path.stat()
    if len(content) > arguments['max_file_bytes']:
        raise ValueError('DOCUMENT_FILE_BYTE_BUDGET')
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('DOCUMENT_SOURCE_CHANGED')
    return encoded_document(arguments['logical_name'], content)


def parse_content(arguments):
    limit = min(arguments['max_file_bytes'], 8_388_608)
    encoded = arguments['content_base64']
    if len(encoded) > 4 * ((limit + 2) // 3):
        raise ValueError('DOCUMENT_FILE_BYTE_BUDGET')
    content = base64.b64decode(encoded, validate=True)
    if len(content) > limit:
        raise ValueError('DOCUMENT_FILE_BYTE_BUDGET')
    return encoded_document(arguments['logical_name'], content)


def generate(arguments):
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Inches, Pt, RGBColor
    document = Document()
    document.core_properties.title = arguments['title']
    document.core_properties.author = ''
    document.core_properties.last_modified_by = ''
    normal = document.styles['Normal']
    normal.font.name, normal.font.size = 'Arial', Pt(11)
    normal.paragraph_format.space_after = Pt(7)
    for style_name in ('Title', 'Heading 1', 'Heading 2', 'Heading 3', 'Heading 4', 'Heading 5', 'Heading 6'):
        style = document.styles[style_name]
        style.font.name, style.font.color.rgb, style.font.underline = 'Arial', RGBColor(0, 0, 0), False
        for border in list(style.element.iter(W + 'pBdr')):
            border.getparent().remove(border)
    for section in document.sections:
        section.top_margin = section.bottom_margin = Inches(.75)
        section.left_margin = section.right_margin = Inches(.85)
    title = document.add_paragraph(arguments['title'], style='Title')
    for border in list(title._p.iter(W + 'pBdr')):
        border.getparent().remove(border)
    for block in arguments['blocks']:
        kind = block['kind']
        if kind == 'table':
            rows = block['rows']
            table = document.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = 'Table Grid'
            for row, values in zip(table.rows, rows, strict=True):
                for cell, value in zip(row.cells, values, strict=True):
                    cell.text = value
            from docx.oxml import OxmlElement
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
        elif kind == 'page_break':
            document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        else:
            style = 'Heading ' + str(block['level']) if kind == 'heading' else 'Normal'
            document.add_paragraph(block['text'], style=style)
    output = io.BytesIO()
    document.save(output)
    return encoded_document(arguments['logical_name'], output.getvalue(), evidence={
        'operation': 'generate', 'engine': 'python-docx', 'source_bytes_mutated': False,
        'layout_verified': False, 'capabilities': ['title', 'heading', 'paragraph', 'rectangular_table', 'page_break']})


def edit(arguments):
    from lxml import etree as xml
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if digest(content) != arguments['expected_sha256']:
        raise ValueError('DOCUMENT_EDIT_INPUT_CHANGED')
    members = package_members(content)
    if 'word/document.xml' not in members:
        raise ValueError('DOCUMENT_EDIT_REQUIRES_DOCX')
    changes, roots, selected = [], {}, set()
    for replacement in arguments['replacements']:
        part, ordinal = replacement['part'], replacement['paragraph_index']
        if (part, ordinal) in selected:
            raise ValueError('DOCUMENT_EDIT_DUPLICATE_TARGET')
        selected.add((part, ordinal))
        if part not in members or not (part == 'word/document.xml' or part.startswith(('word/header', 'word/footer'))):
            raise ValueError('DOCUMENT_EDIT_PART_UNSUPPORTED')
        if part not in roots:
            xml_root(members[part])
            roots[part] = xml.fromstring(members[part], xml.XMLParser(resolve_entities=False, load_dtd=False,
                no_network=True, huge_tree=False, remove_blank_text=False))
        root = roots[part]
        paragraphs = list(root.iter(W + 'p'))
        if ordinal >= len(paragraphs):
            raise ValueError('DOCUMENT_EDIT_TARGET_MISSING')
        paragraph = paragraphs[ordinal]
        if word_text(paragraph) != replacement['expected_text']:
            raise ValueError('DOCUMENT_EDIT_TEXT_CHANGED')
        # Exact paragraph edits preserve all package members and run properties.
        # Complex fields, links, drawings and existing revisions need dedicated
        # contracts; never silently flatten them into plain text.
        allowed = {W + name for name in ('p', 'pPr', 'r', 'rPr', 't', 'tab', 'br', 'cr')}
        for child in paragraph:
            if child.tag == W + 'pPr':
                continue
            if child.tag != W + 'r':
                raise ValueError('DOCUMENT_EDIT_COMPLEX_PARAGRAPH')
            if any(node.tag not in allowed for node in child if node.tag != W + 'rPr'):
                raise ValueError('DOCUMENT_EDIT_COMPLEX_PARAGRAPH')
        runs = [node for node in paragraph if node.tag == W + 'r']
        first_properties = copy.deepcopy(runs[0].find(W + 'rPr')) if runs else None
        for run in runs:
            paragraph.remove(run)
        if replacement['tracked']:
            revision_id = max((int(node.get(W + 'id')) for node in root.iter()
                if node.tag in {W + 'ins', W + 'del'} and (node.get(W + 'id') or '').isdigit()), default=0) + 1
            deletion = xml.SubElement(paragraph, W + 'del', {W + 'id': str(revision_id),
                W + 'author': replacement['author'], W + 'date': arguments['timestamp']})
            for run in runs:
                for text in run.iter(W + 't'):
                    text.tag = W + 'delText'
                deletion.append(run)
            parent = xml.SubElement(paragraph, W + 'ins', {W + 'id': str(revision_id + 1),
                W + 'author': replacement['author'], W + 'date': arguments['timestamp']})
        else:
            parent = paragraph
        run = xml.SubElement(parent, W + 'r')
        if first_properties is not None:
            run.append(first_properties)
        # Word represents tabs and line breaks as elements; literal control
        # characters inside w:t do not reliably render as edited text.
        for text in re.split(r'(\t|\n)', replacement['replacement_text'].replace('\r\n', '\n').replace('\r', '\n')):
            if text in {'\t', '\n'}:
                xml.SubElement(run, W + ('tab' if text == '\t' else 'br'))
            elif text:
                xml.SubElement(run, W + 't', {'{http://www.w3.org/XML/1998/namespace}space': 'preserve'}).text = text
        changes.append({'part': part, 'paragraph_index': ordinal, 'tracked': replacement['tracked']})
    for part, root in roots.items():
        members[part] = xml.tostring(root, encoding='utf-8', xml_declaration=True)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as original, zipfile.ZipFile(output, 'w') as edited:
        for info in original.infolist():
            edited.writestr(info, members.get(info.filename, b''))
    result = output.getvalue()
    after = package_members(result)
    unchanged = all(value == after[name] for name, value in package_members(content).items() if name not in roots)
    if not unchanged:
        raise ValueError('DOCUMENT_EDIT_UNTOUCHED_MEMBER_CHANGED')
    return encoded_document(arguments['logical_name'], result, evidence={
        'operation': 'edit', 'changed_parts': sorted(roots), 'changes': changes,
        'untouched_members_byte_identical': True, 'source_bytes_mutated': False,
        'run_fidelity': 'replacement_uses_first_run_properties', 'layout_verified': False})


def document_worker_operations():
    from .workers import WorkerOperation
    return (WorkerOperation('document_parse_file', __name__, 'parse_file', path_fields=('filename',), max_output_bytes=25_165_824),
        WorkerOperation('document_parse_content', __name__, 'parse_content', path_fields=('filename',),
            max_input_bytes=16_777_216, max_output_bytes=25_165_824),
        WorkerOperation('document_generate', __name__, 'generate', dependencies=('docx',)),
        WorkerOperation('document_edit', __name__, 'edit', dependencies=('lxml',), max_output_bytes=25_165_824),
        WorkerOperation('document_enrich', 'evidence_lane_plugin.document_enrichment', 'enrichment_worker', dependencies=('docling',)),
        WorkerOperation('document_convert', 'evidence_lane_plugin.document_conversion', 'conversion_worker',
            dependencies=('olefile',), path_fields=('filename',), max_output_bytes=33_554_432),
        WorkerOperation('document_render', 'evidence_lane_plugin.document_rendering', 'render', dependencies=('pypdfium2',),
            max_output_bytes=25_165_824, error_codes=(
                'DOCUMENT_RENDER_FORMAT_UNSUPPORTED', 'DOCUMENT_RENDER_ACTIVE_CONTENT', 'DOCUMENT_RENDER_EXTERNAL_RESOURCE',
                'DOCUMENT_RENDER_ACTIVE_FIELD', 'DOCUMENT_RENDER_RUNTIME_MISMATCH', 'DOCUMENT_RENDER_OWNER_UNAVAILABLE',
                'DOCUMENT_RENDER_TIMEOUT', 'DOCUMENT_RENDER_LOG_BUDGET', 'DOCUMENT_RENDER_CONVERTER_FAILED',
                'DOCUMENT_RENDER_OUTPUT_MISSING', 'DOCUMENT_RENDER_RUNTIME_CHANGED', 'DOCUMENT_RENDER_INPUT_CHANGED',
                'DOCUMENT_RENDER_OUTPUT_BUDGET', 'DOCUMENT_RENDER_PAGE_BUDGET', 'DOCUMENT_RENDER_PIXEL_BUDGET',
                'DOCUMENT_RENDER_SOURCE_CHANGED')))
