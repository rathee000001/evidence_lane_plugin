"""Bounded, prewarmed processes. Registration is trusted engine configuration."""

from __future__ import annotations

import importlib
import importlib.util
import json
import multiprocessing
import os
import queue
import re
import threading
import time
from concurrent.futures import CancelledError, Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass
from multiprocessing.queues import Queue
from typing import Self
from uuid import uuid4

from .errors import LaneError
from .process_ownership import ChildProcessGroup, join_child_group
from .storage import json_text


@dataclass(frozen=True)
class WorkerOperation:
    name: str
    module: str
    function: str
    dependencies: tuple[str, ...] = ()
    path_fields: tuple[str, ...] = ()
    max_output_bytes: int = 4_194_304
    error_codes: tuple[str, ...] = ()
    max_input_bytes: int = 1_048_576


_OPERATIONS: dict[str, WorkerOperation] = {}


def _initialize(operations: tuple[WorkerOperation, ...], ready, prewarm: tuple[str, ...], owner_group: str | None):
    global _OPERATIONS
    _OPERATIONS = {operation.name: operation for operation in operations}
    join_child_group(owner_group)
    try:
        for module in prewarm:
            importlib.import_module(module)
        ready.put({"pid": os.getpid(), "prewarmed_modules": list(prewarm), "state": "ready"})
    except ImportError:
        ready.put({"pid": os.getpid(), "state": "failed", "code": "PREWARM_DEPENDENCY_FAILED"})
        raise RuntimeError("Worker prewarm dependency failed") from None


def _probe() -> int:
    return os.getpid()


def _perform(name: str, arguments: dict) -> dict:
    operation = _OPERATIONS[name]
    try:
        for module in operation.dependencies:
            importlib.import_module(module)
        target = getattr(importlib.import_module(operation.module), operation.function)
        result = target(arguments)
        if not isinstance(result, dict):
            raise TypeError()
        if len(json_text(result).encode()) > operation.max_output_bytes:
            return {"status": "error", "code": "WORKER_OUTPUT_TOO_LARGE"}
        return {"status": "ok", "result": result, "worker_pid": os.getpid(),
                "loaded_modules": list(operation.dependencies)}
    except ImportError:
        return {"status": "error", "code": "WORKER_DEPENDENCY_FAILED"}
    except LaneError as error:
        # Only a finite code declared by the trusted operation may cross the
        # boundary. Messages, details and arbitrary vendor exceptions stay out.
        return {"status": "error", "code": error.code if error.code in operation.error_codes else "WORKER_OPERATION_FAILED"}
    except Exception:  # noqa: BLE001 - never return raw inputs or vendor exceptions across the worker boundary
        return {"status": "error", "code": "WORKER_OPERATION_FAILED"}


class WorkerPool:
    def __init__(self, operations: tuple[WorkerOperation, ...], *, workers: int = 2,
                 queue_capacity: int = 8, prewarm: tuple[str, ...] = ("hashlib", "json")):
        if not 1 <= workers <= 16 or not 0 <= queue_capacity <= 256:
            raise LaneError("INVALID_WORKER_BUDGET", "Select bounded worker and queue counts.")
        if len({operation.name for operation in operations}) != len(operations):
            raise LaneError("DUPLICATE_WORKER_OPERATION", "Register each worker operation once.")
        if any(not 1024 <= operation.max_output_bytes <= 67_108_864 for operation in operations):
            raise LaneError('INVALID_WORKER_OUTPUT_BUDGET', 'Register a bounded output contract for each worker operation.')
        if any(not 1024 <= operation.max_input_bytes <= 33_554_432 for operation in operations):
            raise LaneError('INVALID_WORKER_INPUT_BUDGET', 'Register a bounded input contract for each worker operation.')
        if any(len(operation.error_codes) > 64 or any(not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', code)
                for code in operation.error_codes) for operation in operations):
            raise LaneError('INVALID_WORKER_ERROR_CODES', 'Register a finite set of public worker error codes.')
        self.operations = {operation.name: operation for operation in operations}
        self.workers = workers
        self.queue_capacity = queue_capacity
        self.prewarm = prewarm
        self._slots = threading.BoundedSemaphore(workers + queue_capacity)
        self._mutex = threading.RLock()
        self._executor: ProcessPoolExecutor | None = None
        self._ready_queue: Queue | None = None
        self._ready: list[dict] = []
        self._outstanding: set[Future] = set()
        self._accepting = False
        self._submitted = 0
        self._completed = 0
        self._succeeded_operations = 0
        self._failed_operations = 0
        self._cancelled_operations = 0
        self._state = "created"
        self._child_group = ChildProcessGroup()
        self.generation = str(uuid4())
        self._closed = threading.Event()
        self._closing = False
        self._close_error: BaseException | None = None

    def start(self, *, timeout: float = 15) -> None:
        with self._mutex:
            if self._state != "created":
                raise LaneError("WORKERS_ALREADY_STARTED", "This worker pool is already started.")
            context = multiprocessing.get_context("spawn")
            self._state = "starting"
            self._child_group.start()
            self._ready_queue = context.Queue(maxsize=self.workers)
            self._executor = ProcessPoolExecutor(
                max_workers=self.workers, mp_context=context, initializer=_initialize,
                initargs=(tuple(self.operations.values()), self._ready_queue, self.prewarm, self._child_group.name),
            )
            try:
                # Each submission starts another spawn worker until the configured pool is full.
                probes = [self._executor.submit(_probe) for _ in range(self.workers)]
                deadline = time.monotonic() + timeout
                ready: dict[int, dict] = {}
                while len(ready) < self.workers:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty()
                    item = self._ready_queue.get(timeout=remaining)
                    if item["state"] != "ready":
                        raise LaneError("WORKER_PREWARM_FAILED", "A worker failed its initial imports.")
                    ready[item["pid"]] = item
                for probe in probes:
                    probe.result(timeout=max(0.01, deadline - time.monotonic()))
                self._ready = list(ready.values())
                self._accepting = True
                self._state = "ready"
            except queue.Empty:
                self.close(force=True)
                raise LaneError("WORKER_START_TIMEOUT", "Workers did not acknowledge startup in time.") from None
            except BaseException:
                self.close(force=True)
                raise

    def submit(self, name: str, arguments: dict) -> Future:
        # Detach the payload before the asynchronous executor feeder serializes
        # it; a handler cannot alter an already scope-checked submission later.
        arguments = json.loads(json_text(arguments))
        with self._mutex:
            if not self._accepting or self._executor is None:
                raise LaneError("WORKERS_DRAINING", "The worker pool is not accepting work.")
            operation = self.operations.get(name)
            if operation is None:
                raise LaneError("UNKNOWN_WORKER_OPERATION", "This operation is not registered.")
            if len(json_text(arguments).encode()) > operation.max_input_bytes:
                raise LaneError("WORKER_INPUT_TOO_LARGE", "Use addressed input files for large payloads.")
            for module in operation.dependencies:
                try:
                    available = importlib.util.find_spec(module) is not None
                except (ModuleNotFoundError, ValueError):
                    available = False
                if not available:
                    raise LaneError("DEPENDENCY_UNAVAILABLE", "A required worker module is unavailable.",
                                    details={"module": module, "operation": name})
            if not self._slots.acquire(blocking=False):
                raise LaneError("WORKER_QUEUE_FULL", "The worker queue has reached its configured limit.")
            try:
                future = self._executor.submit(_perform, name, arguments)
            except BaseException:
                self._slots.release()
                raise
            self._outstanding.add(future)
            self._submitted += 1
            future.add_done_callback(self._finished)
            return future

    def _finished(self, future: Future) -> None:
        with self._mutex:
            self._outstanding.discard(future)
            self._completed += 1
            try:
                result = future.result()
                if result.get("status") == "ok":
                    self._succeeded_operations += 1
                else:
                    self._failed_operations += 1
            except BrokenProcessPool:
                self._failed_operations += 1
                self._accepting = False
                self._state = "broken"
            except CancelledError:
                self._cancelled_operations += 1
            except RuntimeError:
                self._failed_operations += 1
            self._slots.release()

    def status(self) -> dict:
        with self._mutex:
            return {"state": self._state, "generation": self.generation,
                    "shutdown_complete": self._closed.is_set() and self._close_error is None,
                    "accepting": self._accepting, "configured_workers": self.workers,
                    "initialized_workers": list(self._ready), "queue_capacity": self.queue_capacity,
                    "outstanding": len(self._outstanding), "submitted": self._submitted,
                    "completed": self._completed, "completion_basis": "finished_futures",
                    "succeeded_operations": self._succeeded_operations, "failed_operations": self._failed_operations,
                    "cancelled_operations": self._cancelled_operations,
                    "process_identity_role": "diagnostic_only",
                    "worker_identity_basis": "startup_acknowledgements; current liveness is not inferred from PID"}

    def begin_drain(self) -> None:
        with self._mutex:
            self._accepting = False
            if self._state != "stopped":
                self._state = "draining"

    def close(self, *, force: bool = False, timeout: float | None = None) -> bool:
        """Wait for process exit and completion callbacks, with an optional bound.

        Timeout leaves the same shutdown operation running. Repeated waits do
        not resubmit, cancel, kill or replace accepted work.
        """
        with self._mutex:
            self._accepting = False
            if not self._closing:
                self._closing = True
                self._state = "draining"
                executor, self._executor = self._executor, None
                if force:
                    self._shutdown(executor, force=True)
                else:
                    threading.Thread(target=self._shutdown, args=(executor,), daemon=True,
                                     name="evidence-lane-worker-drain").start()
        completed = self._closed.wait(timeout)
        if completed and self._close_error is not None:
            raise LaneError("WORKER_SHUTDOWN_FAILED", "Worker shutdown did not complete safely.") from None
        return completed

    def _shutdown(self, executor, *, force: bool = False) -> None:
        try:
            self._shutdown_executor(executor, force=force)
        except BaseException as error:  # noqa: BLE001 - transfer shutdown failure to the waiting owner
            self._close_error = error
        finally:
            self._closed.set()

    def _shutdown_executor(self, executor, *, force: bool) -> None:
        if executor is not None:
            if force:
                if not hasattr(executor, "kill_workers"):
                    raise LaneError("RUNTIME_VERSION_UNSUPPORTED", "Forced worker shutdown requires Python 3.14.")
                executor.kill_workers()
            else:
                executor.shutdown(wait=True, cancel_futures=False)
        if self._ready_queue is not None:
            self._ready_queue.close()
            self._ready_queue.join_thread()
            self._ready_queue = None
        self._child_group.close()
        with self._mutex:
            self._state = "stopped"

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.close()
