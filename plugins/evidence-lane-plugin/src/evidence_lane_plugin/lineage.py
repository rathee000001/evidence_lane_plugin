"""Append-only, idempotent, secret-redacted ChatLineage records."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, Self, cast

from .capture_routing import CaptureRouteAuthority
from .constants import LINEAGE_SCHEMA
from .errors import EvidenceLaneError, require
from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes
from .ids import prefixed_id
from .project_authority import refresh_working_sector_operational_checksums
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
LINEAGE_REVISION_SCHEMA = "evidence-lane.chat-lineage.revision-cursor.v1"
LINEAGE_CHUNK_CHARS = 1024
LINEAGE_QUERY_LIMIT_MAX = 50

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


class _LineageWriterLock:
    """Serialize every session writer that contributes to one project lineage."""

    _process_locks: ClassVar[dict[str, threading.RLock]] = {}
    _guard: ClassVar[threading.Lock] = threading.Lock()

    @classmethod
    def process_lock(cls, path: Path) -> threading.RLock:
        with cls._guard:
            return cls._process_locks.setdefault(str(path.resolve()), threading.RLock())

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self._process_lock = self.process_lock(path)
        self._descriptor: int | None = None

    def __enter__(self) -> Self:
        self._process_lock.acquire()
        deadline = time.monotonic() + self.timeout
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                self._descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                os.write(self._descriptor, str(os.getpid()).encode("ascii"))
                return self
            except FileExistsError:
                if time.monotonic() >= deadline:
                    self._process_lock.release()
                    raise EvidenceLaneError(
                        "LINEAGE_SINGLE_WRITER_BUSY",
                        "The project ChatLineage writer remained locked.",
                        status="BLOCKED",
                        details={"lock_path": str(self.path)},
                    )
                time.sleep(0.025)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._descriptor is not None:
                os.close(self._descriptor)
            self.path.unlink(missing_ok=True)
        finally:
            self._process_lock.release()


def _source_revision_identity(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    source_sha256 = str(value.get("source_sha256") or "").upper()
    require(
        bool(re.fullmatch(r"[0-9A-F]{64}", source_sha256)),
        "LINEAGE_SOURCE_REVISION_SHA256_INVALID",
        "A source-backed ChatLineage revision requires an exact SHA-256.",
        status="BLOCKED",
    )
    size_bytes = int(value.get("size_bytes", -1))
    mtime_ns = int(value.get("mtime_ns", -1))
    task_window_id = str(value.get("task_window_id") or "").strip()
    source_event_start = int(value.get("source_event_start", 0))
    source_event_end = int(value.get("source_event_end", 0))
    prefix_sha256 = (
        str(value.get("prefix_sha256") or "").upper() or None
    )
    prefix_size_bytes = (
        int(value["prefix_size_bytes"])
        if value.get("prefix_size_bytes") is not None
        else None
    )
    require(
        size_bytes >= 0
        and mtime_ns >= 0
        and bool(task_window_id)
        and source_event_start > 0
        and source_event_end >= source_event_start,
        "LINEAGE_SOURCE_REVISION_IDENTITY_INVALID",
        "Source revision size, mtime, task window, and event range are required.",
        status="BLOCKED",
    )
    require(
        (prefix_sha256 is None and prefix_size_bytes is None)
        or (
            prefix_sha256 is not None
            and bool(re.fullmatch(r"[0-9A-F]{64}", prefix_sha256))
            and prefix_size_bytes is not None
            and 0 <= prefix_size_bytes <= size_bytes
        ),
        "LINEAGE_SOURCE_REVISION_PREFIX_INVALID",
        "Source revision prefix SHA-256 and size must be supplied together.",
        status="BLOCKED",
    )
    return {
        "schema": LINEAGE_REVISION_SCHEMA,
        "source_sha256": source_sha256,
        "size_bytes": size_bytes,
        "mtime_ns": mtime_ns,
        "task_window_id": task_window_id,
        "source_event_start": source_event_start,
        "source_event_end": source_event_end,
        "prefix_sha256": prefix_sha256,
        "prefix_size_bytes": prefix_size_bytes,
    }


def _revision_cursor(
    *,
    scope_id: str,
    revision_number: int,
    event_id: str,
    event_type: str,
    visible_payload_sha256: str,
    previous_cursor_sha256: str | None,
    source_revision: dict[str, Any] | None,
) -> str:
    return sha256_bytes(
        canonical_json_bytes(
            {
                "schema": LINEAGE_REVISION_SCHEMA,
                "scope_id": scope_id,
                "revision_number": revision_number,
                "event_id": event_id,
                "event_type": event_type,
                "visible_payload_sha256": visible_payload_sha256,
                "previous_revision_cursor_sha256": previous_cursor_sha256,
                "source_revision_sha256": (
                    sha256_bytes(canonical_json_bytes(source_revision))
                    if source_revision is not None
                    else None
                ),
            }
        )
    )


def _revision_view(
    event: dict[str, Any],
    *,
    fallback_scope_id: str,
    fallback_revision_number: int,
    previous_cursor_sha256: str | None,
) -> dict[str, Any]:
    scope_id = str(event.get("revision_scope_id") or fallback_scope_id).strip()
    revision_number = int(
        event.get("revision_number") or fallback_revision_number
    )
    require(
        bool(scope_id) and revision_number > 0,
        "LINEAGE_REVISION_CURSOR_INVALID",
        "ChatLineage revision identities must be non-empty and positive.",
        status="MISMATCH",
        event_id=event.get("event_id"),
    )
    source_revision = _source_revision_identity(event.get("source_revision"))
    expected_previous = event.get("previous_revision_cursor_sha256")
    require(
        expected_previous in (None, previous_cursor_sha256),
        "LINEAGE_REVISION_CHAIN_MISMATCH",
        "ChatLineage revision cursor linkage is not contiguous.",
        status="MISMATCH",
        event_id=event.get("event_id"),
    )
    visible_payload_sha256 = str(
        event.get("visible_payload_sha256")
        or sha256_bytes(canonical_json_bytes(event.get("visible_payload") or {}))
    )
    cursor_sha256 = _revision_cursor(
        scope_id=scope_id,
        revision_number=revision_number,
        event_id=str(event.get("event_id") or ""),
        event_type=str(event.get("event_type") or ""),
        visible_payload_sha256=visible_payload_sha256,
        previous_cursor_sha256=previous_cursor_sha256,
        source_revision=source_revision,
    )
    require(
        event.get("revision_cursor_sha256") in (None, cursor_sha256),
        "LINEAGE_REVISION_CURSOR_HASH_MISMATCH",
        "The recorded ChatLineage revision cursor does not match its event.",
        status="MISMATCH",
        event_id=event.get("event_id"),
    )
    return {
        "revision_scope_id": scope_id,
        "revision_number": revision_number,
        "previous_revision_cursor_sha256": previous_cursor_sha256,
        "revision_cursor_sha256": cursor_sha256,
        "source_revision": source_revision,
        "source_revision_sha256": (
            sha256_bytes(canonical_json_bytes(source_revision))
            if source_revision is not None
            else None
        ),
    }


def _fts_query(value: str) -> str:
    tokens = re.findall(r"[A-Za-z0-9_]{2,}", value)[:16]
    require(
        bool(tokens),
        "LINEAGE_QUERY_REQUIRED",
        "A bounded ChatLineage query requires at least one searchable term.",
        status="BLOCKED",
    )
    return " AND ".join(f'"{token}"' for token in tokens)


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
        self.lock_path = self.path.parent / ".chat-lineage-writer.lock"

    @contextmanager
    def _writer_lock(self) -> Iterator[None]:
        with _LineageWriterLock(self.lock_path):
            yield

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
            CREATE TABLE IF NOT EXISTS lineage_revision(
                event_id TEXT PRIMARY KEY REFERENCES lineage_event(event_id)
                    ON DELETE CASCADE,
                revision_scope_id TEXT NOT NULL,
                revision_number INTEGER NOT NULL CHECK(revision_number > 0),
                previous_revision_cursor_sha256 TEXT,
                revision_cursor_sha256 TEXT NOT NULL UNIQUE,
                source_revision_json TEXT,
                source_revision_sha256 TEXT,
                UNIQUE(revision_scope_id, revision_number)
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
            "revision_cursor_schema": LINEAGE_REVISION_SCHEMA,
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
                connection.execute("DELETE FROM lineage_revision")
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
                revision_counts: dict[str, int] = {}
                revision_heads: dict[str, str | None] = {}
                for fallback_index, event in enumerate(events, start=1):
                    projection = _projection_view(event, fallback_index)
                    fallback_scope_id = str(
                        event.get("task_id") or event.get("session_id") or "legacy"
                    )
                    fallback_scope_id = (
                        f"{event.get('session_id')}:{fallback_scope_id}"
                    )
                    recorded_scope = str(
                        event.get("revision_scope_id") or fallback_scope_id
                    )
                    revision = _revision_view(
                        event,
                        fallback_scope_id=fallback_scope_id,
                        fallback_revision_number=revision_counts.get(recorded_scope, 0)
                        + 1,
                        previous_cursor_sha256=revision_heads.get(recorded_scope),
                    )
                    revision_counts[recorded_scope] = revision["revision_number"]
                    revision_heads[recorded_scope] = revision[
                        "revision_cursor_sha256"
                    ]
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
                    connection.execute(
                        "INSERT INTO lineage_revision VALUES(?,?,?,?,?,?,?)",
                        (
                            event["event_id"],
                            revision["revision_scope_id"],
                            revision["revision_number"],
                            revision["previous_revision_cursor_sha256"],
                            revision["revision_cursor_sha256"],
                            (
                                json.dumps(
                                    revision["source_revision"],
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                if revision["source_revision"] is not None
                                else None
                            ),
                            revision["source_revision_sha256"],
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
            revision_count = int(
                connection.execute("SELECT COUNT(*) FROM lineage_revision").fetchone()[0]
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
                and revision_count == len(events)
                and chunk_count == chunk_fts_count,
                "LINEAGE_SQLITE_PROJECTION_INVALID",
                "The durable ChatLineage SQLite projection failed validation.",
                status="FAIL",
                integrity=integrity,
                foreign_key_errors=len(foreign_keys),
                fts_count=fts_count,
                revision_count=revision_count,
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
                "revision_count": revision_count,
                "link_count": link_count,
                "chunk_count": chunk_count,
                "chunk_fts_count": chunk_fts_count,
                "chunk_chars": LINEAGE_CHUNK_CHARS,
                "private_reasoning_stored": False,
            }
        finally:
            connection.close()

    def _sync_project_authority(self) -> dict[str, Any] | None:
        lineage_root = self.path.parent
        direct_sector = bool(
            lineage_root.name == "chat_lineage"
            and lineage_root.parent.name == "sectors"
        )
        legacy_root = lineage_root.name == "lineage"
        if not direct_sector and not legacy_root:
            return None
        result = ProjectChatLineage(lineage_root).sync()
        if direct_sector:
            result["working_sector_checksum_refresh"] = (
                refresh_working_sector_operational_checksums(
                    lineage_root.parent.parent,
                    authority="CHAT_LINEAGE",
                )
            )
        return result

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
        revision_scope_id: str | None = None,
        source_revision: dict[str, Any] | None = None,
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
        normalized_source_revision = _source_revision_identity(source_revision)
        exact_scope_id = str(
            revision_scope_id or f"{session_id}:{task_id or session_id}"
        ).strip()
        require(
            bool(exact_scope_id),
            "LINEAGE_REVISION_SCOPE_REQUIRED",
            "ChatLineage append requires one stable revision scope.",
            status="BLOCKED",
        )
        with self._writer_lock():
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
                    safe_payload = capture_authority.exclusion_payload(
                        capture_decision
                    )
            events = self._events()
            matches = [
                existing
                for existing in events
                if existing.get("event_id") == exact_event_id
            ]
            require(
                len(matches) <= 1,
                "LINEAGE_DUPLICATE_EVENT",
                "ChatLineage event IDs must be unique.",
                status="FAIL",
                event_id=exact_event_id,
            )
            revision_counts: dict[str, int] = {}
            revision_heads: dict[str, str | None] = {}
            revision_by_event: dict[str, dict[str, Any]] = {}
            latest_source_revision_by_scope: dict[str, dict[str, Any]] = {}
            for fallback_index, existing in enumerate(events, start=1):
                fallback_scope_id = (
                    f"{existing.get('session_id')}:"
                    f"{existing.get('task_id') or existing.get('session_id') or 'legacy'}"
                )
                recorded_scope = str(
                    existing.get("revision_scope_id") or fallback_scope_id
                )
                revision = _revision_view(
                    existing,
                    fallback_scope_id=fallback_scope_id,
                    fallback_revision_number=revision_counts.get(recorded_scope, 0)
                    + 1,
                    previous_cursor_sha256=revision_heads.get(recorded_scope),
                )
                revision_counts[recorded_scope] = revision["revision_number"]
                revision_heads[recorded_scope] = revision[
                    "revision_cursor_sha256"
                ]
                revision_by_event[str(existing.get("event_id") or fallback_index)] = (
                    revision
                )
                if revision["source_revision"] is not None:
                    latest_source_revision_by_scope[recorded_scope] = revision[
                        "source_revision"
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
            visible_payload_sha256 = sha256_bytes(canonical_json_bytes(safe_payload))
            if matches:
                revision = revision_by_event[exact_event_id]
            else:
                previous_source_revision = latest_source_revision_by_scope.get(
                    exact_scope_id
                )
                if normalized_source_revision is not None and previous_source_revision:
                    require(
                        normalized_source_revision["prefix_sha256"]
                        == previous_source_revision["source_sha256"]
                        and normalized_source_revision["prefix_size_bytes"]
                        == previous_source_revision["size_bytes"]
                        and normalized_source_revision["source_event_start"]
                        == previous_source_revision["source_event_end"] + 1,
                        "LINEAGE_SOURCE_REVISION_PREFIX_MISMATCH",
                        "A later source revision does not append to the exact prior bytes and event range.",
                        status="MISMATCH",
                        revision_scope_id=exact_scope_id,
                    )
                revision_number = revision_counts.get(exact_scope_id, 0) + 1
                previous_revision_cursor = revision_heads.get(exact_scope_id)
                revision = {
                    "revision_scope_id": exact_scope_id,
                    "revision_number": revision_number,
                    "previous_revision_cursor_sha256": previous_revision_cursor,
                    "revision_cursor_sha256": _revision_cursor(
                        scope_id=exact_scope_id,
                        revision_number=revision_number,
                        event_id=exact_event_id,
                        event_type=event_type,
                        visible_payload_sha256=visible_payload_sha256,
                        previous_cursor_sha256=previous_revision_cursor,
                        source_revision=normalized_source_revision,
                    ),
                    "source_revision": normalized_source_revision,
                }
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
                "revision_scope_id": revision["revision_scope_id"],
                "revision_number": revision["revision_number"],
                "previous_revision_cursor_sha256": revision[
                    "previous_revision_cursor_sha256"
                ],
                "revision_cursor_sha256": revision["revision_cursor_sha256"],
                "source_revision": revision.get("source_revision"),
                "actor_type": actor_type or _actor_for(event_type),
                "model": model,
                "submodel": submodel,
                "token_metrics": metrics,
                "visible_payload": safe_payload,
                "visible_payload_sha256": visible_payload_sha256,
                "private_reasoning_stored": False,
            }
            event["event_sha256"] = sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in event.items()
                        if key != "event_sha256"
                    }
                )
            )
            if matches:
                candidate = event
                if "revision_scope_id" not in matches[0]:
                    candidate = {
                        key: value
                        for key, value in event.items()
                        if key
                        not in {
                            "revision_scope_id",
                            "revision_number",
                            "previous_revision_cursor_sha256",
                            "revision_cursor_sha256",
                            "source_revision",
                        }
                    }
                    candidate["event_sha256"] = sha256_bytes(
                        canonical_json_bytes(
                            {
                                key: value
                                for key, value in candidate.items()
                                if key != "event_sha256"
                            }
                        )
                    )
                require(
                    matches[0] == candidate,
                    "LINEAGE_EVENT_ID_CONFLICT",
                    "An existing ChatLineage event uses the same ID with different content.",
                    status="BLOCKED",
                    event_id=event["event_id"],
                )
                self._sync_projection(events)
                self._sync_project_authority()
                return matches[0]
            prior_bytes = self.path.read_bytes() if self.path.exists() else b""
            events.append(event)
            payload = b"".join(canonical_json_bytes(item) for item in events)
            atomic_write_bytes(self.path, payload)
            try:
                self._sync_projection(events)
                self._sync_project_authority()
            except Exception:
                atomic_write_bytes(self.path, prior_bytes)
                self._sync_projection(events[:-1])
                self._sync_project_authority()
                raise
            return event

    def copy_from(self, source: str | Path) -> None:
        source_path = Path(source)
        with self._writer_lock():
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
        with self._writer_lock():
            result = self._sync_projection(self._events())
            project = self._sync_project_authority()
            if project is not None:
                result["project_authority"] = project
            capture_authority = CaptureRouteAuthority.for_lineage(self.path)
            if capture_authority is not None:
                result["capture_route"] = capture_authority.status()
            return result

    def query(
        self,
        value: str,
        *,
        limit: int = 20,
        task_id: str | None = None,
        revision_scope_id: str | None = None,
        after_revision_cursor_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Return bounded snippets and stable revision locators, never raw history."""

        require(
            1 <= int(limit) <= LINEAGE_QUERY_LIMIT_MAX,
            "LINEAGE_QUERY_LIMIT_INVALID",
            "ChatLineage query limits must be between 1 and 50.",
            status="BLOCKED",
        )
        query = _fts_query(value)
        status = self.projection_status()
        effective_scope_id = revision_scope_id
        cursor_filter = ""
        cursor_revision_number: int | None = None
        if after_revision_cursor_sha256 is not None:
            exact_cursor = str(after_revision_cursor_sha256).upper()
            require(
                bool(re.fullmatch(r"[0-9A-F]{64}", exact_cursor)),
                "LINEAGE_QUERY_CURSOR_INVALID",
                "The bounded ChatLineage query cursor must be one exact SHA-256.",
                status="BLOCKED",
            )
            cursor_connection = sqlite3.connect(self.sqlite_path, timeout=30)
            try:
                cursor_row = cursor_connection.execute(
                    "SELECT revision_scope_id,revision_number "
                    "FROM lineage_revision WHERE revision_cursor_sha256=?",
                    (exact_cursor,),
                ).fetchone()
            finally:
                cursor_connection.close()
            require(
                cursor_row is not None,
                "LINEAGE_QUERY_CURSOR_UNKNOWN",
                "The bounded ChatLineage query cursor is not in this authority.",
                status="MISMATCH",
            )
            cursor_scope_id = str(cursor_row[0])
            require(
                effective_scope_id in (None, cursor_scope_id),
                "LINEAGE_QUERY_CURSOR_SCOPE_MISMATCH",
                "The query cursor belongs to a different revision scope.",
                status="MISMATCH",
            )
            effective_scope_id = cursor_scope_id
            cursor_revision_number = int(cursor_row[1])
            cursor_filter = " AND r.revision_number > ?"
        parameters: list[Any] = [
            query,
            task_id,
            task_id,
            effective_scope_id,
            effective_scope_id,
        ]
        if cursor_revision_number is not None:
            parameters.append(cursor_revision_number)
        parameters.append(int(limit))
        connection = sqlite3.connect(self.sqlite_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT e.event_id,e.event_type,e.occurred_at,e.session_id,
                       e.task_id,e.actor_type,e.event_sha256,
                       r.revision_scope_id,r.revision_number,
                       r.previous_revision_cursor_sha256,
                       r.revision_cursor_sha256,r.source_revision_sha256,
                       snippet(lineage_fts,4,'[',']',' ... ',12) AS snippet
                FROM lineage_fts
                JOIN lineage_event e ON e.event_id=lineage_fts.event_id
                JOIN lineage_revision r ON r.event_id=e.event_id
                WHERE lineage_fts MATCH ?
                  AND (? IS NULL OR e.task_id=?)
                  AND (? IS NULL OR r.revision_scope_id=?)
                """
                + cursor_filter
                + " ORDER BY e.lineage_index LIMIT ?",
                tuple(parameters),
            ).fetchall()
        except sqlite3.Error as exc:
            raise EvidenceLaneError(
                "LINEAGE_QUERY_FAILED",
                "The bounded ChatLineage query could not be executed safely.",
                status="FAIL",
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            connection.close()
        hits = [
            {
                **dict(row),
                "locator": f"chatlineage://{row['session_id']}/{row['event_id']}",
            }
            for row in rows
        ]
        return {
            "status": "PASS",
            "schema": "evidence-lane.chat-lineage.bounded-query.v1",
            "result_state": "HITS" if hits else "EMPTY",
            "query_terms": len(query.split(" AND ")),
            "result_count": len(hits),
            "limit": int(limit),
            "hits": hits,
            "raw_history_returned": False,
            "private_reasoning_returned": False,
            "projection_sha256": status["projection_sha256"],
        }

    def window(
        self,
        *,
        limit: int = 20,
        task_id: str | None = None,
        revision_scope_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the latest bounded identity window without visible payload bytes."""

        require(
            1 <= int(limit) <= LINEAGE_QUERY_LIMIT_MAX,
            "LINEAGE_WINDOW_LIMIT_INVALID",
            "ChatLineage identity windows must be between 1 and 50.",
            status="BLOCKED",
        )
        status = self.projection_status()
        connection = sqlite3.connect(self.sqlite_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT e.event_id,e.lineage_index,e.event_type,e.occurred_at,
                           e.session_id,e.task_id,e.run_id,e.actor_type,
                           e.visible_payload_sha256,e.previous_event_sha256,
                           e.event_sha256,r.revision_scope_id,r.revision_number,
                           r.previous_revision_cursor_sha256,
                           r.revision_cursor_sha256,r.source_revision_sha256
                    FROM lineage_event e
                    JOIN lineage_revision r ON r.event_id=e.event_id
                    WHERE (? IS NULL OR e.task_id=?)
                      AND (? IS NULL OR r.revision_scope_id=?)
                    ORDER BY e.lineage_index DESC LIMIT ?
                ) ORDER BY lineage_index
                """,
                (
                    task_id,
                    task_id,
                    revision_scope_id,
                    revision_scope_id,
                    int(limit),
                ),
            ).fetchall()
        finally:
            connection.close()
        events = [
            {
                **dict(row),
                "locator": f"chatlineage://{row['session_id']}/{row['event_id']}",
            }
            for row in rows
        ]
        return {
            "status": "PASS",
            "schema": "evidence-lane.chat-lineage.identity-window.v1",
            "result_state": "HITS" if events else "EMPTY",
            "result_count": len(events),
            "limit": int(limit),
            "events": events,
            "raw_history_returned": False,
            "visible_payload_returned": False,
            "private_reasoning_returned": False,
            "projection_sha256": status["projection_sha256"],
        }


class ProjectChatLineage:
    """Project-wide first-read authority over every verified session lineage."""

    def __init__(self, lineage_root: str | Path) -> None:
        self.root = Path(lineage_root).resolve()
        self.path = self.root / "chat_lineage.sqlite"
        self.head_path = self.root / "chat_lineage_head.json"

    def _events(self) -> list[dict[str, Any]]:
        session_events: list[list[dict[str, Any]]] = []
        if not self.root.is_dir():
            return []
        for path in sorted(self.root.glob("*.jsonl"), key=lambda item: item.name):
            one_session: list[dict[str, Any]] = []
            for fallback_index, source_event in enumerate(
                ChatLineage(path).events(), start=1
            ):
                event = dict(source_event)
                event["_projection_lineage_index"] = int(
                    source_event.get("lineage_index") or fallback_index
                )
                one_session.append(event)
            if one_session:
                session_events.append(one_session)
        events: list[dict[str, Any]] = []
        positions = [0 for _ in session_events]
        while True:
            candidates = [
                (session[index], group_index)
                for group_index, session in enumerate(session_events)
                for index in [positions[group_index]]
                if index < len(session)
            ]
            if not candidates:
                break
            event, group_index = min(
                candidates,
                key=lambda item: (
                    str(item[0].get("occurred_at") or ""),
                    str(item[0].get("session_id") or ""),
                    int(item[0].get("_projection_lineage_index") or 0),
                    str(item[0].get("event_id") or ""),
                ),
            )
            events.append(event)
            positions[group_index] += 1
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
                CREATE TABLE IF NOT EXISTS project_lineage_revision(
                    event_id TEXT PRIMARY KEY REFERENCES project_lineage_event(event_id)
                        ON DELETE CASCADE,
                    revision_scope_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL CHECK(revision_number > 0),
                    previous_revision_cursor_sha256 TEXT,
                    revision_cursor_sha256 TEXT NOT NULL UNIQUE,
                    source_revision_json TEXT,
                    source_revision_sha256 TEXT,
                    UNIQUE(revision_scope_id, revision_number)
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
                "revision_cursor_schema": LINEAGE_REVISION_SCHEMA,
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
                connection.execute("DELETE FROM project_lineage_revision")
                connection.execute("DELETE FROM project_lineage_chunk")
                connection.execute("DELETE FROM project_lineage_event")
                connection.execute("DELETE FROM project_lineage_fts")
                connection.execute("DELETE FROM project_lineage_chunk_fts")
                connection.execute("DELETE FROM project_lineage_head")
                previous_state: str | None = None
                revision_counts: dict[str, int] = {}
                revision_heads: dict[str, str | None] = {}
                for index, event in enumerate(events, start=1):
                    projection = _projection_view(event, index)
                    fallback_scope_id = (
                        f"{event.get('session_id')}:"
                        f"{event.get('task_id') or event.get('session_id') or 'legacy'}"
                    )
                    recorded_scope = str(
                        event.get("revision_scope_id") or fallback_scope_id
                    )
                    revision = _revision_view(
                        event,
                        fallback_scope_id=fallback_scope_id,
                        fallback_revision_number=revision_counts.get(recorded_scope, 0)
                        + 1,
                        previous_cursor_sha256=revision_heads.get(recorded_scope),
                    )
                    revision_counts[recorded_scope] = revision["revision_number"]
                    revision_heads[recorded_scope] = revision[
                        "revision_cursor_sha256"
                    ]
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
                    connection.execute(
                        "INSERT INTO project_lineage_revision VALUES(?,?,?,?,?,?,?)",
                        (
                            event["event_id"],
                            revision["revision_scope_id"],
                            revision["revision_number"],
                            revision["previous_revision_cursor_sha256"],
                            revision["revision_cursor_sha256"],
                            (
                                json.dumps(
                                    revision["source_revision"],
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                if revision["source_revision"] is not None
                                else None
                            ),
                            revision["source_revision_sha256"],
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
            revision_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM project_lineage_revision"
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
            and revision_count == len(events)
            and chunk_count == chunk_fts_count,
            "PROJECT_LINEAGE_SQLITE_INVALID",
            "The project ChatLineage SQLite authority failed validation.",
            status="FAIL",
            integrity=integrity,
            foreign_key_errors=len(foreign_keys),
            fts_count=fts_count,
            revision_count=revision_count,
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
            "revision_count": revision_count,
            "link_count": link_count,
            "chunk_count": chunk_count,
            "chunk_fts_count": chunk_fts_count,
            "chunk_chars": LINEAGE_CHUNK_CHARS,
            **head_receipt,
        }

    def query(
        self,
        value: str,
        *,
        limit: int = 20,
        task_id: str | None = None,
        revision_scope_id: str | None = None,
    ) -> dict[str, Any]:
        """Query the project projection through bounded snippets and cursors."""

        require(
            1 <= int(limit) <= LINEAGE_QUERY_LIMIT_MAX,
            "PROJECT_LINEAGE_QUERY_LIMIT_INVALID",
            "Project ChatLineage query limits must be between 1 and 50.",
            status="BLOCKED",
        )
        query = _fts_query(value)
        status = self.sync()
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                """
                SELECT e.event_id,e.event_type,e.occurred_at,e.session_id,
                       e.task_id,e.actor_type,e.event_sha256,
                       r.revision_scope_id,r.revision_number,
                       r.previous_revision_cursor_sha256,
                       r.revision_cursor_sha256,r.source_revision_sha256,
                       snippet(project_lineage_fts,4,'[',']',' ... ',12) AS snippet
                FROM project_lineage_fts
                JOIN project_lineage_event e
                  ON e.event_id=project_lineage_fts.event_id
                JOIN project_lineage_revision r ON r.event_id=e.event_id
                WHERE project_lineage_fts MATCH ?
                  AND (? IS NULL OR e.task_id=?)
                  AND (? IS NULL OR r.revision_scope_id=?)
                ORDER BY e.global_index LIMIT ?
                """,
                (
                    query,
                    task_id,
                    task_id,
                    revision_scope_id,
                    revision_scope_id,
                    int(limit),
                ),
            ).fetchall()
        except sqlite3.Error as exc:
            raise EvidenceLaneError(
                "PROJECT_LINEAGE_QUERY_FAILED",
                "The bounded project ChatLineage query could not execute safely.",
                status="FAIL",
                details={"error_type": type(exc).__name__},
            ) from exc
        finally:
            connection.close()
        hits = [
            {
                **dict(row),
                "locator": f"chatlineage://{row['session_id']}/{row['event_id']}",
            }
            for row in rows
        ]
        return {
            "status": "PASS",
            "schema": "evidence-lane.project-chat-lineage.bounded-query.v1",
            "result_state": "HITS" if hits else "EMPTY",
            "result_count": len(hits),
            "limit": int(limit),
            "hits": hits,
            "raw_history_returned": False,
            "private_reasoning_returned": False,
            "projection_sha256": status["projection_sha256"],
        }


def lineage_sha256(path: str | Path) -> str:
    target = Path(path)
    return sha256_bytes(target.read_bytes() if target.exists() else b"")
