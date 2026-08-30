"""Bounded evaluation routes for action, workflow, and toolchain evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

EVALUATION_TOOLCHAIN_SCHEMA = "evidence-lane.evaluation-toolchain.v1"
_SHA256 = re.compile(r"[A-F0-9]{64}")
_TOOLS: dict[str, dict[str, Any]] = {
    "LangSmith": {
        "modes": ["dataset", "evaluator", "experiment", "trace"],
        "credential_names": ["LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT"],
    },
    "TruLens": {
        "modes": ["feedback_function", "record", "evaluation"],
        "credential_names": ["TRULENS_ENDPOINT", "TRULENS_API_KEY"],
    },
    "DeepEval": {
        "modes": ["metric", "dataset", "test_run"],
        "credential_names": ["DEEPEVAL_API_KEY"],
    },
    "Promptfoo": {
        "modes": ["prompt_test", "assertion", "red_team"],
        "credential_names": ["PROMPTFOO_CONFIG"],
    },
}


def _hash(value: str, field: str) -> str:
    exact = value.strip().upper()
    if _SHA256.fullmatch(exact) is None:
        raise ValueError(f"{field} must be an exact SHA-256.")
    return exact


def build_evaluation_plan(
    *,
    tool_id: str,
    mode: str,
    project_id: str,
    task_id: str,
    action_name: str,
    action_schema_sha256: str,
    route_sha256: str,
    dataset_sha256: str,
    redaction_receipt_sha256: str,
    evaluator_ids: Iterable[str],
    granted_tools: Iterable[str],
) -> dict[str, Any]:
    if tool_id not in _TOOLS:
        raise ValueError(f"Unknown evaluation tool: {tool_id}")
    exact_mode = mode.strip().lower()
    if exact_mode not in _TOOLS[tool_id]["modes"]:
        raise ValueError(f"Unsupported {tool_id} evaluation mode: {mode}")
    if not all(value.strip() for value in (project_id, task_id, action_name)):
        raise ValueError("Evaluation requires project, task, and action identity.")
    evaluators = list(
        dict.fromkeys(str(value).strip() for value in evaluator_ids if str(value).strip())
    )
    if not evaluators:
        raise ValueError("Evaluation requires at least one explicit evaluator.")
    status = "PASS" if tool_id in set(granted_tools) else "BLOCKED_PROJECT_GRANT_REQUIRED"
    body = {
        "schema": EVALUATION_TOOLCHAIN_SCHEMA,
        "status": status,
        "tool_id": tool_id,
        "mode": exact_mode,
        "project_id": project_id.strip(),
        "task_id": task_id.strip(),
        "action_name": action_name.strip(),
        "action_schema_sha256": _hash(action_schema_sha256, "action_schema_sha256"),
        "route_sha256": _hash(route_sha256, "route_sha256"),
        "dataset_sha256": _hash(dataset_sha256, "dataset_sha256"),
        "redaction_receipt_sha256": _hash(
            redaction_receipt_sha256, "redaction_receipt_sha256"
        ),
        "evaluator_ids": evaluators,
        "credential_names": list(_TOOLS[tool_id]["credential_names"]),
        "credential_values_read": False,
        "network_or_command_executed": False,
        "production_authority_write_allowed": False,
        "project_truth_promotion_allowed": False,
        "hil_inferred": False,
    }
    return {**body, "plan_sha256": sha256_bytes(canonical_json_bytes(body))}


def seal_evaluation_result(
    *,
    plan_sha256: str,
    score_by_evaluator: Mapping[str, float],
    passed: bool,
) -> dict[str, Any]:
    scores = {str(key): float(value) for key, value in score_by_evaluator.items()}
    if not scores or any(value < 0.0 or value > 1.0 for value in scores.values()):
        raise ValueError("Evaluation scores must be a non-empty 0..1 mapping.")
    body = {
        "schema": "evidence-lane.evaluation-result.v1",
        "status": "PASS" if passed else "FAIL",
        "evaluation_plan_sha256": _hash(plan_sha256, "plan_sha256"),
        "scores": scores,
        "passed": bool(passed),
        "result_is_evidence_not_project_authority": True,
        "production_state_mutated": False,
        "hil_inferred": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def evaluation_tool_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.evaluation-tool-catalog.v1",
        "status": "PASS",
        "tools": [{"tool_id": tool_id, **contract} for tool_id, contract in _TOOLS.items()],
        "tool_count": len(_TOOLS),
        "counts_are_derived_not_fixed": True,
        "production_authority_write_allowed": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "EVALUATION_TOOLCHAIN_SCHEMA",
    "build_evaluation_plan",
    "evaluation_tool_catalog",
    "seal_evaluation_result",
]
