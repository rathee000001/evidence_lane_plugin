"""Observe uncertain effects without replaying work or declaring job success."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, JsonValue

from .bounded_io import IOBudget, bounded_file_identity
from .errors import LaneError
from .jobs import JobQueue
from .plan_runtime import PlanStore, content_digest
from .projects import ProjectAccess
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import bounded_project_read, json_text, now, reject_links
from .tool_routes import ToolRoute

DIGEST = r'^[0-9a-f]{64}$'
# These are preparation receipt contracts emitted by the current source owners.
LOCAL_PREPARATIONS = {
    'code_apply': ('code_mutation_prepared', 'path'),
    'document_export': ('document_export_prepared', 'destination'),
    'presentation_export': ('presentation_export_prepared', 'destination'),
    'tableau_export': ('tableau_export_prepared', 'destination'),
    'powerbi_export': ('powerbi_export_prepared', 'destination'),
    'pdf_export': ('pdf_export_prepared', 'destination'),
    'media_export': ('media_export_prepared', 'destination'),
    'spreadsheet_export': ('tabular_export_prepared', 'destination'),
    'data_export': ('tabular_export_prepared', 'destination'),
}


class JobRecoveryRead(Contract):
    job_id: str = Field(pattern=UUID_PATTERN)


class JobRecoveryStatus(Contract):
    project_id: str
    job_id: str
    action: str
    state: str
    plan_revision: int | None
    effects: list[dict[str, JsonValue]]
    automatic_replay: Literal[False] = False
    job_success_inferred: Literal[False] = False


class ReconcileJobEffect(JobRecoveryRead):
    effect_id: str = Field(pattern=UUID_PATTERN)
    expected_effect_digest: str = Field(pattern=DIGEST)
    plan_revision: int = Field(ge=1)
    max_file_bytes: int = Field(default=33_554_432, ge=1, le=134_217_728)
    observation_route: Literal['local_or_recorded', 'git_readback'] = 'local_or_recorded'


class ReconciledJobEffect(Contract):
    project_id: str
    job_id: str
    effect_id: str
    outcome: Literal['confirmed', 'absent']
    evidence_object: str
    job_state: str
    plan_refresh_required: Literal[True] = True
    source_bytes_mutated: Literal[False] = False
    automatic_replay: Literal[False] = False
    job_success_inferred: Literal[False] = False


def effect_digest(job, effect):
    return content_digest({'job_id': job['job_id'], 'request_digest': job['request_digest'],
        'execution_id': job['execution_id'], 'state': job['state'],
        'updated_at': job['updated_at'], 'effect': dict(effect)})


class JobRecovery:
    def __init__(self, engine, project):
        self.engine, self.project = engine, project
        self.queue = JobQueue(project)

    def inspect(self, context, request):
        with bounded_project_read(self.project.root, time.monotonic() + 10), self.queue.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='jobs_jobs'").fetchone():
                raise LaneError('JOB_NOT_FOUND', 'This project has no admitted jobs.')
            job = self.queue._row(connection, request.job_id)
            rows = connection.execute('SELECT * FROM jobs_effects WHERE job_id=? ORDER BY effect_id LIMIT 129',
                                      (request.job_id,)).fetchall()
            if len(rows) > 128:
                raise LaneError('JOB_EFFECT_BUDGET', 'The job exceeds the bounded recovery effect page.')
            effects = [{'effect_id': row['effect_id'], 'effect_key': row['effect_key'],
                'state': row['state'], 'evidence_object': row['evidence_object'],
                'effect_digest': effect_digest(job, row),
                'observation_route': ('recorded_confirmation' if row['state'] == 'confirmed' and row['evidence_object']
                    else 'local_preparation' if job['action'] in LOCAL_PREPARATIONS
                    else 'git_readback' if job['action'] in {'git_sync_selected', 'enroll_project', 'remote_git_execute_push'}
                    else 'provider_observation_required')} for row in rows]
            return JobRecoveryStatus(project_id=self.project.project_id, job_id=job['job_id'],
                action=job['action'], state=job['state'], plan_revision=job['plan_revision'], effects=effects)

    def _observe(self, context, job, effect, maximum, *, route='local_or_recorded', deadline=None):
        if effect['state'] == 'confirmed' and effect['evidence_object']:
            # This is an already committed execution confirmation, not a new
            # claim about the current external service or current source bytes.
            with self.queue.store.connection(read_only=True) as connection:
                stored = connection.execute('SELECT size_bytes FROM objects WHERE digest=?', (effect['evidence_object'],)).fetchone()
            if stored is None or stored[0] > 65_536:
                raise LaneError('JOB_EFFECT_EVIDENCE_BUDGET', 'The confirmation requires a registered bounded evidence object.')
            self.queue.store.read_object(effect['evidence_object'])
            return {'outcome': 'confirmed', 'basis': 'recorded_execution_confirmation',
                    'prior_evidence_object': effect['evidence_object'], 'source_currentness': 'not_rechecked'}
        if route == 'git_readback':
            from .git_job_recovery import observe_git_effect
            return observe_git_effect(self.project, context, job, effect, deadline=deadline)
        recipe = LOCAL_PREPARATIONS.get(job['action'])
        if recipe is None:
            raise LaneError('JOB_EFFECT_OBSERVATION_REQUIRED',
                'This unconfirmed effect needs its owning provider observation; it remains uncertain.')
        with self.project.lane('receipts').connection(read_only=True) as receipts:
            rows = receipts.execute("SELECT receipt_id,body_json FROM receipts WHERE kind=? "
                "AND json_extract(body_json,'$.effect_id')=? LIMIT 2", (recipe[0], effect['effect_id'])).fetchall()
        if len(rows) != 1 or len(rows[0]['body_json'].encode()) > 65_536:
            raise LaneError('JOB_EFFECT_PREPARATION_REQUIRED', 'Use the unique bounded preparation for this exact effect.')
        receipt = rows[0]
        body = json.loads(receipt['body_json'])
        before, after, relative = body.get('before_sha256'), body.get('after_sha256'), body.get(recipe[1])
        if (not isinstance(after, str) or not re.fullmatch(DIGEST, after)
                or (before is not None and (not isinstance(before, str) or not re.fullmatch(DIGEST, before)))
                or before == after or not isinstance(relative, str) or not relative or len(relative) > 1000
                or body.get('job_id', job['job_id']) != job['job_id']):
            raise LaneError('JOB_EFFECT_PREPARATION_INVALID', 'The preparation lacks exact before/after source identities.')
        selected = PurePosixPath(relative.replace('\\', '/'))
        if selected.is_absolute() or any(part in {'.', '..'} or ':' in part for part in selected.parts):
            raise LaneError('JOB_EFFECT_PATH_INVALID', 'Recovery reads only a relative path in the selected source root.')
        path = self.project.source_root.joinpath(*selected.parts)
        arguments = json.loads(job['request_json'])['arguments']
        filename = arguments.get('filename')
        if not isinstance(filename, str) or not filename or arguments.get('expected_sha256') != before:
            raise LaneError('JOB_EFFECT_REQUEST_MISMATCH', 'The preparation differs from the admitted file operation.')
        admitted = Path(filename)
        if not admitted.is_absolute():
            admitted = self.project.source_root / admitted
        if os.path.normcase(os.path.abspath(admitted)) != os.path.normcase(os.path.abspath(path)):
            raise LaneError('JOB_EFFECT_REQUEST_MISMATCH', 'The preparation path differs from the admitted file operation.')
        reject_links(path, self.project.source_root)
        ProjectAccess(self.project).authorize(context.client_id, 'read', path=path)
        try:
            identity = bounded_file_identity(path, root=self.project.source_root,
                budget=IOBudget(max_file_bytes=maximum, max_file_count=1, max_aggregate_bytes=maximum))
            observed = identity['sha256'].lower()
        except FileNotFoundError:
            observed, identity = None, {'exists': False}
        if observed == after:
            outcome = 'confirmed'
        elif observed == before:
            outcome = 'absent'
        else:
            raise LaneError('JOB_EFFECT_SOURCE_CONFLICT', 'The current source matches neither prepared outcome; preserve it and reconcile explicitly.')
        return {'outcome': outcome, 'basis': 'observed_local_preparation',
            'preparation_receipt_id': receipt['receipt_id'], 'preparation_digest': content_digest(body),
            'relative_path': selected.as_posix(), 'before_sha256': before, 'after_sha256': after,
            'observed_sha256': observed, 'identity': identity,
            'source_currentness': 'observed_during_reconciliation'}

    def reconcile(self, context, request):
        if context.expected_revision != request.plan_revision:
            raise LaneError('JOB_RECOVERY_REVISION_REQUIRED', 'Bind recovery to the exact current Plan revision.')
        with self.engine.project_work.mutation(self.project) as lease, lease.coordinated_transaction(['plan', 'receipts']):
            deadline = time.monotonic() + (25 if request.observation_route == 'git_readback' else 10)
            with bounded_project_read(self.project.root, deadline, writer=lease), self.queue.store.connection(read_only=True) as connection:
                PlanStore._head(connection, request.plan_revision)
                job = self.queue._row(connection, request.job_id)
                row = connection.execute('SELECT * FROM jobs_effects WHERE job_id=? AND effect_id=?',
                                         (request.job_id, request.effect_id)).fetchone()
                if row is None:
                    raise LaneError('EFFECT_NOT_FOUND', 'This effect does not belong to the selected job.')
                if effect_digest(job, row) != request.expected_effect_digest:
                    raise LaneError('JOB_EFFECT_CHANGED', 'Read the current job and effect before recovery.')
                if job['state'] != 'uncertain':
                    raise LaneError('JOB_STATE_CHANGED', 'Only an uncertain job requires effect reconciliation.')
                observed = self._observe(context, job, row, request.max_file_bytes,
                    route=request.observation_route, deadline=deadline)
                # Repeat the actual observation before publication, without
                # holding any permission inferred from the first file read.
                if self._observe(context, job, row, request.max_file_bytes,
                        route=request.observation_route, deadline=deadline) != observed:
                    raise LaneError('JOB_EFFECT_SOURCE_CHANGED', 'The source changed during reconciliation.')
            evidence = self.queue.store.put_object(json_text({**observed, 'job_id': request.job_id,
                'effect_id': request.effect_id, 'expected_effect_digest': request.expected_effect_digest,
                'actor_id': context.client_id, 'plan_revision': request.plan_revision,
                'observed_at': now(), 'automatic_replay': False, 'job_success_inferred': False}).encode())
            self.queue.reconcile_effect(request.job_id, request.effect_id, observed['outcome'], evidence, lease)
            with lease.transaction('plan') as connection:
                updated = self.queue._row(connection, request.job_id)
                if updated['state'] == 'failed':
                    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_runs'").fetchone():
                        run = connection.execute('SELECT task_id,plan_revision FROM delta_runs WHERE job_id=?',
                                                 (request.job_id,)).fetchone()
                        connection.execute("UPDATE delta_runs SET state='blocked',error_code='RECONCILED_REPLAN_REQUIRED',updated_at=? WHERE job_id=?",
                                           (now(), request.job_id))
                        if run and run['plan_revision'] == request.plan_revision:
                            task = PlanStore._task(connection, request.plan_revision, run['task_id'])
                            if task['state'] == 'active':
                                PlanStore(self.project).transition(run['task_id'], 'blocked', lease,
                                    expected_revision=request.plan_revision, actor_id=context.client_id, transaction=connection)
                    if updated['checkpoint_object'] and connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone():
                        connection.execute("UPDATE steer_requests SET state='ready',updated_at=? WHERE state='checkpointing' AND checkpoint_object=?",
                                           (now(), updated['checkpoint_object']))
                return ReconciledJobEffect(project_id=self.project.project_id, job_id=request.job_id,
                    effect_id=request.effect_id, outcome=observed['outcome'], evidence_object=evidence, job_state=updated['state'])


def register_job_recovery_actions(engine):
    def inspect(context, request):
        return JobRecovery(engine, engine.directory.open(context.project_id)).inspect(context, request)

    def reconcile(context, request):
        return JobRecovery(engine, engine.directory.open(context.project_id, write=True)).reconcile(context, request)

    engine.registry.register(ActionSpec('job_recovery_inspect', 'Read exact job effect heads and available observation routes without replaying work.',
        JobRecoveryRead, JobRecoveryStatus, inspect, profile='plan', workflow='recover', queryable_in_delta=True, studio_read=True))
    engine.registry.register(ActionSpec('job_reconcile_effect', 'Reconcile one uncertain effect using committed confirmation, local prepared bytes or exact Git readback; require a fresh Plan afterward.',
        ReconcileJobEffect, ReconciledJobEffect, reconcile, profile='plan', workflow='recover', permission='admin', mutates=True,
        tool_routes=(ToolRoute('job_reconcile_effect.local_or_recorded', reconcile,
            argument_values=(('observation_route', ('local_or_recorded',)),)),
            ToolRoute('job_reconcile_effect.git_readback', reconcile, ('Python', 'Git'),
                argument_values=(('observation_route', ('git_readback',)),)))))
