"""Universal ENV/UOP decision contract for plugin data-touch operations."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

ENV_UOP_TOOL_ROUTING_SCHEMA = "evidence-lane.env-uop-tool-routing.v1"
_SHA256 = re.compile(r"[A-F0-9]{64}")


def _hash(value: str, field: str) -> str:
    exact = value.strip().upper()
    if _SHA256.fullmatch(exact) is None:
        raise ValueError(f"{field} must be an exact SHA-256.")
    return exact


def route_env_uop_data_touch(
    *,
    project_id: str,
    task_id: str,
    action_name: str,
    action_schema_sha256: str,
    authority_id: str,
    lane_id: str,
    host_profile: str,
    operation: str,
    data_locality: str,
    ordered_candidate_tools: Iterable[str],
    available_tools: Iterable[str],
    granted_tools: Iterable[str],
    grant_required_tools: Iterable[str],
    formula_sha256: str,
    operator_id: str,
    work_gate_open: bool,
) -> dict[str, Any]:
    identities = {
        "project_id": project_id.strip(),
        "task_id": task_id.strip(),
        "action_name": action_name.strip(),
        "authority_id": authority_id.strip(),
        "lane_id": lane_id.strip().lower(),
        "host_profile": host_profile.strip().upper(),
        "operation": operation.strip().lower(),
        "data_locality": data_locality.strip().upper(),
        "operator_id": operator_id.strip(),
    }
    if not all(identities.values()):
        raise ValueError("ENV/UOP routing requires complete operation identity.")
    ordered = list(
        dict.fromkeys(str(value).strip() for value in ordered_candidate_tools if str(value).strip())
    )
    if not ordered:
        raise ValueError("ENV/UOP routing requires an ordered candidate tool list.")
    available = set(available_tools)
    granted = set(granted_tools)
    grant_required = set(grant_required_tools)
    env_eligible = [
        tool
        for tool in ordered
        if tool in available and (tool not in grant_required or tool in granted)
    ]
    selected = env_eligible[:1] if work_gate_open else []
    if not work_gate_open:
        status = "BLOCKED_UOP_WORK_GATE_CLOSED"
    elif not selected:
        status = "BLOCKED_NO_ENV_ELIGIBLE_TOOL"
    else:
        status = "PASS"
    env_decision = {
        "context_identity": identities,
        "ordered_candidate_tools": ordered,
        "available_tools": sorted(available),
        "granted_tools": sorted(granted),
        "grant_required_tools": sorted(grant_required),
        "eligible_tools": env_eligible,
        "selected_tool": selected[0] if selected else None,
        "selection_rule": "FIRST_ORDERED_AVAILABLE_AND_GRANTED",
        "credential_values_read": False,
    }
    uop_decision = {
        "formula_sha256": _hash(formula_sha256, "formula_sha256"),
        "operator_id": identities["operator_id"],
        "work_gate_open": bool(work_gate_open),
        "selected_tool": selected[0] if selected else None,
        "fallback_allowed_only_in_declared_order": True,
        "can_override_env": False,
        "can_override_project_authority": False,
        "can_infer_hil": False,
    }
    body = {
        "schema": ENV_UOP_TOOL_ROUTING_SCHEMA,
        "status": status,
        "action_schema_sha256": _hash(
            action_schema_sha256, "action_schema_sha256"
        ),
        "env_decision": env_decision,
        "uop_decision": uop_decision,
        "selected_tools": selected,
        "selected_tool_count_maximum": 1,
        "data_touch_allowed": status == "PASS",
        "result_must_validate_before_authority_write": True,
        "receipt_required_after_result": True,
        "run_every_tool": False,
    }
    return {**body, "decision_sha256": sha256_bytes(canonical_json_bytes(body))}


def env_uop_tool_routing_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.env-uop-tool-routing-catalog.v1",
        "status": "PASS",
        "selection_inputs": [
            "project",
            "task",
            "action_schema",
            "authority",
            "lane",
            "host",
            "operation",
            "data_locality",
            "availability",
            "grant",
            "formula",
            "operator",
            "work_gate",
        ],
        "workflow_names_hardcoded": False,
        "tool_count_is_fixed_ceiling": False,
        "uop_can_override_env": False,
        "uop_can_override_project_authority": False,
        "hil_inference_allowed": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "ENV_UOP_TOOL_ROUTING_SCHEMA",
    "env_uop_tool_routing_catalog",
    "route_env_uop_data_touch",
]
