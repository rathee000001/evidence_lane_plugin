"""Adaptation of the retained Research, Artifacts and Custom extractors.

Byte-only parsing; project selection, lane publication and worker ownership stay
with the current engine. Source labels retain their unvalidated provenance.
"""
from __future__ import annotations

import io
import re
import stat
import zipfile
from pathlib import PurePosixPath

from .document_parsers import digest
from .hashing import canonical_json_bytes
from .lanes import lane_family

MAX_INPUT_BYTES = 8_388_608
MAX_ITEMS = 8192
MAX_FACT_BYTES = 8_388_608
MAX_TEXT_BYTES = 2_097_152
SOURCE_KINDS = {'research': 'research_source', 'artifacts': 'project_artifact', 'custom': 'custom_source'}
ITEM_KINDS = {'research': 'research_evidence', 'artifacts': 'artifact_text_extract', 'custom': 'custom_evidence'}
PREFIX_KINDS = {
    'research': {key: 'research_' + key for key in ('question', 'hypothesis', 'method', 'evidence',
        'finding', 'limitation', 'citation', 'open_question')},
    'custom': {'item': 'custom_item', 'evidence': 'custom_evidence', 'decision': 'custom_decision',
        'next': 'custom_next_action', 'next_action': 'custom_next_action'},
    'artifacts': {},
}


class EvidenceFacts:
    def __init__(self, lane_id, filename, content):
        if lane_family(lane_id) not in SOURCE_KINDS or len(content) > MAX_INPUT_BYTES:
            raise ValueError('SECTOR_EVIDENCE_INPUT_INVALID')
        self.lane_id, self.filename, self.source_sha256 = lane_id, filename, digest(content)
        self.items, self.identities = [], set()
        self.fact_bytes = self.text_bytes = 0
        self.add(SOURCE_KINDS[lane_family(lane_id)], 'source', 0, path=filename,
            extension=PurePosixPath(filename).suffix.lower(), bytes=len(content), sha256=self.source_sha256)

    def add(self, kind, part, ordinal, text='', **payload):
        identity = digest(canonical_json_bytes([self.lane_id, kind, part, ordinal]))
        row = {'item_id': identity, 'kind': kind, 'part': part, 'ordinal': ordinal, 'text': text, **payload}
        self.fact_bytes += len(canonical_json_bytes(row))
        self.text_bytes += len(text.encode('utf-8'))
        if (identity in self.identities or len(self.items) >= MAX_ITEMS
                or self.fact_bytes > MAX_FACT_BYTES or self.text_bytes > MAX_TEXT_BYTES):
            raise ValueError('SECTOR_EVIDENCE_FACT_BUDGET_OR_DUPLICATE')
        self.items.append(row)
        self.identities.add(identity)

    def text(self, part, ordinal, text, **payload):
        if text:
            self.add(ITEM_KINDS[lane_family(self.lane_id)], part, ordinal, text=text,
                text_sha256=digest(text.encode('utf-8')), **payload)

    def labels(self, part, text):
        # Retained deterministic labels are source assertions, never an AI
        # conclusion, a validation result or a Project Truth promotion.
        mapping = PREFIX_KINDS[lane_family(self.lane_id)]
        for index, line in enumerate(text.splitlines()[:2000], 1):
            stripped = re.sub(r'^\s*(?:[-*+]|\d+[.)])\s+', '', line).strip()
            match = re.match(r'^([A-Za-z][A-Za-z0-9 _/-]{0,48}):\s*(.+)$', stripped)
            if not match:
                continue
            key = re.sub(r'[^a-z0-9]+', '_', match[1].lower()).strip('_')
            if key in mapping:
                self.add(mapping[key], part + '/labels', index, text=match[2].strip(),
                    label=match[1], line=index, extractor='deterministic_prefix_v1',
                    assertion_status='source_assertion_unvalidated')

    def finish(self, parser, fidelity, limitations):
        body = {'schema': 'evidence-lane.sector-evidence-facts.v4', 'lane_id': self.lane_id,
            'source_sha256': self.source_sha256, 'parser': parser, 'items': self.items,
            'fidelity': {'exact_input_bytes_preserved': True, 'imported_code_executed': False,
                'source_assertions_validated': False, **fidelity}, 'limitations': limitations}
        if len(canonical_json_bytes(body)) > MAX_FACT_BYTES:
            raise ValueError('SECTOR_EVIDENCE_FACT_BYTE_BUDGET')
        return body


def _text(content):
    text = content.decode('utf-8-sig', errors='strict')
    if '\x00' in text:
        raise ValueError('SECTOR_EVIDENCE_TEXT_NUL')
    return text


def _json(facts, content, extension):
    from .structured_data import load_json
    from .tabular_values import json_value_bytes
    text = _text(content)
    if extension == '.jsonl':
        values = []
        count = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            value = load_json(line)
            count += 1
            if len(values) < 200:
                values.append(value)
    else:
        root = load_json(text)
        values, count = (root[:200], len(root)) if isinstance(root, list) else ([root], 1)
    for ordinal, value in enumerate(values):
        serialized = json_value_bytes(value).decode('utf-8')
        facts.text('json/record', ordinal, serialized, source_locator=f'$[{ordinal}]',
            record_type=type(value).__name__)
        facts.labels(f'json/record/{ordinal}', serialized)
    return {'records_observed': count, 'records_extracted': len(values), 'complete': count == len(values)}, [
        'At most 200 JSON records are indexed; exact source bytes remain available.',
        'Object keys and decimal values are retained without executing embedded content.']


def _notebook(facts, content):
    from .structured_data import load_json
    from .tabular_values import json_value_bytes
    root = load_json(_text(content))
    if not isinstance(root, dict) or root.get('nbformat') != 4 or not isinstance(root.get('cells'), list):
        raise ValueError('SECTOR_EVIDENCE_NOTEBOOK_FORMAT')
    cells = root['cells']
    if len(cells) > 1000:
        raise ValueError('SECTOR_EVIDENCE_NOTEBOOK_CELL_BUDGET')
    for ordinal, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get('cell_type') not in {'markdown', 'code', 'raw'}:
            raise ValueError('SECTOR_EVIDENCE_NOTEBOOK_CELL_INVALID')
        source = cell.get('source', '')
        if isinstance(source, list) and all(isinstance(line, str) for line in source):
            source = ''.join(source)
        if not isinstance(source, str):
            raise TypeError('SECTOR_EVIDENCE_NOTEBOOK_SOURCE_INVALID')
        facts.text('notebook/cell', ordinal, source, cell_type=cell['cell_type'],
            execution_count=cell.get('execution_count'), executed_by_intake=False)
        facts.labels(f'notebook/cell/{ordinal}', source)
        outputs = cell.get('outputs', [])
        if not isinstance(outputs, list) or len(outputs) > 1000:
            raise ValueError('SECTOR_EVIDENCE_NOTEBOOK_OUTPUT_BUDGET')
        for output_ordinal, output in enumerate(outputs):
            if not isinstance(output, dict):
                raise TypeError('SECTOR_EVIDENCE_NOTEBOOK_OUTPUT_INVALID')
            # Retain the source's output claim and exact hash; no rich renderer,
            # kernel, widget, script, URL or embedded media is activated.
            facts.add('native_fact', f'notebook/cell/{ordinal}/outputs', output_ordinal,
                parser='notebook_json', native_kind=output.get('output_type'),
                output_sha256=digest(json_value_bytes(output)), output_verified=False)
    return {'cells': len(cells), 'notebook_executed': False, 'saved_outputs_verified': False}, [
        'Saved outputs and execution counters are source assertions; no notebook kernel is invoked.']


def _archive(facts, content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        members = archive.infolist()
        if len(members) > 4096:
            raise ValueError('SECTOR_EVIDENCE_ARCHIVE_MEMBER_BUDGET')
        unsafe, names = 0, set()
        for ordinal, member in enumerate(members):
            name, path = member.filename, PurePosixPath(member.filename)
            safe = (member.orig_filename == name and name not in names and not path.is_absolute()
                and '..' not in path.parts and '\\' not in name and ':' not in name and '\x00' not in name
                and not stat.S_ISLNK(member.external_attr >> 16) and not member.flag_bits & 1)
            names.add(name)
            unsafe += not safe
            facts.add('archive_member', 'zip', ordinal, text=name, name=name, bytes=member.file_size,
                compressed_bytes=member.compress_size, crc32=f'{member.CRC:08X}',
                directory=member.is_dir(), safe_path=safe, member_content_read=False)
    return {'member_count': len(members), 'unsafe_members': unsafe,
        'archive_members_extracted': False, 'review_required': bool(unsafe)}, [
        'ZIP intake indexes the directory only. Nested file contents need a separately selected bounded read.']


def _native(facts, filename, content, parser):
    if parser == 'document':
        from .document_parsers import parse_document
        native = parse_document(filename, content)
    elif parser == 'presentation':
        from .presentation_parsers import parse_presentation
        native = parse_presentation(filename, content)
    elif parser == 'spreadsheet':
        from .spreadsheet_workers import parse_spreadsheet
        native = parse_spreadsheet(filename, content)
    elif parser == 'data':
        from .structured_data import parse_data
        native = parse_data(filename, content)
    elif parser in {'pdf_pymupdf', 'pdf_pypdf', 'pdf_pdfplumber'}:
        from .pdf_parsers import parse_pdf
        native = parse_pdf(content, native_backend=parser.removeprefix('pdf_'), extract_tables=False)
    elif parser == 'media':
        from .media_parsers import parse_media
        native = parse_media(filename, content)
    else:
        raise ValueError('SECTOR_EVIDENCE_PARSER_INVALID')
    for ordinal, item in enumerate(native['items']):
        payload = {key: value for key, value in item.items() if key != 'text'}
        facts.add('native_fact', f'{parser}/{item["part"]}', ordinal,
            text=item.get('text', ''), parser=parser, native_kind=item['kind'], native_payload=payload)
        native_item_id = facts.items[-1]['item_id']
        if item.get('text'):
            if facts.lane_id == 'artifacts' and (parser != 'media' or item['kind'] == 'svg_text'):
                # The original artifact extract is a hash/locator record. Keep
                # searchable text in its native fact without duplicate chunks.
                facts.add('artifact_text_extract', f'{parser}/extract', ordinal,
                    source_locator=item['part'], native_item_id=native_item_id,
                    text_sha256=digest(item['text'].encode('utf-8')))
            facts.labels(f'{parser}/{item["part"]}/{ordinal}', item['text'])
    if facts.lane_id == 'artifacts' and parser == 'media':
        facts.add('artifact_media_probe', 'media/probe', 0, source_sha256=digest(content),
            native_evidence=native.get('native_evidence'), metadata=native.get('metadata'),
            media_mutated=False)
    return {'native_fidelity': native['fidelity'], 'native_parser': parser}, native['limitations']


def parse_evidence(lane_id, filename, content, parser, *, sqlite_tables=(), sqlite_rows=100,
                   sqlite_schema_engine='stdlib'):
    facts = EvidenceFacts(lane_id, filename, content)
    extension = PurePosixPath(filename).suffix.lower()
    if parser == 'text':
        text = _text(content)
        for ordinal, start in enumerate(range(0, len(text), 4096)):
            facts.text('text', ordinal, text[start:start + 4096], char_start=start,
                char_end=min(start + 4096, len(text)))
        facts.labels('text', text)
        fidelity, limitations = {'text_encoding': 'UTF-8', 'complete': True}, [
            'Deterministic labels preserve source assertions without judging their truth.']
    elif parser == 'json':
        fidelity, limitations = _json(facts, content, extension)
    elif parser == 'notebook':
        fidelity, limitations = _notebook(facts, content)
    elif parser == 'archive':
        fidelity, limitations = _archive(facts, content)
    elif parser == 'sqlite':
        from .selected_sqlite_parser import inspect_sqlite_bytes
        fidelity, limitations = inspect_sqlite_bytes(facts, content, tables=sqlite_tables, rows=sqlite_rows,
            schema_engine=sqlite_schema_engine)
    elif parser == 'opaque':
        facts.add('review_required', 'source', 0, reason='NO_SELECTED_CONTENT_PARSER',
            exact_bytes_preserved=True, bytes=len(content))
        fidelity, limitations = {'content_extracted': False, 'review_required': True}, [
            'Only exact bytes and file metadata are indexed until a supported parser is selected.']
    else:
        fidelity, limitations = _native(facts, filename, content, parser)
    if lane_id == 'artifacts':
        text_available = any((item['text'] or item.get('native_item_id'))
            for item in facts.items if item['kind'] in {'artifact_text_extract', 'sqlite_row'})
        fidelity['text_extract_available'] = text_available
        if not text_available:
            facts.add('artifact_review_required', 'extraction', 0,
                reason='NO_TEXT_EXTRACT_AVAILABLE', exact_bytes_preserved=True)
            fidelity['review_required'] = True
    metadata_kind = {'research': 'research_receipt', 'artifacts': 'artifact_metadata', 'custom': 'custom_item'}[lane_family(lane_id)]
    facts.add(metadata_kind, 'extraction', 0, parser=parser, source_sha256=digest(content),
        content_items=len(facts.items) - 1, fidelity=fidelity)
    return facts.finish(parser, fidelity, limitations)
