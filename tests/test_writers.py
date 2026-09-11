from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease


def project(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    return ProjectStore.create(tmp_path / "state", source)


def test_one_writer_fences_each_successor(tmp_path):
    store = project(tmp_path)
    with WriterLease(store, "engine-one") as first:
        with pytest.raises(LaneError) as error:
            WriterLease(store, "engine-two").acquire()
        assert error.value.code == "PROJECT_WRITER_BUSY"
        first.check()
        first_fence = first.fence
    with WriterLease(store, "engine-two") as second:
        assert second.fence == first_fence + 1
        with pytest.raises(LaneError) as error:
            first.check()
        assert error.value.code == "WRITER_NOT_HELD"


def test_expiry_does_not_allow_takeover_while_owner_still_holds_kernel_lock(tmp_path):
    store = project(tmp_path)
    current = datetime(2026, 9, 5, tzinfo=UTC)
    with WriterLease(store, "engine", seconds=2, clock=lambda: current) as lease:
        current += timedelta(seconds=3)
        with pytest.raises(LaneError) as error:
            lease.heartbeat()
        assert error.value.code == "WRITER_LEASE_EXPIRED"
        with pytest.raises(LaneError) as error:
            WriterLease(store, "engine-new").acquire()
        assert error.value.code == "PROJECT_WRITER_BUSY"


def test_fenced_transaction_rolls_back_if_lease_expires_before_commit(tmp_path):
    store = project(tmp_path)
    current = datetime(2026, 9, 5, tzinfo=UTC)
    with (
        WriterLease(store, "engine", seconds=2, clock=lambda: current) as lease,
        pytest.raises(LaneError), lease.transaction() as connection,
    ):
        store.append_receipt("must_rollback", {}, connection=connection)
        current += timedelta(seconds=3)
    with store.lane('receipts').connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM receipts WHERE kind='must_rollback'").fetchone()[0] == 0


def test_crashed_writer_recovery_uses_lock_not_pid_or_elapsed_lease(tmp_path):
    store = project(tmp_path)
    code = (
        "import os,sys; from pathlib import Path; "
        "from evidence_lane_plugin.storage import ProjectStore; "
        "from evidence_lane_plugin.writers import WriterLease; "
        "w=WriterLease(ProjectStore(Path(sys.argv[1])),'dead-engine',seconds=3600); "
        "w.acquire(); os._exit(19)"
    )
    source = Path(__file__).parents[1] / "plugins/evidence-lane-plugin/src"
    result = subprocess.run([sys.executable, "-c", code, str(store.root)],
                            env=dict(os.environ, PYTHONPATH=str(source)), check=False, timeout=15)
    assert result.returncode == 19
    with WriterLease(store, "replacement") as replacement:
        assert replacement.fence == 2
        replacement.check()
    with store.lane('receipts').connection(read_only=True) as connection:
        body = connection.execute("SELECT body_json FROM receipts WHERE kind='writer_acquired' ORDER BY created_at DESC").fetchone()[0]
        assert '"recovered_owner":null' not in body
