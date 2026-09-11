"""An attributed source group over one existing Plan and its normal Delta jobs.

Sources records an intent and a terminal coverage observation. It never owns a
second task queue, runs a parser, recovers a child job, or silently resumes work.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from uuid import UUID, uuid5

from pydantic import Field

from .adaptive_delta_entry import PlannedDeltaEnter
from .adaptive_delta_exit import read_recorded_exit
from .errors import LaneError
from .jobs import JobQueue
from .migrations import Migration, apply_migrations
from .plan_runtime import PlanStore, TaskDefinition
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .source_authority import SOURCES_MIGRATIONS
from .source_intake import SourceOperationResult, _source_action_result
from .source_preparation import (
    SourcePrepare,
    SourceRefreshPrepare,
    _freeze_parents,
    read_preparation,
)
from .source_routing import ROUTE_MIGRATIONS, _read, digest, load_route
from .storage import bounded_project_read, json_text, now

MATERIALIZATION_MIGRATIONS = (Migration('sourcematerialization', 1, 'Source group intents and terminal coverage', (
    """CREATE TABLE sourcematerialization_runs (
       run_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, request_digest TEXT NOT NULL,
       preparation_id TEXT NOT NULL REFERENCES objects(digest), plan_revision INTEGER NOT NULL,
       engine_instance TEXT NOT NULL, intent_object TEXT NOT NULL REFERENCES objects(digest),
       state TEXT NOT NULL CHECK(state IN ('active','completed','blocked')),
       outcome_object TEXT REFERENCES objects(digest), created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE UNIQUE INDEX sourcematerialization_one_active ON sourcematerialization_runs(state) WHERE state='active'",
)),)


class SourceMaterialize(Contract):
    preparation_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    plan_revision: int = Field(ge=1)
    plan_document_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    max_seconds: int = Field(default=3600, ge=1, le=86400)
    max_coverage_calls: int = Field(default=4096, ge=1, le=50_000)


class MaterializationRead(Contract):
    run_id: str = Field(pattern=UUID_PATTERN)


def _exists(connection, table):
    return connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (table,)).fetchone() is not None


def _quiescent(connection):
    return not (_exists(connection, 'jobs_jobs') and connection.execute(
        "SELECT 1 FROM jobs_jobs WHERE state IN ('queued','running','uncertain','checkpointed') LIMIT 1").fetchone())


class SourceMaterialization:
    def __init__(self, engine):
        self.engine = engine
        self._lock = threading.RLock()
        self._driver_lock = threading.Lock()
        self._drivers = {}
        for name, model, handler, description, write in (
            ('source_materialize', SourceMaterialize, self.start,
             'Run one exact prepared source group through its current Plan tasks and owning Delta verifiers, then verify coverage.', True),
            ('source_materialization_read', MaterializationRead, self.read,
             'Read attributed source group status and its terminal historical coverage without restarting work.', False),
            ('source_materialization_reconcile', MaterializationRead, self.reconcile,
             'Close an unowned source group as blocked after its normal Plan jobs have been reconciled; never replay it.', True),
        ):
            engine.registry.register(ActionSpec(name, description, model, SourceOperationResult, handler,
                permission='write' if write else 'read', mutates=write, profile='sources', workflow='manage-project-sources',
                studio_read=not write, queryable_in_delta=not write, cross_project_read=not write,
                read_migrations=(*SOURCES_MIGRATIONS, *ROUTE_MIGRATIONS, *MATERIALIZATION_MIGRATIONS) if not write else ()))

    def _row(self, store, run_id):
        with store.lane('sources').connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM sourcematerialization_runs WHERE run_id=?', (run_id,)).fetchone() if _exists(connection, 'sourcematerialization_runs') else None
        if row is None:
            raise LaneError('SOURCE_GROUP_NOT_FOUND', 'Select a recorded source group in this project.')
        return dict(row)

    def _intent(self, store, row):
        body = _read(store.lane('sources'), row['intent_object'])
        if any(body.get(key) != row[key] for key in ('run_id', 'client_id', 'request_digest', 'preparation_id', 'plan_revision', 'engine_instance')) or body.get('project_id') != store.project_id:
            raise LaneError('SOURCE_GROUP_INTEGRITY', 'The source group intent differs from its attributed publication.')
        return body

    def _owned(self, row):
        with self._driver_lock:
            future = self._drivers.get(row['run_id'])
        return row['engine_instance'] == self.engine.instance_id and future is not None and not future.done()

    def _status(self, store, row):
        intent = self._intent(store, row)
        outcome = _read(store.lane('sources'), row['outcome_object']) if row['outcome_object'] else None
        if (row['state'] == 'active') != (outcome is None) or (outcome is not None and
                (outcome.get('intent_object') != row['intent_object'] or outcome.get('state') != row['state'])):
            raise LaneError('SOURCE_GROUP_INTEGRITY', 'The source group outcome differs from its durable status.')
        progress = []
        with store.lane('plan').connection(read_only=True) as connection:
            if _exists(connection, 'delta_runs'):
                for task in intent['tasks']:
                    job = connection.execute('SELECT job_id,state,error_code,request_id FROM delta_runs WHERE plan_revision=? AND task_id=?',
                        (row['plan_revision'], task['task_id'])).fetchone()
                    if job:
                        progress.append({'task_id': task['task_id'], **dict(job),
                            'owned_request': job['request_id'] == task['child_request_id']})
        return {'run_id': row['run_id'], 'preparation_id': row['preparation_id'], 'plan_revision': row['plan_revision'],
            'intent_object': row['intent_object'], 'outcome_object': row['outcome_object'], 'state': row['state'],
            'driver_status': 'owned' if row['state'] == 'active' and self._owned(row) else
                'owner_unavailable' if row['state'] == 'active' else 'finished',
            'task_count': len(intent['tasks']), 'jobs': progress, 'outcome': outcome,
            'automatic_replay': False, 'native_execution_attested': False,
            'source_currentness': 'historical_observation_only' if outcome else 'not_certified'}

    def read(self, context, request):
        store = self.engine.directory.open(context.project_id)
        with bounded_project_read(store.root, time.monotonic() + 10):
            return _source_action_result(store, 'source_materialization_read', self._status(store, self._row(store, request.run_id)))

    def _boundary(self, store, context, request):
        context.authorize('write')
        if self.engine.phase != 'running':
            raise LaneError('ENGINE_DRAINING', 'Finish the current child and stop the source group during engine drain.')
        with store.lane('plan').connection(read_only=True) as connection:
            PlanStore._head(connection, request.plan_revision)
            document = connection.execute('SELECT document_digest FROM plan_revisions WHERE revision=?', (request.plan_revision,)).fetchone()
            if document[0] != request.plan_document_digest:
                raise LaneError('SOURCE_GROUP_PLAN_CHANGED', 'The group must retain its exact adopted Plan document.')
            if self.engine.project_work.paused(connection):
                raise LaneError('PROJECT_PAUSED', 'The source group stops at the project pause boundary.')
            if _exists(connection, 'steer_requests') and connection.execute(
                    "SELECT 1 FROM steer_requests WHERE expected_revision=? AND state IN ('pending','checkpointing','ready') LIMIT 1", (request.plan_revision,)).fetchone():
                raise LaneError('PLAN_STEER_PENDING', 'Reconcile the semantic steer before any further source group work.')

    def _tasks(self, store, preparation, request, context):
        self._boundary(store, context, request)
        tasks = []
        with store.lane('plan').connection(read_only=True) as connection:
            if not _quiescent(connection):
                raise LaneError('JOBS_NOT_QUIESCENT', 'Reconcile existing Plan jobs before admitting a source group.')
            first = connection.execute("SELECT position FROM plan_tasks WHERE revision=? AND state<>'completed' ORDER BY position LIMIT 1",
                (request.plan_revision,)).fetchone()
            for prepared in preparation['tasks']:
                definition = TaskDefinition.model_validate(prepared)
                view = PlanStore._view(PlanStore._task(connection, request.plan_revision, definition.task_id))
                adopted = view.definition.model_dump(mode='json')
                # Plan adoption supplies sequential dependencies and may have an
                # earlier completed prefix. Every other task byte must match.
                expected = definition.model_dump(mode='json')
                expected['dependencies'] = adopted['dependencies']
                if adopted != expected or view.state not in {'queued', 'active'}:
                    raise LaneError('SOURCE_GROUP_TASK_CHANGED', 'Adopt the prepared task contracts unchanged before source materialization.')
                if first is None or view.position != first[0] + len(tasks):
                    raise LaneError('SOURCE_GROUP_ORDER', 'The complete prepared group must be the next contiguous Plan rows.')
                if tasks and tasks[-1]['task_id'] not in adopted['dependencies']:
                    raise LaneError('SOURCE_GROUP_ORDER', 'Each source task must depend on its preceding group task.')
                if _exists(connection, 'delta_runs') and connection.execute('SELECT 1 FROM delta_runs WHERE plan_revision=? AND task_id=?',
                        (request.plan_revision, definition.task_id)).fetchone():
                    raise LaneError('DELTA_ATTEMPT_EXISTS', 'A prepared task already has an attempt; inspect and refresh instead of replaying it.')
                self.engine.registry.validate('delta_enter_planned', PlannedDeltaEnter(task_id=definition.task_id,
                    plan_revision=request.plan_revision, contract_digest=view.contract_digest).model_dump(), context)
                self.engine.registry.validate(definition.operation.action, definition.operation.arguments, context)
                tasks.append({'task_id': definition.task_id, 'contract_digest': view.contract_digest,
                    'child_request_id': str(uuid5(UUID(context.request_id), definition.task_id))})
        return tasks

    def start(self, context, request):
        if context.request_id is None or context.expected_revision != request.plan_revision:
            raise LaneError('SOURCE_GROUP_REVISION_REQUIRED', 'Bind this request envelope to the exact adopted Plan revision.')
        store = self.engine.directory.open(context.project_id, write=True)
        values = request.model_dump(mode='json')
        # This lock only covers admission/publication of driver ownership; it
        # is not held while a child runs or while awaiting its completion.
        with self._lock:
            with self.engine.project_work.mutation(store) as lease:
                apply_migrations(store, MATERIALIZATION_MIGRATIONS, writer=lease)
                with store.lane('sources').connection(read_only=True) as connection:
                    previous = connection.execute('SELECT * FROM sourcematerialization_runs WHERE run_id=?', (context.request_id,)).fetchone()
                    active = connection.execute("SELECT 1 FROM sourcematerialization_runs WHERE state='active'").fetchone()
                if previous:
                    if previous['client_id'] != context.client_id or previous['request_digest'] != digest(values):
                        raise LaneError('SOURCE_GROUP_REQUEST_CONFLICT', 'This group request already belongs to another client or contract.')
                    return _source_action_result(store, 'source_materialize', self._status(store, dict(previous)))
                if active:
                    raise LaneError('SOURCE_GROUP_ACTIVE', 'Finish or explicitly reconcile the existing source group before starting another.')
                preparation = read_preparation(store, request.preparation_id)
                tasks = self._tasks(store, preparation, request, context)
                options = (SourceRefreshPrepare if preparation.get('preparation_kind') == 'refresh' else SourcePrepare).model_validate(preparation['options'])
                _freeze_parents(store, context, options,
                    load_route(store, preparation['selection']['route_id']) if preparation['selection'] is not None else None)
                intent = {'schema': 'evidence-lane.source-materialization.v4', 'project_id': store.project_id,
                    'run_id': context.request_id, 'client_id': context.client_id, 'request_digest': digest(values),
                    'preparation_id': request.preparation_id, 'plan_revision': request.plan_revision,
                    'engine_instance': self.engine.instance_id, 'request': values, 'tasks': tasks,
                    'child_request_identity': 'uuid5_from_group_request_and_existing_plan_task',
                    'native_task_id': context.native_task_id, 'native_execution_attested': False, 'created_at': now()}
                with lease.coordinated_transaction(['sources']):
                    lane = store.lane('sources')
                    intent_object = lane.put_object(json_text(intent).encode(), limit=2_097_152)
                    with lane.transaction() as connection:
                        connection.execute('INSERT INTO sourcematerialization_runs VALUES(?,?,?,?,?,?,?,\'active\',NULL,?,?)',
                            (context.request_id, context.client_id, digest(values), request.preparation_id,
                             request.plan_revision, self.engine.instance_id, intent_object, now(), now()))
                    store.append_receipt('source_group_admitted', {'run_id': context.request_id, 'intent_object': intent_object,
                        'preparation_id': request.preparation_id, 'plan_revision': request.plan_revision,
                        'actor_id': context.client_id, 'engine_instance': self.engine.instance_id, 'automatic_replay': False})
            try:
                with self._driver_lock:
                    if len(self._drivers) >= 256:
                        self._drivers = {key: value for key, value in self._drivers.items() if not value.done()}
                    self._drivers[context.request_id] = self.engine.start_job(lambda: self._run(store, context, request, intent, preparation))
            except Exception:  # noqa: BLE001 - failed dispatch must leave a durable blocked intent
                self._finish(store, context.request_id, 'blocked', {'error_code': 'SOURCE_GROUP_DISPATCH_FAILED'})
                raise LaneError('SOURCE_GROUP_DISPATCH_FAILED', 'The recorded source group could not start; it was blocked without replay.') from None
            # Return the immutable admission, not a racing multi-lane progress
            # read while the first real Delta may already be publishing.
            return _source_action_result(store, 'source_materialize', {'run_id': context.request_id,
                'preparation_id': request.preparation_id, 'plan_revision': request.plan_revision,
                'state': 'active', 'intent_object': intent_object, 'task_count': len(tasks),
                'materialized': False, 'automatic_replay': False, 'native_execution_attested': False})

    def _run(self, store, context, request, intent, preparation):
        started = time.monotonic()
        try:
            for task in intent['tasks']:
                if time.monotonic() - started >= request.max_seconds:
                    raise LaneError('SOURCE_GROUP_TIME_BUDGET', 'The source group exhausted its boundary-enforced elapsed time.')
                self._boundary(store, context, request)
                child = replace(context, request_id=task['child_request_id'], tool_admission=None)
                arguments = PlannedDeltaEnter(task_id=task['task_id'], plan_revision=request.plan_revision,
                    contract_digest=task['contract_digest']).model_dump(mode='json')
                admission = self.engine.registry.execute('delta_enter_planned', arguments, child)
                self.engine.delta.owned_completion(admission['job_id']).result()
                # Joining this exact driver releases its writer. Durable Plan
                # evidence, rather than the Future result, establishes success.
                with bounded_project_read(store.root, time.monotonic() + 10):
                    self._exit(store, task, request)
            self._coverage(store, context, request, intent, preparation, started)
        except Exception as error:  # noqa: BLE001 - unknown failures remain explicit blocked evidence
            code = error.code if isinstance(error, LaneError) else 'SOURCE_GROUP_DRIVER_FAILED'
            self._finish(store, context.request_id, 'blocked', {'error_code': code,
                'elapsed_seconds': round(time.monotonic() - started, 6)})

    def _exit(self, store, task, request):
        with store.lane('plan').connection(read_only=True) as connection:
            row = connection.execute('SELECT * FROM delta_runs WHERE request_id=?', (task['child_request_id'],)).fetchone()
            if row is None or any(row[key] != value for key, value in {
                    'task_id': task['task_id'], 'plan_revision': request.plan_revision, 'contract_digest': task['contract_digest']}.items()):
                raise LaneError('SOURCE_GROUP_JOB_BINDING', 'A source task lacks its exact attributed child execution.')
            job = JobQueue(store).get(row['job_id'], transaction=connection)
            result = json.loads(store.lane('plan').read_object(row['result_object'])) if row['result_object'] else None
            exit_record = read_recorded_exit(store, connection, row, job, result=result)
            if exit_record is None:
                raise LaneError(row['error_code'] or 'SOURCE_GROUP_CHILD_BLOCKED', 'The source task has no verified owning Delta exit.')
            return {'job_id': row['job_id'], 'result_object': row['result_object'],
                'verification_object': exit_record.verification_object, 'receipt_id': exit_record.receipt_id,
                'snapshot_id': result['result']['result']['snapshot_id']}

    def _coverage(self, store, context, request, intent, preparation, started):
        # The writer excludes Plan, selector and lane mutations throughout the
        # observation and its publication. External source bytes are observed at
        # the final capture; a historical receipt never promises future bytes.
        with self.engine.project_work.mutation(store) as lease:
            calls = 0
            def tick():
                if time.monotonic() - started >= request.max_seconds:
                    raise LaneError('SOURCE_GROUP_TIME_BUDGET', 'The source group exceeded its elapsed-time boundary.')
                self._boundary(store, context, request)
            def invoke(action, arguments):
                nonlocal calls
                tick()
                calls += 1
                if calls > request.max_coverage_calls:
                    raise LaneError('SOURCE_GROUP_COVERAGE_BUDGET', 'The complete coverage check exceeds the admitted owning-read budget.')
                return self.engine.registry.execute(action, arguments, replace(context, tool_admission=None))
            tick()
            exits = {task['task_id']: self._exit(store, task, request) for task in intent['tasks']}
            current = {}
            for lane_id in preparation['sector_counts']:
                search = next(item.search for item in self.engine.registry.search_actions() if lane_id in item.search.lanes)
                owner = self.engine.registry.get(search.current_action)
                value = invoke(owner.name, {'lane_id': lane_id} if 'lane_id' in owner.input_model.model_fields else {})
                payload = value.get('result', value)
                if payload.get('truncated'):
                    raise LaneError('SOURCE_GROUP_CURRENT_BUDGET', 'The owning current-snapshot reader is truncated; full coverage cannot be certified.')
                current[lane_id] = {item['snapshot_id'] for item in payload[search.current_items]}
            files, git_observations, git_manifests = [], {}, {}
            definitions = {task['task_id']: TaskDefinition.model_validate(task) for task in preparation['tasks']}
            for row in preparation['files']:
                tick()
                selected = exits[row['task_id']]
                if selected['snapshot_id'] not in current[row['lane_id']]:
                    raise LaneError('SOURCE_GROUP_SELECTOR_CHANGED', 'An owning current selector no longer includes a verified source result.')
                value = invoke('lane_fetch', {'lane_id': row['lane_id'], 'snapshot_id': selected['snapshot_id'],
                    'path': row['path'], 'representation': 'original_source', 'max_bytes': 1024})
                owner = self.engine.registry.get(value['result']['action'])
                payload = value['result']['read']['result']
                owned_sha, owned_size, authority = row['sha256'], row['size_bytes'], {}
                if row['lane_id'] == 'github_code':
                    from .source_git_selectors import matches_blob, observe_checkpoint
                    operation = definitions[row['task_id']].operation
                    reference = operation.arguments['git_snapshot_id']
                    if reference not in git_observations:
                        calls += 1
                        if calls > request.max_coverage_calls:
                            raise LaneError('SOURCE_GROUP_COVERAGE_BUDGET', 'Git checkpoint coverage exceeds the admitted owning-read budget.')
                        # The selected operation already grants this repository
                        # root. The group retains its authenticated project read.
                        from .projects import ProjectAccess
                        def git_root(value):
                            candidate = store.source_root / value
                            ProjectAccess(store).authorize(context.client_id, 'read', path=candidate)
                            return candidate
                        git_observations[reference] = observe_checkpoint(store, reference, git_root, preparation['options']['max_files'])
                    observed = git_observations[reference]
                    if selected['snapshot_id'] not in git_manifests:
                        manifest = self.engine.registry.selector_owner('github_code').read_manifest(store, selected['snapshot_id'])
                        git_manifests[selected['snapshot_id']] = (manifest, {item['path']: item for item in manifest['files']})
                    manifest, members = git_manifests[selected['snapshot_id']]
                    member = members.get(row['path'])
                    if (manifest['byte_source'] != 'git_commit_blobs' or manifest['git_reference'] != observed['reference']
                            or member is None or member['git_blob_id'] != observed['checkpoint']['blob_ids'].get(row['path'])):
                        raise LaneError('SOURCE_GROUP_GIT_COVERAGE', 'The owning source does not match the selected current Git blob.')
                    owned_sha, owned_size = member['sha256'], member['size_bytes']
                    if not matches_blob(store.lane('github_code').read_object(owned_sha), member['git_blob_id']):
                        raise LaneError('SOURCE_GROUP_GIT_COVERAGE', 'The stored original bytes do not match the selected committed blob.')
                    authority = {'byte_source': 'git_commit_blobs', 'git_snapshot_id': reference,
                        'git_blob_id': member['git_blob_id'], 'worktree_sha256': row['sha256'],
                        'worktree_size_bytes': row['size_bytes'], 'worktree_byte_equality_inferred': False}
                if (str(payload[owner.fetch.digest_result]).lower() != owned_sha
                        or payload[owner.fetch.size_result] != owned_size):
                    raise LaneError('SOURCE_GROUP_FILE_COVERAGE', 'The owning snapshot does not preserve an exact selected source file.')
                # The owning reader resolves snapshot -> original object; this
                # verifies all of that stored object, not just the returned page.
                lane = store.lane(row['lane_id'])
                if lane.object_path(owned_sha).stat().st_size != owned_size or len(lane.read_object(owned_sha)) != owned_size:
                    raise LaneError('SOURCE_GROUP_FILE_COVERAGE', 'The complete stored original bytes do not match the selected file.')
                files.append({'path': row['path'], 'lane_id': row['lane_id'], 'sha256': owned_sha,
                    'size_bytes': owned_size, 'task_id': row['task_id'], **selected, **authority})
            tick()
            refresh = self._refresh_coverage(store, context, request, preparation, exits, tick)
            options = (SourceRefreshPrepare if preparation.get('preparation_kind') == 'refresh' else SourcePrepare).model_validate(preparation['options'])
            _freeze_parents(store, context, options,
                load_route(store, preparation['selection']['route_id']) if preparation['selection'] is not None else None)
            tick()
            body = {'files': files, 'file_count': len(files), 'task_count': len(exits), 'files_omitted': 0,
                'coverage': 'all_included_prepared_files_in_current_owning_snapshots',
                'source_selection_rechecked': True, 'stored_original_bytes_verified': True,
                'coverage_owner_calls': calls, 'elapsed_seconds': round(time.monotonic() - started, 6),
                'parser_fidelity': 'the_exact_prepared_operation_options_and_owning_verified_snapshots',
                'materialized': bool(files), 'observed_at': now()}
            if preparation.get('preparation_kind') == 'refresh':
                body.update(refreshed=True, refresh_baselines=refresh)
            # Publish while this exact source/Plan/selector observation still
            # owns the project writer; never release and reacquire before it.
            self._finish(store, intent['run_id'], 'completed', body, lease=lease)
            return body

    def _refresh_coverage(self, store, context, request, preparation, exits, tick):
        from pathlib import Path

        from .projects import ProjectAccess
        from .source_selectors import SnapshotRetire, _coverage, _replacement_tasks
        from .storage import reject_links
        results = []
        definitions = {item['task_id']: TaskDefinition.model_validate(item) for item in preparation['tasks']}
        def source_path(value):
            candidate = Path(value)
            if '..' in candidate.parts or candidate.drive and not candidate.is_absolute():
                raise LaneError('SOURCE_REFRESH_PATH_SCOPE', 'The owning source path is outside this project.')
            candidate = candidate if candidate.is_absolute() else store.source_root / candidate
            if not candidate.is_relative_to(store.source_root):
                raise LaneError('SOURCE_REFRESH_PATH_SCOPE', 'The owning source path is outside this project.')
            reject_links(candidate, store.source_root)
            ProjectAccess(store).authorize(context.client_id, 'read', path=candidate)
            return candidate
        for baseline in preparation.get('refresh_baselines', []):
            tick()
            owner = self.engine.registry.selector_owner(baseline['lane_id'])
            selected = owner.snapshot(store, baseline['snapshot_id'])
            disposition = baseline['disposition']
            if disposition == 'reindexed':
                snapshot_id = exits[baseline['task_id']]['snapshot_id']
                current = owner.snapshot(store, snapshot_id)
                manifest = owner.read_manifest(store, snapshot_id)
                if (not current['active'] or selected['head_snapshot'] != snapshot_id or
                        snapshot_id != baseline['snapshot_id'] and manifest['previous_snapshot'] != baseline['snapshot_id']):
                    raise LaneError('SOURCE_GROUP_SELECTOR_CHANGED', 'The exact refreshed lineage no longer matches its verified parser task.')
                results.append({**baseline, 'current_snapshot': snapshot_id, 'verified': True})
            elif disposition == 'retired':
                task = definitions[baseline['task_id']]
                if task.operation.action != 'source_snapshot_retire':
                    raise LaneError('SOURCE_GROUP_RETIREMENT_CHANGED', 'The prepared retirement task changed.')
                retirement = SnapshotRetire.model_validate(task.operation.arguments)
                replacements, evidence = _replacement_tasks(self.engine, store, retirement, request.plan_revision, task.task_id, check=tick)
                observed, coverage = _coverage(self.engine, store, retirement, source_path, tick,
                    task_replacements=replacements, task_evidence=evidence)
                row = observed['retirement']
                proof = json.loads(store.lane(baseline['lane_id']).read_object(row['proof_object'])) if row else None
                if (proof is None or observed['head_snapshot'] != baseline['snapshot_id'] or observed['active']
                        or proof['coverage'] != coverage or proof['job_id'] != exits[task.task_id]['job_id']):
                    raise LaneError('SOURCE_GROUP_RETIREMENT_CHANGED', 'The retired source observation changed before group completion.')
                results.append({**baseline, 'proof_object': row['proof_object'], 'verified': True})
            elif disposition == 'already_retired':
                if (not selected['retired'] or selected['active'] or selected['head_snapshot'] != baseline['snapshot_id']
                        or selected['retirement']['proof_object'] != baseline['proof_object']):
                    raise LaneError('SOURCE_GROUP_RETIREMENT_CHANGED', 'The explicitly retained historical retirement changed.')
                results.append({**baseline, 'verified': True})
            else:
                raise LaneError('SOURCE_GROUP_RETIREMENT_CHANGED', 'The source preparation has an unknown refresh disposition.')
        return results

    def _finish(self, store, run_id, state, body, *, lease=None):
        if lease is None:
            with self.engine.project_work.mutation(store) as owned:
                return self._finish(store, run_id, state, body, lease=owned)
        row = self._row(store, run_id)
        if row['state'] != 'active':
            return
        outcome = {**body, 'state': state, 'intent_object': row['intent_object'], 'run_id': run_id,
            'materialized': state == 'completed' and bool(body.get('file_count')), 'automatic_replay': False, 'source_bytes_mutated': False,
            'native_execution_attested': False, 'recorded_at': now()}
        with lease.coordinated_transaction(['sources']):
            lane = store.lane('sources')
            outcome_object = lane.put_object(json_text(outcome).encode(), limit=2_097_152)
            with lane.transaction() as connection:
                connection.execute('UPDATE sourcematerialization_runs SET state=?,outcome_object=?,updated_at=? WHERE run_id=?',
                    (state, outcome_object, now(), run_id))
            store.append_receipt('source_group_' + state, {'run_id': run_id, 'intent_object': row['intent_object'],
                'outcome_object': outcome_object, 'error_code': body.get('error_code'),
                'materialized': outcome['materialized'], 'automatic_replay': False})

    def reconcile(self, context, request):
        store = self.engine.directory.open(context.project_id, write=True)
        with self._lock, self.engine.project_work.mutation(store) as lease:
            row = self._row(store, request.run_id)
            self._intent(store, row)
            if row['state'] == 'active':
                if self._owned(row):
                    raise LaneError('SOURCE_GROUP_OWNED', 'The current source driver still owns this run; use the normal project stop or steer.')
                with store.lane('plan').connection(read_only=True) as connection:
                    if not _quiescent(connection):
                        raise LaneError('JOBS_NOT_QUIESCENT', 'Reconcile the normal Plan jobs before closing their unowned source group.')
                self._finish(store, request.run_id, 'blocked', {'error_code': 'SOURCE_GROUP_OWNER_UNAVAILABLE',
                    'reconciled_by': context.client_id, 'reconciliation_request_id': context.request_id}, lease=lease)
            return _source_action_result(store, 'source_materialization_reconcile', self._status(store, self._row(store, request.run_id)))
