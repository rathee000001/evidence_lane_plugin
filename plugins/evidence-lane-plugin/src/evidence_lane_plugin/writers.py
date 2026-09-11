"""One live writer per selected project, with durable fencing and leases."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Self
from uuid import uuid4

from .errors import LaneError
from .locking import RuntimeLock
from .migrations import Migration, apply_migrations
from .storage import LaneStore, ProjectStore

WRITER_MIGRATIONS = (
    Migration("writer", 1, "Single writer fence and expiry", (
        """CREATE TABLE writer_lease (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1),
            fence INTEGER NOT NULL CHECK(fence>=1), owner_id TEXT,
            engine_id TEXT, expires_at TEXT)""",
    )),
)


class WriterLease:
    def __init__(self, store: ProjectStore, engine_id: str, *, seconds: int = 60, clock=None):
        if store.read_only:
            raise LaneError("READ_ONLY_PROJECT", "Writer ownership requires a writable project.")
        if isinstance(store, LaneStore):
            store = store.project
        if not engine_id or not 1 <= seconds <= 3600:
            raise LaneError("INVALID_LEASE", "An engine owner and bounded lease lifetime are required.")
        self.store = store
        self.engine_id = engine_id
        self.owner_id = str(uuid4())
        self.seconds = seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self.lock = RuntimeLock(store.root / "writer.lock")
        self.fence: int | None = None
        self.acquisition_root_pv: dict | None = None
        self._acquiring = False

    def acquire(self) -> None:
        try:
            self.lock.acquire()
        except LaneError as error:
            if error.code == "RUNTIME_IN_USE":
                raise LaneError("PROJECT_WRITER_BUSY", "Another writer owns this project.") from None
            raise
        try:
            self._acquiring = True
            self.store.recover_transactions(writer=self)
            self.store.assert_current_binding()
            with self.coordinated_transaction([]) as commit:
                connection = commit.connection(None)
                # Exact published reference under the kernel writer lock, before
                # this acquisition's own receipt changes the Receipts lane head.
                self.acquisition_root_pv = dict(connection.execute('SELECT * FROM root_pv_head WHERE singleton=1').fetchone())
                apply_migrations(self.store, WRITER_MIGRATIONS)
                previous = connection.execute("SELECT * FROM writer_lease WHERE singleton=1").fetchone()
                fence = previous["fence"] + 1 if previous else 1
                connection.execute(
                    "INSERT OR REPLACE INTO writer_lease VALUES(1,?,?,?,?)",
                    (fence, self.owner_id, self.engine_id, self._expiry()),
                )
                self.store.append_receipt("writer_acquired", {
                    "owner_id": self.owner_id, "engine_id": self.engine_id, "fence": fence,
                    "recovered_owner": previous["owner_id"] if previous else None,
                    "recovery_basis": "exclusive_kernel_lock",
                }, connection=connection)
                self.fence = fence
        except BaseException:
            self.fence = None
            self.lock.release()
            raise
        finally:
            self._acquiring = False

    def check_commit(self, connection):
        if self._acquiring and self.fence is None and self.lock.stream is not None:
            return
        self.check(connection)

    def coordinated_transaction(self, lanes, *, expected_revision=None, fault=None):
        return self.store.coordinated_transaction(lanes, writer=self,
                                                 expected_revision=expected_revision, fault=fault)

    def _expiry(self) -> str:
        return (self.clock() + timedelta(seconds=self.seconds)).isoformat()

    def check(self, connection=None) -> None:
        if self.lock.stream is None or self.fence is None:
            raise LaneError("WRITER_NOT_HELD", "This worker no longer owns the project writer.")
        # A caller may be holding a business-lane connection. The writer fence
        # exists only in project evidence head coordinator, never in each authority's schema.
        if connection is None or not getattr(connection, 'root_only', False):
            with self.store.connection(read_only=True) as selected:
                self.check(selected)
            return
        row = connection.execute("SELECT * FROM writer_lease WHERE singleton=1").fetchone()
        if row is None or row["owner_id"] != self.owner_id or row["fence"] != self.fence:
            raise LaneError("STALE_WRITER", "A newer writer fence invalidated this worker.")
        if row["expires_at"] is None or datetime.fromisoformat(row["expires_at"]) <= self.clock():
            raise LaneError("WRITER_LEASE_EXPIRED", "This writer lease expired; stop work and reconcile.")

    def heartbeat(self) -> None:
        with self.transaction() as connection:
            connection.execute("UPDATE writer_lease SET expires_at=? WHERE singleton=1", (self._expiry(),))

    @contextmanager
    def transaction(self, lane_id=None, *, additional_lanes=()):
        lanes = ([lane_id] if lane_id is not None else []) + list(additional_lanes)
        with self.coordinated_transaction(lanes) as commit:
            connection = commit.connection(lane_id)
            self.check(commit.connection(None))
            yield connection
            self.check(commit.connection(None))

    def release(self) -> None:
        if self.lock.stream is None:
            return
        try:
            with self.store.transaction() as connection:
                connection.execute(
                    "UPDATE writer_lease SET owner_id=NULL,engine_id=NULL,expires_at=NULL "
                    "WHERE singleton=1 AND owner_id=? AND fence=?", (self.owner_id, self.fence),
                )
        finally:
            self.lock.release()

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()
