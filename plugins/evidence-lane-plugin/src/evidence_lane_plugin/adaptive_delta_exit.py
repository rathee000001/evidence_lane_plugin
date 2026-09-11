"""Verified Delta exit and automatic dependency-ready Plan selection.

The original owner's evidence/validator binding and automatic Memory refresh
use a coordinated commit across the separate Plan, Learning, Memory and Receipt
lanes. No Formula, accepted-PV or install-HIL is used here.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import Field

from .agent_learning import LearningStore
from .errors import LaneError
from .plan_runtime import PlanStore
from .project_memory import ProjectMemory
from .registry import Contract
from .storage import ProjectStore, json_text, now


class VerificationCheck(Contract):
    check_id: str = Field(min_length=1, max_length=1000)
    passed: bool
    evidence: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class VerificationContext:
    store: ProjectStore
    task_id: str
    plan_revision: int
    requested_checks: tuple[str, ...]
    source_path: Callable
    worker_evidence: tuple[dict, ...]
    job_id: str | None = None
    check: Callable = lambda: None


class RecordedDeltaExit(Contract):
    receipt_id: str
    verification_object: str
    entry_object: str
    next_task_id: str | None
    checks: list[str]
    learning_version_id: str
    learning_state: str
    memory_refresh: str = 'historical_receipt_without_refresh_policy'
    memory_head: str | None = None
    evidence_scope: str = 'recorded_plan_receipts_learning_links'
    source_currentness: str = 'not_rechecked'
    result_bytes_checked: bool = False
    native_host_tools_attested: bool = False
    verification: dict | None = None


def read_recorded_exit(store, connection, row, job, *, include_verification=False, result=None):
    """Reconcile recorded completion inside the caller's pinned project read.

    This validates the selected evidence links, not the current source tree or
    an installed host. Large operation input/output remains separately addressed.
    """
    def require(condition):
        if not condition:
            raise LaneError('DELTA_EXIT_INTEGRITY', 'The recorded completion evidence does not agree.')

    table = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='delta_exits'").fetchone()
    exit_row = connection.execute('SELECT * FROM delta_exits WHERE job_id=?', (row['job_id'],)).fetchone() if table else None
    if row['state'] != 'verified':
        require(exit_row is None and job['state'] != 'succeeded')
        return None
    require(exit_row is not None and job['state'] == 'succeeded' and row['error_code'] is None)
    task = PlanStore._view(PlanStore._task(connection, row['plan_revision'], row['task_id']), connection=connection)
    require(task.state == 'completed' and task.contract_digest == row['contract_digest']
            and job['plan_revision'] == row['plan_revision'] and job['action'] in task.definition.allowed_actions)
    plan = store.lane('plan')

    def object_json(lane, digest, limit):
        path = lane.object_path(digest)
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            raise LaneError('OBJECT_MISSING', 'The addressed completion evidence is missing.') from None
        if size > limit:
            raise LaneError('DELTA_EXIT_READ_BUDGET', 'The recorded completion evidence exceeds its read budget.')
        raw = lane.read_object(digest)
        if len(raw) > limit:
            raise LaneError('DELTA_EXIT_READ_BUDGET', 'The recorded completion evidence exceeds its read budget.')
        value = json.loads(raw)
        require(isinstance(value, dict))
        return value

    with store.lane('receipts').connection(read_only=True) as receipts:
        size = receipts.execute('SELECT length(CAST(body_json AS BLOB)) FROM receipts WHERE receipt_id=? AND kind=?',
            (exit_row['receipt_id'], 'delta_exit_verified')).fetchone()
        require(size is not None)
        if size[0] > 262_144:
            raise LaneError('DELTA_EXIT_READ_BUDGET', 'The recorded completion receipt exceeds its read budget.')
        receipt = json.loads(receipts.execute('SELECT body_json FROM receipts WHERE receipt_id=?',
            (exit_row['receipt_id'],)).fetchone()[0])
        selected = receipts.execute("SELECT body_json FROM receipts WHERE kind='delta_next_selected' "
            "AND json_extract(body_json,'$.after_job_id')=? LIMIT 2", (row['job_id'],)).fetchall()
        require(len(selected) == 1 and len(selected[0][0].encode()) <= 65_536)
        successor = json.loads(selected[0][0])
        require(successor.get('plan_revision') == row['plan_revision']
            and successor.get('next_task_id') == exit_row['next_task_id']
            and successor.get('automatic_execution') is False and successor.get('native_goal_mutated') is False)
    require(isinstance(receipt, dict))
    verification = object_json(plan, exit_row['verification_object'], 262_144)
    expected = {key: row[key] for key in ('job_id', 'task_id', 'plan_revision', 'contract_digest', 'result_object')}
    expected['execution_id'] = job['execution_id']
    require(all(receipt.get(key) == value and verification.get(key) == value for key, value in expected.items()))
    require(receipt.get('project_id') == store.project_id and receipt.get('status') == 'passed'
        and receipt.get('entry_object') == row['entry_object']
        and receipt.get('verification_object') == exit_row['verification_object']
        and receipt.get('owner_fence') == job['owner_fence']
        and receipt.get('engine_instance') == verification.get('engine_instance')
        and bool(receipt.get('engine_instance'))
        and receipt.get('task_mode') == verification.get('task_mode')
        and receipt.get('native_host_tools_attested') is False
        and verification.get('native_host_tools_attested') is False
        and verification.get('verification_basis') == 'registered_profile_checks')
    checks = verification.get('checks')
    require(receipt.get('memory_refresh_policy') == verification.get('memory_refresh_policy'))
    require(isinstance(checks, list) and len(checks) == len(task.definition.acceptance_checks))
    require(all(isinstance(check, dict) and isinstance(check.get('check_id'), str)
                and check.get('passed') is True for check in checks))
    names = [check['check_id'] for check in checks]
    require(set(names) == set(task.definition.acceptance_checks) and receipt.get('checks') == names)
    if task.definition.validation_policy is not None:
        from .acceptance import verify_recorded_validation
        require(receipt.get('project_validation_summary_object') == verification.get('project_validation_summary_object'))
        verify_recorded_validation(store, connection, task.definition, verification)
    require(job.get('result') == {'result_object': row['result_object'],
        'verification_object': exit_row['verification_object'], 'receipt_id': exit_row['receipt_id']})
    if result is not None:
        require(isinstance(result, dict) and all(result.get(key) == value for key, value in expected.items()
            if key != 'result_object') and result.get('engine_instance') == verification['engine_instance']
            and result.get('task_mode') == verification.get('task_mode'))
    with store.lane('learning').connection(read_only=True) as learning:
        require(learning.execute("SELECT 1 FROM sqlite_schema WHERE name='learning_versions'").fetchone() is not None)
        learned = learning.execute('SELECT version_id,state,source_receipt_id,content_digest FROM learning_versions WHERE source_job_id=?',
            (row['job_id'],)).fetchall()
    require(len(learned) == 1 and learned[0]['source_receipt_id'] == exit_row['receipt_id'])
    observation = object_json(store.lane('learning'), learned[0]['content_digest'], 65_536)
    require(all(observation.get(key) == value for key, value in {
        'project_id': store.project_id, 'source_job_id': row['job_id'], 'source_task_id': row['task_id'],
        'plan_revision': row['plan_revision'], 'verification_object': exit_row['verification_object'],
        'result_object': row['result_object'], 'engine_instance': verification['engine_instance']}.items()))
    memory_head = None
    if 'memory_refresh_policy' in receipt:
        require(receipt['memory_refresh_policy'] == 'verified_exit_memory_v1')
        memory_head = ProjectMemory(store).read_verified_exit(row['job_id'], exit_row['receipt_id']).head
    return RecordedDeltaExit(receipt_id=exit_row['receipt_id'], verification_object=exit_row['verification_object'],
        entry_object=row['entry_object'], next_task_id=exit_row['next_task_id'], checks=names,
        learning_version_id=learned[0]['version_id'], learning_state=learned[0]['state'],
        memory_head=memory_head, memory_refresh='verified_exit_memory_v1' if memory_head else 'historical_receipt_without_refresh_policy',
        result_bytes_checked=result is not None, verification=verification if include_verification else None)


class DeltaExit:
    def __init__(self, engine):
        self.engine = engine

    def finish(self, execution, request, context, result_object):
        store, guard = execution.store, execution.guard
        spec = self.engine.registry.get(request.action)
        if guard is None or spec.verifier is None:
            raise LaneError('DELTA_VERIFIER_UNAVAILABLE', 'The exact registered verifier is required.')
        with execution.slot.mutex:
            execution._before_more_work()
            execution.worker_results()
            if execution._worker_failed:
                raise LaneError('WORKER_OPERATION_FAILED', 'A required operation failed before exit.')
            guard.observe(execution)
            view = PlanStore(store).task(request.task_id, expected_revision=request.plan_revision)
            if view.state != 'active' or view.contract_digest != request.contract_digest:
                raise LaneError('DELTA_EXIT_BINDING_CHANGED', 'The exact active task contract is required at exit.')
        plan_store = store.lane('plan')
        result = json.loads(plan_store.read_object(result_object))
        bindings = {'job_id': execution.claim.job_id, 'task_id': request.task_id,
                    'plan_revision': request.plan_revision, 'contract_digest': request.contract_digest,
                    'execution_id': execution.claim.execution_id, 'engine_instance': self.engine.instance_id,
                    'task_mode': guard.mode_validation}
        if any(result.get(key) != value for key, value in bindings.items()):
            raise LaneError('DELTA_RESULT_BINDING_MISMATCH', 'The addressed result belongs to another execution.')
        typed_input = spec.input_model.model_validate(request.arguments)
        typed_output = spec.output_model.model_validate(result['result'])
        verifier_context = VerificationContext(ProjectStore(store.root, read_only=True), request.task_id,
            request.plan_revision, tuple(view.definition.acceptance_checks), guard.path, tuple(guard.worker_evidence),
            job_id=execution.claim.job_id, check=guard.check)
        guard.spend_call()
        # Check functions are registered engine code. They compute observations
        # from bound data; no caller supplies a success flag or executable code.
        checks = [VerificationCheck.model_validate(value) for value in spec.verifier(verifier_context, typed_input, typed_output)]
        if (len(checks) != len(verifier_context.requested_checks)
                or {check.check_id for check in checks} != set(verifier_context.requested_checks)):
            raise LaneError('DELTA_CHECK_SET_MISMATCH', 'The verifier must return exactly the task acceptance checks.')
        checks_body = [check.model_dump(mode='json') for check in checks]
        if len(json_text(checks_body).encode()) > 65_536:
            raise LaneError('DELTA_VERIFICATION_BUDGET', 'Use bounded check observations with addressed large evidence.')
        guard.output(checks_body)
        profile_passed = all(check.passed for check in checks)
        validation_passed = True
        validation_fields = {}
        if guard.validation is not None:
            validation_passed = guard.validation.finish(profile_passed=profile_passed)
            validation_fields['project_validation_summary_object'] = guard.validation.recorded_object
        verification = {**bindings, 'result_object': result_object, 'checks': checks_body,
                        'usage': guard.usage(), 'worker_evidence': guard.worker_evidence,
                        'verification_basis': 'registered_profile_checks', 'native_host_tools_attested': False,
                        'memory_refresh_policy': 'verified_exit_memory_v1', **validation_fields}
        learning = LearningStore(store)
        with execution.slot.mutex:
            execution._before_more_work()
            if not profile_passed or not validation_passed:
                with execution.lease.transaction('plan') as connection:
                    verification_object = plan_store.put_object(json_text(verification).encode(), limit=262_144)
                    store.append_receipt('delta_verification_failed', {**bindings, 'verification_object': verification_object,
                        'result_object': result_object, 'status': 'failed'}, connection=connection)
                    connection.execute('UPDATE delta_runs SET result_object=? WHERE job_id=?',
                                       (result_object, execution.claim.job_id))
                raise LaneError('DELTA_ACCEPTANCE_FAILED', 'A required profile acceptance check failed.')
            if guard.validation is not None:
                from .acceptance import verify_recorded_validation
                # The policy reader pins a published snapshot. Reconcile the
                # immutable summary before opening the final write transaction;
                # the project mutex and writer remain held across both stages.
                with plan_store.connection(read_only=True) as connection:
                    verify_recorded_validation(store, connection, view.definition, verification)
            with execution.lease.coordinated_transaction(['plan', 'learning', 'memory']) as commit:
                connection = commit.connection('plan')
                verification_object = plan_store.put_object(json_text(verification).encode(), limit=262_144)
                prepared_learning = learning.prepare(view.definition, spec, execution, verification_object, result_object)
                execution._current(connection)
                if execution._steers(connection) or self.engine.project_work.paused(connection):
                    raise LaneError('JOB_CHECKPOINT_REQUIRED', 'Checkpoint the pending stop or steer before completing.')
                row = connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (execution.claim.job_id,)).fetchone()
                if row is None or row['state'] != 'running' or row['contract_digest'] != request.contract_digest:
                    raise LaneError('DELTA_EXIT_BINDING_CHANGED', 'The current run must match the exact result contract.')
                if execution.queue._pending_effect(connection, execution.claim.job_id):
                    raise LaneError('EFFECT_OUTCOME_UNCERTAIN', 'Confirm every prepared effect before verified exit.')
                body = {**bindings, 'project_id': store.project_id, 'status': 'passed',
                        'verification_object': verification_object, 'result_object': result_object,
                        'entry_object': row['entry_object'], 'owner_fence': execution.claim.owner_fence,
                        'worker_generation': self.engine.workers.generation if self.engine.workers else None,
                        'checks': [check.check_id for check in checks], 'native_host_tools_attested': False,
                        'memory_refresh_policy': 'verified_exit_memory_v1', **validation_fields}
                receipt = store.append_receipt('delta_exit_verified', body, connection=connection)
                execution.queue.complete(execution.claim.job_id, {'result_object': result_object,
                    'verification_object': verification_object, 'receipt_id': receipt}, execution.lease,
                    execution_id=execution.claim.execution_id, transaction=connection)
                plan = PlanStore(store)
                plan.transition(request.task_id, 'completed', execution.lease, expected_revision=request.plan_revision,
                                actor_id=context.client_id, evidence_receipt=receipt, transaction=connection)
                next_row = connection.execute("SELECT t.task_id FROM plan_tasks t WHERE t.revision=? AND t.state='queued' "
                    "AND NOT EXISTS (SELECT 1 FROM plan_dependencies d JOIN plan_tasks p "
                    "ON p.revision=d.revision AND p.task_id=d.dependency_id "
                    "WHERE d.revision=t.revision AND d.task_id=t.task_id AND p.state!='completed') "
                    "ORDER BY t.position LIMIT 1", (request.plan_revision,)).fetchone()
                next_id = next_row['task_id'] if next_row else None
                if next_id:
                    plan.transition(next_id, 'active', execution.lease, expected_revision=request.plan_revision,
                                    actor_id='engine_verified_exit', transaction=connection)
                connection.execute("UPDATE delta_runs SET state='verified',result_object=?,error_code=NULL,updated_at=? WHERE job_id=?",
                                   (result_object, now(), execution.claim.job_id))
                connection.execute('INSERT INTO delta_exits VALUES(?,?,?,?,?)',
                    (execution.claim.job_id, verification_object, receipt, next_id, now()))
                learning.record_exit(prepared_learning, receipt, execution.lease, transaction=commit.connection('learning'))
                ProjectMemory(store).record_verified_exit(execution.claim.job_id, receipt, execution.lease,
                    transaction=commit.connection('memory'))
                store.append_receipt('delta_next_selected', {'after_job_id': execution.claim.job_id,
                    'plan_revision': request.plan_revision, 'next_task_id': next_id,
                    'automatic_execution': False, 'native_goal_mutated': False}, connection=connection)
            projection = plan.host_projection()
            plan._publish_if_bound(projection.projection_id, execution.lease, actor_id=context.client_id)
        return {'receipt_id': receipt, 'verification_object': verification_object, 'next_task_id': next_id}
