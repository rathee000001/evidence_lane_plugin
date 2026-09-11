"""Project-owned durable jobs; uncertain external effects are never replayed."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from uuid import uuid4

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .registry import Contract
from .sdk import ActionRequest
from .storage import LaneStore, ProjectStore, json_text, now
from .writers import WriterLease

JOB_MIGRATIONS = (
    Migration("jobs", 1, "Durable requests, worker checkpoints and effect journal", (
        """CREATE TABLE jobs_jobs (
            job_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
            client_id TEXT NOT NULL, request_digest TEXT NOT NULL,
            request_json TEXT NOT NULL CHECK(json_valid(request_json)),
            action TEXT NOT NULL, plan_revision INTEGER,
            state TEXT NOT NULL CHECK(state IN
                ('queued','running','checkpointed','cancelled','succeeded','failed','uncertain','superseded')),
            owner_fence INTEGER, execution_id TEXT, phase TEXT NOT NULL DEFAULT 'admitted',
            checkpoint_object TEXT REFERENCES objects(digest), resumable INTEGER NOT NULL DEFAULT 0,
            cancellation_reason TEXT, result_json TEXT CHECK(json_valid(result_json)),
            error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        "CREATE INDEX jobs_queue_order ON jobs_jobs(state,created_at,job_id)",
        """CREATE TABLE jobs_effects (
            effect_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs_jobs(job_id),
            effect_key TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','confirmed','absent')),
            description TEXT NOT NULL, evidence_object TEXT REFERENCES objects(digest),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(job_id,effect_key))""",
        "CREATE INDEX jobs_effect_state ON jobs_effects(job_id,state)",
    )),
)


class JobClaim(Contract):
    job_id: str
    execution_id: str
    owner_fence: int
    request: ActionRequest


class JobQueue:
    def __init__(self, store: ProjectStore):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('plan')

    def initialize(self, lease: WriterLease) -> None:
        self._lease(lease)
        apply_migrations(self.store, JOB_MIGRATIONS, writer=lease)

    def _lease(self, lease: WriterLease) -> None:
        if lease.store.project_id != self.store.project_id or lease.store.root != self.store.root:
            raise LaneError("WRITER_PROJECT_MISMATCH", "The writer belongs to another project.")
        lease.check()

    @staticmethod
    def _row(connection, job_id: str):
        row = connection.execute("SELECT * FROM jobs_jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise LaneError("JOB_NOT_FOUND", "This job does not exist in the selected project.")
        return row

    def _running(self, connection, job_id: str, lease: WriterLease, execution_id: str):
        row = self._row(connection, job_id)
        if row["state"] != "running" or row["owner_fence"] != lease.fence or row["execution_id"] != execution_id:
            raise LaneError("JOB_OWNER_CHANGED", "This execution no longer owns the running job.")
        return row

    @staticmethod
    def _pending_effect(connection, job_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM jobs_effects WHERE job_id=? AND state='prepared' LIMIT 1", (job_id,),
        ).fetchone() is not None

    def enqueue(self, request: ActionRequest, client_id: str, lease: WriterLease, *, transaction=None,
                max_request_bytes: int = 1_000_000) -> str:
        self._lease(lease)
        if request.project_id != self.store.project_id or not client_id or len(client_id) > 128:
            raise LaneError("JOB_BINDING_MISMATCH", "A selected project and client are required.")
        encoded = request.model_dump_json()
        if type(max_request_bytes) is not int or not 1 <= max_request_bytes <= 67_174_400:
            raise LaneError('INVALID_JOB_BUDGET', 'The admitted request needs a bounded queue budget.')
        if len(encoded.encode()) > max_request_bytes:
            raise LaneError("JOB_TOO_LARGE", "The action request exceeds its queue budget.")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with (lease.transaction('plan') if transaction is None else nullcontext(transaction)) as connection:
            self.store.require_transaction(connection)
            lease.check(connection)
            existing = connection.execute(
                "SELECT job_id,request_digest,client_id FROM jobs_jobs WHERE request_id=?",
                (request.request_id,),
            ).fetchone()
            if existing:
                if existing["request_digest"] != digest or existing["client_id"] != client_id:
                    raise LaneError("REQUEST_ID_CONFLICT", "This request ID belongs to different work.")
                return existing["job_id"]
            job_id = str(uuid4())
            timestamp = now()
            connection.execute(
                "INSERT INTO jobs_jobs(job_id,request_id,client_id,request_digest,request_json,action,"
                "plan_revision,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,'queued',?,?)",
                (job_id, request.request_id, client_id, digest, encoded, request.action,
                 request.expected_revision, timestamp, timestamp),
            )
            self.store.append_receipt("job_admitted", {"job_id": job_id,
                "request_id": request.request_id, "action": request.action,
                "client_id": client_id, "plan_revision": request.expected_revision}, connection=connection)
        return job_id

    def claim(self, job_id: str, lease: WriterLease) -> JobClaim:
        self._lease(lease)
        with lease.transaction('plan') as connection:
            row = self._row(connection, job_id)
            if row["state"] not in {"queued", "checkpointed"} or (
                row["state"] == "checkpointed" and not row["resumable"]
            ) or self._pending_effect(connection, job_id):
                raise LaneError("JOB_NOT_RUNNABLE", "Reconcile this job before requesting execution.")
            if row["cancellation_reason"]:
                raise LaneError("JOB_CANCELLED", "This job has a cancellation request.")
            execution_id = str(uuid4())
            connection.execute(
                "UPDATE jobs_jobs SET state='running',owner_fence=?,execution_id=?,updated_at=? "
                "WHERE job_id=?", (lease.fence, execution_id, now(), job_id),
            )
            return JobClaim(job_id=job_id, execution_id=execution_id, owner_fence=lease.fence,
                            request=ActionRequest.model_validate_json(row["request_json"]))

    def request_cancel(self, job_id: str, reason: str, lease: WriterLease) -> None:
        self._lease(lease)
        if not reason.strip() or len(reason) > 1000:
            raise LaneError("INVALID_CANCELLATION", "Provide a bounded cancellation reason.")
        with lease.transaction('plan') as connection:
            row = self._row(connection, job_id)
            if row["state"] not in {"queued", "running", "checkpointed"}:
                return
            state = "cancelled" if row["state"] != "running" else "running"
            connection.execute(
                "UPDATE jobs_jobs SET cancellation_reason=?,state=?,updated_at=? WHERE job_id=?",
                (reason, state, now(), job_id),
            )
            self.store.append_receipt("job_cancel_requested", {"job_id": job_id, "reason": reason},
                                      connection=connection)

    def checkpoint(self, job_id: str, lease: WriterLease, *, phase: str,
                   object_digest: str, resumable: bool, execution_id: str, pause: bool = False, transaction=None) -> str:
        self._lease(lease)
        if not phase or len(phase) > 200:
            raise LaneError("INVALID_CHECKPOINT", "Provide a bounded execution phase.")
        self.store.read_object(object_digest)
        with (lease.transaction('plan') if transaction is None else nullcontext(transaction)) as connection:
            self.store.require_transaction(connection)
            lease.check(connection)
            row = self._running(connection, job_id, lease, execution_id)
            pending = self._pending_effect(connection, job_id)
            if pending and (pause or row["cancellation_reason"]):
                state, resumable = "uncertain", False
            elif row["cancellation_reason"]:
                state = "cancelled"
            else:
                state = "checkpointed" if pause else "running"
            connection.execute(
                "UPDATE jobs_jobs SET phase=?,checkpoint_object=?,resumable=?,state=?,updated_at=? "
                "WHERE job_id=?", (phase, object_digest, int(resumable), state, now(), job_id),
            )
            return state

    def prepare_effect(self, job_id: str, key: str, description: str, lease: WriterLease, *, execution_id: str) -> str:
        self._lease(lease)
        if not key or len(key) > 200 or not description or len(description) > 1000:
            raise LaneError("INVALID_EFFECT", "Provide a bounded effect key and description.")
        with lease.transaction('plan') as connection:
            row = self._running(connection, job_id, lease, execution_id)
            if row["cancellation_reason"]:
                raise LaneError("JOB_CANCELLED", "Stop before starting another external effect.")
            existing = connection.execute(
                "SELECT state FROM jobs_effects WHERE job_id=? AND effect_key=?", (job_id, key),
            ).fetchone()
            if existing:
                raise LaneError("EFFECT_ALREADY_RECORDED", "Inspect the recorded outcome; do not replay it.")
            effect_id = str(uuid4())
            timestamp = now()
            connection.execute("INSERT INTO jobs_effects VALUES(?,?,?,'prepared',?,NULL,?,?)",
                               (effect_id, job_id, key, description, timestamp, timestamp))
            return effect_id

    def confirm_effect(self, job_id: str, effect_id: str, evidence: str, lease: WriterLease, *, execution_id: str) -> None:
        self._lease(lease)
        self.store.read_object(evidence)
        with lease.transaction('plan') as connection:
            self._running(connection, job_id, lease, execution_id)
            result = connection.execute(
                "UPDATE jobs_effects SET state='confirmed',evidence_object=?,updated_at=? "
                "WHERE effect_id=? AND job_id=? AND state='prepared'", (evidence, now(), effect_id, job_id),
            )
            if result.rowcount != 1:
                raise LaneError("EFFECT_STATE_CHANGED", "The effect is not pending in this job.")

    def complete(self, job_id: str, result: dict, lease: WriterLease, *, execution_id: str, transaction=None) -> None:
        self._lease(lease)
        encoded = json_text(result)
        if len(encoded.encode()) > 1_000_000:
            raise LaneError("RESULT_TOO_LARGE", "Store large outputs as addressed objects.")
        with (lease.transaction('plan') if transaction is None else nullcontext(transaction)) as connection:
            self.store.require_transaction(connection)
            lease.check(connection)
            row = self._running(connection, job_id, lease, execution_id)
            if self._pending_effect(connection, job_id):
                raise LaneError("EFFECT_OUTCOME_UNCERTAIN", "Confirm pending effects before completing.")
            if row["cancellation_reason"]:
                raise LaneError("JOB_CANCELLED", "A cancelled execution cannot report success.")
            connection.execute("UPDATE jobs_jobs SET state='succeeded',result_json=?,updated_at=? WHERE job_id=?",
                               (encoded, now(), job_id))
            self.store.append_receipt("job_completed", {"job_id": job_id}, connection=connection)

    def fail(self, job_id: str, code: str, lease: WriterLease, *, execution_id: str) -> None:
        self._lease(lease)
        if not code or len(code) > 100:
            raise LaneError("INVALID_FAILURE", "Provide a bounded failure code.")
        with lease.transaction('plan') as connection:
            self._running(connection, job_id, lease, execution_id)
            state = "uncertain" if self._pending_effect(connection, job_id) else "failed"
            connection.execute("UPDATE jobs_jobs SET state=?,error_code=?,updated_at=? WHERE job_id=?",
                               (state, code, now(), job_id))

    def reconcile_interrupted(self, lease: WriterLease) -> list[dict]:
        self._lease(lease)
        outcomes = []
        with lease.transaction('plan') as connection:
            rows = connection.execute("SELECT * FROM jobs_jobs WHERE state='running' AND owner_fence!=?",
                                      (lease.fence,)).fetchall()
            for row in rows:
                any_effect = connection.execute(
                    "SELECT 1 FROM jobs_effects WHERE job_id=? AND state!='absent' LIMIT 1", (row["job_id"],),
                ).fetchone()
                if any_effect:
                    state = "uncertain"
                elif row["cancellation_reason"]:
                    state = "cancelled"
                else:
                    state = "checkpointed" if row["checkpoint_object"] and row["resumable"] else "failed"
                connection.execute("UPDATE jobs_jobs SET state=?,error_code='EXECUTION_INTERRUPTED',updated_at=? WHERE job_id=?",
                                   (state, now(), row["job_id"]))
                outcome = {"job_id": row["job_id"], "state": state, "automatic_replay": False}
                outcomes.append(outcome)
                self.store.append_receipt("job_reconciled", outcome, connection=connection)
        return outcomes

    def reconcile_effect(self, job_id: str, effect_id: str, outcome: str,
                         evidence: str, lease: WriterLease) -> None:
        self._lease(lease)
        if outcome not in {"confirmed", "absent"}:
            raise LaneError("INVALID_RECONCILIATION", "Supply an observed effect outcome.")
        self.store.read_object(evidence)
        with lease.transaction('plan') as connection:
            row = self._row(connection, job_id)
            if row["state"] != "uncertain":
                raise LaneError("JOB_STATE_CHANGED", "This job does not require effect reconciliation.")
            updated = connection.execute(
                "UPDATE jobs_effects SET state=?,evidence_object=?,updated_at=? WHERE effect_id=? AND job_id=?",
                (outcome, evidence, now(), effect_id, job_id),
            )
            if updated.rowcount != 1:
                raise LaneError("EFFECT_NOT_FOUND", "This effect does not belong to the job.")
            self.store.append_receipt("effect_reconciled", {"job_id": job_id, "effect_id": effect_id,
                "outcome": outcome, "evidence_object": evidence}, connection=connection)
            # Reconciliation records the effect; it cannot claim the entire job succeeded.
            if not self._pending_effect(connection, job_id):
                connection.execute("UPDATE jobs_jobs SET state='failed',error_code='RECONCILED_REPLAN_REQUIRED',updated_at=? WHERE job_id=?",
                                   (now(), job_id))

    def get(self, job_id: str, *, transaction=None) -> dict:
        with (self.store.connection(read_only=True) if transaction is None else nullcontext(transaction)) as connection:
            row = dict(self._row(connection, job_id))
            row.pop("request_json")
            row.pop("request_digest")
            row["result"] = json.loads(row.pop("result_json")) if row["result_json"] else None
            row.pop("result_json", None)
            row["effects"] = [dict(item) for item in connection.execute(
                "SELECT * FROM jobs_effects WHERE job_id=? ORDER BY created_at", (job_id,),
            )]
            return row

    def list(self, *, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 500:
            raise LaneError("INVALID_LIMIT", "Select between 1 and 500 jobs.")
        with self.store.connection(read_only=True) as connection:
            return [dict(row) for row in connection.execute(
                "SELECT job_id,action,state,phase,plan_revision,updated_at FROM jobs_jobs "
                "ORDER BY created_at DESC,job_id LIMIT ?", (limit,),
            )]
