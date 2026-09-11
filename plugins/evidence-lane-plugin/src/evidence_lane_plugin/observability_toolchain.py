"""Secret-safe optional observability export contracts."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import Field, model_validator

from .hashing import canonical_json_bytes, sha256_bytes
from .registry import Contract

OBSERVABILITY_TOOLCHAIN_SCHEMA = "evidence-lane.observability-toolchain.v1"
OBSERVABILITY_EXPORT = "observability_export"
_SHA256 = re.compile(r"[A-F0-9]{64}")
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")
_ATTRIBUTE_KEY = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
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

_EXECUTABLE_SIGNALS = {
    "OpenTelemetry": {"trace", "metric", "log"},
    "Langfuse": {"span", "generation", "event", "tool", "evaluator"},
    "Grafana": {"dashboard_evidence", "alert_evidence", "trace_link"},
}


class ObservabilityExportRequest(Contract):
    """Export one hash-only observation through one exact provider resource."""

    tool_id: Literal["OpenTelemetry", "Langfuse", "Grafana"]
    signal: str = Field(min_length=3, max_length=32)
    lane_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    resource_id: str = Field(min_length=10, max_length=266)
    plugin_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{2,63}$")
    action_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    correlation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,127}$")
    input_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_at_unix_nano: int = Field(ge=1, le=4_102_444_800_000_000_000)
    duration_nanoseconds: int = Field(default=0, ge=0, le=86_400_000_000_000)
    metric_value: float | None = Field(default=None, allow_inf_nan=False)
    attribute_sha256: dict[str, str] = Field(default_factory=dict, max_length=32)
    dashboard_uid: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    timeout_seconds: float = Field(default=20, gt=0, le=60, allow_inf_nan=False)
    max_request_bytes: int = Field(default=131_072, ge=4096, le=262_144)
    max_response_bytes: int = Field(default=1_048_576, ge=1024, le=4_194_304)

    @model_validator(mode="after")
    def exact_observation(self):
        from .external_evidence_delivery import resource_scope
        from .lanes import CANONICAL_LANE_IDS
        from .redaction import contains_secret

        scope = resource_scope(self.tool_id, self.resource_id)
        timed = self.tool_id == "Langfuse" or (
            self.tool_id == "OpenTelemetry" and self.signal == "trace"
        )
        metric = self.tool_id == "OpenTelemetry" and self.signal == "metric"
        if (
            self.signal not in _EXECUTABLE_SIGNALS[self.tool_id]
            or self.lane_id not in CANONICAL_LANE_IDS
            or scope is None
            or (timed and self.duration_nanoseconds <= 0)
            or (not timed and self.duration_nanoseconds != 0)
            or (metric and self.metric_value is None)
            or (not metric and self.metric_value is not None)
            or (
                self.metric_value is not None
                and (not math.isfinite(self.metric_value) or abs(self.metric_value) > 1e18)
            )
            or contains_secret(self.correlation_id)
            or any(
                _ATTRIBUTE_KEY.fullmatch(key) is None
                or _LOWER_SHA256.fullmatch(value) is None
                or key.casefold() in _FORBIDDEN_FIELDS
                for key, value in self.attribute_sha256.items()
            )
            or self.dashboard_uid is not None
            and (
                self.tool_id != "Grafana"
                or scope != self.dashboard_uid
            )
        ):
            raise ValueError("OBSERVABILITY_EXPORT_SCOPE_INVALID")
        return self


def register_observability_actions(engine: Any) -> None:
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

    def handler(context: Any, request: ObservabilityExportRequest) -> ExternalEvidenceResult:
        return execute(
            engine,
            context,
            request,
            kind="observability",
            action=OBSERVABILITY_EXPORT,
        )

    role = (
        ("tool_id", "text"),
        ("kind", "text"),
        ("delivery", "json"),
        ("receipt_sha256", "blob_hash"),
    )
    routes = []
    for tool_id in ("OpenTelemetry", "Langfuse", "Grafana"):
        backend = EXPORT_BACKENDS[tool_id]
        binding = ExtensionBinding(
            backend["backend_id"],
            backend["version"],
            "python",
            "observability_export",
            "selected_by_action",
            "observability_export_receipt",
            role,
            readiness(tool_id),
            resource_fields=("resource_id",),
            plugin_id_field="plugin_id",
            lane_field="lane_id",
        )
        routes.append(
            ToolRoute(
                OBSERVABILITY_EXPORT + "." + tool_id.casefold(),
                handler,
                ("Python", "HTTPX", tool_id),
                argument_values=(("tool_id", (tool_id,)),),
                extension=binding,
            )
        )
    engine.registry.register(
        ActionSpec(
            OBSERVABILITY_EXPORT,
            "Send one bounded hash-only observation to an exact OTLP, Langfuse or Grafana connector resource.",
            ObservabilityExportRequest,
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
            tool_routes=tuple(routes),
        )
    )
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
    "OBSERVABILITY_EXPORT",
    "OBSERVABILITY_TOOLCHAIN_SCHEMA",
    "ObservabilityExportRequest",
    "build_observability_export",
    "observability_tool_catalog",
    "register_observability_actions",
]
