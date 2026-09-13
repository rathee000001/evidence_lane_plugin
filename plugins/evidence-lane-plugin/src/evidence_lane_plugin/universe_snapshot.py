"""Bounded, coherent Universe references to the project's real lane owners.

project evidence head coordinator coordinates these references. This reader creates no competing index,
does not initialize absent lanes, and does not require every lane to emit graphs.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from .errors import LaneError
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, bounded_project_read, json_text, reject_links


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


class UniverseInspect(Contract):
    timeout_ms: int = Field(default=10000, ge=100, le=30000)
    max_files: int = Field(default=4096, ge=1, le=20000)
    max_bytes: int = Field(default=67108864, ge=1024, le=268435456)
    verify_files: bool = True


class UniverseSnapshot(Contract):
    project_id: str
    project_root_identity_sha256: str
    root_pv: dict[str, JsonValue]
    lanes: list[dict[str, JsonValue]]
    snapshot_sha256: str
    files_verified: bool
    snapshot_scope: Literal['coherent_published_project'] = 'coherent_published_project'
    mutation_performed: Literal[False] = False


def root_reference(project):
    """Verify the current publication against its durable commit reference."""
    with project.connection(read_only=True) as connection:
        head = dict(connection.execute('SELECT * FROM root_pv_head WHERE singleton=1').fetchone())
        heads = [dict(row) for row in connection.execute('SELECT * FROM root_lane_heads ORDER BY lane_id')]
        if head['revision'] == 0:
            if head['head_digest'] or head['commit_id'] is not None or heads:
                raise LaneError('UNIVERSE_ROOT_INTEGRITY', 'The initial project evidence head coordinator has inconsistent lane references.')
        else:
            row = connection.execute('SELECT * FROM root_transaction_journal WHERE commit_id=?',
                                     (head['commit_id'],)).fetchone()
            try:
                body = json.loads(row['body_json']) if row and len(row['body_json'].encode()) <= 1048576 else {}
                expected = digest({'project_id': project.project_id, 'revision': head['revision'],
                    'previous_digest': body['before_root']['head_digest'], 'lanes': heads})
                valid = (row['phase'] == 'published' and body['project_id'] == project.project_id
                    and body['published_heads'] == heads and body['before_root']['revision'] + 1 == head['revision']
                    and expected == head['head_digest'])
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise LaneError('UNIVERSE_ROOT_INTEGRITY', 'project evidence head coordinator differs from its published commit references.')
        return head


def inspect_project(store, request=None):
    project = store.project if isinstance(store, LaneStore) else store
    request = request or UniverseInspect()
    deadline = time.monotonic() + request.timeout_ms / 1000
    remaining_files, remaining_bytes = request.max_files, request.max_bytes

    def tick():
        if time.monotonic() >= deadline:
            raise LaneError('UNIVERSE_READ_TIMEOUT', 'Reduce the selected integrity read or raise its time budget.')

    def file_hash(path, *, capture=False):
        nonlocal remaining_bytes
        tick()
        size = path.stat().st_size
        if size > remaining_bytes:
            raise LaneError('UNIVERSE_BYTE_BUDGET', 'The selected integrity read exceeds its byte budget.')
        remaining_bytes -= size
        value = hashlib.sha256()
        chunks = []
        with path.open('rb') as stream:
            while chunk := stream.read(1048576):
                tick()
                value.update(chunk)
                if capture:
                    chunks.append(chunk)
        return (value.hexdigest(), b''.join(chunks)) if capture else value.hexdigest()

    with bounded_project_read(project.root, deadline):
        project.assert_current_binding()
        head = root_reference(project)
        lanes = []
        for item in project.lane_catalog():
            tick()
            lane = project.lane(item['lane_id'])
            database_hash = file_hash(lane.database)
            if item['head_digest'] is not None and item['head_digest'] != digest({
                    'lane_id': lane.lane_id, 'database_sha256': database_hash}):
                raise LaneError('LANE_HEAD_MISMATCH', 'The lane database differs from its published project evidence head coordinator head.')
            with lane.connection(read_only=True) as connection:
                files = [dict(row) for row in connection.execute(
                    'SELECT digest,size_bytes FROM objects ORDER BY digest LIMIT ?', (remaining_files + 1,))]
                remaining_files -= len(files)
                if remaining_files < 0:
                    raise LaneError('UNIVERSE_FILE_BUDGET', 'The selected project exceeds its file-reference budget.')
                schemas = [dict(row) for row in connection.execute(
                    'SELECT owner,version,digest FROM schema_migrations ORDER BY owner,version LIMIT ?', (remaining_files + 1,))]
                remaining_files -= len(schemas)
                history_exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='schema_history_files'").fetchone()
                history = [dict(row) for row in connection.execute(
                    'SELECT * FROM schema_history_files ORDER BY owner,version LIMIT ?', (request.max_files + 1,))] if history_exists else []
                if remaining_files < 0 or len(history) > request.max_files:
                    raise LaneError('UNIVERSE_FILE_BUDGET', 'The selected schema history exceeds its reference budget.')
                if {(row['owner'], row['version']) for row in schemas} != {(row['owner'], row['version']) for row in history}:
                    raise LaneError('UNIVERSE_SCHEMA_INTEGRITY', 'A lane schema lacks its recorded migration file.')
                artifacts = []
                if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='views_current'").fetchone():
                    views = connection.execute('SELECT c.*,s.body_json FROM views_current c LEFT JOIN views_snapshots s USING(snapshot_digest,view_id) ORDER BY view_id LIMIT ?',
                        (remaining_files + 1,)).fetchall()
                    if len(views) > remaining_files:
                        raise LaneError('UNIVERSE_FILE_BUDGET', 'The lane view references exceed the selected budget.')
                    for view in views:
                        tick()
                        if (not re.fullmatch(re.escape(lane.lane_id) + r'\.[a-z][a-z0-9_]{0,47}', view['view_id'])
                                or not re.fullmatch(r'[0-9a-f]{64}', view['snapshot_digest'])
                                or not view['body_json'] or len(view['body_json'].encode()) > 1048576):
                            raise LaneError('UNIVERSE_VIEW_INTEGRITY', 'The lane view is missing its bounded snapshot manifest.')
                        manifest = json.loads(view['body_json'])
                        binding = manifest['binding']
                        declaration = connection.execute('SELECT body_json FROM views_contracts WHERE contract_digest=? AND view_id=?',
                            (binding['contract_digest'], view['view_id'])).fetchone()
                        if not declaration or len(declaration[0].encode()) > 131072:
                            raise LaneError('UNIVERSE_VIEW_INTEGRITY', 'The original view contract is missing or exceeds its budget.')
                        from .lane_traversal import validate_snapshot_contract
                        validate_snapshot_contract(project.project_id, json.loads(declaration[0]), manifest)
                        if (digest(manifest) != view['snapshot_digest'] or binding['lane_id'] != lane.lane_id
                                or manifest['generation'] != view['generation']):
                            raise LaneError('UNIVERSE_VIEW_INTEGRITY', 'The current lane view differs from its stored identity.')
                        for record in manifest['files']:
                            remaining_files -= 1
                            if remaining_files < 0:
                                raise LaneError('UNIVERSE_FILE_BUDGET', 'The lane artifacts exceed the selected reference budget.')
                            if (not re.fullmatch(r'[a-z][a-z0-9_.-]{0,95}', record['filename'])
                                    or not re.fullmatch(r'[0-9a-f]{64}', record['sha256'])
                                    or not isinstance(record['bytes'], int) or not 0 <= record['bytes'] <= 524288):
                                raise LaneError('UNIVERSE_VIEW_INTEGRITY', 'A lane artifact has an invalid bounded file identity.')
                            path = lane.folder / view['view_id'].split('.')[1] / view['snapshot_digest'] / record['filename']
                            reject_links(path, project.root)
                            if request.verify_files and (not path.is_file() or path.stat().st_size != record['bytes'] or file_hash(path) != record['sha256']):
                                raise LaneError('UNIVERSE_VIEW_INTEGRITY', 'A current natural lane artifact differs from its recorded bytes.')
                        artifacts.append({'view_id': view['view_id'], 'snapshot_digest': view['snapshot_digest'],
                            'generation': view['generation'], 'files': manifest['files']})
            if request.verify_files:
                schema_digests = {(row['owner'], row['version']): row['digest'] for row in schemas}
                for record in files:
                    path = lane.object_path(record['digest'])
                    if not path.is_file() or path.stat().st_size != record['size_bytes'] or file_hash(path) != record['digest']:
                        raise LaneError('UNIVERSE_FILE_INTEGRITY', 'A registered lane file differs from its content address.')
                entries = []
                for record in history:
                    if (not re.fullmatch(r'[a-z][a-z0-9]{0,31}', record['owner']) or record['version'] < 1
                            or not re.fullmatch(r'[0-9a-f]{64}', record['digest'])
                            or record['filename'] != 'schema-history.v4.json'):
                        raise LaneError('UNIVERSE_SCHEMA_INTEGRITY', 'A schema history file has an invalid identity.')
                    try:
                        if len(record['document_json'].encode()) > 4194304:
                            raise ValueError('entry too large')
                        body = json.loads(record['document_json'])
                        migration_digest = digest([body['owner'], body['version'], body['description'], body['statements']])
                        entry_digest = digest(body)
                        valid = (body['schema'] == 'evidence-lane.schema-history-entry.v4'
                            and body['project_id'] == project.project_id and body['lane_id'] == lane.lane_id
                            and body['owner'] == record['owner'] and body['version'] == record['version']
                            and body['migration_digest'] == migration_digest == schema_digests[record['owner'], record['version']])
                    except (ValueError, KeyError, TypeError):
                        entry_digest = None
                        valid = False
                    if entry_digest != record['digest'] or not valid:
                        raise LaneError('UNIVERSE_SCHEMA_INTEGRITY', 'A schema history file differs from its recorded bytes.')
                    entries.append(body)
                path = lane.schema_history
                reject_links(path, project.root)
                if not path.is_file() or path.stat().st_size > 4194304:
                    raise LaneError('UNIVERSE_SCHEMA_INTEGRITY', 'The direct schema history projection is missing or exceeds its bound.')
                remaining_files -= 1
                if remaining_files < 0:
                    raise LaneError('UNIVERSE_FILE_BUDGET', 'The direct schema history projection exceeds the selected file budget.')
                _, content = file_hash(path, capture=True)
                try:
                    projection = json.loads(content)
                except (ValueError, TypeError):
                    raise LaneError('UNIVERSE_SCHEMA_INTEGRITY', 'The direct schema history projection is not valid JSON.') from None
                if projection != {'schema': 'evidence-lane.schema-history.v4',
                        'project_id': project.project_id, 'lane_id': lane.lane_id, 'entries': entries}:
                    raise LaneError('UNIVERSE_SCHEMA_INTEGRITY', 'The direct schema history projection differs from the migration ledger.')
            body = {'project_id': project.project_id, 'lane_id': lane.lane_id,
                'database_sha256': database_hash, 'schema_history_sha256': digest(schemas),
                'file_references_sha256': digest(files), 'file_count': len(files),
                'head_revision': item['revision'], 'head_digest': item['head_digest'],
                'artifact_references': artifacts}
            lanes.append({**body, 'mini_brain_id': 'mini_' + digest(body)})
        identity = digest({'project_id': project.project_id, 'state_root': str(project.root),
                           'source_root': str(project.source_root)})
        core = {'project_id': project.project_id, 'project_root_identity_sha256': identity,
                'root_pv': head, 'lanes': lanes}
        tick()
        return UniverseSnapshot(**core, snapshot_sha256=digest(core), files_verified=request.verify_files)


class UniverseQuery(Contract):
    node_limit: int = Field(default=100, ge=1, le=200)
    edge_limit: int = Field(default=200, ge=0, le=500)
    task_offset: int = Field(default=0, ge=0, le=2000)
    source_offset: int = Field(default=0, ge=0, le=1000000)
    topology_offset: int = Field(default=0, ge=0, le=64)
    include_tasks: bool = True
    include_sources: bool = True
    include_links: bool = True
    linked_project_after: str | None = Field(default=None, pattern=UUID_PATTERN)
    query: str = Field(default='', max_length=500)
    node_kind: Literal['project', 'root_pv', 'lane', 'plan_task', 'source_reference', 'linked_project'] | None = None


class UniverseGraph(Contract):
    project_id: str
    root_pv: dict[str, JsonValue]
    graph: dict[str, JsonValue]
    source_offsets: dict[str, JsonValue]
    query_sha256: str | None = None
    node_kind: str | None = None
    mutation_performed: Literal[False] = False


def query_project(store, request):
    from .lane_contract import ViewGraph, ViewScope
    from .migrations import read_compatibility
    from .plan_runtime import PLAN_MIGRATIONS, PlanRead, PlanStore
    from .project_universe import ProjectUniverse
    from .source_authority import SOURCES_MIGRATIONS
    project = store.project if isinstance(store, LaneStore) else store
    if request.query.strip() or request.node_kind:
        return search_project(project, request)
    with bounded_project_read(project.root, time.monotonic() + 10):
        head = root_reference(project)
        graph = ViewGraph(project.project_id, ViewScope(node_limit=request.node_limit, edge_limit=request.edge_limit))
        root = graph.node('project', project.project_id, project.project_id)
        pv = graph.node('root_pv', head['head_digest'] or 'initial', 'project evidence head coordinator ' + str(head['revision']), locator=head)
        graph.edge(root, pv, 'PUBLISHED_AT')
        lane_ids = {}
        for item in project.lane_catalog():
            lane = project.lane(item['lane_id'])  # Also verifies its published database head.
            lane_ids[lane.lane_id] = graph.node('lane', lane.lane_id, lane.definition.display_label,
                locator={'lane_id': lane.lane_id, 'head_digest': item['head_digest'], 'revision': item['revision']})
            graph.edge(root, lane_ids[lane.lane_id], 'CONTAINS_LANE')
            graph.edge(pv, lane_ids[lane.lane_id], 'REFERENCES_HEAD')
        offsets = {'task_offset': request.task_offset, 'source_offset': request.source_offset}
        if request.include_tasks:
            read_compatibility(project, PLAN_MIGRATIONS)
            page = PlanStore(project).snapshot(PlanRead(offset=request.task_offset, limit=min(request.node_limit, 100)))
            ids = {}
            for row in page.tasks:
                node = graph.node('plan_task', row.definition.task_id,
                    row.definition.title, state=row.state, locator={'revision': page.revision, 'contract_digest': row.contract_digest})
                if node is None:
                    break
                ids[row.definition.task_id] = node
                graph.edge(lane_ids.get('plan'), node, 'GOVERNS_TASK')
            for row in page.tasks:
                for dependency in row.definition.dependencies:
                    graph.edge(ids.get(row.definition.task_id), ids.get(dependency), 'DEPENDS_ON')
            graph.truncated |= page.truncated
            offsets['next_task_offset'] = page.offset + len(ids) if page.truncated or len(ids) < len(page.tasks) else None
        if request.include_sources:
            read_compatibility(project, SOURCES_MIGRATIONS)
            with project.lane('sources').connection(read_only=True) as connection:
                exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='source_object'").fetchone()
                rows = connection.execute('SELECT object_id,lane_id,resolved_pointer,identity_sha256 FROM source_object ORDER BY object_id LIMIT ? OFFSET ?',
                    (request.node_limit + 1, request.source_offset)).fetchall() if exists else []
            emitted = 0
            for row in rows[:request.node_limit]:
                node = graph.node('source_reference', row['object_id'], Path(row['resolved_pointer']).name or row['object_id'],
                    locator={'object_id': row['object_id'], 'lane_id': row['lane_id'], 'identity_sha256': row['identity_sha256']})
                if node is None:
                    break
                emitted += 1
                graph.edge(lane_ids.get('sources'), node, 'REGISTERS_SOURCE')
                graph.edge(lane_ids.get(row['lane_id']), node, 'INDEXES_SOURCE')
            graph.truncated |= len(rows) > request.node_limit
            offsets['next_source_offset'] = request.source_offset + emitted if len(rows) > emitted else None
        if request.include_links:
            from .project_universe import LinkedProjectsRead
            links = ProjectUniverse(project).read(LinkedProjectsRead(after_project_id=request.linked_project_after, limit=50))
            cursor, emitted = request.linked_project_after, 0
            for row in links.links:
                target = graph.node('linked_project', row['target_project_id'], row['label'],
                    locator={'binding_digest': row['binding_digest'], 'access_granted': False})
                if target is None:
                    break
                cursor, emitted = row['target_project_id'], emitted + 1
                graph.edge(root, target, 'LINKS_PROJECT')
            more = links.truncated or emitted < len(links.links)
            graph.truncated |= more
            offsets['next_linked_project_after'] = cursor if more else None
        return UniverseGraph(project_id=project.project_id, root_pv=head, graph=graph.result(), source_offsets=offsets)


def search_project(project, request):
    """Literal filtered pages over owning lanes, without a second search database.

    Cursors count inspected records, including nonmatches. A full page can have
    no hits; callers follow the disclosed cursors to continue that category.
    Matching overflow is never consumed. Edges connect only returned matches.
    """
    from .lane_contract import ViewGraph, ViewScope
    from .migrations import read_compatibility
    from .plan_runtime import PLAN_MIGRATIONS, PlanRead, PlanStore
    from .project_universe import LinkedProjectsRead, ProjectUniverse
    from .source_authority import SOURCES_MIGRATIONS

    graph = ViewGraph(project.project_id, ViewScope(node_limit=request.node_limit, edge_limit=request.edge_limit))
    query = request.query.strip().casefold()
    offsets = {}

    def selected(kind):
        return request.node_kind in (None, kind)

    def add(kind, key, label, *, state=None, locator=None):
        if not selected(kind) or (query and query not in json_text([key, label, state, locator or {}]).casefold()):
            return None, True
        node = graph.node(kind, key, label, state=state, locator=locator)
        return node, node is not None

    def edge(source, target, kind):
        if source and target:
            graph.edge(source, target, kind)

    with bounded_project_read(project.root, time.monotonic() + 10):
        head = root_reference(project)
        topology = [('project', project.project_id, project.project_id, {}),
                    ('root_pv', head['head_digest'] or 'initial', 'project evidence head coordinator ' + str(head['revision']), head)]
        lane_ids = {}
        for item in project.lane_catalog():
            lane = project.lane(item['lane_id'])
            topology.append(('lane', lane.lane_id, lane.definition.display_label,
                {'lane_id': lane.lane_id, 'head_digest': item['head_digest'], 'revision': item['revision']}))
        static_ids, consumed = {}, 0
        for kind, key, label, locator in topology[request.topology_offset:]:
            node, advance = add(kind, key, label, locator=locator)
            if not advance:
                break
            consumed += 1
            static_ids[kind, key] = node
            if kind == 'lane':
                lane_ids[key] = node
        more = request.topology_offset + consumed < len(topology)
        offsets['next_topology_offset'] = request.topology_offset + consumed if more else None
        graph.truncated |= more
        root = static_ids.get(('project', project.project_id))
        pv = static_ids.get(('root_pv', head['head_digest'] or 'initial'))
        edge(root, pv, 'PUBLISHED_AT')
        for lane_node in lane_ids.values():
            edge(root, lane_node, 'CONTAINS_LANE')
            edge(pv, lane_node, 'REFERENCES_HEAD')
        if request.include_tasks and selected('plan_task'):
            read_compatibility(project, PLAN_MIGRATIONS)
            page = PlanStore(project).snapshot(PlanRead(offset=request.task_offset, limit=100))
            consumed, ids = 0, {}
            for row in page.tasks:
                node, advance = add('plan_task', row.definition.task_id, row.definition.title, state=row.state,
                    locator={'revision': page.revision, 'contract_digest': row.contract_digest})
                if not advance:
                    break
                consumed += 1
                ids[row.definition.task_id] = node
                edge(lane_ids.get('plan'), node, 'GOVERNS_TASK')
            for row in page.tasks:
                for dependency in row.definition.dependencies:
                    edge(ids.get(row.definition.task_id), ids.get(dependency), 'DEPENDS_ON')
            more = page.truncated or consumed < len(page.tasks)
            offsets['next_task_offset'] = page.offset + consumed if more else None
            graph.truncated |= more
        if request.include_sources and selected('source_reference'):
            read_compatibility(project, SOURCES_MIGRATIONS)
            with project.lane('sources').connection(read_only=True) as connection:
                exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='source_object'").fetchone()
                rows = connection.execute('SELECT object_id,lane_id,resolved_pointer,identity_sha256 FROM source_object ORDER BY object_id LIMIT 101 OFFSET ?',
                    (request.source_offset,)).fetchall() if exists else []
            consumed = 0
            for row in rows[:100]:
                node, advance = add('source_reference', row['object_id'], Path(row['resolved_pointer']).name or row['object_id'],
                    locator={'object_id': row['object_id'], 'lane_id': row['lane_id'],
                        'resolved_pointer': row['resolved_pointer'], 'identity_sha256': row['identity_sha256']})
                if not advance:
                    break
                consumed += 1
                edge(lane_ids.get('sources'), node, 'REGISTERS_SOURCE')
                edge(lane_ids.get(row['lane_id']), node, 'INDEXES_SOURCE')
            more = len(rows) > consumed
            offsets['next_source_offset'] = request.source_offset + consumed if more else None
            graph.truncated |= more
        if request.include_links and selected('linked_project'):
            page = ProjectUniverse(project).read(LinkedProjectsRead(after_project_id=request.linked_project_after, limit=50))
            consumed, cursor = 0, request.linked_project_after
            for row in page.links:
                node, advance = add('linked_project', row['target_project_id'], row['label'],
                    locator={'binding_digest': row['binding_digest'], 'access_granted': False})
                if not advance:
                    break
                consumed, cursor = consumed + 1, row['target_project_id']
                edge(root, node, 'LINKS_PROJECT')
            more = page.truncated or consumed < len(page.links)
            offsets['next_linked_project_after'] = cursor if more else None
            graph.truncated |= more
        return UniverseGraph(project_id=project.project_id, root_pv=head, graph=graph.result(), source_offsets=offsets,
            query_sha256=hashlib.sha256(request.query.strip().encode()).hexdigest(), node_kind=request.node_kind)


def register_universe_snapshot_actions(engine):
    engine.registry.register(ActionSpec('project_evidence_map_inspect',
        'Verify coherent project evidence head coordinator, initialized lane heads and bounded registered-file identities without refresh.',
        UniverseInspect, UniverseSnapshot,
        lambda context, request: inspect_project(engine.directory.open(context.project_id), request),
        profile='universe', workflow='inspect-project-evidence-map', queryable_in_delta=True, studio_read=True))
    engine.registry.register(ActionSpec('project_evidence_map_query',
        'Read or literally filter bounded project evidence head coordinator, lane, Plan, Source and project-link nodes; follow category cursors.',
        UniverseQuery, UniverseGraph,
        lambda context, request: query_project(engine.directory.open(context.project_id), request),
        profile='universe', workflow='inspect-project-evidence-map', queryable_in_delta=True, studio_read=True))
    register_universe_view(engine)


def universe_topology_head(project):
    """Business inputs only: a view publication cannot invalidate its own source.

The live inspection action supplies exact project evidence head coordinator coordinates. This export is
the project topology, so writer receipts and view selectors are not inputs.
"""
    selections = (
        ('plan', 'plan_events', 'SELECT sequence,digest FROM plan_events ORDER BY sequence DESC LIMIT 1'),
        ('sources', 'registry_event', 'SELECT event_id,event_sha256 FROM registry_event ORDER BY event_id LIMIT 10001'),
        ('universe', 'universe_link_events', 'SELECT sequence,digest FROM universe_link_events ORDER BY sequence DESC LIMIT 1'),
    )
    result = {'lanes': [{'lane_id': row['lane_id'], 'kind': row['kind']} for row in project.lane_catalog()]}
    for lane_id, table, query in selections:
        with project.lane(lane_id).connection(read_only=True) as connection:
            exists = connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (table,)).fetchone()
            rows = [dict(row) for row in connection.execute(query)] if exists else []
            if len(rows) > 10000:
                raise LaneError('VIEW_SOURCE_BUDGET', 'Use bounded Universe queries for a project larger than this topology export budget.')
            result[lane_id] = {'records': len(rows), 'digest': digest(rows)}
    return result


def universe_topology_view(project, scope):
    # The live graph owns exact project evidence head coordinator references; the natural topology export
    # intentionally omits coordinates changed by its own writer/publication.
    value = query_project(project, UniverseQuery(node_limit=min(scope.node_limit + 1, 200), edge_limit=scope.edge_limit)).graph
    roots = {node['id'] for node in value['nodes'] if node['kind'] == 'root_pv'}
    nodes = [node for node in value['nodes'] if node['id'] not in roots]
    for node in nodes:
        if node['kind'] == 'lane':
            node['locator'] = {'lane_id': node['key']}
    edges = [edge for edge in value['edges'] if edge['source'] not in roots and edge['target'] not in roots]
    return {'nodes': nodes[:scope.node_limit], 'edges': edges, 'truncated': value['truncated']}


def register_universe_view(engine):
    from .lane_contract import LaneView
    from .plan_runtime import PLAN_MIGRATIONS
    from .project_universe import UNIVERSE_MIGRATIONS
    from .source_authority import SOURCES_MIGRATIONS
    engine.registry.register_view(LaneView('universe.topology', 'universe', 'universe',
        'Project lane topology, Plan dependencies, Sources and explicit project links; exact project evidence head coordinator coordinates use project_evidence_map_inspect.',
        universe_topology_view, ('plan', 'sources', 'universe'), (*PLAN_MIGRATIONS, *SOURCES_MIGRATIONS, *UNIVERSE_MIGRATIONS),
        'project_universe.mmd', 'project_universe.dot', node_kinds=('project', 'lane', 'plan_task', 'source_reference', 'linked_project'),
        edge_kinds=('CONTAINS_LANE', 'GOVERNS_TASK', 'DEPENDS_ON', 'REGISTERS_SOURCE', 'INDEXES_SOURCE', 'LINKS_PROJECT'),
        head_reader=universe_topology_head))
