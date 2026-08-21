"""Deterministic public projection of the governed executable Plan Lane."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .git_adapter import run_git
from .hashing import canonical_json_bytes, sha256_bytes
from .store import ProjectStore

WEBSITE_PLAN_PROJECTION_SCHEMA = "evidence-lane.website-plan-projection.v1"
WEBSITE_PLAN_PROJECTION_PATH = (
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/"
    "website-plan-projection.json"
)
WEBSITE_DELTA_LEDGER_PATH = (
    "plugins/evidence-lane-plugin/remote_adapter/app/_data/delta-ledger.ts"
)
WEBSITE_PUBLIC_METADATA_PATH = (
    "plugins/evidence-lane-plugin/remote_adapter/public/.well-known/"
    "evidence-lane-plugin.json"
)
PERSISTENT_UNTIL = "NEXT_SIX_WAY_HIL_PRESENTED"

_PUBLIC_STATUS_BY_GOAL_STATUS = {
    "completed": "COMPLETED",
    "in_progress": "IN_PROGRESS",
    "pending": "PENDING",
}


def extract_backlog_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Accept a native backlog, lifecycle result, or persisted tool envelope."""

    value: Any = payload
    if isinstance(value.get("structuredContent"), Mapping):
        value = value["structuredContent"]
    data = value.get("data") if isinstance(value, Mapping) else None
    if isinstance(data, Mapping) and isinstance(data.get("backlog"), Mapping):
        value = data["backlog"]
    elif isinstance(value, Mapping) and isinstance(value.get("backlog"), Mapping):
        value = value["backlog"]
    require(
        isinstance(value, Mapping)
        and isinstance(value.get("goal_projection"), Mapping),
        "WEBSITE_PLAN_BACKLOG_PAYLOAD_INVALID",
        "The website Plan generator requires a native pv_task_backlog payload.",
        status="MISMATCH",
    )
    return dict(value)


def _linked_delta_ids(row: Mapping[str, Any]) -> list[str]:
    linked: list[str] = []
    for delta in row.get("steer_deltas") or []:
        require(
            isinstance(delta, Mapping)
            and isinstance(delta.get("delta_id"), str)
            and bool(delta["delta_id"].strip()),
            "WEBSITE_PLAN_LINKED_DELTA_INVALID",
            "Every public linked Delta reference must have one exact Delta ID.",
            status="MISMATCH",
            row=row.get("number"),
        )
        delta_id = str(delta["delta_id"])
        if delta_id not in linked:
            linked.append(delta_id)
    return linked


def build_website_plan_projection(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build the sealed public-safe view from the canonical executable projection."""

    backlog = extract_backlog_status(payload)
    goal = backlog["goal_projection"]
    require(
        backlog.get("status") == "PASS"
        and goal.get("canonical_authority") == "PLAN_LANE",
        "WEBSITE_PLAN_CANONICAL_AUTHORITY_MISMATCH",
        "The website Plan projection must come from a passing PLAN_LANE readback.",
        status="MISMATCH",
    )
    native_rows = goal.get("rows")
    require(
        isinstance(native_rows, list) and bool(native_rows),
        "WEBSITE_PLAN_ROWS_MISSING",
        "The executable Plan projection has no rows.",
        status="MISMATCH",
    )
    expected_numbers = list(
        range(int(goal["row_start"]), int(goal["row_end"]) + 1)
    )
    observed_numbers = [int(row["number"]) for row in native_rows]
    require(
        observed_numbers == expected_numbers
        and len(native_rows) == int(goal["task_count"]),
        "WEBSITE_PLAN_ROWS_NOT_CONTIGUOUS",
        "The public Plan rows must exactly match the contiguous executable projection.",
        status="MISMATCH",
        expected_numbers=expected_numbers,
        observed_numbers=observed_numbers,
    )

    rows: list[dict[str, Any]] = []
    for task_position, row in enumerate(native_rows, start=1):
        goal_status = str(row.get("status"))
        require(
            goal_status in _PUBLIC_STATUS_BY_GOAL_STATUS,
            "WEBSITE_PLAN_STATUS_INVALID",
            "The executable Plan contains an unsupported public status.",
            status="MISMATCH",
            row=row.get("number"),
            goal_status=goal_status,
        )
        public_row: dict[str, Any] = {
            "row": int(row["number"]),
            "task_position": task_position,
            "task_id": str(row["task_id"]),
            "description": str(row["step"]),
            "status": _PUBLIC_STATUS_BY_GOAL_STATUS[goal_status],
            "lifecycle_status": str(row["lifecycle_status"]),
            "plan_sequence": int(row["plan_sequence"]),
            "linked_delta_ids": _linked_delta_ids(row),
        }
        if row.get("panel_role"):
            public_row["panel_role"] = str(row["panel_role"])
        rows.append(public_row)

    status_counts = Counter(row["status"] for row in rows)
    active_rows = [row for row in rows if row["status"] == "IN_PROGRESS"]
    final_hil_rows = [
        row for row in rows if row.get("panel_role") == "PHYSICALLY_FINAL_HIL"
    ]
    require(
        len(active_rows) == 1,
        "WEBSITE_PLAN_ACTIVE_ROW_COUNT_INVALID",
        "The public executable Plan must contain exactly one active row.",
        status="MISMATCH",
        active_rows=[row["row"] for row in active_rows],
    )
    require(
        len(final_hil_rows) == 1
        and final_hil_rows[0] == rows[-1]
        and final_hil_rows[0]["status"] == "PENDING",
        "WEBSITE_PLAN_FINAL_HIL_INVALID",
        "The PHYSICALLY_FINAL_HIL row must be the final pending executable row.",
        status="MISMATCH",
        final_hil_rows=[row["row"] for row in final_hil_rows],
    )
    require(
        goal.get("persistent_until") == PERSISTENT_UNTIL,
        "WEBSITE_PLAN_PERSISTENCE_LAW_MISMATCH",
        "The public Plan must retain the native NEXT_SIX_WAY_HIL_PRESENTED law.",
        status="MISMATCH",
    )

    runtime = backlog.get("plan_runtime_projection") or {}
    body = {
        "schema": WEBSITE_PLAN_PROJECTION_SCHEMA,
        "canonical_authority": "PLAN_LANE",
        "project_id": str(goal["project_id"]),
        "task_count": len(rows),
        "canonical_task_count": int(goal["canonical_task_count"]),
        "history_task_count": int(goal["history_task_count"]),
        "row_offset": int(goal["row_offset"]),
        "row_start": int(goal["row_start"]),
        "row_end": int(goal["row_end"]),
        "status_counts": {
            "completed": status_counts["COMPLETED"],
            "in_progress": status_counts["IN_PROGRESS"],
            "pending": status_counts["PENDING"],
        },
        "active_row": int(active_rows[0]["row"]),
        "active_task_id": str(active_rows[0]["task_id"]),
        "active_task_position": int(active_rows[0]["task_position"]),
        "physically_final_hil_row": int(final_hil_rows[0]["row"]),
        "physically_final_hil_task_id": str(final_hil_rows[0]["task_id"]),
        "physically_final_hil_task_position": int(
            final_hil_rows[0]["task_position"]
        ),
        "persistent_until": PERSISTENT_UNTIL,
        "canonical_plan_sha256": str(goal["canonical_plan_sha256"]),
        "history_projection_sha256": str(goal["history_projection_sha256"]),
        "executable_projection_sha256": str(goal["projection_sha256"]),
        "lifecycle_event_count": int(backlog.get("event_count") or 0),
        "lifecycle_event_head_sha256": str(
            backlog.get("event_head_sha256") or ""
        ),
        "planning_event_count": int(backlog.get("planning_mode_event_count") or 0),
        "planning_event_head_sha256": str(
            backlog.get("planning_mode_event_head_sha256") or ""
        ),
        "runtime_projection_content_sha256": str(
            runtime.get("projection_content_sha256") or ""
        ),
        "rows": rows,
    }
    return {
        **body,
        "snapshot_sha256": sha256_bytes(canonical_json_bytes(body)),
    }


def validate_website_plan_projection(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed when a committed snapshot is internally stale or malformed."""

    require(
        snapshot.get("schema") == WEBSITE_PLAN_PROJECTION_SCHEMA,
        "WEBSITE_PLAN_SNAPSHOT_SCHEMA_INVALID",
        "The committed website Plan snapshot schema is unsupported.",
        status="MISMATCH",
    )
    body = dict(snapshot)
    observed_snapshot_sha256 = str(body.pop("snapshot_sha256", ""))
    expected_snapshot_sha256 = sha256_bytes(canonical_json_bytes(body))
    require(
        observed_snapshot_sha256 == expected_snapshot_sha256,
        "WEBSITE_PLAN_SNAPSHOT_HASH_MISMATCH",
        "The committed website Plan snapshot seal does not match its content.",
        status="MISMATCH",
        expected_snapshot_sha256=expected_snapshot_sha256,
        observed_snapshot_sha256=observed_snapshot_sha256,
    )
    rebuilt = build_website_plan_projection(
        {
            "status": "PASS",
            "event_count": snapshot.get("lifecycle_event_count"),
            "event_head_sha256": snapshot.get("lifecycle_event_head_sha256"),
            "planning_mode_event_count": snapshot.get("planning_event_count"),
            "planning_mode_event_head_sha256": snapshot.get(
                "planning_event_head_sha256"
            ),
            "plan_runtime_projection": {
                "projection_content_sha256": snapshot.get(
                    "runtime_projection_content_sha256"
                )
            },
            "goal_projection": {
                "canonical_authority": snapshot.get("canonical_authority"),
                "project_id": snapshot.get("project_id"),
                "task_count": snapshot.get("task_count"),
                "canonical_task_count": snapshot.get("canonical_task_count"),
                "history_task_count": snapshot.get("history_task_count"),
                "row_offset": snapshot.get("row_offset"),
                "row_start": snapshot.get("row_start"),
                "row_end": snapshot.get("row_end"),
                "rows": [
                    {
                        "number": row["row"],
                        "task_id": row["task_id"],
                        "step": row["description"],
                        "status": {
                            "COMPLETED": "completed",
                            "IN_PROGRESS": "in_progress",
                            "PENDING": "pending",
                        }[row["status"]],
                        "lifecycle_status": row["lifecycle_status"],
                        "plan_sequence": row["plan_sequence"],
                        "steer_deltas": [
                            {"delta_id": delta_id}
                            for delta_id in row.get("linked_delta_ids") or []
                        ],
                        **(
                            {"panel_role": row["panel_role"]}
                            if row.get("panel_role")
                            else {}
                        ),
                    }
                    for row in snapshot.get("rows") or []
                ],
                "persistent_until": snapshot.get("persistent_until"),
                "canonical_plan_sha256": snapshot.get("canonical_plan_sha256"),
                "history_projection_sha256": snapshot.get(
                    "history_projection_sha256"
                ),
                "projection_sha256": snapshot.get(
                    "executable_projection_sha256"
                ),
            },
        }
    )
    require(
        rebuilt == dict(snapshot),
        "WEBSITE_PLAN_SNAPSHOT_NORMALIZATION_MISMATCH",
        "The committed website Plan snapshot is not the deterministic projection.",
        status="MISMATCH",
    )
    return dict(snapshot)


def expected_public_plan_lane(
    snapshot: Mapping[str, Any],
    current: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the exact public metadata block bound to the snapshot."""

    prior = current or {}
    return {
        "current_public_projection": prior.get(
            "current_public_projection",
            "https://evidencelane.org/#delta-ledger",
        ),
        "sealed_historical_delta_rows": 80,
        **(
            {
                "candidate_comparison_deployment": prior[
                    "candidate_comparison_deployment"
                ]
            }
            if prior.get("candidate_comparison_deployment")
            else {}
        ),
        **(
            {"candidate_comparison_commit": prior["candidate_comparison_commit"]}
            if prior.get("candidate_comparison_commit")
            else {}
        ),
        "production_role": prior.get(
            "production_role",
            "HISTORICAL_BASELINE_ONLY_UNTIL_ACCEPTED_PUBLICATION",
        ),
        "canonical_authority": snapshot["canonical_authority"],
        "live_projection_rows": snapshot["task_count"],
        "row_start": snapshot["row_start"],
        "row_end": snapshot["row_end"],
        "completed_rows": snapshot["status_counts"]["completed"],
        "in_progress_rows": snapshot["status_counts"]["in_progress"],
        "pending_rows": snapshot["status_counts"]["pending"],
        "active_public_row": snapshot["active_row"],
        "active_task_id": snapshot["active_task_id"],
        "active_public_task_position": snapshot["active_task_position"],
        "physically_final_hil_public_row": snapshot[
            "physically_final_hil_row"
        ],
        "physically_final_hil_task_id": snapshot[
            "physically_final_hil_task_id"
        ],
        "physically_final_hil_task_position": snapshot[
            "physically_final_hil_task_position"
        ],
        "canonical_plan_sha256": snapshot["canonical_plan_sha256"],
        "history_projection_sha256": snapshot["history_projection_sha256"],
        "executable_projection_sha256": snapshot[
            "executable_projection_sha256"
        ],
        "website_plan_snapshot_sha256": snapshot["snapshot_sha256"],
        "native_plan_event_count": snapshot["lifecycle_event_count"],
        "native_plan_event_head_sha256": snapshot[
            "lifecycle_event_head_sha256"
        ],
        "planning_event_count": snapshot["planning_event_count"],
        "planning_event_head_sha256": snapshot[
            "planning_event_head_sha256"
        ],
        "runtime_projection_content_sha256": snapshot[
            "runtime_projection_content_sha256"
        ],
        "persistent_until": snapshot["persistent_until"],
    }


def _git_show_json(
    repository: str | Path,
    *,
    commit: str,
    path: str,
) -> dict[str, Any] | None:
    result = run_git(repository, ["show", f"{commit}:{path}"], check=False)
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise EvidenceLaneError(
            "REMOTE_WEBSITE_PLAN_SNAPSHOT_JSON_INVALID",
            "The exact commit contains an invalid website Plan snapshot.",
            status="MISMATCH",
            details={"commit": commit, "path": path},
        ) from exc
    require(
        isinstance(value, dict),
        "REMOTE_WEBSITE_PLAN_SNAPSHOT_JSON_INVALID",
        "The exact commit website Plan snapshot must be a JSON object.",
        status="MISMATCH",
    )
    return value


def require_website_plan_projection_for_push(
    store: ProjectStore,
    *,
    project_id: str,
    repository: str | Path,
    commit: str,
) -> dict[str, Any]:
    """Bind an Evidence Lane feature push to the current native Plan snapshot."""

    marker = run_git(
        repository,
        ["cat-file", "-e", f"{commit}:{WEBSITE_DELTA_LEDGER_PATH}"],
        check=False,
    )
    if marker.returncode != 0:
        return {
            "schema": "evidence-lane.remote-website-plan-gate.v1",
            "status": "NOT_APPLICABLE",
            "reason": "REPOSITORY_HAS_NO_EVIDENCE_LANE_WEBSITE_LEDGER",
        }
    committed = _git_show_json(
        repository,
        commit=commit,
        path=WEBSITE_PLAN_PROJECTION_PATH,
    )
    require(
        committed is not None,
        "REMOTE_WEBSITE_PLAN_SNAPSHOT_MISSING",
        "The exact commit has a Delta ledger but no sealed Plan snapshot.",
        status="BLOCKED",
        commit=commit,
        required_path=WEBSITE_PLAN_PROJECTION_PATH,
    )
    assert committed is not None
    validated = validate_website_plan_projection(committed)
    live = build_website_plan_projection(store.backlog_status(project_id))
    require(
        validated == live,
        "REMOTE_WEBSITE_PLAN_PROJECTION_STALE",
        "The exact commit website Delta ledger does not match the current PLAN_LANE.",
        status="STALE",
        commit=commit,
        committed_snapshot_sha256=validated["snapshot_sha256"],
        live_snapshot_sha256=live["snapshot_sha256"],
        committed_executable_projection_sha256=validated[
            "executable_projection_sha256"
        ],
        live_executable_projection_sha256=live[
            "executable_projection_sha256"
        ],
    )
    return {
        "schema": "evidence-lane.remote-website-plan-gate.v1",
        "status": "PASS",
        "canonical_authority": "PLAN_LANE",
        "snapshot_path": WEBSITE_PLAN_PROJECTION_PATH,
        "snapshot_sha256": live["snapshot_sha256"],
        "executable_projection_sha256": live["executable_projection_sha256"],
        "task_count": live["task_count"],
        "row_start": live["row_start"],
        "row_end": live["row_end"],
        "active_row": live["active_row"],
        "physically_final_hil_row": live["physically_final_hil_row"],
        "persistent_until": live["persistent_until"],
    }
