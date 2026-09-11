"""Task-bound project checks, measured change selection and completion gates."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest
from evidence_lane_plugin.acceptance import (
    TaskValidationBinding,
    ValidationCommand,
    ValidationPolicy,
    ValidationPolicySet,
    ValidationPolicyStore,
    ValidationRule,
    ValidationSelector,
    validation_worker_operations,
)
from evidence_lane_plugin.bounded_io import run_owned_bounded_process
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskBudget, TaskDefinition
from evidence_lane_plugin.registry import ActionRegistry, ActionSpec, Contract
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.workers import WorkerPool
from pydantic import Field

from tests.test_delta_entry import bind_fixture_flash, call, finished


@pytest.mark.skipif(os.name != 'nt', reason='Windows redirector and Job Object regression.')
def test_owned_bootstrap_waits_for_assignment_before_redirector_descendants(tmp_path, monkeypatch):
    import evidence_lane_plugin.process_ownership as owner

    actual_kernel = owner.kernel

    class DelayedAssignment:
        def __getattr__(self, name):
            return getattr(actual_kernel(), name)

        def AssignProcessToJobObject(self, *args):
            # Expose the launch/assignment window deterministically. The
            # bootstrap must wait for its pipe before it may spawn a command.
            time.sleep(0.3)
            return actual_kernel().AssignProcessToJobObject(*args)

    monkeypatch.setattr(owner, 'kernel', lambda: DelayedAssignment())
    marker = tmp_path / 'late-command-write'
    program = 'import pathlib,time\ntime.sleep(1)\npathlib.Path(' + repr(str(marker)) + ').write_text("late")\ntime.sleep(1)'
    with pytest.raises(LaneError) as error:
        run_owned_bounded_process([sys.executable, '-I', '-S', '-c', program], cwd=tmp_path, timeout_seconds=0.2)
    assert error.value.code == 'BOUNDED_PROCESS_TIMEOUT'
    time.sleep(1.2)
    assert not marker.exists(), 'A command escaped the owned timeout through the venv redirector.'


class MutationInput(Contract):
    text: str = Field(default='new', max_length=100)
    behavior: str = Field(default='write', pattern=r'^(write|delete|noop)$')
    corrupt_result: bool = False


class MutationOutput(Contract):
    exists: bool
    content: str


def mutate(context, arguments):
    execution = context.execution
    path = execution.guard.path('input.txt')
    if arguments.behavior != 'noop':
        effect = execution.prepare_effect('fixture-input', 'Apply the bounded test input change')
        if arguments.behavior == 'delete':
            path.unlink()
        else:
            path.write_text(arguments.text)
        with execution.lease.transaction('plan'):
            evidence = execution.store.lane('plan').put_object(path.read_bytes() if path.exists() else b'deleted', limit=1024)
        execution.confirm_effect(effect, evidence)
    return {'exists': path.exists(), 'content': 'wrong' if arguments.corrupt_result else path.read_text() if path.exists() else ''}


def verify_mutation(context, arguments, output):
    path = context.source_path('input.txt')
    return [{'check_id': 'input_matches_result', 'passed': output.exists == path.exists()
        and output.content == (path.read_text() if path.exists() else ''), 'evidence': {'path': 'input.txt'}}]


@pytest.fixture
def delta_system(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.txt').write_text('old')
    (source / 'verify.py').write_text('from pathlib import Path\nassert Path("input.txt").read_text() == "new"\nprint("checked")\n')
    (source / 'after.py').write_text('from pathlib import Path\nPath("second-ran").write_text("yes")\n')
    registry = ActionRegistry()
    registry.register(ActionSpec('fixture_mutate', 'Change one bounded fixture input.', MutationInput, MutationOutput,
        mutate, profile='fixture', permission='write', mutates=True, requires_delta=True,
        verifier=verify_mutation, verification_checks=('input_matches_result',)))
    with Engine(tmp_path / 'runtime', registry=registry,
                worker_pool=WorkerPool(validation_worker_operations(), workers=1)) as engine:
        bind_fixture_flash(engine, tmp_path, monkeypatch)
        row = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(row['project_id'], write=True)
        token, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(project_id=store.project_id,
            permissions=['read', 'write'])]))
        yield engine, store, token, session


def check(check_id='project_check', script='verify.py', paths=None, **command_overrides):
    command = {'tool_id': 'Python', 'executable': sys.executable,
        'executable_sha256': hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
        'arguments': ['-I', '-S', '-B', script], 'timeout_seconds': 4, 'max_output_bytes': 2048}
    command.update(command_overrides)
    return ValidationRule(check_id=check_id, title=check_id, command=ValidationCommand.model_validate(command),
        when=ValidationSelector(paths=paths or ['input.txt'], profiles=['fixture'], actions=['fixture_mutate']))


def configure(system, rules):
    engine, store, _, _ = system
    with engine.project_work.mutation(store) as writer:
        return ValidationPolicyStore(store).set(ValidationPolicySet(expected_revision=0,
            policy=ValidationPolicy(title='Fixture checks', checks=rules)), writer, actor_id='fixture')


def task(system, policy, *, bound=True, binding_changes=None, task_changes=None):
    engine, store, _, _ = system
    binding = TaskValidationBinding.model_validate({'policy_revision': policy.revision, 'policy_digest': policy.revision_digest,
        'input_paths': ['input.txt', 'verify.py', 'after.py'], **(binding_changes or {})}) if bound else None
    arguments = {'task_id': 'first', 'title': 'Change and verify selected source', 'requested_outcome': 'Verified changed input',
        'profile': 'fixture', 'permitted_paths': ['.'], 'permitted_tools': ['Python'],
        'allowed_actions': ['fixture_mutate'], 'acceptance_checks': ['input_matches_result'],
        'budget': TaskBudget(max_seconds=60, max_tool_calls=32), 'validation_policy': binding}
    arguments.update(task_changes or {})
    first = TaskDefinition.model_validate(arguments)
    second = TaskDefinition(task_id='second', title='Next task', requested_outcome='Continue after verification',
        profile='fixture', permitted_paths=['.'], permitted_tools=['Python'], allowed_actions=['fixture_mutate'],
        acceptance_checks=['input_matches_result'])
    with engine.project_work.mutation(store) as writer:
        PlanStore(store).create(PlanCreate(title='Measured project CI', tasks=[first, second]), writer, actor_id='fixture')
    return PlanStore(store).task('first', expected_revision=1)


def enter(system, view, **arguments):
    return call(system, ActionRequest(action='delta_enter', project_id=system[1].project_id, expected_revision=1,
        arguments={'task_id': 'first', 'plan_revision': 1, 'contract_digest': view.contract_digest,
            'action': 'fixture_mutate', 'arguments': arguments}))


def status(system, job_id, *, details=True):
    response = call(system, ActionRequest(action='delta_status', project_id=system[1].project_id,
        arguments={'job_id': job_id, 'include_verification': details}))
    assert response.status == 'ok', response.error
    return response.result


def test_changed_input_selects_only_matching_checks_and_pins_recorded_result(delta_system):
    engine, store, _, _ = delta_system
    policy = configure(delta_system, [check(), check('unrelated', 'after.py', ['unrelated/**'])])
    view = task(delta_system, policy)
    entered = enter(delta_system, view)
    assert entered.status == 'queued', entered.error
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'verified', result
    observed = status(delta_system, entered.job_id)
    summary = observed['validation']['summary']
    assert summary['status'] == 'passed' and summary['changed_counts'] == {'added': 0, 'deleted': 0, 'modified': 1}
    assert summary['selected_checks'] == ['project_check']
    assert summary['results'][0]['returncode'] == 0 and 'checked' in summary['results'][0]['output_preview']
    assert summary['inputs_unchanged_during_checks'] is True and not summary['delta_completion_claimed']
    assert engine.workers.status()['submitted'] == 1
    assert not (store.source_root / 'second-ran').exists()
    assert [row.state for row in PlanStore(store).snapshot().tasks] == ['completed', 'active']
    assert observed['exit']['verification']['project_validation_summary_object'] == observed['validation']['summary_object']
    assert 'output_preview' not in status(delta_system, entered.job_id, details=False)['validation']['summary']['results'][0]
    (store.source_root / 'input.txt').write_text('later independent change')
    historical = status(delta_system, entered.job_id)
    assert historical['exit']['source_currentness'] == 'not_rechecked'
    assert historical['validation'] == observed['validation']
    assert engine.workers.status()['submitted'] == 1


@pytest.mark.parametrize('outcome', ['failing', 'mutating', 'timeout', 'overflow'])
def test_failed_checks_or_changed_inputs_never_complete_task(delta_system, outcome):
    _, store, _, _ = delta_system
    programs = {'failing': 'raise SystemExit(7)',
        'mutating': 'from pathlib import Path\nPath("input.txt").write_text("changed by CI")',
        'timeout': 'import time\ntime.sleep(4)', 'overflow': 'print("x" * 10000)'}
    (store.source_root / 'verify.py').write_text(programs[outcome])
    policy = configure(delta_system, [check(timeout_seconds=1), check('must_not_run', 'after.py')])
    entered = enter(delta_system, task(delta_system, policy))
    assert entered.status == 'queued', entered.error
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'blocked' and result['error_code'] == 'DELTA_ACCEPTANCE_FAILED', result
    observed = status(delta_system, entered.job_id)
    summary = observed['validation']['summary']
    assert summary['status'] == 'failed' and observed['exit'] is None
    assert [row.state for row in PlanStore(store).snapshot().tasks] == ['blocked', 'queued']
    if outcome == 'mutating':
        assert summary['inputs_unchanged_during_checks'] is False
        assert (store.source_root / 'input.txt').read_text() == 'changed by CI'
        assert summary['results'][1]['status'] == 'not_run_after_failure'
        assert not (store.source_root / 'second-ran').exists()
    else:
        assert summary['results'][0]['status'] == {'failing': 'failed', 'timeout': 'timeout', 'overflow': 'output_limit'}[outcome]
        assert summary['results'][1]['status'] == 'not_run_after_failure'
        assert not (store.source_root / 'second-ran').exists()


@pytest.mark.parametrize('behavior,force,expected,submitted', [
    ('noop', False, 'no_changes', 0), ('noop', True, 'passed', 1), ('delete', False, 'passed', 1)])
def test_deleted_inputs_and_explicit_unchanged_verification(delta_system, behavior, force, expected, submitted):
    engine, store, _, _ = delta_system
    (store.source_root / 'verify.py').write_text('print("verification executed")')
    policy = configure(delta_system, [check()])
    view = task(delta_system, policy, binding_changes={'run_all_checks': force})
    entered = enter(delta_system, view, behavior=behavior)
    assert entered.status == 'queued', entered.error
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'verified', result
    summary = status(delta_system, entered.job_id)['validation']['summary']
    assert summary['status'] == expected
    assert summary['changed_counts']['deleted'] == int(behavior == 'delete')
    assert engine.workers.status()['submitted'] == submitted


def test_saved_policy_is_not_activated_on_an_unbound_task(delta_system):
    engine, store, _, _ = delta_system
    (store.source_root / 'verify.py').write_text('raise SystemExit(9)')
    policy = configure(delta_system, [check()])
    view = task(delta_system, policy, bound=False)
    assert 'validation_policy' not in view.definition.model_dump(mode='json')
    entered = enter(delta_system, view)
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'verified', result
    assert status(delta_system, entered.job_id)['validation'] is None
    assert engine.workers.status()['submitted'] == 0


@pytest.mark.parametrize('problem', ['tool_scope', 'executor_hash'])
def test_missing_permission_or_stale_executor_rejects_before_source_effect(delta_system, problem):
    engine, store, _, _ = delta_system
    policy = configure(delta_system, [check(**({'executable_sha256': '0' * 64} if problem == 'executor_hash' else {}))])
    view = task(delta_system, policy, task_changes={'permitted_tools': []} if problem == 'tool_scope' else None)
    response = enter(delta_system, view)
    assert response.status == 'error'
    assert response.error.code == {'tool_scope': 'VALIDATION_TOOL_SCOPE', 'executor_hash': 'VALIDATION_EXECUTOR_CHANGED'}[problem]
    assert (store.source_root / 'input.txt').read_text() == 'old'
    assert engine.workers.status()['submitted'] == 0


def test_policy_update_does_not_switch_a_sealed_task(delta_system):
    engine, store, _, _ = delta_system
    original = configure(delta_system, [check()])
    view = task(delta_system, original)
    with engine.project_work.mutation(store) as writer:
        changed = ValidationPolicyStore(store).set(ValidationPolicySet(expected_revision=1,
            expected_digest=original.revision_digest, policy=ValidationPolicy(title='Later policy', checks=[])),
            writer, actor_id='fixture')
    assert changed.revision == 2
    assert PlanStore(store).task('first', expected_revision=1).contract_digest == view.contract_digest
    entered = enter(delta_system, view)
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'verified', result
    summary = status(delta_system, entered.job_id)['validation']['summary']
    assert summary['policy_revision'] == 1 and summary['selected_checks'] == ['project_check']


def test_stop_during_check_preserves_observed_ci_without_completing_delta(delta_system):
    _, store, _, _ = delta_system
    (store.source_root / 'verify.py').write_text('from pathlib import Path\nimport time\n'
        'Path("started").write_text("yes")\n'
        'while not Path("release").exists():\n time.sleep(.02)\nprint("finished")\n')
    policy = configure(delta_system, [check(timeout_seconds=8)])
    entered = enter(delta_system, task(delta_system, policy))
    assert entered.status == 'queued', entered.error
    deadline = time.monotonic() + 8
    while not (store.source_root / 'started').exists():
        assert time.monotonic() < deadline
        time.sleep(.02)
    source = call(delta_system, ActionRequest(action='lineage_record', project_id=store.project_id,
        arguments={'kind': 'prompt', 'payload': {'text': 'Stop this source change now.'}}))
    stopped = call(delta_system, ActionRequest(action='steer_submit', project_id=store.project_id,
        arguments={'source_event_id': source.result['event_id'], 'source_cursor': source.result['cursor'],
            'expected_revision': 1, 'intent': 'stop', 'rationale': 'Stop while a check is running', 'affected_task_ids': ['first']}))
    assert stopped.status == 'ok', stopped.error
    (store.source_root / 'release').write_text('yes')
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'blocked' and result['error_code'] == 'JOB_CHECKPOINT_REQUIRED', result
    observed = status(delta_system, entered.job_id)
    assert observed['exit'] is None and observed['validation']['summary']['delta_completion_claimed'] is False
    assert PlanStore(store).snapshot().tasks[1].state == 'queued'


def test_owning_profile_failure_prevents_ci_and_completion(delta_system):
    engine, _, _, _ = delta_system
    policy = configure(delta_system, [check()])
    entered = enter(delta_system, task(delta_system, policy), corrupt_result=True)
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'blocked' and result['error_code'] == 'DELTA_ACCEPTANCE_FAILED', result
    summary = status(delta_system, entered.job_id)['validation']['summary']
    assert summary['status'] == 'not_run_profile_failed'
    assert summary['results'][0]['status'] == 'not_run_after_failure'
    assert engine.workers.status()['submitted'] == 0


def test_input_budget_blocks_before_operation_and_records_summary(delta_system):
    engine, store, _, _ = delta_system
    policy = configure(delta_system, [check()])
    entered = enter(delta_system, task(delta_system, policy, binding_changes={'max_file_bytes': 1}))
    assert entered.status == 'queued', entered.error
    result = finished(delta_system, entered.job_id)
    assert result['state'] == 'blocked' and result['error_code'] == 'BOUNDED_IO_FILE_BYTES_EXCEEDED', result
    summary = status(delta_system, entered.job_id)['validation']['summary']
    assert summary['status'] == 'blocked' and summary['before_object'] is None
    assert (store.source_root / 'input.txt').read_text() == 'old'
    assert engine.workers.status()['submitted'] == 0


def test_sealed_summary_cannot_drop_a_required_check(delta_system):
    from evidence_lane_plugin.acceptance import digest, verify_recorded_validation
    from evidence_lane_plugin.storage import json_text

    engine, store, _, _ = delta_system
    policy = configure(delta_system, [check()])
    entered = enter(delta_system, task(delta_system, policy))
    assert finished(delta_system, entered.job_id)['state'] == 'verified'
    observed = status(delta_system, entered.job_id)
    original = observed['validation']['summary']
    changed = {**original, 'selected_checks': [], 'results': [], 'status': 'no_changes'}
    with engine.project_work.mutation(store) as writer, writer.coordinated_transaction(['plan', 'receipts']) as commit:
        connection = commit.connection('plan')
        receipts = commit.connection('receipts')
        altered = store.lane('plan').put_object(json_text(changed).encode(), limit=131072)
        row = connection.execute('SELECT receipt_id FROM validationrun_summaries WHERE job_id=?', (entered.job_id,)).fetchone()
        connection.execute('UPDATE validationrun_summaries SET summary_object=? WHERE job_id=?', (altered, entered.job_id))
        body = json.loads(receipts.execute('SELECT body_json FROM receipts WHERE receipt_id=?', (row['receipt_id'],)).fetchone()[0])
        body.update(summary_object=altered, status='no_changes')
        # This adversarial fixture tampers with stored evidence; the normal SDK
        # exposes no route to rewrite it. Even coherent local hashes must not
        # let the altered summary satisfy the already recorded Delta exit.
        receipts.execute('UPDATE receipts SET body_json=? WHERE receipt_id=?', (json_text(body), row['receipt_id']))
    assert digest(changed) == altered
    with store.lane('plan').connection(read_only=True) as connection, pytest.raises(LaneError, match='selected checks differ'):
        verify_recorded_validation(store, connection, PlanStore(store).task('first', expected_revision=1).definition,
            {**observed['exit']['verification'], 'project_validation_summary_object': altered})
    response = call(delta_system, ActionRequest(action='delta_status', project_id=store.project_id,
        arguments={'job_id': entered.job_id, 'include_verification': True}))
    assert response.status == 'error' and response.error.code == 'VALIDATION_SUMMARY_INTEGRITY'


def test_current_packaged_stdio_code_operation_runs_task_bound_ci(tmp_path):
    from evidence_lane_plugin.code_workers import code_worker_operations
    from evidence_lane_plugin.local_transport import LocalEndpoint

    from tests.test_native_workflow_bindings import native

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.py').write_text('def answer():\n    return 42\n')
    (source / 'check.py').write_text('from pathlib import Path\nassert "return 42" in Path("app.py").read_text()\nprint("source check passed")\n')
    original = {path.name: path.read_bytes() for path in source.iterdir()}
    operations = (*code_worker_operations(), *validation_worker_operations())
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(operations, workers=1)) as engine, LocalEndpoint(engine):
        record = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        project_id = record['project_id']

        async def exercise():
            async with native(engine.root, project_id, permissions=('read', 'write'), entrypoint='package') as session:
                async def invoke(action, arguments=None, **envelope):
                    result = await session.call_tool(action, {'project_id': project_id, 'arguments': arguments or {}, **envelope})
                    assert result.structuredContent is not None
                    return result.structuredContent

                catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
                assert 'validation_policy' in json.dumps(catalog['plan_create'].inputSchema)
                rule = check(script='check.py').model_dump(mode='json')
                rule['when'] = {'paths': ['app.py'], 'profiles': ['code'], 'actions': ['code_index']}
                saved = await invoke('validation_policy_set', {'expected_revision': 0,
                    'policy': {'title': 'Actual Code fixture checks', 'checks': [rule]}})
                assert saved['status'] == 'ok', saved
                policy = saved['result']
                definition = TaskDefinition(task_id='index', title='Index and verify one source file',
                    requested_outcome='Verified Code snapshot and explicitly requested source checks', profile='code',
                    permitted_paths=['.'], permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser'],
                    allowed_actions=['code_index'], acceptance_checks=['code_snapshot_integrity', 'code_source_hashes_unchanged'],
                    budget=TaskBudget(max_seconds=90, max_tool_calls=32),
                    validation_policy=TaskValidationBinding(policy_revision=policy['revision'], policy_digest=policy['revision_digest'],
                        input_paths=['app.py', 'check.py'], run_all_checks=True),
                    operation={'action': 'code_index', 'arguments': {'paths': ['app.py']}})
                created = await invoke('plan_create', PlanCreate(title='Packaged stdio CI', tasks=[definition]).model_dump(mode='json'))
                assert created['status'] == 'ok', created
                selected = created['result']['tasks'][0]
                assert selected['definition']['validation_policy']['policy_digest'] == policy['revision_digest']
                admitted = await invoke('delta_enter_planned', {'task_id': 'index', 'plan_revision': 1,
                    'contract_digest': selected['contract_digest']}, expected_revision=1)
                assert admitted['status'] == 'queued', admitted
                deadline = time.monotonic() + 25
                while True:
                    result = await invoke('delta_status', {'job_id': admitted['job_id'], 'include_verification': True})
                    if result['status'] == 'error' and result['error']['code'] == 'PROJECT_RECOVERY_REQUIRED':
                        assert time.monotonic() < deadline
                        await asyncio.sleep(.05)
                        continue
                    assert result['status'] == 'ok', result
                    if result['result']['state'] in {'verified', 'blocked'}:
                        break
                    assert time.monotonic() < deadline
                    await asyncio.sleep(.05)
                observed = result['result']
                assert observed['state'] == 'verified', observed
                summary = observed['validation']['summary']
                assert summary['status'] == 'passed' and summary['action'] == 'code_index'
                assert summary['changed_counts'] == {'added': 0, 'modified': 0, 'deleted': 0}
                assert summary['selection_basis'] == 'explicit_run_all_checks'
                assert 'source check passed' in summary['results'][0]['output_preview']
                assert not observed['exit']['native_host_tools_attested']
                context = await session.call_tool('client_context', {'project_id': None, 'arguments': {}})
                assert context.structuredContent is not None, context
                assert context.structuredContent['result']['native_task_attestation'] == 'not_provided'

        asyncio.run(exercise())
        assert {path.name: path.read_bytes() for path in source.iterdir()} == original
        assert engine.workers.status()['succeeded_operations'] == 2
