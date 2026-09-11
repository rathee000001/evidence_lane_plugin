"""Visible-message interpretation separated from authoritative Plan mutation.

Extracts the imported store's stable steer IDs, explicit affected-task binding
and exact visible-message provenance. A model interpretation alone never
advances a row or replaces the Plan; the coordinator must checkpoint and apply.
"""

from __future__ import annotations

import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, model_validator

from .errors import LaneError
from .lineage import ChatLineage
from .migrations import Migration, apply_migrations
from .plan_runtime import PlanStore, content_digest
from .redaction import redact_text
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, ProjectStore, json_text, now
from .writers import WriterLease


class SteerIntent(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    source_event_id: str = Field(pattern=UUID_PATTERN)
    source_cursor: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=1)
    intent: Literal["informational", "semantic", "stop"]
    rationale: str = Field(min_length=1, max_length=2000)
    affected_task_ids: list[str] = Field(default_factory=list, max_length=2000)
    source_text: str | None = Field(default=None, max_length=65536)

    @model_validator(mode="after")
    def valid_effect(self):
        if len(set(self.affected_task_ids)) != len(self.affected_task_ids):
            raise ValueError("Select each affected task once")
        if self.intent == "informational" and self.affected_task_ids:
            raise ValueError("An informational query cannot propose task changes")
        return self


class SteerAssessment(Contract):
    request_id: str
    intent: str
    plan_revision: int
    source_event_id: str
    source_cursor: str
    source_provenance: str
    affected_task_ids: list[str]
    active_task_id: str | None
    checkpoint_required: bool
    classification_basis: Literal["agent_interpretation_of_visible_message", "agent_interpretation_of_hash_verified_visible_source", "captured_stop_signal"] = "agent_interpretation_of_visible_message"
    plan_changed: Literal[False] = False
    disposition: Literal["read_only", "requires_checkpoint_and_revision", "requires_safe_stop"]


class SteerQueued(Contract):
    request_id: str
    state: str
    expected_revision: int
    duplicate: bool
    plan_changed: bool = False
    plan_revision_changed: Literal[False] = False
    checkpoint_object: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')


STEER_MIGRATIONS = (Migration("steer", 1, "Exact visible-message steers awaiting safe project checkpoints", (
    """CREATE TABLE steer_requests (
        request_id TEXT PRIMARY KEY, source_event_id TEXT NOT NULL, source_cursor TEXT NOT NULL,
        actor_id TEXT NOT NULL, expected_revision INTEGER NOT NULL, intent TEXT NOT NULL CHECK(intent IN ('semantic','stop')),
        affected_json TEXT NOT NULL CHECK(json_valid(affected_json)), rationale TEXT NOT NULL,
        identity_digest TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN
           ('pending','checkpointing','ready','applied','rejected','superseded')),
        checkpoint_object TEXT REFERENCES objects(digest), applied_revision INTEGER,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX steer_pending_order ON steer_requests(state,created_at,request_id)",
)), Migration("steer", 2, "Persistent project stop without automatic continuation", (
    """CREATE TABLE steer_control (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), paused INTEGER NOT NULL CHECK(paused IN (0,1)),
        source_event_id TEXT NOT NULL, source_cursor TEXT NOT NULL, actor_id TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)), Migration("steer", 3, "Idempotent interrupt consumption across explicit later resumes", (
    """CREATE TABLE steer_interrupts (
        event_id TEXT PRIMARY KEY, source_cursor TEXT NOT NULL, actor_id TEXT NOT NULL,
        receipt_id TEXT NOT NULL, created_at TEXT NOT NULL)""",
)))


class Steering:
    def __init__(self, store: ProjectStore):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('plan')
        self.plan = PlanStore(store)

    def _assess(self, connection, request: SteerIntent, *, actor_id: str) -> SteerAssessment:
        self.plan._head(connection, request.expected_revision)
        lineage_store = self.project.lane('chat_lineage')
        with lineage_store.connection(read_only=True) as lineage:
            if not lineage.execute("SELECT 1 FROM sqlite_schema WHERE name='lineage_events' AND type='table'").fetchone():
                raise LaneError("STEER_SOURCE_REQUIRED", "Capture the exact visible user message before proposing a steer.")
            source = lineage.execute("SELECT * FROM lineage_events WHERE event_id=?", (request.source_event_id,)).fetchone()
        if (source is None or source["cursor"] != request.source_cursor
                or source["kind"] not in {"prompt", "steer", "interrupt"} or source["client_id"] != actor_id):
            raise LaneError("STEER_SOURCE_MISMATCH", "The steer must bind this client's captured visible input and exact cursor.")
        payload = json.loads(lineage_store.read_object(source["payload_digest"]))
        ChatLineage.validate_row(source)
        from .capture_routing import CAPTURE_ENVELOPE, resolve_visible_source
        sparse = CAPTURE_ENVELOPE in payload
        payload = resolve_visible_source(payload, request.source_text)
        if payload.get('truncated') and source['kind'] != 'interrupt':
            raise LaneError('STEER_SOURCE_TRUNCATED', 'Interpret only a complete captured prompt or exact bounded source segment.')
        active = connection.execute("SELECT task_id FROM plan_tasks WHERE revision=? AND state='active'", (request.expected_revision,)).fetchone()
        active_id = active["task_id"] if active else None
        affected = list(request.affected_task_ids)
        if request.intent == "stop" and active_id and active_id not in affected:
            affected.append(active_id)
        for task_id in affected:
            task = self.plan._task(connection, request.expected_revision, task_id)
            if task["state"] in {"completed", "cancelled", "superseded"}:
                raise LaneError("STEER_HISTORY_IMMUTABLE", "Keep completed and terminal history; propose new work for a changed outcome.")
        return SteerAssessment(request_id=request.request_id, intent=request.intent, plan_revision=request.expected_revision,
            source_event_id=request.source_event_id, source_cursor=request.source_cursor, source_provenance=source["provenance"],
            affected_task_ids=affected, active_task_id=active_id,
            checkpoint_required=active_id is not None and request.intent != "informational",
            classification_basis="captured_stop_signal" if source["kind"] == "interrupt" and request.intent == "stop"
                                 else "agent_interpretation_of_hash_verified_visible_source" if sparse
                                 else "agent_interpretation_of_visible_message",
            disposition={"informational": "read_only", "semantic": "requires_checkpoint_and_revision", "stop": "requires_safe_stop"}[request.intent])

    def preview(self, request: SteerIntent, *, actor_id: str) -> SteerAssessment:
        request = SteerIntent.model_validate(request.model_dump())
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            return self._assess(connection, request, actor_id=actor_id)

    def submit(self, request: SteerIntent, lease: WriterLease, *, actor_id: str) -> SteerQueued:
        request = SteerIntent.model_validate(request.model_dump())
        if request.intent == "informational":
            raise LaneError("INFORMATIONAL_QUERY_READ_ONLY", "Use the read-only preview/query route for information requests.")
        self.plan._lease(lease)
        # Validate against a read snapshot before initializing any new owner schema.
        self.preview(request, actor_id=actor_id)
        apply_migrations(self.store, STEER_MIGRATIONS, writer=lease)
        with lease.transaction('plan', additional_lanes=['chat_lineage']) as connection:
            assessment = self._assess(connection, request, actor_id=actor_id)
            identity = {**request.model_dump(mode="json", exclude={'source_text'}), "actor_id": actor_id,
                        "rationale": redact_text(request.rationale)}
            digest = content_digest(identity)
            existing = connection.execute("SELECT * FROM steer_requests WHERE request_id=?", (request.request_id,)).fetchone()
            if existing:
                if existing["identity_digest"] != digest:
                    raise LaneError("STEER_REQUEST_CONFLICT", "This steer ID already identifies a different requested change.")
                return SteerQueued(request_id=request.request_id, state=existing["state"], expected_revision=request.expected_revision, duplicate=True)
            timestamp = now()
            connection.execute("INSERT INTO steer_requests VALUES(?,?,?,?,?,?,?,?,?,'pending',NULL,NULL,?,?)",
                (request.request_id, request.source_event_id, request.source_cursor, actor_id, request.expected_revision,
                 request.intent, json_text(assessment.affected_task_ids), identity["rationale"], digest, timestamp, timestamp))
            if request.intent == "stop":
                self._pause(connection, request.source_event_id, request.source_cursor, actor_id)
            self.store.append_receipt("steer_queued", {"request_id": request.request_id, "source_event_id": request.source_event_id,
                "source_cursor": request.source_cursor, "expected_revision": request.expected_revision,
                "intent": request.intent, "plan_changed": False}, connection=connection)
        return SteerQueued(request_id=request.request_id, state="pending", expected_revision=request.expected_revision, duplicate=False)

    @staticmethod
    def _pause(connection, event_id: str, cursor: str, actor_id: str):
        connection.execute("INSERT OR REPLACE INTO steer_control VALUES(1,1,?,?,?,?)", (event_id, cursor, actor_id, now()))

    def record_interrupt(self, event_id: str, cursor: str, lease: WriterLease, *, actor_id: str):
        self.plan._lease(lease)
        apply_migrations(self.store, STEER_MIGRATIONS, writer=lease)
        with lease.transaction('plan', additional_lanes=['chat_lineage']) as connection:
            with self.project.lane('chat_lineage').connection(read_only=True) as lineage:
                source = lineage.execute("SELECT * FROM lineage_events WHERE event_id=?", (event_id,)).fetchone()
            if source is None or source["kind"] != "interrupt" or source["client_id"] != actor_id or source["cursor"] != cursor:
                raise LaneError("STEER_SOURCE_MISMATCH", "A stop must bind the exact recorded interruption.")
            ChatLineage.validate_row(source)
            previous = connection.execute("SELECT * FROM steer_interrupts WHERE event_id=?", (event_id,)).fetchone()
            if previous:
                if previous["source_cursor"] != cursor or previous["actor_id"] != actor_id:
                    raise LaneError("STEER_SOURCE_MISMATCH", "This interruption already binds a different source.")
                return
            self._pause(connection, event_id, cursor, actor_id)
            if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='jobs_jobs'").fetchone():
                connection.execute("UPDATE jobs_jobs SET cancellation_reason=?,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,updated_at=? "
                    "WHERE state IN ('running','queued','checkpointed')", ("Captured host interrupt", now()))
            receipt = self.store.append_receipt("project_interrupted", {"source_event_id": event_id, "source_cursor": cursor,
                                      "paused": True, "automatic_resume": False}, connection=connection)
            connection.execute("INSERT INTO steer_interrupts VALUES(?,?,?,?,?)", (event_id, cursor, actor_id, receipt, now()))

    def control(self) -> dict:
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_control'").fetchone():
                return {"paused": False, "source_event_id": None}
            row = connection.execute("SELECT paused,source_event_id,source_cursor,updated_at FROM steer_control WHERE singleton=1").fetchone()
            return {**dict(row), "paused": bool(row["paused"])} if row else {"paused": False, "source_event_id": None}

    def pending(self, *, limit: int = 100) -> list[dict]:
        if not 1 <= limit <= 100:
            raise LaneError("INVALID_STEER_LIMIT", "Select up to 100 pending steer records.")
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone():
                return []
            rows = connection.execute("SELECT request_id,source_event_id,source_cursor,actor_id,expected_revision,intent,affected_json,state,created_at "
                "FROM steer_requests WHERE state IN ('pending','checkpointing','ready') ORDER BY created_at,request_id LIMIT ?", (limit,)).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["affected_task_ids"] = json.loads(item.pop("affected_json"))
                result.append(item)
            return result


def register_steer_actions(engine):
    def submit(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store, kind="signal") as lease, lease.coordinated_transaction(['plan', 'chat_lineage', 'receipts']):
            result = Steering(store).submit(request, lease, actor_id=context.client_id)
            with store.lane('chat_lineage').connection(read_only=True) as connection:
                source = connection.execute('SELECT kind FROM lineage_events WHERE event_id=?', (request.source_event_id,)).fetchone()
            if source['kind'] in {'prompt', 'steer'}:
                from .prompt_index import EntryClassify, PromptIndex
                classification = EntryClassify(classification_id=request.request_id, source_event_id=request.source_event_id,
                    source_cursor=request.source_cursor, intent=request.intent, focus=request.rationale,
                    workflow='manage-project-plan', next_action='plan_refresh' if request.intent == 'semantic' else 'plan_read', lanes=['plan'],
                    source_text=request.source_text)
                PromptIndex(store).classify(classification, lease, client_id=context.client_id, registry=engine.registry)
            checkpoint = engine.project_work.checkpoint_idle_plan(store, lease,
                request_id=request.request_id, expected_revision=request.expected_revision, actor_id=context.client_id)
            if checkpoint is not None:
                return result.model_copy(update={'state': 'ready', 'checkpoint_object': checkpoint, 'plan_changed': True})
            return result

    engine.registry.register(ActionSpec("steer_preview", "Inspect a visible input's declared intent without changing Plan or project data.",
        SteerIntent, SteerAssessment, lambda context, request: Steering(engine.directory.open(context.project_id)).preview(request, actor_id=context.client_id),
        profile="plan", workflow='manage-project-plan'))
    engine.registry.register(ActionSpec("steer_submit", "Queue an exact visible change or stop; checkpoint an unstarted selected task, while admitted jobs keep their owning checkpoint route.",
        SteerIntent, SteerQueued, submit, permission="write", profile="plan", mutates=True, workflow='manage-project-plan'))
