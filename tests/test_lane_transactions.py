import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.lane_transactions import file_digest
from evidence_lane_plugin.storage import ProjectStore, bounded_project_read
from evidence_lane_plugin.writers import WriterLease


@pytest.fixture
def project(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    store = ProjectStore.create(tmp_path / 'state', source)
    with store.coordinated_transaction(['plan', 'memory']) as commit:
        for lane in ('plan', 'memory'):
            connection = commit.connection(lane)
            connection.execute('CREATE TABLE sample (value TEXT NOT NULL)')
            connection.execute("INSERT INTO sample VALUES('old')")
    return store


def values(store):
    with bounded_project_read(store.root, time.monotonic() + 5):
        result = []
        for lane in ('plan', 'memory'):
            with store.lane(lane).connection(read_only=True) as connection:
                result.append(connection.execute('SELECT value FROM sample').fetchone()[0])
        return result


def change(store, commit):
    for lane in ('plan', 'memory'):
        commit.connection(lane).execute("UPDATE sample SET value='complete'")
    store.append_receipt('delta_complete', {'fixture': True}, connection=commit.connection('plan'))


def crash(store, phase, *, recovery=False):
    code = '''
import os, sys
from pathlib import Path
from evidence_lane_plugin.storage import ProjectStore
store = ProjectStore(Path(sys.argv[1]))
def fault(phase):
    if phase == sys.argv[2]:
        os._exit(73)
if sys.argv[3] == 'recover':
    store.recover_transactions(fault=fault)
else:
    with store.coordinated_transaction(['plan', 'memory'], fault=fault) as commit:
        commit.connection(None).execute('PRAGMA cache_size=1')
        for lane in ('plan', 'memory'):
            commit.connection(lane).execute("UPDATE sample SET value='complete'")
        store.append_receipt('delta_complete', {'fixture': True}, connection=commit.connection('plan'))
'''
    source = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin/src'
    result = subprocess.run([sys.executable, '-c', code, str(store.root), phase,
                             'recover' if recovery else 'write'],
                            env=dict(os.environ, PYTHONPATH=str(source)),
                            capture_output=True, text=True, timeout=15, check=False)
    assert result.returncode == 73, result.stderr


def test_atomic_publication_has_independent_lane_heads_and_receipt(project):
    before = project.pv_head()
    with project.coordinated_transaction(['plan', 'memory'], expected_revision=before['revision']) as commit:
        change(project, commit)
    assert values(project) == ['complete', 'complete']
    after = project.pv_head()
    assert after['revision'] == before['revision'] + 1
    assert after['commit_id'] == commit.commit_id == commit.published_head['commit_id']
    catalog = project.lane_catalog()
    published = [item for item in catalog if item['head_digest'] is not None]
    assert {item['lane_id'] for item in published} == {'plan', 'memory', 'receipts'}
    assert {item['commit_id'] for item in published} == {commit.commit_id}
    assert len({item['head_digest'] for item in published}) == 3
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='delta_complete'").fetchone()[0] == 1
    with project.connection(read_only=True) as connection:
        assert connection.execute('SELECT phase FROM root_transaction_journal WHERE commit_id=?', (commit.commit_id,)).fetchone()[0] == 'published'


@pytest.mark.parametrize('phase', ['prepared', 'before_lane_commits', 'committed:memory',
                                   'committed:plan', 'committed:receipts', 'validated:memory',
                                   'validated:plan', 'validated:receipts', 'before_root_publish'])
def test_process_crash_keeps_partial_writes_unpublished_and_recoverable(project, phase):
    before = project.pv_head()
    databases = {row['lane_id']: project.root / row['database_path'] for row in project.lane_catalog()}
    hashes = {lane: file_digest(path) for lane, path in databases.items()}
    crash(project, phase)
    reopened = ProjectStore(project.root)
    with pytest.raises(LaneError) as error:
        values(reopened)
    assert error.value.code == 'PROJECT_RECOVERY_REQUIRED'
    with pytest.raises(LaneError), reopened.coordinated_transaction(['plan']):
        pass
    result = reopened.recover_transactions()
    assert len(result) == 1 and result[0]['phase'] == 'aborted'
    assert reopened.pv_head() == before
    assert values(reopened) == ['old', 'old']
    assert {lane: file_digest(path) for lane, path in databases.items()} == hashes
    with reopened.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='delta_complete'").fetchone()[0] == 0
    assert reopened.recover_transactions() == []


def test_process_crash_after_publication_keeps_the_completed_generation(project):
    before = project.pv_head()
    crash(project, 'published')
    reopened = ProjectStore(project.root)
    assert reopened.recover_transactions() == []
    assert reopened.pv_head()['revision'] == before['revision'] + 1
    assert values(reopened) == ['complete', 'complete']


def test_pending_reopen_preserves_bytes_and_read_only_cannot_recover(project):
    crash(project, 'committed:receipts')
    before = {path: path.read_bytes() for path in project.root.rglob('*') if path.is_file()}
    readonly = ProjectStore(project.root, read_only=True)
    assert readonly.project_id == project.project_id
    assert readonly.registration == project.registration
    with pytest.raises(LaneError) as caught:
        values(readonly)
    assert caught.value.code == 'PROJECT_RECOVERY_REQUIRED'
    with pytest.raises(LaneError) as caught:
        readonly.recover_transactions()
    assert caught.value.code == 'READ_ONLY_PROJECT'
    assert {path: path.read_bytes() for path in project.root.rglob('*') if path.is_file()} == before


def test_pending_registration_corruption_rejected_before_recovery(project):
    crash(project, 'committed:receipts')
    with sqlite3.connect(project.database) as connection:
        body = json.loads(connection.execute('SELECT body_json FROM project_registration').fetchone()[0])
        body['display_name'] = 'tampered registration'
        connection.execute('UPDATE project_registration SET body_json=?', (json.dumps(body),))
    before = {path: path.read_bytes() for path in project.root.rglob('*') if path.is_file()}
    with pytest.raises(LaneError) as caught:
        ProjectStore(project.root)
    assert caught.value.code == 'PROJECT_REGISTRATION_INTEGRITY'
    assert {path: path.read_bytes() for path in project.root.rglob('*') if path.is_file()} == before


def test_reopened_project_writer_recovers_before_new_work(project):
    crash(project, 'committed:receipts')
    reopened = ProjectStore(project.root)
    with WriterLease(reopened, 'recovery-owner') as writer:
        assert values(reopened) == ['old', 'old']
        with reopened.connection(read_only=True) as connection:
            assert connection.execute("SELECT COUNT(*) FROM root_transaction_journal WHERE phase='prepared'").fetchone()[0] == 0
        with reopened.lane('receipts').connection(read_only=True) as connection:
            assert connection.execute("SELECT COUNT(*) FROM receipts WHERE kind='delta_complete'").fetchone()[0] == 0
        with pytest.raises(LaneError) as caught:
            reopened.recover_transactions()
        assert caught.value.code == 'PROJECT_WRITER_BUSY'
        assert reopened.recover_transactions(writer=writer) == []


def test_recovery_itself_can_crash_and_resume_idempotently(project):
    before = project.pv_head()
    crash(project, 'committed:receipts')
    crash(project, 'restored:memory', recovery=True)
    reopened = ProjectStore(project.root)
    with pytest.raises(LaneError):
        values(reopened)
    assert reopened.recover_transactions()[0]['phase'] == 'aborted'
    assert reopened.pv_head() == before and values(reopened) == ['old', 'old']


@pytest.mark.parametrize('tamper', ['hash', 'path', 'wrong_lane'])
def test_all_recovery_evidence_is_checked_before_any_restore(project, tamper):
    paths = [project.lane(lane).database for lane in ('plan', 'memory', 'receipts')]
    crash(project, 'committed:receipts')
    with sqlite3.connect(project.database) as connection:
        row = connection.execute("SELECT commit_id,body_json FROM root_transaction_journal WHERE phase='prepared'").fetchone()
        body = json.loads(row[1])
        item = body['lanes'][-1]
        if tamper == 'hash':
            item['before_sha256'] = '0' * 64
        elif tamper == 'path':
            item['backup'] = '../source/untouched.sqlite'
        else:
            item['database'] = body['lanes'][0]['database']
        connection.execute('UPDATE root_transaction_journal SET body_json=? WHERE commit_id=?', (json.dumps(body), row[0]))
    before = [path.read_bytes() for path in paths]
    with pytest.raises(LaneError) as error:
        ProjectStore(project.root).recover_transactions()
    assert error.value.code == 'RECOVERY_EVIDENCE_INVALID'
    assert [path.read_bytes() for path in paths] == before


def test_nested_savepoint_rolls_back_all_lanes_and_receipts_together(project):
    before = project.pv_head()
    with project.coordinated_transaction(['plan', 'memory']) as commit:
        with pytest.raises(ValueError), project.lane('plan').transaction() as connection:
            connection.execute("UPDATE sample SET value='wrong'")
            project.lane('memory').put_object(b'unpublished orphan')
            project.append_receipt('nested_wrong', {}, connection=connection)
            raise ValueError('cancel nested operation')
        with pytest.raises(LaneError) as error:
            project.lane('canon', create=True)
        assert error.value.code == 'LANE_NOT_ENLISTED'
        with pytest.raises(sqlite3.DatabaseError):
            commit.connection('plan').commit()
    assert values(project) == ['old', 'old']
    assert project.pv_head()['revision'] == before['revision'] + 1
    with project.lane('memory').connection(read_only=True) as connection:
        assert connection.execute('SELECT count(*) FROM objects').fetchone()[0] == 0
    with project.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='nested_wrong'").fetchone()[0] == 0


def test_stale_revision_and_expiring_writer_cannot_publish(project):
    before = project.pv_head()
    with pytest.raises(LaneError) as error, project.coordinated_transaction(['plan'], expected_revision=before['revision'] - 1):
        pass
    assert error.value.code == 'STALE_ROOT_REVISION'
    current = datetime(2026, 9, 5, tzinfo=UTC)
    with WriterLease(project, 'engine', seconds=2, clock=lambda: current) as writer:
        before = project.pv_head()
        with pytest.raises(LaneError) as error, writer.coordinated_transaction(['plan', 'memory']) as commit:
            change(project, commit)
            current += timedelta(seconds=3)
        assert error.value.code == 'WRITER_LEASE_EXPIRED'
        assert project.pv_head() == before
        assert values(project) == ['old', 'old']


def test_one_writer_lock_and_out_of_band_database_change_are_rejected(project):
    with WriterLease(project, 'engine'):
        with pytest.raises(LaneError) as error, project.coordinated_transaction(['plan']):
            pass
        assert error.value.code == 'PROJECT_WRITER_BUSY'
    lane = project.lane('memory')
    with pytest.raises(LaneError), lane.connection(read_only=False):
        pass
    with sqlite3.connect(lane.database) as connection:
        connection.execute("UPDATE sample SET value='unpublished'")
    with pytest.raises(LaneError) as error:
        values(project)
    assert error.value.code == 'LANE_HEAD_MISMATCH'


def test_reader_pins_one_root_generation_across_sequential_lane_reads(project):
    first_read = threading.Event()
    writer_started = threading.Event()
    finish_read = threading.Event()
    committed = threading.Event()

    def reader():
        with bounded_project_read(project.root, time.monotonic() + 5):
            with project.lane('plan').connection(read_only=True) as connection:
                one = connection.execute('SELECT value FROM sample').fetchone()[0]
            first_read.set()
            assert finish_read.wait(3)
            with project.lane('memory').connection(read_only=True) as connection:
                two = connection.execute('SELECT value FROM sample').fetchone()[0]
            return one, two

    def writer():
        writer_started.set()
        with project.coordinated_transaction(['plan', 'memory']) as commit:
            change(project, commit)
        committed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        observed = pool.submit(reader)
        assert first_read.wait(3)
        written = pool.submit(writer)
        assert writer_started.wait(3)
        assert not committed.wait(0.1)
        finish_read.set()
        assert observed.result(timeout=4) == ('old', 'old')
        written.result(timeout=4)
    assert values(project) == ['complete', 'complete']


def test_reader_waiting_during_partial_lane_commit_sees_only_final_generation(project):
    partial = threading.Event()
    release = threading.Event()

    def fault(phase):
        if phase == 'committed:memory':
            partial.set()
            assert release.wait(3)

    def writer():
        with project.coordinated_transaction(['plan', 'memory'], fault=fault) as commit:
            change(project, commit)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(writer)
        assert partial.wait(3)
        reading = pool.submit(values, project)
        assert not reading.done()
        release.set()
        writing.result(timeout=4)
        assert reading.result(timeout=4) == ['complete', 'complete']


def test_all_retained_lanes_can_publish_in_one_root_revision(project):
    from evidence_lane_plugin.lanes import CANONICAL_LANE_IDS
    before = project.pv_head()
    with project.coordinated_transaction(CANONICAL_LANE_IDS) as commit:
        for lane_id in CANONICAL_LANE_IDS:
            commit.connection(lane_id).execute('CREATE TABLE batch_sample(value TEXT)')
            commit.connection(lane_id).execute('INSERT INTO batch_sample VALUES(?)', (lane_id,))
    assert project.pv_head()['revision'] == before['revision'] + 1
    assert {row['lane_id'] for row in project.lane_catalog()} == set(CANONICAL_LANE_IDS)
    assert {row['commit_id'] for row in project.lane_catalog()} == {commit.commit_id}


def test_fence_expiry_after_lane_commits_still_prevents_root_publication(project):
    current = datetime(2026, 9, 5, tzinfo=UTC)

    def fault(phase):
        nonlocal current
        if phase == 'committed:receipts':
            current += timedelta(seconds=3)

    with WriterLease(project, 'engine', seconds=2, clock=lambda: current) as writer:
        before = project.pv_head()
        with pytest.raises(LaneError) as error, writer.coordinated_transaction(['plan', 'memory'], fault=fault) as commit:
            change(project, commit)
        assert error.value.code == 'WRITER_LEASE_EXPIRED'
        assert project.pv_head() == before
        assert values(project) == ['old', 'old']


def test_normal_exception_after_partial_commit_restores_before_returning(project):
    before = project.pv_head()

    def fault(phase):
        if phase == 'committed:memory':
            raise ValueError('fixture interruption')

    with pytest.raises(ValueError), project.coordinated_transaction(['plan', 'memory'], fault=fault) as commit:
        change(project, commit)
    assert project.pv_head() == before and values(project) == ['old', 'old']
    assert project.recover_transactions() == []


def test_snapshot_cannot_be_released_by_nested_consumer_and_honors_shorter_budget(project):
    with bounded_project_read(project.root, time.monotonic() + 5):
        with project.connection(read_only=True) as connection:
            connection.execute('BEGIN')
            with pytest.raises(sqlite3.DatabaseError):
                connection.commit()
        with pytest.raises(LaneError) as error, bounded_project_read(project.root, time.monotonic() - 1):
            project.pv_head()
        assert error.value.code == 'QUERY_TIMEOUT'
        assert project.pv_head()['revision'] > 0


def test_caller_cannot_publish_its_own_root_head_or_rebind_a_lane(project):
    before = project.pv_head()
    with pytest.raises(sqlite3.DatabaseError), project.coordinated_transaction(['plan']) as commit:
        commit.connection(None).execute('UPDATE root_pv_head SET revision=999 WHERE singleton=1')
    assert project.pv_head() == before
    with pytest.raises(LaneError) as error, project.coordinated_transaction(['memory']) as commit:
        commit.connection('memory').execute("UPDATE lane_identity SET lane_id='canon'")
    assert error.value.code == 'LANE_IDENTITY_MISMATCH'
    assert project.pv_head() == before and values(project) == ['old', 'old']
