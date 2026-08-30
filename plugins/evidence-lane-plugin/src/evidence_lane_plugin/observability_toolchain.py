"""Secret-safe optional observability export contracts."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from .hashing import canonical_json_bytes, sha256_bytes

OBSERVABILITY_TOOLCHAIN_SCHEMA = "evidence-lane.observability-toolchain.v1"
_SHA256 = re.compile(r"[A-F0-9]{64}")
_FORBIDDEN_FIELDS = {
    "prompt",
    "response",
    "source_bytes",
    "secret",
    "token",
    "password",
    "private_key",
    "hil_token",
}
_TOOLS: dict[str, dict[str, Any]] = {
    "OpenTelemetry": {
        "signals": ["traces", "metrics", "logs"],
        "credential_names": ["OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS"],
    },
    "Langfuse": {
        "signals": ["trace", "span", "generation", "score"],
        "credential_names": [
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
            "LANGFUSE_BASE_URL",
        ],
    },
    "Helicone": {
        "signals": ["request_metadata", "cost", "latency"],
        "credential_names": ["HELICONE_API_KEY", "HELICONE_BASE_URL"],
    },
    "Grafana": {
        "signals": ["dashboard_evidence", "alert_evidence", "trace_link"],
        "credential_names": ["GRAFANA_URL", "GRAFANA_SERVICE_ACCOUNT_TOKEN"],
    },
}


def _hash(value: str, field: str) -> str:
    exact = value.strip().upper()
    if _SHA256.fullmatch(exact) is None:
        raise ValueError(f"{field} must be an exact SHA-256.")
    return exact


def build_observability_export(
    *,
    tool_id: str,
    signal: str,
    project_id: str,
    task_id: str,
    action_name: str,
    correlation_id: str,
    input_sha256: str,
    result_sha256: str,
    redaction_receipt_sha256: str,
    attributes: Mapping[str, str | int | float | bool],
    granted_tools: Iterable[str],
) -> dict[str, Any]:
    if tool_id not in _TOOLS:
        raise ValueError(f"Unknown observability tool: {tool_id}")
    exact_signal = signal.strip().lower()
    if exact_signal not in _TOOLS[tool_id]["signals"]:
        raise ValueError(f"Unsupported {tool_id} signal: {signal}")
    if not all(
        value.strip() for value in (project_id, task_id, action_name, correlation_id)
    ):
        raise ValueError("Observability requires project/task/action/correlation identity.")
    normalized_attributes = {str(key): value for key, value in attributes.items()}
    forbidden = sorted(
        key for key in normalized_attributes if key.casefold() in _FORBIDDEN_FIELDS
    )
    if forbidden:
        raise ValueError(f"Observability attributes contain forbidden fields: {forbidden}")
    status = "PASS" if tool_id in set(granted_tools) else "BLOCKED_PROJECT_GRANT_REQUIRED"
    body = {
        "schema": OBSERVABILITY_TOOLCHAIN_SCHEMA,
        "status": status,
        "tool_id": tool_id,
        "signal": exact_signal,
        "project_id": project_id.strip(),
        "task_id": task_id.strip(),
        "action_name": action_name.strip(),
        "correlation_id": correlation_id.strip(),
        "input_sha256": _hash(input_sha256, "input_sha256"),
        "result_sha256": _hash(result_sha256, "result_sha256"),
        "redaction_receipt_sha256": _hash(
            redaction_receipt_sha256, "redaction_receipt_sha256"
        ),
        "attributes": normalized_attributes,
        "credential_names": list(_TOOLS[tool_id]["credential_names"]),
        "credential_values_read": False,
        "raw_prompt_exported": False,
        "raw_response_exported": False,
        "raw_source_exported": False,
        "hil_token_exported": False,
        "network_call_performed": False,
        "export_failure_blocks_authority_commit": False,
        "evidence_lane_receipt_remains_authority": True,
    }
    return {**body, "export_sha256": sha256_bytes(canonical_json_bytes(body))}


def observability_tool_catalog() -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.observability-tool-catalog.v1",
        "status": "PASS",
        "tools": [{"tool_id": tool_id, **contract} for tool_id, contract in _TOOLS.items()],
        "tool_count": len(_TOOLS),
        "counts_are_derived_not_fixed": True,
        "credentials_external": True,
        "raw_authority_payload_export_allowed": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


__all__ = [
    "OBSERVABILITY_TOOLCHAIN_SCHEMA",
    "build_observability_export",
    "observability_tool_catalog",
]
