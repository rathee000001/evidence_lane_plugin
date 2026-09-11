"""Cross-authority evidence and transaction boundaries for Plan operations."""
import sqlite3

import pytest
from evidence_lane_plugin.database_recovery import RECOVERY_MIGRATIONS
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lineage import ChatLineage, LineageRecord
from evidence_lane_plugin.migrations import apply_migrations
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.steering import Steering, SteerIntent
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.store import RESTORE_MIGRATIONS
from evidence_lane_plugin.writers import WriterLease


@pytest.fixture
def project(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    return ProjectStore.create(tmp_path / 'state', source)


def request(**kwargs):
    return PlanCreate(title='Verified lane ownership', tasks=[
        TaskDefinition(task_id='one', title='One', requested_outcome='A bounded outcome')], **kwargs)


def controls(project):
    with project.connection(read_only=True):
        with project.lane('sources').connection(read_only=True) as connection:
            source = connection.execute('SELECT cleared_by_revision FROM restoration_control').fetchone()[0]
        with project.lane('receipts').connection(read_only=True) as connection:
            recovery = connection.execute('SELECT cleared_by_revision FROM recovery_control').fetchone()[0]
        return source, recovery


def test_plan_and_both_recovery_boundaries_commit_together(project, monkeypatch):
    plan = PlanStore(project)
    source_digest, recovery_digest = '1' * 64, '2' * 64
    # This fixture isolates atomic clearing of the three lane records. The
    # restored-source reindex itself is exercised in test_source_restoration_v4.
    # Its later-added prerequisite must succeed before reaching our injected
    # failure after all participating records change.
    reindex_checks = []
    def verified_reindex(connection, control):
        assert connection.in_transaction and control['restore_digest'] == source_digest
        reindex_checks.append(control['generation'])
    monkeypatch.setattr('evidence_lane_plugin.store.require_restoration_reindex', verified_reindex)
    with WriterLease(project, 'fixture') as lease:
        apply_migrations(project, RESTORE_MIGRATIONS + RECOVERY_MIGRATIONS, writer=lease)
        with lease.coordinated_transaction(['sources']) as commit:
            commit.connection('sources').execute('INSERT INTO restoration_history VALUES(1,?,?)', (source_digest, '{}'))
            commit.connection('sources').execute('INSERT INTO restoration_control VALUES(1,1,?,0,NULL)', (source_digest,))
            commit.connection('receipts').execute('INSERT INTO recovery_control VALUES(1,?,0,NULL)', (recovery_digest,))
        pending = request(source_restore_digest=source_digest, database_recovery_digest=recovery_digest)
        original = plan._event

        def fail(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError('Failure after all three authorities changed')

        with monkeypatch.context() as patch:
            patch.setattr(plan, '_event', fail)
            with pytest.raises(OSError):
                plan.create(pending, lease, actor_id='fixture')
        assert controls(project) == (None, None)
        assert plan.snapshot().state == 'no_plan'
        with project.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute("SELECT 1 FROM receipts WHERE kind='plan_created'").fetchone() is None
        assert plan.create(pending, lease, actor_id='fixture').revision == 1
        assert controls(project) == (1, 1)
        assert reindex_checks == [1, 1]
    heads = {item['lane_id']: item['commit_id'] for item in project.lane_catalog()}
    assert heads['plan'] == heads['sources'] == heads['receipts']


def test_steer_validates_chatlineage_without_copying_it_into_plan(project):
    with WriterLease(project, 'fixture') as lease:
        plan = PlanStore(project)
        plan.create(request(), lease, actor_id='actor')
        event = ChatLineage(project).append(LineageRecord(kind='prompt', payload={'text': 'Change the outcome'}), lease, client_id='actor')
        steer = SteerIntent(source_event_id=event.event_id, source_cursor=event.cursor,
            expected_revision=1, intent='semantic', affected_task_ids=['one'], rationale='Visible changed request')
        Steering(project).submit(steer, lease, actor_id='actor')
    with project.lane('plan').connection(read_only=True) as connection:
        assert connection.execute('SELECT source_event_id FROM steer_requests').fetchone()[0] == event.event_id
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name IN ('lineage_events','receipts')").fetchone() is None
    with pytest.raises(LaneError, match='not registered'):
        project.lane('plan').read_object(event.payload_digest)
    assert project.lane('chat_lineage').read_object(event.payload_digest)


def test_foreign_and_unmanaged_transactions_cannot_drive_plan(project):
    plan = PlanStore(project)
    with WriterLease(project, 'fixture') as lease:
        plan.create(request(), lease, actor_id='actor')
        with lease.coordinated_transaction(['plan', 'memory', 'sources']) as commit:
            with pytest.raises(LaneError, match='owning lane connection'):
                plan.transition('one', 'active', lease, expected_revision=1, actor_id='actor', transaction=commit.connection('memory'))
            # A read inside the declared transaction uses its existing snapshot.
            assert plan.task('one', expected_revision=1).state == 'queued'
        with sqlite3.connect(':memory:') as unrelated, pytest.raises(LaneError, match='owning lane connection'):
            plan.transition('one', 'active', lease, expected_revision=1, actor_id='actor', transaction=unrelated)
    assert plan.task('one', expected_revision=1).state == 'queued'


def test_reference_reads_in_a_commit_do_not_grant_writes_or_advance_source_heads(project):
    with project.coordinated_transaction(['memory']) as commit:
        commit.connection('memory').execute('CREATE TABLE fixture (value TEXT)')
        commit.connection('memory').execute("INSERT INTO fixture VALUES('original')")
    before = next(row for row in project.lane_catalog() if row['lane_id'] == 'memory')
    with project.coordinated_transaction(['plan']):
        memory = project.lane('memory')
        with memory.connection(read_only=True) as connection:
            assert connection.execute('SELECT value FROM fixture').fetchone()[0] == 'original'
            with pytest.raises(sqlite3.OperationalError):
                connection.execute("UPDATE fixture SET value='unauthorized'")
        with pytest.raises(LaneError, match='Declare every participating lane'), memory.transaction():
            pass
    assert next(row for row in project.lane_catalog() if row['lane_id'] == 'memory') == before
