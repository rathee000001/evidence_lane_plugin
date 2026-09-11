"""Bounded read-only authority queries under one current Plan revision.

The prior live-root query's exact task and bounded-arm concepts remain. Its
implicit miss-refresh writes and active-session/PV lookup are removed. Query
eligibility comes from the same registry as every other SDK/MCP action.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace

from pydantic import Field, JsonValue

from .adaptive_delta_exit import RecordedDeltaExit, read_recorded_exit
from .errors import LaneError
from .jobs import JobQueue
from .lane_reader import LaneReader, SearchBounds, SearchPage, lexical_tool_routes
from .plan_runtime import TASK_ID_PATTERN, PlanStore
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import bounded_project_read, json_text


class DeltaQuery(Contract):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    plan_revision: int = Field(ge=1)
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    max_bytes: int = Field(default=65_536, ge=1024, le=262_144)


class ProjectSearchRequest(SearchBounds):
    lane_ids: list[str] = Field(default_factory=list, max_length=32)


def query_live_authorities(engine, context, request):
    from .custom_lanes import registered_lane_ids
    from .lanes import LANE_REGISTRY
    store = engine.directory.open(context.project_id)
    return LaneReader(engine).search_lanes(context, request,
        request.lane_ids or [*LANE_REGISTRY, *registered_lane_ids(store)])


class QueryResult(Contract):
    project_id: str
    task_id: str
    plan_revision: int
    contract_digest: str
    action: str
    result: dict[str, JsonValue]
    mutation_performed: bool = False
    refresh_performed: bool = False
    snapshot_scope: str = 'independent_owner_snapshots_with_unchanged_plan_revision'


class DeltaStatusRequest(Contract):
    job_id: str = Field(pattern=UUID_PATTERN)
    include_result: bool = False
    include_verification: bool = False
    max_bytes: int = Field(default=65_536, ge=1024, le=262_144)


class DeltaStatus(Contract):
    project_id: str
    job_id: str
    task_id: str
    plan_revision: int
    current_revision: int
    state: str
    contract_digest: str
    result_object: str | None
    error_code: str | None
    job: dict[str, JsonValue]
    result: dict[str, JsonValue] | None = None
    exit: RecordedDeltaExit | None = None
    validation: dict[str, JsonValue] | None = None


def bounded(value, limit):
    if len(json_text(value).encode()) > limit:
        raise LaneError('QUERY_OUTPUT_BUDGET', 'Request a smaller page or omit the addressed result.')
    return value


def register_query_actions(engine):
    def search(context, request):
        return query_live_authorities(engine, context, request)
    engine.registry.register(ActionSpec('search', 'Search current registered lane owners with separate authority results, exact snapshots and explicit missing coverage.',
        ProjectSearchRequest, SearchPage, search,
        profile='projects', queryable_in_delta=True, studio_read=True, workflow='evidence-lane',
        tool_routes=lexical_tool_routes('search', search)))
    def status(context, request):
        store = engine.directory.open(context.project_id)
        with bounded_project_read(store.root, time.monotonic() + 10), store.lane('plan').connection(read_only=True) as connection:
            connection.execute('BEGIN')
            head = PlanStore._head(connection)
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_runs'").fetchone():
                raise LaneError('DELTA_NOT_FOUND', 'This project has no recorded Delta run.')
            row = connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (request.job_id,)).fetchone()
            if row is None:
                raise LaneError('DELTA_NOT_FOUND', 'The run does not belong to this selected project.')
            job = JobQueue(store).get(request.job_id, transaction=connection)
            if request.include_result and row['result_object']:
                size = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (row['result_object'],)).fetchone()
                if size is None or size[0] > request.max_bytes:
                    raise LaneError('QUERY_OUTPUT_BUDGET', 'Read the result through its bounded addressed artifact route.')
            result = json.loads(store.lane('plan').read_object(row['result_object'])) if request.include_result and row['result_object'] else None
            recorded_exit = read_recorded_exit(store, connection, row, job,
                include_verification=request.include_verification, result=result)
            from .acceptance import read_validation_summary
            validation = read_validation_summary(store, connection, request.job_id)
            if validation is not None and not request.include_verification:
                validation = {**validation, 'summary': {**validation['summary'], 'results': [
                    {key: value for key, value in item.items() if key != 'output_preview'}
                    for item in validation['summary']['results']]}}
        value = DeltaStatus(project_id=store.project_id, job_id=request.job_id, task_id=row['task_id'],
            plan_revision=row['plan_revision'], current_revision=head['revision'], state=row['state'],
            contract_digest=row['contract_digest'], result_object=row['result_object'], error_code=row['error_code'],
            job=job, result=result, exit=recorded_exit, validation=validation)
        bounded(value.model_dump(mode='json'), request.max_bytes)
        return value

    def query(context, request):
        if context.expected_revision != request.plan_revision:
            raise LaneError('DELTA_REVISION_REQUIRED', 'Bind the query envelope to the current Plan revision.')
        spec = engine.registry.get(request.action)
        if not spec.queryable_in_delta:
            raise LaneError('NOT_A_DELTA_QUERY', 'Select an admitted read-only query action.')
        if request.action == 'plan_read' and request.arguments.get('revision') not in {None, request.plan_revision}:
            raise LaneError('QUERY_REVISION_MISMATCH', 'The Plan page must match this query revision.')
        store = engine.directory.open(context.project_id)
        task = PlanStore(store).task(request.task_id, expected_revision=request.plan_revision)
        if context.execution is not None:
            context.execution._before_more_work()
            context.execution.guard.spend_call()
        result = engine.registry.execute(spec.name, request.arguments, replace(context, expected_revision=request.plan_revision))
        if spec.name == 'delta_status' and (result['task_id'] != request.task_id or result['plan_revision'] != request.plan_revision):
            raise LaneError('QUERY_JOB_BINDING_MISMATCH', 'Select the run belonging to this exact task and revision.')
        # Revisions only advance; checking again rejects a refresh crossing the
        # independent read transactions. No query takes the project writer.
        PlanStore(store).task(request.task_id, expected_revision=request.plan_revision)
        value = QueryResult(project_id=store.project_id, task_id=request.task_id, plan_revision=request.plan_revision,
            contract_digest=task.contract_digest, action=spec.name, result=result)
        bounded(value.model_dump(mode='json'), request.max_bytes)
        return value

    engine.registry.register(ActionSpec('delta_status', 'Read a recorded run and reconcile its completion evidence; optionally return result and verification bytes.',
        DeltaStatusRequest, DeltaStatus, status, profile='delta', queryable_in_delta=True, studio_read=True, workflow='execute-project-plan'))
    engine.registry.register(ActionSpec('delta_query', 'Query an admitted authority during work without refreshing or changing the Plan.',
        DeltaQuery, QueryResult, query, profile='delta', workflow='evidence-lane'))
