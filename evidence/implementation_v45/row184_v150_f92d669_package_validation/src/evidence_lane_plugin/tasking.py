"""Deterministic task contracts for one agent and one bounded execution line."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import PurePosixPath

from .errors import EvidenceLaneError, require
from .ids import prefixed_id
from .models import TaskClass, TaskContract

_TOOL_ALLOWLIST = {
    "pv_search",
    "pv_fetch",
    "pv_query",
    "repository_read",
    "repository_write",
    "terminal",
    "test",
    "build",
    "git_diff",
    "patch",
}

_WRITE_CLASSES = {
    TaskClass.MODIFY_CODE,
    TaskClass.FIX_BUG,
    TaskClass.ADD_BOUNDED_FEATURE,
    TaskClass.PREPARE_PATCH,
}


def _safe_relative_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    require(
        bool(normalized)
        and not path.is_absolute()
        and ".." not in path.parts
        and not normalized.startswith(("/", "~")),
        "TASK_PATH_INVALID",
        "Task scope paths must be repository-relative and may not traverse upward.",
        status="BLOCKED",
        path=value,
    )
    return path.as_posix()


def classify_task(
    *,
    task_class: str | TaskClass,
    requested_outcome: str,
    permitted_paths: Iterable[str],
    permitted_tools: Iterable[str],
    acceptance_checks: Iterable[str],
    stop_condition: str,
    task_id: str | None = None,
) -> TaskContract:
    try:
        classification = (
            task_class if isinstance(task_class, TaskClass) else TaskClass(task_class)
        )
    except ValueError:
        raise EvidenceLaneError(
            "TASK_CLASS_UNSUPPORTED",
            "The task class is not part of the first Git/code HIL.",
            status="BLOCKED",
            details={"supported": [item.value for item in TaskClass]},
        )
    outcome = requested_outcome.strip()
    stop = stop_condition.strip()
    require(
        bool(outcome),
        "TASK_OUTCOME_REQUIRED",
        "A bounded requested outcome is required.",
        status="BLOCKED",
    )
    require(
        bool(stop),
        "TASK_STOP_CONDITION_REQUIRED",
        "An explicit task stop condition is required.",
        status="BLOCKED",
    )
    paths = sorted({_safe_relative_path(path) for path in permitted_paths})
    tools = sorted({tool.strip() for tool in permitted_tools if tool.strip()})
    unsupported = sorted(set(tools) - _TOOL_ALLOWLIST)
    require(
        not unsupported,
        "TASK_TOOL_NOT_AUTHORIZED",
        "One or more task tools are outside the first-HIL allowlist.",
        status="BLOCKED",
        unsupported=unsupported,
    )
    checks = [check.strip() for check in acceptance_checks if check.strip()]
    if classification in _WRITE_CLASSES:
        require(
            bool(paths),
            "WRITE_SCOPE_REQUIRED",
            "A code-writing task requires at least one permitted path.",
            status="BLOCKED",
        )
        require(
            bool(checks),
            "ACCEPTANCE_CHECK_REQUIRED",
            "A code-writing task requires at least one acceptance check.",
            status="BLOCKED",
        )
        require(
            "repository_write" in tools,
            "WRITE_TOOL_REQUIRED",
            "A code-writing task must explicitly authorize sandbox repository writes.",
            status="BLOCKED",
        )
        write_boundary = "AUTHORIZED_SANDBOX_PATHS_ONLY"
    else:
        require(
            "repository_write" not in tools,
            "READ_TASK_WRITE_FORBIDDEN",
            "A read-only task class may not authorize repository writes.",
            status="BLOCKED",
        )
        write_boundary = "READ_ONLY"
    return TaskContract(
        task_id=task_id or prefixed_id("task"),
        task_class=classification,
        requested_outcome=outcome,
        permitted_paths=paths,
        permitted_tools=tools,
        acceptance_checks=checks,
        write_boundary=write_boundary,
        stop_condition=stop,
        hil_required=True,
        status="CLASSIFIED",
    )
