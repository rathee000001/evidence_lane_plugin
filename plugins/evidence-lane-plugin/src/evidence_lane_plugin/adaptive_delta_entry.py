"""Replay-safe first-class Delta-entry authority orchestration."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from .errors import require
from .git_adapter import inspect_repository
from .hashing import atomic_write_json, canonical_json_bytes, sha256_bytes
from .internal_sdk import build_live_local_sdk_context
from .lanes import CANONICAL_LANE_IDS
from .live_authority_query import query_live_authorities

ADAPTIVE_DELTA_ENTRY_SCHEMA = "evidence-lane.adaptive-delta-entry-receipt.v1"
EXECUTABLE_DELTA_ENTRY_FORMULA_SCHEMA = (
    "evidence-lane.executable-delta-entry-formula.v2"
)
_ENV_UOP_EXECUTION_BUDGET_SCHEMA = "evidence-lane.env-uop-execution-budget.v1"
_CODE_TASK_CLASSES = frozenset(
    {"modify_code", "fix_bug", "add_bounded_feature", "prepare_patch"}
)
_TASK_CLASS_MODES: dict[str, tuple[str, ...]] = {
    "inspect": ("AL",),
    "explain": ("D", "AL"),
    "research": ("RS", "AL"),
    "modify_code": ("CD",),
    "fix_bug": ("CD",),
    "add_bounded_feature": ("CD",),
    "run_test": ("CD", "VAL"),
    "verify_result": ("AL", "VAL"),
    "prepare_patch": ("CD",),
}


def _mode_ids_for_delta(
    session: Any,
    *,
    runtime_task: Mapping[str, Any],
) -> list[str]:
    """Prefer the exact task binding, then derive a deterministic task-class mode."""

    metadata = cast(dict[str, Any], session.metadata)
    binding = metadata.get("task_mode_binding") or metadata.get("active_mode_binding")
    selected = (
        [
            str(item)
            for item in cast(dict[str, Any], binding).get("selected_mode_ids") or []
        ]
        if isinstance(binding, dict)
        else []
    )
    task_class = str(runtime_task.get("task_class") or "").strip().lower()
    if not selected:
        selected = list(_TASK_CLASS_MODES.get(task_class) or ())
    require(
        bool(selected),
        "ADAPTIVE_DELTA_ENTRY_MODE_REQUIRED",
        "Delta entry requires an exact task-mode binding or supported task class.",
        status="BLOCKED",
        task_class=task_class or None,
    )
    if task_class in _CODE_TASK_CLASSES and "CD" not in selected:
        selected.append("CD")
    return list(dict.fromkeys(selected))


def _safe_tool_routes(
    runtime_task: Mapping[str, Any],
) -> tuple[list[str], dict[str, str]]:
    raw_tools = [
        str(item).strip()
        for item in runtime_task.get("permitted_tools") or []
        if str(item).strip()
    ] or ["internal_sdk"]
    routes: list[str] = []
    aliases: dict[str, str] = {}
    for ordinal, raw in enumerate(raw_tools[:64], start=1):
        route = re.sub(r"[^A-Za-z0-9._:/-]+", "_", raw).strip("._:/-")[:96]
        if not route:
            route = f"tool_{ordinal}"
        candidate = route
        suffix = 2
        while candidate in aliases and aliases[candidate] != raw:
            candidate = f"{route[:88]}_{suffix}"
            suffix += 1
        aliases[candidate] = raw
        if candidate not in routes:
            routes.append(candidate)
    return routes, aliases


def _operator_assignment_plan(
    mode_governance: Mapping[str, Any],
    *,
    runtime_task: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    tools, aliases = _safe_tool_routes(runtime_task)
    assignments: list[dict[str, Any]] = []
    lane_counts: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    selected_lanes: list[str] = []
    for contract in cast(list[dict[str, Any]], mode_governance.get("contracts") or []):
        lanes = [str(item) for item in contract.get("canonical_lanes") or []]
        require(
            bool(lanes),
            "ADAPTIVE_DELTA_ENTRY_MODE_LANE_REQUIRED",
            "Every compiled mode requires one canonical execution lane.",
            status="MISMATCH",
            mode_id=contract.get("mode_id"),
        )
        selected_lanes.extend(lane for lane in lanes if lane not in selected_lanes)
        for operator in cast(list[dict[str, Any]], contract.get("operators") or []):
            ordinal = len(assignments)
            lane_id = lanes[ordinal % len(lanes)]
            tool_id = tools[ordinal % len(tools)]
            lane_counts[lane_id] += 1
            tool_counts[tool_id] += 1
            assignments.append(
                {
                    "ordinal": ordinal + 1,
                    "mode_id": str(contract["mode_id"]),
                    "operator_id": int(operator["operator_id"]),
                    "declared_effect": str(operator["effect"]),
                    "lane_id": lane_id,
                    "tool_id": tool_id,
                    "lane_units": 1,
                    "tool_invocations": 1,
                }
            )
    for lane in selected_lanes:
        lane_counts[lane] = max(1, lane_counts[lane])
    for tool in tools:
        tool_counts[tool] = max(1, tool_counts[tool])
    budget = {
        "schema": _ENV_UOP_EXECUTION_BUDGET_SCHEMA,
        "lane_units": dict(lane_counts),
        "tool_invocations": dict(tool_counts),
        "max_total_lane_units": sum(lane_counts.values()),
        "max_total_tool_invocations": sum(tool_counts.values()),
    }
    return budget, assignments, aliases


def _mathematical_execution_receipt(
    *,
    mode_governance: Mapping[str, Any],
    compiled_formula: Mapping[str, Any],
    route_receipts: list[dict[str, Any]],
    live_authority: Mapping[str, Any],
) -> dict[str, Any]:
    contracts = cast(list[dict[str, Any]], compiled_formula.get("contracts") or [])
    compiled_pairs = [
        (str(contract["mode_id"]), int(operator["operator_id"]))
        for contract in contracts
        for operator in cast(list[dict[str, Any]], contract.get("operators") or [])
    ]
    routed_pairs = [
        (str(row["mode_id"]), int(row["operator_id"])) for row in route_receipts
    ]
    chapters = sorted(
        {
            str(operator.get("chapter") or "")
            for contract in cast(
                list[dict[str, Any]], mode_governance.get("contracts") or []
            )
            for operator in cast(list[dict[str, Any]], contract.get("operators") or [])
            if str(operator.get("chapter") or "")
        }
    )
    conditions = {
        "mode_governance_pass": mode_governance.get("status") == "PASS",
        "compiled_formula_pass": compiled_formula.get("status") == "PASS",
        "operator_route_matrix_complete": compiled_pairs == routed_pairs,
        "all_operator_routes_pass": all(
            row.get("status") == "PASS" for row in route_receipts
        ),
        "live_authority_pass": live_authority.get("status") in {"PASS", "EMPTY"},
        "accepted_archive_not_opened": live_authority.get("accepted_archive_opened")
        is False,
        "accepted_archive_not_queried": live_authority.get("accepted_archive_queried")
        is False,
        "pointer_effect_none": all(
            row.get("pointer_effect") == "NONE" for row in route_receipts
        ),
        "hil_effect_none": all(
            row.get("hil_effect") == "NONE" for row in route_receipts
        ),
    }
    evaluated = all(conditions.values())
    core = {
        "schema": "evidence-lane.env-uop-mathematical-execution.v1",
        "status": "PASS" if evaluated else "FAIL",
        "formula_components": next(
            (
                dict(contract["formula"]["components"])
                for contract in cast(
                    list[dict[str, Any]], mode_governance.get("contracts") or []
                )
                if isinstance(contract.get("formula"), dict)
                and isinstance(contract["formula"].get("components"), dict)
            ),
            {},
        ),
        "boolean_ast": {
            "operator": "AND",
            "terms": [
                {"condition": key, "value": value} for key, value in conditions.items()
            ],
        },
        "formula_expression": (
            "S_next = V_mode(S_t AND G_gate AND Omega_op) AND "
            "NOT(ACCEPTED_ARCHIVE_QUERY OR POINTER_MOVE OR HIL_INFERENCE)"
        ),
        "precedence": {
            "boolean": ["PARENTHESES", "NOT", "AND", "OR"],
            "arithmetic_inside_operator": "BODMAS",
            "fabricated_numeric_operation_allowed": False,
        },
        "set_execution": {
            "mode_intersection": [
                str(contract["mode_id"])
                for contract in cast(
                    list[dict[str, Any]], mode_governance.get("contracts") or []
                )
            ],
            "canonical_lane_union": list(compiled_formula.get("canonical_lanes") or []),
            "operator_pair_set": [
                f"{mode}:{operator}" for mode, operator in compiled_pairs
            ],
        },
        "operator_matrix": {
            "row_count": len(contracts),
            "cell_count": len(compiled_pairs),
            "compiled_pairs": [
                f"{mode}:{operator}" for mode, operator in compiled_pairs
            ],
            "routed_pairs": [f"{mode}:{operator}" for mode, operator in routed_pairs],
            "complete": compiled_pairs == routed_pairs,
        },
        "mode_specific_math_activation": {
            "declared_chapters": chapters,
            "probability": any("Probability" in item for item in chapters),
            "permutation_combination": any(
                "Permutation" in item or "Combination" in item for item in chapters
            ),
            "matrices": any(
                "Matrices" in item or "Matrix" in item for item in chapters
            ),
            "activation_law": "ONLY_WHEN_SELECTED_MODE_SQLITE_OPERATOR_DECLARES_IT",
        },
        "conditions": conditions,
        "evaluated_result": evaluated,
        "null_execution": not evaluated,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _is_executable_env_uop_formula(value: object) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("schema") == EXECUTABLE_DELTA_ENTRY_FORMULA_SCHEMA
        and value.get("source_work_authorized") is True
        and isinstance(value.get("env_uop_runtime_execution"), dict)
        and value["env_uop_runtime_execution"].get("status") == "PASS"
        and isinstance(value.get("mathematical_execution"), dict)
        and value["mathematical_execution"].get("evaluated_result") is True
    )


def _sole_active_row(backlog: dict[str, Any], task_id: str) -> dict[str, Any]:
    projection = cast(dict[str, Any], backlog.get("goal_projection") or {})
    rows = [
        dict(row)
        for row in projection.get("rows") or []
        if isinstance(row, dict)
        and str(row.get("status") or "").lower() == "in_progress"
        and str(row.get("lifecycle_status") or "").upper() == "ACTIVE"
    ]
    require(
        len(rows) == 1 and rows[0].get("task_id") == task_id,
        "ADAPTIVE_DELTA_ENTRY_ACTIVE_BINDING_MISMATCH",
        "Delta entry requires the exact sole ACTIVE canonical Plan row.",
        status="MISMATCH",
        requested_task_id=task_id,
        active_task_ids=[row.get("task_id") for row in rows],
    )
    return rows[0]


def _write_immutable(path: Path, receipt: dict[str, Any]) -> None:
    if path.is_file():
        require(
            json.loads(path.read_text(encoding="utf-8")) == receipt,
            "ADAPTIVE_DELTA_ENTRY_RECEIPT_CONFLICT",
            "The immutable Delta-entry receipt path contains different bytes.",
            status="MISMATCH",
        )
        return
    atomic_write_json(path, receipt)


def _learning_inspection(
    service: Any,
    *,
    project_id: str,
    session_id: str,
    request_seed: str,
) -> dict[str, Any]:
    sdk, binding = build_live_local_sdk_context(
        service,
        project_id=project_id,
        session_id=session_id,
    )
    response = sdk.invoke(
        module_id="agent_learning",
        operation="inspect",
        binding=binding,
        payload={},
        request_id=f"delta-entry:{request_seed}:learning-inspect",
        timeout_ms=30_000,
    )
    data = dict(response.get("data") or {})
    require(
        response.get("status") == "PASS"
        and data.get("status") == "PASS"
        and data.get("integrity") == ["ok"]
        and int(data.get("foreign_key_errors") or 0) == 0,
        "ADAPTIVE_DELTA_ENTRY_LEARNING_INSPECTION_FAILED",
        "Delta entry requires the intact Learning candidate and weave authority.",
        status="FAIL",
    )
    return {
        "status": "PASS",
        "candidate_count": int(data.get("candidate_count") or 0),
        "auto_accepted_delta_count": int(data.get("auto_accepted_delta_count") or 0),
        "pending_weave_count": int(data.get("pending_weave_count") or 0),
        "learning_weaves": list(data.get("learning_weaves") or [])[:4],
        "current_pointer": data.get("current_pointer"),
        "integrity": data.get("integrity"),
        "foreign_key_errors": int(data.get("foreign_key_errors") or 0),
        "full_candidate_ledger_returned": False,
        "receipt_sha256": response.get("receipt_sha256"),
    }


def _execute_env_uop_action_plane(
    service: Any,
    *,
    project_id: str,
    session_id: str,
    request_seed: str,
    request: str,
    runtime_task: Mapping[str, Any],
    mode_ids: list[str],
    active_mode_binding: Mapping[str, Any] | None,
    live_authority: Mapping[str, Any],
) -> dict[str, Any]:
    """Classify, compile, and route ENV/UOP without adding public MCP actions."""

    custom_mode_selected = any(mode_id.startswith("X:") for mode_id in mode_ids)
    if custom_mode_selected:
        governance = (
            active_mode_binding.get("mode_governance")
            if isinstance(active_mode_binding, Mapping)
            else None
        )
        require(
            isinstance(governance, Mapping),
            "ADAPTIVE_DELTA_ENTRY_CUSTOM_MODE_BINDING_REQUIRED",
            "A custom mode requires its exact previously validated task binding.",
            status="MISMATCH",
        )
        validated_governance = cast(Mapping[str, Any], governance)
        validated_binding = cast(Mapping[str, Any], active_mode_binding)
        mode_governance = dict(validated_governance)
        classification_receipt = {
            "status": "PASS",
            "operation": "REUSE_ACTIVE_CUSTOM_MODE_BINDING",
            "receipt_sha256": validated_binding.get("binding_receipt_sha256"),
            "selected_mode_ids": mode_ids,
            "public_action_created": False,
        }
    else:
        classification_sdk, classification_binding = build_live_local_sdk_context(
            service,
            project_id=project_id,
            session_id=session_id,
            write_scope=("env_uop_operator_runtime:classify_mode",),
        )
        classified = classification_sdk.invoke(
            module_id="env_uop_operator_runtime",
            operation="classify_mode",
            binding=classification_binding,
            payload={"request": request, "explicit_modes": mode_ids},
            request_id=f"delta-entry:{request_seed}:mode-classify",
            timeout_ms=30_000,
        )
        classified_data = cast(dict[str, Any], classified.get("data") or {})
        require(
            classified.get("status") == "PASS"
            and classified_data.get("status") == "PASS"
            and isinstance(classified_data.get("mode_governance"), dict),
            "ADAPTIVE_DELTA_ENTRY_MODE_CLASSIFICATION_FAILED",
            "The ENV/UOP AI action plane failed to classify the Delta mode.",
            status="FAIL",
        )
        mode_governance = cast(dict[str, Any], classified_data["mode_governance"])
        classification_receipt = {
            "status": "PASS",
            "operation": "classify_mode",
            "receipt_sha256": classified.get("receipt_sha256"),
            "binding_sha256": classified.get("binding_sha256"),
            "selected_mode_ids": [
                str(row["id"])
                for row in cast(
                    list[dict[str, Any]], classified_data.get("selected_modes") or []
                )
            ],
            "mode_intersection": classified_data.get("mode_intersection"),
            "chat_lineage": classified_data.get("chat_lineage"),
            "public_action_created": False,
        }

    budget, assignments, tool_aliases = _operator_assignment_plan(
        mode_governance,
        runtime_task=runtime_task,
    )
    execution_sdk, execution_binding = build_live_local_sdk_context(
        service,
        project_id=project_id,
        session_id=session_id,
        write_scope=(
            "env_uop_operator_runtime:compile_formula",
            "env_uop_operator_runtime:route_operator",
        ),
    )
    compiled_response = execution_sdk.invoke(
        module_id="env_uop_operator_runtime",
        operation="compile_formula",
        binding=execution_binding,
        payload={
            "mode_governance": mode_governance,
            "execution_budget": budget,
        },
        request_id=f"delta-entry:{request_seed}:formula-compile",
        timeout_ms=30_000,
    )
    compiled_formula = cast(dict[str, Any], compiled_response.get("data") or {})
    require(
        compiled_response.get("status") == "PASS"
        and compiled_formula.get("status") == "PASS"
        and bool(compiled_formula.get("compiled_formula_sha256")),
        "ADAPTIVE_DELTA_ENTRY_FORMULA_COMPILATION_FAILED",
        "The ENV/UOP AI action plane failed to compile the selected formula.",
        status="FAIL",
    )
    compiled_index = {
        (str(contract["mode_id"]), int(operator["operator_id"])): operator
        for contract in cast(list[dict[str, Any]], compiled_formula["contracts"])
        for operator in cast(list[dict[str, Any]], contract.get("operators") or [])
    }
    route_receipts: list[dict[str, Any]] = []
    for assignment in assignments:
        key = (str(assignment["mode_id"]), int(assignment["operator_id"]))
        compiled_operator = compiled_index.get(key)
        require(
            isinstance(compiled_operator, dict)
            and compiled_operator.get("declared_effect")
            == assignment["declared_effect"],
            "ADAPTIVE_DELTA_ENTRY_COMPILED_OPERATOR_MISMATCH",
            "The compiled formula omitted or changed an assigned ENV/UOP operator.",
            status="MISMATCH",
            mode_id=key[0],
            operator_id=key[1],
        )
        routed = execution_sdk.invoke(
            module_id="env_uop_operator_runtime",
            operation="route_operator",
            binding=execution_binding,
            payload={
                "compiled_formula": compiled_formula,
                "mode_id": assignment["mode_id"],
                "operator_id": assignment["operator_id"],
                "requested_effect": assignment["declared_effect"],
                "lane_id": assignment["lane_id"],
                "tool_id": assignment["tool_id"],
                "lane_units": assignment["lane_units"],
                "tool_invocations": assignment["tool_invocations"],
            },
            request_id=(
                f"delta-entry:{request_seed}:operator-{int(assignment['ordinal']):03d}"
            ),
            timeout_ms=30_000,
        )
        data = cast(dict[str, Any], routed.get("data") or {})
        require(
            routed.get("status") == "PASS"
            and data.get("status") == "PASS"
            and data.get("route_executed") is True
            and data.get("compiled_formula_sha256")
            == compiled_formula["compiled_formula_sha256"],
            "ADAPTIVE_DELTA_ENTRY_OPERATOR_ROUTE_FAILED",
            "One ENV/UOP operator failed its compiled lane/tool route.",
            status="FAIL",
            mode_id=assignment["mode_id"],
            operator_id=assignment["operator_id"],
        )
        route_receipts.append(
            {
                "ordinal": assignment["ordinal"],
                "status": "PASS",
                "mode_id": data["mode_id"],
                "operator_id": data["operator_id"],
                "declared_effect": data["declared_effect"],
                "lane_id": data["lane_id"],
                "tool_id": data["tool_id"],
                "budget_consumption": data["budget_consumption"],
                "effect_receipt": data["effect_receipt"],
                "pointer_effect": data["pointer_effect"],
                "hil_effect": data["hil_effect"],
                "receipt_sha256": routed.get("receipt_sha256"),
                "operator_route_receipt_sha256": data.get("receipt_sha256"),
            }
        )
    mathematical = _mathematical_execution_receipt(
        mode_governance=mode_governance,
        compiled_formula=compiled_formula,
        route_receipts=route_receipts,
        live_authority=live_authority,
    )
    require(
        mathematical["evaluated_result"] is True,
        "ADAPTIVE_DELTA_ENTRY_ENV_UOP_EVALUATION_FAILED",
        "The ENV/UOP Boolean/operator matrix did not authorize source work.",
        status="FAIL",
        failed_conditions=[
            key for key, value in mathematical["conditions"].items() if not value
        ],
    )
    runtime_execution = {
        "schema": "evidence-lane.env-uop-ai-action-plane-execution.v1",
        "status": "PASS",
        "plane_role": "INTERNAL_AI_ACTION_PLANE_BETWEEN_SQLITE_AND_WORK",
        "public_action_sdk_role": "SEPARATE_OUTER_ACTION_ROUTER",
        "counted_as_public_action": False,
        "classification_receipt": classification_receipt,
        "mode_governance": mode_governance,
        "execution_budget": compiled_formula["execution_budget"],
        "tool_route_aliases": tool_aliases,
        "compiled_formula": compiled_formula,
        "compile_receipt_sha256": compiled_response.get("receipt_sha256"),
        "operator_route_receipts": route_receipts,
        "operator_route_count": len(route_receipts),
        "mathematical_execution_receipt_sha256": mathematical["receipt_sha256"],
        "source_work_authorized": True,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }
    return {
        **runtime_execution,
        "receipt_sha256": sha256_bytes(canonical_json_bytes(runtime_execution)),
        "mathematical_execution": mathematical,
    }


def run_adaptive_delta_entry(
    service: Any,
    project_id: str,
    session_id: str,
    *,
    classification_result: dict[str, Any] | None = None,
    actor: str = "ADAPTIVE_DELTA_ENTRY",
) -> dict[str, Any]:
    """Consume the live root and all separate authorities before Delta source work."""

    session_before = service.sessions.load(project_id, session_id)
    task_id = str(session_before.metadata.get("active_backlog_task_id") or "").strip()
    require(
        bool(task_id),
        "ADAPTIVE_DELTA_ENTRY_TASK_REQUIRED",
        "Delta entry requires the exact active Plan task binding.",
        status="MISMATCH",
    )
    backlog_before = service.store.backlog_status(project_id)
    active = _sole_active_row(backlog_before, task_id)
    service.store.plan_runtime_query(
        project_id,
        task_id=task_id,
        limit=20,
    )
    pointer_before = service.store.pointer(project_id).as_dict()
    repository_path = service.store.config(project_id).repository_path
    repository_before = inspect_repository(repository_path).as_dict()
    candidate_before = session_before.candidate_id

    predecessor_sub_pv = service.store.reconcile_verified_predecessor_sub_pv(
        project_id,
        active_task_id=task_id,
        session_id=session_id,
    )
    plan_after_sub_pv = service.store.plan_runtime_query(
        project_id,
        task_id=task_id,
        limit=20,
    )
    linked_steers = list(plan_after_sub_pv.get("steers") or [])
    normalized_steer_contract_body = {
        "schema": "evidence-lane.normalized-delta-steer-contract.v1",
        "task_id": task_id,
        "task_contract_sha256": plan_after_sub_pv.get("contract", {}).get(
            "task_contract_sha256"
        ),
        "steer_count": len(linked_steers),
        "ordered_steers": [
            {
                "delta_id": row.get("delta_id"),
                "delta_sha256": row.get("delta_sha256"),
                "boundary": row.get("boundary"),
                "classification": row.get("classification"),
                "task_steer_sequence": row.get("task_steer_sequence"),
            }
            for row in linked_steers
        ],
        "immutable_steer_history_rewritten": False,
        "verification_scope": "ONCE_PER_NORMALIZED_DELTA_ROW",
        "regression_repeated_per_steer": False,
    }
    normalized_steer_contract = {
        **normalized_steer_contract_body,
        "normalized_contract_sha256": sha256_bytes(
            canonical_json_bytes(normalized_steer_contract_body)
        ),
    }
    query_parts = [
        task_id,
        str(active.get("title") or ""),
        str(active.get("requested_outcome") or ""),
        str(active.get("stage") or ""),
        *[str(row.get("text") or "") for row in linked_steers[-8:]],
    ]
    bounded_query = " ".join(part.strip() for part in query_parts if part.strip())[
        :4096
    ]
    live_authority = query_live_authorities(
        service,
        project_id=project_id,
        session_id=session_id,
        query=bounded_query,
        limit=8,
        refresh_on_miss=True,
    )
    require(
        live_authority.get("status") in {"PASS", "EMPTY"}
        and live_authority.get("accepted_archive_opened") is False
        and live_authority.get("accepted_archive_queried") is False,
        "ADAPTIVE_DELTA_ENTRY_AUTHORITY_QUERY_FAILED",
        "Delta entry requires the bounded live-root query and one miss-refresh-refire cycle.",
        status="FAIL",
    )
    request_seed = sha256_bytes(
        canonical_json_bytes(
            {
                "project_id": project_id,
                "session_id": session_id,
                "task_id": task_id,
                "query": bounded_query,
                "classification_binding": (
                    (classification_result or {}).get("classification_binding")
                ),
            }
        )
    )[:32].lower()
    learning = _learning_inspection(
        service,
        project_id=project_id,
        session_id=session_id,
        request_seed=request_seed,
    )

    authority_summary = cast(dict[str, Any], live_authority["authorities"])
    runtime_task = dict((classification_result or {}).get("task") or {})
    if not runtime_task:
        runtime_task = dict(session_before.task or {})
    if not runtime_task.get("task_class"):
        runtime_task["task_class"] = active.get("task_classification")
    mode_ids = _mode_ids_for_delta(session_before, runtime_task=runtime_task)
    active_mode_value = session_before.metadata.get("task_mode_binding") or (
        session_before.metadata.get("active_mode_binding")
    )
    active_mode_binding = (
        cast(dict[str, Any], active_mode_value)
        if isinstance(active_mode_value, dict)
        else None
    )
    env_uop_execution = _execute_env_uop_action_plane(
        service,
        project_id=project_id,
        session_id=session_id,
        request_seed=request_seed,
        request=bounded_query,
        runtime_task=runtime_task,
        mode_ids=mode_ids,
        active_mode_binding=active_mode_binding,
        live_authority=live_authority,
    )
    mathematical_execution = cast(
        dict[str, Any], env_uop_execution["mathematical_execution"]
    )
    activity_id = f"delta_entry_{request_seed}"
    activity = service.sessions.record_activity(
        project_id,
        session_id,
        activity_type="tool.selected",
        visible_payload={
            "operation": "ADAPTIVE_DELTA_ENTRY",
            "active_plan_task_id": task_id,
            "plan_runtime_query_executed": True,
            "linked_steer_count": len(linked_steers),
            "normalized_steer_contract_sha256": normalized_steer_contract[
                "normalized_contract_sha256"
            ],
            "predecessor_sub_pv_state": predecessor_sub_pv.get("state"),
            "six_authority_query_status": live_authority.get("status"),
            "fallback_refresh_performed": live_authority.get("refresh_performed"),
            "bounded_retry_performed": live_authority.get("bounded_retry_performed"),
            "learning_candidate_count": learning["candidate_count"],
            "learning_auto_accepted_delta_count": learning["auto_accepted_delta_count"],
            "learning_pending_weave_count": learning["pending_weave_count"],
            "env_uop_ai_action_plane_status": env_uop_execution["status"],
            "env_uop_ai_action_plane_receipt_sha256": env_uop_execution[
                "receipt_sha256"
            ],
            "compiled_formula_sha256": env_uop_execution["compiled_formula"][
                "compiled_formula_sha256"
            ],
            "operator_route_count": env_uop_execution["operator_route_count"],
            "mathematical_execution_receipt_sha256": mathematical_execution[
                "receipt_sha256"
            ],
            "accepted_archive_queried": False,
            "source_work_authorized_after_entry": True,
        },
        event_id=activity_id,
    )
    source_event_id = str(cast(dict[str, Any], activity["event"])["event_id"])

    existing_formula_events = list(plan_after_sub_pv.get("formula_events") or [])
    entry_events = [
        row
        for row in existing_formula_events
        if row.get("event_kind") == "ENTRY_FORMULA"
    ]
    exit_events = [
        row
        for row in existing_formula_events
        if row.get("event_kind") == "EXIT_FORMULA"
    ]
    require(
        len(entry_events) <= 1 and len(exit_events) <= 1,
        "ADAPTIVE_DELTA_ENTRY_FORMULA_CARDINALITY_INVALID",
        "Delta entry requires at most one persisted ENTRY and EXIT formula.",
        status="MISMATCH",
    )
    formula = {
        "schema": EXECUTABLE_DELTA_ENTRY_FORMULA_SCHEMA,
        "fired_modes": mode_ids,
        "modes_fired": mode_ids,
        "operators": [
            int(row["operator_id"])
            for row in env_uop_execution["operator_route_receipts"]
        ],
        "operators_fired": [
            f"{row['mode_id']}:{row['operator_id']}"
            for row in env_uop_execution["operator_route_receipts"]
        ],
        "bounded_source_locators": list(runtime_task.get("permitted_paths") or [])[:32]
        or [task_id],
        "sector_locators": list(CANONICAL_LANE_IDS),
        "env_uop_terms": [
            str(live_authority["env_uop"].get(key) or "UNAVAILABLE")
            for key in (
                "env_authority_sha256",
                "uop_authority_sha256",
                "derived_projection_sha256",
                "flash_receipt_sha256",
            )
        ],
        "assumptions": [
            "accepted_pointer_is_baseline_identity_only",
            "accepted_archive_is_not_a_query_source",
            "authorities_remain_separate",
        ],
        "intended_validator": (
            "LOCKED_SQLITE_MMD_MODE_COMPILE_OPERATOR_MATRIX_AND_LIVE_AUTHORITY"
        ),
        "expected_result": str(active.get("requested_outcome") or task_id),
        "formula_expression": mathematical_execution["formula_expression"],
        "authority_query_receipt_sha256": sha256_bytes(
            canonical_json_bytes(live_authority)
        ),
        "predecessor_sub_pv_receipt": predecessor_sub_pv,
        "learning_entry_receipt": learning,
        "env_uop_runtime_execution": env_uop_execution,
        "mathematical_execution": mathematical_execution,
        "public_action_sdk_separate": True,
        "env_uop_ai_action_plane_separate": True,
        "source_work_authorized": True,
        "accepted_archive_queried": False,
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }
    if entry_events:
        if exit_events:
            formula_event = dict(exit_events[0])
            formula_disposition = "CLOSED_ROW_ENTRY_AUDITED_NO_APPEND"
        else:
            open_events = [
                row
                for row in existing_formula_events
                if row.get("event_kind") in {"ENTRY_FORMULA", "MUTATION"}
            ]
            require(
                bool(open_events),
                "ADAPTIVE_DELTA_ENTRY_OPEN_FORMULA_HEAD_MISSING",
                "The open Delta formula has no append-only head.",
                status="MISMATCH",
            )
            open_head = dict(open_events[-1])
            if _is_executable_env_uop_formula(open_head.get("formula")):
                formula_event = open_head
                formula_disposition = "EXISTING_OPEN_EXECUTABLE_ENTRY_REUSED"
            else:
                prior_formula_sha256 = str(open_head.get("formula_sha256") or "")
                require(
                    len(prior_formula_sha256) == 64,
                    "ADAPTIVE_DELTA_ENTRY_OPEN_FORMULA_HASH_INVALID",
                    "The legacy open formula cannot be superseded without its exact hash.",
                    status="MISMATCH",
                )
                persisted = service.store.record_task_formula(
                    project_id,
                    task_id=task_id,
                    event_kind="MUTATION",
                    source_event_id=source_event_id,
                    session_id=session_id,
                    formula=formula,
                    actor=actor,
                    prior_formula_sha256=prior_formula_sha256,
                    changed_terms={
                        "env_uop_runtime": "SQLITE_MMD_EXECUTABLE_AI_ACTION_PLANE",
                        "superseded_formula_sha256": prior_formula_sha256,
                    },
                    cause_evidence_locator=f"delta-entry://{request_seed}/runtime-repair",
                    event_id=f"formula_entry_runtime_repair_{request_seed}",
                )
                formula_event = dict(persisted["event"])
                formula_disposition = (
                    "LEGACY_OPEN_ENTRY_SUPERSEDED_BY_EXECUTABLE_MUTATION"
                )
    else:
        require(
            not exit_events,
            "ADAPTIVE_DELTA_ENTRY_EXIT_WITHOUT_ENTRY",
            "A Delta row cannot have an EXIT formula without its ENTRY formula.",
            status="MISMATCH",
        )
        persisted = service.store.record_task_formula(
            project_id,
            task_id=task_id,
            event_kind="ENTRY_FORMULA",
            source_event_id=source_event_id,
            session_id=session_id,
            formula=formula,
            actor=actor,
            event_id=f"formula_entry_{request_seed}",
        )
        formula_event = dict(persisted["event"])
        formula_disposition = "NEW_EXECUTABLE_ENTRY_FORMULA_APPENDED"

    pointer_after = service.store.pointer(project_id).as_dict()
    session_after = service.sessions.load(project_id, session_id)
    backlog_after = service.store.backlog_status(project_id)
    _sole_active_row(backlog_after, task_id)
    repository_after = inspect_repository(repository_path).as_dict()
    protected_repository_fields = (
        "branch",
        "commit_sha",
        "tree_sha",
        "worktree_sha256",
    )
    repository_unchanged = all(
        repository_before.get(field) == repository_after.get(field)
        for field in protected_repository_fields
    )
    require(
        pointer_after == pointer_before
        and session_after.candidate_id == candidate_before
        and repository_unchanged,
        "ADAPTIVE_DELTA_ENTRY_PROTECTED_STATE_CHANGED",
        "Delta entry changed the project pointer, pending candidate, or dirty repository identity.",
        status="MISMATCH",
    )
    plan_after = service.store.plan_runtime_query(
        project_id,
        task_id=task_id,
        limit=20,
    )
    receipt_body = {
        "schema": ADAPTIVE_DELTA_ENTRY_SCHEMA,
        "status": "PASS",
        "project_id": project_id,
        "session_id": session_id,
        "task_id": task_id,
        "source_event_id": source_event_id,
        "formula_disposition": formula_disposition,
        "formula_event_id": formula_event.get("event_id"),
        "formula_sha256": formula_event.get("formula_sha256"),
        "plan_runtime": {
            "query_mode": plan_after.get("query_mode"),
            "linked_steer_count": len(linked_steers),
            "linked_steers": linked_steers,
            "entry_formula_count": sum(
                row.get("event_kind") == "ENTRY_FORMULA"
                for row in plan_after.get("formula_events") or []
            ),
            "exit_formula_count": sum(
                row.get("event_kind") == "EXIT_FORMULA"
                for row in plan_after.get("formula_events") or []
            ),
            "canonical_plan_sha256_before": cast(
                dict[str, Any], backlog_before["goal_projection"]
            ).get("canonical_plan_sha256"),
            "canonical_plan_sha256_after": cast(
                dict[str, Any], backlog_after["goal_projection"]
            ).get("canonical_plan_sha256"),
        },
        "predecessor_sub_pv": predecessor_sub_pv,
        "normalized_steer_contract": normalized_steer_contract,
        "live_authority_query": {
            "status": live_authority.get("status"),
            "result_state": live_authority.get("result_state"),
            "refresh_required": live_authority.get("refresh_required"),
            "refresh_performed": live_authority.get("refresh_performed"),
            "bounded_retry_performed": live_authority.get("bounded_retry_performed"),
            "receipt_sha256": sha256_bytes(canonical_json_bytes(live_authority)),
        },
        "authorities_consumed": {
            key: {
                "status": (value.get("status") if isinstance(value, dict) else None),
                "authority": (
                    value.get("authority") if isinstance(value, dict) else key
                ),
            }
            for key, value in authority_summary.items()
        },
        "learning": learning,
        "env_uop_ai_action_plane": {
            "status": env_uop_execution["status"],
            "plane_role": env_uop_execution["plane_role"],
            "public_action_sdk_role": env_uop_execution["public_action_sdk_role"],
            "counted_as_public_action": False,
            "selected_mode_ids": mode_ids,
            "runtime_authority": env_uop_execution["mode_governance"].get(
                "runtime_authority"
            ),
            "compiled_formula_sha256": env_uop_execution["compiled_formula"][
                "compiled_formula_sha256"
            ],
            "operator_route_count": env_uop_execution["operator_route_count"],
            "receipt_sha256": env_uop_execution["receipt_sha256"],
        },
        "mathematical_execution": mathematical_execution,
        "public_action_sdk_separate": True,
        "env_uop_ai_action_plane_separate": True,
        "authority_time_boundary": {
            "full_pv_pointer_baseline": pointer_before.get("accepted_pv"),
            "full_pv_history_role": "IMMUTABLE_PV_N_MINUS_1_BASELINE",
            "sector_lane_snapshot_role": (
                "PROGRESSIVE_LIVE_ROOT_THROUGH_PREDECESSOR_DELTA_EXIT"
            ),
            "predecessor_sub_pv_id": (
                predecessor_sub_pv.get("sub_pv_id")
                or dict(predecessor_sub_pv.get("sub_pv_acceptance") or {}).get(
                    "sub_pv_id"
                )
            ),
            "predecessor_sub_pv_role": (
                "LATEST_AUTO_ACCEPTED_DELTA_CHECKPOINT_AND_LANE_WATERMARK"
            ),
            "delta_learning_role": "LATEST_AUTO_ACCEPTED_PROCEDURAL_EVIDENCE",
            "dirty_live_repository_role": (
                "CURRENT_ACTIVE_DELTA_IMPLEMENTATION_UNDER_CONSTRUCTION"
            ),
            "current_delta_present_in_sector_lanes": False,
            "completed_predecessor_deltas_present_in_sector_lanes": True,
            "current_delta_refresh_owner": "ADAPTIVE_DELTA_EXIT",
            "source_test_claim_scope": "SOURCE_IMPLEMENTATION_ONLY",
            "installed_public_behavior_claimed": False,
        },
        "accepted_archive_opened": False,
        "accepted_archive_queried": False,
        "accepted_pointer_used_as_baseline_only": True,
        "project_pointer_before": pointer_before,
        "project_pointer_after": pointer_after,
        "project_candidate_before": candidate_before,
        "project_candidate_after": session_after.candidate_id,
        "repository_identity_unchanged": repository_unchanged,
        "source_work_authorized": mathematical_execution["evaluated_result"],
        "candidate_created": False,
        "hil_inferred": False,
        "pointer_moved": False,
    }
    receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_body))
    receipt = {**receipt_body, "receipt_sha256": receipt_sha256}
    receipt_path = (
        service.store.project_root(project_id)
        / "receipts"
        / "delta-entry"
        / f"{receipt_sha256.lower()}.json"
    )
    _write_immutable(receipt_path, receipt)
    return {
        "status": "PASS",
        "receipt": receipt,
        "receipt_path": str(receipt_path),
        "raw_plan_returned": False,
        "raw_pv_returned": False,
        "raw_chat_lineage_returned": False,
    }
