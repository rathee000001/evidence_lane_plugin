"""Exact, replay-safe Codex host Plan-window activation receipts.

The complete native Plan Lane remains the only row/status authority.  The host
artifact contains one permanent non-Delta progress item followed by one fixed
batch of at most nine executable Delta rows from that authority.  The batch
identity must already be persisted by the canonical Plan/Goal activation and
is never inferred from the ACTIVE row.  Task transitions update statuses in
place; Plan steers refresh only changed labels.  This module never claims
that the host rendered or accepted an artifact.  Visibility and the one State
Travel Plan-acceptance gate are separate observed facts; Sources/icon presence,
a backlog readback, or an empty host receipt are not substitutes for either
fact.
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .store import ProjectStore
from .timeutil import utc_now

_TRIGGERS = {
    "TASK_CLASSIFICATION_TRANSITION",
    "SESSION_START_COLD",
    "SESSION_START_WARM",
    "HOT_REATTACH",
    "PRECOMPACT",
    "POSTCOMPACT",
    "STATE_TRAVEL_DESTINATION_ENTRY",
    "USER_PROMPT_TURN",
    "GOAL_ACTIVE_TURN",
    "NO_GOAL_TURN",
    "PANEL_LOSS",
    "STALE_ARTIFACT",
    "EXPLICIT_HOST_OBSERVATION",
    "APP_RENDERER_RELOAD",
    "HOST_REACT_ROOT_RERENDER",
    "THREAD_HYDRATION_OVERFLOW",
    "COLLABORATION_OVERLAY_CONFLICT",
    "TASK_PANEL_LOSS",
    "CHANGES_SURFACE_LOSS",
    "PLAN_STEER_DELTA_APPLIED",
    "ACTIVE_ROW_TRANSITION",
}
_OBSERVATION_STATES = {
    "UNCONFIRMED",
    "MISSING",
    "STALE",
    "VISIBLE_UNACCEPTED",
    "VISIBLE_ACCEPTED",
}
_HOST_CAPABILITIES = {"SUPPORTED", "HOST_CAPABILITY_UNAVAILABLE"}
_VISIBLE_STATES = {"VISIBLE_UNACCEPTED", "VISIBLE_ACCEPTED"}
_HOST_PLAN_WINDOW_SIZE = 9
_HOST_PLAN_MAX_VISIBLE_ITEMS = 10
_HOST_PLAN_UI_MAX_LINES = 4
_HOST_PLAN_UI_MAX_CHARS_PER_LINE = 72
_HOST_PLAN_UI_MAX_TOTAL_CHARS = 288
_HOST_PLAN_HEADER_MAX_LINES = 1
_HOST_PLAN_HEADER_MAX_CHARS_PER_LINE = 160
_PLAN_STEER_TRIGGERS = {"PLAN_STEER_DELTA_APPLIED"}
_STATUS_TRANSITION_TRIGGERS = {
    "TASK_CLASSIFICATION_TRANSITION",
    "ACTIVE_ROW_TRANSITION",
}
_REACTIVATION_TRIGGERS = {
    "SESSION_START_COLD",
    "SESSION_START_WARM",
    "HOT_REATTACH",
    "STATE_TRAVEL_DESTINATION_ENTRY",
    "USER_PROMPT_TURN",
    "GOAL_ACTIVE_TURN",
    "PANEL_LOSS",
    "STALE_ARTIFACT",
    "APP_RENDERER_RELOAD",
    "HOST_REACT_ROOT_RERENDER",
    "THREAD_HYDRATION_OVERFLOW",
    "COLLABORATION_OVERLAY_CONFLICT",
    "TASK_PANEL_LOSS",
    "CHANGES_SURFACE_LOSS",
}


def _compact_ui_token(value: Any, *, max_chars: int) -> str:
    """Bind a long stored value to one short UI token without changing it."""

    normalized = " ".join(str(value or "UNDECLARED").split())
    if len(normalized) <= max_chars:
        return normalized
    digest = sha256_bytes(normalized.encode("utf-8"))[:4]
    suffix = f"~{digest}"
    return f"{normalized[: max_chars - len(suffix)]}{suffix}"


def _compact_ui_words(
    value: Any,
    *,
    max_words: int,
    max_word_chars: int = 7,
) -> str:
    """Return one or two readable words bound to the full stored value."""

    normalized = " ".join(str(value or "UNDECLARED").split())
    words = re.findall(r"[A-Za-z0-9]+", normalized)
    generic = {
        "EL",
        "CODEX",
        "PROPOSAL",
        "DELTA",
        "SUCCESSOR",
        "NORMALIZED",
    }
    meaningful = [
        word
        for word in words
        if word.upper() not in generic
        and re.fullmatch(r"T\d+", word.upper()) is None
        and re.fullmatch(r"(?:ROW|V|PV)?\d+", word.upper()) is None
        and re.fullmatch(r"[A-Fa-f0-9]{8,}", word) is None
    ]
    candidates = meaningful or words or ["UNDECLARED"]
    selected = candidates[:max_words]
    rendered_words = [word[:max_word_chars].upper() for word in selected]
    rendered = " ".join(rendered_words)
    shortened = (
        len(candidates) > max_words
        or len(meaningful) != len(words)
        or any(len(word) > max_word_chars for word in selected)
    )
    if shortened:
        rendered += f"~{sha256_bytes(normalized.encode('utf-8'))[:4]}"
    return rendered


def _git_route_ui_segment(value: Any, *, panel_role: Any) -> str:
    """Expose Git only on the row that actually executes a Git route."""

    if str(panel_role or "STANDARD").upper() in {
        "HIL_GATE",
        "PHYSICALLY_FINAL_HIL",
    }:
        return ""
    normalized = "_".join(str(value or "").upper().split())
    non_execution_markers = {
        "NO_COMMIT",
        "NOT_DECLARED",
        "UNASSIGNED",
        "NO_EXPLICIT",
        "PREPARE",
        "BEFORE",
        "FREEZE",
        "VERIFY",
        "IMPLEMENT",
    }
    if not normalized or any(marker in normalized for marker in non_execution_markers):
        return ""
    if any(marker in normalized for marker in ("COMMIT", "PUSH", "MERGE")):
        return " || Git=COMMIT"
    return ""


def _ui_abbreviation(value: Any, *, max_chars: int) -> str:
    """Return a readable UI abbreviation; the FTS locator binds full authority."""

    words = re.findall(r"[A-Za-z0-9]+", str(value or "UNDECLARED"))
    return (words[0] if words else "UND")[:max_chars].upper()


def _host_step_description_lines(value: Any) -> list[str]:
    """Render one complete human outcome across exactly two bounded UI lines."""

    normalized = " ".join(str(value or "Outcome is not declared.").split())
    digest = sha256_bytes(normalized.encode("utf-8"))[:4]
    content_lines = textwrap.wrap(
        normalized,
        width=_HOST_PLAN_UI_MAX_CHARS_PER_LINE - len("Do: "),
        break_long_words=True,
        break_on_hyphens=False,
    )
    if len(content_lines) == 1:
        words = normalized.split()
        require(
            len(words) >= 2,
            "HOST_PLAN_UI_DESCRIPTION_TOO_SHORT",
            "A host Step Task List description requires at least two words.",
            status="MISMATCH",
        )
        split_at = max(1, len(words) // 2)
        content_lines = [
            " ".join(words[:split_at]),
            " ".join(words[split_at:]),
        ]
    omitted = len(content_lines) > 2
    first_content = content_lines[0]
    second_content = content_lines[1]
    if omitted:
        suffix = f" ...~{digest}"
        second_capacity = _HOST_PLAN_UI_MAX_CHARS_PER_LINE - len("   ") - len(suffix)
        if len(second_content) > second_capacity:
            bounded = second_content[:second_capacity].rstrip()
            if " " in bounded:
                word_bounded = bounded.rsplit(" ", 1)[0].rstrip()
                bounded = word_bounded or bounded
            second_content = bounded
        second_content = f"{second_content}{suffix}"
    return [f"Do: {first_content}", f"   {second_content}"]


def _host_step_ui_label(
    row: dict[str, Any],
    *,
    row_number_by_task_id: dict[str, int],
) -> str:
    """Return two authority lines plus two human-readable outcome lines."""

    dependencies = [str(value) for value in row.get("dependencies") or []]
    dependency_refs = [
        (
            f"R{row_number_by_task_id[dependency]}"
            if dependency in row_number_by_task_id
            else f"EXT~{sha256_bytes(dependency.encode('utf-8'))[:4]}"
        )
        for dependency in dependencies
    ]
    dependency_token = _compact_ui_token(
        "+".join(dependency_refs) or "ROOT",
        max_chars=6,
    )
    task_id = str(row["task_id"])
    git_segment = _git_route_ui_segment(
        row.get("git_commit_stage"),
        panel_role=row.get("panel_role"),
    )
    identity_line = (
        f"R{row['number']}|"
        f"ID={_compact_ui_words(task_id, max_words=1, max_word_chars=5)}|"
        f"STATE={_ui_abbreviation(row.get('lifecycle_status'), max_chars=6)}|"
        f"FTS={row['number']}:{sha256_bytes(task_id.encode('utf-8'))[:8]}"
        f"{'|Git' if git_segment else ''}"
    )
    classification_line = (
        f"C={_ui_abbreviation(row.get('task_classification'), max_chars=3)}|"
        f"G={_ui_abbreviation(row.get('plan_group'), max_chars=3)}|"
        f"D={dependency_token}|"
        f"B={_ui_abbreviation(row.get('commit_batch_id'), max_chars=4)}"
    )
    lines = [
        identity_line,
        classification_line,
        *_host_step_description_lines(row.get("step")),
    ]
    require(
        len(lines) == _HOST_PLAN_UI_MAX_LINES
        and all(len(line) <= _HOST_PLAN_UI_MAX_CHARS_PER_LINE for line in lines)
        and sum(len(line) for line in lines) <= _HOST_PLAN_UI_MAX_TOTAL_CHARS,
        "HOST_PLAN_UI_LABEL_BOUND_EXCEEDED",
        "A host Step Task List row violated the four-line UI projection law.",
        status="MISMATCH",
        row=row.get("number"),
        task_id=row.get("task_id"),
    )
    return "\n".join(lines)


def _session_record(
    store: ProjectStore,
    *,
    project_id: str,
    evidence_session_id: str,
    host_task_id: str,
) -> dict[str, Any]:
    require(
        evidence_session_id.startswith("session_")
        and evidence_session_id.replace("_", "").isalnum(),
        "HOST_PLAN_SESSION_ID_INVALID",
        "Host Plan rehydration requires one exact Evidence Lane session ID.",
        status="BLOCKED",
    )
    project_root = store.project_root(project_id)
    session_path = (project_root / "sessions" / f"{evidence_session_id}.json").resolve()
    session_path.relative_to(project_root)
    require(
        session_path.is_file(),
        "HOST_PLAN_SESSION_REQUIRED",
        "Host Plan rehydration requires the exact governed session.",
        status="BLOCKED",
        project_id=project_id,
        evidence_session_id=evidence_session_id,
    )
    session = cast(dict[str, Any], json.loads(session_path.read_text(encoding="utf-8")))
    require(
        session.get("project_id") == project_id
        and session.get("session_id") == evidence_session_id,
        "HOST_PLAN_PROJECT_SESSION_BINDING_MISMATCH",
        "The governed session does not belong to the requested project.",
        status="MISMATCH",
    )
    current_host_task_id = str(
        cast(dict[str, Any], session.get("metadata") or {}).get(
            "current_host_session_id"
        )
        or ""
    ).strip()
    require(
        bool(host_task_id)
        and bool(current_host_task_id)
        and host_task_id == current_host_task_id,
        "HOST_PLAN_EXACT_TASK_BINDING_REQUIRED",
        "Host Plan rehydration requires the exact currently bound Codex task ID.",
        status="MISMATCH",
    )
    return session


def _exact_projection(
    store: ProjectStore,
    *,
    project_id: str,
    fixed_window_task_ids: list[str] | None = None,
) -> dict[str, Any]:
    backlog = store.backlog_status(project_id)
    goal = cast(dict[str, Any], backlog.get("goal_projection") or {})
    full_rows = [dict(row) for row in goal.get("rows") or [] if isinstance(row, dict)]
    require(
        goal.get("canonical_authority") == "PLAN_LANE" and bool(full_rows),
        "HOST_PLAN_CANONICAL_AUTHORITY_REQUIRED",
        "A non-empty canonical Plan Lane projection is required.",
        status="BLOCKED",
    )
    row_start = int(goal.get("row_start") or 0)
    row_end = int(goal.get("row_end") or 0)
    require(
        len(full_rows) == int(goal.get("task_count") or -1)
        and [int(row.get("number") or 0) for row in full_rows]
        == list(range(row_start, row_end + 1)),
        "HOST_PLAN_ROWS_NOT_CONTIGUOUS",
        "The current executable Plan rows are not complete and contiguous.",
        status="MISMATCH",
    )
    active = [row for row in full_rows if row.get("status") == "in_progress"]
    require(
        len(active) == 1 and active[0].get("lifecycle_status") == "ACTIVE",
        "HOST_PLAN_SOLE_ACTIVE_ROW_REQUIRED",
        "The host Plan requires exactly one native ACTIVE row.",
        status="MISMATCH",
        active_count=len(active),
    )
    physical_final_rows = [
        row for row in full_rows if row.get("panel_role") == "PHYSICALLY_FINAL_HIL"
    ]
    require(
        len(physical_final_rows) == 1
        and physical_final_rows[0].get("task_id") == full_rows[-1].get("task_id")
        and physical_final_rows[0].get("number") == row_end,
        "HOST_PLAN_PHYSICALLY_FINAL_HIL_INVALID",
        "Exactly one PHYSICALLY_FINAL_HIL row must be physically last.",
        status="MISMATCH",
    )
    pointer = store.pointer(project_id).as_dict()
    full_items = []
    full_item_bindings = []
    row_number_by_task_id = {
        str(row["task_id"]): int(row["number"]) for row in full_rows
    }
    for row in full_rows:
        label = str(row.get("visible_label") or "")
        require(
            label.startswith(f"Row {row['number']} / {row['task_id']} ")
            and str(row.get("step") or "") in label
            and "steer_deltas" not in label,
            "HOST_PLAN_VISIBLE_LABEL_INVALID",
            "Every host Plan item must use the exact metadata-rich visible label.",
            status="MISMATCH",
            row=row.get("number"),
            task_id=row.get("task_id"),
        )
        full_items.append(
            {
                "step": _host_step_ui_label(
                    row,
                    row_number_by_task_id=row_number_by_task_id,
                ),
                "status": str(row["status"]),
            }
        )
        full_item_bindings.append(
            {
                "number": int(row["number"]),
                "task_id": str(row["task_id"]),
                "canonical_row_sha256": sha256_bytes(canonical_json_bytes(row)),
                "full_visible_label_sha256": sha256_bytes(label.encode("utf-8")),
                "full_description_sha256": sha256_bytes(
                    str(row.get("step") or "").encode("utf-8")
                ),
                "primary_query_key": str(row["task_id"]),
                "fts_link_id": (
                    f"plan:{row['number']}:"
                    f"{sha256_bytes(str(row['task_id']).encode('utf-8'))[:12]}"
                ),
                "linked_detail_query": "BOUNDED_NATIVE_PLAN_AND_FTS",
            }
        )

    active_index = full_rows.index(active[0])
    next_hil_rows = [
        row
        for row in full_rows[active_index:]
        if row.get("panel_role") in {"HIL_GATE", "PHYSICALLY_FINAL_HIL"}
        and row.get("status") in {"in_progress", "pending"}
    ]
    requested_window_task_ids = [
        str(task_id).strip()
        for task_id in fixed_window_task_ids or []
        if str(task_id).strip()
    ]
    require(
        bool(requested_window_task_ids),
        "HOST_PLAN_FIXED_BATCH_REQUIRED",
        "The canonical fixed host batch must be persisted before projection; sliding and de-dup fallbacks are disabled.",
        status="BLOCKED",
    )
    require(
        len(requested_window_task_ids) <= _HOST_PLAN_WINDOW_SIZE
        and len(requested_window_task_ids) == len(set(requested_window_task_ids))
        and str(active[0]["task_id"]) in requested_window_task_ids,
        "HOST_PLAN_FIXED_WINDOW_BINDING_INVALID",
        "The fixed host Plan batch must be unique, bounded, and contain ACTIVE.",
        status="MISMATCH",
    )
    task_index = {str(row["task_id"]): index for index, row in enumerate(full_rows)}
    require(
        set(requested_window_task_ids).issubset(task_index),
        "HOST_PLAN_FIXED_WINDOW_TASK_UNKNOWN",
        "The fixed host Plan batch references a task outside the live Plan.",
        status="MISMATCH",
    )
    requested_indexes = [task_index[task_id] for task_id in requested_window_task_ids]
    require(
        requested_indexes
        == list(
            range(
                requested_indexes[0],
                requested_indexes[0] + len(requested_indexes),
            )
        ),
        "HOST_PLAN_FIXED_WINDOW_NOT_CONTIGUOUS",
        "The fixed host Plan batch task identities are not contiguous.",
        status="MISMATCH",
    )
    window_start_index = requested_indexes[0]
    requested_window_size = len(requested_indexes)
    window_index = window_start_index // _HOST_PLAN_WINDOW_SIZE
    window_end_index = min(
        window_start_index + requested_window_size,
        len(full_rows),
    )
    window_rows = full_rows[window_start_index:window_end_index]
    delta_items = full_items[window_start_index:window_end_index]
    delta_item_bindings = full_item_bindings[window_start_index:window_end_index]
    require(
        active[0] in window_rows
        and len(delta_items) <= _HOST_PLAN_WINDOW_SIZE
        and len(delta_items) == len(window_rows),
        "HOST_PLAN_ACTIVE_WINDOW_INVALID",
        "The host Plan window must contain the sole ACTIVE row and at most ten rows.",
        status="MISMATCH",
    )

    completed_windows = []
    for completed_index in range(window_index):
        start = completed_index * _HOST_PLAN_WINDOW_SIZE
        end = min(start + _HOST_PLAN_WINDOW_SIZE, len(full_rows))
        completed_rows = full_rows[start:end]
        require(
            bool(completed_rows)
            and all(row.get("status") == "completed" for row in completed_rows),
            "HOST_PLAN_PRIOR_WINDOW_NOT_TERMINAL",
            "Every host Plan window before the ACTIVE window must be terminal.",
            status="MISMATCH",
            window_index=completed_index,
        )
        completed_body = {
            "window_index": completed_index,
            "row_start": int(completed_rows[0]["number"]),
            "row_end": int(completed_rows[-1]["number"]),
            "item_count": len(completed_rows),
            "statuses": [str(row["status"]) for row in completed_rows],
            "task_ids": [str(row["task_id"]) for row in completed_rows],
        }
        completed_windows.append(
            {
                **completed_body,
                "window_sha256": sha256_bytes(canonical_json_bytes(completed_body)),
            }
        )

    window_row_start = int(window_rows[0]["number"])
    window_row_end = int(window_rows[-1]["number"])
    next_start_index = window_end_index
    next_window_row_start = (
        int(full_rows[next_start_index]["number"])
        if next_start_index < len(full_rows)
        else None
    )
    next_window_row_end = (
        int(
            full_rows[
                min(next_start_index + _HOST_PLAN_WINDOW_SIZE, len(full_rows)) - 1
            ]["number"]
        )
        if next_window_row_start is not None
        else None
    )
    next_hil = next_hil_rows[0] if next_hil_rows else None
    completed_count = sum(1 for row in full_rows if row.get("status") == "completed")
    queued_rows = [row for row in full_rows if row.get("status") == "pending"]
    queued_after_window = [
        row for row in full_rows[window_end_index:] if row.get("status") == "pending"
    ]
    final_hil_text = " ".join(
        [str(full_rows[-1].get("step") or "")]
        + [
            str(delta.get("text") or "")
            for delta in full_rows[-1].get("steer_deltas") or []
            if isinstance(delta, dict)
        ]
    )
    final_hil_candidate_matches: list[str] = []
    for pattern in (
        r"\bPHYSICALLY\s+FINAL\s+(PV\d+)\s+HIL\b",
        r"\bFINAL\s+(PV\d+)\s+HIL\b",
        r"\b(PV\d+)\s+PHYSICALLY\s+FINAL\b",
    ):
        final_hil_candidate_matches = re.findall(
            pattern,
            final_hil_text,
            flags=re.IGNORECASE,
        )
        if final_hil_candidate_matches:
            break
    final_hil_candidate = (
        final_hil_candidate_matches[0].upper() if final_hil_candidate_matches else None
    )
    window_ui_fingerprint_body = {
        "row_start": window_row_start,
        "row_end": window_row_end,
        "items": delta_items,
    }
    window_ui_fingerprint_sha256 = sha256_bytes(
        canonical_json_bytes(window_ui_fingerprint_body)
    )
    continuity_header = {
        "schema": "evidence-lane.host-plan-continuity-header.v1",
        "surface": "HOST_STEP_TASK_LIST_HEADER",
        "purpose": "CROSS_WINDOW_EXECUTION_CONTINUITY",
        "accepted_pv": pointer.get("accepted_pv"),
        "pointer_generation": int(pointer.get("generation") or 0),
        "absolute_active_row": int(active[0]["number"]),
        "absolute_active_task_id": str(active[0]["task_id"]),
        "window_row_start": window_row_start,
        "window_row_end": window_row_end,
        "window_ordinal": window_index + 1,
        "window_count": (len(full_rows) + _HOST_PLAN_WINDOW_SIZE - 1)
        // _HOST_PLAN_WINDOW_SIZE,
        "total_executable_count": len(full_rows),
        "completed_count": completed_count,
        "queued_count": len(queued_rows),
        "queued_row_start": (int(queued_rows[0]["number"]) if queued_rows else None),
        "queued_row_end": (int(queued_rows[-1]["number"]) if queued_rows else None),
        "queued_after_window_count": len(queued_after_window),
        "queued_after_window_row_start": (
            int(queued_after_window[0]["number"]) if queued_after_window else None
        ),
        "queued_after_window_row_end": (
            int(queued_after_window[-1]["number"]) if queued_after_window else None
        ),
        "next_hil_boundary_row": (
            int(next_hil["number"]) if next_hil is not None else None
        ),
        "next_hil_boundary_task_id": (
            str(next_hil["task_id"]) if next_hil is not None else None
        ),
        "physically_final_row": int(full_rows[-1]["number"]),
        "physically_final_task_id": str(full_rows[-1]["task_id"]),
        "physically_final_candidate": final_hil_candidate,
        "physically_final_hil_scope": "FINAL_PROJECT_HIL_NOT_INTERMEDIATE",
        "detailed_hil_queue_surface": "EVIDENCE_LANE_PROJECT_RENDERER",
        "hil_controls_in_step_task_list": False,
    }
    continuity_header["visible_text"] = (
        f"{continuity_header['accepted_pv'] or 'NONE'}/generation "
        f"{continuity_header['pointer_generation']} | "
        f"ACTIVE R{continuity_header['absolute_active_row']} | "
        f"ACTIVE BATCH R{window_row_start}-R{window_row_end} | "
        f"NEXT_HIL R{continuity_header['next_hil_boundary_row'] or 'NONE'} | "
        f"FINAL_HIL R{continuity_header['physically_final_row']}"
    )
    header_lines = str(continuity_header["visible_text"]).splitlines()
    require(
        len(header_lines) == _HOST_PLAN_HEADER_MAX_LINES
        and all(
            len(line) <= _HOST_PLAN_HEADER_MAX_CHARS_PER_LINE for line in header_lines
        ),
        "HOST_PLAN_HEADER_BOUND_EXCEEDED",
        "The fixed host progress header exceeded its one-line UI bound.",
        status="MISMATCH",
        row_start=window_row_start,
        row_end=window_row_end,
    )
    header_item: dict[str, str] = {
        "step": str(continuity_header["visible_text"]),
        "status": "completed",
    }
    items: list[dict[str, Any]] = [header_item, *delta_items]
    item_bindings = [
        {
            "kind": "FIXED_PROGRESS_HEADER",
            "canonical_row": None,
            "task_id": None,
            "primary_query_key": None,
            "linked_detail_query": "HEADER_DERIVED_FROM_NATIVE_PLAN_COUNTS",
        },
        *delta_item_bindings,
    ]
    require(
        len(items) <= _HOST_PLAN_MAX_VISIBLE_ITEMS
        and items[0]["status"] == "completed"
        and sum(item["status"] == "in_progress" for item in items) == 1,
        "HOST_PLAN_FIXED_HEADER_WINDOW_INVALID",
        "The host Plan must contain one fixed progress header plus at most nine Delta rows and one ACTIVE Delta.",
        status="MISMATCH",
    )
    window_ui_fingerprint_body = {
        "row_start": window_row_start,
        "row_end": window_row_end,
        "items": items,
    }
    window_ui_fingerprint_sha256 = sha256_bytes(
        canonical_json_bytes(window_ui_fingerprint_body)
    )
    projection_body = {
        "schema": "evidence-lane.host-plan-window-projection.v2",
        "project_id": project_id,
        "canonical_authority": "PLAN_LANE",
        "canonical_plan_sha256": goal.get("canonical_plan_sha256"),
        "executable_projection_sha256": goal.get("projection_sha256"),
        "visible_label_contract": goal.get("visible_label_contract"),
        "visible_label_metadata_schema": goal.get("visible_label_metadata_schema"),
        "full_row_start": row_start,
        "full_row_end": row_end,
        "total_executable_count": len(full_rows),
        "full_task_ids": [str(row["task_id"]) for row in full_rows],
        "window_size": _HOST_PLAN_WINDOW_SIZE,
        "fixed_header_item_count": 1,
        "maximum_host_item_count": _HOST_PLAN_MAX_VISIBLE_ITEMS,
        "window_index": window_index,
        "window_ordinal": window_index + 1,
        "window_count": (len(full_rows) + _HOST_PLAN_WINDOW_SIZE - 1)
        // _HOST_PLAN_WINDOW_SIZE,
        "row_start": window_row_start,
        "row_end": window_row_end,
        "item_count": len(items),
        "delta_item_count": len(delta_items),
        "window_ui_fingerprint_sha256": window_ui_fingerprint_sha256,
        "ui_row_max_lines": _HOST_PLAN_UI_MAX_LINES,
        "ui_line_max_chars": _HOST_PLAN_UI_MAX_CHARS_PER_LINE,
        "ui_projection_contains_full_plan_row": False,
        "step_one_is_delta_row": False,
        "delta_rows_begin_at_host_step": 2,
        "delta_rows_end_at_host_step": len(items),
        "ui_overflow_creates_canonical_row": False,
        "window_task_ids": [str(row["task_id"]) for row in window_rows],
        "item_bindings": item_bindings,
        "detail_retrieval_contract": {
            "full_row_authority": "PLAN_LANE",
            "primary_lookup": "EXACT_TASK_ID",
            "linked_evidence_lookup": "BOUNDED_FTS_ON_DEMAND",
            "raw_pv_loaded_into_model_context": False,
            "raw_chat_scrollback_loaded_into_model_context": False,
            "ui_projection_reconstructs_full_row": False,
        },
        "sole_active_row": active[0]["number"],
        "sole_active_task_id": active[0]["task_id"],
        "completed_window_count": len(completed_windows),
        "completed_window_history": completed_windows,
        "next_window_row_start": next_window_row_start,
        "next_window_row_end": next_window_row_end,
        "remaining_after_current_window": len(full_rows) - window_end_index,
        "continuity_header": continuity_header,
        "host_update_plan_contract": {
            "explanation": (
                "Evidence Lane bounded Step Task List | Step 1 fixed progress | "
                "Steps 2-10 native Delta rows"
            ),
            "plan": items,
            "source_window_ui_fingerprint_sha256": window_ui_fingerprint_sha256,
            "native_contract_must_be_forwarded_unchanged": True,
            "manual_summary_projection_forbidden": True,
            "fallback_projection_forbidden": True,
            "header_surface": "HOST_STEP_TASK_LIST_STEP_1",
            "header_role": "FIXED_PROGRESS_HEADER",
            "header_is_plan_item": True,
            "header_is_delta_row": False,
            "detailed_hil_queue_in_step_task_list": False,
        },
        "final_window_may_contain_fewer_than_ten": True,
        "physically_final_hil_row": full_rows[-1]["number"],
        "physically_final_hil_task_id": full_rows[-1]["task_id"],
        "physically_final_hil_visible_in_window": (full_rows[-1] in window_rows),
        "full_ledger_preserved_outside_host_window": True,
        "window_advancement_rewrites_plan_history": False,
        "native_host_plan_projection_only_law": {
            "law_id": "NATIVE_HOST_PLAN_PROJECTION_ONLY_LAW",
            "authority_route": "PV_TASK_BACKLOG_HOST_UPDATE_PLAN_CONTRACT",
            "host_action": "FORWARD_EXPLANATION_AND_PLAN_UNCHANGED",
            "panel_loss_action": "RELOCK_SAME_PERSISTED_CONTRACT_AND_FINGERPRINT",
            "manual_summary_projection_allowed": False,
            "reconstructed_label_projection_allowed": False,
            "sliding_window_projection_allowed": False,
            "generic_fallback_projection_allowed": False,
        },
        "items": items,
    }
    return {
        **projection_body,
        "projection_sha256": sha256_bytes(canonical_json_bytes(projection_body)),
    }


def _normalized_observation(
    raw: dict[str, Any] | None,
    *,
    project_id: str,
    host_task_id: str,
    projection: dict[str, Any],
) -> dict[str, Any]:
    observation = dict(raw or {})
    state = str(observation.get("state") or "UNCONFIRMED").strip().upper()
    require(
        state in _OBSERVATION_STATES,
        "HOST_PLAN_OBSERVATION_STATE_INVALID",
        "The host Plan artifact observation state is unsupported.",
        status="BLOCKED",
        state=state,
    )
    observed_project_id = str(observation.get("project_id") or project_id)
    observed_host_task_id = str(observation.get("host_task_id") or host_task_id)
    require(
        observed_project_id == project_id and observed_host_task_id == host_task_id,
        "HOST_PLAN_OBSERVATION_BINDING_MISMATCH",
        "A host Plan observation cannot cross project or task boundaries.",
        status="MISMATCH",
    )
    update_receipt = observation.get("host_update_plan_receipt")
    update_receipt_nonempty = isinstance(update_receipt, dict) and bool(update_receipt)
    visible = state in _VISIBLE_STATES
    if visible:
        require(
            observation.get("surface") == "CODEX_RIGHT_SIDE_PLAN"
            and bool(str(observation.get("artifact_id") or "").strip())
            and observation.get("projection_sha256") == projection["projection_sha256"]
            and observation.get("item_count") == projection["item_count"],
            "HOST_PLAN_VISIBLE_ARTIFACT_PROOF_INVALID",
            "Visible Plan proof must bind the right-side Plan artifact to the exact projection.",
            status="MISMATCH",
        )
    accepted = state == "VISIBLE_ACCEPTED"
    if accepted:
        require(
            bool(str(observation.get("explicit_acceptance_event_id") or "").strip())
            and observation.get("acceptance_control") == "ACCEPT",
            "HOST_PLAN_EXPLICIT_ACCEPTANCE_PROOF_REQUIRED",
            "Host Plan acceptance requires one explicit Accept-control event.",
            status="MISMATCH",
        )
    return {
        "state": state,
        "project_id": project_id,
        "host_task_id": host_task_id,
        "observation_event_id": (
            str(observation.get("observation_event_id") or "").strip() or None
        ),
        "surface": observation.get("surface") if visible else None,
        "artifact_id": observation.get("artifact_id") if visible else None,
        "projection_sha256": (
            observation.get("projection_sha256") if visible else None
        ),
        "item_count": observation.get("item_count") if visible else None,
        "explicit_acceptance_event_id": (
            observation.get("explicit_acceptance_event_id") if accepted else None
        ),
        "acceptance_control": (
            observation.get("acceptance_control") if accepted else None
        ),
        "host_update_plan_receipt_nonempty": update_receipt_nonempty,
        "empty_update_plan_receipt_is_visibility_proof": False,
        "sources_presence_is_visibility_proof": False,
        "icon_presence_is_visibility_proof": False,
        "native_backlog_readback_is_visibility_proof": False,
        "artifact_visibility_proven": visible,
        "explicit_host_plan_acceptance_proven": accepted,
    }


def _worktree_binding_sha256(repository_path: str | Path) -> str:
    """Return one bounded, platform-stable identity for the governed worktree."""

    normalized = str(Path(repository_path).expanduser().resolve()).replace("\\", "/")
    return sha256_bytes(normalized.casefold().encode("utf-8"))


def _normalized_changes_observation(
    raw: dict[str, Any] | None,
    *,
    project_id: str,
    host_task_id: str,
    active_plan_task_id: str,
    worktree_binding_sha256: str,
) -> dict[str, Any]:
    """Validate a bounded, independent observation of the native Changes surface."""

    observation = dict(raw or {})
    state = str(observation.get("state") or "UNCONFIRMED").strip().upper()
    require(
        state in _OBSERVATION_STATES,
        "HOST_CHANGES_OBSERVATION_STATE_INVALID",
        "The host Changes artifact observation state is unsupported.",
        status="BLOCKED",
        state=state,
    )
    observed_project_id = str(observation.get("project_id") or project_id)
    observed_host_task_id = str(observation.get("host_task_id") or host_task_id)
    observed_worktree = str(
        observation.get("worktree_binding_sha256") or worktree_binding_sha256
    ).strip()
    require(
        observed_project_id == project_id
        and observed_host_task_id == host_task_id
        and observed_worktree == worktree_binding_sha256,
        "HOST_CHANGES_OBSERVATION_BINDING_MISMATCH",
        "A host Changes observation cannot cross project, task, or worktree boundaries.",
        status="MISMATCH",
    )
    visible = state in _VISIBLE_STATES
    if visible:
        require(
            observation.get("surface") == "CODEX_RIGHT_SIDE_CHANGES"
            and bool(str(observation.get("artifact_id") or "").strip())
            and observation.get("active_plan_task_id") == active_plan_task_id
            and observation.get("worktree_binding_sha256") == worktree_binding_sha256,
            "HOST_CHANGES_VISIBLE_ARTIFACT_PROOF_INVALID",
            (
                "Visible Changes proof must bind the exact native surface, "
                "active Plan task, and worktree."
            ),
            status="MISMATCH",
        )
    return {
        "state": state,
        "project_id": project_id,
        "host_task_id": host_task_id,
        "active_plan_task_id": active_plan_task_id,
        "worktree_binding_sha256": worktree_binding_sha256,
        "observation_event_id": (
            str(observation.get("observation_event_id") or "").strip() or None
        ),
        "surface": observation.get("surface") if visible else None,
        "artifact_id": observation.get("artifact_id") if visible else None,
        "artifact_visibility_proven": visible,
        "missing_or_stale": state in {"MISSING", "STALE"},
        "private_diff_content_returned": False,
    }


def prepare_host_plan_rehydration(
    store_root: str | Path,
    *,
    project_id: str,
    evidence_session_id: str,
    host_task_id: str,
    trigger: str,
    trigger_event_id: str,
    host_capability: str = "SUPPORTED",
    observed_artifact: dict[str, Any] | None = None,
    observed_changes_artifact: dict[str, Any] | None = None,
    host_goal_active: bool | None = None,
    affected_plan_task_ids: list[str] | None = None,
    fixed_window_task_ids: list[str] | None = None,
    reuse_previous_window: bool = True,
) -> dict[str, Any]:
    """Seal one exact host Plan-window activation request or verified no-op.

    The returned projection is suitable for the host ``update_plan`` action.
    The function itself never invokes that action and never fabricates host UI
    visibility or acceptance.
    """

    exact_trigger = str(trigger or "").strip().upper()
    exact_event_id = str(trigger_event_id or "").strip()
    exact_capability = str(host_capability or "").strip().upper()
    require(
        exact_trigger in _TRIGGERS and bool(exact_event_id),
        "HOST_PLAN_REHYDRATION_TRIGGER_INVALID",
        "Host Plan rehydration requires one supported trigger and event identity.",
        status="BLOCKED",
    )
    require(
        exact_capability in _HOST_CAPABILITIES,
        "HOST_PLAN_CAPABILITY_STATUS_INVALID",
        "The host Plan capability status is unsupported.",
        status="BLOCKED",
    )
    exact_affected_task_ids = sorted(
        {
            str(task_id).strip()
            for task_id in affected_plan_task_ids or []
            if str(task_id).strip()
        }
    )
    require(
        exact_trigger not in _PLAN_STEER_TRIGGERS or bool(exact_affected_task_ids),
        "HOST_PLAN_STEER_AFFECTED_TASK_REQUIRED",
        "A Plan-steer host decision requires the exact affected Plan task IDs.",
        status="BLOCKED",
    )
    store = ProjectStore(store_root)
    session = _session_record(
        store,
        project_id=project_id,
        evidence_session_id=evidence_session_id,
        host_task_id=host_task_id,
    )
    metadata = cast(dict[str, Any], session.get("metadata") or {})
    previous_window = cast(dict[str, Any], metadata.get("host_plan_window") or {})
    requested_window_task_ids = [
        str(task_id).strip()
        for task_id in fixed_window_task_ids or []
        if str(task_id).strip()
    ]
    explicit_fixed_window = bool(requested_window_task_ids)
    if not explicit_fixed_window and reuse_previous_window:
        requested_window_task_ids = [
            str(task_id).strip()
            for task_id in previous_window.get("window_task_ids") or []
            if str(task_id).strip()
        ]
        if requested_window_task_ids:
            live_rows = cast(
                list[dict[str, Any]],
                store.backlog_status(project_id)
                .get("goal_projection", {})
                .get("rows", []),
            )
            active_task_ids = [
                str(row.get("task_id") or "")
                for row in live_rows
                if row.get("status") == "in_progress"
            ]
            if (
                len(active_task_ids) == 1
                and active_task_ids[0] not in requested_window_task_ids
            ):
                requested_window_task_ids = []
    pointer_before = store.pointer(project_id).as_dict()
    projection = _exact_projection(
        store,
        project_id=project_id,
        fixed_window_task_ids=requested_window_task_ids,
    )
    require(
        set(exact_affected_task_ids).issubset(set(projection["full_task_ids"])),
        "HOST_PLAN_STEER_AFFECTED_TASK_UNKNOWN",
        "A Plan-steer host decision may reference only current native Plan tasks.",
        status="MISMATCH",
        unknown_task_ids=sorted(
            set(exact_affected_task_ids).difference(projection["full_task_ids"])
        ),
    )
    observation = _normalized_observation(
        observed_artifact,
        project_id=project_id,
        host_task_id=host_task_id,
        projection=projection,
    )
    worktree_binding_sha256 = _worktree_binding_sha256(
        store.config(project_id).repository_path
    )
    changes_observation = _normalized_changes_observation(
        observed_changes_artifact,
        project_id=project_id,
        host_task_id=host_task_id,
        active_plan_task_id=str(projection["sole_active_task_id"]),
        worktree_binding_sha256=worktree_binding_sha256,
    )
    visible_current = bool(observation["artifact_visibility_proven"])
    changes_visible_current = bool(changes_observation["artifact_visibility_proven"])
    changes_surface_loss_detected = exact_trigger == "CHANGES_SURFACE_LOSS" or bool(
        changes_observation["missing_or_stale"]
    )
    capability_available = exact_capability == "SUPPORTED"
    previous_window_exists = bool(previous_window.get("projection_sha256"))
    same_window = (
        previous_window.get("row_start") == projection["row_start"]
        and previous_window.get("row_end") == projection["row_end"]
    )
    ledger_changed = (
        previous_window_exists
        and previous_window.get("executable_projection_sha256")
        != projection["executable_projection_sha256"]
    )
    window_changed = previous_window_exists and not same_window
    current_window_ui_changed = (
        previous_window_exists
        and previous_window.get("window_ui_fingerprint_sha256")
        != projection["window_ui_fingerprint_sha256"]
    )
    current_window_task_ids = list(projection["window_task_ids"])
    plan_steer_affects_current_window = bool(
        set(exact_affected_task_ids).intersection(current_window_task_ids)
    )

    if not capability_available:
        status = "BLOCKED"
        action = "FAIL_CLOSED_HOST_CAPABILITY_UNAVAILABLE"
    elif changes_surface_loss_detected:
        status = "PASS"
        action = "RELOCK_HOST_PLAN_AND_REQUEST_CHANGES_SURFACE_RECOVERY"
    elif visible_current:
        status = "PASS"
        action = "NO_HOST_PLAN_ACTION_CURRENT_WINDOW_VISIBLE"
    elif exact_trigger in _PLAN_STEER_TRIGGERS and current_window_ui_changed:
        status = "PASS"
        action = "SYNC_HOST_PLAN_WINDOW_AFTER_PLAN_STEER"
    elif exact_trigger in _PLAN_STEER_TRIGGERS:
        status = "PASS"
        action = "NO_HOST_PLAN_ACTION_STEER_OUTSIDE_CURRENT_WINDOW"
    elif exact_trigger in _REACTIVATION_TRIGGERS:
        status = "PASS"
        action = (
            "ACTIVATE_HOST_PLAN_CURRENT_WINDOW"
            if not previous_window_exists
            else "REACTIVATE_EXISTING_HOST_PLAN_WINDOW"
        )
    elif window_changed:
        status = "PASS"
        action = "ADVANCE_HOST_PLAN_TO_NEXT_WINDOW"
    elif exact_trigger in _STATUS_TRANSITION_TRIGGERS:
        status = "PASS"
        action = (
            "ACTIVATE_HOST_PLAN_CURRENT_WINDOW"
            if not previous_window_exists
            else "UPDATE_HOST_PLAN_CURRENT_WINDOW_STATUSES"
        )
    elif not previous_window_exists:
        status = "PASS"
        action = "ACTIVATE_HOST_PLAN_CURRENT_WINDOW"
    else:
        status = "PASS"
        action = "NO_HOST_PLAN_ACTION_REUSE_CURRENT_WINDOW"
    update_plan_required = action in {
        "ACTIVATE_HOST_PLAN_CURRENT_WINDOW",
        "REACTIVATE_EXISTING_HOST_PLAN_WINDOW",
        "SYNC_HOST_PLAN_WINDOW_AFTER_PLAN_STEER",
        "UPDATE_HOST_PLAN_CURRENT_WINDOW_STATUSES",
        "ADVANCE_HOST_PLAN_TO_NEXT_WINDOW",
        "RELOCK_HOST_PLAN_AND_REQUEST_CHANGES_SURFACE_RECOVERY",
    }
    changes_surface_recovery_required = (
        action == "RELOCK_HOST_PLAN_AND_REQUEST_CHANGES_SURFACE_RECOVERY"
    )
    acceptance_required = exact_trigger == "STATE_TRAVEL_DESTINATION_ENTRY"
    core = {
        "schema": "evidence-lane.host-plan-window-activation-receipt.v2",
        "status": status,
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "host_task_id": host_task_id,
        "host_task_id_sha256": sha256_bytes(host_task_id.encode("utf-8")),
        "active_runtime_task_id": cast(dict[str, Any], session.get("task") or {}).get(
            "task_id"
        ),
        "active_plan_task_id": metadata.get("active_backlog_task_id"),
        "trigger": exact_trigger,
        "trigger_event_id": exact_event_id,
        "host_goal_active": host_goal_active,
        "projection_owner": "ACTIVE_CANONICAL_PLAN",
        "goal_presence_is_projector_precondition": False,
        "host_goal_presence_changes_projection": False,
        "previous_window_exists": previous_window_exists,
        "same_window_as_previous": same_window,
        "full_ledger_changed_since_previous_window_receipt": ledger_changed,
        "window_changed_since_previous_receipt": window_changed,
        "current_window_ui_changed_since_previous_receipt": (current_window_ui_changed),
        "affected_plan_task_ids": exact_affected_task_ids,
        "current_window_task_ids": current_window_task_ids,
        "plan_steer_affects_current_window": (plan_steer_affects_current_window),
        "host_surface_persistence": {
            "schema": "evidence-lane.host-surface-persistence.v2",
            "native_plan_surface": "CODEX_RIGHT_SIDE_PLAN",
            "native_changes_surface": "CODEX_RIGHT_SIDE_CHANGES",
            "plan_activation_action": "update_plan",
            "host_plan_mode": "FIXED_HEADER_PLUS_NINE_DELTA_WINDOW",
            "ordinary_turn_action": "REUSE_CURRENT_NATIVE_ARTIFACT",
            "plan_steer_action": (
                "SYNC_ONLY_WHEN_CURRENT_WINDOW_UI_FINGERPRINT_CHANGES"
            ),
            "evi_refresh_command_invoked_by_host_plan_sync": False,
            "task_transition_action": "UPDATE_STATUSES_WITHIN_CURRENT_WINDOW",
            "window_completion_action": (
                "SEAL_COMPLETED_WINDOW_AND_ACTIVATE_NEXT_WINDOW"
            ),
            "final_window_cardinality": (
                "ONE_FIXED_HEADER_PLUS_EXACT_REMAINING_DELTA_ROWS_UP_TO_NINE"
            ),
            "full_ledger_remains_native_authority": True,
            "pv_exit_reconstructs_new_entry": False,
            "changes_surface_binding": "EXACT_TASK_UUID_AND_WORKTREE",
            "changes_surface_observation_independent_from_plan": True,
            "changes_surface_loss_overrides_plan_visible_noop": True,
            "step_list_projection_owner": "ACTIVE_CANONICAL_PLAN_NOT_GOAL",
            "required_until": (
                "HUMAN_MARKS_GOAL_COMPLETE_OR_EXPLICIT_TASK_STATE_TRAVEL_HANDOFF_PASSES"
            ),
            "goal_completion_authority": "HUMAN_ONLY",
            "hil_may_complete_goal": False,
            "drop_is_continuity_failure": True,
            "rehydrate_before_source_or_lifecycle_work": True,
            "canonical_rehydration_source": ("PLAN_LANE_BACKLOG_NOT_THREAD_HISTORY"),
            "projection_only_law": "NATIVE_HOST_PLAN_PROJECTION_ONLY_LAW",
            "manual_summary_projection_allowed": False,
            "native_contract_forwarded_unchanged": True,
            "full_thread_history_hydration_allowed": False,
            "collaboration_overlay_hydration_allowed_during_recovery": False,
            "recovery_concurrency": "ONE_ACTIVE_TASK_ZERO_SUBAGENTS",
            "renderer_reset_effect": (
                "FAIL_CLOSED_THEN_REPROJECT_EXACTLY_ONCE_PER_EVENT"
            ),
            "host_owned_surface_survival_guaranteed_by_plugin": False,
            "missing_host_capability_behavior": "FAIL_CLOSED",
        },
        "host_capability": exact_capability,
        "host_capability_failure_behavior": "FAIL_CLOSED",
        "plan_authority": "PLAN_LANE",
        "projection": projection,
        "observation": observation,
        "changes_observation": changes_observation,
        "changes_surface_binding": {
            "schema": "evidence-lane.host-changes-surface-binding.v1",
            "project_id": project_id,
            "host_task_id": host_task_id,
            "active_plan_task_id": projection["sole_active_task_id"],
            "worktree_binding_sha256": worktree_binding_sha256,
            "private_diff_content_returned": False,
        },
        "changes_surface_recovery_contract": {
            "required": changes_surface_recovery_required,
            "surface": "CODEX_RIGHT_SIDE_CHANGES",
            "relock_plan_atomically": changes_surface_recovery_required,
            "host_action_receipt_required": changes_surface_recovery_required,
            "plugin_claims_host_rendered_surface": False,
            "fail_closed_on_wrong_task_or_worktree": True,
        },
        "action": action,
        "host_plan_tool": "update_plan",
        "native_runtime_invoked_host_update_plan": False,
        "host_action_receipt_required": update_plan_required,
        "host_update_plan_required": update_plan_required,
        "host_changes_action_receipt_required": changes_surface_recovery_required,
        "host_changes_surface_status": (
            "CONFIRMED_VISIBLE"
            if changes_visible_current
            else (
                "MISSING_OR_STALE" if changes_surface_loss_detected else "UNCONFIRMED"
            )
        ),
        "host_artifact_visibility_status": (
            "CONFIRMED_VISIBLE" if visible_current else "UNCONFIRMED"
        ),
        "host_plan_acceptance_required": acceptance_required,
        "host_plan_acceptance_status": (
            "EXPLICITLY_ACCEPTED"
            if observation["explicit_host_plan_acceptance_proven"]
            else (
                "PENDING_EXPLICIT_HOST_ACCEPTANCE"
                if acceptance_required
                else "NOT_REQUIRED_FOR_EXISTING_TASK_PLAN"
            )
        ),
        "host_plan_acceptance_is_evidence_lane_hil": False,
        "candidate_created": False,
        "pending_hil_mutated": False,
        "pointer_moved": False,
        "source_authority_mutated": False,
        "git_mutated": False,
        "plan_lane_mutated": False,
        "sealed_at": utc_now(),
    }
    receipt = {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
    project_root = store.project_root(project_id)
    request_identity = {
        "project_id": project_id,
        "evidence_session_id": evidence_session_id,
        "host_task_id": host_task_id,
        "trigger": exact_trigger,
        "trigger_event_id": exact_event_id,
        "projection_sha256": projection["projection_sha256"],
        "observation": observation,
        "changes_observation": changes_observation,
        "worktree_binding_sha256": worktree_binding_sha256,
        "host_capability": exact_capability,
        "host_goal_active": host_goal_active,
        "affected_plan_task_ids": exact_affected_task_ids,
    }
    request_sha256 = sha256_bytes(canonical_json_bytes(request_identity))
    receipt_path = (
        project_root
        / "receipts"
        / "host-plan-rehydration"
        / sha256_bytes(host_task_id.encode("utf-8"))[:24].lower()
        / f"{request_sha256.lower()}.json"
    )
    if receipt_path.is_file():
        existing = cast(
            dict[str, Any], json.loads(receipt_path.read_text(encoding="utf-8"))
        )
        claimed = str(existing.get("receipt_sha256") or "")
        require(
            claimed
            == sha256_bytes(
                canonical_json_bytes(
                    {
                        key: value
                        for key, value in existing.items()
                        if key != "receipt_sha256"
                    }
                )
            )
            and existing.get("project_id") == project_id
            and cast(dict[str, Any], existing.get("projection") or {}).get(
                "projection_sha256"
            )
            == projection["projection_sha256"],
            "HOST_PLAN_REHYDRATION_RECEIPT_CONFLICT",
            "The existing host Plan rehydration receipt failed identity or SHA-256 verification.",
            status="MISMATCH",
        )
        return {
            "state": "REHYDRATION_RECEIPT_IDEMPOTENT_REUSE",
            "request_sha256": request_sha256,
            "receipt_path": str(receipt_path),
            "receipt": existing,
        }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(receipt_path, receipt)
    pointer_after = store.pointer(project_id).as_dict()
    require(
        pointer_before == pointer_after,
        "HOST_PLAN_REHYDRATION_POINTER_CHANGED",
        "Preparing a host Plan projection must not move the accepted pointer.",
        status="MISMATCH",
    )
    return {
        "state": "REHYDRATION_RECEIPT_SEALED",
        "request_sha256": request_sha256,
        "receipt_path": str(receipt_path),
        "receipt": receipt,
    }


def validate_host_plan_rehydration_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    """Validate the self-hash and non-promoting boundary of one receipt."""

    exact = dict(receipt)
    claimed = str(exact.pop("receipt_sha256", ""))
    require(
        receipt.get("schema") == "evidence-lane.host-plan-window-activation-receipt.v2"
        and claimed == sha256_bytes(canonical_json_bytes(exact))
        and receipt.get("candidate_created") is False
        and receipt.get("pending_hil_mutated") is False
        and receipt.get("pointer_moved") is False
        and receipt.get("plan_lane_mutated") is False,
        "HOST_PLAN_REHYDRATION_RECEIPT_INVALID",
        "The host Plan rehydration receipt failed integrity or boundary validation.",
        status="MISMATCH",
    )
    return receipt
