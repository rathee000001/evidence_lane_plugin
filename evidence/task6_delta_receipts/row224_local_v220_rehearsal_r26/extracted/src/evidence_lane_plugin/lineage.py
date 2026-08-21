"""Append-only, idempotent, secret-redacted ChatLineage records."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from .capture_routing import CaptureRouteAuthority
from .constants import LINEAGE_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .redaction import contains_secret, redact

_PRIVATE_REASONING_KEYS = {
    "chain_of_thought",
    "hidden_reasoning",
    "internal_reasoning",
    "private_reasoning",
    "reasoning_content",
}

LINEAGE_SQLITE_SCHEMA = "evidence-lane.chat-lineage.sqlite.v1"
PROJECT_LINEAGE_SQLITE_SCHEMA = "evidence-lane.project-chat-lineage.sqlite.v1"
LINEAGE_CHUNK_CHARS = 1024

_VISIBLE_LINK_KEY_MARKERS = {
    "command": "command",
    "commands": "command",
    "file": "file",
    "files": "file",
    "test": "test",
    "tests": "test",
    "build": "build",
    "builds": "build",
    "output": "output",
    "outputs": "output",
    "output_link": "output",
    "output_links": "output",
    "links": "output",
    "tool": "tool",
    "tools": "tool",
    "source_locator": "source_locator",
    "source_locators": "source_locator",
    "locator": "source_locator",
    "locators": "source_locator",
    "chunk": "chunk",
    "chunks": "chunk",
    "chunk_id": "chunk",
    "chunk_ids": "chunk",
    "receipt": "receipt",
    "receipts": "receipt",
    "receipt_id": "receipt",
    "receipt_ids": "receipt",
    "receipt_sha256": "receipt",
    "receipt_sha256s": "receipt",
    "host_identity": "host_identity",
    "host_kind": "host_identity",
    "host_profile": "host_identity",
    "host_app": "host_identity",
    "host_session_id_sha256": "host_identity",
}
_VISIBLE_LINK_KINDS = tuple(sorted(set(_VISIBLE_LINK_KEY_MARKERS.values())))


def _visible_link_kind(key: Any, inherited_kind: str | None) -> str | None:
    normalized = str(key).strip().lower().replace("-", "_")
    if normalized == "raw_host_session_id_stored":
        return None
    exact = _VISIBLE_LINK_KEY_MARKERS.get(normalized)
    if exact is not None:
        return exact
    if normalized.endswith(("_receipt_id", "_receipt_sha256")):
        return "receipt"
    if normalized.endswith(("_chunk_id", "_chunk_sha256")):
        return "chunk"
    if normalized.endswith(("_source_locator", "_source_locator_sha256")):
        return "source_locator"
    return inherited_kind


def _deterministic_visible_chunks(
    event_id: str, visible_payload_json: str
) -> list[dict[str, Any]]:
    """Split one redacted visible payload into stable content-addressed chunks."""

    chunks: list[dict[str, Any]] = []
    for ordinal, start in enumerate(
        range(0, len(visible_payload_json), LINEAGE_CHUNK_CHARS), start=1
    ):
        text = visible_payload_json[start : start + LINEAGE_CHUNK_CHARS]
        text_sha256 = sha256_bytes(text.encode("utf-8"))
        chunk_id = "linchunk_" + sha256_bytes(
            canonical_json_bytes(
                {
                    "event_id": event_id,
                    "ordinal": ordinal,
                    "text_sha256": text_sha256,
                }
            )
        )[:24].lower()
        chunks.append(
            {
                "chunk_id": chunk_id,
                "chunk_ordinal": ordinal,
                "chunk_text": text,
                "chunk_sha256": text_sha256,
            }
        )
    return chunks


def _private_reasoning_paths(value: Any, prefix: str = "") -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            path = f"{prefix}.{key}" if prefix else str(key)
            if normalized in _PRIVATE_REASONING_KEYS:
                paths.append(path)
            paths.extend(_private_reasoning_paths(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            paths.extend(_private_reasoning_paths(item, f"{prefix}[{index}]"))
    return paths


def _actor_for(event_type: str) -> str:
    lowered = event_type.lower()
    if "prompt" in lowered or "user" in lowered or "steer" in lowered:
        return "user"
    if "assistant" in lowered or lowered.endswith(".response"):
        return "assistant"
    if any(
        marker in lowered
        for marker in ("tool", "command", "file", "test", "build", "git", "usage")
    ):
        return "tool"
    return "system"


def _projection_view(
    event: dict[str, Any], fallback_index: int
) -> dict[str, Any]:
    """Derive projection-only fields without changing the source event.

    ChatLineage JSONL written before the SQLite projection existed does not
    contain the later projection metadata.  Those source events remain valid
    immutable evidence: their canonical bytes and event hashes must not be
    rewritten merely to populate a derived index.
    """

    raw_index = event.get("_projection_lineage_index")
    if raw_index is None:
        raw_index = event.get("lineage_index")
    lineage_index = int(raw_index or fallback_index)
    require(
        lineage_index > 0,
        "LINEAGE_PROJECTION_INDEX_INVALID",
        "ChatLineage projection indexes must be positive.",
        status="FAIL",
        event_id=event.get("event_id"),
        lineage_index=lineage_index,
    )
    visible_payload = event.get("visible_payload") or {}
    computed_visible_sha256 = sha256_bytes(canonical_json_bytes(visible_payload))
    recorded_visible_sha256 = event.get("visible_payload_sha256")
    require(
        recorded_visible_sha256 in (None, computed_visible_sha256),
        "LINEAGE_VISIBLE_PAYLOAD_HASH_MISMATCH",
        "ChatLineage visible-payload hash does not match its source payload.",
        status="MISMATCH",
        event_id=event.get("event_id"),
    )
    event_type = str(event.get("event_type") or "")
    return {
        "lineage_index": lineage_index,
        "actor_type": str(event.get("actor_type") or _actor_for(event_type)),
        "token_metrics": event.get("token_metrics")
        or {"availability": "UNAVAILABLE"},
        "visible_payload": visible_payload,
        "visible_payload_sha256": recorded_visible_sha256
        or computed_visible_sha256,
    }


class ChatLineage:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.sqlite_path = self.path.with_suffix(".sqlite")

    def _projection_connection(self) -> sqlite3.Connection:
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.sqlite_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS lineage_meta(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            ) STRICT;
            CREATE TABLE IF NOT EXISTS lineage_event(
                event_id TEXT PRIMARY KEY,
                lineage_index INTEGER NOT NULL UNIQUE CHECK(lineage_index > 0),
                event_type TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                session_id TEXT NOT NULL,
                task_id TEXT,
                run_id TEXT,
                actor_type TEXT NOT NULL,
                model TEXT,
                submodel TEXT,
                token_metrics_json TEXT NOT NULL,
                visible_payload_json TEXT NOT NULL,
                visible_payload_sha256 TEXT NOT NULL,
                previous_event_sha256 TEXT,
                event_sha256 TEXT NOT NULL UNIQUE,
                private_reasoning_stored INTEGER NOT NULL DEFAULT 0
                    CHECK(private_reasoning_stored = 0)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS lineage_link(
                event_id TEXT NOT NULL REFERENCES lineage_event(event_id)
                    ON DELETE CASCADE,
                link_kind TEXT NOT NULL,
                link_value TEXT NOT NULL,
                link_sha256 TEXT NOT NULL,
                PRIMARY KEY(event_id, link_kind, link_sha256)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS lineage_chunk(
                event_id TEXT NOT NULL REFERENCES lineage_event(event_id)
                    ON DELETE CASCADE,
                chunk_ordinal INTEGER NOT NULL CHECK(chunk_ordinal > 0),
                chunk_id TEXT NOT NULL UNIQUE,
                chunk_text TEXT NOT NULL,
                chunk_sha256 TEXT NOT NULL,
                PRIMARY KEY(event_id, chunk_ordinal)
            ) STRICT;
            CREATE TABLE IF NOT EXISTS lineage_head(
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                event_count INTEGER NOT NULL,
                head_event_sha256 TEXT,
                jsonl_sha256 TEXT NOT NULL,
                projection_sha256 TEXT NOT NULL
            ) STRICT;
            CREATE VIRTUAL TABLE IF NOT EXISTS lineage_fts USING fts5(
                event_id UNINDEXED,
                event_type,
                actor_type,
                model,
                visible_payload,
                tokenize='unicode61'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS lineage_chunk_fts USING fts5(
                chunk_id UNINDEXED,
                event_id UNINDEXED,
                chunk_text,
                tokenize='unicode61'
            );
            """
        )
        return connection

    @staticmethod
    def _visible_links(payload: Any) -> list[tuple[str, str]]:
        """Extract privacy-safe visible operational and provenance references."""

        links: list[tuple[str, str]] = []

        def visit(value: Any, inherited_kind: str | None = None) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    visit(item, _visible_link_kind(key, inherited_kind))
            elif isinstance(value, list):
                for item in value:
                    visit(item, inherited_kind)
            elif inherited_kind and isinstance(value, (str, int, float, bool)):
                visible = str(value).strip()
                if visible:
                    links.append((inherited_kind, visible))

        visit(payload)
        return list(dict.fromkeys(links))

    def _sync_projection(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        """Synchronize the durable SQLite projection from the verified JSONL chain."""

        jsonl_sha256 = sha256_bytes(
            self.path.read_bytes() if self.path.exists() else b""
        )
        projection_payload = {
            "schema": LINEAGE_SQLITE_SCHEMA,
            "jsonl_sha256": jsonl_sha256,
            "events": [str(item["event_sha256"]) for item in events],
            "visible_link_kinds": list(_VISIBLE_LINK_KINDS),
            "chunking": {
                "algorithm": "FIXED_UNICODE_CODEPOINT_WINDOWS_NO_OVERLAP",
                "chunk_chars": LINEAGE_CHUNK_CHARS,
            },
        }
        projection_sha256 = sha256_bytes(canonical_json_bytes(projection_payload))
        connection = self._projection_connection()
        try:
            existing = connection.execute(
                "SELECT * FROM lineage_head WHERE singleton=1"
            ).fetchone()
            if (
                existing
                and int(existing["event_count"]) == len(events)
                and existing["jsonl_sha256"] == jsonl_sha256
                and existing["projection_sha256"] == projection_sha256
            ):
                action = "REUSED"
            else:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM lineage_link")
                connection.execute("DELETE FROM lineage_chunk")
                connection.execute("DELETE FROM lineage_event")
                connection.execute("DELETE FROM lineage_fts")
                connection.execute("DELETE FROM lineage_chunk_fts")
                connection.execute("DELETE FROM lineage_head")
                connection.execute("DELETE FROM lineage_meta")
                connection.execute(
                    "INSERT INTO lineage_meta(key,value) VALUES('schema',?)",
                    (LINEAGE_SQLITE_SCHEMA,),
                )
                for fallback_index, event in enumerate(events, start=1):
                    projection = _projection_view(event, fallback_index)
                    visible_payload = json.dumps(
                        projection["visible_payload"],
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    token_metrics = json.dumps(
                        projection["token_metrics"],
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """
                        INSERT INTO lineage_event(
                            event_id, lineage_index, event_type, occurred_at,
                            session_id, task_id, run_id, actor_type, model,
                            submodel, token_metrics_json, visible_payload_json,
                            visible_payload_sha256, previous_event_sha256,
                            event_sha256, private_reasoning_stored
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                        """,
                        (
                            event["event_id"],
                            projection["lineage_index"],
                            event["event_type"],
                            event["occurred_at"],
                            event["session_id"],
                            event.get("task_id"),
                            event.get("run_id"),
                            projection["actor_type"],
                            event.get("model"),
                            event.get("submodel"),
                            token_metrics,
                            visible_payload,
                            projection["visible_payload_sha256"],
                            event.get("previous_event_sha256"),
                            event["event_sha256"],
                        ),
                    )
                    connection.execute(
                        "INSERT INTO lineage_fts VALUES(?,?,?,?,?)",
                        (
                            event["event_id"],
                            event["event_type"],
                            projection["actor_type"],
                            event.get("model") or "",
                            visible_payload,
                        ),
                    )
                    for chunk in _deterministic_visible_chunks(
                        str(event["event_id"]), visible_payload
                    ):
                        connection.execute(
                            "INSERT INTO lineage_chunk VALUES(?,?,?,?,?)",
                            (
                                event["event_id"],
                                chunk["chunk_ordinal"],
                                chunk["chunk_id"],
                                chunk["chunk_text"],
                                chunk["chunk_sha256"],
                            ),
                        )
                        connection.execute(
                            "INSERT INTO lineage_chunk_fts VALUES(?,?,?)",
                            (
                                chunk["chunk_id"],
                                event["event_id"],
                                chunk["chunk_text"],
                            ),
                        )
                    for link_kind, link_value in self._visible_links(
                        event.get("visible_payload") or {}
                    ):
                        connection.execute(
                            "INSERT INTO lineage_link VALUES(?,?,?,?)",
                            (
                                event["event_id"],
                                link_kind,
                                link_value,
                                sha256_bytes(link_value.encode("utf-8")),
                            ),
                        )
                connection.execute(
                    "INSERT INTO lineage_head VALUES(1,?,?,?,?)",
                    (
                        len(events),
                        events[-1]["event_sha256"] if events else None,
                        jsonl_sha256,
                        projection_sha256,
                    ),
                )
                connection.commit()
                action = "REBUILT"
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            fts_count = int(
                connection.execute("SELECT COUNT(*) FROM lineage_fts").fetchone()[0]
            )
            link_count = int(
                connection.execute("SELECT COUNT(*) FROM lineage_link").fetchone()[0]
            )
            chunk_count = int(
                connection.execute("SELECT COUNT(*) FROM lineage_chunk").fetchone()[0]
            )
            chunk_fts_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM lineage_chunk_fts"
                ).fetchone()[0]
            )
            require(
                integrity == ["ok"]
                and not foreign_keys
                and fts_count == len(events)
                and chunk_count == chunk_fts_count,
                "LINEAGE_SQLITE_PROJECTION_INVALID",
                "The durable ChatLineage SQLite projection failed validation.",
                status="FAIL",
                integrity=integrity,
                foreign_key_errors=len(foreign_keys),
                fts_count=fts_count,
                chunk_count=chunk_count,
                chunk_fts_count=chunk_fts_count,
                event_count=len(events),
            )
            return {
                "status": "PASS",
                "schema": LINEAGE_SQLITE_SCHEMA,
                "action": action,
                "path": str(self.sqlite_path),
                "event_count": len(events),
                "head_event_sha256": (
                    events[-1]["event_sha256"] if events else None
                ),
                "jsonl_sha256": jsonl_sha256,
                "projection_sha256": projection_sha256,
                "integrity": integrity,
                "foreign_key_errors": 0,
                "fts_count": fts_count,
                "link_count": link_count,
                "chunk_count": chunk_count,
                "chunk_fts_count": chunk_fts_count,
                "chunk_chars": LINEAGE_CHUNK_CHARS,
                "private_reasoning_stored": False,
            }
        finally:
            connection.close()

    def _sync_project_authority(self) -> dict[str, Any] | None:
        if self.path.parent.name != "lineage":
            return None
        return ProjectChatLineage(self.path.parent).sync()

    def _events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EvidenceLaneError(
                    "LINEAGE_INVALID_JSONL",
                    "ChatLineage contains invalid JSON.",
                    status="FAIL",
                    details={"line_number": line_number},
                ) from exc
            expected_hash = sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in event.items()
                        if key != "event_sha256"
                    }
                )
            )
            require(
                event.get("event_sha256") == expected_hash,
                "LINEAGE_EVENT_HASH_MISMATCH",
                "ChatLineage contains an event whose content hash does not match.",
                status="MISMATCH",
                line_number=line_number,
                event_id=event.get("event_id"),
            )
            if "previous_event_sha256" in event:
                require(
                    event.get("previous_event_sha256")
                    == (events[-1].get("event_sha256") if events else None),
                    "LINEAGE_CHAIN_MISMATCH",
                    "ChatLineage previous-event linkage is not contiguous.",
                    status="MISMATCH",
                    line_number=line_number,
                    event_id=event.get("event_id"),
                )
            private_paths = _private_reasoning_paths(event.get("visible_payload") or {})
            require(
                not private_paths and event.get("private_reasoning_stored") is not True,
                "LINEAGE_PRIVATE_REASONING_FORBIDDEN",
                "ChatLineage contains hidden or private model reasoning fields.",
                status="BLOCKED",
                line_number=line_number,
                forbidden_paths=private_paths,
            )
            require(
                not contains_secret(event),
                "LINEAGE_SECRET_DETECTED",
                "ChatLineage contains an unredacted secret-like value.",
                status="BLOCKED",
                line_number=line_number,
                event_id=event.get("event_id"),
            )
            events.append(event)
        return events

    def append(
        self,
        *,
        event_type: str,
        visible_payload: dict[str, Any],
        occurred_at: str,
        session_id: str,
        task_id: str | None = None,
        run_id: str | None = None,
        event_id: str | None = None,
        actor_type: str | None = None,
        model: str | None = None,
        submodel: str | None = None,
        token_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        require(
            bool(event_type.strip()),
            "LINEAGE_EVENT_TYPE_REQUIRED",
            "ChatLineage event_type is required.",
        )
        private_paths = _private_reasoning_paths(visible_payload)
        require(
            not private_paths,
            "LINEAGE_PRIVATE_REASONING_FORBIDDEN",
            "Hidden or private model reasoning fields cannot enter ChatLineage.",
            status="BLOCKED",
            forbidden_paths=private_paths,
        )
        safe_payload = redact(visible_payload)
        if contains_secret(safe_payload):
            raise EvidenceLaneError(
                "LINEAGE_SECRET_REDACTION_FAILED",
                "A secret-like value remained after ChatLineage redaction.",
                status="BLOCKED",
            )
        exact_event_id = event_id or prefixed_id("evt")
        capture_authority = CaptureRouteAuthority.for_lineage(self.path)
        if capture_authority is not None:
            try:
                capture_decision = capture_authority.classify_and_record(
                    event_id=exact_event_id,
                    event_type=event_type,
                    visible_payload=safe_payload,
                    occurred_at=occurred_at,
                    session_id=session_id,
                    task_id=task_id,
                    run_id=run_id,
                )
            except EvidenceLaneError as exc:
                if exc.code != "CAPTURE_DECISION_EVENT_ID_CONFLICT":
                    raise
                raise EvidenceLaneError(
                    "LINEAGE_EVENT_ID_CONFLICT",
                    "An existing ChatLineage event uses the same ID with different content.",
                    status="BLOCKED",
                    details={"event_id": exact_event_id},
                ) from exc
            if capture_decision["included"] is not True:
                event_type = "capture.excluded"
                safe_payload = capture_authority.exclusion_payload(capture_decision)
        events = self._events()
        matches = [
            existing
            for existing in events
            if existing.get("event_id") == exact_event_id
        ]
        lineage_index = (
            int(matches[0].get("lineage_index", len(events)))
            if matches
            else len(events) + 1
        )
        previous_event_sha256 = (
            matches[0].get("previous_event_sha256")
            if matches
            else (events[-1].get("event_sha256") if events else None)
        )
        metrics = redact(token_metrics or {"availability": "UNAVAILABLE"})
        event = {
            "schema": LINEAGE_SCHEMA,
            "event_id": exact_event_id,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "session_id": session_id,
            "task_id": task_id,
            "run_id": run_id,
            "lineage_index": lineage_index,
            "previous_event_sha256": previous_event_sha256,
            "actor_type": actor_type or _actor_for(event_type),
            "model": model,
            "submodel": submodel,
            "token_metrics": metrics,
            "visible_payload": safe_payload,
            "visible_payload_sha256": sha256_bytes(canonical_json_bytes(safe_payload)),
            "private_reasoning_stored": False,
        }
        event["event_sha256"] = sha256_bytes(
            canonical_json_bytes(
                {key: value for key, value in event.items() if key != "event_sha256"}
            )
        )
        if matches:
            require(
                matches[0] == event,
                "LINEAGE_EVENT_ID_CONFLICT",
                "An existing ChatLineage event uses the same ID with different content.",
                status="BLOCKED",
                event_id=event["event_id"],
            )
            self._sync_projection(events)
            self._sync_project_authority()
            return matches[0]
        events.append(event)
        payload = b"".join(canonical_json_bytes(item) for item in events)
        atomic_write_bytes(self.path, payload)
        self._sync_projection(events)
        self._sync_project_authority()
        return event

    def copy_from(self, source: str | Path) -> None:
        source_path = Path(source)
        if not source_path.exists():
            atomic_write_bytes(self.path, b"")
            self._sync_projection([])
            self._sync_project_authority()
            return
        source_events = ChatLineage(source_path)._events()
        seen: set[str] = set()
        for event in source_events:
            event_id = event.get("event_id")
            require(
                isinstance(event_id, str) and event_id not in seen,
                "LINEAGE_DUPLICATE_EVENT",
                "ChatLineage event IDs must be unique.",
                status="FAIL",
                event_id=event_id,
            )
            seen.add(cast(str, event_id))
        atomic_write_bytes(
            self.path,
            b"".join(canonical_json_bytes(item) for item in source_events),
        )
        self._sync_projection(source_events)
        self._sync_project_authority()

    def events(self) -> list[dict[str, Any]]:
        return self._events()

    def projection_status(self) -> dict[str, Any]:
        result = self._sync_projection(self._events())
        project = self._sync_project_authority()
        if project is not None:
            result["project_authority"] = project
        capture_authority = CaptureRouteAuthority.for_lineage(self.path)
        if capture_authority is not None:
            result["capture_route"] = capture_authority.status()
        return result


class ProjectChatLineage:
    """Project-wide first-read authority over every verified session lineage."""

    def __init__(self, lineage_root: str | Path) -> None:
        self.root = Path(lineage_root).resolve()
        self.path = self.root / "chat_lineage.sqlite"
        self.head_path = self.root / "chat_lineage_head.json"

    def _events(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return events
        for path in sorted(self.root.glob("*.jsonl"), key=lambda item: item.name):
            for fallback_index, source_event in enumerate(
                ChatLineage(path).events(), start=1
            ):
                event = dict(source_event)
                event["_projection_lineage_index"] = int(
                    source_event.get("lineage_index") or fallback_index
                )
                events.append(event)
        events.sort(
            key=lambda item: (
                str(item.get("occurred_at") or ""),
                str(item.get("session_id") or ""),
                int(item.get("_projection_lineage_index") or 0),
                str(item.get("event_id") or ""),
            )
        )
        event_ids = [str(item.get("event_id") or "") for item in events]
        require(
            all(event_ids) and len(event_ids) == len(set(event_ids)) or not events,
            "PROJECT_LINEAGE_DUPLICATE_EVENT",
            "Project ChatLineage event IDs must be globally unique.",
            status="FAIL",
        )
        return events

    def sync(self) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        events = self._events()
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS project_lineage_event(
                    event_id TEXT PRIMARY KEY,
                    global_index INTEGER NOT NULL UNIQUE CHECK(global_index > 0),
                    session_id TEXT NOT NULL,
                    session_index INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    task_id TEXT,
                    run_id TEXT,
                    actor_type TEXT NOT NULL,
                    model TEXT,
                    submodel TEXT,
                    token_metrics_json TEXT NOT NULL,
                    visible_payload_json TEXT NOT NULL,
                    visible_payload_sha256 TEXT NOT NULL,
                    previous_session_event_sha256 TEXT,
                    event_sha256 TEXT NOT NULL UNIQUE,
                    previous_project_state_sha256 TEXT,
                    project_state_sha256 TEXT NOT NULL UNIQUE,
                    private_reasoning_stored INTEGER NOT NULL DEFAULT 0
                        CHECK(private_reasoning_stored=0)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS project_lineage_head(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    event_count INTEGER NOT NULL,
                    head_event_id TEXT,
                    head_event_sha256 TEXT,
                    head_state_sha256 TEXT,
                    projection_sha256 TEXT NOT NULL
                ) STRICT;
                CREATE TABLE IF NOT EXISTS project_lineage_link(
                    event_id TEXT NOT NULL REFERENCES project_lineage_event(event_id)
                        ON DELETE CASCADE,
                    link_kind TEXT NOT NULL,
                    link_value TEXT NOT NULL,
                    link_sha256 TEXT NOT NULL,
                    PRIMARY KEY(event_id, link_kind, link_sha256)
                ) STRICT;
                CREATE TABLE IF NOT EXISTS project_lineage_chunk(
                    event_id TEXT NOT NULL REFERENCES project_lineage_event(event_id)
                        ON DELETE CASCADE,
                    chunk_ordinal INTEGER NOT NULL CHECK(chunk_ordinal > 0),
                    chunk_id TEXT NOT NULL UNIQUE,
                    chunk_text TEXT NOT NULL,
                    chunk_sha256 TEXT NOT NULL,
                    PRIMARY KEY(event_id, chunk_ordinal)
                ) STRICT;
                CREATE VIRTUAL TABLE IF NOT EXISTS project_lineage_fts USING fts5(
                    event_id UNINDEXED,
                    event_type,
                    actor_type,
                    model,
                    visible_payload,
                    tokenize='unicode61'
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS project_lineage_chunk_fts USING fts5(
                    chunk_id UNINDEXED,
                    event_id UNINDEXED,
                    chunk_text,
                    tokenize='unicode61'
                );
                """
            )
            projection_payload = {
                "schema": PROJECT_LINEAGE_SQLITE_SCHEMA,
                "events": [str(item["event_sha256"]) for item in events],
                "visible_link_kinds": list(_VISIBLE_LINK_KINDS),
                "chunking": {
                    "algorithm": "FIXED_UNICODE_CODEPOINT_WINDOWS_NO_OVERLAP",
                    "chunk_chars": LINEAGE_CHUNK_CHARS,
                },
            }
            projection_sha256 = sha256_bytes(canonical_json_bytes(projection_payload))
            existing = connection.execute(
                "SELECT * FROM project_lineage_head WHERE singleton=1"
            ).fetchone()
            if (
                existing
                and int(existing["event_count"]) == len(events)
                and existing["projection_sha256"] == projection_sha256
            ):
                action = "REUSED"
                head_state = existing["head_state_sha256"]
            else:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM project_lineage_link")
                connection.execute("DELETE FROM project_lineage_chunk")
                connection.execute("DELETE FROM project_lineage_event")
                connection.execute("DELETE FROM project_lineage_fts")
                connection.execute("DELETE FROM project_lineage_chunk_fts")
                connection.execute("DELETE FROM project_lineage_head")
                previous_state: str | None = None
                for index, event in enumerate(events, start=1):
                    projection = _projection_view(event, index)
                    state_payload = {
                        "schema": PROJECT_LINEAGE_SQLITE_SCHEMA,
                        "global_index": index,
                        "event_sha256": event["event_sha256"],
                        "previous_project_state_sha256": previous_state,
                    }
                    state_sha256 = sha256_bytes(canonical_json_bytes(state_payload))
                    token_metrics = json.dumps(
                        projection["token_metrics"],
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    visible_payload = json.dumps(
                        projection["visible_payload"],
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """
                        INSERT INTO project_lineage_event VALUES(
                            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0
                        )
                        """,
                        (
                            event["event_id"],
                            index,
                            event["session_id"],
                            projection["lineage_index"],
                            event["event_type"],
                            event["occurred_at"],
                            event.get("task_id"),
                            event.get("run_id"),
                            projection["actor_type"],
                            event.get("model"),
                            event.get("submodel"),
                            token_metrics,
                            visible_payload,
                            projection["visible_payload_sha256"],
                            event.get("previous_event_sha256"),
                            event["event_sha256"],
                            previous_state,
                            state_sha256,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO project_lineage_fts VALUES(?,?,?,?,?)",
                        (
                            event["event_id"],
                            event["event_type"],
                            projection["actor_type"],
                            event.get("model") or "",
                            visible_payload,
                        ),
                    )
                    for chunk in _deterministic_visible_chunks(
                        str(event["event_id"]), visible_payload
                    ):
                        connection.execute(
                            "INSERT INTO project_lineage_chunk VALUES(?,?,?,?,?)",
                            (
                                event["event_id"],
                                chunk["chunk_ordinal"],
                                chunk["chunk_id"],
                                chunk["chunk_text"],
                                chunk["chunk_sha256"],
                            ),
                        )
                        connection.execute(
                            "INSERT INTO project_lineage_chunk_fts VALUES(?,?,?)",
                            (
                                chunk["chunk_id"],
                                event["event_id"],
                                chunk["chunk_text"],
                            ),
                        )
                    for link_kind, link_value in ChatLineage._visible_links(
                        event.get("visible_payload") or {}
                    ):
                        connection.execute(
                            "INSERT INTO project_lineage_link VALUES(?,?,?,?)",
                            (
                                event["event_id"],
                                link_kind,
                                link_value,
                                sha256_bytes(link_value.encode("utf-8")),
                            ),
                        )
                    previous_state = state_sha256
                head_state = previous_state
                head = events[-1] if events else None
                connection.execute(
                    "INSERT INTO project_lineage_head VALUES(1,?,?,?,?,?)",
                    (
                        len(events),
                        head["event_id"] if head else None,
                        head["event_sha256"] if head else None,
                        head_state,
                        projection_sha256,
                    ),
                )
                connection.commit()
                action = "REBUILT"
            integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
            foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
            fts_count = int(
                connection.execute("SELECT COUNT(*) FROM project_lineage_fts").fetchone()[0]
            )
            link_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_lineage_link"
                ).fetchone()[0]
            )
            chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_lineage_chunk"
                ).fetchone()[0]
            )
            chunk_fts_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_lineage_chunk_fts"
                ).fetchone()[0]
            )
        finally:
            connection.close()
        require(
            integrity == ["ok"]
            and not foreign_keys
            and fts_count == len(events)
            and chunk_count == chunk_fts_count,
            "PROJECT_LINEAGE_SQLITE_INVALID",
            "The project ChatLineage SQLite authority failed validation.",
            status="FAIL",
            integrity=integrity,
            foreign_key_errors=len(foreign_keys),
            fts_count=fts_count,
            chunk_count=chunk_count,
            chunk_fts_count=chunk_fts_count,
            event_count=len(events),
        )
        head_receipt = {
            "schema": PROJECT_LINEAGE_SQLITE_SCHEMA,
            "event_count": len(events),
            "head_event_id": events[-1]["event_id"] if events else None,
            "head_event_sha256": events[-1]["event_sha256"] if events else None,
            "head_state_sha256": head_state,
            "projection_sha256": projection_sha256,
            "sqlite_path": str(self.path),
            "private_reasoning_stored": False,
        }
        atomic_write_bytes(self.head_path, canonical_json_bytes(head_receipt))
        return {
            "status": "PASS",
            "action": action,
            "integrity": integrity,
            "foreign_key_errors": 0,
            "fts_count": fts_count,
            "link_count": link_count,
            "chunk_count": chunk_count,
            "chunk_fts_count": chunk_fts_count,
            "chunk_chars": LINEAGE_CHUNK_CHARS,
            **head_receipt,
        }


def lineage_sha256(path: str | Path) -> str:
    target = Path(path)
    return sha256_bytes(target.read_bytes() if target.exists() else b"")
