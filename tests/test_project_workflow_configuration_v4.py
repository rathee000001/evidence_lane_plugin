"""Recipes preserve source ownership and lead to real Git/non-Git lane work."""
import asyncio
import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.engine_runtime import create_runtime_engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.sdk import ActionRequest

from tests.test_custom_instances_v4 import database_bytes
from tests.test_custom_protocol_v4 import join
from tests.test_git_sync_v4 import git
from tests.test_native_workflow_bindings import native


def files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob('*') if path.is_file()}


def initialize(source, *, commit=True):
    git(source, 'init', '-b', 'codex/workflow-fixture')
    git(source, 'config', 'user.name', 'Workflow fixture')
    git(source, 'config', 'user.email', 'workflow@example.invalid')
    git(source, 'config', 'core.autocrlf', 'false')
    if commit:
        git(source, 'add', 'app.py')
        git(source, 'commit', '-m', 'Workflow fixture')


@contextmanager
def system(tmp_path, source=None):
    source = source or tmp_path / 'source'
    source.mkdir(exist_ok=True)
    if not (source / 'app.py').exists():
        (source / 'app.py').write_bytes(b'answer = 42\n')
    with Engine(tmp_path / 'runtime') as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, writer = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write'])]))
        _, reader = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read'])]))
        dispatch = PublicActionSDKDispatcher(engine)

        def call(action, arguments=None, *, writing=False):
            return dispatch.execute(ActionRequest(action=action, project_id=store.project_id,
                arguments=arguments or {}), writer if writing else reader)

        registered = call('source_register', {'sources': [str(source / 'app.py')], 'git_mode': 'DISABLED'}, writing=True)
        assert registered.status == 'ok', registered.error
        batch = registered.result['result']['source_authority']['batch_id']

        def workflow(**values):
            return call('project_workflow_configure', {'batch_id': batch, 'requested_outcome': 'Maintain the selected source.', **values})

        yield engine, store, call, workflow


@pytest.mark.parametrize('state', ['attached', 'detached', 'unborn', 'linked'])
def test_exact_git_workflow_preserves_every_source_and_database_byte(tmp_path, state):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'answer = 1\n')
    initialize(source, commit=state != 'unborn')
    if state == 'detached':
        git(source, 'checkout', '--detach')
    if state == 'linked':
        linked = tmp_path / 'linked'
        git(source, 'worktree', 'add', '-b', 'codex/linked-workflow', str(linked))
        source = linked
    if state != 'unborn':
        (source / 'app.py').write_bytes(b'answer = 2\n')
        git(source, 'add', 'app.py')
    (source / 'app.py').write_bytes(b'answer = 3\n')
    (source / 'untracked.txt').write_bytes(b'Untracked bytes\x00remain exact.')
    with system(tmp_path, source) as (_, store, _, workflow):
        before = files(tmp_path / 'source'), files(source), database_bytes(store)
        response = workflow(git_mode='REQUIRED')
        assert response.status == 'ok', response.error
        result = response.result
        repository = result['repository_context']
        assert repository['state'] == ('attached' if state == 'linked' else state)
        assert repository['exact_root_admitted'] and not repository['dirty_bytes_attested']
        assert repository['git_commands_executed'] in (3, 4) and not repository['remote_contacted']
        assert result['execution_workflow']['kind'] == 'git_existing_project'
        assert 'remote_git_execute_push' in result['execution_workflow']['git_actions']
        assert result['operation_contracts']['remote_git_execute_push']['requires_delta']
        assert result['selected_sector_lanes'] == ['local_code']
        assert not result['workflow_dependency_readiness_checked']
        assert result['lane_workflows'][0]['materialization_actions'] == ['code_index']
        assert 'code_index_syntax' in result['lane_workflows'][0]['change_actions']
        assert (files(tmp_path / 'source'), files(source), database_bytes(store)) == before
        assert response.tool_execution['selection']['selected_route'] == 'project_workflow_configure.git'


def test_non_git_and_parent_repository_do_not_get_adopted(tmp_path, monkeypatch):
    # The runner can redirect TEMP/TMP beneath this Git checkout. Select the
    # platform default before creating any engine threads, then restore the
    # runner's environment and tempfile cache for all remaining test work.
    with monkeypatch.context() as default_temp:
        for name in ('TMPDIR', 'TEMP', 'TMP'):
            default_temp.delenv(name, raising=False)
        default_temp.setattr(tempfile, 'tempdir', None)
        system_temp = Path(tempfile.gettempdir()).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='evidence-workflow-no-git-', dir=system_temp) as temporary, system(tmp_path, Path(temporary)) as (_, store, _, workflow):
        before = files(store.source_root), database_bytes(store)
        result = workflow()
        assert result.status == 'ok', result.error
        assert result.result['repository_context']['state'] == 'not_detected'
        assert result.result['execution_workflow']['kind'] == 'content_only'
        assert result.result['execution_workflow']['git_actions'] == []
        required = workflow(git_mode='REQUIRED')
        assert required.error.code == 'PROJECT_WORKFLOW_GIT_REQUIRED'
        assert (files(store.source_root), database_bytes(store)) == before
    parent = tmp_path / 'parent'
    parent.mkdir()
    git(parent, 'init', '-b', 'codex/parent')
    child = parent / 'selected'
    child.mkdir()
    nested_runtime = tmp_path / 'nested-case'
    nested_runtime.mkdir()
    with system(nested_runtime, child) as (_, store, _, workflow):
        before = files(parent), database_bytes(store)
        result = workflow()
        assert result.result['repository_context']['state'] == 'parent_not_admitted'
        assert not result.result['repository_context']['exact_root_admitted']
        assert result.result['execution_workflow']['kind'] == 'content_only'
        assert workflow(git_mode='REQUIRED').error.code == 'PROJECT_WORKFLOW_GIT_REQUIRED'
        assert (files(parent), database_bytes(store)) == before


def test_real_missing_git_uses_content_fallback_but_required_fails(tmp_path, monkeypatch):
    with system(tmp_path) as (_, store, _, workflow):
        before = files(store.source_root), database_bytes(store)
        monkeypatch.setenv('PATH', '')
        result = workflow()
        assert result.status == 'ok', result.error
        assert result.result['repository_context']['state'] == 'unavailable'
        assert result.result['repository_context']['git_commands_executed'] == 0
        assert result.tool_execution['selection']['selected_route'] == 'project_workflow_configure.content'
        assert result.tool_execution['selection']['fallback_used']
        required = workflow(git_mode='REQUIRED')
        assert required.error.code == 'TOOL_ROUTE_UNAVAILABLE'
        assert (files(store.source_root), database_bytes(store)) == before


def test_disabled_git_does_not_invoke_repository_helpers(tmp_path, monkeypatch):
    import evidence_lane_plugin.git_adapter as adapter
    with system(tmp_path) as (_, store, _, workflow):
        initialize(store.source_root)
        before = files(store.source_root), database_bytes(store)

        def forbidden(*args, **kwargs):
            pytest.fail('Explicitly disabled Git was invoked.')

        monkeypatch.setattr(adapter, 'restoration_git', forbidden)
        result = workflow(git_mode='DISABLED')
        assert result.status == 'ok', result.error
        assert result.result['repository_context']['state'] == 'disabled'
        assert result.result['execution_workflow']['kind'] == 'content_only'
        assert (files(store.source_root), database_bytes(store)) == before


def test_git_failure_after_invocation_does_not_silently_switch_to_content(tmp_path, monkeypatch):
    import evidence_lane_plugin.git_adapter as adapter
    with system(tmp_path) as (_, store, _, workflow):
        before = files(store.source_root), database_bytes(store)
        observed = []

        def timeout(root, arguments, **bounds):
            observed.append((root, arguments, bounds))
            raise LaneError('PROCESS_TIMEOUT', 'Injected timeout at the bounded process boundary.')

        monkeypatch.setattr(adapter, 'restoration_git', timeout)
        response = workflow()
        assert response.error.code == 'PROCESS_TIMEOUT'
        assert len(observed) == 1
        assert 0 < observed[0][2]['timeout_seconds'] <= 10
        assert observed[0][2]['max_stdout_bytes'] == 16384
        assert response.error.details['tool_execution']['selection']['selected_route'] == 'project_workflow_configure.git'
        assert not response.error.details['tool_execution']['automatic_retry']
        assert (files(store.source_root), database_bytes(store)) == before


def test_mixed_workflow_keeps_all_lane_contracts_and_refuses_partial_output(tmp_path):
    with system(tmp_path) as (engine, store, call, _):
        lanes = ['local_code', 'github_code', 'docs', 'data_excel', 'data', 'ppt', 'tableau',
            'power_bi', 'pdf_ocr', 'images_ocr', 'research', 'artifacts', 'custom']
        paths = []
        for lane in lanes:
            path = store.source_root / (lane + '.txt')
            path.write_text('Selected bytes for classification only.')
            paths.append(str(path))
        registration = call('source_register', {'sources': paths,
            'overrides': dict(zip(paths, lanes)), 'git_mode': 'DISABLED'}, writing=True)
        assert registration.status == 'ok', registration.error
        batch = registration.result['result']['source_authority']['batch_id']
        arguments = {'batch_id': batch, 'requested_outcome': 'Maintain the selected mixed project.', 'git_mode': 'DISABLED'}
        before = files(store.source_root), database_bytes(store)
        result = call('project_workflow_configure', arguments)
        assert result.status == 'ok', result.error
        body = result.result
        assert body['project_type'] == 'MIXED' and body['selected_sector_lanes'] == lanes
        assert {row['lane_id'] for row in body['lane_workflows']} == set(lanes)
        assert all(row['materialization_actions'] for row in body['lane_workflows'])
        assert all(not row['plugin_maintainer_ci_imposed'] for row in body['selected_lane_validation'].values())
        assert not body['source_bytes_reverified'] and body['work_classification'] is None
        for name, contract in body['operation_contracts'].items():
            assert engine.registry.get(name).schema()['toolchain'] == contract['toolchain']
        denied = call('project_workflow_configure', {**arguments, 'max_result_bytes': 8192})
        assert denied.error.code == 'PROJECT_WORKFLOW_RESULT_BUDGET'
        assert (files(store.source_root), database_bytes(store)) == before


@pytest.mark.parametrize('git_enabled', [False, True])
def test_packaged_workflow_prepares_and_executes_a_verified_code_task(tmp_path, git_enabled):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'app.py').write_bytes(b'def answer():\n    return 42\n')
    if git_enabled:
        initialize(source)
    with create_runtime_engine(tmp_path / 'runtime', workers=1, capabilities=CapabilityMonitor()) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)

        async def run():
            async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools'), entrypoint='package') as session:
                async def call(action, arguments=None, **envelope):
                    response = (await session.call_tool(action, {'project_id': store.project_id,
                        'arguments': arguments or {}, **envelope})).structuredContent
                    assert response['status'] == 'ok', response
                    return response['result']

                tools = {item.name: item for item in (await session.list_tools()).tools}
                assert tools['project_workflow_configure'].annotations.readOnlyHint
                assert (await call('client_context'))['native_task_attestation'] == 'not_provided'
                registered = (await call('source_register', {'sources': [str(source / 'app.py')], 'git_mode': 'DISABLED'}))['result']
                before = files(source), database_bytes(store)
                workflow = await call('project_workflow_configure', {'batch_id': registered['source_authority']['batch_id'],
                    'requested_outcome': 'Index the selected Python function.', 'git_mode': 'AUTO'})
                assert workflow['execution_workflow']['kind'] == ('git_existing_project' if git_enabled else 'content_only')
                assert (files(source), database_bytes(store)) == before
                prepared = (await call(workflow['lane_workflows'][0]['source_preparation_action'],
                    {'selection': {'route_id': registered['source_routes']['route_id'], 'occurrence_ordinals': [1]}}))['result']
                assert prepared['task_count'] == 1
                task = prepared['tasks'][0]
                assert task['operation']['action'] in workflow['lane_workflows'][0]['materialization_actions']
                await call('plan_create', {'title': 'Execute the proposed Code workflow', 'tasks': prepared['tasks']})
                task = (await call('plan_read'))['tasks'][0]
                admitted = (await session.call_tool('delta_enter_planned', {'project_id': store.project_id,
                    'expected_revision': 1, 'arguments': {'task_id': task['definition']['task_id'],
                        'plan_revision': 1, 'contract_digest': task['contract_digest']}})).structuredContent
                assert admitted['status'] == 'queued', admitted
                await asyncio.to_thread(join, engine)
                result = await call('delta_status', {'job_id': admitted['job_id'],
                    'include_result': True, 'include_verification': True})
                assert result['state'] == 'verified' and result['exit'] is not None, result
                assert result['result']['result']['result']['files'] == 1
                assert (await call('plan_read'))['counts'] == {'completed': 1}
                assert files(source) == before[0]
        with LocalEndpoint(engine, studio_enabled=False):
            asyncio.run(run())
        assert engine.workers.status()['succeeded_operations'] >= 1
