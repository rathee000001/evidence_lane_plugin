"""Conditional runtime and deployment evidence routes."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

DEPLOYMENT_TOOLCHAIN_SCHEMA = "evidence-lane.deployment-toolchain.v1"
_SHA256 = re.compile(r"[A-F0-9]{64}")
_TOOLS: dict[str, dict[str, Any]] = {
    "Docker": {"credential_names": [], "transport": "host_command"},
    "Kubernetes": {"credential_names": ["KUBECONFIG"], "transport": "host_command"},
    "AWS_Lambda": {
        "credential_names": ["AWS_PROFILE", "AWS_REGION"],
        "transport": "cloud_api",
    },
    "Google_Cloud_Run": {
        "credential_names": ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
        "transport": "cloud_api",
    },
    "Vercel_Git_integration": {
        "credential_names": ["VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"],
        "transport": "git_integration_or_api",
    },
    "AWS": {
        "credential_names": ["AWS_PROFILE", "AWS_REGION"],
        "transport": "cloud_api_or_cli",
    },
    "Azure": {
        "credential_names": ["AZURE_TENANT_ID", "AZURE_CLIENT_ID"],
        "transport": "cloud_api_or_cli",
    },
    "Google_Cloud": {
        "credential_names": ["GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"],
        "transport": "cloud_api_or_cli",
    },
}


def _hash(value: str, field: str) -> str:
    exact = value.strip().upper()
    if _SHA256.fullmatch(exact) is None:
        raise ValueError(f"{field} must be an exact SHA-256.")
    return exact


def build_deployment_route(
    *,
    tool_id: str,
    operation: str,
    project_id: str,
    task_id: str,
    artifact_sha256: str,
    source_commit_sha256: str,
    route_receipt_sha256: str,
    granted_tools: Iterable[str],
    external_write_approved: bool = False,
) -> dict[str, Any]:
    if tool_id not in _TOOLS:
        raise ValueError(f"Unknown deployment tool: {tool_id}")
    exact_operation = operation.strip().lower()
    if exact_operation not in {"validate", "plan", "deploy", "rollback", "readback"}:
        raise ValueError("Deployment operation is not supported.")
    if not project_id.strip() or not task_id.strip():
        raise ValueError("Deployment routing requires project and task identity.")
    if tool_id not in set(granted_tools):
        status = "BLOCKED_PROJECT_GRANT_REQUIRED"
    elif exact_operation in {"deploy", "rollback"} and not external_write_approved:
        status = "BLOCKED_EXTERNAL_WRITE_APPROVAL_REQUIRED"
    else:
        status = "PASS"
    body = {
        "schema": DEPLOYMENT_TOOLCHAIN_SCHEMA,
        "status": status,
        "tool_id": tool_id,
        "operation": exact_operation,
        "transport": _TOOLS[tool_id]["transport"],
        "project_id": project_id.strip(),
        "task_id": task_id.strip(),
        "artifact_sha256": _hash(artifact_sha256, "artifact_sha256"),
        "source_commit_sha256": _hash(source_commit_sha256, "source_commit_sha256"),
        "route_receipt_sha256": _hash(route_receipt_sha256, "route_receipt_sha256"),
        "credential_names": list(_TOOLS[tool_id]["credential_names"]),
        "credential_values_read": False,
        "external_write_approved": bool(external_write_approved),
        "network_or_command_executed": False,
        "deployment_result_is_evidence_only": True,
        "plan_authority": False,
        "goal_authority": False,
        "project_registration_authority": False,
        "tunnel_scheduler_authority": False,
        "hil_inferred": False,
    }
    return {**body, "route_sha256": sha256_bytes(canonical_json_bytes(body))}


def deployment_tool_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.deployment-tool-catalog.v1",
        "status": "PASS",
        "tools": [{"tool_id": tool_id, **contract} for tool_id, contract in _TOOLS.items()],
        "tool_count": len(_TOOLS),
        "counts_are_derived_not_fixed": True,
        "deployment_results_are_evidence_only": True,
        "no_lifecycle_or_scheduler_authority": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "DEPLOYMENT_TOOLCHAIN_SCHEMA",
    "build_deployment_route",
    "deployment_tool_catalog",
]
