"""Compile complete bounded source selections into owning Plan task contracts.

Sources owns the immutable preparation and child byte identities. Plan adoption,
job admission, parser execution and materialization coverage remain distinct.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, ValidationError

from .errors import LaneError
from .migrations import apply_migrations
from .plan_runtime import PlanCreate, TaskBudget, TaskDefinition, TaskOperation
from .registry import ActionSpec, Contract
from .source_authority import (
    SOURCES_MIGRATIONS,
    SourceAuthoritySpec,
    SourceCaptureBudget,
    freeze_source_authority,
    register_source_batch,
)
from .source_intake import (
    SourceOperationResult,
    SourceRequest,
    _classify_one,
    _source_action_result,
)
from .source_routing import (
    ROUTE_MIGRATIONS,
    SourceRouteSelection,
    _read,
    digest,
    load_route,
    publish_batch_route,
)
from .source_selection import registered_directory_selection
from .source_selectors import SnapshotSelection
from .storage import json_text, now, reject_links
from .tool_routes import routes_for, supports_arguments


class _SourcePrepareFields(SourceRequest):
    selection: SourceRouteSelection | None
    code_mode: Literal['local_code', 'github_code'] = 'local_code'
    # Exact project-relative member overrides; existing file occurrences retain
    # their registered sector unless this request explicitly overrides them.
    overrides: dict[str, str] = Field(default_factory=dict, max_length=2000)
    lane_options: dict[str, dict[str, JsonValue]] = Field(default_factory=dict, max_length=64)
    budget: TaskBudget = Field(default_factory=TaskBudget)
    max_files: int = Field(default=2000, ge=1, le=25_000)
    max_file_bytes: int = Field(default=8_388_608, ge=1, le=8_388_608)
    max_total_bytes: int = Field(default=67_108_864, ge=1, le=1_073_741_824)
    max_seconds: int = Field(default=120, ge=1, le=300)


class SourcePrepare(_SourcePrepareFields):
    selection: SourceRouteSelection


class PreparationRead(Contract):
    preparation_id: str = Field(pattern=r'^[0-9a-f]{64}$')


class SourceRefreshPrepare(_SourcePrepareFields):
    selection: SourceRouteSelection | None = None
    baseline_snapshots: list[SnapshotSelection] = Field(min_length=1, max_length=128)
    git_snapshot_id: str | None = Field(default=None, min_length=1, max_length=160)


def _capture(store, context, request):
    from .projects import ProjectAccess
    access = ProjectAccess(store)

    def authorize(path):
        if not path.is_absolute() or not path.is_relative_to(store.source_root):
            raise LaneError('SOURCE_PREPARE_LOCAL_SCOPE', 'Preparation requires selected local inputs inside this project source root.')
        access.authorize(context.client_id, 'read', path=path)
        reject_links(path, store.source_root)

    return SourceCaptureBudget(authorize, lambda: context.authorize('write'),
        max_files=request.max_files, max_file_bytes=request.max_file_bytes,
        max_total_bytes=request.max_total_bytes, max_seconds=request.max_seconds,
        max_entries=min(1_000_100, request.max_files * 40 + 100), allow_archives=True)


def _freeze_parents(store, context, request, parent, *, started=None):
    if request.selection is None:
        return [], _capture(store, context, request)
    selected = [row for row in parent['routes'] if row['ordinal'] in request.selection.occurrence_ordinals]
    if len(selected) != len(request.selection.occurrence_ordinals):
        raise LaneError('SOURCE_ROUTE_OCCURRENCE_MISSING', 'Select existing source occurrences from the exact route.')
    capture = _capture(store, context, request)
    if started is not None:
        capture.started = started
    result = []
    for row in selected:
        path = Path(row['resolved_pointer'])
        capture.boundary(path)
        if row['kind'] not in {'file', 'zip', 'directory'}:
            raise LaneError('SOURCE_PREPARE_LOCAL_SCOPE', 'Fetch remote content through its owning workflow before preparing local tasks.')
        source = freeze_source_authority(SourceAuthoritySpec(str(path), row['ordinal'], row['lane_id'],
            directory_selection=registered_directory_selection(store, row['object_id']) if row['kind'] == 'directory' else None),
            capture=capture)
        if source.identity_sha256.upper() != row['identity_sha256'].upper():
            raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'A selected source changed; register its current identity before task preparation.')
        result.append((row, source))
    return result, capture


def _members(store, context, request, parent):
    frozen, capture = _freeze_parents(store, context, request, parent)
    candidates = {}
    for row, source in frozen:
        source_path = Path(source.resolved_pointer)
        members = source.members if source.kind == 'directory' else (
            {'member_path': None, 'sha256': source.byte_sha256, 'size_bytes': source.size_bytes},)
        for member in members:
            path = source_path / member['member_path'] if member['member_path'] is not None else source_path
            capture.boundary(path)
            relative = path.relative_to(store.source_root).as_posix()
            identity = {'sha256': member['sha256'].lower(), 'size_bytes': member['size_bytes']}
            item = candidates.setdefault(relative, dict(identity, parents=[], routes=[]))
            if any(item[key] != value for key, value in identity.items()):
                raise LaneError('SOURCE_ROUTE_AMBIGUOUS', 'Overlapping selections disagree about the same source bytes.')
            item['parents'].append({'ordinal': row['ordinal'], 'object_id': row['object_id'],
                'member_path': member['member_path'], 'identity_sha256': row['identity_sha256']})
            item['routes'].append((len(source_path.parts), row['lane_id'], source.kind))
    if not candidates and not isinstance(request, SourceRefreshPrepare):
        raise LaneError('SOURCE_ROUTE_EMPTY_SELECTION', 'The selected sources contain no admitted input files.')
    if set(request.overrides) - set(candidates):
        raise LaneError('SOURCE_PREPARE_OVERRIDE_SCOPE', 'Every override must name an exact selected project-relative file.')
    if len(candidates) > request.max_files:
        raise LaneError('SOURCE_PREPARE_FILE_BUDGET', 'The complete selection exceeds the requested preparation file budget.')
    result = []
    for relative, item in sorted(candidates.items()):
        path = store.source_root / relative
        capture.boundary(path)
        depth = max(route[0] for route in item['routes'])
        routes = [route for route in item['routes'] if route[0] == depth]
        if len({(lane, kind) for _, lane, kind in routes}) != 1:
            raise LaneError('SOURCE_ROUTE_AMBIGUOUS', 'Equally specific selected sources disagree about the target sector.')
        _, parent_lane, kind = routes[0]
        override = request.overrides.get(relative, parent_lane if kind != 'directory' else None)
        code_mode = parent_lane if parent_lane in {'local_code', 'github_code'} else request.code_mode
        classified = _classify_one(str(path), code_mode=code_mode, override=override, git_mode='DISABLED')
        measured = classified['source_identity']
        if measured.get('sha256', '').lower() != item['sha256'] or measured.get('bytes') != item['size_bytes']:
            raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'A selected member changed during classification.')
        result.append({'path': relative, 'lane_id': classified['canonical_lane_id'],
            'sha256': item['sha256'], 'size_bytes': item['size_bytes'], 'parents': item['parents'],
            'routing_basis': 'request_exact_override' if relative in request.overrides else
                'registered_file_occurrence' if kind != 'directory' else classified['classification_reason']})
    if set(request.lane_options) - {row['lane_id'] for row in result}:
        raise LaneError('SOURCE_PREPARE_OPTIONS_SCOPE', 'Parser options must name a sector selected by this preparation.')
    return result, capture


def _owner(engine, store, context, request, row):
    matches = []
    for spec in engine.registry.materialization_actions():
        from .lanes import lane_family
        if lane_family(row['lane_id']) not in spec.source_lanes:
            continue
        route = spec.materialization
        options = dict(request.lane_options.get(row['lane_id'], {}))
        if isinstance(request, SourceRefreshPrepare) and row['lane_id'] == 'github_code' and request.git_snapshot_id is not None:
            if options.get('git_snapshot_id', request.git_snapshot_id) != request.git_snapshot_id:
                raise LaneError('SOURCE_REFRESH_GIT_SELECTION', 'Use one exact Git checkpoint for this refresh group.')
            options['git_snapshot_id'] = request.git_snapshot_id
        if {'lane_id', *spec.path_fields, 'expected_snapshot'} & set(options):
            raise LaneError('SOURCE_PREPARE_OPTIONS_SCOPE', 'Preparation owns the exact input paths, sector and initial snapshot selection.')
        values = {**options, 'lane_id': row['lane_id'],
            route.path_argument: [row['path']] if route.group_limit > 1 else row['path']}
        try:
            arguments = spec.input_model.model_validate(values)
        except ValidationError:
            continue
        if any(supports_arguments(tool, context, arguments) for tool in routes_for(spec)):
            matches.append((spec, arguments))
    if len(matches) != 1:
        raise LaneError('SOURCE_PREPARE_OPERATION_REQUIRED', 'Select valid parser options for exactly one registered initial index owner.',
            details={'path': row['path'], 'lane_id': row['lane_id'], 'matching_operations': [spec.name for spec, _ in matches]})
    spec, arguments = matches[0]
    from .lanes import is_named_custom_lane
    if is_named_custom_lane(arguments.lane_id):
        from .custom_lanes import selected_adapter
        selected_adapter(store, arguments)
    if row['size_bytes'] > getattr(arguments, 'max_file_bytes', request.max_file_bytes):
        raise LaneError('SOURCE_PREPARE_PARSER_BUDGET', 'A selected file exceeds its owning parser input budget.',
            details={'path': row['path'], 'action': spec.name})
    return spec, arguments


def _compile(engine, store, context, request, parent, rows, capture, lease, *, baselines=()):
    tasks, bindings = [], []
    rebound = {}
    counts = Counter()
    # One bounded Sources batch per 256 files; Code groups remain at most 32
    # exact file paths. No repeated whole-directory capture for child batches.
    for start in range(0, len(rows), 256):
        batch_rows = rows[start:start + 256]
        specs = [SourceAuthoritySpec(str(store.source_root / row['path']), ordinal, row['lane_id'])
            for ordinal, row in enumerate(batch_rows, 1)]
        batch = register_source_batch(store, specs, capture=capture, writer=lease)
        inheritance = {'parent_route_id': request.selection.route_id, 'parent_batch_id': parent['batch_id'],
            'inherited_sources': [], 'explicit_sources': [str(store.source_root / row['path']) for row in batch_rows],
            'omitted_parent_sources': [], 'inheritance_key': 'verified_selected_parent_member_bytes'}
        route_id = publish_batch_route(store, batch['batch_id'], inheritance, lease)
        route = load_route(store, route_id)
        groups = []
        for ordinal, (row, bound) in enumerate(zip(batch_rows, route['routes'], strict=True), 1):
            if bound['byte_sha256'].lower() != row['sha256'] or bound['size_bytes'] != row['size_bytes']:
                raise LaneError('SOURCE_ROUTE_SOURCE_CHANGED', 'A child source differs from its captured parent member bytes.')
            spec, arguments = _owner(engine, store, context, request, row)
            values = arguments.model_dump(mode='json')
            field = spec.materialization.path_argument
            key = (spec.name, digest({name: value for name, value in values.items() if name != field}))
            group = next((group for group in groups if group['key'] == key
                and len(group['rows']) < min(spec.materialization.group_limit, getattr(arguments, 'max_files', 32))
                and group['bytes'] + row['size_bytes'] <= getattr(arguments, 'max_total_bytes', 33_554_432)), None)
            if group is None:
                group = {'key': key, 'spec': spec, 'values': values, 'rows': [], 'ordinals': [], 'bytes': 0}
                groups.append(group)
            group['rows'].append(row)
            group['ordinals'].append(ordinal)
            group['bytes'] += row['size_bytes']
        for group in groups:
            spec, values = group['spec'], group['values']
            paths = [row['path'] for row in group['rows']]
            if spec.materialization.group_limit > 1:
                values[spec.materialization.path_argument] = paths
            arguments = spec.input_model.model_validate(values)
            if baselines:
                from .source_refresh_preparation import bind_refresh
                arguments, baseline = bind_refresh(arguments, spec, paths, baselines)
            else:
                baseline = None
            counts[arguments.lane_id] += 1
            if spec.materialization.group_limit > 1 and counts[arguments.lane_id] > 128:
                raise LaneError('SOURCE_PREPARE_SCOPE_BUDGET', 'This Code selection exceeds the current 128-scope reader and refresh contract.')
            tools = sorted(set(spec.required_tools) | {tool for route in routes_for(spec)
                if supports_arguments(route, context, arguments) for tool in route.tool_ids})
            for path in spec.materialization.auxiliary_paths:
                capture.boundary((store.source_root / path).resolve())
            permitted_paths = list(dict.fromkeys([*paths, *spec.materialization.auxiliary_paths]))
            task_id = 'source-' + context.request_id + '-' + str(len(tasks) + 1)
            if baseline:
                rebound[(baseline['lane_id'], baseline['snapshot_id'])] = task_id
            tasks.append(TaskDefinition(task_id=task_id, title='Index ' + arguments.lane_id + ': ' + paths[0][:350],
                requested_outcome='Index the exact selected source bytes and verify the owning sector result.',
                profile=spec.profile, permitted_paths=permitted_paths, permitted_tools=tools, allowed_actions=[spec.name],
                acceptance_checks=list(spec.verification_checks), budget=request.budget,
                plan_group='sources-' + context.request_id,
                operation=TaskOperation(action=spec.name, arguments=arguments.model_dump(mode='json'),
                    source_route=SourceRouteSelection(route_id=route_id, occurrence_ordinals=group['ordinals']))))
            for row in group['rows']:
                bindings.append({**row, 'task_id': task_id, 'route_id': route_id, 'batch_id': batch['batch_id']})
    refresh = []
    if baselines:
        from .source_refresh_preparation import retirement_tasks
        retirements, refresh = retirement_tasks(engine, store, context, request,
            tasks, bindings, baselines, rebound, capture)
        tasks.extend(retirements)
    # Enforce the actual Plan size and dependency contracts before publishing a
    # preparation, without creating a Plan or modifying an existing revision.
    try:
        proposal = PlanCreate(title='Materialize selected sources', tasks=tasks)
    except ValidationError:
        raise LaneError('SOURCE_PREPARE_PLAN_BUDGET', 'The complete prepared selection exceeds the current Plan contract; select a smaller explicit source group.') from None
    return proposal.tasks, bindings, refresh


def read_preparation(store, preparation_id):
    value = _read(store.lane('sources'), preparation_id)
    if (value.get('schema') != 'evidence-lane.source-preparation.v4'
            or value.get('project_id') != store.project_id):
        raise LaneError('SOURCE_PREPARATION_INTEGRITY', 'Select an immutable source preparation belonging to this project.')
    with store.lane('sources').connection(read_only=True) as connection:
        row = connection.execute('SELECT client_id,request_digest FROM sourceroutes_preparations '
            'WHERE preparation_id=? LIMIT 1', (preparation_id,)).fetchone()
    if row is None or row['client_id'] != value['client_id'] or row['request_digest'] != value['request_digest']:
        raise LaneError('SOURCE_PREPARATION_INTEGRITY', 'The preparation is not bound to its attributed Sources publication.')
    if value['selection'] is not None:
        load_route(store, value['selection']['route_id'])
    for route_id in {row['route_id'] for row in value['files']}:
        load_route(store, route_id)
    return {**value, 'preparation_id': preparation_id}


def register_preparation_actions(engine):
    def prepare(context, request):
        store = engine.directory.open(context.project_id, write=True)
        values = request.model_dump(mode='json')
        action = 'source_prepare_refresh' if isinstance(request, SourceRefreshPrepare) else 'source_prepare_tasks'
        with engine.project_work.mutation(store) as lease, lease.coordinated_transaction(['sources']):
            context.authorize('write')
            apply_migrations(store, ROUTE_MIGRATIONS, writer=lease)
            with store.lane('sources').connection(read_only=True) as connection:
                previous = connection.execute('SELECT * FROM sourceroutes_preparations WHERE request_id=?',
                    (context.request_id,)).fetchone()
            if previous:
                if previous['client_id'] != context.client_id or previous['request_digest'] != digest(values):
                    raise LaneError('SOURCE_PREPARATION_REQUEST_CONFLICT', 'This preparation request already binds another selection or client.')
                return _source_action_result(store, action, read_preparation(store, previous['preparation_id']))
            parent = load_route(store, request.selection.route_id) if request.selection is not None else None
            rows, capture = _members(store, context, request, parent)
            baselines = []
            if isinstance(request, SourceRefreshPrepare):
                from .source_refresh_preparation import read_baselines
                baselines = read_baselines(engine, store, request, capture)
            tasks, files, refresh = _compile(engine, store, context, request, parent, rows, capture, lease, baselines=baselines)
            # Reconcile the complete selection again, including membership,
            # before publication. A preview never certifies later execution.
            _freeze_parents(store, context, request, parent, started=capture.started)
            body = {'schema': 'evidence-lane.source-preparation.v4', 'project_id': store.project_id,
                'client_id': context.client_id, 'request_id': context.request_id, 'request_digest': digest(values),
                'selection': request.selection.model_dump(mode='json') if request.selection is not None else None, 'options': values,
                'tasks': [task.model_dump(mode='json') for task in tasks], 'files': files,
                'file_count': len(files), 'input_bytes': sum(row['size_bytes'] for row in files),
                'task_count': len(tasks), 'sector_counts': dict(Counter(row['lane_id'] for row in files)),
                'selection_coverage': 'all_included_files_in_selected_occurrences', 'files_omitted': 0,
                'plan_changed': False, 'jobs_started': 0, 'materialized': False,
                'tool_readiness_verified': False, 'native_execution_attested': False,
                'source_bytes_mutated': False, 'created_at': now()}
            if isinstance(request, SourceRefreshPrepare):
                body.update(preparation_kind='refresh', refresh_baselines=refresh)
            lane = store.lane('sources')
            preparation_id = lane.put_object(json_text(body).encode(), limit=2_097_152)
            with lane.transaction() as connection:
                connection.execute('INSERT INTO sourceroutes_preparations VALUES(?,?,?,?,?)',
                    (context.request_id, context.client_id, digest(values), preparation_id, now()))
            store.append_receipt('source_task_preparation', {'preparation_id': preparation_id,
                'client_id': context.client_id, 'request_id': context.request_id, 'request_digest': digest(values),
                'route_id': request.selection.route_id if request.selection is not None else None, 'files': len(files), 'tasks': len(tasks),
                'plan_changed': False, 'materialized': False})
            return _source_action_result(store, action, {**body, 'preparation_id': preparation_id})

    engine.registry.register(ActionSpec('source_prepare_tasks',
        'Prepare complete bounded selected-source file routes and owning Plan tasks; does not adopt a Plan or run parsers.',
        SourcePrepare, SourceOperationResult, prepare, permission='write', mutates=True,
        profile='sources', workflow='manage-project-sources'))
    engine.registry.register(ActionSpec('source_prepare_refresh',
        'Prepare changed, new and deleted selected sources as exact owning parser and retirement Plan tasks; preserve unselected scopes and history.',
        SourceRefreshPrepare, SourceOperationResult, prepare, permission='write', mutates=True,
        profile='sources', workflow='refresh-project-evidence'))
    engine.registry.register(ActionSpec('source_preparation_read',
        'Read an exact historical source task preparation with its immutable parent and child route bindings.',
        PreparationRead, SourceOperationResult,
        lambda c, r: _source_action_result(engine.directory.open(c.project_id), 'source_preparation_read',
            read_preparation(engine.directory.open(c.project_id), r.preparation_id)),
        profile='sources', workflow='manage-project-sources', queryable_in_delta=True, cross_project_read=True,
        studio_read=True, read_migrations=(*SOURCES_MIGRATIONS, *ROUTE_MIGRATIONS)))
