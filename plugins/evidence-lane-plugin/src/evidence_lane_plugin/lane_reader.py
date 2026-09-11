"""Explicit bounded reads across separately selected project authorities.

Adapts LaneReader's authorization, budgets and attribution to v4 registry owner
actions. No legacy lane/PV database is opened, refreshed, attached or copied.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from .errors import LaneError
from .lanes import get_lane, is_named_custom_lane, lane_family
from .migrations import read_compatibility, verify_schema_history_files
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import FORMAT_VERSION, bounded_project_read, json_text, now
from .tool_routes import ToolRoute


class SearchBounds(Contract):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)
    snapshot_limit: int = Field(default=4, ge=1, le=8)
    snapshot_offset: int = Field(default=0, ge=0, le=127)
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)
    max_bytes: int = Field(default=131_072, ge=4096, le=262_144)
    retrieval: Literal['fts5', 'bm25', 'tfidf', 'hybrid', 'rank_bm25'] = 'fts5'
    candidate_limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode='after')
    def lexical_terms(self):
        if not 1 <= len(re.findall(r'\w+', self.query, flags=re.UNICODE)) <= 12:
            raise ValueError('Use one to twelve literal lexical terms')
        if self.retrieval in {'tfidf', 'hybrid', 'rank_bm25'} and self.candidate_limit < self.limit:
            raise ValueError('The candidate limit must cover the selected result limit')
        return self


class LaneSearchRequest(SearchBounds):
    lane_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')


class SearchPage(Contract):
    project_id: str
    root_pv: dict[str, JsonValue]
    plan_boundary: dict[str, JsonValue]
    query: str
    retrieval: str
    arms: list[dict[str, JsonValue]]
    registered_lanes: list[str]
    complete_within_selected_scope: bool
    owner_calls: int
    elapsed_ms: int
    observed_at: str
    snapshot_scope: Literal['one_published_root_and_independent_owner_reads'] = 'one_published_root_and_independent_owner_reads'
    source_currentness: Literal['not_rechecked'] = 'not_rechecked'
    ranking_scope: Literal['within_each_owner_snapshot_only'] = 'within_each_owner_snapshot_only'
    mutation_performed: Literal[False] = False
    refresh_performed: Literal[False] = False
    databases_merged: Literal[False] = False


class LaneCatalogRequest(Contract):
    lane_ids: list[str] = Field(default_factory=list, max_length=32)
    kind: Literal['authority', 'sector'] | None = None
    include_schema: bool = False
    max_bytes: int = Field(default=131_072, ge=4096, le=1_048_576)


class LaneCatalogPage(Contract):
    lanes: list[dict[str, JsonValue]]
    lane_count: int
    registry_digest: str
    availability_basis: Literal['registered_contracts_only'] = 'registered_contracts_only'
    installed_readiness_verified: Literal[False] = False


class LaneStatusRequest(Contract):
    lane_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    include_views: bool = True
    max_views: int = Field(default=8, ge=1, le=16)
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)
    max_bytes: int = Field(default=262_144, ge=4096, le=1_048_576)


class LaneReadResult(Contract):
    project_id: str
    lane_id: str
    operation: Literal['lane_status', 'lane_fetch']
    root_pv: dict[str, JsonValue]
    result: dict[str, JsonValue]
    source_currentness: Literal['not_rechecked'] = 'not_rechecked'
    mutation_performed: Literal[False] = False
    refresh_performed: Literal[False] = False


class LaneFetchRequest(Contract):
    lane_id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
    snapshot_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    path: str = Field(min_length=1, max_length=1000)
    representation: Literal['primary', 'original_source'] = 'primary'
    byte_offset: int = Field(default=0, ge=0, le=33_554_432)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    max_lines: int = Field(default=400, ge=1, le=1000)
    max_bytes: int = Field(default=65_536, ge=1024, le=131_072)
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)
    max_output_bytes: int = Field(default=262_144, ge=4096, le=262_144)

    @model_validator(mode='after')
    def exact_path(self):
        path = self.path.replace('\\', '/')
        if (PurePosixPath(path).is_absolute() or ':' in path or '\x00' in path
                or any(part in {'', '.', '..'} for part in path.split('/'))):
            raise ValueError('Use an exact relative stored source path')
        if self.end_line is not None and self.end_line < (self.start_line or 1):
            raise ValueError('The line range must be ordered')
        return self


class OwnerQuery(Contract):
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class ProjectQuery(Contract):
    project_id: str = Field(pattern=UUID_PATTERN)
    queries: list[OwnerQuery] = Field(min_length=1, max_length=4)
    expected_plan_revision: int | None = Field(default=None, ge=1)


class CrossProjectQuery(Contract):
    projects: list[ProjectQuery] = Field(min_length=2, max_length=4)
    require_links: bool = False
    timeout_ms: int = Field(default=10_000, ge=100, le=30_000)
    max_bytes: int = Field(default=131_072, ge=2048, le=262_144)

    @model_validator(mode="after")
    def bounded_selection(self):
        if len({item.project_id for item in self.projects}) != len(self.projects):
            raise ValueError("Select each project exactly once")
        if sum(len(item.queries) for item in self.projects) > 8:
            raise ValueError("Select at most eight owner queries in total")
        if len(json_text(self.model_dump()).encode()) > 32_768:
            raise ValueError("Cross-project query arguments exceed their budget")
        return self


class CrossProjectPage(Contract):
    project_id: str
    source_client_id: str
    projects: list[dict[str, JsonValue]]
    observed_at: str
    elapsed_ms: int
    snapshot_scope: Literal["independent_project_reads_with_unchanged_plan_and_schema"] = "independent_project_reads_with_unchanged_plan_and_schema"
    mutation_performed: Literal[False] = False
    refresh_performed: Literal[False] = False
    databases_merged: Literal[False] = False


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def plan_boundary(store):
    from .plan_runtime import PLAN_MIGRATIONS
    read_compatibility(store, PLAN_MIGRATIONS)
    with store.lane('plan').connection(read_only=True) as connection:
        connection.execute("BEGIN")
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='plan_current'").fetchone():
            return {"revision": None, "event_head": None}
        row = connection.execute("SELECT revision FROM plan_current WHERE singleton=1").fetchone()
        event = connection.execute("SELECT digest FROM plan_events ORDER BY sequence DESC LIMIT 1").fetchone()
        return {"revision": row[0] if row else None, "event_head": event[0] if event else None}


def lexical_tool_routes(name, handler):
    return (ToolRoute(name + '.sqlite_lexical', handler, ('Python', 'SQLite_FTS5_BM25'),
        argument_values=(('retrieval', ('fts5', 'bm25', 'tfidf', 'hybrid')),)),
        ToolRoute(name + '.rank_bm25', handler, ('Python', 'SQLite_FTS5_BM25', 'rank_bm25'),
            argument_values=(('retrieval', ('rank_bm25',)),)))


class LaneReader:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _select_lanes(lane_ids):
        from .lanes import LANE_REGISTRY
        if not lane_ids or len(set(lane_ids)) != len(lane_ids) or any(
                lane not in LANE_REGISTRY and not is_named_custom_lane(lane) for lane in lane_ids):
            raise LaneError('SEARCH_LANES_INVALID', 'Select distinct canonical retained lanes.')
        return [get_lane(key) for key in lane_ids]

    def _catalog_row(self, lane, *, include_schema=False):
        from .lanes import lane_artifact_contract, lane_schema_asset
        searches = [spec for spec in self.engine.registry.search_actions() if lane_family(lane.canonical_lane_id) in spec.search.lanes]
        fetches = [spec for spec in self.engine.registry.fetch_actions() if lane_family(lane.canonical_lane_id) in spec.fetch.lanes]
        def route(spec, kind):
            return {'action': spec.name, 'workflow': spec.workflow, 'schema_digest': digest(spec.schema()),
                'contract': getattr(spec, kind).schema()}
        row = {'definition': lane.as_dict(), 'search': [route(spec, 'search') for spec in searches],
            'fetch': [route(spec, 'fetch') for spec in fetches],
            'views': [spec for spec in self.engine.registry.view_schemas() if spec['lane_id'] == lane.canonical_lane_id],
            'artifact_roles': lane_artifact_contract(lane.canonical_lane_id)}
        if include_schema:
            row['declared_schema'] = lane_schema_asset(lane.canonical_lane_id)
        if is_named_custom_lane(lane.canonical_lane_id):
            row['views'] = [self.engine.registry.get_view(lane.canonical_lane_id + '.structure').schema()]
        return json.loads(json_text(row))

    def lane_catalog(self, context, request):
        from .lanes import LANE_REGISTRY
        lane_ids = list(LANE_REGISTRY)
        if context.project_id:
            store = self.engine.directory.open(context.project_id)
            from .custom_lanes import ReadRegistrations, records
            page = records(store, ReadRegistrations(limit=100, max_bytes=262_144))
            lane_ids.extend(row['lane_id'] for row in page['rows'])
            if page['next_offset'] is not None and not request.lane_ids:
                raise LaneError('LANE_CATALOG_INSTANCE_BUDGET', 'Select named lane IDs from the paginated Custom registration reader.')
        selected = self._select_lanes(request.lane_ids or lane_ids)
        rows = [self._catalog_row(lane, include_schema=request.include_schema) for lane in selected
                if request.kind is None or lane.kind == request.kind]
        result = LaneCatalogPage(lanes=rows, lane_count=len(rows), registry_digest=digest(rows))
        self._search_output_budget(result.model_dump(mode='json'), request.max_bytes)
        return result

    def _owner_read(self, context, store, spec, arguments, deadline, maximum):
        if not spec.queryable_in_delta or not spec.project_required:
            raise LaneError('INVALID_OWNER_READ', 'Select a registered project read operation.')
        if time.monotonic() >= deadline:
            raise LaneError('QUERY_TIMEOUT', 'The selected lane read exceeded its time budget.')
        if context.execution is not None:
            context.execution._before_more_work()
            context.execution.guard.spend_call()
        self.engine.registry.validate(spec.name, arguments, context)
        target = store.lane(arguments['lane_id']) if is_named_custom_lane(arguments.get('lane_id')) else store
        schemas = read_compatibility(target, spec.read_migrations)
        value = self.engine.registry.execute(spec.name, arguments, context)
        if value.get('project_id') != store.project_id:
            raise LaneError('SEARCH_OWNER_BINDING', 'The owning reader returned a different project.')
        self._search_output_budget(value, maximum)
        if read_compatibility(target, spec.read_migrations) != schemas:
            raise LaneError('QUERY_SCHEMA_CHANGED', 'The owning schema changed during the read.')
        if time.monotonic() >= deadline:
            raise LaneError('QUERY_TIMEOUT', 'The selected lane read exceeded its time budget.')
        return value, schemas

    def lane_status(self, context, request):
        from .universe_snapshot import root_reference
        lane_definition = self._select_lanes([request.lane_id])[0]
        store = self.engine.directory.open(context.project_id)
        deadline = time.monotonic() + request.timeout_ms / 1000
        with bounded_project_read(store.root, deadline):
            root = root_reference(store)
            boundary = plan_boundary(store)
            if context.expected_revision is not None and boundary['revision'] != context.expected_revision:
                raise LaneError('QUERY_PLAN_REVISION_MISMATCH', 'Read the selected current Plan revision.')
            selected = next((row for row in store.lane_catalog() if row['lane_id'] == request.lane_id), None)
            body = {'catalog': self._catalog_row(lane_definition), 'initialized': selected is not None,
                'published_lane': selected, 'plan_boundary': boundary, 'objects_verified': False,
                'database_head_verified': False, 'schema_history_verified': False, 'views': []}
            if selected is not None:
                lane = store.lane(request.lane_id)
                with lane.connection(read_only=True) as connection:
                    connection.execute('BEGIN')
                    schemas = [dict(row) for row in connection.execute(
                        'SELECT owner,version,digest FROM schema_migrations ORDER BY owner,version LIMIT 257')]
                    if len(schemas) > 256:
                        raise LaneError('LANE_STATUS_SCHEMA_BUDGET', 'Use a bounded schema-history inspection.')
                    verify_schema_history_files(lane, connection)
                    count, size = connection.execute('SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM objects').fetchone()
                body.update({'schema_history': schemas, 'schema_history_digest': digest(schemas),
                    'object_references': count, 'referenced_bytes': size, 'schema_history_verified': True,
                    'database_head_verified': selected['head_digest'] is not None})
                if request.include_views:
                    views = body['catalog']['views']
                    if len(views) > request.max_views:
                        raise LaneError('LANE_STATUS_VIEW_BUDGET', 'Select a smaller view inspection or increase max_views.')
                    spec = self.engine.registry.get('lane_view_read')
                    for view in views:
                        value, _ = self._owner_read(context, store, spec,
                            {'view_id': view['view_id'], 'include_content': False,
                             'max_bytes': min(request.max_bytes, 262144)}, deadline, request.max_bytes)
                        body['views'].append(value)
            if root_reference(store) != root or plan_boundary(store) != boundary:
                raise LaneError('QUERY_PLAN_CHANGED', 'The selected publication or Plan changed during status.')
            if context.authorize is not None:
                context.authorize('read')
            result = LaneReadResult(project_id=store.project_id, lane_id=request.lane_id,
                operation='lane_status', root_pv=root, result=body)
            self._search_output_budget(result.model_dump(mode='json'), request.max_bytes)
            return result

    def fetch_source(self, context, request):
        from .universe_snapshot import root_reference
        self._select_lanes([request.lane_id])
        spec = next((spec for spec in self.engine.registry.fetch_actions() if lane_family(request.lane_id) in spec.fetch.lanes), None)
        if spec is None:
            raise LaneError('LANE_FETCH_UNAVAILABLE', 'This lane has no registered immutable source-byte reader.')
        route = spec.fetch
        path = request.path.replace('\\', '/')
        arguments = self._lane_arguments(spec, request.lane_id) | {'snapshot_id': request.snapshot_id,
            route.offset_argument: request.byte_offset, 'max_bytes': request.max_bytes}
        if route.primary_representation:
            arguments['representation'] = route.primary_representation if request.representation == 'primary' else 'original_source'
        if route.path_argument:
            arguments[route.path_argument] = route.path_prefix + path
        if request.start_line is not None or request.end_line is not None or request.max_lines != 400:
            if not {'start_line', 'end_line', 'max_lines'} <= set(spec.input_model.model_fields):
                raise LaneError('LANE_FETCH_LINE_RANGE_UNSUPPORTED', 'Use byte ranges for this owning format.')
            arguments.update(start_line=request.start_line, end_line=request.end_line, max_lines=request.max_lines)
        self.engine.registry.validate(spec.name, arguments, context)
        store = self.engine.directory.open(context.project_id)
        deadline = time.monotonic() + request.timeout_ms / 1000
        with bounded_project_read(store.root, deadline):
            root, boundary = root_reference(store), plan_boundary(store)
            if context.expected_revision is not None and boundary['revision'] != context.expected_revision:
                raise LaneError('QUERY_PLAN_REVISION_MISMATCH', 'Read the selected current Plan revision.')
            value, schemas = self._owner_read(context, store, spec, arguments, deadline, request.max_output_bytes)
            payload = value.get('result', value)
            if str(payload.get(route.path_result, '')).replace('\\', '/') != path:
                raise LaneError('LANE_FETCH_PATH_MISMATCH', 'The snapshot representation belongs to a different stored source path.')
            search = next((item for item in self.engine.registry.search_actions() if lane_family(request.lane_id) in item.search.lanes), None)
            body = {'snapshot_id': request.snapshot_id, 'path': path, 'requested_representation': request.representation,
                'action': spec.name, 'arguments': arguments, 'schema_compatibility': schemas,
                'result_digest': digest(value), 'read': value,
                'typed_facts_action': search.name if search else None, 'plan_boundary': boundary}
            if root_reference(store) != root or plan_boundary(store) != boundary:
                raise LaneError('QUERY_PLAN_CHANGED', 'The selected publication or Plan changed during fetch.')
            if context.authorize is not None:
                context.authorize('read')
            result = LaneReadResult(project_id=store.project_id, lane_id=request.lane_id,
                operation='lane_fetch', root_pv=root, result=body)
            self._search_output_budget(result.model_dump(mode='json'), request.max_output_bytes)
            return result

    def search(self, context, request):
        return self.search_lanes(context, request, [request.lane_id])

    def search_lanes(self, context, request, lane_ids):
        """Read separate registered owners; never synthesize a shared index."""
        from .universe_snapshot import root_reference
        self._select_lanes(lane_ids)
        started = time.monotonic()
        deadline = started + request.timeout_ms / 1000
        routes = {lane: spec for spec in self.engine.registry.search_actions() for lane in spec.search.lanes}
        selected = [(lane, routes.get(lane_family(lane))) for lane in lane_ids]
        rerank = request.retrieval in {'tfidf', 'hybrid', 'rank_bm25'}
        def method_available(spec):
            return spec is not None and ((not rerank or spec.search.rerank_text is not None)
                and (request.retrieval != 'bm25' or spec.search.basis == 'sqlite_fts5_bm25'))
        # Check permission for every owning read before returning any results.
        for lane_id, spec in selected:
            if not method_available(spec):
                continue
            arguments = self._search_arguments(spec, request, lane_id)
            if spec.search.current_action:
                arguments['snapshot_id'] = '0' * 64  # Schema validation only, never an executed selection.
                current = self.engine.registry.get(spec.search.current_action)
                self.engine.registry.validate(current.name, self._lane_arguments(current, lane_id), context)
            self.engine.registry.validate(spec.name, arguments, context)
        store = self.engine.directory.open(context.project_id)
        calls = 0

        def invoke(spec, arguments):
            nonlocal calls
            calls += 1
            return self._owner_read(context, store, spec, arguments, deadline, request.max_bytes)

        arms = []
        policy = self.engine.registry.control_plane
        policy_batch = policy.readonly_batch(context.project_id) if policy is not None else nullcontext()
        with bounded_project_read(store.root, deadline), policy_batch:
            boundary = plan_boundary(store)
            if context.expected_revision is not None and boundary['revision'] != context.expected_revision:
                raise LaneError('QUERY_PLAN_REVISION_MISMATCH', 'Search requires the selected current Plan revision.')
            root = root_reference(store)
            for lane_id, spec in selected:
                if not method_available(spec):
                    arms.append({'lane_id': lane_id, 'state': 'unavailable',
                        'reason': 'no_registered_content_search' if spec is None else 'requested_ranking_not_supported',
                        'owner_search_action': spec.name if spec else None,
                        'searched': False, 'hit_count': 0, 'queries': [], 'truncated': False})
                    continue
                route = spec.search
                candidates = [None]
                catalog_digest, catalog_truncated, more_snapshots = None, False, False
                current_value = None
                owner_initialized = True
                if route.current_action:
                    current = self.engine.registry.get(route.current_action)
                    current_value, _ = invoke(current, self._lane_arguments(current, lane_id))
                    payload = current_value.get('result', current_value)
                    rows = payload.get(route.current_items)
                    if not isinstance(rows, list) or len(rows) > 128:
                        raise LaneError('SEARCH_ROUTE_RESULT', 'The owning snapshot catalog violates its declared search route.')
                    candidates = rows[request.snapshot_offset:request.snapshot_offset + request.snapshot_limit]
                    catalog_digest = digest(current_value)
                    catalog_truncated = bool(payload.get('truncated', False))
                    more_snapshots = len(rows) > request.snapshot_offset + len(candidates)
                queries, hit_count = [], 0
                truncated = catalog_truncated or more_snapshots
                for snapshot in candidates:
                    arguments = self._search_arguments(spec, request, lane_id)
                    if snapshot is not None:
                        arguments['snapshot_id'] = snapshot['snapshot_id']
                    value, schemas = invoke(spec, arguments)
                    own_schemas = [row for row in schemas if row['owner'] in get_lane(lane_id).schema_owners]
                    owner_initialized = not own_schemas or any(row['status'] != 'not_initialized' for row in own_schemas)
                    payload = value.get('result', value)
                    hits = payload.get(route.items)
                    if not isinstance(hits, list) or len(hits) > arguments['limit']:
                        raise LaneError('SEARCH_ROUTE_RESULT', 'The owning reader violates its declared bounded hit collection.')
                    ranking = None
                    if rerank:
                        from .hybrid_retrieval import rank_owner_candidates
                        try:
                            ranking = rank_owner_candidates(request.query, hits, text_field=route.rerank_text,
                                method=request.retrieval, limit=request.limit)
                        except ValueError as error:
                            raise LaneError('SEARCH_RANKING_INVALID', str(error)) from None
                    hit_count += min(len(hits), request.limit)
                    truncated |= (bool(payload.get('truncated', False))
                        or payload.get('next_offset') is not None or len(hits) > request.limit)
                    queries.append({'action': spec.name, 'arguments': arguments, 'schema_compatibility': schemas,
                        'snapshot_id': arguments.get('snapshot_id'), 'result_digest': digest(value), 'result': value,
                        'ranking': ranking, 'owner_result_unchanged': True,
                        'candidate_limit_requested': request.candidate_limit if rerank else None,
                        'candidate_limit_used': arguments['limit'] if rerank else None})
                    self._search_output_budget([*arms, queries], request.max_bytes)
                if current_value is not None:
                    observed, _ = invoke(current, self._lane_arguments(current, lane_id))
                    if observed != current_value:
                        raise LaneError('SEARCH_CURRENT_CHANGED', 'The lane snapshot selection changed during search.')
                state = 'hit' if hit_count else 'no_hit'
                if not owner_initialized or (current_value is not None and not current_value.get('result', current_value).get('initialized', True)):
                    state = 'uninitialized'
                arms.append({'lane_id': lane_id, 'state': state, 'searched': bool(candidates),
                    'search_basis': route.basis, 'match_mode': 'any_literal_term',
                    'content_scope': 'primary_snapshot_text' if route.current_action else 'current_authority_records',
                    'hit_count': hit_count, 'queries': queries, 'truncated': truncated,
                    'current_catalog_digest': catalog_digest, 'catalog_truncated': catalog_truncated,
                    'snapshot_offset': request.snapshot_offset if route.current_action else None,
                    'next_snapshot_offset': request.snapshot_offset + len(candidates) if more_snapshots else None,
                    'snapshot_catalog_limit': 128 if route.current_action else None})
                self._search_output_budget(arms, request.max_bytes)
            if plan_boundary(store) != boundary or root_reference(store) != root:
                raise LaneError('QUERY_PLAN_CHANGED', 'The project publication or Plan changed during search.')
            if context.authorize is not None:
                context.authorize('read')
        if time.monotonic() >= deadline:
            raise LaneError('QUERY_TIMEOUT', 'The selected lane read exceeded its time budget.')
        page = SearchPage(project_id=store.project_id, root_pv=root, plan_boundary=boundary, query=request.query,
            retrieval=request.retrieval,
            arms=arms, registered_lanes=sorted(routes), owner_calls=calls, observed_at=now(),
            complete_within_selected_scope=all(arm['state'] not in {'unavailable', 'uninitialized'} and not arm['truncated'] for arm in arms),
            elapsed_ms=int((time.monotonic() - started) * 1000))
        self._search_output_budget(page.model_dump(mode='json'), request.max_bytes)
        return page

    @staticmethod
    def _lane_arguments(spec, lane_id):
        return {'lane_id': lane_id} if 'lane_id' in spec.input_model.model_fields else {}

    @classmethod
    def _search_arguments(cls, spec, request, lane_id):
        limit = request.limit
        if request.retrieval in {'tfidf', 'hybrid', 'rank_bm25'}:
            maximum = spec.input_model.model_json_schema()['properties']['limit'].get('maximum', 20)
            limit = min(request.candidate_limit, maximum)
        arguments = cls._lane_arguments(spec, lane_id) | {'query': request.query, 'limit': limit}
        if spec.search.collection:
            arguments['collection'] = spec.search.collection
        if spec.search.match_mode:
            arguments['match_mode'] = spec.search.match_mode
        if 'max_bytes' in spec.input_model.model_fields:
            properties = spec.input_model.model_json_schema()['properties']['max_bytes']
            arguments['max_bytes'] = max(properties.get('minimum', 0), min(request.max_bytes, 65_536))
        return arguments

    @staticmethod
    def _search_output_budget(value, maximum):
        if len(json_text(value).encode()) > maximum:
            raise LaneError('QUERY_OUTPUT_BUDGET', 'Reduce the selected lanes, snapshots or per-snapshot hit limit.')

    def _context(self, context, project_id):
        if context.project_context is None:
            raise LaneError("CROSS_PROJECT_AUTHORIZATION_UNAVAILABLE", "This connection cannot authorize additional project selections.")
        target = context.project_context(project_id, "read")
        if (target.project_id != project_id or target.client_id != context.client_id
                or "read" not in target.permissions or target.authorize is None):
            raise LaneError("CROSS_PROJECT_AUTHORIZATION_INVALID", "The target context must preserve the authenticated principal and exact project.")
        allowed = context.allowed_actions
        if target.allowed_actions is not None:
            allowed = target.allowed_actions if allowed is None else allowed & target.allowed_actions
        return replace(target, permissions=frozenset({"read"}), allowed_actions=allowed,
                       execution=None, expected_revision=None, project_context=None)

    def search_cross_project(self, context, request):
        from .project_universe import ProjectUniverse
        started = time.monotonic()
        deadline = started + request.timeout_ms / 1000
        source = self.engine.directory.open(context.project_id)
        selected = []
        # Validate every project selection and action before reading any results.
        for project in request.projects:
            target_context = self._context(context, project.project_id)
            store = self.engine.directory.open(project.project_id)
            link = ProjectUniverse(source).binding(store) if request.require_links and store.project_id != source.project_id else None
            for query in project.queries:
                spec = self.engine.registry.get(query.action)
                if not spec.cross_project_read:
                    raise LaneError("NOT_A_CROSS_PROJECT_QUERY", "Select an explicitly admitted read-only owner query.")
                self.engine.registry.validate(query.action, query.arguments, target_context)
            selected.append((project, store, link))
        values, observations = [], []
        for project, store, link in selected:
            target_context = self._context(context, project.project_id)
            with bounded_project_read(store.root, deadline):
                boundary = plan_boundary(store)
                if project.expected_plan_revision is not None and boundary["revision"] != project.expected_plan_revision:
                    raise LaneError("QUERY_PLAN_REVISION_MISMATCH", "The selected project does not have the requested current Plan revision.")
                results = []
                for query in project.queries:
                    spec = self.engine.registry.get(query.action)
                    schemas = read_compatibility(store, spec.read_migrations)
                    result = self.engine.registry.execute(query.action, query.arguments,
                        replace(target_context, expected_revision=project.expected_plan_revision))
                    if time.monotonic() >= deadline:
                        raise LaneError("QUERY_TIMEOUT", "The query exceeded its selected time budget.")
                    if read_compatibility(store, spec.read_migrations) != schemas:
                        raise LaneError("QUERY_SCHEMA_CHANGED", "The selected owner schema changed during the read.")
                    results.append({"action": query.action, "arguments_digest": digest(query.arguments),
                        "schema_compatibility": schemas, "result_digest": digest(result), "result": result})
                if plan_boundary(store) != boundary:
                    raise LaneError("QUERY_PLAN_CHANGED", "The project's Plan changed while its owner snapshots were read.")
            self._context(context, project.project_id)
            values.append({"project_id": store.project_id, "state_root": str(store.root),
                "source_root": str(store.source_root), "format_version": FORMAT_VERSION,
                "plan_boundary": boundary, "link": link, "queries": results})
            if len(json_text(values).encode()) > request.max_bytes - 1024:
                raise LaneError("QUERY_OUTPUT_BUDGET", "Reduce the selected projects, query pages or requested payloads.")
            observations.append((store, boundary, link, results))
        # Recheck earlier targets after later reads; never return under revoked
        # authorization, changed root binding, stale link, Plan or owner schema.
        if context.authorize is not None:
            context.authorize("read")
        for store, boundary, link, results in observations:
            self._context(context, store.project_id)
            current = self.engine.directory.open(store.project_id)
            if current.root != store.root or current.source_root != store.source_root:
                raise LaneError("QUERY_PROJECT_CHANGED", "A selected project binding changed during the query.")
            if link is not None and ProjectUniverse(source).binding(current) != link:
                raise LaneError("QUERY_LINK_CHANGED", "The selected project link changed during the query.")
            with bounded_project_read(store.root, deadline):
                if plan_boundary(current) != boundary:
                    raise LaneError("QUERY_PLAN_CHANGED", "A selected project's Plan changed during the query.")
                for result in results:
                    schemas = self.engine.registry.get(result["action"]).read_migrations
                    if read_compatibility(current, schemas) != result["schema_compatibility"]:
                        raise LaneError("QUERY_SCHEMA_CHANGED", "A selected owner schema changed during the query.")
        page = CrossProjectPage(project_id=source.project_id, source_client_id=context.client_id,
            projects=values, observed_at=now(), elapsed_ms=int((time.monotonic() - started) * 1000))
        if len(json_text(page.model_dump()).encode()) > request.max_bytes:
            raise LaneError("QUERY_OUTPUT_BUDGET", "The attributed response exceeds its output budget.")
        return page


def register_cross_project_actions(engine):
    reader = LaneReader(engine)
    engine.registry.register(ActionSpec('lane_catalog', 'Discover canonical authority/sector definitions and their registered search, fetch and view contracts.',
        LaneCatalogRequest, LaneCatalogPage, reader.lane_catalog, project_required=False,
        profile='projects', queryable_in_delta=True, workflow='evi'))
    engine.registry.register(ActionSpec('lane_status', 'Inspect one physical lane, its published database head, schema history and stored view freshness without refresh.',
        LaneStatusRequest, LaneReadResult, reader.lane_status,
        profile='projects', queryable_in_delta=True, studio_read=True, cross_project_read=True, workflow='evi'))
    engine.registry.register(ActionSpec('lane_fetch', 'Fetch bounded immutable source bytes through the owning lane using an exact snapshot and path.',
        LaneFetchRequest, LaneReadResult, reader.fetch_source,
        profile='projects', queryable_in_delta=True, studio_read=True, cross_project_read=True, workflow='source-intake'))
    search = reader.search
    engine.registry.register(ActionSpec('lane_search', 'Search one separate lane through its current registered owner and disclose snapshot coverage.',
        LaneSearchRequest, SearchPage, search,
        profile='projects', queryable_in_delta=True, studio_read=True, workflow='evi',
        tool_routes=lexical_tool_routes('lane_search', search)))
    engine.registry.register(ActionSpec("cross_project_query", "Read explicit authorized projects in place with bounded attributed owner views.",
        CrossProjectQuery, CrossProjectPage, LaneReader(engine).search_cross_project,
        profile="projects", queryable_in_delta=True, workflow='universe'))
