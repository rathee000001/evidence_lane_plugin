"""First registration of one explicit project source into a fresh flat PV root.

The selected source lane is initialized before the retained authority lanes.
Source Intake records exact read-only source identity; it does not pretend that
the later Plan-governed content index has already run.
"""

from __future__ import annotations

from pathlib import Path

from .errors import LaneError
from .lanes import AUTHORITY_LANE_IDS, get_lane
from .migrations import apply_migrations
from .storage import json_text, now


def classify_initial_source(source_root: Path) -> dict:
    """Classify the selected project root without creating project state."""

    from .source_intake import classify_source_intake

    result = classify_source_intake(
        [str(source_root)],
        code_mode="local_code",
        git_mode="AUTO",
        authority_mode="CLASSIFICATION_ONLY",
        registered_repository_path=source_root,
    )
    lanes = tuple(result["ordered_canonical_lanes"])
    if not lanes or len(set(lanes)) != len(lanes):
        raise LaneError(
            "PROJECT_INITIAL_SOURCE_CLASSIFICATION_INVALID",
            "The selected project source did not resolve to a distinct retained lane sequence.",
        )
    if any(get_lane(lane_id).kind != "sector" for lane_id in lanes):
        raise LaneError(
            "PROJECT_INITIAL_SOURCE_CLASSIFICATION_INVALID",
            "Initial project sources must resolve to retained sector lanes.",
        )
    return {"classification": result, "lane_ids": lanes}


def initial_lane_order(classification: dict) -> tuple[str, ...]:
    """Put the selected source lane before every retained authority."""

    selected = tuple(classification["lane_ids"])
    return (*selected, *(lane_id for lane_id in AUTHORITY_LANE_IDS if lane_id not in selected))


def bootstrap_project(engine, project, classification: dict, *, actor_id: str) -> dict:
    """Initialize the selected source lane, then each authority, under one writer."""

    selected_lanes = tuple(classification["lane_ids"])
    source_root = project.source_root
    code_lanes = tuple(lane_id for lane_id in selected_lanes if lane_id in {"local_code", "github_code"})
    with engine.project_work.mutation(project) as lease:
        for lane_id in selected_lanes:
            if lane_id in {"local_code", "github_code"}:
                from .code_profile_schema import code_migrations
                apply_migrations(project.lane(lane_id), code_migrations(lane_id), writer=lease)

        governed_intake = None
        initialized_authorities = []
        for lane_id in AUTHORITY_LANE_IDS:
            from .authority_support import authority_migrations
            migrations = authority_migrations(lane_id)
            if migrations:
                apply_migrations(project.lane(lane_id), migrations, writer=lease)
            if lane_id == "sources":
                from .source_intake import classify_source_intake
                governed_intake = classify_source_intake(
                    [str(source_root)],
                    code_mode="local_code",
                    git_mode="AUTO",
                    authority_mode="GOVERNED_CONTENT_REGISTRY",
                    authority_registry_path=project,
                    registered_repository_path=source_root,
                    writer=lease,
                )
                if tuple(governed_intake["ordered_canonical_lanes"]) != selected_lanes:
                    raise LaneError(
                        "PROJECT_INITIAL_SOURCE_CLASSIFICATION_CHANGED",
                        "The selected source lane changed while the new project was being registered.",
                    )
            initialized_authorities.append(lane_id)

        if governed_intake is None:
            raise LaneError(
                "PROJECT_INITIAL_SOURCE_INTAKE_MISSING",
                "The retained authority sequence did not register the selected source.",
            )
        authority = governed_intake["source_authority"]
        result = {
            "schema": "evidence-lane.project-initial-source-intake.v4",
            "project_id": project.project_id,
            "source_root": str(source_root),
            "selected_lane_ids": list(selected_lanes),
            "code_lane_ids": list(code_lanes),
            "source_batch_id": authority.get("batch_id"),
            "source_batch_sha256": authority.get("batch_sha256"),
            "source_count": governed_intake["source_count"],
            "authority_lane_order": initialized_authorities,
            "code_lane_state": (
                "SCHEMA_READY_SOURCE_REGISTERED_INDEX_PENDING_PLAN"
                if code_lanes
                else "NOT_A_CODE_PROJECT"
            ),
            "authority_lane_state": "SCHEMA_READY_NO_FABRICATED_RECORDS",
            "source_bytes_mutated": False,
            "source_payloads_copied": False,
            "plan_governed_content_index_complete": False,
            "registered_by": actor_id,
            "registered_at": now(),
        }
        if len(json_text(result).encode("utf-8")) > 65_536:
            raise LaneError(
                "PROJECT_INITIAL_SOURCE_RECEIPT_BUDGET",
                "The initial source registration receipt exceeded its bounded size.",
            )
        with lease.transaction("receipts") as connection:
            project.append_receipt("project_initial_source_intake", result, connection=connection)
        return result


__all__ = ["bootstrap_project", "classify_initial_source", "initial_lane_order"]
