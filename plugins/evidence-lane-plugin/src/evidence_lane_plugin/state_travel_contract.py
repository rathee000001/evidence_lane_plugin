"""Public-safe contracts for exact unfinished-work State Travel."""

from __future__ import annotations

from typing import Any

from .errors import require

_PROFILE_ALIASES = {
    "model": "model",
    "model_id": "model",
    "submodel": "submodel",
    "model_variant": "submodel",
    "reasoning_effort": "reasoning_effort",
    "reasoning_speed": "reasoning_speed",
    "speed": "reasoning_speed",
    "service_tier": "service_tier",
}

_CODEX_REQUIRED_PROFILE_FIELDS = (
    "model",
    "submodel",
    "reasoning_effort",
    "reasoning_speed",
)

_TASK_STATUSES = {
    "COMPLETED": "COMPLETED",
    "COMPLETE": "COMPLETED",
    "DONE": "COMPLETED",
    "ACCEPTED": "COMPLETED",
    "IN_PROGRESS": "IN_PROGRESS",
    "IN PROGRESS": "IN_PROGRESS",
    "ACTIVE": "IN_PROGRESS",
    "PENDING": "PENDING",
    "QUEUED": "PENDING",
}

_PLAN_METADATA_ID_CHARS = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _plan_metadata_id(value: Any, *, field: str, position: int) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact)
        and len(exact) <= 128
        and all(character in _PLAN_METADATA_ID_CHARS for character in exact),
        "STATE_TRAVEL_TASK_METADATA_INVALID",
        "State Travel Plan metadata must use bounded public-safe identifiers.",
        status="BLOCKED",
        position=position,
        field=field,
    )
    return exact


def execution_profile_from_context(context: dict[str, Any] | None) -> dict[str, str]:
    """Extract only non-secret host execution-selector fields."""

    raw_context = context or {}
    nested = raw_context.get("execution_profile")
    source = nested if isinstance(nested, dict) else raw_context
    result: dict[str, str] = {}
    for source_key, target_key in _PROFILE_ALIASES.items():
        if source_key not in source or source[source_key] is None:
            continue
        value = source[source_key]
        require(
            isinstance(value, str) and bool(value.strip()) and len(value.strip()) <= 128,
            "STATE_TRAVEL_EXECUTION_PROFILE_INVALID",
            "Execution-profile values must be non-empty public-safe strings.",
            status="BLOCKED",
            field=source_key,
        )
        normalized = value.strip()
        existing = result.get(target_key)
        require(
            existing in {None, normalized},
            "STATE_TRAVEL_EXECUTION_PROFILE_ALIAS_CONFLICT",
            "Two execution-profile aliases bind different values.",
            status="MISMATCH",
            field=target_key,
        )
        result[target_key] = normalized
    return result


def require_unfinished_execution_profile(
    profile: dict[str, str],
    *,
    host_kind: str,
) -> None:
    """Fail closed when a Codex handoff cannot prove the selector profile."""

    if not host_kind.startswith("CODEX"):
        return
    missing = [field for field in _CODEX_REQUIRED_PROFILE_FIELDS if not profile.get(field)]
    require(
        not missing,
        "STATE_TRAVEL_EXECUTION_PROFILE_INCOMPLETE",
        "Unfinished Codex State Travel requires model, submodel, reasoning effort, "
        "and reasoning speed so the destination can verify the same host profile.",
        status="BLOCKED",
        missing=missing,
        host_settings_mutation_supported=False,
    )


def execution_profile_mismatches(
    expected: dict[str, str],
    actual: dict[str, str],
) -> dict[str, dict[str, str | None]]:
    return {
        field: {"expected": value, "actual": actual.get(field)}
        for field, value in expected.items()
        if actual.get(field) != value
    }


def normalize_task_list(rows: Any) -> list[dict[str, Any]]:
    """Normalize one visible Plan Lane/host task-panel projection."""

    if rows is None:
        return []
    require(
        isinstance(rows, list) and len(rows) <= 200,
        "STATE_TRAVEL_TASK_LIST_INVALID",
        "The State Travel task list must contain no more than two hundred rows.",
        status="BLOCKED",
    )
    normalized: list[dict[str, Any]] = []
    first_number: int | None = None
    for index, row in enumerate(rows, start=1):
        require(
            isinstance(row, dict),
            "STATE_TRAVEL_TASK_ROW_INVALID",
            "Every State Travel task row must be a structured object.",
            status="BLOCKED",
            position=index,
        )
        number = row.get("number", row.get("sequence", index))
        if first_number is None and isinstance(number, int):
            first_number = number
        require(
            isinstance(number, int)
            and number >= 1
            and first_number is not None
            and number == first_number + index - 1,
            "STATE_TRAVEL_TASK_SEQUENCE_INVALID",
            "State Travel task rows must use one positive contiguous visible range.",
            status="BLOCKED",
            position=index,
            number=number,
        )
        text = row.get("step", row.get("requested_outcome", row.get("title")))
        require(
            isinstance(text, str) and bool(text.strip()) and len(text) <= 20000,
            "STATE_TRAVEL_TASK_TEXT_INVALID",
            "Every State Travel task row requires its exact visible step text.",
            status="BLOCKED",
            position=index,
        )
        raw_status = str(row.get("status", "PENDING")).strip().upper()
        status = _TASK_STATUSES.get(raw_status)
        require(
            status is not None,
            "STATE_TRAVEL_TASK_STATUS_INVALID",
            "Task-panel rows support COMPLETED, IN_PROGRESS, or PENDING.",
            status="BLOCKED",
            position=index,
            supplied_status=raw_status,
        )
        task_id = str(row.get("task_id") or f"STEP_{index:03d}").strip()
        require(
            bool(task_id) and len(task_id) <= 128,
            "STATE_TRAVEL_TASK_ID_INVALID",
            "Every State Travel row requires a bounded stable task ID.",
            status="BLOCKED",
            position=index,
        )
        normalized_row: dict[str, Any] = {
            "number": number,
            "task_id": task_id,
            "step": text,
            "status": status,
        }
        canonical_plan_sequence = row.get(
            "canonical_plan_sequence",
            row.get("plan_sequence"),
        )
        if canonical_plan_sequence is not None:
            require(
                isinstance(canonical_plan_sequence, int)
                and canonical_plan_sequence >= index,
                "STATE_TRAVEL_CANONICAL_PLAN_SEQUENCE_INVALID",
                "A current Goal row must retain a valid canonical Plan sequence when supplied.",
                status="BLOCKED",
                position=index,
                canonical_plan_sequence=canonical_plan_sequence,
            )
            normalized_row["canonical_plan_sequence"] = canonical_plan_sequence
        panel_role = str(row.get("panel_role") or "").strip().upper()
        if panel_role:
            require(
                panel_role
                in {"STANDARD", "HIL_GATE", "PHYSICALLY_FINAL_HIL"},
                "STATE_TRAVEL_TASK_PANEL_ROLE_INVALID",
                "A State Travel row contains an unsupported persistent-panel role.",
                status="BLOCKED",
                position=index,
                panel_role=panel_role,
            )
            normalized_row["panel_role"] = panel_role
        task_classification = str(
            row.get("task_classification") or row.get("task_class") or ""
        ).strip()
        if task_classification:
            normalized_row["task_classification"] = _plan_metadata_id(
                task_classification,
                field="task_classification",
                position=index,
            )
        plan_group = str(row.get("plan_group") or "").strip()
        if plan_group:
            normalized_row["plan_group"] = _plan_metadata_id(
                plan_group,
                field="plan_group",
                position=index,
            )
        commit_batch_id = str(row.get("commit_batch_id") or "").strip()
        if commit_batch_id:
            normalized_row["commit_batch_id"] = _plan_metadata_id(
                commit_batch_id,
                field="commit_batch_id",
                position=index,
            )
        if "dependencies" in row:
            dependencies = row.get("dependencies")
            require(
                isinstance(dependencies, list)
                and len(dependencies) <= 64
                and all(
                    isinstance(value, str)
                    and bool(value.strip())
                    and len(value.strip()) <= 128
                    and all(
                        character in _PLAN_METADATA_ID_CHARS
                        for character in value.strip()
                    )
                    for value in dependencies
                ),
                "STATE_TRAVEL_TASK_DEPENDENCIES_INVALID",
                "State Travel dependencies must be bounded public-safe task IDs.",
                status="BLOCKED",
                position=index,
            )
            normalized_row["dependencies"] = list(
                dict.fromkeys(value.strip() for value in dependencies)
            )
        for metadata_field in (
            "dependency_source",
            "git_commit_stage",
            "git_commit_stage_source",
        ):
            metadata_value = str(row.get(metadata_field) or "").strip()
            if metadata_value:
                normalized_row[metadata_field] = _plan_metadata_id(
                    metadata_value,
                    field=metadata_field,
                    position=index,
                )
        visible_label = row.get("visible_label")
        if visible_label is not None:
            require(
                isinstance(visible_label, str)
                and bool(visible_label.strip())
                and len(visible_label) <= 25000,
                "STATE_TRAVEL_TASK_VISIBLE_LABEL_INVALID",
                "A State Travel visible label must be bounded non-empty text.",
                status="BLOCKED",
                position=index,
            )
            normalized_row["visible_label"] = visible_label
        raw_steers = row.get("steer_deltas")
        if raw_steers:
            require(
                isinstance(raw_steers, list) and len(raw_steers) <= 500,
                "STATE_TRAVEL_TASK_STEERS_INVALID",
                "A task-panel row contains an invalid steer Delta list.",
                status="BLOCKED",
                position=index,
            )
            normalized_steers: list[dict[str, Any]] = []
            for steer_position, steer in enumerate(raw_steers, start=1):
                require(
                    isinstance(steer, dict),
                    "STATE_TRAVEL_TASK_STEER_INVALID",
                    "Every task-panel steer Delta must be a structured row.",
                    status="BLOCKED",
                    position=index,
                    steer_position=steer_position,
                )
                delta_id = str(steer.get("delta_id") or "").strip()
                delta_text = steer.get("text")
                boundary = str(
                    steer.get("boundary") or "BEFORE_NEXT_HIL"
                ).strip().upper()
                require(
                    bool(delta_id)
                    and len(delta_id) <= 96
                    and isinstance(delta_text, str)
                    and bool(delta_text.strip())
                    and len(delta_text) <= 50000
                    and bool(boundary)
                    and len(boundary) <= 128,
                    "STATE_TRAVEL_TASK_STEER_CONTRACT_INVALID",
                    "A task-panel steer Delta has incomplete immutable content.",
                    status="BLOCKED",
                    position=index,
                    steer_position=steer_position,
                )
                normalized_steer = {
                    "delta_id": delta_id,
                    "text": delta_text,
                    "boundary": boundary,
                    "boundary_defaulted": bool(
                        steer.get(
                            "boundary_defaulted",
                            boundary == "BEFORE_NEXT_HIL",
                        )
                    ),
                    "classification": str(
                        steer.get("classification") or "LINKED_EXISTING_STEP"
                    ),
                    "linked_task_id": str(
                        steer.get("linked_task_id") or task_id
                    ),
                    "recorded_by": str(steer.get("recorded_by") or "UNKNOWN"),
                }
                normalized_steers.append(normalized_steer)
            normalized_row["steer_deltas"] = normalized_steers
        normalized.append(normalized_row)
    in_progress = [row for row in normalized if row["status"] == "IN_PROGRESS"]
    require(
        len(in_progress) <= 1,
        "STATE_TRAVEL_MULTIPLE_ACTIVE_STEPS",
        "The persistent task panel may contain at most one in-progress row.",
        status="BLOCKED",
        active_rows=[row["number"] for row in in_progress],
    )
    require(
        not normalized or len(in_progress) == 1,
        "STATE_TRAVEL_ACTIVE_STEP_REQUIRED",
        "A non-empty persistent task panel requires exactly one in-progress row.",
        status="BLOCKED",
        task_count=len(normalized),
    )
    return normalized

def normalize_additive_deltas(rows: Any) -> list[dict[str, Any]]:
    """Preserve exact visible steer text with its canonical HIL boundary."""

    if rows is None:
        return []
    require(
        isinstance(rows, list) and len(rows) <= 500,
        "STATE_TRAVEL_DELTA_LIST_INVALID",
        "The additive Delta list must contain no more than five hundred rows.",
        status="BLOCKED",
    )
    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if isinstance(row, str):
            text = row
            linked_step = None
            boundary = "BEFORE_NEXT_HIL"
            delta_id = f"DELTA_{index:03d}"
        else:
            require(
                isinstance(row, dict),
                "STATE_TRAVEL_DELTA_ROW_INVALID",
                "Every additive Delta must be exact text or a structured row.",
                status="BLOCKED",
                position=index,
            )
            text = row.get("text", row.get("delta"))
            linked_step = row.get("linked_step")
            boundary = str(row.get("boundary") or "BEFORE_NEXT_HIL").strip().upper()
            delta_id = str(row.get("delta_id") or f"DELTA_{index:03d}").strip()
        require(
            isinstance(text, str) and bool(text.strip()) and len(text) <= 50000,
            "STATE_TRAVEL_DELTA_TEXT_INVALID",
            "Every additive Delta requires its exact visible text.",
            status="BLOCKED",
            position=index,
        )
        require(
            bool(boundary) and len(boundary) <= 128,
            "STATE_TRAVEL_DELTA_BOUNDARY_INVALID",
            "A Delta boundary must be a bounded visible label.",
            status="BLOCKED",
            position=index,
        )
        if linked_step is not None:
            require(
                isinstance(linked_step, int) and linked_step >= 1,
                "STATE_TRAVEL_DELTA_LINK_INVALID",
                "A linked Delta must name a positive task-panel row.",
                status="BLOCKED",
                position=index,
            )
        normalized.append(
            {
                "delta_id": delta_id,
                "text": text,
                "linked_step": linked_step,
                "boundary": boundary,
                "defaulted_to_pre_hil": boundary == "BEFORE_NEXT_HIL",
            }
        )
    delta_ids = [row["delta_id"] for row in normalized]
    require(
        len(delta_ids) == len(set(delta_ids)),
        "STATE_TRAVEL_DELTA_ID_DUPLICATE",
        "A State Travel handoff may seal each additive Delta ID only once.",
        status="BLOCKED",
        delta_ids=delta_ids,
    )
    return normalized


def additive_deltas_from_task_list(
    task_list: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project every persisted Plan Lane steer into the State Travel Delta seal."""

    projected: list[dict[str, Any]] = []
    for task in task_list:
        for steer in task.get("steer_deltas", []):
            projected.append(
                {
                    "delta_id": steer["delta_id"],
                    "text": steer["text"],
                    "linked_step": task["number"],
                    "boundary": steer["boundary"],
                }
            )
    return normalize_additive_deltas(projected)
