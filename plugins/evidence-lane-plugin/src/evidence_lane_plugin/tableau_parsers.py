"""Closed-input Tableau XML/package extraction; connection text never grants I/O."""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import zipfile
from collections import Counter
from pathlib import PurePosixPath

from .errors import LaneError
from .hashing import canonical_json_bytes

EXTENSIONS = {'.twb', '.twbx', '.tds', '.tdsx', '.hyper', '.tde'}
MAX_BYTES = 8_388_608
KINDS = ('workbook', 'datasource', 'sheet', 'dashboard', 'story', 'column', 'calculation',
    'relationship', 'connection', 'filter', 'parameter', 'mark', 'layout', 'package_member',
    'hyper_schema', 'hyper_table', 'hyper_column', 'hyper_row', 'opaque')
TAGS = {'workbook': 'workbook', 'datasource': 'datasource', 'worksheet': 'sheet', 'dashboard': 'dashboard',
    'story': 'story', 'column': 'column', 'calculation': 'calculation', 'relation': 'relationship',
    'relationship': 'relationship', 'datasource-dependencies': 'relationship', 'connection': 'connection',
    'filter': 'filter', 'groupfilter': 'filter', 'parameter': 'parameter', 'mark': 'mark', 'zone': 'layout'}
PRIVATE_ATTRIBUTE = re.compile(r'password|passwd|token|secret|credential|oauth', re.IGNORECASE)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def fail(code):
    raise LaneError('TABLEAU_' + code, 'The selected Tableau file does not satisfy the bounded format contract.')


def member_name(value):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in value or '\x00' in value or str(path) == '.':
        fail('PACKAGE_PATH_INVALID')
    if len(value) > 1000:
        fail('PACKAGE_PATH_INVALID')
    return path.as_posix()


def package_members(content):
    result, seen, total = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 256:
                fail('PACKAGE_MEMBER_BUDGET')
            for info in archive.infolist():
                name = member_name(info.filename)
                if name.casefold() in seen or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                    fail('PACKAGE_MEMBER_INVALID')
                seen.add(name.casefold())
                if info.is_dir():
                    continue
                total += info.file_size
                if total > 16_777_216 or info.file_size > MAX_BYTES:
                    fail('PACKAGE_BYTE_BUDGET')
                with archive.open(info) as stream:
                    raw = stream.read(MAX_BYTES + 1)
                if len(raw) != info.file_size or len(raw) > MAX_BYTES:
                    fail('PACKAGE_BYTE_BUDGET')
                result[name] = raw
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError):
        fail('PACKAGE_INVALID')
    return result


def xml_root(content):
    from lxml import etree
    if len(content) > MAX_BYTES:
        fail('XML_BYTE_BUDGET')
    try:
        root = etree.fromstring(content, etree.XMLParser(resolve_entities=False, load_dtd=False,
            no_network=True, huge_tree=False, remove_blank_text=False))
    except etree.XMLSyntaxError:
        fail('XML_INVALID')
    if root.getroottree().docinfo.doctype or any(isinstance(node, etree._Entity) for node in root.iter()):
        fail('XML_ACTIVE_CONTENT')
    if sum(1 for _ in root.iter()) > 50_000:
        fail('XML_NODE_BUDGET')
    return root


def xml_items(part, content):
    from lxml import etree
    root = xml_root(content)
    if etree.QName(root).localname not in {'workbook', 'datasource'}:
        fail('XML_ROOT_INVALID')
    if (PurePosixPath(part).suffix.lower() == '.twb') != (etree.QName(root).localname == 'workbook'):
        fail('XML_FORMAT_MISMATCH')
    result, counts, tree = [], Counter(), root.getroottree()
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node).localname
        kind = TAGS.get(tag)
        if kind is None:
            continue
        ordinal = counts[kind]
        counts[kind] += 1
        path = tree.getpath(node)
        attrs = {key: '[redacted]' if PRIVATE_ATTRIBUTE.search(key) else value for key, value in sorted(node.attrib.items())}
        # Only direct text belongs to this node. Descendant text remains at its own locator.
        direct = (node.text or '').strip()
        if len(direct) > 32768 or any(len(value) > 32768 for value in attrs.values()):
            fail('XML_VALUE_BUDGET')
        text = ' '.join([tag, *[key + '=' + value for key, value in attrs.items()], direct]).strip()
        result.append({'item_id': digest(canonical_json_bytes([part, path, kind])), 'kind': kind,
            'part': part, 'ordinal': ordinal, 'xml_path': path, 'tag': tag, 'attributes': attrs,
            'text': text, 'direct_text': direct, 'name': attrs.get('name'), 'caption': attrs.get('caption'),
            'parent_xml_path': tree.getpath(node.getparent()) if node.getparent() is not None else None})
    return result


def parse_tableau(filename, content, *, inspect_extracts=True, max_rows_per_table=100):
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in EXTENSIONS:
        fail('FORMAT_UNSUPPORTED')
    if len(content) > MAX_BYTES or not 0 <= max_rows_per_table <= 1000:
        fail('INPUT_BUDGET')
    packaged = extension in {'.twbx', '.tdsx'}
    members = package_members(content) if packaged else {PurePosixPath(filename).name: content}
    expected = '.twb' if extension == '.twbx' else '.tds'
    if packaged and len([name for name in members if PurePosixPath(name).suffix.lower() == expected]) != 1:
        fail('PACKAGE_DOCUMENT_AMBIGUOUS')
    items, extracts, limitations = [], [], ['Live connections and external files are recorded without being opened.',
        'Workbook calculations, marks and layout are metadata; no Tableau rendering or calculation execution is implied.']
    for part, raw in members.items():
        suffix = PurePosixPath(part).suffix.lower()
        if packaged:
            items.append({'item_id': digest(canonical_json_bytes(['member', part])), 'kind': 'package_member',
                'part': part, 'ordinal': len(items), 'text': part, 'name': part, 'sha256': digest(raw), 'bytes': len(raw)})
        if suffix in {'.twb', '.tds'}:
            items.extend(xml_items(part, raw))
        elif suffix == '.hyper':
            if inspect_extracts:
                extracts.append((part, raw))
            else:
                limitations.append('Hyper contents were explicitly not inspected.')
        elif suffix == '.tde':
            items.append({'item_id': digest(canonical_json_bytes(['tde', part])), 'kind': 'opaque', 'part': part,
                'ordinal': len(items), 'text': part + ' legacy TDE bytes only', 'sha256': digest(raw), 'bytes': len(raw)})
            limitations.append('Legacy TDE is retained as exact opaque bytes; use a compatible vendor tool to migrate it to Hyper.')
    hyper_evidence = None
    if extracts:
        from .tableau_hyper import inspect_hyper
        native, hyper_evidence = inspect_hyper(extracts, max_rows_per_table=max_rows_per_table)
        for entry in native:
            part = entry['name']
            for kind, rows in entry['collections'].items():
                for ordinal, row in enumerate(rows):
                    text = json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)
                    items.append({'item_id': digest(canonical_json_bytes([part, kind, ordinal])), 'kind': kind,
                        'part': part, 'ordinal': ordinal, 'text': text, **row})
    if len(items) > 50_000:
        fail('FACT_ITEM_BUDGET')
    body = {'schema': 'evidence-lane.tableau-facts.v4', 'lane_id': 'tableau', 'format': extension, 'items': items,
        'counts': dict(sorted(Counter(item['kind'] for item in items).items())),
        'features': {'package': packaged, 'hyper_files_inspected': len(extracts)},
        'parse_options': {'inspect_extracts': inspect_extracts, 'max_rows_per_table': max_rows_per_table},
        'fidelity': {'xml_metadata': extension in {'.twb', '.tds', '.twbx', '.tdsx'},
            'layout_verified': False, 'tableau_semantic_validation': False, 'live_data_read': False,
            'hyper_native_read': bool(extracts), 'hyper_rows': 'bounded_sample_not_complete_dataset', 'exact_source_bytes': True},
        'limitations': sorted(set(limitations)), 'hyper_evidence': hyper_evidence}
    if len(canonical_json_bytes(body)) > 16_777_216:
        fail('FACT_BYTE_BUDGET')
    return body
