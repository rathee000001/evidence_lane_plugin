import os
import subprocess
import sys
from pathlib import Path

import evidence_lane_plugin.lane_transactions as transactions
import pytest
from evidence_lane_plugin.adaptive_delta_entry import DeltaEnter
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.plan_runtime import PlanStore
from evidence_lane_plugin.storage import _commit_scope

from tests.test_completion_exit_v4 import bytes_digest, status
from tests.test_delta_entry import call, finished, plan, request, system
from tests.test_delta_exit import second_task

__all__ = ['system']


@pytest.mark.parametrize('phase', ['committed:learning', 'committed:memory', 'committed:plan',
                                  'committed:receipts', 'before_root_publish', 'published'])
def test_completion_publication_fault_preserves_one_coherent_outcome(system, monkeypatch, phase):
    view = plan(system, additional=[second_task()])
    operation = request(system, view)
    original = transactions._checkpoint
    reached = []

    def interrupt(fault, current):
        original(fault, current)
        active = _commit_scope.get()
        if (current != phase or active is None or reached
                or not {'plan', 'learning', 'memory'}.issubset(active.connections)):
            return
        if active.connection('plan').execute('SELECT 1 FROM delta_exits').fetchone():
            reached.append(active.commit_id)
            raise LaneError('INJECTED_PUBLICATION_REPLY_FAILURE', 'Fault at the selected completion publication boundary.')

    monkeypatch.setattr(transactions, '_checkpoint', interrupt)
    entered = call(system, operation)
    assert entered.status == 'queued', entered.error
    run = finished(system, entered.job_id)
    system[0].delta.owned_completion(entered.job_id).result(timeout=5)
    assert len(reached) == 1
    store = system[1]
    with store.connection(read_only=True) as connection:
        phase_recorded = connection.execute('SELECT phase FROM root_transaction_journal WHERE commit_id=?', reached).fetchone()[0]
    if phase == 'published':
        assert phase_recorded == 'published'
        assert run['state'] == 'verified' and run['error_code'] is None, run
        assert JobQueue(store).get(entered.job_id)['state'] == 'succeeded'
        assert [task.state for task in PlanStore(store).snapshot().tasks] == ['completed', 'active']
        result = status(system, entered.job_id, include_result=True, include_verification=True)
        assert result.status == 'ok' and result.result['exit']['memory_head'], result
        before = bytes_digest(store.root)
        assert call(system, operation).job_id == entered.job_id
        assert bytes_digest(store.root) == before
        assert system[0].workers.status()['submitted'] == 1
    else:
        assert phase_recorded == 'aborted'
        assert run['state'] == 'blocked' and run['error_code'] == 'INJECTED_PUBLICATION_REPLY_FAILURE', run
        assert [task.state for task in PlanStore(store).snapshot().tasks] == ['blocked', 'queued']
        assert JobQueue(store).get(entered.job_id)['state'] == 'checkpointed'
        for lane, table in [('plan', 'delta_exits'), ('learning', 'learning_versions'), ('memory', 'memory_events')]:
            with store.lane(lane).connection(read_only=True) as connection:
                exists = connection.execute('SELECT 1 FROM sqlite_schema WHERE name=?', (table,)).fetchone()
                assert not exists or connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
        with store.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute("SELECT count(*) FROM receipts WHERE kind IN "
                "('delta_exit_verified','delta_exit_memory_refreshed','delta_next_selected')").fetchone()[0] == 0
        assert status(system, entered.job_id).result['exit'] is None
    PlanStore(store).verify_history()


def test_late_driver_recovery_preserves_published_completion(system):
    view = plan(system, additional=[second_task()])
    operation = request(system, view)
    entered = call(system, operation)
    assert finished(system, entered.job_id)['state'] == 'verified'
    engine, store, _, _ = system
    before = bytes_digest(store.root)
    engine.delta._recover(store, entered.job_id, DeltaEnter.model_validate(operation.arguments), 'LATE_DRIVER_FAILURE')
    result = status(system, entered.job_id, include_result=True)
    assert result.status == 'ok' and result.result['state'] == 'verified', result
    assert [task.state for task in PlanStore(store).snapshot().tasks] == ['completed', 'active']
    assert engine.workers.status()['submitted'] == 1
    assert bytes_digest(store.root) == before


@pytest.mark.parametrize('damage', ['wrong_contract', 'missing_exit'])
def test_conflicting_late_recovery_preserves_evidence_for_inspection(system, damage):
    view = plan(system)
    operation = request(system, view)
    entered = call(system, operation)
    assert finished(system, entered.job_id)['state'] == 'verified'
    engine, store, _, _ = system
    selected = DeltaEnter.model_validate(operation.arguments)
    if damage == 'wrong_contract':
        selected = selected.model_copy(update={'contract_digest': '0' * 64})
    else:
        with engine.project_work.mutation(store) as lease, lease.transaction('plan') as connection:
            connection.execute('DELETE FROM delta_exits WHERE job_id=?', (entered.job_id,))
    before = bytes_digest(store.root)
    with pytest.raises(LaneError) as caught:
        engine.delta._recover(store, entered.job_id, selected, 'LATE_DRIVER_FAILURE')
    assert caught.value.code == ('DELTA_RECOVERY_BINDING_CHANGED' if damage == 'wrong_contract' else 'DELTA_EXIT_INTEGRITY')
    assert bytes_digest(store.root) == before


def test_driver_recovery_restores_pending_journal_before_reconciling_completion(system):
    view = plan(system)
    operation = request(system, view)
    entered = call(system, operation)
    assert finished(system, entered.job_id)['state'] == 'verified'
    engine, store, _, _ = system
    code = '''
import os,sys
from pathlib import Path
from evidence_lane_plugin.storage import ProjectStore
store=ProjectStore(Path(sys.argv[1]))
def stop(phase):
    if phase == 'committed:plan':
        os._exit(73)
with store.coordinated_transaction(['plan','memory'],fault=stop) as commit:
    commit.connection('plan').execute("UPDATE delta_runs SET state='blocked',error_code='UNPUBLISHED'")
'''
    source = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src'
    crashed = subprocess.run([sys.executable, '-c', code, str(store.root)],
        env=dict(os.environ, PYTHONPATH=str(source)), capture_output=True, text=True, timeout=15, check=False)
    assert crashed.returncode == 73, crashed.stderr
    assert status(system, entered.job_id).error.code == 'PROJECT_RECOVERY_REQUIRED'
    engine.delta._recover(store, entered.job_id, DeltaEnter.model_validate(operation.arguments), 'LATE_DRIVER_FAILURE')
    result = status(system, entered.job_id, include_result=True)
    assert result.status == 'ok' and result.result['state'] == 'verified', result
    assert PlanStore(store).snapshot().counts == {'completed': 1}
    assert engine.workers.status()['submitted'] == 1
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM root_transaction_journal WHERE phase='prepared'").fetchone()[0] == 0
