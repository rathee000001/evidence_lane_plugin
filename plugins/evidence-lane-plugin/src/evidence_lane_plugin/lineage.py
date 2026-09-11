"""Visible ChatLineage in its own SQLite database and content-addressed files.

Retains the base's redaction, deterministic chunks, bounded FTS and revision
cursors. Capture provenance stays distinct from native task attestation, Plan
authority and learned conclusions. Private model reasoning is never admitted.
"""

from __future__ import annotations

import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .plan_runtime import content_digest
from .redaction import redact
from .registry import ActionSpec, Contract, SearchRoute
from .sdk import UUID_PATTERN
from .storage import LaneStore, ProjectStore, json_text, now
from .writers import WriterLease

LINEAGE_CHUNK_CHARS = 1024
MAX_VISIBLE_BYTES = 65_536
EventKind = Literal["prompt", "steer", "assistant", "tool_call", "tool_result", "session", "interrupt", "receipt", "handoff"]
PRIVATE_KEYS = frozenset({"chain_of_thought", "hidden_reasoning", "internal_reasoning", "private_reasoning", "reasoning_content"})


def visible_payload(value):
    """Reject private fields before redaction or hashing; omit media byte blocks."""
    if isinstance(value, dict):
        if any(str(key).lower().replace("-", "_") in PRIVATE_KEYS for key in value):
            raise LaneError("VISIBLE_CONTENT_ONLY", "ChatLineage accepts visible content and attributed results only.")
        if value.get("type") in {"image", "audio"} and any(key in value for key in ("data", "blob")):
            return {"type": value["type"], "binary_omitted": True, "mimeType": value.get("mimeType")}
        return {str(key): visible_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [visible_payload(item) for item in value]
    return value


class LineageRecord(Contract):
    event_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    kind: EventKind
    payload: dict[str, JsonValue]
    parent_event_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    reported_session_id: str | None = Field(default=None, min_length=1, max_length=128)
    reported_turn_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_use_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def bounded_visible_content(self):
        # Bound parsing before recursive redaction and avoid storing binary blobs.
        encoded = self.model_dump_json()
        if len(encoded.encode()) > 1_048_576:
            raise ValueError("Capture input exceeds one MiB")
        visible_payload(self.payload)
        return self


class LineageAppendResult(Contract):
    event_id: str
    sequence: int
    cursor: str
    payload_digest: str
    duplicate: bool
    provenance: str
    native_task_attestation: Literal["not_provided"] = "not_provided"


class LineageRead(Contract):
    match_mode: Literal['all', 'any'] = 'all'
    query: str | None = Field(default=None, min_length=1, max_length=500)
    after_sequence: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class LineagePage(Contract):
    project_id: str
    events: list[dict]
    total_events: int
    current_cursor: str | None
    last_sequence: int
    truncated: bool
    query_mode: Literal["sequence", "fts5"]
    authority: Literal["project_sqlite"] = "project_sqlite"


LINEAGE_MIGRATIONS = (Migration("lineage", 1, "Visible events, ancestry, cursors and searchable chunks", (
    """CREATE TABLE lineage_events (
        sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL, client_id TEXT NOT NULL, provenance TEXT NOT NULL,
        reported_session_id TEXT, reported_turn_id TEXT, tool_use_id TEXT,
        parent_event_id TEXT REFERENCES lineage_events(event_id),
        payload_digest TEXT NOT NULL REFERENCES objects(digest),
        identity_digest TEXT NOT NULL, previous_cursor TEXT, cursor TEXT NOT NULL UNIQUE,
        received_at TEXT NOT NULL)""",
    "CREATE INDEX lineage_event_links ON lineage_events(parent_event_id,sequence)",
    "CREATE INDEX lineage_tool_pairs ON lineage_events(reported_session_id,reported_turn_id,tool_use_id,sequence)",
    """CREATE TABLE lineage_chunks (
        chunk_id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES lineage_events(event_id),
        ordinal INTEGER NOT NULL, text_digest TEXT NOT NULL, text TEXT NOT NULL, UNIQUE(event_id,ordinal))""",
    "CREATE VIRTUAL TABLE lineage_fts USING fts5(chunk_id UNINDEXED,event_id UNINDEXED,text,tokenize='unicode61')",
)),)


class ChatLineage:
    def __init__(self, store: ProjectStore):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('chat_lineage')

    def _lease(self, lease):
        if lease.store.project_id != self.store.project_id or lease.store.root != self.store.root:
            raise LaneError("WRITER_PROJECT_MISMATCH", "The capture writer belongs to another project.")
        lease.check()

    def initialize(self, lease):
        self._lease(lease)
        apply_migrations(self.store, LINEAGE_MIGRATIONS, writer=lease)

    def append(self, record: LineageRecord, lease: WriterLease, *, client_id: str,
               provenance: Literal["agent_report", "owner_hook_channel", "engine_observed", "authenticated_remote_hook_report"] = "agent_report") -> LineageAppendResult:
        record = LineageRecord.model_validate(record.model_dump())
        self._lease(lease)
        if not client_id or len(client_id) > 128 or provenance not in {"agent_report", "owner_hook_channel", "engine_observed", "authenticated_remote_hook_report"}:
            raise LaneError("CAPTURE_PROVENANCE_INVALID", "Capture requires an engine-selected actor and source channel.")
        payload = redact(visible_payload(record.payload))
        encoded = json_text(payload).encode()
        if len(encoded) > MAX_VISIBLE_BYTES:
            raise LaneError("VISIBLE_CONTENT_TOO_LARGE", "Split visible capture into bounded, explicitly linked events.")
        from .capture_routing import CaptureRouteAuthority
        capture = CaptureRouteAuthority(self.project)
        payload, decision = capture.filter(payload, kind=record.kind)
        encoded = json_text(payload).encode()
        self.initialize(lease)
        import hashlib
        payload_digest = hashlib.sha256(encoded).hexdigest()
        identity = {"event_id": record.event_id, "kind": record.kind, "client_id": client_id,
                    "provenance": provenance, "reported_session_id": record.reported_session_id,
                    "reported_turn_id": record.reported_turn_id, "tool_use_id": record.tool_use_id,
                    "parent_event_id": record.parent_event_id, "payload_digest": payload_digest}
        identity_digest = content_digest(identity)
        with lease.transaction('chat_lineage') as connection:
            self.store.put_object(encoded, limit=MAX_VISIBLE_BYTES)
            existing = connection.execute("SELECT * FROM lineage_events WHERE event_id=?", (record.event_id,)).fetchone()
            if existing:
                self.validate_row(existing)
                if existing["identity_digest"] != identity_digest:
                    raise LaneError("LINEAGE_EVENT_CONFLICT", "This event ID is already bound to different visible content or provenance.")
                return LineageAppendResult(event_id=record.event_id, sequence=existing["sequence"], cursor=existing["cursor"],
                                           payload_digest=payload_digest, duplicate=True, provenance=provenance)
            if record.parent_event_id and not connection.execute(
                    "SELECT 1 FROM lineage_events WHERE event_id=?", (record.parent_event_id,)).fetchone():
                raise LaneError("LINEAGE_PARENT_NOT_FOUND", "An ancestry link must name an earlier event in this project.")
            previous = connection.execute("SELECT sequence,cursor FROM lineage_events ORDER BY sequence DESC LIMIT 1").fetchone()
            sequence = previous["sequence"] + 1 if previous else 1
            timestamp = now()
            previous_cursor = previous["cursor"] if previous else None
            cursor = content_digest({**identity, "sequence": sequence, "previous_cursor": previous_cursor, "received_at": timestamp})
            connection.execute("INSERT INTO lineage_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sequence, record.event_id, record.kind, client_id, provenance, record.reported_session_id,
                 record.reported_turn_id, record.tool_use_id, record.parent_event_id, payload_digest,
                 identity_digest, previous_cursor, cursor, timestamp))
            text = encoded.decode()
            for ordinal, start in enumerate(range(0, len(text), LINEAGE_CHUNK_CHARS), 1):
                chunk = text[start:start + LINEAGE_CHUNK_CHARS]
                text_digest = content_digest(chunk)
                chunk_id = content_digest({"event_id": record.event_id, "ordinal": ordinal, "text_digest": text_digest})
                connection.execute("INSERT INTO lineage_chunks VALUES(?,?,?,?,?)", (chunk_id, record.event_id, ordinal, text_digest, chunk))
                connection.execute("INSERT INTO lineage_fts VALUES(?,?,?)", (chunk_id, record.event_id, chunk))
            self.store.append_receipt("visible_lineage_append", {"event_id": record.event_id, "sequence": sequence,
                                      "cursor": cursor, "provenance": provenance}, connection=connection)
            result = LineageAppendResult(event_id=record.event_id, sequence=sequence, cursor=cursor,
                                         payload_digest=payload_digest, duplicate=False, provenance=provenance)
            capture.record(decision, result, lease, client_id=client_id, provenance=provenance)
        return result

    def read(self, request: LineageRead | None = None) -> LineagePage:
        request = request or LineageRead()
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='lineage_events' AND type='table'").fetchone():
                return LineagePage(project_id=self.store.project_id, events=[], total_events=0, current_cursor=None,
                                   last_sequence=0, truncated=False, query_mode="fts5" if request.query else "sequence")
            head = connection.execute("SELECT sequence,cursor FROM lineage_events ORDER BY sequence DESC LIMIT 1").fetchone()
            if request.query:
                tokens = re.findall(r"\w{2,}", request.query, re.UNICODE)[:16]
                if not tokens:
                    raise LaneError("LINEAGE_QUERY_REQUIRED", "Use at least one searchable term.")
                match = (' OR ' if request.match_mode == 'any' else ' AND ').join('"' + token.replace('"', '""') + '"' for token in tokens)
                rows = connection.execute("SELECT e.* FROM lineage_events e WHERE e.sequence>? AND EXISTS "
                    "(SELECT 1 FROM lineage_fts WHERE lineage_fts MATCH ? AND event_id=e.event_id) ORDER BY e.sequence LIMIT ?",
                    (request.after_sequence, match, request.limit + 1)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM lineage_events WHERE sequence>? ORDER BY sequence LIMIT ?",
                                          (request.after_sequence, request.limit + 1)).fetchall()
            events = []
            # At most 256 KiB of visible payload per read, even when event count
            # permits more. The caller continues with the last returned sequence.
            budget = 262_144
            for row in rows[:request.limit]:
                self.validate_row(row)
                raw = self.store.read_object(row["payload_digest"])
                if len(raw) > budget:
                    break
                budget -= len(raw)
                item = {key: row[key] for key in ("sequence", "event_id", "kind", "client_id", "provenance", "reported_session_id",
                        "reported_turn_id", "tool_use_id", "parent_event_id", "payload_digest", "cursor", "received_at")}
                item["payload"] = json.loads(raw)
                item["native_task_attestation"] = "not_provided"
                events.append(item)
            return LineagePage(project_id=self.store.project_id, events=events,
                total_events=connection.execute("SELECT count(*) FROM lineage_events").fetchone()[0],
                current_cursor=head["cursor"] if head else None,
                last_sequence=events[-1]["sequence"] if events else request.after_sequence,
                truncated=len(events) < len(rows), query_mode="fts5" if request.query else "sequence")

    @staticmethod
    def validate_row(row):
        identity = {key: row[key] for key in ("event_id", "kind", "client_id", "provenance", "reported_session_id",
                    "reported_turn_id", "tool_use_id", "parent_event_id", "payload_digest")}
        cursor = content_digest({**identity, "sequence": row["sequence"], "previous_cursor": row["previous_cursor"],
                                 "received_at": row["received_at"]})
        if row["cursor"] != cursor or row["identity_digest"] != content_digest(identity):
            raise LaneError("LINEAGE_INTEGRITY_FAILED", "The recorded visible event differs from its cursor binding.")

    def verify(self, *, limit: int = 50_000) -> dict:
        if not 1 <= limit <= 50_000:
            raise LaneError("INVALID_HISTORY_BUDGET", "Use a bounded lineage verification limit.")
        with self.store.connection(read_only=True) as connection:
            if not connection.in_transaction:
                connection.execute("BEGIN")
            rows = connection.execute("SELECT * FROM lineage_events ORDER BY sequence LIMIT ?", (limit + 1,)).fetchall()
            if len(rows) > limit:
                raise LaneError("LINEAGE_HISTORY_BUDGET", "This history exceeds the selected verification budget.")
            previous = None
            for sequence, row in enumerate(rows, 1):
                identity = {key: row[key] for key in ("event_id", "kind", "client_id", "provenance", "reported_session_id",
                            "reported_turn_id", "tool_use_id", "parent_event_id", "payload_digest")}
                cursor = content_digest({**identity, "sequence": sequence, "previous_cursor": previous, "received_at": row["received_at"]})
                if (row["sequence"] != sequence or row["previous_cursor"] != previous or row["cursor"] != cursor
                        or row["identity_digest"] != content_digest(identity)):
                    raise LaneError("LINEAGE_INTEGRITY_FAILED", "The recorded lineage cursor chain is inconsistent.")
                self.store.read_object(row["payload_digest"])
                previous = cursor
            return {"events_verified": len(rows), "cursor": previous, "native_task_attestation": "not_provided"}


def lineage_view(store, scope):
    from .lane_contract import ViewGraph
    page = ChatLineage(store).read(LineageRead(limit=min(scope.node_limit, 50), query=scope.query))
    graph = ViewGraph(store.project_id, scope)
    ids = {}
    for event in page.events:
        ids[event['event_id']] = graph.node('lineage_event', event['event_id'],
            event['kind'] + ': ' + str(event['payload'].get('text', event['event_id'])),
            locator={'cursor': event['cursor'], 'source_client_id': event['client_id'],
                     'provenance': event['provenance'], 'native_task_attestation': 'not_provided'})
    for event in page.events:
        if event['parent_event_id']:
            graph.edge(ids.get(event['parent_event_id']), ids.get(event['event_id']), 'PARENT_OF')
    graph.truncated |= page.truncated
    return graph.result()


def register_lineage_actions(engine):
    def append(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store, kind="capture") as lease, lease.coordinated_transaction(['chat_lineage', 'receipts']):
            result = ChatLineage(store).append(request, lease, client_id=context.client_id)
            if request.kind in {'prompt', 'steer'}:
                from .prompt_index import PromptIndex
                PromptIndex(store).record_entry(result, lease, client_id=context.client_id, registry=engine.registry)
            return result

    engine.registry.register(ActionSpec("lineage_record", "Record bounded visible content with agent-report provenance.",
                                       LineageRecord, LineageAppendResult, append, permission="write", profile="chatlineage", mutates=True, workflow='lifecycle'))
    engine.registry.register(ActionSpec("lineage_read", "Search or page through the selected project's visible ChatLineage.",
        LineageRead, LineagePage, lambda context, request: ChatLineage(engine.directory.open(context.project_id)).read(request),
        profile="chatlineage", queryable_in_delta=True, cross_project_read=True, read_migrations=LINEAGE_MIGRATIONS, workflow='lifecycle',
        search=SearchRoute(('chat_lineage',), 'events', match_mode='any', basis='sqlite_fts5_sequence')))
