import asyncio
import hashlib
import json
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from evidence_lane_plugin.connections import ConnectRequest
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.storage import json_text
from evidence_lane_plugin.studio_gateway import StudioGateway
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.test_code_profile_v4 import code_system, create_plan, execute
from tests.test_delta_entry import call, finished, plan, request, system
from tests.test_delta_exit import second_task

__all__ = ['code_system', 'system']


def status(system, job_id, **arguments):
    return call(system, ActionRequest(action='delta_status', project_id=system[1].project_id,
        arguments={'job_id': job_id, **arguments}))


def bytes_digest(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def enter_task(system, task_id, **arguments):
    view = PlanStore(system[1]).task(task_id, expected_revision=1)
    response = call(system, ActionRequest(action='delta_enter', project_id=system[1].project_id,
        expected_revision=1, arguments={'task_id': task_id, 'plan_revision': 1,
            'contract_digest': view.contract_digest, 'action': 'fixture_hash', 'arguments': arguments}))
    assert response.status == 'queued', response.error
    return response, finished(system, response.job_id)


def test_sequential_group_has_exact_exits_learning_and_replay_without_another_completion_action(system):
    view = plan(system, additional=[second_task()])
    original = request(system, view)
    first = call(system, original)
    assert first.status == 'queued', first.error
    assert finished(system, first.job_id)['state'] == 'verified'
    second, run = enter_task(system, 'second', text='second result')
    assert run['state'] == 'verified'
    assert [task.state for task in PlanStore(system[1]).snapshot().tasks] == ['completed', 'completed']
    before = bytes_digest(system[1].root)
    one = status(system, first.job_id, include_result=True, include_verification=True)
    two = status(system, second.job_id)
    assert one.status == two.status == 'ok', (one.error, two.error)
    assert one.result['exit']['next_task_id'] == 'second'
    assert two.result['exit']['next_task_id'] is None
    assert one.result['exit']['learning_state'] == 'superseded'
    assert two.result['exit']['learning_state'] == 'active'
    assert one.result['exit']['result_bytes_checked'] is True
    assert two.result['exit']['result_bytes_checked'] is False
    assert two.result['exit']['verification'] is None
    assert all(check['passed'] for check in one.result['exit']['verification']['checks'])
    assert one.result['exit']['source_currentness'] == 'not_rechecked'
    assert one.result['exit']['native_host_tools_attested'] is False
    assert bytes_digest(system[1].root) == before
    repeated = call(system, original)
    assert repeated.job_id == first.job_id
    assert system[0].workers.status()['submitted'] == 2
    assert bytes_digest(system[1].root) == before


def test_failed_middle_row_keeps_successor_queued_and_has_no_verified_exit(system):
    third = second_task().model_copy(update={'task_id': 'third'})
    plan(system, additional=[second_task(), third])
    first, _ = enter_task(system, 'first')
    failed, run = enter_task(system, 'second', corrupt_output=True)
    assert run['error_code'] == 'DELTA_ACCEPTANCE_FAILED'
    assert [task.state for task in PlanStore(system[1]).snapshot().tasks] == ['completed', 'blocked', 'queued']
    before = bytes_digest(system[1].root)
    assert status(system, failed.job_id).result['exit'] is None
    assert status(system, first.job_id).result['exit']['receipt_id']
    with system[1].lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM delta_exits').fetchone()[0] == 1
    assert bytes_digest(system[1].root) == before


@pytest.mark.parametrize('damage', ['missing_exit', 'receipt_task', 'check_value', 'duplicate_check',
    'job_result', 'learning_link', 'next_task', 'result_binding', 'dependency_index'])
def test_published_but_inconsistent_completion_is_rejected(system, damage):
    plan(system)
    entered, _ = enter_task(system, 'first')
    engine, store, _, _ = system
    with engine.project_work.mutation(store) as lease, lease.coordinated_transaction(['plan', 'learning']) as commit:
        connection = commit.connection('plan')
        receipts = commit.connection('receipts')
        row = connection.execute('SELECT * FROM delta_exits WHERE job_id=?', (entered.job_id,)).fetchone()
        if damage == 'missing_exit':
            connection.execute('DELETE FROM delta_exits WHERE job_id=?', (entered.job_id,))
        elif damage == 'receipt_task':
            body = json.loads(receipts.execute('SELECT body_json FROM receipts WHERE receipt_id=?', (row['receipt_id'],)).fetchone()[0])
            body['task_id'] = 'other'
            receipts.execute('UPDATE receipts SET body_json=? WHERE receipt_id=?', (json_text(body), row['receipt_id']))
        elif damage in {'check_value', 'duplicate_check'}:
            body = json.loads(store.lane('plan').read_object(row['verification_object']))
            if damage == 'check_value':
                body['checks'][0]['passed'] = False
            else:
                body['checks'][1] = body['checks'][0]
            digest = store.lane('plan').put_object(json_text(body).encode())
            connection.execute('UPDATE delta_exits SET verification_object=? WHERE job_id=?', (digest, entered.job_id))
        elif damage == 'job_result':
            connection.execute("UPDATE jobs_jobs SET result_json='{}' WHERE job_id=?", (entered.job_id,))
        elif damage == 'learning_link':
            commit.connection('learning').execute('UPDATE learning_versions SET source_receipt_id=? WHERE source_job_id=?',
                (str(uuid4()), entered.job_id))
        elif damage == 'next_task':
            connection.execute("UPDATE delta_exits SET next_task_id='invented' WHERE job_id=?", (entered.job_id,))
        elif damage == 'dependency_index':
            connection.execute("INSERT INTO plan_dependencies VALUES(1,'first','first')")
        else:
            result = connection.execute('SELECT result_object FROM delta_runs WHERE job_id=?', (entered.job_id,)).fetchone()[0]
            body = json.loads(store.lane('plan').read_object(result))
            body['task_id'] = 'other'
            digest = store.lane('plan').put_object(json_text(body).encode())
            connection.execute('UPDATE delta_runs SET result_object=? WHERE job_id=?', (digest, entered.job_id))
    before = bytes_digest(store.root)
    response = status(system, entered.job_id, include_result=True, include_verification=True)
    expected = 'PLAN_DEPENDENCY_INTEGRITY' if damage == 'dependency_index' else 'DELTA_EXIT_INTEGRITY'
    assert response.error is not None and response.error.code == expected, response
    assert bytes_digest(store.root) == before


def test_status_output_budget_and_unknown_run_preserve_every_lane(system):
    plan(system)
    entered, _ = enter_task(system, 'first')
    before = bytes_digest(system[1].root)
    assert status(system, entered.job_id, include_verification=True, max_bytes=1024).error.code == 'QUERY_OUTPUT_BUDGET'
    assert status(system, str(uuid4())).error.code == 'DELTA_NOT_FOUND'
    assert bytes_digest(system[1].root) == before


def test_source_edit_after_exit_is_not_reported_as_a_fresh_verification(system):
    plan(system)
    entered, _ = enter_task(system, 'first')
    (system[1].source_root / 'new-untracked.txt').write_text('later source edit', encoding='utf-8')
    response = status(system, entered.job_id, include_verification=True)
    assert response.status == 'ok', response.error
    assert response.result['exit']['source_currentness'] == 'not_rechecked'


def test_corrupt_verification_bytes_reject_without_refresh_or_repair(system):
    plan(system)
    entered, _ = enter_task(system, 'first')
    store = system[1]
    response = status(system, entered.job_id)
    target = store.lane('plan').object_path(response.result['exit']['verification_object'])
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b'"passed":true', b'"passed":null', 1))
    before = bytes_digest(store.root)
    assert status(system, entered.job_id).error.code == 'OBJECT_INTEGRITY_FAILED'
    assert bytes_digest(store.root) == before


def test_production_code_completion_through_stdio_and_readonly_studio(code_system):
    create_plan(code_system)
    execute(code_system)
    engine, store, _ = code_system
    with store.lane('plan').connection(read_only=True) as connection:
        job_id = connection.execute('SELECT job_id FROM delta_runs').fetchone()[0]
    before = bytes_digest(store.root)
    with LocalEndpoint(engine, studio_enabled=False):
        async def exercise():
            parameters = StdioServerParameters(command=sys.executable, args=[
                '-m', 'evidence_lane_plugin.mcp_adapter', '--runtime-root', str(engine.root),
                '--project-id', store.project_id], env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] /
                    'plugins/evidence-lane-plugin/src')})
            async with (stdio_client(parameters) as (read, write),
                        ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session):
                await session.initialize()
                tool = next(tool for tool in (await session.list_tools()).tools if tool.name == 'delta_status')
                assert tool.annotations.readOnlyHint
                response = await session.call_tool('delta_status', {'project_id': store.project_id,
                    'arguments': {'job_id': job_id, 'include_result': True, 'include_verification': True}})
                assert not response.isError and response.structuredContent['status'] == 'ok', response
                result = response.structuredContent['result']
                assert result['state'] == 'verified' and result['exit']['result_bytes_checked']
                assert result['exit']['checks'] == ['code_snapshot_integrity', 'code_source_hashes_unchanged']
                assert json.loads(response.content[0].text) == response.structuredContent
        asyncio.run(exercise())
    gateway = StudioGateway(engine)
    _, session = gateway.exchange(gateway.issue_ticket())
    result = gateway.command('read', {'project_id': store.project_id, 'action': 'delta_status',
        'arguments': {'job_id': job_id, 'include_verification': True}}, session)
    assert result['exit']['verification']['verification_basis'] == 'registered_profile_checks'
    _, unselected = engine.clients.connect(ConnectRequest())
    denied = PublicActionSDKDispatcher(engine).execute(ActionRequest(action='delta_status', project_id=store.project_id,
        arguments={'job_id': job_id}), unselected)
    assert denied.error.code == 'PROJECT_NOT_SELECTED'
    assert bytes_digest(store.root) == before
