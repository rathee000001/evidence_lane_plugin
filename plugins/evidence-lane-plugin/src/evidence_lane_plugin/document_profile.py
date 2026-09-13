"""Registered Docs operations with immutable versions and explicit derivatives.

Intake snapshots exact source bytes. Generation and edits publish new lane-owned
documents. A separate journaled export is required to write a project file.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from .document_parsers import PACKAGE_EXTENSIONS, TEXT_EXTENSIONS, digest
from .document_schema import DOC_MIGRATIONS, DOC_TABLES
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql
from .storage import bounded_project_read, json_text, now, project_snapshot, reject_links
from .tool_routes import ToolRoute

DIGEST = r'^[0-9a-f]{64}$'


def _export_mkstemp(*, prefix: str, suffix: str, directory: Path) -> tuple[int, str]:
    return tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)


class DocumentSelection(Contract):
    lane_id: Literal['docs'] = 'docs'


class DocumentIndex(DocumentSelection):
    filename: str = Field(min_length=1, max_length=1000)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)
    max_file_bytes: int = Field(default=1_048_576, ge=1, le=8_388_608)


class DocumentSnapshot(DocumentSelection):
    snapshot_id: str = Field(pattern=DIGEST)


class DocumentQuery(DocumentSnapshot):
    match_mode: Literal['all', 'any'] = 'all'
    collection: Literal['metadata', 'text', 'paragraph', 'heading', 'table', 'image', 'relationship', 'content_control',
        'insertion', 'deletion', 'field', 'field_instruction', 'hyperlink', 'bookmark', 'embedded_object'] = 'text'
    query: str | None = Field(default=None, min_length=1, max_length=500)
    offset: int = Field(default=0, ge=0, le=100_000)
    limit: int = Field(default=20, ge=1, le=100)
    max_bytes: int = Field(default=65_536, ge=2048, le=262_144)


class DocumentRead(DocumentSnapshot):
    representation: Literal['document', 'original_source'] = 'document'
    offset: int = Field(default=0, ge=0, le=8_388_608)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)


class DocumentBlock(Contract):
    kind: Literal['paragraph', 'heading', 'table', 'page_break']
    text: str = Field(default='', max_length=16_384)
    level: int = Field(default=1, ge=1, le=6)
    rows: list[list[str]] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def shape(self):
        if self.kind == 'table':
            if (not self.rows or not self.rows[0] or len(self.rows[0]) > 20
                    or any(len(row) != len(self.rows[0]) for row in self.rows)
                    or sum(len(cell.encode()) for row in self.rows for cell in row) > 131_072):
                raise ValueError('Use a bounded rectangular document table')
        elif self.rows:
            raise ValueError('Only a table block accepts rows')
        return self


class DocumentGenerate(DocumentSelection):
    logical_name: str = Field(min_length=1, max_length=160, pattern=r'^[^/\\:]+\.docx$')
    title: str = Field(min_length=1, max_length=200)
    blocks: list[DocumentBlock] = Field(min_length=1, max_length=200)
    expected_snapshot: str | None = Field(default=None, pattern=DIGEST)

    @model_validator(mode='after')
    def total(self):
        if len(canonical_json_bytes(self.model_dump(mode='json'))) > 262_144:
            raise ValueError('Select a smaller document generation batch')
        return self


class ParagraphReplacement(Contract):
    part: str = Field(default='word/document.xml', pattern=r'^word/(document|header\d+|footer\d+)\.xml$')
    paragraph_index: int = Field(ge=0, le=8191)
    expected_text: str = Field(max_length=32_768)
    replacement_text: str = Field(max_length=32_768)
    tracked: bool = False
    author: str = Field(default='Evidence Lane', min_length=1, max_length=120)


class DocumentEdit(DocumentSnapshot):
    expected_sha256: str = Field(pattern=DIGEST)
    replacements: list[ParagraphReplacement] = Field(min_length=1, max_length=32)


class DocumentRender(DocumentSnapshot):
    max_pages: int = Field(default=20, ge=1, le=100)
    dpi: int = Field(default=110, ge=72, le=150)
    timeout_seconds: int = Field(default=45, ge=5, le=120)
    max_output_bytes: int = Field(default=8_388_608, ge=65_536, le=16_777_216)


class DocumentRenderRead(DocumentSelection):
    render_id: str = Field(pattern=DIGEST)


class DocumentExport(DocumentSnapshot):
    filename: str = Field(min_length=1, max_length=1000)
    expected_sha256: str | None = Field(default=None, pattern=DIGEST)


class DocumentResult(Contract):
    project_id: str
    lane_id: Literal['docs'] = 'docs'
    operation: str
    result: dict[str, JsonValue]


def result(store, operation, body):
    value = DocumentResult(project_id=store.project_id, operation=operation, result=body)
    if len(canonical_json_bytes(value.model_dump(mode='json'))) > 2_097_152:
        raise LaneError('DOCUMENT_OUTPUT_BUDGET', 'Select a smaller document result.')
    return value


def _relative(value):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path) or '\x00' in str(path) or str(path) == '.':
        raise LaneError('DOCUMENT_PATH_INVALID', 'Select an exact project-relative document filename.')
    return path.as_posix()


def _bytes(path, limit=8_388_608):
    reject_links(path, Path(path.anchor))
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise LaneError('DOCUMENT_FILE_BYTE_BUDGET', 'The selected document exceeds its byte budget.')
    return content


def _lane(store):
    try:
        return store.lane('docs')
    except LaneError as error:
        if error.code != 'LANE_NOT_INITIALIZED':
            raise
        return None


def _parser_contract():
    from . import document_parsers, document_workers
    return digest(canonical_json_bytes({Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
        for module in (document_parsers, document_workers)}))


def _calls(execution, remaining):
    execution.guard.check()
    if execution.guard.calls + remaining > execution.guard.task.budget.max_tool_calls:
        raise LaneError('DELTA_TOOL_BUDGET', 'The document operation must leave room for its required verification.')


def current_snapshot(store, document_id):
    lane = _lane(store)
    if lane is None:
        return None
    read_compatibility(lane, DOC_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='doc_current'").fetchone():
            return None
        row = connection.execute('SELECT snapshot_id FROM doc_current WHERE document_id=?', (document_id,)).fetchone()
    return row[0] if row else None


def read_snapshot(store, snapshot_id):
    lane = _lane(store)
    if lane is None:
        raise LaneError('DOCUMENT_SNAPSHOT_MISSING', 'Index or generate the selected document first.')
    read_compatibility(lane, DOC_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='doc_version'").fetchone():
            raise LaneError('DOCUMENT_SNAPSHOT_MISSING', 'Index or generate the selected document first.')
        row = connection.execute('SELECT * FROM doc_version WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if row is None:
        raise LaneError('DOCUMENT_SNAPSHOT_MISSING', 'Select an exact document snapshot from this Docs lane.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(manifest)) != snapshot_id or manifest['project_id'] != store.project_id
            or manifest['lane_id'] != 'docs' or any(manifest[key] != row[key] for key in (
                'document_id', 'generation', 'previous_snapshot', 'raw_object', 'facts_object', 'parser_contract'))):
        raise LaneError('DOCUMENT_SNAPSHOT_INTEGRITY', 'The document manifest differs from its indexed identity.')
    facts = json.loads(lane.read_object(manifest['facts_object']))
    return manifest, facts


def _worker(execution, operation, arguments):
    response = execution.submit(operation, arguments).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'DOCUMENT_WORKER_FAILED'), 'The selected document worker did not complete.')
    body = json.loads(json_text(response['result']))
    content = base64.b64decode(body.pop('content_base64'), validate=True)
    if digest(content) != body['sha256'] or len(content) != body['bytes'] or body['filename'] != arguments['logical_name']:
        raise LaneError('DOCUMENT_WORKER_BINDING', 'The document worker returned different bytes or a different document.')
    return content, body


def _natural(lane, snapshot_id, name, content):
    name = _relative(name)
    if '/' in name:
        raise LaneError('DOCUMENT_OUTPUT_NAME_INVALID', 'A lane-owned natural artifact requires a simple filename.')
    target = lane.files / 'natural' / snapshot_id / name
    reject_links(target, lane.folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_links(target, lane.folder)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if _bytes(target, 16_777_216) != content:
            raise LaneError('DOCUMENT_ARTIFACT_CHANGED', 'An existing natural artifact differs from its immutable bytes.') from None
    else:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    if _bytes(target, 16_777_216) != content:
        raise LaneError('DOCUMENT_ARTIFACT_WRITE_FAILED', 'The natural document artifact failed byte verification.')
    return str(target)


def _chunks(facts):
    for item in facts['items']:
        if item['kind'] != 'paragraph':
            continue
        text = item['text']
        for ordinal, start in enumerate(range(0, len(text), 4096)):
            yield item['item_id'], ordinal, text[start:start + 4096]


def publish(context, *, document_id, logical_name, source_path, origin, previous, content, parsed, operation, source_content=None,
            source_observation_route_id=None, max_file_bytes=None, intake=False, inputs=None):
    execution, store = context.execution, context.execution.store
    if current_snapshot(store, document_id) != previous:
        raise LaneError('DOCUMENT_SNAPSHOT_CHANGED', 'Bind this operation to the exact current document snapshot.')
    prior = read_snapshot(store, previous)[0] if previous else None
    selection = (execution.guard.source_route.selection.model_dump(mode='json') if execution.guard.source_route
                 else prior.get('source_route') if prior and not intake else None)
    observation = (source_observation_route_id or (selection['route_id'] if selection else None)
                   or (prior.get('source_observation_route_id') if prior and not intake else None))
    if source_observation_route_id and selection:
        selection = {**selection, 'route_id': source_observation_route_id}
    generation, stamp = (prior['generation'] + 1 if prior else 1), now()
    facts, contract = parsed['facts'], _parser_contract()
    manifest = {'schema': 'evidence-lane.document-snapshot.v4', 'project_id': store.project_id, 'lane_id': 'docs',
        'document_id': document_id, 'logical_name': logical_name, 'source_path': source_path, 'origin': origin,
        'generation': generation, 'previous_snapshot': previous, 'raw_object': digest(content),
        'facts_object': digest(canonical_json_bytes(facts)), 'parser_contract': contract,
        'source_object': digest(source_content) if source_content is not None else
            prior.get('source_object') if prior else digest(content) if source_path is not None else None,
        'bytes': len(content), 'created_at': stamp, 'operation': operation, 'tool_evidence': parsed['evidence'],
        'fidelity': facts['fidelity'], 'source_bytes_mutated': False}
    manifest.update(source_route=selection, source_observation_route_id=observation, inputs=inputs or [],
        capture_limits={'max_file_bytes': max_file_bytes or (prior.get('capture_limits', {}).get('max_file_bytes', 8_388_608) if prior else 8_388_608)})
    snapshot_id = digest(canonical_json_bytes(manifest))
    execution._before_more_work()
    with execution.lease.coordinated_transaction(['docs', 'receipts']):
        lane = store.lane('docs')
        apply_migrations(lane, DOC_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute('SELECT snapshot_id FROM doc_current WHERE document_id=?', (document_id,)).fetchone()
            if (live[0] if live else None) != previous:
                raise LaneError('DOCUMENT_SNAPSHOT_CHANGED', 'The document changed before publication.')
            lane.put_object(content)
            if source_content is not None:
                lane.put_object(source_content)
            lane.put_object(canonical_json_bytes(facts))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute('INSERT OR IGNORE INTO doc_file VALUES(?,?,?,?,?)',
                (document_id, logical_name, source_path, origin, stamp))
            connection.execute('INSERT INTO doc_version VALUES(?,?,?,?,?,?,?,?,?)',
                (snapshot_id, document_id, generation, previous, manifest_object, manifest['raw_object'],
                 manifest['facts_object'], contract, stamp))
            connection.execute('INSERT INTO doc_structure VALUES(?,?,?)',
                (snapshot_id, manifest['facts_object'], json_text(facts['fidelity'])))
            for item in facts['items']:
                connection.execute('INSERT INTO ' + DOC_TABLES[item['kind']] + ' VALUES(?,?,?,?,?,?)',
                    (snapshot_id, item['item_id'], item['kind'], item['ordinal'], item['part'], json_text(item)))
            for item_id, ordinal, text in _chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                obj = lane.put_object(text.encode('utf-8'))
                connection.execute('INSERT INTO doc_chunk VALUES(?,?,?,?,?)', (snapshot_id, chunk_id, item_id, ordinal, obj))
                connection.execute('INSERT INTO doc_chunk_fts VALUES(?,?,?)', (snapshot_id, chunk_id, text))
            path = _natural(lane, snapshot_id, PurePosixPath(logical_name).name, content)
            connection.execute('INSERT INTO doc_current VALUES(?,?) ON CONFLICT(document_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                               (document_id, snapshot_id))
            store.append_receipt('document_snapshot', {'snapshot_id': snapshot_id, 'document_id': document_id,
                'job_id': execution.claim.job_id, 'operation': operation, 'source_bytes_mutated': False})
    return result(store, operation, {'snapshot_id': snapshot_id, 'document_id': document_id,
        'generation': generation, 'previous_snapshot': previous, 'sha256': manifest['raw_object'], 'bytes': len(content),
        'logical_name': logical_name, 'natural_path': path, 'source_bytes_mutated': False,
        'fidelity': facts['fidelity'], 'limitations': facts['limitations'], 'tool_evidence': parsed['evidence']})


def index_document(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    document_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    if current_snapshot(store, document_id) != request.expected_snapshot:
        raise LaneError('DOCUMENT_SNAPSHOT_CHANGED', 'Refresh requires the exact current document snapshot.')
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior.get('source_route') is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this previously routed input.')
    content, parsed = _worker(execution, 'document_parse_file', {'filename': str(path), 'logical_name': relative,
        'max_file_bytes': request.max_file_bytes})
    if _bytes(path, request.max_file_bytes) != content:
        raise LaneError('DOCUMENT_SOURCE_CHANGED', 'The source document changed during extraction.')
    return publish(context, document_id=document_id, logical_name=relative, source_path=relative, origin='source',
        previous=request.expected_snapshot, content=content, source_content=content, parsed=parsed, operation=execution.guard.spec.name,
        max_file_bytes=request.max_file_bytes, intake=True)


def generate_document(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    name = _relative(request.logical_name)
    document_id = digest(canonical_json_bytes([store.project_id, 'generated', name]))
    if current_snapshot(store, document_id) != request.expected_snapshot:
        raise LaneError('DOCUMENT_SNAPSHOT_CHANGED', 'Generation requires the exact current document identity.')
    content, parsed = _worker(execution, 'document_generate', request.model_dump(mode='json'))
    return publish(context, document_id=document_id, logical_name=name, source_path=None, origin='generated',
        previous=request.expected_snapshot, content=content, parsed=parsed, operation='document_generate')


def edit_document(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    if manifest['raw_object'] != request.expected_sha256 or current_snapshot(store, manifest['document_id']) != request.snapshot_id:
        raise LaneError('DOCUMENT_EDIT_SNAPSHOT_CHANGED', 'Edit the exact current document bytes.')
    if PurePosixPath(manifest['logical_name']).suffix.lower() not in {'.docx', '.dotx'}:
        raise LaneError('DOCUMENT_EDIT_FORMAT_UNSUPPORTED', 'This bounded paragraph edit contract requires DOCX or DOTX.')
    content, parsed = _worker(execution, 'document_edit', {'logical_name': manifest['logical_name'],
        'content_base64': base64.b64encode(store.lane('docs').read_object(manifest['raw_object'])).decode('ascii'),
        'expected_sha256': request.expected_sha256, 'timestamp': now(),
        'replacements': [row.model_dump(mode='json') for row in request.replacements]})
    return publish(context, document_id=manifest['document_id'], logical_name=manifest['logical_name'],
        source_path=manifest['source_path'], origin=manifest['origin'], previous=request.snapshot_id,
        content=content, parsed=parsed, operation='document_edit')


def verify_document(context, request, output):
    store, snapshot_id = context.store, output.result['snapshot_id']
    manifest, facts = read_snapshot(store, snapshot_id)
    lane = store.lane('docs')
    raw = lane.read_object(manifest['raw_object'])
    valid = len(raw) == manifest['bytes'] and manifest['raw_object'] == output.result['sha256']
    from .document_parsers import parse_document
    valid &= parse_document(manifest['logical_name'], raw) == facts
    valid &= _bytes(Path(output.result['natural_path'])) == raw
    with lane.connection(read_only=True) as connection:
        for table in sorted(set(DOC_TABLES.values())):
            rows = connection.execute('SELECT * FROM ' + table + ' WHERE snapshot_id=? ORDER BY item_id', (snapshot_id,)).fetchall()
            expected = sorted([item for item in facts['items'] if DOC_TABLES[item['kind']] == table], key=lambda item: item['item_id'])
            valid &= [json.loads(row['payload_json']) for row in rows] == expected
            valid &= all((row['item_id'], row['kind'], row['ordinal'], row['part']) == (
                item['item_id'], item['kind'], item['ordinal'], item['part']) for row, item in zip(rows, expected))
        expected = list(_chunks(facts))
        rows = connection.execute('SELECT c.*,f.text_content FROM doc_chunk c JOIN doc_chunk_fts f '
            'ON c.chunk_id=f.chunk_id AND c.snapshot_id=f.snapshot_id WHERE c.snapshot_id=?', (snapshot_id,)).fetchall()
        valid &= len(rows) == len(expected)
        lookup = {(item_id, ordinal): text for item_id, ordinal, text in expected}
        for row in rows:
            text = lookup.get((row['item_id'], row['ordinal']))
            valid &= (text == row['text_content'] and text is not None and lane.read_object(row['text_object']).decode() == text
                and row['chunk_id'] == digest(canonical_json_bytes([snapshot_id, row['item_id'], row['ordinal']])))
    live = not isinstance(request, DocumentIndex) or _bytes(context.source_path(request.filename), request.max_file_bytes) == raw
    checks = {'document_snapshot_integrity': valid, 'document_source_hash_unchanged': live}
    return [{'check_id': name, 'passed': checks[name], 'evidence': {'snapshot_id': snapshot_id,
        'source_bytes_mutated': False, 'layout_verified': False}} for name in context.requested_checks]


def current_documents(store, request=None):
    lane = _lane(store)
    if lane is None:
        return {'documents': [], 'initialized': False}
    read_compatibility(lane, DOC_MIGRATIONS)
    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='doc_current'").fetchone():
            return {'documents': [], 'initialized': False}
        rows = connection.execute('SELECT v.*,f.logical_name,f.source_path,f.origin FROM doc_current c '
            'JOIN doc_version v USING(snapshot_id) JOIN doc_file f ON f.document_id=c.document_id '
            f'WHERE {active} ORDER BY v.created_at DESC LIMIT 129').fetchall()
    return {'documents': [dict(row) for row in rows[:128]], 'truncated': len(rows) > 128,
            'initialized': True, 'source_currentness': 'not_checked_by_metadata_read'}


def query_document(store, request):
    with bounded_project_read(store.root, time.monotonic() + 5):
        manifest, facts = read_snapshot(store, request.snapshot_id)
        lane = store.lane('docs')
        items = {row['item_id']: row for row in facts['items']}
        if request.collection == 'metadata':
            return {'snapshot_id': request.snapshot_id, 'document_id': manifest['document_id'],
                'logical_name': manifest['logical_name'], 'source_object': manifest['source_object'],
                'raw_object': manifest['raw_object'], 'fidelity': facts['fidelity'], 'features': facts['features'],
                'limitations': facts['limitations'], 'structure_counts': {kind: sum(row['kind'] == kind for row in facts['items'])
                    for kind in sorted({row['kind'] for row in facts['items']})}, 'source_bytes_mutated': False}
        args = [request.snapshot_id]
        if request.collection == 'text':
            tokens = re.findall(r'\w+', request.query or '', re.UNICODE)
            if not 1 <= len(tokens) <= 32:
                raise LaneError('DOCUMENT_QUERY_TERMS_REQUIRED', 'Use one to thirty-two literal text search terms.')
            sql = ('SELECT c.*,f.text_content,bm25(doc_chunk_fts) AS rank FROM doc_chunk_fts f JOIN doc_chunk c '
                'ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id WHERE c.snapshot_id=? AND doc_chunk_fts MATCH ? '
                'ORDER BY rank,c.item_id,c.ordinal')
            args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
        else:
            sql = 'SELECT * FROM ' + DOC_TABLES[request.collection] + ' WHERE snapshot_id=? AND kind=?'
            args.append(request.collection)
            if request.query:
                sql += " AND instr(lower(json_extract(payload_json,'$.text')),lower(?))>0"
                args.append(request.query)
            sql += ' ORDER BY part,ordinal,item_id'
        with lane.connection(read_only=True) as connection:
            rows = connection.execute(sql + ' LIMIT ? OFFSET ?', [*args, request.limit + 1, request.offset]).fetchall()
        values, used, truncated = [], 0, False
        for row in rows:
            item = items.get(row['item_id'])
            if item is None:
                raise LaneError('DOCUMENT_QUERY_INTEGRITY', 'A query item is outside the selected immutable document.')
            if request.collection == 'text':
                start = row['ordinal'] * 4096
                text = lane.read_object(row['text_object']).decode('utf-8')
                if (item['kind'] != 'paragraph' or row['ordinal'] < 0 or text != item['text'][start:start + 4096]
                        or text != row['text_content'] or row['chunk_id'] != digest(canonical_json_bytes([
                            request.snapshot_id, row['item_id'], row['ordinal']]))):
                    raise LaneError('DOCUMENT_QUERY_INTEGRITY', 'The indexed text differs from its immutable document facts.')
                value = {'item_id': item['item_id'], 'part': item['part'], 'paragraph_index': item['ordinal'],
                         'text': text, 'rank': row['rank'], 'chunk_ordinal': row['ordinal']}
            else:
                if (json.loads(row['payload_json']) != item or (row['kind'], row['ordinal'], row['part']) != (
                        item['kind'], item['ordinal'], item['part'])):
                    raise LaneError('DOCUMENT_QUERY_INTEGRITY', 'The typed item differs from its immutable document facts.')
                value = item
            size = len(canonical_json_bytes(value))
            if len(values) >= request.limit or used + size > request.max_bytes:
                truncated = True
                break
            values.append(value)
            used += size
        if truncated and not values:
            raise LaneError('DOCUMENT_QUERY_ITEM_TOO_LARGE', 'Increase max_bytes for the next complete document item.')
        return {'snapshot_id': request.snapshot_id, 'document_id': manifest['document_id'], 'rows': values,
            'next_offset': request.offset + len(values) if truncated else None, 'truncated': truncated,
            'fidelity': facts['fidelity'], 'source_bytes_mutated': False}


def read_document(store, request):
    manifest, _ = read_snapshot(store, request.snapshot_id)
    selected = manifest['source_object'] if request.representation == 'original_source' else manifest['raw_object']
    if selected is None:
        raise LaneError('DOCUMENT_ORIGINAL_SOURCE_ABSENT', 'This generated document has no separate original source file.')
    raw = store.lane('docs').read_object(selected)
    value = raw[request.offset:request.offset + request.max_bytes]
    end = request.offset + len(value)
    return {'snapshot_id': request.snapshot_id, 'logical_name': manifest['source_path'] if request.representation == 'original_source' else manifest['logical_name'],
        'sha256': selected, 'representation': request.representation,
        'total_bytes': len(raw), 'offset': request.offset, 'content_base64': base64.b64encode(value).decode('ascii'),
        'next_offset': end if end < len(raw) else None, 'source_bytes_mutated': False}


def export_document(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() != PurePosixPath(manifest['logical_name']).suffix.lower():
        raise LaneError('DOCUMENT_EXPORT_FORMAT_MISMATCH', 'Export uses the existing document format; it is not conversion.')
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'write', path=path)
    reject_links(path, store.source_root)
    before_content = _bytes(path) if path.is_file() else None
    before = digest(before_content) if before_content is not None else None
    if path.exists() and not path.is_file() or before != request.expected_sha256:
        raise LaneError('DOCUMENT_EXPORT_DESTINATION_CHANGED', 'Bind export to the exact destination hash, or absence for a new file.')
    if not path.parent.is_dir():
        raise LaneError('DOCUMENT_EXPORT_PARENT_MISSING', 'Select an existing granted destination directory.')
    lane, export_id = store.lane('docs'), str(uuid4())
    content = lane.read_object(manifest['raw_object'])
    if manifest['parser_contract'] != _parser_contract():
        raise LaneError('DOCUMENT_PARSER_CONTRACT_CHANGED', 'Refresh this development snapshot with the current parser before exporting.')
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    previous = current_snapshot(store, destination_id)
    destination_manifest = read_snapshot(store, previous)[0] if previous else None
    if destination_manifest and 'source_observation_route_id' not in destination_manifest:
        raise LaneError('DOCUMENT_REFRESH_PROVENANCE_REQUIRED', 'Refresh the destination snapshot with current source provenance before export.')
    limit = (destination_manifest or manifest).get('capture_limits', {}).get('max_file_bytes', 8_388_608)
    if len(content) > limit:
        raise LaneError('DOCUMENT_FILE_BYTE_BUDGET', 'The proposed export exceeds the selected destination intake bound.')
    from .artifact_contract import LaneArtifacts
    artifacts = LaneArtifacts(execution.guard.engine, store)
    view_id = 'docs.structure'
    view_selection = artifacts.current_selection(view_id)
    from .source_routing import SourceMutationRefresh
    source_refresh = SourceMutationRefresh(context, [relative], lane_id='docs',
        action='document_export', max_file_bytes=8_388_608, allow_create=True,
        parent_route_id=destination_manifest.get('source_observation_route_id') if destination_manifest else None)
    source_refresh.expected_after(path, before, content)
    _calls(execution, 3 + int(bool(view_selection and view_selection['formats'])))
    parsed_content, parsed = _worker(execution, 'document_parse_content', {
        'filename': str(path), 'logical_name': relative, 'max_file_bytes': limit,
        'content_base64': base64.b64encode(content).decode('ascii')})
    execution.guard.observe(execution)
    if parsed_content != content or manifest['parser_contract'] != _parser_contract():
        raise LaneError('DOCUMENT_PARSER_CONTRACT_CHANGED', 'The proposed bytes or selected parser changed before export.')
    effect = execution.prepare_effect('document:' + export_id, 'Export exact versioned document bytes to one granted project filename.')
    with execution.lease.coordinated_transaction(['docs', 'receipts']):
        with lane.transaction() as connection:
            if before_content is not None:
                lane.put_object(before_content)
            connection.execute('INSERT INTO doc_export VALUES(?,?,?,?,?,?,?)',
                (export_id, request.snapshot_id, relative, before, manifest['raw_object'], effect, now()))
        store.append_receipt('document_export_prepared', {'export_id': export_id, 'snapshot_id': request.snapshot_id,
            'destination': relative, 'before_sha256': before, 'after_sha256': manifest['raw_object'], 'effect_id': effect})
    descriptor, temporary = _export_mkstemp(
        prefix='.evidence-lane-document-', suffix='.tmp', directory=path.parent
    )
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        execution._before_more_work()
        ProjectAccess(store).authorize(context.client_id, 'write', path=path)
        reject_links(path, store.source_root)
        observed = digest(_bytes(path)) if path.is_file() else None
        if observed != before or path.exists() and not path.is_file():
            raise LaneError('DOCUMENT_EXPORT_DESTINATION_CHANGED', 'The destination changed immediately before export; reconcile this effect.')
        if before is None:
            # Windows rename fails if a new destination appeared; unlike replace,
            # it cannot silently overwrite a concurrently created user file.
            if os.name == 'nt':
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
                temporary.unlink()
        else:
            os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        if _bytes(path) != content:
            raise LaneError('DOCUMENT_EXPORT_VERIFY_FAILED', 'The written document bytes were not confirmed.')
        with execution.lease.transaction('plan'):
            evidence = execution.plan_store.put_object(canonical_json_bytes({'export_id': export_id,
                'destination': relative, 'sha256': manifest['raw_object'], 'exact_bytes_verified': True}))
            execution.confirm_effect(effect, evidence)
    finally:
        if temporary.exists():
            temporary.unlink()
    with execution.lease.coordinated_transaction(['sources', 'docs', 'receipts']):
        source_result = source_refresh.publish(path=path, before_sha256=before, replacement=content,
            mutation_id=export_id, effect_id=effect)
        indexed = publish(context, document_id=destination_id, logical_name=relative,
            source_path=relative, origin='source', previous=previous, content=content, parsed=parsed,
            operation='document_export', max_file_bytes=limit,
            source_observation_route_id=source_result['route_id'],
            inputs=[{'lane_id': 'docs', 'snapshot_id': request.snapshot_id, 'sha256': manifest['raw_object'], 'operation': 'export'}])
        view_result = artifacts.refresh_selected(view_id, view_selection, execution, actor_id=context.client_id)
        execution._before_more_work()
        final_capture = source_refresh.capture()
        from .source_authority import freeze_source_authority
        for spec in source_refresh.specs:
            freeze_source_authority(spec, capture=final_capture)
        if final_capture.identities != source_refresh.expected_after(path, before, content):
            raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'A source changed before the refreshed lanes could publish.')
    return result(store, 'document_export', {'export_id': export_id, 'snapshot_id': request.snapshot_id,
        'destination': relative, 'before_sha256': before, 'after_sha256': manifest['raw_object'], 'effect_id': effect,
        'source_bytes_mutated': True, 'source_index_refresh_required': False, 'automatic_replay': False,
        'index_refresh': indexed.model_dump(mode='json'), 'source_refresh': source_result, 'view_refresh': view_result})


def verify_export(context, request, output, *, engine=None):
    manifest, _ = read_snapshot(context.store, request.snapshot_id)
    valid = digest(_bytes(context.source_path(request.filename))) == manifest['raw_object'] == output.result['after_sha256']
    lane = context.store.lane('docs')
    with lane.connection(read_only=True) as connection:
        exported = connection.execute('SELECT * FROM doc_export WHERE export_id=?', (output.result['export_id'],)).fetchone()
    with context.store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT * FROM jobs_effects WHERE effect_id=?', (output.result['effect_id'],)).fetchone()
    valid &= exported is not None and effect is not None and effect['job_id'] == context.job_id and effect['state'] == 'confirmed'
    if exported is not None and effect is not None:
        valid &= all(exported[key] == value for key, value in {'snapshot_id': request.snapshot_id,
            'destination': _relative(request.filename), 'before_sha256': request.expected_sha256,
            'after_sha256': manifest['raw_object'], 'effect_id': output.result['effect_id']}.items())
        valid &= json.loads(context.store.lane('plan').read_object(effect['evidence_object'])) == {
            'export_id': output.result['export_id'], 'destination': _relative(request.filename),
            'sha256': manifest['raw_object'], 'exact_bytes_verified': True}
    indexed = DocumentResult.model_validate(output.result['index_refresh'])
    index_manifest, _ = read_snapshot(context.store, indexed.result['snapshot_id'])
    expected_id = digest(canonical_json_bytes([context.store.project_id, 'source', _relative(request.filename)]))
    valid &= (indexed.project_id == context.store.project_id and indexed.operation == 'document_export'
        and indexed.lane_id == 'docs' and index_manifest['document_id'] == expected_id
        and current_snapshot(context.store, expected_id) == indexed.result['snapshot_id']
        and index_manifest['logical_name'] == index_manifest['source_path'] == _relative(request.filename)
        and index_manifest['raw_object'] == manifest['raw_object'] and not output.result['source_index_refresh_required']
        and index_manifest['inputs'] == [{'lane_id': 'docs', 'snapshot_id': request.snapshot_id,
            'sha256': manifest['raw_object'], 'operation': 'export'}])
    valid &= all(row['passed'] for row in verify_document(replace(context,
        requested_checks=('document_snapshot_integrity',)), request, indexed))
    source = output.result['source_refresh']
    prior = read_snapshot(context.store, index_manifest['previous_snapshot'])[0] if index_manifest['previous_snapshot'] else None
    from .source_routing import verify_export_refresh
    valid &= (index_manifest['source_observation_route_id'] == source['route_id']
        and source['parent_route_id'] == (prior.get('source_observation_route_id') if prior else None)
        and verify_export_refresh(context, source, lane_id='docs',
            action='document_export', mutation_id=output.result['export_id'],
            effect_id=output.result['effect_id'], max_file_bytes=8_388_608))
    if engine is None:
        valid = False
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed(
            'docs.structure', output.result['view_refresh'])
        valid &= view_valid and len(context.worker_evidence) == 1 + graph_workers and all(
            row['status'] == 'ok' for row in context.worker_evidence)
    return [{'check_id': name, 'passed': valid, 'evidence': {'export_id': output.result['export_id'],
        'after_sha256': manifest['raw_object']}} for name in context.requested_checks]


def register_document_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def export_route(render):
        def applicable(context, request):
            from .artifact_contract import LaneArtifacts
            store = engine.directory.open(context.project_id)
            view = LaneArtifacts(engine, store).current_selection('docs.structure')
            return bool(view and view['formats']) is render
        return applicable

    def verify_exported(context, request, output):
        return verify_export(context, request, output, engine=engine)

    for action, workflow in (('document_index', 'manage-project-sources'), ('document_refresh', 'refresh-project-evidence')):
        engine.registry.register(ActionSpec(action, 'Snapshot one exact document and its bounded native structure in the separate Docs lane.',
            DocumentIndex, DocumentResult, index_document, permission='write', mutates=True, requires_delta=True,
            profile='document', workflow=workflow, path_fields=('filename',), source_lanes=('docs',),
            materialization=SourceMaterialization() if action == 'document_index' else None, worker_operations=('document_parse_file',),
            verification_checks=('document_snapshot_integrity', 'document_source_hash_unchanged'), verifier=verify_document))
    for action, model, handler, workers, tools in (
        ('document_generate', DocumentGenerate, generate_document, ('document_generate',), ('Python', 'DOCX_OpenXML')),
        ('document_edit', DocumentEdit, edit_document, ('document_edit',), ('Python', 'lxml'))):
        engine.registry.register(ActionSpec(action, 'Publish a new immutable lane-owned DOCX version with explicit structural and layout fidelity.',
            model, DocumentResult, handler, permission='write', mutates=True, requires_delta=True, profile='document', workflow='execute-project-plan',
            worker_operations=workers, verification_checks=('document_snapshot_integrity',), verifier=verify_document,
            tool_routes=(ToolRoute(action + '.native_document', handler, tools),)))

    def query_handler(function, action):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, action, function(store, request))
        return handler

    for action, model, function, description in (
        ('document_current', DocumentSelection, current_documents, 'Read current document identities without refreshing source files.'),
        ('document_query', DocumentQuery, query_document, 'Read bounded native structures or literal FTS5/BM25 matches from an exact document version.'),
        ('document_read', DocumentRead, read_document, 'Read a bounded base64 byte range from an exact immutable document.')):
        engine.registry.register(ActionSpec(action, description, model, DocumentResult, query_handler(function, action),
            profile='document', workflow='manage-project-sources', queryable_in_delta=True, cross_project_read=True,
            studio_read=True, read_migrations=DOC_MIGRATIONS,
            search=SearchRoute(('docs',), 'rows', 'document_current', 'documents', 'text', 'any', rerank_text='text') if action == 'document_query' else None,
            fetch=FetchRoute(('docs',), 'document') if action == 'document_read' else None))
    engine.registry.register(ActionSpec('document_export', 'Parse and export exact versioned bytes, then automatically refresh Sources, destination facts and existing lane views.',
        DocumentExport, DocumentResult, export_document, permission='write', mutates=True, requires_delta=True,
        profile='document', workflow='execute-project-plan', path_fields=('filename',), verification_checks=('document_export_hash_verified',),
        verifier=verify_exported, worker_operations=('document_parse_content', 'render_lane_view'),
        tool_routes=tuple(ToolRoute('document_export.native' + ('_view' if render else ''), export_document,
            ('Python', *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
            view_refresh=SelectedViewRefresh(engine, 'docs.structure'), applicable=export_route(render), worker_operations=('document_parse_content', *(('render_lane_view',) if render else ())))
            for render in (False, True))))
    from .document_rendering import register_render_actions
    register_render_actions(engine)
    from .document_views import register_document_views
    register_document_views(engine)
    from .document_enrichment import register_enrichment_actions
    register_enrichment_actions(engine)
    from .document_conversion import register_conversion_actions
    register_conversion_actions(engine)


def format_contract():
    return {'schema': 'evidence-lane.document-formats.v4', 'lane_id': 'docs',
        'native_intake': sorted(PACKAGE_EXTENSIONS | TEXT_EXTENSIONS),
        'generation': ['.docx'], 'paragraph_edit': ['.docx', '.dotx'],
        'render': ['.docx', '.dotx', '.odt'], 'render_engine': 'verified_shared_LibreOffice_and_pypdfium2',
        'legacy_conversion_required': ['.doc', '.rtf'], 'legacy_conversion_implemented': True,
        'legacy_fidelity': 'original_retained_and_derived_docx_not_lossless_round_trip',
        'optional_enrichment': {'engine': 'Docling', 'formats': ['.docx', '.dotx'],
            'completion_required': 'success', 'model_download_during_operation': False},
        'budgets': {'max_source_bytes': 8_388_608, 'max_expanded_package_bytes': 33_554_432,
            'max_package_members': 4096, 'max_structure_items': 8192, 'text_chunk_characters': 4096,
            'max_query_items': 100, 'max_render_pages': 100, 'max_render_bytes': 16_777_216,
            'project_task_budget_applies_to_all_workers': True},
        'render_is_word_equivalence_proof': False, 'source_edits_require_separate_export': True,
        'macros_and_external_resources_executed': False, 'original_pdf_ocr_owner': 'pdf_ocr',
        'natural_files': ['original_format', '.docx', '.pdf', '.png'], 'graph_files': ['docs.mmd', 'docs.dot', 'docs.pointer.json']}
