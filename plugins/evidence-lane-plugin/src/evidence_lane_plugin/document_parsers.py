"""Bounded document facts adapted from the admitted native OpenXML extractor.

Package XML is data only. Relationships are recorded without dereferencing them;
rendering and conversion have separate operation and dependency contracts.
"""
from __future__ import annotations

import hashlib
import io
import posixpath
import re
import stat
import xml.etree.ElementTree as ET
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath

from .hashing import canonical_json_bytes

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
OD = {'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
      'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'draw': 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0',
      'xlink': 'http://www.w3.org/1999/xlink'}
MAX_XML_BYTES = 16_777_216
MAX_ITEMS = 8192
MAX_TEXT_BYTES = 2_097_152
TEXT_EXTENSIONS = {'.txt', '.md', '.rst', '.html', '.htm', '.xml'}
PACKAGE_EXTENSIONS = {'.docx', '.dotx', '.odt'}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def xml_root(content):
    if (len(content) > MAX_XML_BYTES or b'<!DOCTYPE' in content.upper()
            or b'<!ENTITY' in content.upper() or b'\x00' in content):
        raise ValueError('DOCUMENT_XML_UNSAFE_OR_TOO_LARGE')
    root = ET.fromstring(content)
    count, stack = 0, [(root, 0)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 100_000 or depth > 128:
            raise ValueError('DOCUMENT_XML_NODE_BUDGET')
        stack.extend((child, depth + 1) for child in node)
    return root


def package_members(content):
    """Read a finite regular ZIP member set without extracting filesystem paths."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > 4096 or sum(row.file_size for row in members) > 33_554_432:
            raise ValueError('DOCUMENT_PACKAGE_BYTE_BUDGET')
        paths, result = set(), {}
        for member in members:
            path = PurePosixPath(member.filename)
            if (member.orig_filename != member.filename or member.filename in paths or path.is_absolute() or '..' in path.parts
                    or '\\' in member.filename or ':' in member.filename or '\x00' in member.filename
                    or member.flag_bits & 1 or stat.S_ISLNK(member.external_attr >> 16)
                    or member.file_size > MAX_XML_BYTES
                    or member.file_size > max(member.compress_size, 1) * 1000):
                raise ValueError('DOCUMENT_PACKAGE_MEMBER_INVALID')
            paths.add(member.filename)
            if not member.is_dir():
                result[member.filename] = archive.read(member)
    return result


def word_text(element, *, deleted=False):
    values = []
    def walk(node):
        if node.tag == W + 'del' and not deleted:
            return
        if node.tag in {W + 't', W + 'delText'}:
            values.append(node.text or '')
        elif node.tag == W + 'tab':
            values.append('\t')
        elif node.tag in {W + 'br', W + 'cr'}:
            values.append('\n')
        for child in node:
            walk(child)
    walk(element)
    return ''.join(values)


class Facts:
    def __init__(self):
        self.items = []
        self.text_bytes = 0

    def add(self, kind, part, ordinal, text='', **data):
        self.text_bytes += len(text.encode('utf-8'))
        if len(self.items) >= MAX_ITEMS or self.text_bytes > MAX_TEXT_BYTES:
            raise ValueError('DOCUMENT_STRUCTURE_BUDGET')
        self.items.append({'item_id': digest(canonical_json_bytes([part, kind, ordinal])),
            'kind': kind, 'part': part, 'ordinal': ordinal, 'text': text, **data})


def _word(content):
    members, facts = package_members(content), Facts()
    if 'word/document.xml' not in members or '[Content_Types].xml' not in members:
        raise ValueError('DOCUMENT_DOCX_STRUCTURE_INVALID')
    xml_root(members['[Content_Types].xml'])
    parts = ['word/document.xml'] + sorted(name for name in members if re.fullmatch(
        r'word/(header\d+|footer\d+|footnotes|endnotes|comments)\.xml', name))
    features = {'tracked_changes': False, 'content_controls': False, 'fields': False,
                'macros': any(name.lower().endswith('vbaproject.bin') for name in members),
                'external_resources': False, 'embedded_objects': False}
    for part in parts:
        root = xml_root(members[part])
        rel_name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
        rels = {}
        if rel_name in members:
            for ordinal, row in enumerate(xml_root(members[rel_name]).findall(REL + 'Relationship')):
                identity, target, kind = row.get('Id', ''), row.get('Target', ''), row.get('Type', '')
                external = row.get('TargetMode') == 'External'
                resolved = None if external else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
                rels[identity] = {'relationship_id': identity, 'target': target, 'relationship_type': kind,
                    'external': external, 'member': resolved, 'member_present': resolved in members if resolved else None}
                facts.add('relationship', part, ordinal, **rels[identity])
                features['external_resources'] |= external and not kind.endswith('/hyperlink')
        parents = {child: parent for parent in root.iter() for child in parent}
        paragraph_nodes = list(root.iter(W + 'p'))
        for ordinal, paragraph in enumerate(paragraph_nodes):
            ancestor, in_table, note_id = paragraph, False, None
            while ancestor in parents:
                ancestor = parents[ancestor]
                in_table |= ancestor.tag == W + 'tc'
                if ancestor.tag in {W + 'footnote', W + 'endnote', W + 'comment'}:
                    note_id = ancestor.get(W + 'id')
            style_node = paragraph.find(W + 'pPr/' + W + 'pStyle')
            style = style_node.get(W + 'val', '') if style_node is not None else ''
            match = re.fullmatch(r'heading\s*([1-9])', style, re.IGNORECASE)
            text = word_text(paragraph)
            facts.add('paragraph', part, ordinal, text, style=style, heading_level=int(match[1]) if match else None,
                      in_table=in_table, note_id=note_id, locator={'paragraph_index': ordinal})
            if match:
                facts.add('heading', part, ordinal, text, level=int(match[1]), paragraph_index=ordinal)
        for ordinal, table in enumerate(root.iter(W + 'tbl')):
            rows = [[word_text(cell) for cell in row.findall(W + 'tc')] for row in table.findall(W + 'tr')]
            if sum(len(row) for row in rows) > 4096:
                raise ValueError('DOCUMENT_TABLE_CELL_BUDGET')
            facts.add('table', part, ordinal, '\n'.join('\t'.join(row) for row in rows), rows=rows,
                merged_cells=any(node.tag in {W + 'vMerge', W + 'gridSpan'} for node in table.iter()),
                nested_tables=any(len(list(cell.iter(W + 'tbl'))) for cell in table.iter(W + 'tc')))
        for kind, tag in (('content_control', 'sdt'), ('insertion', 'ins'), ('deletion', 'del'),
                          ('field', 'fldSimple'), ('field_instruction', 'instrText'), ('hyperlink', 'hyperlink'),
                          ('bookmark', 'bookmarkStart'), ('image', 'drawing'), ('embedded_object', 'object')):
            for ordinal, node in enumerate(root.iter(W + tag)):
                value = ''.join(node.itertext()) if kind == 'field_instruction' else word_text(node, deleted=True)
                data = {'attributes': dict(node.attrib)}
                if kind == 'content_control':
                    meta = node.find(W + 'sdtPr')
                    data['properties'] = [{child.tag.rsplit('}', 1)[-1]: dict(child.attrib)} for child in meta] if meta is not None else []
                if kind in {'image', 'hyperlink'}:
                    identities = sorted({value for child in node.iter() for key, value in child.attrib.items()
                                         if key in {R + 'embed', R + 'link', R + 'id'}})
                    data['relationships'] = [rels.get(identity, {'relationship_id': identity, 'missing': True}) for identity in identities]
                facts.add(kind, part, ordinal, value, **data)
                features['tracked_changes'] |= kind in {'insertion', 'deletion'}
                features['content_controls'] |= kind == 'content_control'
                features['fields'] |= kind in {'field', 'field_instruction'}
                features['embedded_objects'] |= kind == 'embedded_object'
    return facts.items, features, ['Document order is part-local; pages require separate rendering.',
        'Paragraph text excludes tracked deletions and includes insertions; both revisions are indexed separately.',
        'Tables retain logical cells and merge markers; visual cell geometry is not inferred.',
        'Image relationships and field instructions are locators, never executed or downloaded.']


def odt_text(element):
    """ODF whitespace is encoded as elements, including repeated spaces."""
    values, used = [], 0
    def append(value):
        nonlocal used
        used += len(value.encode('utf-8'))
        if used > MAX_TEXT_BYTES:
            raise ValueError('DOCUMENT_STRUCTURE_BUDGET')
        values.append(value)
    def walk(node):
        append(node.text or '')
        for child in node:
            if child.tag == '{' + OD['text'] + '}s':
                count = int(child.get('{' + OD['text'] + '}c', '1'))
                if not 1 <= count <= MAX_TEXT_BYTES - used:
                    raise ValueError('DOCUMENT_STRUCTURE_BUDGET')
                append(' ' * count)
            elif child.tag == '{' + OD['text'] + '}tab':
                append('\t')
            elif child.tag == '{' + OD['text'] + '}line-break':
                append('\n')
            else:
                walk(child)
            append(child.tail or '')
    walk(element)
    return ''.join(values)


def _odt(content):
    members, facts = package_members(content), Facts()
    if members.get('mimetype', b'').strip() != b'application/vnd.oasis.opendocument.text' or 'content.xml' not in members:
        raise ValueError('DOCUMENT_ODT_STRUCTURE_INVALID')
    root = xml_root(members['content.xml'])
    paragraph_index = 0
    for node in root.iter():
        local = node.tag.rsplit('}', 1)[-1]
        if node.tag in {'{' + OD['text'] + '}p', '{' + OD['text'] + '}h'}:
            level = node.get('{' + OD['text'] + '}outline-level')
            value = odt_text(node)
            facts.add('paragraph', 'content.xml', paragraph_index, value, style=node.get('{' + OD['text'] + '}style-name', ''),
                heading_level=int(level) if level and level.isdigit() else None, locator={'paragraph_index': paragraph_index})
            if local == 'h':
                facts.add('heading', 'content.xml', paragraph_index, value, level=int(level) if level and level.isdigit() else 1)
            paragraph_index += 1
    for ordinal, table in enumerate(root.findall('.//table:table', OD)):
        rows = []
        for row in table.findall('table:table-row', OD):
            cells = []
            for cell in row:
                repeat = int(cell.get('{' + OD['table'] + '}number-columns-repeated', '1'))
                if repeat < 1 or repeat > 256 or len(cells) + repeat > 256:
                    raise ValueError('DOCUMENT_TABLE_CELL_BUDGET')
                paragraphs = [odt_text(node) for node in cell.iter() if node.tag in {
                    '{' + OD['text'] + '}p', '{' + OD['text'] + '}h'}]
                cells.extend(['\n'.join(paragraphs)] * repeat)
            repeat = int(row.get('{' + OD['table'] + '}number-rows-repeated', '1'))
            if repeat < 1 or repeat > 256 or len(rows) + repeat > 256:
                raise ValueError('DOCUMENT_TABLE_CELL_BUDGET')
            rows.extend([list(cells) for _ in range(repeat)])
        if sum(map(len, rows)) > 4096:
            raise ValueError('DOCUMENT_TABLE_CELL_BUDGET')
        facts.add('table', 'content.xml', ordinal, '\n'.join('\t'.join(row) for row in rows), rows=rows)
    return facts.items, {'external_resources': any(node.get('{' + OD['xlink'] + '}href', '').startswith(
        ('http:', 'https:', 'file:', 'ftp:')) for node in root.iter()),
        'macros': any(name.startswith(('Basic/', 'Scripts/')) for name in members)}, [
        'ODT text, headings and bounded repeated cells are extracted; style and page-layout equivalence is not claimed.']


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values, self.suppressed, self.depth = [], 0, 0

    def handle_starttag(self, tag, attrs):
        self.depth += int(tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'})
        if self.depth > 128:
            raise ValueError('DOCUMENT_HTML_DEPTH_BUDGET')
        if tag in {'script', 'style'}:
            self.suppressed += 1
        if tag in {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.values.append('\n')

    def handle_endtag(self, tag):
        self.depth = max(0, self.depth - 1)
        if tag in {'script', 'style'}:
            self.suppressed = max(0, self.suppressed - 1)

    def handle_data(self, data):
        if not self.suppressed:
            self.values.append(data)


def _text(content, extension):
    text = content.decode('utf-8-sig')
    if '\x00' in text:
        raise ValueError('DOCUMENT_TEXT_ENCODING_UNSUPPORTED')
    if extension in {'.html', '.htm'}:
        parser = _HTML()
        parser.feed(text)
        text = ''.join(parser.values)
    elif extension == '.xml':
        text = '\n'.join(value.strip() for value in xml_root(content).itertext() if value.strip())
    facts = Facts()
    for ordinal, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        match = re.match(r'^(#{1,6})\s+(.+)', line) if extension == '.md' else None
        value = match[2] if match else line
        facts.add('paragraph', 'text', ordinal, value, heading_level=len(match[1]) if match else None,
                  locator={'line': ordinal + 1})
        if match:
            facts.add('heading', 'text', ordinal, value, level=len(match[1]))
    return facts.items, {}, ['UTF-8 textual extraction; HTML scripts/styles are ignored and resources are never fetched.',
                             'Text syntax extraction is not a browser or page-layout rendering.']


def parse_document(filename, content):
    extension = PurePosixPath(filename).suffix.lower()
    if len(content) > 8_388_608:
        raise ValueError('DOCUMENT_FILE_BYTE_BUDGET')
    if extension in {'.docx', '.dotx'}:
        items, features, limitations = _word(content)
        parser = 'DOCX_OpenXML'
    elif extension == '.odt':
        items, features, limitations = _odt(content)
        parser = 'ODT_XML'
    elif extension in TEXT_EXTENSIONS:
        items, features, limitations = _text(content, extension)
        parser = 'bounded_document_text'
    else:
        raise ValueError('DOCUMENT_FORMAT_REQUIRES_EXPLICIT_CONVERSION')
    body = {'schema': 'evidence-lane.document-facts.v4', 'parser': parser, 'extension': extension,
        'items': items, 'features': features, 'limitations': limitations,
        'fidelity': {'structure': 'bounded_native_facts', 'layout': 'not_rendered',
                     'page_count': None, 'resources_fetched': False, 'macros_executed': False}}
    if len(canonical_json_bytes(body)) > 8_388_608:
        raise ValueError('DOCUMENT_FACT_BYTE_BUDGET')
    return body
