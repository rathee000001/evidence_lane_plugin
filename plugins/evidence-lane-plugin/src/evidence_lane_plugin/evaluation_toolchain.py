"""Bounded evaluation routes for action, workflow, and toolchain evidence."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .hashing import canonical_json_bytes, sha256_bytes
from .registry import Contract

EVALUATION_TOOLCHAIN_SCHEMA = "evidence-lane.evaluation-toolchain.v1"
EVALUATION_FEEDBACK_EXPORT = "evaluation_feedback_export"
_SHA256 = re.compile(r"[A-F0-9]{64}")
_TOOLS: dict[str, dict[str, Any]] = {
    "LangSmith": {
        "modes": ["dataset", "evaluator", "experiment", "trace"],
        "credential_names": ["LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT"],
    },
}


class EvaluationFeedbackRequest(Contract):
    """One pre-existing LangSmith run receives one bounded numeric feedback row."""

    tool_id: Literal["LangSmith"]
    lane_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    resource_id: str = Field(min_length=13, max_length=266)
    plugin_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{2,63}$")
    action_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    run_id: str = Field(min_length=36, max_length=36)
    session_id: str = Field(min_length=36, max_length=36)
    feedback_key: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    observed_at_unix_nano: int = Field(ge=1, le=4_102_444_800_000_000_000)
    action_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    route_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    timeout_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    max_request_bytes: int = Field(default=131_072, ge=4096, le=262_144)
    max_response_bytes: int = Field(default=1_048_576, ge=1024, le=4_194_304)

    @model_validator(mode="after")
    def exact_feedback_scope(self):
        from .external_evidence_delivery import resource_scope
        from .lanes import CANONICAL_LANE_IDS

        if (
            self.lane_id not in CANONICAL_LANE_IDS
            or resource_scope(self.tool_id, self.resource_id) is None
            or str(UUID(self.run_id)) != self.run_id
            or str(UUID(self.session_id)) != self.session_id
        ):
            raise ValueError("EVALUATION_FEEDBACK_SCOPE_INVALID")
        return self


def register_evaluation_actions(engine: Any) -> None:
    from .extension_routes import ExtensionBinding
    from .external_evidence_delivery import (
        EXPORT_BACKENDS,
        ExternalEvidenceResult,
        execute,
        readiness,
        verify_external_evidence,
    )
    from .registry import ActionSpec
    from .tool_routes import ToolRoute

    backend = EXPORT_BACKENDS["LangSmith"]

    def handler(context: Any, request: EvaluationFeedbackRequest) -> ExternalEvidenceResult:
        return execute(
            engine,
            context,
            request,
            kind="evaluation",
            action=EVALUATION_FEEDBACK_EXPORT,
        )

    role = (
        ("tool_id", "text"),
        ("kind", "text"),
        ("delivery", "json"),
        ("receipt_sha256", "blob_hash"),
    )
    binding = ExtensionBinding(
        backend["backend_id"],
        backend["version"],
        "python",
        "evaluation_feedback",
        "selected_by_action",
        "evaluation_feedback_receipt",
        role,
        readiness("LangSmith"),
        resource_fields=("resource_id",),
        plugin_id_field="plugin_id",
        lane_field="lane_id",
    )
    engine.registry.register(
        ActionSpec(
            EVALUATION_FEEDBACK_EXPORT,
            "Write one hash-bound numeric feedback row to a pre-existing LangSmith run through an exact project connector grant.",
            EvaluationFeedbackRequest,
            ExternalEvidenceResult,
            handler,
            permission="publish",
            profile="core",
            workflow="select-project-tools",
            mutates=True,
            requires_delta=True,
            required_tools=("Python", "HTTPX"),
            verifier=verify_external_evidence,
            verification_checks=("external_evidence_receipt",),
            tool_routes=(
                ToolRoute(
                    EVALUATION_FEEDBACK_EXPORT + ".langsmith",
                    handler,
                    ("Python", "HTTPX", "LangSmith"),
                    argument_values=(("tool_id", ("LangSmith",)),),
                    extension=binding,
                ),
            ),
        )
    )


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
    "EVALUATION_FEEDBACK_EXPORT",
    "EVALUATION_TOOLCHAIN_SCHEMA",
    "EvaluationFeedbackRequest",
    "build_evaluation_plan",
    "evaluation_tool_catalog",
    "register_evaluation_actions",
    "seal_evaluation_result",
]
