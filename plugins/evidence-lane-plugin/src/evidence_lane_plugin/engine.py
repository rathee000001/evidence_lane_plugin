"""Process ownership and durable lifecycle for the local v4 engine.

The OS lock establishes singleton ownership. A PID is diagnostic information,
never evidence of a native Codex task or of permission to take over a writer.
"""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Self
from uuid import uuid4

from . import __version__
from .build import runtime_source_identity
from .connections import ClientRouter
from .errors import LaneError
from .host_routing import HostDetector, HostObservation
from .locking import RuntimeLock
from .projects import ProjectDirectory, atomic_json
from .registry import ActionRegistry, ActionSpec, Contract
from .runtime_health import CapabilityMonitor, RuntimeStatus, RuntimeStatusInput, runtime_status
from .storage import now, reject_links
from .workers import WorkerPool


class Empty(Contract):
    pass


class ProjectStatus(Contract):
    project_id: str
    format_version: int
    object_count: int
    object_bytes: int
    receipt_count: int


class EngineHealth(Contract):
    version: str
    instance_id: str
    phase: Literal["created", "running", "draining", "stopped"]
    started_at: str | None
    previous_shutdown: Literal["first_start", "clean", "unclean"]
    project_count: int
    host_observation: HostObservation | None
    native_task_attestation: Literal["not_provided"] = "not_provided"
    accepted_requests: int = 0
    runtime_identity: dict


class Quiescence(Contract):
    instance_id: str
    quiescent: bool
    accepted_requests: int
    worker_generation: str | None
    stage: Literal["requests", "workers", "provider_workers", "quiescent"]
    automatic_kill: Literal[False] = False
    automatic_replay: Literal[False] = False


class Engine:
    def __init__(self, runtime_root: Path, *, registry: ActionRegistry | None = None,
                 detector: HostDetector | None = None, worker_pool: WorkerPool | None = None,
                 capabilities: CapabilityMonitor | None = None):
        self.root = Path(os.path.abspath(runtime_root.expanduser()))
        self.runtime_identity = runtime_source_identity()
        reject_links(self.root, Path(self.root.anchor))
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = RuntimeLock(self.root / "engine.lock")
        self.state_path = self.root / "engine-state.json"
        self.directory = ProjectDirectory(self.root)
        self.detector = detector or HostDetector()
        self.host_observation: HostObservation | None = None
        self.workers = worker_pool
        self.capabilities = capabilities or CapabilityMonitor()
        from .provider_workers import ProviderWorkers
        self.provider_workers = ProviderWorkers(self.capabilities)
        self.capabilities.provider_workers = self.provider_workers
        self.instance_id = str(uuid4())
        from .storage_selection import LocalStoragePolicy, local_storage_evidence
        self.local_storage_policy = LocalStoragePolicy.load(self.root)
        self.clients = ClientRouter(self.directory, detector=self.detector, engine_id=self.instance_id,
            storage_observer=lambda project_id: local_storage_evidence(self, project_id))
        self.phase = "created"
        self.started_at: str | None = None
        self.previous_shutdown = "first_start"
        self._mutex = threading.RLock()
        self._admission = threading.Condition(self._mutex)
        self._local = threading.local()
        self._accepted = 0
        self._background_jobs = 0
        self._lifecycle = threading.RLock()
        self._stopped = threading.Event()
        self.registry = registry or ActionRegistry()
        from .extension_routes import ExtensionRouter
        self.registry.tool_router.extensions = ExtensionRouter(self)
        from .compute_routes import ComputeRouter
        self.registry.tool_router.compute = ComputeRouter(self)
        self.registry.register(
            ActionSpec(
                "engine_health", "Read the local engine lifecycle and version.",
                Empty, EngineHealth, lambda context, arguments: self.health(),
                project_required=False, workflow='open-project-session')
        )
        self.registry.register(ActionSpec(
            "runtime_status", "Read measured tools/providers, worker state and selected-project job health.",
            RuntimeStatusInput, RuntimeStatus,
            lambda context, arguments: runtime_status(self, context, arguments), project_required=False, workflow='select-project-tools'))
        self.registry.register(ActionSpec(
            "project_status", "Read identity and storage counts for one selected project.",
            Empty, ProjectStatus, self.project_status, queryable_in_delta=True, cross_project_read=True, workflow='open-project-session'))
        from .plan_runtime import register_plan_actions
        register_plan_actions(self)
        from .acceptance import register_validation_actions
        register_validation_actions(self)
        from .capture_routing import CaptureRouter, register_capture_actions
        from .lineage import register_lineage_actions
        self.capture = CaptureRouter(self)
        register_lineage_actions(self)
        register_capture_actions(self)
        from .steering import register_steer_actions
        register_steer_actions(self)
        from .coordination import ProjectCoordinator
        self.project_work = ProjectCoordinator(self)
        from .adaptive_delta_entry import DeltaService
        self.delta = DeltaService(self)
        from .live_authority_query import register_query_actions
        register_query_actions(self)
        from .adaptive_delta_exit import DeltaExit
        self.delta_exit = DeltaExit(self)
        from .agent_learning import register_learning_actions
        register_learning_actions(self)
        from .project_memory import register_memory_actions
        register_memory_actions(self)
        from .source_intake import register_source_actions
        register_source_actions(self)
        from .source_preparation import register_preparation_actions
        register_preparation_actions(self)
        from .source_materialization import SourceMaterialization
        self.source_materialization = SourceMaterialization(self)
        from .code_profile import register_code_actions
        register_code_actions(self)
        from .enrollment import register_git_sync_actions
        register_git_sync_actions(self)
        from .remote_git import register_remote_git_actions
        register_remote_git_actions(self)
        from .github_toolchain import register_github_actions
        register_github_actions(self)
        from .context_index_routing import _register_remote_context_index_actions
        _register_remote_context_index_actions(self)
        from .evaluation_toolchain import register_evaluation_actions
        from .observability_toolchain import register_observability_actions
        register_evaluation_actions(self)
        register_observability_actions(self)
        from .document_profile import register_document_actions
        register_document_actions(self)
        from .tabular_profile import register_tabular_actions
        register_tabular_actions(self)
        from .presentation_profile import register_presentation_actions
        register_presentation_actions(self)
        from .tableau_profile import register_tableau_actions
        register_tableau_actions(self)
        from .powerbi_profile import register_powerbi_actions
        register_powerbi_actions(self)
        from .pdf_profile import register_pdf_actions
        register_pdf_actions(self)
        from .media_profile import register_media_actions
        register_media_actions(self)
        from .sector_evidence_profile import register_evidence_sector_actions
        register_evidence_sector_actions(self)
        from .research_web_profile import register_web_actions
        register_web_actions(self)
        from .research_discovery_profile import register_discovery_actions
        register_discovery_actions(self)
        from .sector_evidence_views import register_evidence_views
        register_evidence_views(self)
        from .canon_task_graph import register_canon_actions
        register_canon_actions(self)
        from .task_binding_registry import register_continuation_actions
        register_continuation_actions(self)
        from .canon_runtime_continuity import register_continuation_context
        register_continuation_context(self)
        from .project_universe import register_project_link_actions
        register_project_link_actions(self)
        from .universe_snapshot import register_universe_snapshot_actions
        register_universe_snapshot_actions(self)
        from .universe_federation import register_federation_actions
        register_federation_actions(self)
        from .lane_reader import register_cross_project_actions
        register_cross_project_actions(self)
        from .lane_contract import register_authority_views
        register_authority_views(self)
        from .artifact_contract import register_artifact_actions
        from .store import register_restoration_actions
        register_restoration_actions(self)
        from .database_recovery import register_recovery_actions
        register_recovery_actions(self)
        from .job_recovery import register_job_recovery_actions
        register_job_recovery_actions(self)
        register_artifact_actions(self)
        from .workflow_surface import register_workflow_actions
        register_workflow_actions(self)
        from .connections import register_client_actions
        register_client_actions(self)
        from .connector_governance import register_connector_actions
        register_connector_actions(self)
        from .accelerators import register_accelerator_actions
        register_accelerator_actions(self)
        from .session import register_session_actions
        register_session_actions(self)
        from .state_law import register_transition_law
        register_transition_law(self)
        from .project_actions import register_project_actions
        register_project_actions(self)
        from .storage_selection import register_storage_actions
        register_storage_actions(self)
        from .mcp_apps import register_panel_actions
        register_panel_actions(self)
        from .reader import register_reader_actions
        register_reader_actions(self)
        from .agent_configuration import register_instruction_actions
        register_instruction_actions(self)
        from .first_class_workflows import register_first_class_actions
        register_first_class_actions(self)
        from .tool_routes import register_toolchain_actions
        register_toolchain_actions(self)
        from .mode_governance import register_mode_actions
        register_mode_actions(self)
        from .prompt_index import register_prompt_actions
        register_prompt_actions(self)
        from .selector_owners import register_selector_owners
        from .source_selectors import register_selector_actions
        register_selector_owners(self.registry)
        register_selector_actions(self)
        from .env_uop_tool_routing import EnvUopRuntime
        self.registry.control_plane = EnvUopRuntime(self.registry)

    def project_status(self, context, arguments) -> ProjectStatus:
        store = self.directory.open(context.project_id)
        with store.connection(read_only=True) as connection:
            metadata = connection.execute("SELECT format_version FROM project WHERE singleton=1").fetchone()
            # Holding this project evidence head coordinator read pins every lane to the same publication.
            object_count = object_bytes = receipts = 0
            for item in store.lane_catalog():
                with store.lane(item['lane_id']).connection(read_only=True) as lane:
                    objects = lane.execute("SELECT COUNT(*),COALESCE(SUM(size_bytes),0) FROM objects").fetchone()
                    object_count += objects[0]
                    object_bytes += objects[1]
                    if item['lane_id'] == 'receipts':
                        receipts = lane.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        return ProjectStatus(project_id=store.project_id, format_version=metadata[0],
                             object_count=object_count, object_bytes=object_bytes, receipt_count=receipts)

    def _persist(self) -> None:
        reject_links(self.state_path, self.root)
        atomic_json(self.state_path, {
            "format_version": 1, "engine_version": __version__,
            "instance_id": self.instance_id, "pid": os.getpid(),
            "phase": self.phase, "started_at": self.started_at, "updated_at": now(),
        })

    def start(self) -> None:
        with self._mutex:
            if self.phase != "created":
                raise LaneError("INVALID_ENGINE_TRANSITION", "Create a new engine to start again.")
            self.lock.acquire()
            try:
                reject_links(self.state_path, self.root)
                if self.state_path.exists():
                    try:
                        if self.state_path.stat().st_size > 16_384:
                            raise ValueError()
                        previous = json.loads(self.state_path.read_text(encoding="utf-8"))
                        if previous["format_version"] != 1 or previous["phase"] not in {
                            "running", "draining", "stopped"
                        }:
                            raise ValueError()
                    except (KeyError, TypeError, ValueError):
                        raise LaneError(
                            "RUNTIME_STATE_INVALID", "The previous lifecycle record is invalid."
                        ) from None
                    self.previous_shutdown = (
                        "clean" if previous["phase"] == "stopped" else "unclean"
                    )
                self.host_observation = self.detector.inspect(trigger="engine_start")
                self.capabilities.refresh()
                if self.workers is not None:
                    self.workers.start()
                self.registry.freeze()
                self.started_at = now()
                self.phase = "running"
                self._persist()
            except BaseException:
                if self.workers is not None:
                    self.workers.close(force=True)
                self.phase = "created"
                self.lock.release()
                raise

    def begin_drain(self) -> None:
        with self._mutex:
            if self.phase != "running":
                raise LaneError("INVALID_ENGINE_TRANSITION", "Only a running engine can drain.")
            self.phase = "draining"
            if self._accepted == 0 and self.workers is not None:
                self.workers.begin_drain()
            self._persist()

    @contextmanager
    def admit(self):
        """Atomically admit an entire request, including its nested SDK call.

        Draining blocks new admissions while an already accepted handler may
        still submit its worker operation. Worker shutdown follows these exits.
        """
        with self._admission:
            depth = getattr(self._local, "depth", 0)
            if depth == 0:
                if self.phase != "running":
                    raise LaneError("ENGINE_DRAINING", "The engine is finishing its current work.")
                self._accepted += 1
            self._local.depth = depth + 1
        try:
            yield
        finally:
            with self._admission:
                self._local.depth -= 1
                if self._local.depth == 0:
                    self._accepted -= 1
                    if self._accepted == 0 and self.phase == "draining" and self.workers is not None:
                        self.workers.begin_drain()
                    self._admission.notify_all()

    @contextmanager
    def lifecycle_control(self):
        if getattr(self._local, "depth", 0):
            raise LaneError("LIFECYCLE_FROM_ACTION", "Request lifecycle work through the owner control channel.")
        if not self._lifecycle.acquire(blocking=False):
            raise LaneError("LIFECYCLE_BUSY", "Another engine lifecycle operation is still finishing.")
        try:
            yield
        finally:
            self._lifecycle.release()

    def start_job(self, target) -> Future:
        """Transfer accepted work to a bounded driver before this request exits.

        The reservation closes the enqueue/drain race. Only registered engine
        code supplies a callable; tool arguments cannot choose Python code.
        """
        with self._admission:
            if not getattr(self._local, "depth", 0) or self._background_jobs >= 16:
                raise LaneError("JOB_ADMISSION_UNAVAILABLE", "No accepted background job slot is available.")
            self._background_jobs += 1
            self._accepted += 1
            completion: Future = Future()
            completion.set_running_or_notify_cancel()

            def run():
                self._local.depth = 1
                result, failure = None, None
                try:
                    result = target()
                except BaseException as error:  # noqa: BLE001 - transfer every driver failure to its owned Future
                    failure = error
                finally:
                    self._local.depth = 0
                    with self._admission:
                        self._background_jobs -= 1
                        self._accepted -= 1
                        if self._accepted == 0 and self.phase == "draining" and self.workers is not None:
                            self.workers.begin_drain()
                        self._admission.notify_all()
                # Completion means the actual owner released its work, including
                # the project writer. Nested drivers must never wait on the
                # aggregate background count (which includes themselves).
                if failure is None:
                    completion.set_result(result)
                else:
                    completion.set_exception(failure)

            try:
                threading.Thread(target=run, daemon=True, name="evidence-lane-delta-driver").start()
            except BaseException:
                self._background_jobs -= 1
                self._accepted -= 1
                self._admission.notify_all()
                raise
            return completion

    def _quiescence(self, stage: str) -> Quiescence:
        with self._mutex:
            return Quiescence(instance_id=self.instance_id, quiescent=stage == "quiescent",
                              accepted_requests=self._accepted,
                              worker_generation=self.workers.generation if self.workers else None, stage=stage)

    def quiesce(self, *, timeout: float | None = 30) -> Quiescence:
        """Bounded owner drain. A timeout keeps ownership and never kills work."""
        if timeout is not None and (not 0 <= timeout <= 3600):
            raise LaneError("INVALID_DRAIN_TIMEOUT", "Use a drain timeout between zero and one hour.")
        with self.lifecycle_control():
            deadline = time.monotonic() + timeout if timeout is not None else None
            with self._admission:
                if self.phase == "running":
                    self.begin_drain()
                if self.phase != "draining":
                    raise LaneError("INVALID_ENGINE_TRANSITION", "Only an active engine can quiesce.")
                while self._accepted:
                    remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
                    if remaining == 0:
                        return self._quiescence("requests")
                    self._admission.wait(remaining)
            if self.workers is not None:
                remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
                if not self.workers.close(timeout=remaining):
                    return self._quiescence("workers")
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
            if not self.provider_workers.close(timeout=remaining):
                return self._quiescence('provider_workers')
            return self._quiescence("quiescent")

    def replace_workers(self, replacement: WorkerPool, *, timeout: float = 30) -> Quiescence:
        """Replace compatible OS workers after the old executor fully joins.

        This replaces processes for the same operation definitions. Updating
        engine code or schema requires a complete stopped-engine installation.
        """
        with self.lifecycle_control():
            old = self.workers
            if (old is None or replacement is old or replacement.status()["state"] != "created"
                    or old.operations != replacement.operations or old.prewarm != replacement.prewarm):
                raise LaneError("INCOMPATIBLE_WORKER_REPLACEMENT", "Use a fresh pool with the same operation contracts.")
            result = self.quiesce(timeout=timeout)
            if not result.quiescent:
                return result
            replacement.start(timeout=timeout)
            with self._mutex:
                from .provider_workers import ProviderWorkers
                self.provider_workers = ProviderWorkers(self.capabilities)
                self.capabilities.provider_workers = self.provider_workers
                self.workers = replacement
                self.phase = "running"
                try:
                    self._persist()
                except BaseException:
                    self.phase = "draining"
                    raise
            return result

    def stop(self, *, timeout: float | None = None) -> bool:
        with self.lifecycle_control():
            if self.phase == "stopped":
                return True
            if not self.quiesce(timeout=timeout).quiescent:
                return False
            self.clients.close()
            with self._mutex:
                self.phase = "stopped"
                try:
                    self._persist()
                finally:
                    self.lock.release()
                    self._stopped.set()
            return True

    def health(self) -> EngineHealth:
        with self._mutex:
            return EngineHealth(
                version=__version__, instance_id=self.instance_id, phase=self.phase,
                started_at=self.started_at, previous_shutdown=self.previous_shutdown,
                project_count=len(self.directory.entries()),
                host_observation=self.host_observation,
                accepted_requests=self._accepted,
                runtime_identity=self.runtime_identity,
            )

    def wait(self, timeout: float | None = None) -> bool:
        return self._stopped.wait(timeout)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
