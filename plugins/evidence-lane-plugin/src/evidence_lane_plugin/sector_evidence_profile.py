"""V4 adapter for retained Research, Artifacts and Custom source facts."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path, PurePosixPath

from pydantic import Field, JsonValue

from .document_parsers import digest
from .errors import LaneError
from .hashing import canonical_json_bytes
from .lanes import CUSTOM_INSTANCE_PATTERN
from .migrations import apply_migrations, read_compatibility
from .plan_runtime import content_digest
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .sector_evidence_contracts import (
    MEDIA,
    RASTER,
    Index,
    Query,
    Read,
    Selection,
    model_for,
    parser_for,
)
from .sector_evidence_schema import PREFIX, TABLES, migrations
from .sector_evidence_workers import read_source
from .selector_schema import active_selector_sql
from .storage import json_text, now, project_snapshot
from .tabular_profile import natural_file
from .tool_routes import ToolRoute


class EvidenceResult(Contract):
    project_id: str
    lane_id: str = Field(pattern=r'^(?:research|artifacts|custom|' + CUSTOM_INSTANCE_PATTERN + r')$')
    operation: str
    result: dict[str, JsonValue]


def chunks(facts):
    for item in facts['items']:
        for ordinal, start in enumerate(range(0, len(item['text']), 4096)):
            yield item['item_id'], ordinal, item['text'][start:start + 4096]


def result(store, lane_id, operation, body):
    value = EvidenceResult(project_id=store.project_id, lane_id=lane_id, operation=operation, result=body)
    if len(canonical_json_bytes(value.model_dump(mode='json'))) > 2_097_152:
        raise LaneError('SECTOR_EVIDENCE_OUTPUT_BUDGET', 'Select a smaller evidence result.')
    return value


def relative(value):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path) or '\x00' in str(path) or str(path) == '.':
        raise LaneError('SECTOR_EVIDENCE_PATH_INVALID', 'Select one project-relative source file.')
    return path.as_posix()


def lane_if_present(store, lane_id):
    try:
        return store.lane(lane_id)
    except LaneError as error:
        if error.code != 'LANE_NOT_INITIALIZED':
            raise
        return None


def object_bytes(lane, identity, limit):
    with lane.connection(read_only=True) as connection:
        row = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (identity,)).fetchone()
    if row is None or row[0] > limit:
        raise LaneError('SECTOR_EVIDENCE_OBJECT_BUDGET', 'The selected evidence object is missing or exceeds its bound.')
    with lane.object_path(identity).open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) != row[0] or digest(content) != identity:
        raise LaneError('SECTOR_EVIDENCE_OBJECT_INTEGRITY', 'The evidence bytes differ from their registered object.')
    return content


def current_snapshot(store, lane_id, source_id):
    lane, prefix = lane_if_present(store, lane_id), PREFIX[lane_id]
    if lane is None:
        return None
    read_compatibility(lane, migrations(lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (prefix + '_current',)).fetchone():
            return None
        row = connection.execute(f'SELECT snapshot_id FROM {prefix}_current WHERE source_id=?', (source_id,)).fetchone()
    return row[0] if row else None


def read_snapshot(store, lane_id, snapshot_id):
    lane, prefix = lane_if_present(store, lane_id), PREFIX[lane_id]
    if lane is None:
        raise LaneError('SECTOR_EVIDENCE_SNAPSHOT_MISSING', 'Index the selected source in its owning lane first.')
    read_compatibility(lane, migrations(lane_id))
    with lane.connection(read_only=True) as connection:
        if not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (prefix + '_version',)).fetchone():
            raise LaneError('SECTOR_EVIDENCE_SNAPSHOT_MISSING', 'Index the selected source in its owning lane first.')
        row = connection.execute(f'SELECT * FROM {prefix}_version WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if row is None:
        raise LaneError('SECTOR_EVIDENCE_SNAPSHOT_MISSING', 'Select an exact snapshot from this evidence lane.')
    manifest = json.loads(object_bytes(lane, row['manifest_object'], 2_097_152))
    if (digest(canonical_json_bytes(manifest)) != snapshot_id or manifest['project_id'] != store.project_id
            or manifest['lane_id'] != lane_id or any(manifest[key] != row[key] for key in (
                'source_id', 'generation', 'previous_snapshot', 'raw_object', 'facts_object', 'parser_contract'))):
        raise LaneError('SECTOR_EVIDENCE_SNAPSHOT_INTEGRITY', 'The snapshot differs from its indexed identity.')
    facts = json.loads(object_bytes(lane, manifest['facts_object'], 8_388_608))
    if (facts['lane_id'] != lane_id or facts['source_sha256'] != manifest['raw_object']
            or facts['parser'] != manifest['parser'] or len(facts['items']) > 8192):
        raise LaneError('SECTOR_EVIDENCE_FACT_OWNER', 'The parsed evidence belongs to another source or lane.')
    identities = set()
    for item in facts['items']:
        if (item['kind'] not in TABLES[lane_id] or item['item_id'] in identities
                or item['item_id'] != digest(canonical_json_bytes([lane_id, item['kind'], item['part'], item['ordinal']]))):
            raise LaneError('SECTOR_EVIDENCE_FACT_IDENTITY', 'The parsed evidence has an invalid typed locator.')
        identities.add(item['item_id'])
    return manifest, facts


def parser_contract(parser):
    names = {'sector_evidence_profile.py', 'sector_evidence_parsers.py', 'sector_evidence_contracts.py', 'sector_evidence_workers.py',
        'selected_sqlite_parser.py', 'document_parsers.py', 'structured_data.py', 'tabular_values.py'}
    if parser == 'sqlite':
        names.update(('data_toolchain.py', 'sqlite_execution.py'))
    if parser == 'spreadsheet':
        names.update(('spreadsheet_workers.py', 'spreadsheet_parsers.py'))
    if parser == 'presentation':
        names.add('presentation_parsers.py')
    if parser.startswith('pdf_'):
        names.update(('pdf_parsers.py', 'pdf_forms.py'))
    if parser == 'media':
        names.update(('media_parsers.py', 'media_native.py', 'media_child.py', 'media_process.py'))
    return digest(canonical_json_bytes({name: digest(Path(__file__).with_name(name).read_bytes()) for name in sorted(names)}))


def index_native(context, request):
    return index(context, request, sqlite_schema_engine='stdlib')


def index(context, request, *, sqlite_schema_engine=None):
    from .custom_lanes import selected_adapter
    execution, store, lane_id = context.execution, context.execution.store, request.lane_id
    execution.guard.check()
    adapter = selected_adapter(store, request)
    if execution.guard.calls + 2 > execution.guard.task.budget.max_tool_calls:
        raise LaneError('DELTA_TOOL_BUDGET', 'Leave room for the selected parser and verification.')
    name, prefix = relative(request.filename), PREFIX[lane_id]
    path = execution.guard.path(name)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    source_id = digest(canonical_json_bytes([store.project_id, lane_id, 'source', name]))
    if current_snapshot(store, lane_id, source_id) != request.expected_snapshot:
        raise LaneError('SECTOR_EVIDENCE_SNAPSHOT_CHANGED', 'Bind intake to the exact current source snapshot.')
    prior = read_snapshot(store, lane_id, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior['source_route'] is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this routed input.')
    selected_parser = parser_for(name, request.parser)
    if sqlite_schema_engine is None:
        sqlite_schema_engine = 'sqlalchemy' if selected_parser == 'sqlite' else 'stdlib'
    contract = parser_contract(selected_parser)
    raw = read_source(path, request.max_file_bytes)
    response = execution.submit('sector_evidence_parse_file', {'lane_id': lane_id, 'filename': str(path),
        'logical_name': name, 'parser': request.parser, 'max_file_bytes': request.max_file_bytes,
        'sqlite_tables': request.sqlite_tables, 'sqlite_rows': request.sqlite_rows,
        'sqlite_schema_engine': sqlite_schema_engine}).result()
    if response['status'] != 'ok':
        raise LaneError('SECTOR_EVIDENCE_WORKER_FAILED', 'The selected source parser did not complete.')
    parsed = response['result']
    if (parsed['lane_id'] != lane_id or parsed['filename'] != name or parsed['sha256'] != digest(raw)
            or parsed['bytes'] != len(raw) or base64.b64decode(parsed['content_base64'], validate=True) != raw
            or parsed['facts']['lane_id'] != lane_id or parsed['facts']['source_sha256'] != digest(raw)
            or parsed['facts']['parser'] != selected_parser or parser_contract(selected_parser) != contract
            or parsed['evidence']['sqlite_schema_engine'] != sqlite_schema_engine
            or read_source(path, request.max_file_bytes) != raw):
        raise LaneError('SECTOR_EVIDENCE_WORKER_BINDING', 'The parser or source changed during this operation.')
    facts = parsed['facts']
    if any(item['kind'] not in TABLES[lane_id] for item in facts['items']):
        raise LaneError('SECTOR_EVIDENCE_FACT_OWNER', 'The parser returned facts outside its lane contract.')
    stamp, generation = now(), prior['generation'] + 1 if prior else 1
    selection = execution.guard.source_route.selection.model_dump(mode='json') if execution.guard.source_route else None
    operation = execution.guard.spec.name
    manifest = {'schema': 'evidence-lane.sector-evidence-snapshot.v4', 'project_id': store.project_id,
        'lane_id': lane_id, 'source_id': source_id, 'logical_name': name, 'source_path': name,
        'generation': generation, 'previous_snapshot': request.expected_snapshot, 'created_at': stamp,
        'raw_object': digest(raw), 'facts_object': digest(canonical_json_bytes(facts)), 'bytes': len(raw),
        'parser': selected_parser, 'parser_contract': contract, 'operation': operation,
        'source_route': selection, 'source_observation_route_id': selection['route_id'] if selection else None,
        'capture_limits': {'max_file_bytes': request.max_file_bytes},
        'parse_options': {'parser': request.parser, 'sqlite_tables': request.sqlite_tables, 'sqlite_rows': request.sqlite_rows,
            'sqlite_schema_engine': sqlite_schema_engine},
        'worker_envelope': {key: value for key, value in response.items() if key != 'result'},
        'worker_fields': {key: value for key, value in parsed.items() if key not in {'content_base64', 'facts'}},
        'worker_digest': content_digest(response), 'job_id': execution.claim.job_id,
        'task_id': execution.task_id, 'plan_revision': context.expected_revision, 'source_bytes_mutated': False}
    snapshot_id = digest(canonical_json_bytes(manifest))
    if adapter is not None:
        manifest['adapter_contract'] = adapter
        snapshot_id = digest(canonical_json_bytes(manifest))
    execution._before_more_work()
    if selected_adapter(store, request) != adapter:
        raise LaneError('CUSTOM_LANE_CONTRACT_CHANGED', 'The adapter changed before publication.')
    with execution.lease.coordinated_transaction([lane_id, 'receipts']):
        lane = store.lane(lane_id)
        apply_migrations(lane, migrations(lane_id), writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute(f'SELECT snapshot_id FROM {prefix}_current WHERE source_id=?', (source_id,)).fetchone()
            if (live[0] if live else None) != request.expected_snapshot or read_source(path, request.max_file_bytes) != raw:
                raise LaneError('SECTOR_EVIDENCE_SNAPSHOT_CHANGED', 'The selected source changed before publication.')
            lane.put_object(raw)
            lane.put_object(canonical_json_bytes(facts))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute(f'INSERT OR IGNORE INTO {prefix}_file VALUES(?,?,?,?)', (source_id, name, name, stamp))
            connection.execute(f'INSERT INTO {prefix}_version VALUES(?,?,?,?,?,?,?,?,?)',
                (snapshot_id, source_id, generation, request.expected_snapshot, manifest_object,
                    manifest['raw_object'], manifest['facts_object'], contract, stamp))
            for item in facts['items']:
                connection.execute('INSERT INTO ' + TABLES[lane_id][item['kind']] + ' VALUES(?,?,?,?,?,?)',
                    (snapshot_id, item['item_id'], item['kind'], item['ordinal'], item['part'], json_text(item)))
            for item_id, ordinal, text in chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                connection.execute(f'INSERT INTO {prefix}_chunk VALUES(?,?,?,?,?)',
                    (snapshot_id, chunk_id, item_id, ordinal, lane.put_object(text.encode())))
                connection.execute(f'INSERT INTO {prefix}_chunk_fts VALUES(?,?,?)', (snapshot_id, chunk_id, text))
            natural_path = natural_file(lane, snapshot_id, PurePosixPath(name).name, raw)
            connection.execute(f'INSERT INTO {prefix}_current VALUES(?,?) ON CONFLICT(source_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                (source_id, snapshot_id))
            receipt_id = store.append_receipt('sector_evidence_snapshot', {'project_id': store.project_id,
                'lane_id': lane_id, 'snapshot_id': snapshot_id, 'source_id': source_id,
                'previous_snapshot': request.expected_snapshot, 'job_id': execution.claim.job_id,
                'task_id': execution.task_id, 'plan_revision': context.expected_revision,
                'worker_digest': manifest['worker_digest'], 'operation': operation, 'source_bytes_mutated': False})
    return result(store, lane_id, operation, {'snapshot_id': snapshot_id, 'source_id': source_id,
        'generation': generation, 'previous_snapshot': request.expected_snapshot, 'sha256': digest(raw),
        'bytes': len(raw), 'logical_name': name, 'natural_path': natural_path, 'receipt_id': receipt_id,
        'parser': selected_parser, 'source_bytes_mutated': False, 'fidelity': facts['fidelity'],
        'limitations': facts['limitations']})


def verify(context, request, output):
    from .custom_lanes import selected_adapter
    store, lane_id = context.store, request.lane_id
    snapshot_id, prefix = output.result['snapshot_id'], PREFIX[lane_id]
    manifest, facts = read_snapshot(store, lane_id, snapshot_id)
    schema_engine = manifest['parse_options'].get('sqlite_schema_engine')
    adapter_valid = manifest.get('adapter_contract') == selected_adapter(store, request)
    lane = store.lane(lane_id)
    raw = object_bytes(lane, manifest['raw_object'], request.max_file_bytes)
    name = relative(request.filename)
    response = {**manifest['worker_envelope'], 'result': {**manifest['worker_fields'],
        'content_base64': base64.b64encode(raw).decode('ascii'), 'facts': facts}}
    source_id = digest(canonical_json_bytes([store.project_id, lane_id, 'source', name]))
    valid = (adapter_valid and output.project_id == store.project_id and output.lane_id == lane_id
        and output.operation == manifest['operation'] and manifest['source_path'] == name
        and manifest['logical_name'] == name and manifest['source_id'] == source_id
        and manifest['previous_snapshot'] == request.expected_snapshot
        and current_snapshot(store, lane_id, source_id) == snapshot_id
        and manifest['parser'] == parser_for(name, request.parser)
        and manifest['parser_contract'] == parser_contract(manifest['parser'])
        and schema_engine in {'stdlib', 'sqlalchemy'}
        and manifest['parse_options'] == {'parser': request.parser, 'sqlite_tables': request.sqlite_tables, 'sqlite_rows': request.sqlite_rows,
            'sqlite_schema_engine': schema_engine}
        and manifest['worker_fields']['evidence']['sqlite_schema_engine'] == schema_engine
        and manifest['capture_limits'] == {'max_file_bytes': request.max_file_bytes}
        and manifest['bytes'] == len(raw) and output.result['sha256'] == digest(raw)
        and output.result['source_id'] == source_id and output.result['logical_name'] == name
        and manifest['job_id'] == context.job_id and manifest['task_id'] == context.task_id
        and manifest['plan_revision'] == context.plan_revision
        and content_digest(response) == manifest['worker_digest']
        and list(context.worker_evidence) == [{'digest': manifest['worker_digest'], 'status': 'ok',
            'worker_pid': manifest['worker_envelope']['worker_pid']}])
    expected_natural = lane.files / 'natural' / snapshot_id / PurePosixPath(name).name
    valid &= (str(expected_natural) == output.result['natural_path']
        and read_source(expected_natural, request.max_file_bytes) == raw)
    expected_output = {'snapshot_id': snapshot_id, 'source_id': source_id,
        'generation': manifest['generation'], 'previous_snapshot': request.expected_snapshot,
        'sha256': digest(raw), 'bytes': len(raw), 'logical_name': name, 'natural_path': str(expected_natural),
        'receipt_id': output.result['receipt_id'], 'parser': manifest['parser'], 'source_bytes_mutated': False,
        'fidelity': facts['fidelity'], 'limitations': facts['limitations']}
    valid &= output.result == expected_output
    plan_lane = store.lane('plan')
    with plan_lane.connection(read_only=True) as connection:
        admitted = connection.execute('SELECT entry_object FROM delta_runs WHERE job_id=?', (context.job_id,)).fetchone()
    if admitted is None:
        valid = False
    else:
        admission = json.loads(object_bytes(plan_lane, admitted['entry_object'], 2_097_152))
        entry = admission['entry']
        valid &= (entry['action'] == output.operation and entry['task_id'] == context.task_id
            and entry['plan_revision'] == context.plan_revision
            and type(request).model_validate(entry['arguments']) == request
            and entry['source_route'] == manifest['source_route'])
        expected_engine = 'sqlalchemy' if admission['tool_admission']['route_id'].endswith('.sqlite_sqlalchemy') else 'stdlib'
        valid &= schema_engine == expected_engine
        if schema_engine == 'sqlalchemy':
            reflection = facts['fidelity']['schema_reflection']
            valid &= (manifest['parser'] == 'sqlite' and reflection['engine'] == 'SQLAlchemy'
                and reflection['source_sha256'] == digest(raw)
                and bool(manifest['worker_fields']['evidence']['versions'].get('sqlalchemy')))
    with lane.connection(read_only=True) as connection:
        for table in sorted(set(TABLES[lane_id].values())):
            rows = connection.execute('SELECT * FROM ' + table + ' WHERE snapshot_id=? ORDER BY item_id', (snapshot_id,)).fetchall()
            expected = sorted((item for item in facts['items'] if TABLES[lane_id][item['kind']] == table), key=lambda item: item['item_id'])
            valid &= ([json.loads(row['payload_json']) for row in rows] == expected and all(
                (row['item_id'], row['kind'], row['ordinal'], row['part']) ==
                (item['item_id'], item['kind'], item['ordinal'], item['part']) for row, item in zip(rows, expected)))
        rows = connection.execute(f'SELECT c.*,f.text_content FROM {prefix}_chunk c JOIN {prefix}_chunk_fts f '
            'ON c.snapshot_id=f.snapshot_id AND c.chunk_id=f.chunk_id WHERE c.snapshot_id=?', (snapshot_id,)).fetchall()
        expected = {(item_id, ordinal): text for item_id, ordinal, text in chunks(facts)}
        valid &= len(rows) == len(expected)
        for row in rows:
            text = expected.get((row['item_id'], row['ordinal']))
            valid &= (text is not None and text == row['text_content']
                and object_bytes(lane, row['text_object'], 16_384).decode() == text
                and row['chunk_id'] == digest(canonical_json_bytes([snapshot_id, row['item_id'], row['ordinal']])))
    expected_receipt = {'project_id': store.project_id, 'lane_id': lane_id, 'snapshot_id': snapshot_id,
        'source_id': source_id, 'previous_snapshot': request.expected_snapshot, 'job_id': context.job_id,
        'task_id': context.task_id, 'plan_revision': context.plan_revision, 'worker_digest': manifest['worker_digest'],
        'operation': output.operation, 'source_bytes_mutated': False}
    with store.lane('receipts').connection(read_only=True) as connection:
        row = connection.execute("SELECT body_json FROM receipts WHERE receipt_id=? AND kind='sector_evidence_snapshot'",
            (output.result['receipt_id'],)).fetchone()
    valid &= row is not None and len(row[0].encode()) <= 65_536 and json.loads(row[0]) == expected_receipt
    if manifest['source_route'] is not None:
        from .source_routing import load_route
        route = load_route(store, manifest['source_route']['route_id'])
        valid &= manifest['source_observation_route_id'] == manifest['source_route']['route_id'] and all(
            item['lane_id'] == lane_id for item in route['routes'] if item['ordinal'] in manifest['source_route']['occurrence_ordinals'])
    source_current = read_source(context.source_path(request.filename), request.max_file_bytes) == raw
    checks = {'sector_evidence_snapshot_integrity': valid, 'sector_evidence_source_hash_unchanged': source_current}
    return [{'check_id': name, 'passed': checks[name], 'evidence': {'snapshot_id': snapshot_id,
        'lane_id': lane_id, 'source_bytes_mutated': False, 'source_assertions_validated': False}} for name in context.requested_checks]


def current(store, request):
    lane, prefix = lane_if_present(store, request.lane_id), PREFIX[request.lane_id]
    if lane is None:
        return {'files': [], 'initialized': False}
    read_compatibility(lane, migrations(request.lane_id))
    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (prefix + '_current',)).fetchone():
            return {'files': [], 'initialized': False}
        rows = connection.execute(f'SELECT v.*,f.logical_name,f.source_path FROM {prefix}_current c '
            f'JOIN {prefix}_version v USING(snapshot_id) JOIN {prefix}_file f ON f.source_id=c.source_id '
            f'WHERE {active} ORDER BY v.created_at DESC LIMIT 129').fetchall()
    files = [dict(row) for row in rows]
    if request.lane_id == 'research':
        from .research_discovery_profile import current as discovery_current
        from .research_web_profile import current as web_current
        files.extend(web_current(store))
        files.extend(discovery_current(store))
        files.sort(key=lambda row: (row['created_at'], row['snapshot_id']), reverse=True)
    return {'files': files[:128], 'truncated': len(files) > 128,
        'initialized': True, 'source_currentness': 'not_checked_by_metadata_read'}


def query(store, request):
    if request.lane_id == 'research':
        from .research_discovery_profile import present as discovery_present
        from .research_discovery_profile import query as discovery_query
        from .research_web_profile import present
        from .research_web_profile import query as web_query
        if present(store, request.snapshot_id):
            return web_query(store, request)
        if discovery_present(store, request.snapshot_id):
            return discovery_query(store, request)
    lane_id, prefix = request.lane_id, PREFIX[request.lane_id]
    manifest, facts = read_snapshot(store, lane_id, request.snapshot_id)
    return query_facts(store, request, manifest, facts, prefix, TABLES[lane_id])


def query_facts(store, request, manifest, facts, prefix, tables):
    lane_id = request.lane_id
    if request.collection == 'metadata':
        value = {'snapshot_id': request.snapshot_id, 'source_id': manifest['source_id'],
            'logical_name': manifest['logical_name'], 'sha256': manifest['raw_object'],
            'parser': manifest['parser'], 'fidelity': facts['fidelity'], 'limitations': facts['limitations']}
        if len(canonical_json_bytes(value)) > request.max_bytes:
            raise LaneError('SECTOR_EVIDENCE_QUERY_ITEM_BUDGET', 'Increase max_bytes for the complete metadata record.')
        return value
    lane, args = store.lane(lane_id), [request.snapshot_id]
    if request.collection == 'text':
        tokens = re.findall(r'\w+', request.query or '', re.UNICODE)
        if not 1 <= len(tokens) <= 32:
            raise LaneError('SECTOR_EVIDENCE_QUERY_TERMS', 'Use one to thirty-two literal search terms.')
        sql = (f'SELECT c.*,f.text_content,bm25({prefix}_chunk_fts) AS rank FROM {prefix}_chunk_fts f '
            f'JOIN {prefix}_chunk c ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id '
            f'WHERE c.snapshot_id=? AND {prefix}_chunk_fts MATCH ?')
        args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
        order = ' ORDER BY rank,c.item_id,c.ordinal'
    else:
        if request.collection not in tables:
            raise LaneError('SECTOR_EVIDENCE_COLLECTION', 'Select a typed collection owned by this lane.')
        sql = 'SELECT * FROM ' + tables[request.collection] + ' WHERE snapshot_id=? AND kind=?'
        args.append(request.collection)
        if request.query:
            sql += " AND instr(lower(json_extract(payload_json,'$.text')),lower(?))>0"
            args.append(request.query)
        order = ' ORDER BY part,ordinal,item_id'
    with lane.connection(read_only=True) as connection:
        rows = connection.execute(sql + order + ' LIMIT ? OFFSET ?', [*args, request.limit + 1, request.offset]).fetchall()
    items = {item['item_id']: item for item in facts['items']}
    values, used, truncated = [], 0, False
    for row in rows:
        item = items.get(row['item_id'])
        if item is None:
            raise LaneError('SECTOR_EVIDENCE_QUERY_INTEGRITY', 'The query row is outside its immutable facts.')
        if request.collection == 'text':
            text = object_bytes(lane, row['text_object'], 16_384).decode()
            start = row['ordinal'] * 4096
            if (row['ordinal'] < 0 or text != item['text'][start:start + 4096] or text != row['text_content']
                    or row['chunk_id'] != digest(canonical_json_bytes([request.snapshot_id, row['item_id'], row['ordinal']]))):
                raise LaneError('SECTOR_EVIDENCE_QUERY_INTEGRITY', 'The indexed text differs from its immutable source facts.')
            value = {'item_id': item['item_id'], 'part': item['part'], 'kind': item['kind'], 'text': text,
                'chunk_ordinal': row['ordinal'], 'rank': row['rank']}
        else:
            if json.loads(row['payload_json']) != item or (row['kind'], row['ordinal'], row['part']) != (item['kind'], item['ordinal'], item['part']):
                raise LaneError('SECTOR_EVIDENCE_QUERY_INTEGRITY', 'The typed row differs from its immutable source facts.')
            value = item
        size = len(canonical_json_bytes(value))
        if len(values) >= request.limit or used + size > request.max_bytes:
            truncated = True
            break
        values.append(value)
        used += size
    if truncated and not values:
        raise LaneError('SECTOR_EVIDENCE_QUERY_ITEM_BUDGET', 'Increase max_bytes for the next complete item.')
    return {'snapshot_id': request.snapshot_id, 'rows': values, 'truncated': truncated,
        'next_offset': request.offset + len(values) if truncated else None, 'fidelity': facts['fidelity']}


def read(store, request):
    if request.lane_id == 'research':
        from .research_discovery_profile import present as discovery_present
        from .research_discovery_profile import read as discovery_read
        from .research_web_profile import present
        from .research_web_profile import read as web_read
        if present(store, request.snapshot_id):
            return web_read(store, request)
        if discovery_present(store, request.snapshot_id):
            return discovery_read(store, request)
    manifest, _ = read_snapshot(store, request.lane_id, request.snapshot_id)
    raw = object_bytes(store.lane(request.lane_id), manifest['raw_object'], 8_388_608)
    content = raw[request.offset:request.offset + request.max_bytes]
    end = request.offset + len(content)
    return {'snapshot_id': request.snapshot_id, 'logical_name': manifest['logical_name'],
        'sha256': manifest['raw_object'], 'representation': request.representation,
        'total_bytes': len(raw), 'offset': request.offset, 'content_base64': base64.b64encode(content).decode(),
        'next_offset': end if end < len(raw) else None}


def route_group(request):
    parser = parser_for(request.filename, request.parser)
    suffix = PurePosixPath(request.filename).suffix.lower()
    if parser == 'presentation':
        return 'presentation'
    if parser == 'spreadsheet' and suffix in {'.xls', '.xlsb', '.ods'}:
        return 'calamine'
    if parser == 'data' and suffix in {'.parquet', '.arrow', '.feather'}:
        return 'arrow'
    if parser.startswith('pdf_'):
        return parser
    if parser == 'media':
        return 'raster' if suffix in RASTER else 'vector' if suffix == '.svg' else 'media' if suffix in MEDIA else 'unsupported'
    return 'native'


def format_contract(lane_id):
    from .sector_evidence_contracts import DATA, DOCUMENT, PRESENTATIONS, SHEETS, SQLITE, TEXT
    return {'lane_id': lane_id, 'operations': ['index', 'refresh', 'current', 'literal_query', 'historical_bytes'],
        'native_sources': sorted(DATA | DOCUMENT | PRESENTATIONS | SHEETS | SQLITE | TEXT | {'.json', '.jsonl', '.ipynb', '.zip', '.pdf'}),
        'named_custom_instances': {'lane_id_pattern': CUSTOM_INSTANCE_PATTERN, 'registration_owner': 'sources',
            'configure_action': 'custom_lane_configure', 'read_action': 'custom_lanes_read',
            'parser_contract_selection': 'exact_current_adapter_contract_in_task_arguments',
            'schema_and_file_ownership': 'one_independent_database_history_and_files_per_instance',
            'global_registry_mutated': False, 'optional_connector_grants_are_separate': True} if lane_id == 'custom' else None,
        'media_sources': sorted(RASTER | MEDIA | {'.svg'}), 'unknown_formats': 'exact_bytes_and_explicit_review_state',
        'source_labels': 'original_deterministic_labels_with_unvalidated_assertion_status',
        'database': 'separate_owning_lane_sqlite', 'limits': {'source_bytes': 8_388_608, 'facts_bytes': 8_388_608,
            'text_bytes': 2_097_152, 'fact_items': 8192, 'sqlite_selected_rows': 2000},
        'selected_sqlite': 'complete_DELETE_journal_image_fixed_reads_no_imported_SQL_execution',
        'sqlite_schema_reflection': 'ordered_SQLAlchemy_private_image_then_native_metadata_before_invocation',
        'network_fetch_implemented': lane_id == 'research', 'network_discovery_implemented': lane_id == 'research',
        'offline_web_extractors': ['trafilatura', 'readability', 'beautifulsoup', 'markdownify', 'html2text', 'stdlib'] if lane_id == 'research' else [],
        'notebook_code_executed': False,
        'archive_members_extracted': False, 'source_bytes_mutated': False,
        'source_family_native_fidelity': 'retained_with_each_actual_parser_result',
        'retained_original_fact_tables': sorted(TABLES[lane_id]), 'full_lane_workflow_qualified': False}


def register_evidence_sector_actions(engine):
    from .custom_lanes import register_custom_lane_actions
    register_custom_lane_actions(engine)
    groups = {'sqlite_sqlalchemy': ('Python', 'SQLAlchemy'), 'native': ('Python',), 'calamine': ('Python', 'python_calamine'),
        'presentation': ('Python', 'PPTX_OpenXML'),
        'arrow': ('Python', 'pyarrow'), 'pdf_pymupdf': ('Python', 'pypdf', 'PyMuPDF'),
        'pdf_pypdf': ('Python', 'pypdf'), 'pdf_pdfplumber': ('Python', 'pypdf', 'pdfplumber'),
        'raster': ('Python', 'Pillow'), 'vector': ('Python', 'defusedxml'), 'media': ('Python', 'FFmpeg')}

    def applies(group):
        def applicable(_context, request):
            if group == 'sqlite_sqlalchemy':
                return parser_for(request.filename, request.parser) == 'sqlite'
            return route_group(request) == group
        return applicable

    def reader(function, operation):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, request.lane_id, operation, function(store, request))
        return handler

    for lane_id in PREFIX:
        for media in (False, True):
            selected_groups = {key: tools for key, tools in groups.items() if (key in {'raster', 'vector', 'media'}) is media}
            for suffix, workflow in (('index', 'source-intake'), ('refresh', 'refresh')):
                action = lane_id + '_' + suffix + ('_media' if media else '')
                engine.registry.register(ActionSpec(action,
                    'Index exact selected source bytes and attributed bounded facts in the separate ' + lane_id + ' lane.',
                    model_for(lane_id, Index), EvidenceResult, index, permission='write', mutates=True,
                    requires_delta=True, profile=lane_id, workflow=workflow, path_fields=('filename',), source_lanes=(lane_id,),
                    materialization=SourceMaterialization() if suffix == 'index' else None,
                    worker_operations=('sector_evidence_parse_file',),
                    verification_checks=('sector_evidence_snapshot_integrity', 'sector_evidence_source_hash_unchanged'), verifier=verify,
                    tool_routes=tuple(ToolRoute(action + '.' + group,
                        index_native if group == 'native' else index, tools, applicable=applies(group))
                        for group, tools in selected_groups.items())))
        for suffix, model, function in (('current', Selection, current), ('query', Query, query), ('read', Read, read)):
            action = lane_id + '_' + suffix
            engine.registry.register(ActionSpec(action,
                'Read bounded current identities, literal search, typed facts or historical bytes in the ' + lane_id + ' lane.',
                model_for(lane_id, model), EvidenceResult, reader(function, action), profile=lane_id,
                workflow='source-intake', queryable_in_delta=True, cross_project_read=True, studio_read=True,
                read_migrations=migrations(lane_id),
                search=SearchRoute((lane_id,), 'rows', lane_id + '_current', 'files', 'text', 'any', rerank_text='text') if suffix == 'query' else None,
                fetch=FetchRoute((lane_id,), 'snapshot') if suffix == 'read' else None))
