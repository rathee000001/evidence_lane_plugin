"""One project writer for OS-worker jobs, capture and safe control checkpoints."""

from __future__ import annotations

import threading
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from .errors import LaneError
from .jobs import JobClaim, JobQueue
from .plan_runtime import PlanStore
from .storage import ProjectStore, json_text, now
from .writers import WriterLease


@dataclass
class _ProjectSlot:
    root: Path
    mutex: threading.RLock = field(default_factory=threading.RLock)
    execution: Execution | None = None


class Execution:
    def __init__(self, coordinator, slot, store: ProjectStore, lease: WriterLease, claim: JobClaim,
                 task_id: str | None, plan_revision: int | None):
        self.coordinator, self.slot, self.store, self.lease, self.claim = coordinator, slot, store, lease, claim
        self.task_id, self.plan_revision = task_id, plan_revision
        self.queue = JobQueue(store)
        self.plan_store = self.queue.store
        self._futures: set[Future] = set()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_error: str | None = None
        self._closed = False
        self._worker_failed = False
        self.guard = None
        self._heartbeat_thread = threading.Thread(target=self._heartbeat, daemon=True, name="evidence-lane-writer-heartbeat")

    def _heartbeat(self):
        while not self._heartbeat_stop.wait(15):
            with self.slot.mutex:
                if self._closed:
                    return
                try:
                    self.lease.heartbeat()
                except LaneError as error:
                    if error.code == "PROJECT_COMMIT_BUSY":
                        # A long operation may still be publishing under this
                        # exact writer. The active commit rechecks the fence and
                        # lease before publication; retry the heartbeat after
                        # the normal interval instead of poisoning the job.
                        continue
                    self._heartbeat_error = error.code
                    return

    def _current(self, connection=None):
        if self._closed or self.slot.execution is not self:
            raise LaneError("JOB_OWNER_CHANGED", "This execution no longer owns the project.")
        if self._heartbeat_error:
            raise LaneError(self._heartbeat_error, "The writer heartbeat failed; reconcile before continuing work.")
        self.lease.check(connection)
        if connection is None:
            with self.plan_store.connection(read_only=True) as selected:
                return self._current(selected)
        self.queue._running(connection, self.claim.job_id, self.lease, self.claim.execution_id)
        if self.plan_revision is not None:
            PlanStore._head(connection, self.plan_revision)

    def _steers(self, connection):
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone():
            return []
        # A new Plan head crosses the execution's pinned revision even when it
        # changes a later row. Observe a safe boundary before installing it.
        rows = connection.execute("SELECT * FROM steer_requests WHERE state IN ('pending','checkpointing') "
            "AND expected_revision=? ORDER BY created_at,request_id LIMIT 101", (self.plan_revision,)).fetchall()
        if len(rows) > 100:
            raise LaneError("STEER_BACKLOG_BUDGET", "Reconcile the bounded steer backlog before continuing work.")
        return rows

    def _before_more_work(self):
        if self.guard is not None:
            self.guard.check()
        with self.plan_store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            self._current(connection)
            row = self.queue._row(connection, self.claim.job_id)
            if row["cancellation_reason"] or self._steers(connection) or self.coordinator.paused(connection):
                raise LaneError("JOB_CHECKPOINT_REQUIRED", "Handle the pending stop or steer before more work.")

    def submit(self, operation: str, arguments: dict) -> Future:
        with self.slot.mutex:
            self._before_more_work()
            if self.coordinator.engine.workers is None:
                raise LaneError("WORKERS_UNAVAILABLE", "Configure the required OS worker pool first.")
            if self.guard is not None:
                arguments = self.guard.worker(operation, arguments)
            future = self.coordinator.engine.workers.submit(operation, arguments)
            self._futures.add(future)
            return future

    def check(self) -> None:
        """Check current ownership, Plan, stop/steer and grants during owned I/O."""
        with self.slot.mutex:
            self._before_more_work()

    def prepare_effect(self, key: str, description: str) -> str:
        with self.slot.mutex:
            self._before_more_work()
            if self.guard is not None:
                self.guard.spend_call()
            return self.queue.prepare_effect(self.claim.job_id, key, description, self.lease, execution_id=self.claim.execution_id)

    def confirm_effect(self, effect_id: str, evidence: str):
        with self.slot.mutex:
            self._current()
            self.queue.confirm_effect(self.claim.job_id, effect_id, evidence, self.lease, execution_id=self.claim.execution_id)

    def checkpoint(self, phase: str, content: dict, *, resumable: bool = False, pause: bool = False) -> dict:
        with self.slot.mutex:
            self._current()
            if any(not future.done() for future in self._futures):
                raise LaneError("JOB_WORK_STILL_RUNNING", "Wait for the owned worker before claiming a safe checkpoint.")
            self.worker_results()
            if self.guard is not None:
                try:
                    self.guard.observe(self)
                except LaneError as error:
                    self.guard.failure_code = error.code
                    pause = True
            body = {"job_id": self.claim.job_id, "execution_id": self.claim.execution_id, "owner_fence": self.claim.owner_fence,
                    "task_id": self.task_id, "plan_revision": self.plan_revision, "phase": phase, "content": content,
                    "worker_generation": self.coordinator.engine.workers.generation if self.coordinator.engine.workers else None,
                    "quiescence_scope": "this_engine_owned_job", "native_host_tools_attested": False}
            with self.lease.transaction('plan') as connection:
                digest = self.plan_store.put_object(json_text(body).encode(), limit=1_048_576)
                self._current(connection)
                steers = self._steers(connection)
                if any(row["intent"] == "stop" for row in steers):
                    connection.execute("UPDATE jobs_jobs SET cancellation_reason=? WHERE job_id=?",
                                       ("Visible stop request", self.claim.job_id))
                state = self.queue.checkpoint(self.claim.job_id, self.lease, phase=phase, object_digest=digest,
                    resumable=resumable and not steers, pause=pause or bool(steers), execution_id=self.claim.execution_id, transaction=connection)
                paused = state != "running"
                if paused and self.task_id is not None:
                    PlanStore(self.store).transition(self.task_id, "blocked", self.lease, expected_revision=cast(int, self.plan_revision),
                                                    actor_id=self.claim.request.request_id, transaction=connection)
                for request in steers:
                    connection.execute("UPDATE steer_requests SET state=?,checkpoint_object=?,updated_at=? WHERE request_id=?",
                        ("checkpointing" if state == "uncertain" else "ready", digest, now(), request["request_id"]))
                self.store.append_receipt("job_safe_checkpoint", {"job_id": self.claim.job_id, "execution_id": self.claim.execution_id,
                    "checkpoint_object": digest, "state": state, "steer_ids": [row["request_id"] for row in steers],
                    "scope": "this_engine_owned_job", "automatic_kill": False}, connection=connection)
            if paused and self.task_id is not None:
                plan = PlanStore(self.store)
                projection = plan.host_projection()
                plan._publish_if_bound(
                    projection.projection_id, self.lease, actor_id=self.claim.request.request_id)
            self._futures.clear()
            return {"job_id": self.claim.job_id, "state": state, "checkpoint_object": digest, "paused": paused,
                    "steers_ready": state != "uncertain" and bool(steers)}

    def worker_results(self) -> list[dict]:
        """Remember failure across checkpoints; unfinished work is never proof."""
        results = []
        for future in self._futures:
            if not future.done():
                raise LaneError("JOB_WORK_STILL_RUNNING", "An owned OS worker is still running.")
            try:
                result = future.result()
            except Exception:  # noqa: BLE001 - vendor worker failure is recorded, never treated as success
                result = {"status": "error", "code": "WORKER_OPERATION_FAILED"}
            if result.get("status") != "ok":
                self._worker_failed = True
            results.append(result)
        return results

    def complete(self, result: dict):
        """Internal job completion; Plan success still requires verified Delta exit."""
        with self.slot.mutex:
            self._before_more_work()
            if any(not future.done() for future in self._futures):
                raise LaneError("JOB_WORK_STILL_RUNNING", "A job has an unfinished owned worker.")
            if self._worker_failed:
                raise LaneError("WORKER_OPERATION_FAILED", "A previous worker operation failed before a checkpoint.")
            for future in self._futures:
                try:
                    succeeded = future.result().get("status") == "ok"
                except Exception:  # noqa: BLE001 - do not propagate vendor exceptions from a worker result
                    succeeded = False
                if not succeeded:
                    raise LaneError("WORKER_OPERATION_FAILED", "A required owned worker operation did not succeed.")
            self.queue.complete(self.claim.job_id, result, self.lease, execution_id=self.claim.execution_id)

    def _finish(self):
        # Keep the writer until owned work finishes. Hold no project/engine mutex
        # while waiting, so bounded capture and cancellation can still be recorded.
        for future in self._futures:
            try:
                future.result()
            except Exception:  # noqa: BLE001 - incomplete execution is recorded below
                self._worker_failed = True
        self._heartbeat_stop.set()
        self._heartbeat_thread.join()
        with self.slot.mutex:
            try:
                row = self.queue.get(self.claim.job_id)
                if row["state"] == "running" and row["execution_id"] == self.claim.execution_id and not self._heartbeat_error:
                    code = "WORKER_OPERATION_FAILED" if self._worker_failed else "EXECUTION_ENDED_WITHOUT_RESULT"
                    self.queue.fail(self.claim.job_id, code, self.lease, execution_id=self.claim.execution_id)
            finally:
                self._closed = True
                try:
                    self.lease.release()
                finally:
                    self.slot.execution = None


class ProjectCoordinator:
    def __init__(self, engine):
        self.engine = engine
        self._slots: dict[str, _ProjectSlot] = {}
        self._guard = threading.RLock()
        self._authorization = threading.local()

    @contextmanager
    def authorized(self, context, permission):
        previous = getattr(self._authorization, "check", None)
        self._authorization.check = (lambda: context.authorize(permission)) if context.authorize else None
        try:
            yield
        finally:
            self._authorization.check = previous

    def _authorize_mutation(self):
        check = getattr(self._authorization, "check", None)
        if check is not None:
            check()

    @staticmethod
    def paused(connection) -> bool:
        if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_control'").fetchone():
            return False
        row = connection.execute("SELECT paused FROM steer_control WHERE singleton=1").fetchone()
        return bool(row and row[0])

    def _slot(self, store: ProjectStore):
        with self._guard:
            slot = self._slots.setdefault(store.project_id, _ProjectSlot(store.root))
            if slot.root != store.root:
                raise LaneError("PROJECT_IDENTITY_COLLISION", "The live project writer is bound to another root.")
            return slot

    @contextmanager
    def control_boundary(self, store):
        """Serialize client control with project jobs before its owner takes a writer."""
        slot = self._slot(store)
        with slot.mutex:
            self._authorize_mutation()
            store.assert_current_binding()
            if slot.execution is not None:
                raise LaneError('PROJECT_WRITER_BUSY', 'Checkpoint project work before changing its client selection.')
            yield

    @contextmanager
    def mutation(self, store: ProjectStore, *, kind: str = "ordinary"):
        slot = self._slot(store)
        with slot.mutex:
            self._authorize_mutation()
            if slot.execution is not None:
                store.assert_current_binding()
                if kind not in {"capture", "signal"}:
                    raise LaneError("PROJECT_WRITER_BUSY", "An active job owns this project's mutation scope.")
                slot.execution.lease.check()
                yield slot.execution.lease
            else:
                # Writer acquisition reconciles an interrupted publication
                # before checking the current binding under its kernel lock.
                with WriterLease(store, self.engine.instance_id) as lease:
                    yield lease

    def checkpoint_idle_plan(self, store, lease, *, request_id, expected_revision, actor_id):
        """Checkpoint an active selection that has never admitted a job.

        Running and previously admitted work retain their owning job checkpoint
        or recovery route. The caller publishes this boundary with its steer.
        """
        slot = self._slot(store)
        with slot.mutex:
            self._authorize_mutation()
            store.assert_current_binding()
            plan = PlanStore(store)
            plan._lease(lease)
            if slot.execution is not None:
                return None
            with lease.transaction('plan') as connection:
                plan._head(connection, expected_revision)
                selected = connection.execute('SELECT * FROM steer_requests WHERE request_id=?', (request_id,)).fetchone()
                if (selected is None or selected['actor_id'] != actor_id
                        or selected['expected_revision'] != expected_revision):
                    raise LaneError('STEER_SOURCE_MISMATCH', 'The idle checkpoint must belong to this client and Plan steer.')
                if selected['state'] not in {'pending', 'checkpointing'}:
                    return None
                active = connection.execute("SELECT * FROM plan_tasks WHERE revision=? AND state='active'", (expected_revision,)).fetchone()
                if active is None:
                    return None
                plan._view(active)
                def exists(table):
                    return connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone()
                if exists('jobs_jobs') and connection.execute("SELECT 1 FROM jobs_jobs WHERE state IN ('queued','running','uncertain','checkpointed') LIMIT 1").fetchone():
                    return None
                if exists('jobs_effects') and connection.execute("SELECT 1 FROM jobs_effects WHERE state='prepared' LIMIT 1").fetchone():
                    return None
                if exists('delta_runs') and connection.execute('SELECT 1 FROM delta_runs WHERE task_id=? AND plan_revision=?',
                        (active['task_id'], expected_revision)).fetchone():
                    return None
                steers = connection.execute("SELECT request_id FROM steer_requests WHERE expected_revision=? "
                    "AND state IN ('pending','checkpointing') ORDER BY created_at,request_id LIMIT 101", (expected_revision,)).fetchall()
                if len(steers) > 100:
                    raise LaneError('STEER_BACKLOG_BUDGET', 'Reconcile the bounded steer backlog before checkpointing.')
                body = {'task_id': active['task_id'], 'plan_revision': expected_revision,
                    'contract_digest': active['contract_digest'], 'phase': 'selected_task_before_job_admission',
                    'steer_ids': [row['request_id'] for row in steers], 'actor_id': actor_id,
                    'engine_instance': self.engine.instance_id, 'owner_fence': lease.fence,
                    'quiescence_scope': 'this_project_engine_jobs_and_unstarted_selected_task',
                    'native_host_tools_attested': False, 'job_admitted': False, 'automatic_replay': False}
                digest = store.lane('plan').put_object(json_text(body).encode(), limit=65_536)
                plan.transition(active['task_id'], 'blocked', lease, expected_revision=expected_revision,
                    actor_id=actor_id, transaction=connection)
                for row in steers:
                    connection.execute("UPDATE steer_requests SET state='ready',checkpoint_object=?,updated_at=? WHERE request_id=?",
                        (digest, now(), row['request_id']))
                store.append_receipt('plan_idle_checkpoint', {**body, 'checkpoint_object': digest,
                    'plan_revision_changed': False, 'automatic_kill': False}, connection=connection)
                return digest

    @contextmanager
    def execution(self, store: ProjectStore, job_id: str, *, task_id: str | None = None, plan_revision: int | None = None):
        with self.engine.admit():
            slot = self._slot(store)
            with slot.mutex:
                if slot.execution is not None:
                    raise LaneError("PROJECT_WRITER_BUSY", "This project already has an active job.")
                lease = WriterLease(store, self.engine.instance_id)
                lease.acquire()
                try:
                    if (task_id is None) != (plan_revision is None):
                        raise LaneError("JOB_PLAN_BINDING_REQUIRED", "Bind both task and Plan revision, or neither.")
                    if task_id is not None and PlanStore(store).task(task_id, expected_revision=cast(int, plan_revision)).state != "active":
                        raise LaneError("PLAN_TASK_NOT_ACTIVE", "Activate the exact Plan task before executing its job.")
                    queue = JobQueue(store)
                    with queue.store.connection(read_only=True) as connection:
                        if self.paused(connection):
                            raise LaneError("PROJECT_PAUSED", "A visible stop paused this project; explicit new input is required to resume.")
                    if queue.get(job_id)["plan_revision"] != plan_revision:
                        raise LaneError("JOB_PLAN_BINDING_REQUIRED", "The queued request and execution must bind the same Plan revision.")
                    claim = queue.claim(job_id, lease)
                    active = Execution(self, slot, store, lease, claim, task_id, plan_revision)
                    slot.execution = active
                    active._heartbeat_thread.start()
                except BaseException:
                    slot.execution = None
                    lease.release()
                    raise
            try:
                yield active
            finally:
                active._finish()

    def status(self) -> list[dict]:
        with self._guard:
            slots = list(self._slots.items())
        result = []
        for project_id, slot in slots:
            with slot.mutex:
                active = slot.execution
                if active:
                    result.append({"project_id": project_id, "job_id": active.claim.job_id,
                        "execution_id": active.claim.execution_id, "task_id": active.task_id,
                        "plan_revision": active.plan_revision, "worker_operations_pending": sum(not future.done() for future in active._futures),
                        "heartbeat_error": active._heartbeat_error, "worker_kind": "os_process"})
        return result
