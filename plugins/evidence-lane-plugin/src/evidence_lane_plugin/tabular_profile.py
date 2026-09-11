"""Versioned tabular operations with separate physical lane ownership.

Shared transaction mechanics do not merge Excel or structured-data facts.
Every public schema fixes its lane, and external file export is a separate effect.
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

from pydantic import JsonValue

from . import tabular_contracts as contracts
from .document_parsers import digest
from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql
from .storage import json_text, now, project_snapshot, reject_links
from .tabular_schema import LANE_TABLES, PREFIX, tabular_migrations
from .tabular_values import MAX_FILE_BYTES
from .tool_routes import ToolRoute

ACTION_PREFIX = {'data_excel': 'spreadsheet', 'data': 'data'}


class TabularResult(Contract):
    project_id: str
    lane_id: Literal['data_excel', 'data']
    operation: str
    result: dict[str, JsonValue]


def result(store, lane_id, operation, body):
    value = TabularResult(project_id=store.project_id, lane_id=lane_id, operation=operation, result=body)
    if len(canonical_json_bytes(value.model_dump(mode='json'))) > 2_097_152:
        raise LaneError('TABULAR_OUTPUT_BUDGET', 'Select a smaller tabular result.')
    return value


def relative_path(value):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path) or '\x00' in str(path) or str(path) == '.':
        raise LaneError('TABULAR_PATH_INVALID', 'Select an exact project-relative filename.')
    return path.as_posix()


def read_bytes(path, limit=MAX_FILE_BYTES):
    reject_links(path, Path(path.anchor))
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise LaneError('TABULAR_FILE_BYTE_BUDGET', 'The selected file exceeds its byte budget.')
    return content


def lane_if_present(store, lane_id):
    try:
        return store.lane(lane_id)
    except LaneError as error:
        if error.code != 'LANE_NOT_INITIALIZED':
            raise
        return None


def parser_contract(lane_id):
    from . import tabular_values, tabular_workers
    if lane_id == 'data_excel':
        from . import spreadsheet_parsers, spreadsheet_workers
        modules = [spreadsheet_parsers, spreadsheet_workers]
    elif lane_id == 'data':
        from . import structured_data
        modules = [structured_data]
    else:
        raise LaneError('TABULAR_LANE_UNSUPPORTED', 'Select Excel or structured data.')
    files = {Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
        for module in [tabular_values, tabular_workers, *modules]}
    return digest(canonical_json_bytes(files))


def check_calls(execution, count=2):
    execution.guard.check()
    if execution.guard.calls + count > execution.guard.task.budget.max_tool_calls:
        raise LaneError('DELTA_TOOL_BUDGET', 'Leave room for the operation and required verification.')


def current_snapshot(store, lane_id, source_id):
    lane, prefix = lane_if_present(store, lane_id), PREFIX[lane_id]
    if lane is None:
        return None
    read_compatibility(lane, tabular_migrations(lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (prefix + '_current',)).fetchone():
            return None
        row = connection.execute(f'SELECT snapshot_id FROM {prefix}_current WHERE source_id=?', (source_id,)).fetchone()
    return row[0] if row else None


def read_snapshot(store, lane_id, snapshot_id):
    lane, prefix = lane_if_present(store, lane_id), PREFIX[lane_id]
    if lane is None:
        raise LaneError('TABULAR_SNAPSHOT_MISSING', 'Index or generate the selected file in its owning lane first.')
    read_compatibility(lane, tabular_migrations(lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (prefix + '_version',)).fetchone():
            raise LaneError('TABULAR_SNAPSHOT_MISSING', 'Index or generate the selected file first.')
        row = connection.execute(f'SELECT * FROM {prefix}_version WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if row is None:
        raise LaneError('TABULAR_SNAPSHOT_MISSING', 'Select an exact snapshot from this lane.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(manifest)) != snapshot_id or manifest['project_id'] != store.project_id
            or manifest['lane_id'] != lane_id or any(manifest[key] != row[key] for key in (
                'source_id', 'generation', 'previous_snapshot', 'raw_object', 'facts_object', 'parser_contract'))):
        raise LaneError('TABULAR_SNAPSHOT_INTEGRITY', 'The immutable manifest differs from its indexed identity.')
    facts = json.loads(lane.read_object(manifest['facts_object']))
    if facts['lane_id'] != lane_id:
        raise LaneError('TABULAR_SNAPSHOT_INTEGRITY', 'The facts belong to a different lane.')
    return manifest, facts


def worker(execution, operation, arguments):
    response = execution.submit(operation, arguments).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'TABULAR_WORKER_FAILED'), 'The selected tabular worker did not complete.')
    body = json.loads(json_text(response['result']))
    content = base64.b64decode(body.pop('content_base64'), validate=True)
    if (digest(content) != body['sha256'] or len(content) != body['bytes'] or body['filename'] != arguments['logical_name']
            or body['lane_id'] != arguments['lane_id']):
        raise LaneError('TABULAR_WORKER_BINDING', 'The worker returned different bytes, lane or filename.')
    return content, body


def natural_file(lane, snapshot_id, filename, content):
    filename = relative_path(filename)
    if '/' in filename:
        raise LaneError('TABULAR_OUTPUT_NAME_INVALID', 'A lane-owned natural artifact needs a simple filename.')
    target = lane.files / 'natural' / snapshot_id / filename
    reject_links(target, lane.folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_links(target, lane.folder)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if read_bytes(target, max(MAX_FILE_BYTES, len(content))) != content:
            raise LaneError('TABULAR_ARTIFACT_CHANGED', 'An existing natural artifact differs from its immutable bytes.') from None
    else:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    return str(target)


def chunks(facts):
    for item in facts['items']:
        if item['kind'] not in {'cell', 'row', 'query', 'defined_name'}:
            continue
        for ordinal, start in enumerate(range(0, len(item['text']), 4096)):
            yield item['item_id'], ordinal, item['text'][start:start + 4096]


def with_lineage(facts, inputs):
    if facts['lane_id'] != 'data' or not inputs:
        return facts
    facts = json.loads(json.dumps(facts))
    for ordinal, value in enumerate(inputs):
        part, kind = 'transformation_inputs', 'lineage'
        facts['items'].append({'item_id': digest(canonical_json_bytes([kind, part, ordinal])),
            'kind': kind, 'part': part, 'ordinal': ordinal, 'text': value['snapshot_id'], 'input': value})
    facts['counts']['lineage'] = len(inputs)
    if len(canonical_json_bytes(facts)) > 16_777_216:
        raise LaneError('TABULAR_FACT_BYTE_BUDGET', 'The typed facts and transformation lineage exceed their byte budget.')
    return facts


def publish(context, *, lane_id, source_id, logical_name, source_path, origin, previous, content, parsed, operation, inputs=None,
            source_observation_route_id=None, max_file_bytes=None, intake=False):
    execution, store = context.execution, context.execution.store
    if current_snapshot(store, lane_id, source_id) != previous:
        raise LaneError('TABULAR_SNAPSHOT_CHANGED', 'Bind the operation to the exact current snapshot.')
    prior = read_snapshot(store, lane_id, previous)[0] if previous else None
    selection = (execution.guard.source_route.selection.model_dump(mode='json') if execution.guard.source_route
                 else prior.get('source_route') if prior and not intake else None)
    observation = (source_observation_route_id or (selection['route_id'] if selection else None)
                   or (prior.get('source_observation_route_id') if prior and not intake else None))
    if source_observation_route_id and selection:
        selection = {**selection, 'route_id': source_observation_route_id}
    generation, stamp = (prior['generation'] + 1 if prior else 1), now()
    facts, contract, prefix = with_lineage(parsed['facts'], inputs), parser_contract(lane_id), PREFIX[lane_id]
    if facts['lane_id'] != lane_id or any(item['kind'] not in LANE_TABLES[lane_id] for item in facts['items']):
        raise LaneError('TABULAR_FACT_OWNER_INVALID', 'The parser returned facts outside the owning lane contract.')
    manifest = {'schema': 'evidence-lane.tabular-snapshot.v4', 'project_id': store.project_id, 'lane_id': lane_id,
        'source_id': source_id, 'logical_name': logical_name, 'source_path': source_path, 'origin': origin,
        'generation': generation, 'previous_snapshot': previous, 'raw_object': digest(content),
        'facts_object': digest(canonical_json_bytes(facts)), 'parser_contract': contract,
        'original_source_object': digest(content) if intake else prior['original_source_object'] if prior else digest(content) if source_path else None,
        'bytes': len(content), 'created_at': stamp, 'operation': operation, 'tool_evidence': parsed['evidence'],
        'inputs': inputs or [], 'fidelity': facts['fidelity'], 'source_bytes_mutated': False}
    manifest.update(source_route=selection, source_observation_route_id=observation,
        capture_limits={'max_file_bytes': max_file_bytes or (prior.get('capture_limits', {}).get('max_file_bytes', MAX_FILE_BYTES) if prior else MAX_FILE_BYTES)})
    snapshot_id = digest(canonical_json_bytes(manifest))
    execution._before_more_work()
    with execution.lease.coordinated_transaction([lane_id, 'receipts']):
        lane = store.lane(lane_id)
        apply_migrations(lane, tabular_migrations(lane_id), writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute(f'SELECT snapshot_id FROM {prefix}_current WHERE source_id=?', (source_id,)).fetchone()
            if (live[0] if live else None) != previous:
                raise LaneError('TABULAR_SNAPSHOT_CHANGED', 'The lane head changed before publication.')
            lane.put_object(content)
            lane.put_object(canonical_json_bytes(facts))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute(f'INSERT OR IGNORE INTO {prefix}_source VALUES(?,?,?,?,?)', (source_id, logical_name, source_path, origin, stamp))
            connection.execute(f'INSERT INTO {prefix}_version VALUES(?,?,?,?,?,?,?,?,?)',
                (snapshot_id, source_id, generation, previous, manifest_object, manifest['raw_object'], manifest['facts_object'], contract, stamp))
            for item in facts['items']:
                connection.execute('INSERT INTO ' + LANE_TABLES[lane_id][item['kind']] + ' VALUES(?,?,?,?,?,?)',
                    (snapshot_id, item['item_id'], item['kind'], item['ordinal'], item['part'], json_text(item)))
            for item_id, ordinal, text in chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                connection.execute(f'INSERT INTO {prefix}_chunk VALUES(?,?,?,?,?)',
                    (snapshot_id, chunk_id, item_id, ordinal, lane.put_object(text.encode('utf-8'))))
                connection.execute(f'INSERT INTO {prefix}_chunk_fts VALUES(?,?,?)', (snapshot_id, chunk_id, text))
            path = natural_file(lane, snapshot_id, PurePosixPath(logical_name).name, content)
            connection.execute(f'INSERT INTO {prefix}_current VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                (source_id, snapshot_id))
            store.append_receipt('tabular_snapshot', {'lane_id': lane_id, 'snapshot_id': snapshot_id, 'source_id': source_id,
                'job_id': execution.claim.job_id, 'operation': operation, 'source_bytes_mutated': False})
    return result(store, lane_id, operation, {'snapshot_id': snapshot_id, 'source_id': source_id,
        'generation': generation, 'previous_snapshot': previous, 'sha256': manifest['raw_object'], 'bytes': len(content),
        'logical_name': logical_name, 'natural_path': path, 'source_bytes_mutated': False,
        'fidelity': facts['fidelity'], 'limitations': facts['limitations'], 'tool_evidence': parsed['evidence']})


def index(context, request):
    execution, lane_id = context.execution, request.lane_id
    store = execution.store
    check_calls(execution)
    name = relative_path(request.filename)
    path = execution.guard.path(name)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    source_id = digest(canonical_json_bytes([store.project_id, lane_id, 'source', name]))
    if current_snapshot(store, lane_id, source_id) != request.expected_snapshot:
        raise LaneError('TABULAR_SNAPSHOT_CHANGED', 'Refresh requires the exact current source snapshot.')
    prior = read_snapshot(store, lane_id, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior.get('source_route') is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this previously routed input.')
    raw, parsed = worker(execution, 'tabular_parse_file', {'lane_id': lane_id, 'filename': str(path),
        'logical_name': name, 'max_file_bytes': request.max_file_bytes})
    if read_bytes(path, request.max_file_bytes) != raw:
        raise LaneError('TABULAR_SOURCE_CHANGED', 'The source changed during extraction.')
    return publish(context, lane_id=lane_id, source_id=source_id, logical_name=name, source_path=name, origin='source',
        previous=request.expected_snapshot, content=raw, parsed=parsed, operation=execution.guard.spec.name,
        max_file_bytes=request.max_file_bytes, intake=True)


def generate(context, request):
    execution, lane_id = context.execution, request.lane_id
    check_calls(execution)
    operation = ACTION_PREFIX[lane_id] + '_generate'
    name = relative_path(request.logical_name)
    source_id = digest(canonical_json_bytes([execution.store.project_id, lane_id, 'generated', name]))
    if current_snapshot(execution.store, lane_id, source_id) != request.expected_snapshot:
        raise LaneError('TABULAR_SNAPSHOT_CHANGED', 'Generation requires the exact output identity.')
    content, parsed = worker(execution, operation, request.model_dump(mode='json'))
    return publish(context, lane_id=lane_id, source_id=source_id, logical_name=name, source_path=None, origin='generated',
        previous=request.expected_snapshot, content=content, parsed=parsed, operation=operation)


def edit(context, request):
    execution, lane_id = context.execution, request.lane_id
    check_calls(execution)
    manifest, _ = read_snapshot(execution.store, lane_id, request.snapshot_id)
    if manifest['raw_object'] != request.expected_sha256 or current_snapshot(execution.store, lane_id, manifest['source_id']) != request.snapshot_id:
        raise LaneError('TABULAR_EDIT_SNAPSHOT_CHANGED', 'Edit the exact current version and bytes.')
    operation = ACTION_PREFIX[lane_id] + '_edit'
    arguments = request.model_dump(mode='json') | {'logical_name': manifest['logical_name'],
        'content_base64': base64.b64encode(execution.store.lane(lane_id).read_object(manifest['raw_object'])).decode('ascii')}
    content, parsed = worker(execution, operation, arguments)
    return publish(context, lane_id=lane_id, source_id=manifest['source_id'], logical_name=manifest['logical_name'],
        source_path=manifest['source_path'], origin=manifest['origin'], previous=request.snapshot_id,
        content=content, parsed=parsed, operation=operation)


def transform_data(context, request):
    execution, lane_id = context.execution, request.lane_id
    check_calls(execution)
    manifest, facts = read_snapshot(execution.store, lane_id, request.snapshot_id)
    if manifest['raw_object'] != request.expected_sha256:
        raise LaneError('TABULAR_SOURCE_HASH_MISMATCH', 'Bind the transformation to exact source bytes.')
    source_id = digest(canonical_json_bytes([execution.store.project_id, lane_id, 'generated', request.logical_name]))
    if current_snapshot(execution.store, lane_id, source_id) != request.expected_output_snapshot:
        raise LaneError('TABULAR_SNAPSHOT_CHANGED', 'Bind the transformation to its exact output identity.')
    content, parsed = worker(execution, 'data_transform', request.model_dump(mode='json') | {
        'source_name': manifest['logical_name'],
        'facts': facts, 'facts_sha256': manifest['facts_object'],
        'content_base64': base64.b64encode(execution.store.lane(lane_id).read_object(manifest['raw_object'])).decode('ascii')})
    return publish(context, lane_id=lane_id, source_id=source_id, logical_name=request.logical_name, source_path=None,
        origin='generated', previous=request.expected_output_snapshot, content=content, parsed=parsed, operation='data_transform',
        inputs=[{'lane_id': lane_id, 'snapshot_id': request.snapshot_id, 'sha256': request.expected_sha256,
                 'transformation': request.model_dump(mode='json')}])


def verify_snapshot(context, request, output):
    lane_id, snapshot_id = request.lane_id, output.result['snapshot_id']
    store, prefix = context.store, PREFIX[lane_id]
    manifest, facts = read_snapshot(store, lane_id, snapshot_id)
    lane = store.lane(lane_id)
    raw = lane.read_object(manifest['raw_object'])
    from .tabular_workers import parse
    valid = (with_lineage(parse(lane_id, manifest['logical_name'], raw), manifest['inputs']) == facts and len(raw) == manifest['bytes']
        and manifest['raw_object'] == output.result['sha256'] and read_bytes(Path(output.result['natural_path'])) == raw)
    with lane.connection(read_only=True) as connection:
        for table in sorted(set(LANE_TABLES[lane_id].values())):
            rows = connection.execute('SELECT * FROM ' + table + ' WHERE snapshot_id=? ORDER BY item_id', (snapshot_id,)).fetchall()
            expected = sorted([item for item in facts['items'] if LANE_TABLES[lane_id][item['kind']] == table], key=lambda item: item['item_id'])
            valid &= [json.loads(row['payload_json']) for row in rows] == expected
            valid &= all((row['item_id'], row['kind'], row['ordinal'], row['part']) == (
                item['item_id'], item['kind'], item['ordinal'], item['part']) for row, item in zip(rows, expected))
        rows = connection.execute(f'SELECT c.*,f.text_content FROM {prefix}_chunk c JOIN {prefix}_chunk_fts f '
            'ON c.chunk_id=f.chunk_id AND c.snapshot_id=f.snapshot_id WHERE c.snapshot_id=?', (snapshot_id,)).fetchall()
        expected = {(item_id, ordinal): text for item_id, ordinal, text in chunks(facts)}
        valid &= len(rows) == len(expected)
        for row in rows:
            text = expected.get((row['item_id'], row['ordinal']))
            valid &= (text is not None and text == row['text_content'] and lane.read_object(row['text_object']).decode() == text
                and row['chunk_id'] == digest(canonical_json_bytes([snapshot_id, row['item_id'], row['ordinal']])))
    source = not isinstance(request, contracts.Index) or read_bytes(context.source_path(request.filename), request.max_file_bytes) == raw
    checks = {'tabular_snapshot_integrity': valid, 'tabular_source_hash_unchanged': source}
    return [{'check_id': name, 'passed': checks[name], 'evidence': {'lane_id': lane_id, 'snapshot_id': snapshot_id,
        'source_bytes_mutated': False, 'formula_calculation_verified': False}} for name in context.requested_checks]


def current(store, request):
    lane_id = request.lane_id
    lane, prefix = lane_if_present(store, lane_id), PREFIX[lane_id]
    if lane is None:
        return {'files': [], 'initialized': False}
    read_compatibility(lane, tabular_migrations(lane_id))
    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (prefix + '_current',)).fetchone():
            return {'files': [], 'initialized': False}
        rows = connection.execute(f'SELECT v.*,f.logical_name,f.source_path,f.origin FROM {prefix}_current c '
            f'JOIN {prefix}_version v USING(snapshot_id) JOIN {prefix}_source f ON f.source_id=c.source_id '
            f'WHERE {active} ORDER BY v.created_at DESC LIMIT 129').fetchall()
    return {'files': [dict(row) for row in rows[:128]], 'truncated': len(rows) > 128,
        'initialized': True, 'source_currentness': 'not_checked_by_metadata_read'}


def query(store, request):
    lane_id, prefix = request.lane_id, PREFIX[request.lane_id]
    manifest, facts = read_snapshot(store, lane_id, request.snapshot_id)
    lane = store.lane(lane_id)
    if request.collection == 'metadata':
        return {'snapshot_id': request.snapshot_id, 'source_id': manifest['source_id'], 'logical_name': manifest['logical_name'],
            'sha256': manifest['raw_object'], 'original_source_object': manifest['original_source_object'], 'inputs': manifest['inputs'],
            'counts': facts['counts'], 'fidelity': facts['fidelity'], 'features': facts['features'], 'limitations': facts['limitations']}
    args = [request.snapshot_id]
    if request.collection == 'text':
        tokens = re.findall(r'\w+', request.query or '', re.UNICODE)
        if not 1 <= len(tokens) <= 32:
            raise LaneError('TABULAR_QUERY_TERMS_REQUIRED', 'Use one to thirty-two literal text search terms.')
        sql = (f'SELECT c.*,f.text_content,bm25({prefix}_chunk_fts) AS rank FROM {prefix}_chunk_fts f '
            f'JOIN {prefix}_chunk c ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id '
            f'WHERE c.snapshot_id=? AND {prefix}_chunk_fts MATCH ?')
        args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
        order = ' ORDER BY rank,c.item_id,c.ordinal'
        if request.part is not None:
            raise LaneError('TABULAR_TEXT_PART_FILTER_UNSUPPORTED', 'Use typed queries for an exact part filter.')
    else:
        sql = 'SELECT * FROM ' + LANE_TABLES[lane_id][request.collection] + ' WHERE snapshot_id=? AND kind=?'
        args.append(request.collection)
        if request.part is not None:
            sql += ' AND part=?'
            args.append(request.part)
        if request.query:
            sql += " AND instr(lower(json_extract(payload_json,'$.text')),lower(?))>0"
            args.append(request.query)
        order = ' ORDER BY part,ordinal,item_id'
    with lane.connection(read_only=True) as connection:
        rows = connection.execute(sql + order + ' LIMIT ? OFFSET ?', [*args, request.limit + 1, request.offset]).fetchall()
    items = {row['item_id']: row for row in facts['items']}
    values, used, truncated = [], 0, False
    for row in rows:
        item = items.get(row['item_id'])
        if item is None:
            raise LaneError('TABULAR_QUERY_INTEGRITY', 'The item is outside the selected immutable facts.')
        if request.collection == 'text':
            text = lane.read_object(row['text_object']).decode('utf-8')
            start = row['ordinal'] * 4096
            if (row['ordinal'] < 0 or text != item['text'][start:start + 4096] or text != row['text_content']
                    or row['chunk_id'] != digest(canonical_json_bytes([request.snapshot_id, row['item_id'], row['ordinal']]))):
                raise LaneError('TABULAR_QUERY_INTEGRITY', 'The indexed text differs from its immutable source facts.')
            value = {'item_id': item['item_id'], 'part': item['part'], 'kind': item['kind'], 'text': text,
                     'chunk_ordinal': row['ordinal'], 'rank': row['rank']}
        else:
            if json.loads(row['payload_json']) != item or (row['kind'], row['ordinal'], row['part']) != (item['kind'], item['ordinal'], item['part']):
                raise LaneError('TABULAR_QUERY_INTEGRITY', 'The typed row differs from its immutable source facts.')
            value = item
        size = len(canonical_json_bytes(value))
        if len(values) >= request.limit or used + size > request.max_bytes:
            truncated = True
            break
        values.append(value)
        used += size
    if truncated and not values:
        raise LaneError('TABULAR_QUERY_ITEM_TOO_LARGE', 'Increase max_bytes for the next complete item.')
    return {'snapshot_id': request.snapshot_id, 'rows': values, 'truncated': truncated,
        'next_offset': request.offset + len(values) if truncated else None, 'fidelity': facts['fidelity']}


def read(store, request):
    manifest, _ = read_snapshot(store, request.lane_id, request.snapshot_id)
    selected = manifest['original_source_object'] if request.representation == 'original_source' else manifest['raw_object']
    if selected is None:
        raise LaneError('TABULAR_ORIGINAL_SOURCE_ABSENT', 'This generated version has no original external source.')
    raw = store.lane(request.lane_id).read_object(selected)
    content = raw[request.offset:request.offset + request.max_bytes]
    end = request.offset + len(content)
    return {'snapshot_id': request.snapshot_id, 'logical_name': manifest['logical_name'], 'sha256': selected,
        'representation': request.representation, 'total_bytes': len(raw), 'offset': request.offset,
        'content_base64': base64.b64encode(content).decode('ascii'), 'next_offset': end if end < len(raw) else None}


def export(context, request):
    execution, lane_id = context.execution, request.lane_id
    store, prefix = execution.store, PREFIX[lane_id]
    check_calls(execution)
    manifest, _ = read_snapshot(store, lane_id, request.snapshot_id)
    relative = relative_path(request.filename)
    if PurePosixPath(relative).suffix.lower() != PurePosixPath(manifest['logical_name']).suffix.lower():
        raise LaneError('TABULAR_EXPORT_FORMAT_MISMATCH', 'Export preserves the versioned format; select conversion separately.')
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'write', path=path)
    reject_links(path, store.source_root)
    before_content = read_bytes(path) if path.is_file() else None
    before = digest(before_content) if before_content is not None else None
    if path.exists() and not path.is_file() or before != request.expected_sha256:
        raise LaneError('TABULAR_EXPORT_DESTINATION_CHANGED', 'Bind export to the exact destination hash or absence.')
    if not path.parent.is_dir():
        raise LaneError('TABULAR_EXPORT_PARENT_MISSING', 'Select an existing destination directory.')
    lane, export_id = store.lane(lane_id), str(uuid4())
    content = lane.read_object(manifest['raw_object'])
    if manifest['parser_contract'] != parser_contract(lane_id):
        raise LaneError('TABULAR_PARSER_CONTRACT_CHANGED', 'Refresh this development snapshot with the current parser before exporting.')
    destination_id = digest(canonical_json_bytes([store.project_id, lane_id, 'source', relative]))
    previous = current_snapshot(store, lane_id, destination_id)
    destination_manifest = read_snapshot(store, lane_id, previous)[0] if previous else None
    if destination_manifest and 'source_observation_route_id' not in destination_manifest:
        raise LaneError('TABULAR_REFRESH_PROVENANCE_REQUIRED', 'Refresh the destination snapshot with current source provenance before export.')
    limit = (destination_manifest or manifest).get('capture_limits', {}).get('max_file_bytes', MAX_FILE_BYTES)
    if len(content) > limit:
        raise LaneError('TABULAR_FILE_BYTE_BUDGET', 'The proposed export exceeds the selected destination intake bound.')
    from .artifact_contract import LaneArtifacts
    artifacts = LaneArtifacts(execution.guard.engine, store)
    view_id = lane_id + '.structure'
    view_selection = artifacts.current_selection(view_id)
    from .source_routing import SourceMutationRefresh
    source_refresh = SourceMutationRefresh(context, [relative], lane_id=lane_id,
        action=ACTION_PREFIX[lane_id] + '_export', max_file_bytes=MAX_FILE_BYTES, allow_create=True,
        parent_route_id=destination_manifest.get('source_observation_route_id') if destination_manifest else None)
    source_refresh.expected_after(path, before, content)
    check_calls(execution, 3 + int(bool(view_selection and view_selection['formats'])))
    parsed_content, parsed = worker(execution, 'tabular_parse_content', {'lane_id': lane_id,
        'filename': str(path), 'logical_name': relative, 'max_file_bytes': limit,
        'content_base64': base64.b64encode(content).decode('ascii')})
    execution.guard.observe(execution)
    if parsed_content != content or manifest['parser_contract'] != parser_contract(lane_id):
        raise LaneError('TABULAR_PARSER_CONTRACT_CHANGED', 'The proposed bytes or selected parser changed before export.')
    effect = execution.prepare_effect(lane_id + ':' + export_id, 'Export exact versioned bytes to one granted project file.')
    with execution.lease.coordinated_transaction([lane_id, 'receipts']):
        with lane.transaction() as connection:
            if before_content is not None:
                lane.put_object(before_content)
            connection.execute(f'INSERT INTO {prefix}_export VALUES(?,?,?,?,?,?,?)',
                (export_id, request.snapshot_id, relative, before, manifest['raw_object'], effect, now()))
        store.append_receipt('tabular_export_prepared', {'lane_id': lane_id, 'export_id': export_id,
            'snapshot_id': request.snapshot_id, 'destination': relative, 'before_sha256': before,
            'after_sha256': manifest['raw_object'], 'effect_id': effect})
    descriptor, temporary = tempfile.mkstemp(prefix='.evidence-lane-tabular-', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        execution._before_more_work()
        ProjectAccess(store).authorize(context.client_id, 'write', path=path)
        reject_links(path, store.source_root)
        observed = digest(read_bytes(path)) if path.is_file() else None
        if observed != before or path.exists() and not path.is_file():
            raise LaneError('TABULAR_EXPORT_DESTINATION_CHANGED', 'The destination changed immediately before export; reconcile this effect.')
        if before is None:
            if os.name == 'nt':
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
                temporary.unlink()
        else:
            os.chmod(temporary, path.stat().st_mode)
            os.replace(temporary, path)
        if read_bytes(path) != content:
            raise LaneError('TABULAR_EXPORT_VERIFY_FAILED', 'The written bytes were not confirmed.')
        with execution.lease.transaction('plan'):
            evidence = execution.plan_store.put_object(canonical_json_bytes({'export_id': export_id,
                'destination': relative, 'sha256': manifest['raw_object'], 'exact_bytes_verified': True}))
            execution.confirm_effect(effect, evidence)
    finally:
        if temporary.exists():
            temporary.unlink()
    with execution.lease.coordinated_transaction(['sources', lane_id, 'receipts']):
        source_result = source_refresh.publish(path=path, before_sha256=before, replacement=content,
            mutation_id=export_id, effect_id=effect)
        indexed = publish(context, lane_id=lane_id, source_id=destination_id, logical_name=relative,
            source_path=relative, origin='source', previous=previous, content=content, parsed=parsed,
            operation=ACTION_PREFIX[lane_id] + '_export', max_file_bytes=limit,
            source_observation_route_id=source_result['route_id'],
            inputs=[{'lane_id': lane_id, 'snapshot_id': request.snapshot_id, 'sha256': manifest['raw_object'], 'operation': 'export'}])
        view_result = artifacts.refresh_selected(view_id, view_selection, execution, actor_id=context.client_id)
        execution._before_more_work()
        final_capture = source_refresh.capture()
        from .source_authority import freeze_source_authority
        for spec in source_refresh.specs:
            freeze_source_authority(spec, capture=final_capture)
        if final_capture.identities != source_refresh.expected_after(path, before, content):
            raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'A source changed before the refreshed lanes could publish.')
    return result(store, lane_id, ACTION_PREFIX[lane_id] + '_export', {'export_id': export_id, 'snapshot_id': request.snapshot_id,
        'destination': relative, 'before_sha256': before, 'after_sha256': manifest['raw_object'], 'effect_id': effect,
        'source_bytes_mutated': True, 'source_index_refresh_required': False, 'automatic_replay': False,
        'index_refresh': indexed.model_dump(mode='json'), 'source_refresh': source_result, 'view_refresh': view_result})


def verify_export(context, request, output, *, engine=None):
    manifest, _ = read_snapshot(context.store, request.lane_id, request.snapshot_id)
    valid = digest(read_bytes(context.source_path(request.filename))) == manifest['raw_object'] == output.result['after_sha256']
    lane, prefix = context.store.lane(request.lane_id), PREFIX[request.lane_id]
    with lane.connection(read_only=True) as connection:
        exported = connection.execute(f'SELECT * FROM {prefix}_export WHERE export_id=?', (output.result['export_id'],)).fetchone()
    with context.store.lane('plan').connection(read_only=True) as connection:
        effect = connection.execute('SELECT * FROM jobs_effects WHERE effect_id=?', (output.result['effect_id'],)).fetchone()
    valid &= exported is not None and effect is not None and effect['job_id'] == context.job_id and effect['state'] == 'confirmed'
    if exported is not None and effect is not None:
        valid &= all(exported[key] == value for key, value in {'snapshot_id': request.snapshot_id,
            'destination': relative_path(request.filename), 'before_sha256': request.expected_sha256,
            'after_sha256': manifest['raw_object'], 'effect_id': output.result['effect_id']}.items())
        valid &= json.loads(context.store.lane('plan').read_object(effect['evidence_object'])) == {
            'export_id': output.result['export_id'], 'destination': relative_path(request.filename),
            'sha256': manifest['raw_object'], 'exact_bytes_verified': True}
    indexed = TabularResult.model_validate(output.result['index_refresh'])
    index_manifest, _ = read_snapshot(context.store, request.lane_id, indexed.result['snapshot_id'])
    expected_id = digest(canonical_json_bytes([context.store.project_id, request.lane_id, 'source', relative_path(request.filename)]))
    valid &= (indexed.lane_id == request.lane_id and index_manifest['source_id'] == expected_id
        and current_snapshot(context.store, request.lane_id, expected_id) == indexed.result['snapshot_id']
        and index_manifest['logical_name'] == index_manifest['source_path'] == relative_path(request.filename)
        and index_manifest['raw_object'] == manifest['raw_object'] and not output.result['source_index_refresh_required']
        and index_manifest['inputs'] == [{'lane_id': request.lane_id, 'snapshot_id': request.snapshot_id,
            'sha256': manifest['raw_object'], 'operation': 'export'}])
    valid &= all(row['passed'] for row in verify_snapshot(replace(context,
        requested_checks=('tabular_snapshot_integrity',)), request, indexed))
    source = output.result['source_refresh']
    prior = read_snapshot(context.store, request.lane_id, index_manifest['previous_snapshot'])[0] if index_manifest['previous_snapshot'] else None
    from .source_routing import verify_export_refresh
    valid &= (index_manifest['source_observation_route_id'] == source['route_id']
        and source['parent_route_id'] == (prior.get('source_observation_route_id') if prior else None)
        and verify_export_refresh(context, source, lane_id=request.lane_id,
            action=ACTION_PREFIX[request.lane_id] + '_export', mutation_id=output.result['export_id'],
            effect_id=output.result['effect_id'], max_file_bytes=MAX_FILE_BYTES))
    if engine is None:
        valid = False
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed(
            request.lane_id + '.structure', output.result['view_refresh'])
        valid &= view_valid and len(context.worker_evidence) == 1 + graph_workers and all(
            row['status'] == 'ok' for row in context.worker_evidence)
    return [{'check_id': name, 'passed': valid, 'evidence': {'export_id': output.result['export_id'],
        'after_sha256': manifest['raw_object']}} for name in context.requested_checks]


def register_tabular_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def export_route(lane_id, extensions, render):
        def applicable(context, request):
            from .artifact_contract import LaneArtifacts
            store = engine.directory.open(context.project_id)
            manifest, _ = read_snapshot(store, lane_id, request.snapshot_id)
            view = LaneArtifacts(engine, store).current_selection(lane_id + '.structure')
            return PurePosixPath(manifest['logical_name']).suffix.lower().lstrip('.') in extensions and bool(view and view['formats']) is render
        return applicable

    def verify_exported(context, request, output):
        return verify_export(context, request, output, engine=engine)

    for lane_id, prefix in ACTION_PREFIX.items():
        groups = {'data_excel': [('', ('xlsx', 'xlsm', 'xltx', 'xltm'), ('Python',)),
                                  ('_values', ('xls', 'xlsb', 'ods'), ('Python', 'python_calamine'))],
            'data': [('', ('csv', 'tsv', 'json', 'jsonl'), ('Python',)),
                     ('_arrow', ('parquet', 'arrow', 'feather'), ('Python', 'pyarrow'))]}[lane_id]
        for qualifier, extensions, tools in groups:
            for suffix, workflow in (('index', 'manage-project-sources'), ('refresh', 'refresh-project-evidence')):
                action = prefix + '_' + suffix + qualifier
                engine.registry.register(ActionSpec(action, 'Snapshot exact file bytes and bounded facts in the separate ' + lane_id + ' lane.',
                contracts.model_for(lane_id, contracts.Index, name=action.title().replace('_', '') + 'Request', extensions=extensions), TabularResult, index, permission='write', mutates=True,
                requires_delta=True, profile=prefix, workflow=workflow, path_fields=('filename',), source_lanes=(lane_id,),
                materialization=SourceMaterialization() if action == prefix + '_index' else None, worker_operations=('tabular_parse_file',),
                verification_checks=('tabular_snapshot_integrity', 'tabular_source_hash_unchanged'), verifier=verify_snapshot,
                tool_routes=(ToolRoute(action + '.native', index, tools,
                    systems=('Windows', 'Darwin', 'Linux')),)))
        def query_handler(function, operation):
            def handler(context, request):
                store = engine.directory.open(context.project_id)
                with project_snapshot(store.root):
                    return result(store, request.lane_id, operation, function(store, request))
            return handler
        for suffix, model, function in (('current', contracts.Selection, current), ('query', contracts.Query, query), ('read', contracts.Read, read)):
            action = prefix + '_' + suffix
            engine.registry.register(ActionSpec(action, 'Read bounded identities, typed facts or exact historical bytes from the ' + lane_id + ' lane.',
                contracts.model_for(lane_id, model), TabularResult, query_handler(function, action), profile=prefix,
                workflow='manage-project-sources', queryable_in_delta=True, cross_project_read=True, studio_read=True,
                read_migrations=tabular_migrations(lane_id),
                search=SearchRoute((lane_id,), 'rows', prefix + '_current', 'files', 'text', 'any', rerank_text='text') if suffix == 'query' else None,
                fetch=FetchRoute((lane_id,), 'snapshot') if suffix == 'read' else None))
        export_routes = tuple(ToolRoute(prefix + '_export.native' + qualifier + ('_view' if render else ''), export,
            (*tools, *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
            view_refresh=SelectedViewRefresh(engine, lane_id + '.structure'), applicable=export_route(lane_id, extensions, render),
            worker_operations=('tabular_parse_content', *(('render_lane_view',) if render else ())))
            for qualifier, extensions, tools in groups for render in (False, True))
        engine.registry.register(ActionSpec(prefix + '_export', 'Parse and export exact versioned bytes, then automatically refresh Sources, destination facts and existing lane views.',
            contracts.model_for(lane_id, contracts.Export), TabularResult, export, permission='write', mutates=True, requires_delta=True,
            profile=prefix, workflow='execute-project-plan', path_fields=('filename',), verification_checks=('tabular_export_hash_verified',),
            verifier=verify_exported, worker_operations=('tabular_parse_content', 'render_lane_view'), tool_routes=export_routes))
        model = {'data_excel': contracts.SpreadsheetGenerate, 'data': contracts.DataGenerate}[lane_id]
        tool_ids = {'data_excel': ('Python', 'openpyxl'), 'data': ('Python',)}[lane_id]
        engine.registry.register(ActionSpec(prefix + '_generate', 'Generate a new immutable lane-owned file under the format-specific contract.',
            contracts.model_for(lane_id, model), TabularResult, generate, permission='write', mutates=True, requires_delta=True,
            profile=prefix, workflow='execute-project-plan', worker_operations=(prefix + '_generate',),
            tool_routes=(ToolRoute(prefix + '_generate.native', generate, tool_ids,
                systems=('Windows', 'Darwin', 'Linux')),),
            verification_checks=('tabular_snapshot_integrity',), verifier=verify_snapshot))
    engine.registry.register(ActionSpec('spreadsheet_edit', 'Replace exact existing OpenXML cells while preserving other package members and invalidating stale formula caches.',
        contracts.model_for('data_excel', contracts.SpreadsheetEdit), TabularResult, edit, permission='write', mutates=True, requires_delta=True,
        profile='spreadsheet', workflow='execute-project-plan', worker_operations=('spreadsheet_edit',),
        tool_routes=(ToolRoute('spreadsheet_edit.native', edit, ('Python', 'lxml')),),
        verification_checks=('tabular_snapshot_integrity',), verifier=verify_snapshot))
    engine.registry.register(ActionSpec('data_transform', 'Select, filter, sort or explicitly cast a complete bounded dataset into a separately versioned result.',
        contracts.model_for('data', contracts.DataTransform), TabularResult, transform_data, permission='write', mutates=True,
        requires_delta=True, profile='data', workflow='execute-project-plan', worker_operations=('data_transform',),
        verification_checks=('tabular_snapshot_integrity',), verifier=verify_snapshot))
    from .tabular_views import register_tabular_views
    register_tabular_views(engine)
    from .spreadsheet_rendering import register_spreadsheet_rendering
    register_spreadsheet_rendering(engine)
    from .tabular_inspection import register_inspections
    register_inspections(engine)
