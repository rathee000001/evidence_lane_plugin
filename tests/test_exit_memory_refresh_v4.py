import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.project_memory import MemoryRead, ProjectMemory
from evidence_lane_plugin.studio_gateway import StudioGateway

from tests.test_completion_exit_v4 import bytes_digest, status
from tests.test_delta_entry import call, finished, plan, request, system
from tests.test_delta_exit import second_task
from tests.test_native_workflow_bindings import call as native_call
from tests.test_native_workflow_bindings import native

__all__ = ['system']


def complete(system):
    view = plan(system, additional=[second_task()])
    operation = request(system, view, text='Private input stays in its owning result lane')
    entered = call(system, operation)
    assert entered.status == 'queued', entered.error
    run = finished(system, entered.job_id)
    assert run['state'] == 'verified', run
    result = status(system, entered.job_id)
    assert result.status == 'ok', result.error
    return entered, operation, result.result['exit']


def test_completion_indexes_only_attributed_references_and_protocol_reads_preserve_bytes(system):
    entered, operation, exit_record = complete(system)
    engine, store, _, _ = system
    memory = ProjectMemory(store)
    refreshed = memory.read_verified_exit(entered.job_id, exit_record['receipt_id'])
    assert refreshed.link_semantics == 'engine_verified_exit'
    assert len(refreshed.locator_ids) == 4 and len(refreshed.edge_ids) == 3
    assert exit_record['memory_refresh'] == 'verified_exit_memory_v1'
    assert exit_record['memory_head'] == refreshed.head
    page = memory.read(MemoryRead(query='verified', limit=20))
    assert len(page.locators) == 4 and not page.truncated
    assert {item['locator']['reference']['kind'] for item in page.locators} == {
        'plan_task', 'receipt', 'learning_version', 'source_object'}
    assert all(item['metadata_provenance'] == 'engine_verified_exit' for item in page.locators)
    assert all(item['source_content_returned'] is False for item in page.locators)
    result_locator = next(item for item in page.locators if item['locator']['reference']['kind'] == 'source_object')
    assert result_locator['profile_attribution'] == 'engine_verified_exit'
    assert all(edge['semantics_provenance'] == 'engine_verified_exit' for item in page.locators for edge in item['edges'])
    assert 'Private input' not in page.model_dump_json() and str(store.source_root) not in page.model_dump_json()
    before = bytes_digest(store.root)
    assert call(system, operation).job_id == entered.job_id
    with LocalEndpoint(engine):
        async def exercise():
            async with native(engine.root, store.project_id) as session:
                read = await native_call(session, 'delta_status', store.project_id, job_id=entered.job_id)
                assert read['status'] == 'ok', read
                assert read['result']['exit']['memory_head'] == refreshed.head
                items = await native_call(session, 'memory_read', store.project_id, query='verified', limit=20)
                assert items['status'] == 'ok', items
                assert len(items['result']['locators']) == 4
        asyncio.run(exercise())
    gateway = StudioGateway(engine)
    _, session = gateway.exchange(gateway.issue_ticket())
    read = gateway.command('read', {'project_id': store.project_id, 'action': 'delta_status',
        'arguments': {'job_id': entered.job_id}}, session)
    assert read['exit']['memory_head'] == refreshed.head
    assert bytes_digest(store.root) == before
    assert engine.workers.status()['submitted'] == 1


def test_same_exit_replay_creates_no_memory_event_and_wrong_receipt_is_rejected(system):
    entered, _, exit_record = complete(system)
    engine, store, _, _ = system
    memory = ProjectMemory(store)
    history = memory.verify_history()
    with engine.project_work.mutation(store) as lease, lease.transaction('memory') as connection:
        prior = memory.record_verified_exit(entered.job_id, exit_record['receipt_id'], lease, transaction=connection)
        assert prior.duplicate and prior.head == exit_record['memory_head']
        with pytest.raises(LaneError) as error:
            memory.record_verified_exit(entered.job_id, str(uuid4()), lease, transaction=connection)
        assert error.value.code == 'MEMORY_VERIFIED_EXIT_REQUIRED'
    assert memory.verify_history() == history
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='delta_exit_memory_refreshed'").fetchone()[0] == 1


def test_failure_after_memory_refresh_rolls_back_all_completion_lanes(system, monkeypatch):
    view = plan(system, additional=[second_task()])
    original = ProjectMemory.record_verified_exit
    reached = []
    def fail(self, *args, **kwargs):
        reached.append(original(self, *args, **kwargs).head)
        raise LaneError('INJECTED_MEMORY_EXIT_FAILURE', 'Fault after Memory rows and receipt were written.')
    monkeypatch.setattr(ProjectMemory, 'record_verified_exit', fail)
    entered = call(system, request(system, view))
    run = finished(system, entered.job_id)
    assert run['error_code'] == 'INJECTED_MEMORY_EXIT_FAILURE', run
    assert len(reached) == 1
    store = system[1]
    assert [task.state for task in PlanStore(store).snapshot().tasks] == ['blocked', 'queued']
    assert JobQueue(store).get(entered.job_id)['state'] == 'checkpointed'
    for lane, table in [('plan', 'delta_exits'), ('learning', 'learning_versions'), ('memory', 'memory_events')]:
        with store.lane(lane).connection(read_only=True) as connection:
            exists = connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (table,)).fetchone()
            assert not exists or connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind IN "
            "('delta_exit_verified','delta_exit_memory_refreshed','delta_next_selected')").fetchone()[0] == 0
    assert ProjectMemory(store).read().locators == []


@pytest.mark.parametrize('damage', ['event', 'locator', 'edge', 'result_ids', 'refresh_receipt', 'policy', 'learning_profile'])
def test_completion_read_detects_missing_or_inconsistent_memory_without_repair(system, damage):
    entered, _, exit_record = complete(system)
    engine, store, _, _ = system
    with engine.project_work.mutation(store) as lease, lease.coordinated_transaction(['memory', 'learning']) as commit:
        connection = commit.connection('memory')
        if damage == 'event':
            connection.execute("DELETE FROM memory_events WHERE kind='verified_exit'")
        elif damage == 'locator':
            connection.execute("UPDATE memory_locators SET reference_digest=? WHERE reference_kind='receipt'", ('f'*64,))
        elif damage == 'edge':
            connection.execute('DELETE FROM memory_edges WHERE edge_id=(SELECT edge_id FROM memory_edges LIMIT 1)')
        elif damage == 'result_ids':
            row = connection.execute("SELECT result_json FROM memory_events WHERE kind='verified_exit'").fetchone()
            body = json.loads(row[0])
            body['locator_ids'] = []
            connection.execute("UPDATE memory_events SET result_json=? WHERE kind='verified_exit'", (json.dumps(body),))
        elif damage == 'refresh_receipt':
            commit.connection('receipts').execute("DELETE FROM receipts WHERE kind='delta_exit_memory_refreshed'")
        elif damage == 'learning_profile':
            commit.connection('learning').execute("UPDATE learning_versions SET profile='wrong' WHERE source_job_id=?", (entered.job_id,))
        else:
            receipts = commit.connection('receipts')
            body = json.loads(receipts.execute('SELECT body_json FROM receipts WHERE receipt_id=?', (exit_record['receipt_id'],)).fetchone()[0])
            body.pop('memory_refresh_policy')
            receipts.execute('UPDATE receipts SET body_json=? WHERE receipt_id=?', (json.dumps(body), exit_record['receipt_id']))
    before = bytes_digest(store.root)
    result = status(system, entered.job_id)
    assert result.error.code in {'MEMORY_EXIT_INTEGRITY', 'MEMORY_LOCATOR_INTEGRITY', 'MEMORY_HISTORY_INTEGRITY',
        'MEMORY_VERIFIED_EXIT_REQUIRED', 'DELTA_EXIT_INTEGRITY'}, result
    assert bytes_digest(store.root) == before


def test_readers_cannot_observe_completed_plan_before_memory_publication(system, monkeypatch):
    view = plan(system)
    written, release, reading = Event(), Event(), Event()
    original = ProjectMemory.record_verified_exit
    def pause(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        written.set()
        assert release.wait(8)
        return result
    monkeypatch.setattr(ProjectMemory, 'record_verified_exit', pause)
    entered = call(system, request(system, view))
    assert written.wait(15)
    def inspect():
        reading.set()
        return status(system, entered.job_id)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(inspect)
        try:
            assert reading.wait(1)
            assert not future.done()
        finally:
            release.set()
        assert finished(system, entered.job_id)['state'] == 'verified'
        result = future.result(timeout=15)
    assert result.status == 'ok', result.error
    assert result.result['exit']['memory_refresh'] == 'verified_exit_memory_v1'
    assert result.result['exit']['memory_head']
