from __future__ import annotations

import hashlib
import json
import sys

import pytest
from evidence_lane_plugin.acceptance import (
    ValidationCommand,
    ValidationPolicy,
    ValidationPolicyRead,
    ValidationPolicySet,
    ValidationPolicyStore,
    ValidationRule,
    ValidationSelector,
)
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import EvidenceLaneClient
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease
from pydantic import ValidationError


def rule(**overrides):
    from pathlib import Path
    values = {'check_id': 'project_tests', 'title': 'Run selected project tests',
        'command': ValidationCommand(tool_id='Python', executable=sys.executable,
            executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            arguments=['-m', 'pytest', 'tests/test_component.py'], timeout_seconds=30),
        'when': ValidationSelector(paths=['src/**', 'tests/**'], profiles=['code'])}
    values.update(overrides)
    return ValidationRule(**values)


@pytest.fixture
def project(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'unchanged.txt').write_bytes(b'original user bytes')
    return ProjectStore.create(tmp_path / 'state', source)


def save(project, request, actor='client-a'):
    with WriterLease(project, 'policy-test-engine') as writer:
        return ValidationPolicyStore(project).set(request, writer, actor_id=actor)


def test_read_is_nonmutating_and_never_discovers_repository_commands(project):
    path = project.source_root / 'evidence/acceptance/commands.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'commands': {'run': {'argv': [sys.executable, '-c', 'raise SystemExit(0)']}}}))
    original = project.lane('plan').database.read_bytes()
    result = ValidationPolicyStore(project).read()
    assert result.state == 'not_configured' and result.policy is None
    assert result.checks_executed is False and result.runtime_readiness_verified is False
    assert project.lane('plan').database.read_bytes() == original
    with pytest.raises(LaneError, match='no selected validation policy'):
        ValidationPolicyStore(project).read(ValidationPolicyRead(revision=1))


def test_policy_history_cas_and_idempotence_leave_active_plan_unchanged(project):
    plan = PlanStore(project)
    with WriterLease(project, 'test-engine') as writer:
        plan.create(PlanCreate(title='Document project', tasks=[TaskDefinition(task_id='one',
            title='Update document', requested_outcome='Updated document', profile='docs',
            acceptance_checks=['document_content_verified'])]), writer, actor_id='client-a')
        plan.transition('one', 'active', writer, expected_revision=1, actor_id='client-a')
    original = plan.snapshot().model_dump(mode='json')
    request = ValidationPolicySet(expected_revision=0, policy=ValidationPolicy(title='This project', checks=[rule()]))
    first = save(project, request)
    assert first.revision == first.current_revision == 1
    assert save(project, request) == first
    second = save(project, ValidationPolicySet(expected_revision=1, expected_digest=first.revision_digest,
        policy=ValidationPolicy(title='No additional CI', checks=[])))
    assert second.revision == 2 and second.previous_digest == first.revision_digest
    assert second.policy.checks == []
    replay = save(project, request)
    assert replay.revision == 1 and replay.current_revision == 2
    assert replay.policy_digest == first.policy_digest
    assert ValidationPolicyStore(project).read(ValidationPolicyRead(revision=1,
        expected_digest=first.revision_digest)) == replay
    for changed, actor, code in [
        (request.model_copy(update={'policy': ValidationPolicy(title='Changed')}), 'client-a', 'VALIDATION_POLICY_REQUEST_CONFLICT'),
        (request, 'other-client', 'VALIDATION_POLICY_REQUEST_CONFLICT'),
        (ValidationPolicySet(expected_revision=1, expected_digest=first.revision_digest,
            policy=request.policy), 'client-a', 'VALIDATION_POLICY_STALE'),
    ]:
        with pytest.raises(LaneError) as error:
            save(project, changed, actor)
        assert error.value.code == code
    assert plan.snapshot().model_dump(mode='json') == original
    assert (project.source_root / 'unchanged.txt').read_bytes() == b'original user bytes'
    assert not (project.source_root / '.github').exists()
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='validation_policy_configured'").fetchone()[0] == 2
    with project.connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='validation_policy_revisions'").fetchone()


def test_policy_failure_rolls_back_revision_head_and_receipt(project, monkeypatch):
    first = save(project, ValidationPolicySet(expected_revision=0, policy=ValidationPolicy(title='Initial')))
    original = project.append_receipt

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('Injected failure after receipt insertion')

    monkeypatch.setattr(project, 'append_receipt', fail)
    with pytest.raises(OSError):
        save(project, ValidationPolicySet(expected_revision=1, expected_digest=first.revision_digest,
            policy=ValidationPolicy(title='Replacement')))
    assert ValidationPolicyStore(project).read() == first
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='validation_policy_configured'").fetchone()[0] == 1


@pytest.mark.parametrize('column,value', [('policy_json', '{"title":"tampered","checks":[]}'),
    ('actor_id', 'other'), ('revision_digest', '0' * 64), ('previous_digest', '1' * 64), ('receipt_id', 'missing')])
def test_corrupt_policy_is_rejected_without_rewriting(project, column, value):
    save(project, ValidationPolicySet(expected_revision=0, policy=ValidationPolicy(title='Initial')))
    with project.lane('plan').transaction() as connection:
        connection.execute(f'UPDATE validation_policy_revisions SET {column}=?', (value,))
    before = project.lane('plan').database.read_bytes()
    with pytest.raises(LaneError) as error:
        ValidationPolicyStore(project).read()
    assert error.value.code == 'VALIDATION_POLICY_INTEGRITY'
    assert project.lane('plan').database.read_bytes() == before


def test_policy_cannot_be_rebound_to_other_project(project, tmp_path):
    first = save(project, ValidationPolicySet(expected_revision=0, policy=ValidationPolicy(title='Initial')))
    other = ProjectStore.create(tmp_path / 'other-state', project.source_root)
    assert ValidationPolicyStore(other).read().state == 'not_configured'
    request = ValidationPolicySet(expected_revision=1, expected_digest=first.revision_digest, policy=first.policy)
    with WriterLease(project, 'test-engine') as writer, pytest.raises(LaneError) as error:
        ValidationPolicyStore(other).set(request, writer, actor_id='client-a')
    assert error.value.code == 'WRITER_PROJECT_MISMATCH'
    with pytest.raises(LaneError) as error:
        save(other, request)
    assert error.value.code == 'VALIDATION_POLICY_STALE'


def test_missing_head_cannot_make_saved_policy_look_unconfigured(project):
    save(project, ValidationPolicySet(expected_revision=0, policy=ValidationPolicy(title='Initial')))
    with project.lane('plan').transaction() as connection:
        connection.execute('DELETE FROM validation_policy_current')
    with pytest.raises(LaneError) as error:
        ValidationPolicyStore(project).read()
    assert error.value.code == 'VALIDATION_POLICY_INTEGRITY'


def test_recipe_returns_only_selected_project_policy_and_preserves_bytes(tmp_path):
    from tests.test_project_recipes_v4 import database_bytes, files, system
    with system(tmp_path) as (_, store, call, recipe):
        result = recipe(git_mode='DISABLED')
        assert result.status == 'ok', result.error
        assert result.result['project_validation_policy']['state'] == 'not_configured'
        configured = call('validation_policy_set', ValidationPolicySet(expected_revision=0,
            policy=ValidationPolicy(title='Selected project', checks=[rule()])).model_dump(mode='json'), writing=True)
        assert configured.status == 'ok', configured.error
        before = files(store.source_root), database_bytes(store)
        observed = recipe(git_mode='DISABLED')
        assert observed.status == 'ok', observed.error
        assert observed.result['project_validation_policy'] == configured.result
        assert (files(store.source_root), database_bytes(store)) == before


@pytest.mark.parametrize('field,value', [('executable', 'python'), ('executable', 'C:/Windows/System32/cmd.exe'),
    ('executable', '/bin/bash'), ('executable', '//server/share/python.exe'),
    ('executable', 'C:/tools/../python.exe'), ('working_directory', '../outside'),
    ('working_directory', 'C:/outside'), ('arguments', ['x', '&&', 'y']),
    ('arguments', ['password=fixture-secret']), ('arguments', ['a' * 8193]), ('timeout_seconds', 1801)])
def test_unsafe_or_unbounded_command_declaration_is_rejected(field, value):
    command = rule().command.model_dump(mode='json')
    command[field] = value
    with pytest.raises(ValidationError):
        ValidationCommand.model_validate(command)


def test_invalid_checks_and_ambiguous_heads_are_rejected():
    for paths in [['../outside/**'], ['/absolute/**'], ['src//**'], ['src/**', 'src/**']]:
        with pytest.raises(ValidationError):
            ValidationSelector(paths=paths)
    with pytest.raises(ValidationError):
        ValidationPolicy(title='Duplicate', checks=[rule(), rule()])
    with pytest.raises(ValidationError):
        ValidationPolicySet(expected_revision=1, policy=ValidationPolicy(title='Missing digest'))
    with pytest.raises(ValidationError):
        ValidationPolicySet(expected_revision=0, expected_digest='a' * 64, policy=ValidationPolicy(title='Wrong initial digest'))


def test_sdk_routes_preserve_read_only_and_project_scope(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        project_id = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)['project_id']
        request = ValidationPolicySet(expected_revision=0, policy=ValidationPolicy(title='Selected project', checks=[rule()]))
        with LocalTransport(engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=project_id,
            permissions=['read', 'write'])])) as transport:
            client = EvidenceLaneClient(transport)
            before = client.call('validation_policy_read', project_id=project_id)
            assert before.status == 'ok' and before.result['state'] == 'not_configured'
            result = client.call('validation_policy_set', project_id=project_id, arguments=request.model_dump(mode='json'))
            assert result.status == 'ok', result
            assert result.result['revision'] == 1 and result.result['checks_executed'] is False
            read = client.call('validation_policy_read', project_id=project_id)
            assert read.result == result.result
            assert client.call('plan_read', project_id=project_id).result['state'] == 'no_plan'
        with LocalTransport(engine.root, connection=ConnectRequest(projects=[ProjectSelection(project_id=project_id)])) as transport:
            client = EvidenceLaneClient(transport)
            assert client.call('validation_policy_read', project_id=project_id).status == 'ok'
            assert client.call('validation_policy_set', project_id=project_id, arguments=request.model_dump(mode='json')).status == 'error'
        assert engine.registry.get('validation_policy_set').studio_read is False
        assert engine.registry.get('validation_policy_set').workflow == 'plan'
        assert not engine.registry.get('validation_policy_set').worker_operations
