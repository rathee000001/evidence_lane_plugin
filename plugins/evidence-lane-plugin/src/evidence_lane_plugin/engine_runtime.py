"""Shared engine construction and startup recovery for local and HTTPS hosts."""
from __future__ import annotations

import platform
from pathlib import Path

from .engine import Engine
from .errors import LaneError
from .jobs import JobQueue
from .projects import atomic_json
from .runtime_health import CapabilityMonitor
from .workers import WorkerOperation, WorkerPool
from .writers import WriterLease


def create_runtime_engine(runtime_root: Path, *, workers: int = 2, capabilities=None) -> Engine:
    """Windows hosts share all lane workers; Unix keeps the reduced native path."""
    pool = None
    if platform.system() == 'Windows':
        from .acceptance import validation_worker_operations
        from .code_workers import code_worker_operations
        from .document_workers import document_worker_operations
        from .media_workers import media_worker_operations
        from .pdf_workers import pdf_worker_operations
        from .powerbi_workers import powerbi_worker_operations
        from .presentation_workers import presentation_worker_operations
        from .research_discovery_workers import discovery_worker_operations
        from .research_web_workers import web_worker_operations
        from .sector_evidence_workers import evidence_worker_operations
        from .tableau_workers import tableau_worker_operations
        from .tabular_workers import tabular_worker_operations

        operations = (*validation_worker_operations(), *code_worker_operations(), *document_worker_operations(), *tabular_worker_operations(),
            *presentation_worker_operations(), *tableau_worker_operations(), *powerbi_worker_operations(),
            *pdf_worker_operations(), *media_worker_operations(), *evidence_worker_operations(),
            *web_worker_operations(), *discovery_worker_operations(),
            WorkerOperation('hash_text', 'evidence_lane_plugin.worker_tasks', 'hash_text'),
            WorkerOperation('render_lane_view', 'evidence_lane_plugin.artifact_contract', 'render_lane_view_worker',
                            dependencies=('langgraph', 'langchain_core', 'graphviz', 'rustworkx')))
        pool = WorkerPool(operations, workers=workers)
        capabilities = capabilities or CapabilityMonitor.from_shared_installation()
    return Engine(runtime_root, worker_pool=pool, capabilities=capabilities or CapabilityMonitor())


def reconcile_jobs(engine: Engine) -> list[dict]:
    """Reconcile prior admitted work under its writer; never replay its effects."""
    recovery = []
    for project_id, record in engine.directory.entries().items():
        if record['read_only']:
            recovery.append({'project_id': project_id, 'state': 'read_only', 'automatic_replay': False})
            continue
        try:
            store = engine.directory.open(project_id, write=True)
            with store.lane('plan').connection(read_only=True) as connection:
                present = connection.execute("SELECT 1 FROM sqlite_master WHERE name='jobs_jobs' AND type='table'").fetchone()
            if not present:
                recovery.append({'project_id': project_id, 'state': 'no_job_schema', 'automatic_replay': False})
                continue
            with WriterLease(store, engine.instance_id) as lease:
                outcomes = JobQueue(store).reconcile_interrupted(lease)
            recovery.append({'project_id': project_id, 'state': 'reconciled', 'outcomes': outcomes, 'automatic_replay': False})
        except LaneError as error:
            recovery.append({'project_id': project_id, 'state': 'unavailable', 'reason': error.code, 'automatic_replay': False})
    atomic_json(engine.root / 'startup-recovery.json', {
        'engine_instance': engine.instance_id, 'previous_shutdown': engine.previous_shutdown,
        'projects': recovery, 'automatic_replay': False})
    return recovery
