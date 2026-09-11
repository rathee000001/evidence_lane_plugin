"""Revision-pinned Delta entry, replacing retired backlog/PV/Formula control.

OS workers execute registered operations. The engine owns the writer, live
permission checks, bounded task contracts, control checkpoints and receipts.
"""
from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import replace
from pathlib import Path

from pydantic import Field, JsonValue

from .agent_learning import LearningRead, LearningStore
from .errors import LaneError
from .jobs import JobQueue
from .migrations import Migration, apply_migrations
from .plan_runtime import TASK_ID_PATTERN, PlanStore, content_digest
from .registry import ActionContext, ActionSpec, Contract
from .sdk import UUID_PATTERN, ActionRequest
from .source_routing import SourceRouteGuard, SourceRouteSelection
from .storage import json_text, now, reject_links


class PlannedDeltaEnter(Contract):
    task_id: str = Field(pattern=TASK_ID_PATTERN)
    plan_revision: int = Field(ge=1)
    contract_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeltaEnter(PlannedDeltaEnter):
    action: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    source_route: SourceRouteSelection | None = None


class DeltaAdmission(Contract):
    job_id: str = Field(pattern=UUID_PATTERN)
    task_id: str
    plan_revision: int
    contract_digest: str
    state: str
    automatic_replay: bool = False


DELTA_MIGRATIONS = (Migration("delta", 1, "Revision-pinned registered operation runs", (
    """CREATE TABLE delta_runs (
       job_id TEXT PRIMARY KEY REFERENCES jobs_jobs(job_id), request_id TEXT NOT NULL UNIQUE,
       actor_id TEXT NOT NULL, task_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
       contract_digest TEXT NOT NULL, binding_digest TEXT NOT NULL, entry_object TEXT NOT NULL REFERENCES objects(digest),
       state TEXT NOT NULL CHECK(state IN ('queued','running','awaiting_verification','blocked','verified')),
       result_object TEXT REFERENCES objects(digest), error_code TEXT,
       created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(plan_revision,task_id),
       FOREIGN KEY(plan_revision,task_id) REFERENCES plan_tasks(revision,task_id))""",
)), Migration("delta", 2, "Verified Delta exit and exact selected successor", (
    """CREATE TABLE delta_exits (job_id TEXT PRIMARY KEY REFERENCES delta_runs(job_id),
       verification_object TEXT NOT NULL REFERENCES objects(digest),
       receipt_id TEXT NOT NULL UNIQUE, next_task_id TEXT, created_at TEXT NOT NULL)""",
)))


class ExecutionGuard:
    """Adapter-boundary policy for trusted code, not an arbitrary-code OS sandbox.

    Wall time is checked at boundaries. Owned processes must join before a
    checkpoint; exceeding a budget never automatically kills or replays them.
    """
    def __init__(self, engine, store, task, spec, context: ActionContext):
        self.engine, self.store, self.task, self.spec, self.context = engine, store, task, spec, context
        self.started = time.monotonic()
        self.calls = self.input_bytes = self.output_bytes = 0
        self.worker_evidence: list[dict] = []
        self._observed: set[Future] = set()
        self.failure_code: str | None = None
        self.extension_checks: list[Callable] = []
        self.compute_checks: list[Callable] = []
        self.mode_validation = None
        self.source_route = None
        self.validation = None
        self.worker_operations = spec.worker_operations
        if context.tool_admission is not None:
            from .tool_routes import routes_for
            selected = next((route for route in routes_for(spec)
                if route.route_id == context.tool_admission['route_id']), None)
            if selected is None:
                raise LaneError('TOOL_ADMISSION_CHANGED', 'The admitted worker route is no longer registered.')
            if selected.worker_operations is not None:
                self.worker_operations = selected.worker_operations
        if task.validation_policy is not None:
            self.worker_operations = (*self.worker_operations, 'validation_command')

    def check(self):
        if self.failure_code:
            raise LaneError(self.failure_code, "A prior execution boundary failed; reconcile this task.")
        if self.context.authorize is None:
            raise LaneError("LIVE_AUTHORIZATION_REQUIRED", "Delta execution needs a live connection grant.")
        self.context.authorize("write")
        self.context.authorize(self.spec.permission)
        from .mode_governance import validate_task_mode_binding
        validated = validate_task_mode_binding(self.store, self.task, action=self.spec.name, registry=self.engine.registry)
        if self.mode_validation is not None and validated != self.mode_validation:
            raise LaneError('TASK_MODE_BINDING_CHANGED', 'The execution must retain its admitted mode binding.')
        self.mode_validation = validated
        if time.monotonic() - self.started > self.task.budget.max_seconds:
            raise LaneError("DELTA_TIME_BUDGET", "The task exceeded its elapsed-time budget.")
        for check in self.extension_checks:
            check()
        for check in self.compute_checks:
            check()
        if self.source_route is not None:
            self.source_route.check()

    def spend_call(self):
        self.check()
        if self.calls >= self.task.budget.max_tool_calls:
            raise LaneError("DELTA_TOOL_BUDGET", "The task exhausted its operation-call budget.")
        self.calls += 1

    def input(self, value):
        size = len(json_text(value).encode())
        if self.input_bytes + size > self.task.budget.max_input_bytes:
            raise LaneError("DELTA_INPUT_BUDGET", "The task exceeded its cumulative input byte budget.")
        self.input_bytes += size

    def output(self, value):
        size = len(json_text(value).encode())
        if self.output_bytes + size > self.task.budget.max_output_bytes:
            raise LaneError("DELTA_OUTPUT_BUDGET", "The task exceeded its cumulative output byte budget.")
        self.output_bytes += size

    def path(self, value: str) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise LaneError("DELTA_PATH_SCOPE", "Provide a nonempty declared filesystem path.")
        source = self.store.source_root

        def scoped(value):
            path = Path(value)
            if ".." in path.parts or (path.drive and not path.is_absolute()) or any(":" in part for part in path.parts if part != path.anchor):
                raise LaneError("DELTA_PATH_SCOPE", "Traversal and drive-relative paths are outside the contract.")
            candidate = Path(os.path.abspath(path if path.is_absolute() else source / path))
            if not candidate.is_relative_to(source):
                raise LaneError("DELTA_PATH_SCOPE", "The path is outside the selected source root.")
            reject_links(candidate, source)
            return candidate

        candidate = scoped(value)
        if not any(candidate.is_relative_to(scoped(root)) for root in self.task.permitted_paths):
            raise LaneError("DELTA_PATH_SCOPE", "The task does not grant this source path.")
        return candidate

    def paths(self, fields, arguments):
        for field in fields:
            values = arguments.get(field)
            if not isinstance(values, list):
                values = [values]
            if not values or len(values) > 128:
                raise LaneError("DELTA_PATH_SCOPE", "Use a bounded declared path list.")
            for value in values:
                self.path(value)

    def worker(self, operation, arguments):
        if operation not in self.worker_operations:
            raise LaneError("DELTA_WORKER_SCOPE", "The action does not grant this worker operation.")
        self.paths(self.engine.workers.operations[operation].path_fields, arguments)
        self.spend_call()
        self.input(arguments)
        normalized = dict(arguments)
        for field in self.engine.workers.operations[operation].path_fields:
            value = arguments[field]
            normalized[field] = [str(self.path(item)) for item in value] if isinstance(value, list) else str(self.path(value))
        return normalized

    def observe(self, execution):
        for future in execution._futures:
            if future in self._observed:
                continue
            if not future.done():
                raise LaneError("JOB_WORK_STILL_RUNNING", "An owned OS worker is still running.")
            self._observed.add(future)
            try:
                result = future.result()
            except Exception:  # noqa: BLE001 - vendor future errors become explicit failed evidence
                result = {"status": "error", "code": "WORKER_OPERATION_FAILED"}
            self.output(result)
            self.worker_evidence.append({"digest": content_digest(result), "status": result.get("status"),
                                         "worker_pid": result.get("worker_pid")})
            if result.get("status") != "ok":
                raise LaneError("WORKER_OPERATION_FAILED", "A required OS-worker operation failed.")

    def usage(self):
        return {"tool_calls": self.calls, "input_bytes": self.input_bytes, "output_bytes": self.output_bytes,
                "elapsed_seconds": round(time.monotonic() - self.started, 6),
                "wall_time_enforcement": "cooperative_boundaries_no_automatic_kill"}


class DeltaService:
    def __init__(self, engine):
        self.engine = engine
        self._drivers = {}
        self._drivers_lock = threading.Lock()
        engine.registry.register(ActionSpec("delta_enter", "Run one registered operation under the exact Plan task contract.",
            DeltaEnter, DeltaAdmission, self.enter, permission="write", mutates=True, profile="delta", queued=True, workflow='execute-project-plan'))
        engine.registry.register(ActionSpec('delta_enter_planned',
            'Run the operation already bound to one exact Plan task through its owning Delta executor and verifier.',
            PlannedDeltaEnter, DeltaAdmission, self.enter_planned, permission='write', mutates=True,
            profile='delta', queued=True, workflow='execute-project-plan'))

    def enter_planned(self, context, request):
        store = self.engine.directory.open(context.project_id, write=True)
        view = PlanStore(store).task(request.task_id, expected_revision=request.plan_revision)
        if view.contract_digest != request.contract_digest:
            raise LaneError('DELTA_CONTRACT_CHANGED', 'Read the current exact task contract before executing it.')
        operation = view.definition.operation
        if operation is None:
            raise LaneError('PLAN_OPERATION_REQUIRED', 'This task has no bound operation; supply its permitted Delta arguments or refresh its Plan contract.')
        return self.enter(context, DeltaEnter(**request.model_dump(mode='json'), **operation.model_dump(mode='json')))

    def _preflight(self, context, request, store):
        from .store import require_restoration_execution_ready
        with store.lane('sources').connection(read_only=True) as connection:
            require_restoration_execution_ready(connection)
            from .database_recovery import require_recovery_execution_ready
            with store.lane('receipts').connection(read_only=True) as receipts:
                require_recovery_execution_ready(receipts)
        if context.request_id is None or context.expected_revision != request.plan_revision:
            raise LaneError("DELTA_REVISION_REQUIRED", "Bind the envelope and Delta to the same exact Plan revision.")
        spec = self.engine.registry.get(request.action)
        if not spec.requires_delta:
            raise LaneError("NOT_A_DELTA_OPERATION", "Select a registered Delta operation, not a control or query action.")
        arguments = self.engine.registry.validate(request.action, request.arguments, context)
        view = PlanStore(store).task(request.task_id, expected_revision=request.plan_revision)
        if view.contract_digest != request.contract_digest:
            raise LaneError("DELTA_CONTRACT_CHANGED", "Read the current exact task contract before executing it.")
        task = view.definition
        if task.operation is not None:
            bound = task.operation
            if bound.action != request.action or bound.source_route != request.source_route:
                raise LaneError('PLAN_OPERATION_CHANGED', 'Execution must retain the Plan-bound action and source route.')
            bound_arguments = self.engine.registry.validate(bound.action, bound.arguments, context)
            if content_digest(bound_arguments.model_dump(mode='json')) != content_digest(arguments.model_dump(mode='json')):
                raise LaneError('PLAN_OPERATION_CHANGED', 'Execution arguments differ from the bound Plan operation; refresh the affected contract first.')
        if spec.profile != task.profile or spec.name not in task.allowed_actions:
            raise LaneError("DELTA_ACTION_SCOPE", "The task profile and allowed actions do not cover this operation.")
        if not set(spec.required_tools) <= set(task.permitted_tools):
            raise LaneError("DELTA_TOOL_SCOPE", "The task does not grant the operation's required tools.")
        if spec.verifier is None or not task.acceptance_checks or not set(task.acceptance_checks) <= set(spec.verification_checks):
            raise LaneError("DELTA_VERIFIER_UNAVAILABLE", "Each task acceptance check needs its registered profile verifier.")
        guard = ExecutionGuard(self.engine, store, task, spec, context)
        guard.check()
        if task.validation_policy is not None:
            from .acceptance import validation_preflight
            validation_preflight(guard)
        guard.paths(spec.path_fields, arguments.model_dump(mode="json"))
        if request.source_route is not None:
            guard.source_route = SourceRouteGuard(guard, request.source_route, arguments.model_dump(mode='json'))
        guard.input(arguments.model_dump(mode="json"))
        route = self.engine.registry.tool_router.resolve(spec, context, permitted_tools=task.permitted_tools, arguments=arguments)
        if route['selected_route'] is None:
            raise LaneError('TOOL_ROUTE_UNAVAILABLE', 'The exact task has no ready authorized operation route.', details=route)
        from .tool_routes import routes_for
        selected = next(item for item in routes_for(spec) if item.route_id == route['selected_route'])
        required_workers = spec.worker_operations if selected.worker_operations is None else selected.worker_operations
        if required_workers:
            pool = self.engine.workers
            if pool is None or not pool.status()["accepting"]:
                raise LaneError("WORKERS_UNAVAILABLE", "Start the operation's required OS-worker runtime first.")
            for name in required_workers:
                operation = pool.operations.get(name)
                if operation is None:
                    raise LaneError("WORKER_OPERATION_UNAVAILABLE", "The registered action's worker is unavailable.")
                for module in operation.dependencies:
                    try:
                        available = importlib.util.find_spec(module) is not None
                    except (ModuleNotFoundError, ValueError):
                        available = False
                    if not available:
                        raise LaneError("DEPENDENCY_UNAVAILABLE", "A required operation dependency is unavailable.",
                                        details={"module": module, "operation": name})
        return spec, view, route, guard.source_route.evidence() if guard.source_route is not None else None

    @staticmethod
    def _admission(row):
        return DeltaAdmission(job_id=row["job_id"], task_id=row["task_id"], plan_revision=row["plan_revision"],
                              contract_digest=row["contract_digest"], state=row["state"])

    def enter(self, context, request):
        store = self.engine.directory.open(context.project_id, write=True)
        spec, view, route, source_route = self._preflight(context, request, store)
        binding = {"actor_id": context.client_id, "entry": request.model_dump(mode="json")}
        binding_digest = content_digest(binding)
        with store.lane('plan').connection(read_only=True) as connection:
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_runs'").fetchone():
                existing = connection.execute("SELECT * FROM delta_runs WHERE request_id=?", (context.request_id,)).fetchone()
                if existing:
                    if existing["binding_digest"] != binding_digest:
                        raise LaneError("REQUEST_ID_CONFLICT", "This request ID is already bound to different work.")
                    return self._admission(existing)
        with self.engine.project_work.mutation(store) as lease:
            spec, view, route, source_route = self._preflight(context, request, store)
            if view.state not in {"queued", "active"}:
                raise LaneError("DELTA_TASK_NOT_RUNNABLE", "Replan this task before another execution.")
            queue = JobQueue(store)
            queue.initialize(lease)
            apply_migrations(store, DELTA_MIGRATIONS, writer=lease)
            learning = LearningStore(store)
            learning.initialize(lease)
            lessons = learning.read(LearningRead(profile=spec.profile, action=spec.name), exact_scope_paths=view.definition.permitted_paths)
            from .tool_routes import admission_binding
            selected_tools = admission_binding(route)
            entry_body = {**binding, "tool_admission": selected_tools, "source_route": source_route, "learning_context": [{"version_id": item["version_id"], "content_digest": item["content_digest"]}
                                                         for item in lessons.lessons]}
            from .mode_governance import validate_task_mode_binding
            entry_body['task_mode'] = validate_task_mode_binding(store, view.definition, action=spec.name, registry=self.engine.registry)
            target = ActionRequest(request_id=context.request_id, action=request.action, project_id=store.project_id,
                                   expected_revision=request.plan_revision, arguments=request.arguments)
            plan = PlanStore(store)
            plan_changed = False
            with lease.transaction('plan') as connection:
                # Preflight has already enforced this task's input budget. Keep
                # its admitted arguments plus bounded receipt overhead durable.
                entry_object = store.lane('plan').put_object(json_text(entry_body).encode(),
                    limit=min(view.definition.budget.max_input_bytes + 65536, 67_174_400))
                if self.engine.project_work.paused(connection):
                    raise LaneError("PROJECT_PAUSED", "Explicit new input and Plan refresh are required after a stop.")
                if (connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone()
                        and connection.execute("SELECT 1 FROM steer_requests WHERE expected_revision=? AND state IN "
                            "('pending','checkpointing','ready') LIMIT 1", (request.plan_revision,)).fetchone()):
                    raise LaneError("PLAN_STEER_PENDING", "Reconcile pending semantic steers before new work.")
                if connection.execute("SELECT 1 FROM delta_runs WHERE plan_revision=? AND task_id=?",
                                      (request.plan_revision, request.task_id)).fetchone():
                    raise LaneError("DELTA_ATTEMPT_EXISTS", "Inspect this task's existing run; do not replay it.")
                if connection.execute("SELECT 1 FROM jobs_jobs WHERE state IN ('running','uncertain') LIMIT 1").fetchone():
                    raise LaneError("JOBS_NOT_QUIESCENT", "Reconcile unfinished project work before a new Delta.")
                if view.state == "queued":
                    plan.transition(request.task_id, "active", lease, expected_revision=request.plan_revision,
                                    actor_id=context.client_id, transaction=connection)
                    plan_changed = True
                job_id = queue.enqueue(target, context.client_id, lease, transaction=connection,
                    max_request_bytes=min(view.definition.budget.max_input_bytes + 65536, 67_174_400))
                stamp = now()
                connection.execute("INSERT INTO delta_runs VALUES(?,?,?,?,?,?,?,?, 'queued',NULL,NULL,?,?)",
                    (job_id, context.request_id, context.client_id, request.task_id, request.plan_revision,
                     request.contract_digest, binding_digest, entry_object, stamp, stamp))
                store.append_receipt("delta_entered", {"job_id": job_id, "task_id": request.task_id,
                    "plan_revision": request.plan_revision, "contract_digest": request.contract_digest,
                    "entry_object": entry_object, "actor_id": context.client_id}, connection=connection)
            if plan_changed:
                projection = plan.host_projection()
                plan._publish_if_bound(projection.projection_id, lease, actor_id=context.client_id)
        try:
            future = self.engine.start_job(lambda: self._run(store, job_id, replace(context, tool_admission=selected_tools), request, view.definition, spec))
            with self._drivers_lock:
                # Only active work and a bounded tail of completion handles.
                if len(self._drivers) >= 256:
                    self._drivers = {key: value for key, value in self._drivers.items() if not value.done()}
                self._drivers[job_id] = future
        except Exception:  # noqa: BLE001 - every failed dispatch must record its blocked job
            self._recover(store, job_id, request, "JOB_DISPATCH_FAILED")
            raise LaneError("JOB_DISPATCH_FAILED", "The recorded run's driver could not start; inspect it.") from None
        return DeltaAdmission(job_id=job_id, task_id=request.task_id, plan_revision=request.plan_revision,
                              contract_digest=request.contract_digest, state="queued")

    def owned_completion(self, job_id):
        with self._drivers_lock:
            future = self._drivers.get(job_id)
        if future is None:
            raise LaneError('DELTA_OWNER_UNAVAILABLE', 'This engine has no completion handle for the recorded run; inspect its durable state without replay.')
        return future

    def _run(self, store, job_id, context, request, task, spec):
        try:
            with self.engine.project_work.execution(store, job_id, task_id=request.task_id,
                                                    plan_revision=request.plan_revision) as execution:
                guard = execution.guard = ExecutionGuard(self.engine, store, task, spec, context)
                with execution.lease.transaction('plan') as connection:
                    connection.execute("UPDATE delta_runs SET state='running',updated_at=? WHERE job_id=?", (now(), job_id))
                try:
                    execution._before_more_work()
                    with store.lane('plan').connection(read_only=True) as connection:
                        admitted = connection.execute('SELECT entry_object FROM delta_runs WHERE job_id=?', (job_id,)).fetchone()
                    entry = json.loads(store.lane('plan').read_object(admitted['entry_object']))
                    if entry.get('task_mode') != guard.mode_validation:
                        raise LaneError('TASK_MODE_BINDING_CHANGED', 'Execution differs from its exact admitted mode snapshot.')
                    if request.source_route is not None:
                        arguments = spec.input_model.model_validate(request.arguments).model_dump(mode='json')
                        guard.source_route = SourceRouteGuard(guard, request.source_route, arguments)
                    source_route = guard.source_route.evidence() if guard.source_route is not None else None
                    if entry.get('source_route') != source_route:
                        raise LaneError('SOURCE_ROUTE_BINDING_CHANGED', 'Execution differs from its admitted immutable source-route selection.')
                    if task.validation_policy is not None:
                        from .acceptance import DeltaValidation
                        guard.validation = DeltaValidation(execution, request)
                        guard.validation.start()
                    guard.spend_call()
                    guard.input(request.arguments)
                    result, tool_execution = self.engine.registry.execute_attributed(spec.name, request.arguments, replace(context, execution=execution))
                    for future in execution._futures:
                        future.result()
                    guard.observe(execution)
                    execution._before_more_work()
                    guard.output(result)
                    body = {"job_id": job_id, "task_id": request.task_id, "plan_revision": request.plan_revision,
                            "contract_digest": request.contract_digest, "task_mode": guard.mode_validation,
                            "result": result, "usage": guard.usage(), "source_route": source_route,
                            "usage_scope": "handler_execution_before_verification",
                            "workers": guard.worker_evidence, "tool_execution": tool_execution, "engine_instance": self.engine.instance_id,
                            "execution_id": execution.claim.execution_id, "native_host_tools_attested": False}
                    with execution.lease.transaction('plan'):
                        result_object = store.lane('plan').put_object(json_text(body).encode(), limit=min(task.budget.max_output_bytes + 65536, 67_174_400))
                    self.engine.delta_exit.finish(execution, request, context, result_object)
                except Exception as error:  # noqa: BLE001 - the driver records all handler failures
                    code = error.code if isinstance(error, LaneError) else "DELTA_OPERATION_FAILED"
                    for future in execution._futures:
                        try:
                            future.result()
                        except Exception:  # noqa: BLE001 - join every owned worker and retain its failure
                            execution._worker_failed = True
                    if guard.validation is not None:
                        guard.validation.blocked(code)
                    with store.lane('plan').connection(read_only=True) as connection:
                        if self._published_completion(store, connection, job_id, request):
                            return
                    if execution.queue.get(job_id)["state"] == "running":
                        execution.checkpoint("execution_blocked", {"error_code": code, "usage": guard.usage()}, pause=True)
                    with execution.lease.transaction('plan') as connection:
                        connection.execute("UPDATE delta_runs SET state='blocked',error_code=?,updated_at=? WHERE job_id=?",
                                           (code, now(), job_id))
        except Exception as error:  # noqa: BLE001 - durable recovery handles unexpected driver failures
            code = error.code if isinstance(error, LaneError) else "DELTA_DRIVER_FAILED"
            self._recover(store, job_id, request, code)

    @staticmethod
    def _published_completion(store, connection, job_id, request):
        """A lost completion reply cannot overwrite the published evidence.

        Reconcile the exact recorded exit across the pinned lanes. Conflicting
        completion evidence raises for inspection without rewriting its state.
        """
        from .adaptive_delta_exit import read_recorded_exit

        row = connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (job_id,)).fetchone()
        if (row is None or row['task_id'] != request.task_id
                or row['plan_revision'] != request.plan_revision or row['contract_digest'] != request.contract_digest):
            raise LaneError('DELTA_RECOVERY_BINDING_CHANGED', 'Recovery requires the exact recorded task and contract.')
        job = JobQueue(store).get(job_id, transaction=connection)
        return read_recorded_exit(store, connection, row, job) is not None

    def _recover(self, store, job_id, request, code):
        try:
            with store.lane('plan').connection(read_only=True) as connection:
                if self._published_completion(store, connection, job_id, request):
                    return
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            # Acquiring the project writer below restores the unpublished
            # transaction before its run can be inspected or checkpointed.
        with self.engine.project_work.mutation(store) as lease:
            plan = PlanStore(store)
            plan_changed = False
            with lease.transaction('plan') as connection:
                if self._published_completion(store, connection, job_id, request):
                    return
                connection.execute("UPDATE delta_runs SET state='blocked',error_code=?,updated_at=? WHERE job_id=?", (code, now(), job_id))
                connection.execute("UPDATE jobs_jobs SET state='failed',error_code=?,resumable=0,updated_at=? "
                                   "WHERE job_id=? AND state='queued'", (code, now(), job_id))
                head = PlanStore._head(connection)
                if head["revision"] == request.plan_revision:
                    task = PlanStore._task(connection, request.plan_revision, request.task_id)
                    if task["state"] == "active":
                        plan.transition(request.task_id, "blocked", lease, expected_revision=request.plan_revision,
                                        actor_id="engine_recovery", transaction=connection)
                        plan_changed = True
                store.append_receipt("delta_driver_blocked", {"job_id": job_id, "error_code": code,
                    "automatic_replay": False}, connection=connection)
            if plan_changed:
                projection = plan.host_projection()
                plan._publish_if_bound(projection.projection_id, lease, actor_id='engine_recovery')
