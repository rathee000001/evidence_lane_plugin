"""Tableau versions in their own lane, with separate journaled file export."""
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

from pydantic import JsonValue

from .errors import LaneError
from .hashing import canonical_json_bytes
from .migrations import apply_migrations, read_compatibility
from .projects import ProjectAccess
from .registry import ActionSpec, Contract, FetchRoute, SearchRoute, SourceMaterialization
from .selector_schema import active_selector_sql
from .storage import bounded_project_read, json_text, now, project_snapshot, reject_links
from .tableau_contracts import (
    TableauEdit,
    TableauExport,
    TableauGenerate,
    TableauIndex,
    TableauQuery,
    TableauRead,
    TableauSelection,
)
from .tableau_parsers import EXTENSIONS, digest, parse_tableau
from .tableau_schema import TABLEAU_MIGRATIONS, TABLEAU_TABLES
from .tool_routes import ToolRoute


def _parser_contract():
    from . import (
        tableau_authoring,
        tableau_contracts,
        tableau_hyper,
        tableau_hyper_child,
        tableau_parsers,
        tableau_workers,
    )
    return digest(canonical_json_bytes({Path(module.__file__).name: digest(Path(module.__file__).read_bytes())
        for module in (tableau_authoring, tableau_contracts, tableau_hyper, tableau_hyper_child, tableau_parsers, tableau_workers)}))


def _chunks(facts):
    for item in facts['items']:
        for ordinal, start in enumerate(range(0, len(item['text']), 4096)):
            yield item['item_id'], ordinal, item['text'][start:start + 4096]


def index_tableau(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() not in EXTENSIONS:
        raise LaneError('TABLEAU_FORMAT_UNSUPPORTED', 'Select a Tableau workbook, data source, package or extract.')
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'read', path=path)
    tableau_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    if current_snapshot(store, tableau_id) != request.expected_snapshot:
        raise LaneError('TABLEAU_SNAPSHOT_CHANGED', 'Refresh requires the exact current Tableau snapshot.')
    prior = read_snapshot(store, request.expected_snapshot)[0] if request.expected_snapshot else None
    if prior and prior.get('source_route') is not None and execution.guard.source_route is None:
        raise LaneError('SOURCE_ROUTE_SELECTION_REQUIRED', 'Select the refreshed Sources route for this previously routed input.')
    content, parsed = _worker(execution, 'tableau_parse_file', {'filename': str(path), 'logical_name': relative,
        'max_file_bytes': request.max_file_bytes, 'inspect_extracts': request.inspect_extracts,
        'max_rows_per_table': request.max_rows_per_table})
    if _bytes(path, request.max_file_bytes) != content:
        raise LaneError('TABLEAU_SOURCE_CHANGED', 'The source changed during extraction; select its current bytes.')
    return publish(context, tableau_id=tableau_id, logical_name=relative, source_path=relative, origin='source',
        previous=request.expected_snapshot, content=content, parsed=parsed, operation='tableau_index', source_content=content, max_file_bytes=request.max_file_bytes, intake=True)


def edit_tableau(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, facts = read_snapshot(store, request.snapshot_id)
    if manifest['raw_object'] != request.expected_sha256 or current_snapshot(store, manifest['tableau_id']) != request.snapshot_id:
        raise LaneError('TABLEAU_EDIT_SNAPSHOT_CHANGED', 'Edit the exact current Tableau bytes.')
    content, parsed = _worker(execution, 'tableau_edit', {'logical_name': manifest['logical_name'],
        'content_base64': base64.b64encode(store.lane('tableau').read_object(manifest['raw_object'])).decode('ascii'),
        'expected_sha256': request.expected_sha256, 'parse_options': facts['parse_options'],
        'replacements': [row.model_dump(mode='json') for row in request.replacements]})
    return publish(context, tableau_id=manifest['tableau_id'], logical_name=manifest['logical_name'],
        source_path=manifest['source_path'], origin=manifest['origin'], previous=request.snapshot_id,
        content=content, parsed=parsed, operation='tableau_edit')


def read_tableau(store, request):
    manifest, _ = read_snapshot(store, request.snapshot_id)
    selected = manifest['source_object'] if request.representation == 'original_source' else manifest['raw_object']
    if selected is None:
        raise LaneError('TABLEAU_ORIGINAL_SOURCE_ABSENT', 'This generated extract has no separate original source.')
    raw = store.lane('tableau').read_object(selected)
    name = manifest['source_path'] if request.representation == 'original_source' else manifest['logical_name']
    if request.representation == 'member':
        from .tableau_parsers import package_members
        if PurePosixPath(name).suffix.lower() not in {'.twbx', '.tdsx'}:
            raise LaneError('TABLEAU_MEMBER_REQUIRES_PACKAGE', 'Select a packaged Tableau snapshot.')
        members = package_members(raw)
        if request.member not in members:
            raise LaneError('TABLEAU_MEMBER_MISSING', 'Select an exact admitted package member.')
        raw, name = members[request.member], request.member
        selected = digest(raw)
    value = raw[request.offset:request.offset + request.max_bytes]
    end = request.offset + len(value)
    return {'snapshot_id': request.snapshot_id, 'logical_name': name, 'sha256': selected,
        'representation': request.representation, 'total_bytes': len(raw), 'offset': request.offset,
        'content_base64': base64.b64encode(value).decode('ascii'),
        'next_offset': end if end < len(raw) else None, 'source_bytes_mutated': False}


def register_tableau_actions(engine):
    from .artifact_contract import SelectedViewRefresh

    def export_route(render):
        def applicable(context, request):
            from .artifact_contract import LaneArtifacts
            store = engine.directory.open(context.project_id)
            view = LaneArtifacts(engine, store).current_selection('tableau.structure')
            return bool(view and view['formats']) is render
        return applicable

    def verify_exported(context, request, output):
        return verify_export(context, request, output, engine=engine)

    for action, workflow in (('tableau_index', 'source-intake'), ('tableau_refresh', 'refresh')):
        engine.registry.register(ActionSpec(action, 'Snapshot Tableau XML, package members and bounded native Hyper samples in the separate Tableau lane.',
            TableauIndex, TableauResult, index_tableau, permission='write', mutates=True, requires_delta=True,
            profile='tableau', workflow=workflow, path_fields=('filename',), source_lanes=('tableau',),
            materialization=SourceMaterialization() if action == 'tableau_index' else None, worker_operations=('tableau_parse_file',),
            verification_checks=('tableau_snapshot_integrity', 'tableau_source_hash_unchanged'), verifier=verify_tableau,
            tool_routes=(ToolRoute(action + '.native', index_tableau, ('Python', 'lxml', 'Tableau_Hyper_API'), systems=('Windows',)),)))
    for action, model, handler, worker in (('tableau_generate', TableauGenerate, generate_tableau, 'tableau_generate'),
            ('tableau_edit', TableauEdit, edit_tableau, 'tableau_edit')):
        engine.registry.register(ActionSpec(action, 'Publish a new native Hyper extract or an exact selected XML edit as immutable lane-owned bytes.',
            model, TableauResult, handler, permission='write', mutates=True, requires_delta=True,
            profile='tableau', workflow='build', worker_operations=(worker,),
            verification_checks=('tableau_snapshot_integrity',), verifier=verify_tableau,
            tool_routes=(ToolRoute(action + '.native', handler, ('Python', 'lxml', 'Tableau_Hyper_API'), systems=('Windows',)),)))

    def query_handler(function, action):
        def handler(context, request):
            store = engine.directory.open(context.project_id)
            with project_snapshot(store.root):
                return result(store, action, function(store, request))
        return handler

    for action, model, function, description in (
        ('tableau_current', TableauSelection, current_tableaus, 'Read current Tableau identities without refreshing sources.'),
        ('tableau_query', TableauQuery, query_tableau, 'Read bounded typed metadata, stored Hyper samples or literal FTS5/BM25 matches.'),
        ('tableau_read', TableauRead, read_tableau, 'Read an exact immutable Tableau file or package member by byte range.')):
        engine.registry.register(ActionSpec(action, description, model, TableauResult, query_handler(function, action),
            profile='tableau', workflow='source-intake', queryable_in_delta=True, cross_project_read=True,
            studio_read=True, read_migrations=TABLEAU_MIGRATIONS,
            search=SearchRoute(('tableau',), 'rows', 'tableau_current', 'tableaus', 'text', 'any', rerank_text='text') if action == 'tableau_query' else None,
            fetch=FetchRoute(('tableau',), 'tableau') if action == 'tableau_read' else None))
    engine.registry.register(ActionSpec('tableau_export', 'Export an exact Tableau version with a journaled effect and destination hash binding.',
        TableauExport, TableauResult, export_tableau, permission='write', mutates=True, requires_delta=True,
        profile='tableau', workflow='build', path_fields=('filename',),
        verification_checks=('tableau_export_hash_verified',), verifier=verify_exported,
        worker_operations=('tableau_parse_content', 'render_lane_view'), tool_routes=tuple(ToolRoute('tableau_export.' + ('view' if render else 'native'), export_tableau,
                (*('Python', 'lxml', 'Tableau_Hyper_API'), *(('LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx') if render else ())),
                view_refresh=SelectedViewRefresh(engine, 'tableau.structure'), systems=('Windows',), applicable=export_route(render),
                worker_operations=('tableau_parse_content', *(('render_lane_view',) if render else ())))
                for render in (False, True))))
    from .tableau_views import register_tableau_views
    register_tableau_views(engine)


def format_contract():
    return {'schema': 'evidence-lane.tableau-formats.v4', 'lane_id': 'tableau',
        'native_intake': ['.twb', '.twbx', '.tds', '.tdsx', '.hyper'], 'opaque_intake': ['.tde'],
        'native_generation': ['.hyper'], 'native_editing': ['TWB/TDS captions and calculation formula XML, including packaged documents'],
        'native_export': 'exact_whole_snapshot_bytes_with_destination_refresh', 'hyper_api_version': '0.0.26359',
        'hyper_queries': 'all schemas, quoted table metadata, exact row counts and bounded typed samples; no client SQL',
        'native_operation_platforms': ['Windows'], 'source_bytes_mutated_by_intake': False,
        'live_connections_opened': False, 'external_files_loaded': False, 'tableau_layout_equivalence': False,
        'tableau_semantic_validation': 'requires separately configured vendor validation; not claimed by local XML parsing',
        'budgets': {'max_file_bytes': 8_388_608, 'max_package_bytes': 16_777_216, 'max_package_members': 256,
            'max_hyper_files': 16, 'max_hyper_tables_per_file': 128, 'max_rows_per_table': 1000,
            'max_native_cells_per_file': 50_000, 'max_items': 50_000, 'hyper_job_memory_bytes': 2_147_483_648},
        'natural_files': ['exact_Tableau_format'], 'graph_files': ['tableau.mmd', 'tableau.dot', 'tableau.pointer.json']}


class TableauResult(Contract):
    project_id: str
    lane_id: Literal['tableau'] = 'tableau'
    operation: str
    result: dict[str, JsonValue]


def result(store, operation, body):
    value = TableauResult(project_id=store.project_id, operation=operation, result=body)
    if len(canonical_json_bytes(value.model_dump(mode='json'))) > 2_097_152:
        raise LaneError('TABLEAU_OUTPUT_BUDGET', 'Select a smaller tableau result.')
    return value


def _relative(value):
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path) or '\x00' in str(path) or str(path) == '.':
        raise LaneError('TABLEAU_PATH_INVALID', 'Select an exact project-relative tableau filename.')
    return path.as_posix()


def _bytes(path, limit=8_388_608):
    reject_links(path, Path(path.anchor))
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise LaneError('TABLEAU_FILE_BYTE_BUDGET', 'The selected tableau exceeds its byte budget.')
    return content


def _lane(store):
    try:
        return store.lane('tableau')
    except LaneError as error:
        if error.code != 'LANE_NOT_INITIALIZED':
            raise
        return None


def _calls(execution, remaining):
    execution.guard.check()
    if execution.guard.calls + remaining > execution.guard.task.budget.max_tool_calls:
        raise LaneError('DELTA_TOOL_BUDGET', 'The tableau operation must leave room for its required verification.')


def current_snapshot(store, tableau_id):
    lane = _lane(store)
    if lane is None:
        return None
    read_compatibility(lane, TABLEAU_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='tableau_current'").fetchone():
            return None
        row = connection.execute('SELECT snapshot_id FROM tableau_current WHERE tableau_id=?', (tableau_id,)).fetchone()
    return row[0] if row else None


def read_snapshot(store, snapshot_id):
    lane = _lane(store)
    if lane is None:
        raise LaneError('TABLEAU_SNAPSHOT_MISSING', 'Index or generate the selected tableau first.')
    read_compatibility(lane, TABLEAU_MIGRATIONS)
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='tableau_version'").fetchone():
            raise LaneError('TABLEAU_SNAPSHOT_MISSING', 'Index or generate the selected tableau first.')
        row = connection.execute('SELECT * FROM tableau_version WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if row is None:
        raise LaneError('TABLEAU_SNAPSHOT_MISSING', 'Select an exact tableau snapshot from this TABLEAU lane.')
    manifest = json.loads(lane.read_object(row['manifest_object']))
    if (digest(canonical_json_bytes(manifest)) != snapshot_id or manifest['project_id'] != store.project_id
            or manifest['lane_id'] != 'tableau' or any(manifest[key] != row[key] for key in (
                'tableau_id', 'generation', 'previous_snapshot', 'raw_object', 'facts_object', 'parser_contract'))):
        raise LaneError('TABLEAU_SNAPSHOT_INTEGRITY', 'The tableau manifest differs from its indexed identity.')
    facts = json.loads(lane.read_object(manifest['facts_object']))
    return manifest, facts


def _worker(execution, operation, arguments):
    response = execution.submit(operation, arguments).result()
    if response['status'] != 'ok':
        raise LaneError(response.get('code', 'TABLEAU_WORKER_FAILED'), 'The selected tableau worker did not complete.')
    body = json.loads(json_text(response['result']))
    content = base64.b64decode(body.pop('content_base64'), validate=True)
    if digest(content) != body['sha256'] or len(content) != body['bytes'] or body['filename'] != arguments['logical_name']:
        raise LaneError('TABLEAU_WORKER_BINDING', 'The tableau worker returned different bytes or a different tableau.')
    return content, body


def _natural(lane, snapshot_id, name, content):
    name = _relative(name)
    if '/' in name:
        raise LaneError('TABLEAU_OUTPUT_NAME_INVALID', 'A lane-owned natural artifact requires a simple filename.')
    target = lane.files / 'natural' / snapshot_id / name
    reject_links(target, lane.folder)
    target.parent.mkdir(parents=True, exist_ok=True)
    reject_links(target, lane.folder)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if _bytes(target, 16_777_216) != content:
            raise LaneError('TABLEAU_ARTIFACT_CHANGED', 'An existing natural artifact differs from its immutable bytes.') from None
    else:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    if _bytes(target, 16_777_216) != content:
        raise LaneError('TABLEAU_ARTIFACT_WRITE_FAILED', 'The natural tableau artifact failed byte verification.')
    return str(target)


def publish(context, *, tableau_id, logical_name, source_path, origin, previous, content, parsed, operation, source_content=None,
            source_observation_route_id=None, max_file_bytes=None, intake=False, inputs=None):
    execution, store = context.execution, context.execution.store
    if current_snapshot(store, tableau_id) != previous:
        raise LaneError('TABLEAU_SNAPSHOT_CHANGED', 'Bind this operation to the exact current tableau snapshot.')
    prior = read_snapshot(store, previous)[0] if previous else None
    selection = (execution.guard.source_route.selection.model_dump(mode='json') if execution.guard.source_route
                 else prior.get('source_route') if prior and not intake else None)
    observation = (source_observation_route_id or (selection['route_id'] if selection else None)
                   or (prior.get('source_observation_route_id') if prior and not intake else None))
    if source_observation_route_id and selection:
        selection = {**selection, 'route_id': source_observation_route_id}
    generation, stamp = (prior['generation'] + 1 if prior else 1), now()
    facts, contract = parsed['facts'], _parser_contract()
    manifest = {'schema': 'evidence-lane.tableau-snapshot.v4', 'project_id': store.project_id, 'lane_id': 'tableau',
        'tableau_id': tableau_id, 'logical_name': logical_name, 'source_path': source_path, 'origin': origin,
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
    with execution.lease.coordinated_transaction(['tableau', 'receipts']):
        lane = store.lane('tableau')
        apply_migrations(lane, TABLEAU_MIGRATIONS, writer=execution.lease)
        with lane.transaction() as connection:
            live = connection.execute('SELECT snapshot_id FROM tableau_current WHERE tableau_id=?', (tableau_id,)).fetchone()
            if (live[0] if live else None) != previous:
                raise LaneError('TABLEAU_SNAPSHOT_CHANGED', 'The tableau changed before publication.')
            lane.put_object(content)
            if source_content is not None:
                lane.put_object(source_content)
            lane.put_object(canonical_json_bytes(facts))
            manifest_object = lane.put_object(canonical_json_bytes(manifest))
            connection.execute('INSERT OR IGNORE INTO tableau_file VALUES(?,?,?,?,?)',
                (tableau_id, logical_name, source_path, origin, stamp))
            connection.execute('INSERT INTO tableau_version VALUES(?,?,?,?,?,?,?,?,?)',
                (snapshot_id, tableau_id, generation, previous, manifest_object, manifest['raw_object'],
                 manifest['facts_object'], contract, stamp))
            connection.execute('INSERT INTO tableau_structure VALUES(?,?,?)',
                (snapshot_id, manifest['facts_object'], json_text(facts['fidelity'])))
            for item in facts['items']:
                connection.execute('INSERT INTO ' + TABLEAU_TABLES[item['kind']] + ' VALUES(?,?,?,?,?,?)',
                    (snapshot_id, item['item_id'], item['kind'], item['ordinal'], item['part'], json_text(item)))
            for item_id, ordinal, text in _chunks(facts):
                chunk_id = digest(canonical_json_bytes([snapshot_id, item_id, ordinal]))
                obj = lane.put_object(text.encode('utf-8'))
                connection.execute('INSERT INTO tableau_chunk VALUES(?,?,?,?,?)', (snapshot_id, chunk_id, item_id, ordinal, obj))
                connection.execute('INSERT INTO tableau_chunk_fts VALUES(?,?,?)', (snapshot_id, chunk_id, text))
            path = _natural(lane, snapshot_id, PurePosixPath(logical_name).name, content)
            connection.execute('INSERT INTO tableau_current VALUES(?,?) ON CONFLICT(tableau_id) DO UPDATE SET snapshot_id=excluded.snapshot_id',
                               (tableau_id, snapshot_id))
            store.append_receipt('tableau_snapshot', {'snapshot_id': snapshot_id, 'tableau_id': tableau_id,
                'job_id': execution.claim.job_id, 'operation': operation, 'source_bytes_mutated': False})
    return result(store, operation, {'snapshot_id': snapshot_id, 'tableau_id': tableau_id,
        'generation': generation, 'previous_snapshot': previous, 'sha256': manifest['raw_object'], 'bytes': len(content),
        'logical_name': logical_name, 'natural_path': path, 'source_bytes_mutated': False,
        'fidelity': facts['fidelity'], 'limitations': facts['limitations'], 'tool_evidence': parsed['evidence']})


def generate_tableau(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    name = _relative(request.logical_name)
    tableau_id = digest(canonical_json_bytes([store.project_id, 'generated', name]))
    if current_snapshot(store, tableau_id) != request.expected_snapshot:
        raise LaneError('TABLEAU_SNAPSHOT_CHANGED', 'Generation requires the exact current tableau identity.')
    content, parsed = _worker(execution, 'tableau_generate', request.model_dump(mode='json'))
    return publish(context, tableau_id=tableau_id, logical_name=name, source_path=None, origin='generated',
        previous=request.expected_snapshot, content=content, parsed=parsed, operation='tableau_generate')


def verify_tableau(context, request, output):
    store, snapshot_id = context.store, output.result['snapshot_id']
    manifest, facts = read_snapshot(store, snapshot_id)
    lane = store.lane('tableau')
    raw = lane.read_object(manifest['raw_object'])
    valid = len(raw) == manifest['bytes'] and manifest['raw_object'] == output.result['sha256']
    valid &= parse_tableau(manifest['logical_name'], raw, **facts['parse_options']) == facts
    valid &= _bytes(Path(output.result['natural_path'])) == raw
    with lane.connection(read_only=True) as connection:
        for table in sorted(set(TABLEAU_TABLES.values())):
            rows = connection.execute('SELECT * FROM ' + table + ' WHERE snapshot_id=? ORDER BY item_id', (snapshot_id,)).fetchall()
            expected = sorted([item for item in facts['items'] if TABLEAU_TABLES[item['kind']] == table], key=lambda item: item['item_id'])
            valid &= [json.loads(row['payload_json']) for row in rows] == expected
            valid &= all((row['item_id'], row['kind'], row['ordinal'], row['part']) == (
                item['item_id'], item['kind'], item['ordinal'], item['part']) for row, item in zip(rows, expected))
        expected = list(_chunks(facts))
        rows = connection.execute('SELECT c.*,f.text_content FROM tableau_chunk c JOIN tableau_chunk_fts f '
            'ON c.chunk_id=f.chunk_id AND c.snapshot_id=f.snapshot_id WHERE c.snapshot_id=?', (snapshot_id,)).fetchall()
        valid &= len(rows) == len(expected)
        lookup = {(item_id, ordinal): text for item_id, ordinal, text in expected}
        for row in rows:
            text = lookup.get((row['item_id'], row['ordinal']))
            valid &= (text == row['text_content'] and text is not None and lane.read_object(row['text_object']).decode() == text
                and row['chunk_id'] == digest(canonical_json_bytes([snapshot_id, row['item_id'], row['ordinal']])))
    live = not isinstance(request, TableauIndex) or _bytes(context.source_path(request.filename), request.max_file_bytes) == raw
    checks = {'tableau_snapshot_integrity': valid, 'tableau_source_hash_unchanged': live}
    return [{'check_id': name, 'passed': checks[name], 'evidence': {'snapshot_id': snapshot_id,
        'source_bytes_mutated': False, 'layout_verified': False}} for name in context.requested_checks]


def current_tableaus(store, request=None):
    lane = _lane(store)
    if lane is None:
        return {'tableaus': [], 'initialized': False}
    read_compatibility(lane, TABLEAU_MIGRATIONS)
    active = active_selector_sql(lane)
    with lane.connection(read_only=True) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='tableau_current'").fetchone():
            return {'tableaus': [], 'initialized': False}
        rows = connection.execute('SELECT v.*,f.logical_name,f.source_path,f.origin FROM tableau_current c '
            'JOIN tableau_version v USING(snapshot_id) JOIN tableau_file f ON f.tableau_id=c.tableau_id '
            f'WHERE {active} ORDER BY v.created_at DESC LIMIT 129').fetchall()
    return {'tableaus': [dict(row) for row in rows[:128]], 'truncated': len(rows) > 128,
            'initialized': True, 'source_currentness': 'not_checked_by_metadata_read'}


def query_tableau(store, request):
    with bounded_project_read(store.root, time.monotonic() + 5):
        manifest, facts = read_snapshot(store, request.snapshot_id)
        lane = store.lane('tableau')
        items = {row['item_id']: row for row in facts['items']}
        if request.collection == 'metadata':
            return {'snapshot_id': request.snapshot_id, 'tableau_id': manifest['tableau_id'],
                'logical_name': manifest['logical_name'], 'source_object': manifest['source_object'],
                'raw_object': manifest['raw_object'], 'fidelity': facts['fidelity'], 'features': facts['features'],
                'limitations': facts['limitations'], 'structure_counts': {kind: sum(row['kind'] == kind for row in facts['items'])
                    for kind in sorted({row['kind'] for row in facts['items']})}, 'source_bytes_mutated': False}
        args = [request.snapshot_id]
        if request.collection == 'text':
            tokens = re.findall(r'\w+', request.query or '', re.UNICODE)
            if not 1 <= len(tokens) <= 32:
                raise LaneError('TABLEAU_QUERY_TERMS_REQUIRED', 'Use one to thirty-two literal text search terms.')
            sql = ('SELECT c.*,f.text_content,bm25(tableau_chunk_fts) AS rank FROM tableau_chunk_fts f JOIN tableau_chunk c '
                'ON f.snapshot_id=c.snapshot_id AND f.chunk_id=c.chunk_id WHERE c.snapshot_id=? AND tableau_chunk_fts MATCH ? '
                'ORDER BY rank,c.item_id,c.ordinal')
            args.append((' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token + '"' for token in tokens))
        else:
            sql = 'SELECT * FROM ' + TABLEAU_TABLES[request.collection] + ' WHERE snapshot_id=? AND kind=?'
            args.append(request.collection)
            if request.query:
                sql += " AND instr(lower(json_extract(payload_json,'$.text')),lower(?))>0"
                args.append(request.query)
            sql += " ORDER BY part,ordinal,item_id"
        with lane.connection(read_only=True) as connection:
            rows = connection.execute(sql + ' LIMIT ? OFFSET ?', [*args, request.limit + 1, request.offset]).fetchall()
        values, used, truncated = [], 0, False
        for row in rows:
            item = items.get(row['item_id'])
            if item is None:
                raise LaneError('TABLEAU_QUERY_INTEGRITY', 'A query item is outside the selected immutable tableau.')
            if request.collection == 'text':
                start = row['ordinal'] * 4096
                text = lane.read_object(row['text_object']).decode('utf-8')
                if (row['ordinal'] < 0 or text != item['text'][start:start + 4096]
                        or text != row['text_content'] or row['chunk_id'] != digest(canonical_json_bytes([
                            request.snapshot_id, row['item_id'], row['ordinal']]))):
                    raise LaneError('TABLEAU_QUERY_INTEGRITY', 'The indexed text differs from its immutable tableau facts.')
                value = {'item_id': item['item_id'], 'part': item['part'], 'item_ordinal': item['ordinal'],
                         'text': text, 'rank': row['rank'], 'chunk_ordinal': row['ordinal']}
            else:
                if (json.loads(row['payload_json']) != item or (row['kind'], row['ordinal'], row['part']) != (
                        item['kind'], item['ordinal'], item['part'])):
                    raise LaneError('TABLEAU_QUERY_INTEGRITY', 'The typed item differs from its immutable tableau facts.')
                value = item
            size = len(canonical_json_bytes(value))
            if len(values) >= request.limit or used + size > request.max_bytes:
                truncated = True
                break
            values.append(value)
            used += size
        if truncated and not values:
            raise LaneError('TABLEAU_QUERY_ITEM_TOO_LARGE', 'Increase max_bytes for the next complete tableau item.')
        return {'snapshot_id': request.snapshot_id, 'tableau_id': manifest['tableau_id'], 'rows': values,
            'next_offset': request.offset + len(values) if truncated else None, 'truncated': truncated,
            'fidelity': facts['fidelity'], 'source_bytes_mutated': False}


def _export_parse_options(store, request):
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', _relative(request.filename)]))
    snapshot = current_snapshot(store, destination_id) or request.snapshot_id
    return read_snapshot(store, snapshot)[1]['parse_options']


def export_tableau(context, request):
    execution, store = context.execution, context.execution.store
    _calls(execution, 2)
    manifest, _ = read_snapshot(store, request.snapshot_id)
    relative = _relative(request.filename)
    if PurePosixPath(relative).suffix.lower() != PurePosixPath(manifest['logical_name']).suffix.lower():
        raise LaneError('TABLEAU_EXPORT_FORMAT_MISMATCH', 'Export uses the existing tableau format; it is not conversion.')
    path = execution.guard.path(relative)
    ProjectAccess(store).authorize(context.client_id, 'write', path=path)
    reject_links(path, store.source_root)
    before_content = _bytes(path) if path.is_file() else None
    before = digest(before_content) if before_content is not None else None
    if path.exists() and not path.is_file() or before != request.expected_sha256:
        raise LaneError('TABLEAU_EXPORT_DESTINATION_CHANGED', 'Bind export to the exact destination hash, or absence for a new file.')
    if not path.parent.is_dir():
        raise LaneError('TABLEAU_EXPORT_PARENT_MISSING', 'Select an existing granted destination directory.')
    lane, export_id = store.lane('tableau'), str(uuid4())
    content = lane.read_object(manifest['raw_object'])
    if manifest['parser_contract'] != _parser_contract():
        raise LaneError('TABLEAU_PARSER_CONTRACT_CHANGED', 'Refresh this development snapshot with the current parser before exporting.')
    destination_id = digest(canonical_json_bytes([store.project_id, 'source', relative]))
    previous = current_snapshot(store, destination_id)
    destination_manifest = read_snapshot(store, previous)[0] if previous else None
    if destination_manifest and 'source_observation_route_id' not in destination_manifest:
        raise LaneError('TABLEAU_REFRESH_PROVENANCE_REQUIRED', 'Refresh the destination snapshot with current source provenance before export.')
    limit = (destination_manifest or manifest).get('capture_limits', {}).get('max_file_bytes', 8_388_608)
    if len(content) > limit:
        raise LaneError('TABLEAU_FILE_BYTE_BUDGET', 'The proposed export exceeds the selected destination intake bound.')
    from .artifact_contract import LaneArtifacts
    artifacts = LaneArtifacts(execution.guard.engine, store)
    view_id = 'tableau.structure'
    view_selection = artifacts.current_selection(view_id)
    from .source_routing import SourceMutationRefresh
    source_refresh = SourceMutationRefresh(context, [relative], lane_id='tableau',
        action='tableau_export', max_file_bytes=8_388_608, allow_create=True,
        parent_route_id=destination_manifest.get('source_observation_route_id') if destination_manifest else None)
    source_refresh.expected_after(path, before, content)
    _calls(execution, 3 + int(bool(view_selection and view_selection['formats'])))
    parsed_content, parsed = _worker(execution, 'tableau_parse_content', {
        'filename': str(path), 'logical_name': relative, 'max_file_bytes': limit,
        'content_base64': base64.b64encode(content).decode('ascii'),
        'expected_sha256': manifest['raw_object'], 'parse_options': _export_parse_options(store, request)})
    execution.guard.observe(execution)
    if parsed_content != content or manifest['parser_contract'] != _parser_contract():
        raise LaneError('TABLEAU_PARSER_CONTRACT_CHANGED', 'The proposed bytes or selected parser changed before export.')
    effect = execution.prepare_effect('tableau:' + export_id, 'Export exact versioned tableau bytes to one granted project filename.')
    with execution.lease.coordinated_transaction(['tableau', 'receipts']):
        with lane.transaction() as connection:
            if before_content is not None:
                lane.put_object(before_content)
            connection.execute('INSERT INTO tableau_export VALUES(?,?,?,?,?,?,?)',
                (export_id, request.snapshot_id, relative, before, manifest['raw_object'], effect, now()))
        store.append_receipt('tableau_export_prepared', {'export_id': export_id, 'snapshot_id': request.snapshot_id,
            'destination': relative, 'before_sha256': before, 'after_sha256': manifest['raw_object'], 'effect_id': effect})
    descriptor, temporary = tempfile.mkstemp(prefix='.evidence-lane-tableau-', suffix='.tmp', dir=path.parent)
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
            raise LaneError('TABLEAU_EXPORT_DESTINATION_CHANGED', 'The destination changed immediately before export; reconcile this effect.')
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
            raise LaneError('TABLEAU_EXPORT_VERIFY_FAILED', 'The written tableau bytes were not confirmed.')
        with execution.lease.transaction('plan'):
            evidence = execution.plan_store.put_object(canonical_json_bytes({'export_id': export_id,
                'destination': relative, 'sha256': manifest['raw_object'], 'exact_bytes_verified': True}))
            execution.confirm_effect(effect, evidence)
    finally:
        if temporary.exists():
            temporary.unlink()
    with execution.lease.coordinated_transaction(['sources', 'tableau', 'receipts']):
        source_result = source_refresh.publish(path=path, before_sha256=before, replacement=content,
            mutation_id=export_id, effect_id=effect)
        indexed = publish(context, tableau_id=destination_id, logical_name=relative,
            source_path=relative, origin='source', previous=previous, content=content, parsed=parsed,
            operation='tableau_export', max_file_bytes=limit,
            source_observation_route_id=source_result['route_id'],
            inputs=[{'lane_id': 'tableau', 'snapshot_id': request.snapshot_id, 'sha256': manifest['raw_object'], 'operation': 'export'}])
        view_result = artifacts.refresh_selected(view_id, view_selection, execution, actor_id=context.client_id)
        execution._before_more_work()
        final_capture = source_refresh.capture()
        from .source_authority import freeze_source_authority
        for spec in source_refresh.specs:
            freeze_source_authority(spec, capture=final_capture)
        if final_capture.identities != source_refresh.expected_after(path, before, content):
            raise LaneError('SOURCE_REFRESH_UNEXPECTED_CHANGE', 'A source changed before the refreshed lanes could publish.')
    return result(store, 'tableau_export', {'export_id': export_id, 'snapshot_id': request.snapshot_id,
        'destination': relative, 'before_sha256': before, 'after_sha256': manifest['raw_object'], 'effect_id': effect,
        'source_bytes_mutated': True, 'source_index_refresh_required': False,
        'index_refresh': indexed.model_dump(mode='json'), 'source_refresh': source_result, 'view_refresh': view_result, 'automatic_replay': False})


def verify_export(context, request, output, *, engine=None):
    manifest, _ = read_snapshot(context.store, request.snapshot_id)
    valid = digest(_bytes(context.source_path(request.filename))) == manifest['raw_object'] == output.result['after_sha256']
    lane = context.store.lane('tableau')
    with lane.connection(read_only=True) as connection:
        exported = connection.execute('SELECT * FROM tableau_export WHERE export_id=?', (output.result['export_id'],)).fetchone()
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
    indexed = TableauResult.model_validate(output.result['index_refresh'])
    index_manifest, index_facts = read_snapshot(context.store, indexed.result['snapshot_id'])
    expected_id = digest(canonical_json_bytes([context.store.project_id, 'source', _relative(request.filename)]))
    valid &= (indexed.project_id == context.store.project_id and indexed.operation == 'tableau_export'
        and indexed.lane_id == 'tableau' and index_manifest['tableau_id'] == expected_id
        and current_snapshot(context.store, expected_id) == indexed.result['snapshot_id']
        and index_manifest['logical_name'] == index_manifest['source_path'] == _relative(request.filename)
        and index_manifest['raw_object'] == manifest['raw_object'] and not output.result['source_index_refresh_required']
        and index_manifest['inputs'] == [{'lane_id': 'tableau', 'snapshot_id': request.snapshot_id,
            'sha256': manifest['raw_object'], 'operation': 'export'}])
    valid &= all(row['passed'] for row in verify_tableau(replace(context,
        requested_checks=('tableau_snapshot_integrity',)), request, indexed))
    source = output.result['source_refresh']
    prior = read_snapshot(context.store, index_manifest['previous_snapshot'])[0] if index_manifest['previous_snapshot'] else None
    options_snapshot = index_manifest['previous_snapshot'] or request.snapshot_id
    valid &= index_facts['parse_options'] == read_snapshot(context.store, options_snapshot)[1]['parse_options']
    from .source_routing import verify_export_refresh
    valid &= (index_manifest['source_observation_route_id'] == source['route_id']
        and source['parent_route_id'] == (prior.get('source_observation_route_id') if prior else None)
        and verify_export_refresh(context, source, lane_id='tableau',
            action='tableau_export', mutation_id=output.result['export_id'],
            effect_id=output.result['effect_id'], max_file_bytes=8_388_608))
    if engine is None:
        valid = False
    else:
        from .artifact_contract import LaneArtifacts
        view_valid, graph_workers = LaneArtifacts(engine, context.store).verify_refreshed(
            'tableau.structure', output.result['view_refresh'])
        valid &= view_valid and len(context.worker_evidence) == 1 + graph_workers and all(
            row['status'] == 'ok' for row in context.worker_evidence)
    return [{'check_id': name, 'passed': valid, 'evidence': {'export_id': output.result['export_id'],
        'after_sha256': manifest['raw_object']}} for name in context.requested_checks]
