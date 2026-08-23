from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.lineage import ChatLineage, ProjectChatLineage


def _append(
    lineage: ChatLineage,
    *,
    event_id: str,
    text: str,
    second: int,
    source_revision: dict | None = None,
    revision_scope_id: str | None = None,
) -> dict:
    return lineage.append(
        event_type="user.steer",
        visible_payload={"prompt": text},
        occurred_at=f"2026-08-21T12:00:{second:02d}Z",
        session_id="session-revision",
        task_id="task-revision",
        event_id=event_id,
        actor_type="user",
        source_revision=source_revision,
        revision_scope_id=revision_scope_id,
    )


def test_revision_cursor_append_is_idempotent_chained_and_bounded(
    tmp_path: Path,
) -> None:
    lineage = ChatLineage(tmp_path / "lineage" / "session-revision.jsonl")
    first = _append(
        lineage,
        event_id="revision-1",
        text="preserve exact projector authority",
        second=1,
    )
    second = _append(
        lineage,
        event_id="revision-2",
        text="preserve exact projector authority after restart",
        second=2,
    )
    assert first["revision_number"] == 1
    assert second["revision_number"] == 2
    assert second["previous_revision_cursor_sha256"] == first[
        "revision_cursor_sha256"
    ]
    assert _append(
        lineage,
        event_id="revision-2",
        text="preserve exact projector authority after restart",
        second=2,
    ) == second
    assert len(lineage.events()) == 2

    window = lineage.window(limit=2, task_id="task-revision")
    assert window["result_count"] == 2
    assert window["visible_payload_returned"] is False
    assert all("visible_payload" not in event for event in window["events"])

    result = lineage.query(
        "projector authority",
        task_id="task-revision",
        after_revision_cursor_sha256=first["revision_cursor_sha256"],
    )
    assert result["status"] == "PASS"
    assert result["result_count"] == 1
    assert result["hits"][0]["event_id"] == "revision-2"
    assert result["hits"][0]["revision_number"] == 2
    assert "visible_payload_json" not in result["hits"][0]
    assert result["raw_history_returned"] is False
    assert result["private_reasoning_returned"] is False

    with sqlite3.connect(lineage.sqlite_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lineage_revision"
        ).fetchone()[0] == 2
    project = ProjectChatLineage(lineage.path.parent).query("projector authority")
    assert project["result_count"] == 2
    assert all("visible_payload_json" not in hit for hit in project["hits"])


def test_project_writer_lock_preserves_concurrent_revisions(tmp_path: Path) -> None:
    lineage = ChatLineage(tmp_path / "lineage" / "session-revision.jsonl")

    def append(number: int) -> dict:
        return _append(
            lineage,
            event_id=f"concurrent-{number}",
            text=f"concurrent visible event {number}",
            second=number,
        )

    with ThreadPoolExecutor(max_workers=6) as executor:
        receipts = list(executor.map(append, range(1, 7)))

    assert len(lineage.events()) == 6
    assert sorted(receipt["revision_number"] for receipt in receipts) == list(
        range(1, 7)
    )
    assert len({receipt["revision_cursor_sha256"] for receipt in receipts}) == 6
    status = lineage.projection_status()
    assert status["event_count"] == status["revision_count"] == 6
    assert status["project_authority"]["revision_count"] == 6


def test_projection_failure_rolls_back_the_whole_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lineage = ChatLineage(tmp_path / "lineage" / "session-revision.jsonl")
    first = _append(
        lineage,
        event_id="stable-before-fault",
        text="stable prior revision",
        second=1,
    )
    before_bytes = lineage.path.read_bytes()
    real_sync = lineage._sync_projection
    calls = 0

    def fail_once(events: list[dict]) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("fault injection after JSONL publication")
        return real_sync(events)

    monkeypatch.setattr(lineage, "_sync_projection", fail_once)
    with pytest.raises(RuntimeError, match="fault injection"):
        _append(
            lineage,
            event_id="rolled-back-revision",
            text="must not remain partially published",
            second=2,
        )
    assert lineage.path.read_bytes() == before_bytes
    assert lineage.events() == [first]
    assert real_sync(lineage.events())["event_count"] == 1


def test_source_revision_identity_is_exact_and_replay_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    lineage = ChatLineage(tmp_path / "lineage" / "session-revision.jsonl")
    source = {
        "source_sha256": "A" * 64,
        "size_bytes": 4096,
        "mtime_ns": 123456789,
        "task_window_id": "task3-export-window-2",
        "source_event_start": 5,
        "source_event_end": 9,
    }
    event = _append(
        lineage,
        event_id="source-revision",
        text="source-backed revision",
        second=1,
        source_revision=source,
        revision_scope_id="task3-export",
    )
    assert event["source_revision"]["source_sha256"] == "A" * 64
    with pytest.raises(EvidenceLaneError) as conflict:
        _append(
            lineage,
            event_id="source-revision",
            text="changed bytes under the same event identity",
            second=1,
            source_revision=source,
            revision_scope_id="task3-export",
        )
    assert conflict.value.code == "LINEAGE_EVENT_ID_CONFLICT"

    appended_source = {
        **source,
        "source_sha256": "B" * 64,
        "size_bytes": 5120,
        "source_event_start": 10,
        "source_event_end": 12,
        "prefix_sha256": "A" * 64,
        "prefix_size_bytes": 4096,
    }
    later = _append(
        lineage,
        event_id="source-revision-2",
        text="appended source-backed revision",
        second=2,
        source_revision=appended_source,
        revision_scope_id="task3-export",
    )
    assert later["revision_number"] == 2
    changed_prefix = {**appended_source, "source_sha256": "C" * 64}
    with pytest.raises(EvidenceLaneError) as prefix_conflict:
        _append(
            lineage,
            event_id="source-revision-3",
            text="must reject changed source prefix",
            second=3,
            source_revision=changed_prefix,
            revision_scope_id="task3-export",
        )
    assert prefix_conflict.value.code == "LINEAGE_SOURCE_REVISION_PREFIX_MISMATCH"

    invalid = {**source, "source_sha256": "not-a-sha"}
    with pytest.raises(EvidenceLaneError) as invalid_source:
        _append(
            lineage,
            event_id="invalid-source-revision",
            text="invalid source identity",
            second=2,
            source_revision=invalid,
            revision_scope_id="invalid-source",
        )
    assert invalid_source.value.code == "LINEAGE_SOURCE_REVISION_SHA256_INVALID"
