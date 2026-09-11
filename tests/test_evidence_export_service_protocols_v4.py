"""Actual HTTPX protocol shapes for bounded evaluation and observation exports."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.evaluation_toolchain import (
    EVALUATION_FEEDBACK_EXPORT,
    EvaluationFeedbackRequest,
)
from evidence_lane_plugin.external_evidence_delivery import (
    endpoint,
    parse_otlp_headers,
    programs,
    validate_result,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.observability_toolchain import (
    OBSERVABILITY_EXPORT,
    ObservabilityExportRequest,
)
from pydantic import ValidationError

from .evidence_export_http_fixture import (
    CONFIG_KEYS,
    ENDPOINT,
    ENVIRONMENT,
    HASH_A,
    HASH_B,
    HASH_C,
    RESOURCES,
    RUN_ID,
    SECRETS,
    SESSION_ID,
)


def evaluation_arguments() -> dict:
    return {
        "tool_id": "LangSmith",
        "lane_id": "receipts",
        "resource_id": RESOURCES["LangSmith"],
        "action_name": "sample_action",
        "run_id": RUN_ID,
        "session_id": SESSION_ID,
        "feedback_key": "correctness",
        "score": 0.75,
        "observed_at_unix_nano": 1_800_000_000_000_000_000,
        "action_schema_sha256": HASH_A,
        "route_sha256": HASH_B,
        "dataset_sha256": HASH_C,
        "redaction_receipt_sha256": "d" * 64,
    }


def observation_arguments(tool_id: str, signal: str) -> dict:
    result = {
        "tool_id": tool_id,
        "signal": signal,
        "lane_id": "receipts",
        "resource_id": RESOURCES[tool_id],
        "action_name": "sample_action",
        "correlation_id": "correlation-one",
        "input_sha256": HASH_A,
        "result_sha256": HASH_B,
        "redaction_receipt_sha256": HASH_C,
        "observed_at_unix_nano": 1_800_000_000_000_000_000,
        "duration_nanoseconds": 100 if tool_id == "Langfuse" or signal == "trace" else 0,
        "attribute_sha256": {"source": "d" * 64},
    }
    if signal == "metric":
        result["metric_value"] = 1.5
    if tool_id == "Grafana":
        result["dashboard_uid"] = "dashboard-one"
    return result


def child_config(tool_id: str) -> dict[str, str]:
    endpoint_key = {
        "LangSmith": "LANGSMITH_ENDPOINT",
        "OpenTelemetry": "OTEL_EXPORTER_OTLP_ENDPOINT",
        "Langfuse": "LANGFUSE_BASE_URL",
        "Grafana": "GRAFANA_URL",
    }[tool_id]
    return {
        key: ENVIRONMENT[key]
        for key in CONFIG_KEYS[tool_id]
        if key != endpoint_key
    }


def run_worker(tmp_path: Path, tool_id: str, kind: str, signal: str | None, fixture=None):
    model = EvaluationFeedbackRequest if kind == "evaluation" else ObservabilityExportRequest
    arguments = evaluation_arguments() if kind == "evaluation" else observation_arguments(tool_id, str(signal))
    request = model.model_validate(arguments)
    action = EVALUATION_FEEDBACK_EXPORT if kind == "evaluation" else OBSERVABILITY_EXPORT
    program_map = programs()
    body = {
        "schema": "evidence-lane.external-evidence-request.v4",
        "kind": kind,
        "action": action,
        "arguments": request.model_dump(mode="json"),
        "project_id": "project-one",
        "task_id": "task-one",
        "expires_at": "NO_EXPIRY",
        "programs": program_map,
    }
    raw = canonical_json_bytes(body)
    digest = sha256_bytes(raw).lower()
    (tmp_path / "request.json").write_bytes(raw)
    (tmp_path / "fixture.json").write_text(
        json.dumps(
            {
                "tool_id": tool_id,
                "kind": kind,
                "signal": signal,
                "configuration": fixture or {},
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        _EVI_EXPORT_ENDPOINT=ENDPOINT,
        _EVI_EXPORT_CONFIG=json.dumps(child_config(tool_id), separators=(",", ":")),
    )
    wrapper = Path(__file__).with_name("evidence_export_http_fixture_worker.py")
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(wrapper), digest],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return request, program_map, result


@pytest.mark.parametrize(
    ("tool_id", "kind", "signal"),
    [
        ("LangSmith", "evaluation", None),
        ("OpenTelemetry", "observability", "trace"),
        ("OpenTelemetry", "observability", "metric"),
        ("OpenTelemetry", "observability", "log"),
        ("Langfuse", "observability", "span"),
        ("Langfuse", "observability", "generation"),
        ("Langfuse", "observability", "event"),
        ("Langfuse", "observability", "tool"),
        ("Langfuse", "observability", "evaluator"),
        ("Grafana", "observability", "dashboard_evidence"),
        ("Grafana", "observability", "alert_evidence"),
        ("Grafana", "observability", "trace_link"),
    ],
)
def test_each_provider_shape_runs_once_and_returns_only_bound_evidence(
    tmp_path: Path,
    tool_id: str,
    kind: str,
    signal: str | None,
) -> None:
    request, program_map, process = run_worker(tmp_path, tool_id, kind, signal)
    assert process.returncode == 0, process.stderr + process.stdout
    response = json.loads(process.stdout)
    assert response["status"] == "ok"
    assert response["program_sha256"] == sha256_bytes(
        canonical_json_bytes(program_map)
    ).lower()
    validate_result(
        response["result"],
        request,
        kind=kind,
        project_id="project-one",
        task_id="task-one",
    )
    serialized = json.dumps(response)
    assert all(secret not in serialized for secret in SECRETS)
    assert response["result"]["raw_prompt_exported"] is False
    assert response["result"]["raw_response_exported"] is False
    assert response["result"]["project_authority_mutated"] is False


@pytest.mark.parametrize(
    ("tool_id", "kind", "signal", "fixture", "error"),
    [
        ("OpenTelemetry", "observability", "trace", {"partial_success": True}, "EVIDENCE_EXPORT_PROVIDER_FAILURE"),
        ("Grafana", "observability", "trace_link", {"provider_failure": True}, "EVIDENCE_EXPORT_PROVIDER_FAILURE"),
        ("LangSmith", "evaluation", None, {"status": 307}, "EVIDENCE_EXPORT_HTTP_STATUS"),
        ("Langfuse", "observability", "span", {"invalid_json": True}, "EVIDENCE_EXPORT_RESPONSE_INVALID"),
        ("OpenTelemetry", "observability", "log", {"content_encoding": "gzip"}, "EVIDENCE_EXPORT_RESPONSE_INVALID"),
    ],
)
def test_partial_redirect_invalid_and_encoded_responses_fail_closed(
    tmp_path: Path,
    tool_id: str,
    kind: str,
    signal: str | None,
    fixture: dict,
    error: str,
) -> None:
    _, _, process = run_worker(tmp_path, tool_id, kind, signal, fixture)
    assert process.returncode == 1
    assert json.loads(process.stdout)["error_code"] == error
    assert all(secret not in process.stdout + process.stderr for secret in SECRETS)


def test_request_models_reject_unbound_resources_raw_values_and_wrong_signal_shapes() -> None:
    cases = [
        evaluation_arguments() | {"resource_id": "LangSmith:other"},
        observation_arguments("OpenTelemetry", "trace") | {"duration_nanoseconds": 0},
        observation_arguments("OpenTelemetry", "metric") | {"metric_value": None},
        observation_arguments("OpenTelemetry", "log") | {"metric_value": 1.0},
        observation_arguments("Langfuse", "score") | {"duration_nanoseconds": 100},
        observation_arguments("Grafana", "trace_link") | {"dashboard_uid": "other"},
        observation_arguments("OpenTelemetry", "log")
        | {"attribute_sha256": {"prompt": "d" * 64}},
    ]
    # A differently named LangSmith resource is syntactically valid at the model
    # boundary and is rejected against the selected connector configuration.
    EvaluationFeedbackRequest.model_validate(cases.pop(0))
    for value in cases:
        with pytest.raises(ValidationError):
            ObservabilityExportRequest.model_validate(value)


def test_endpoints_and_otlp_headers_are_origin_bound_and_hop_headers_are_rejected() -> None:
    assert endpoint("https://Observe.Example.Test/") == ENDPOINT
    assert parse_otlp_headers("Authorization=Bearer%20token,x-tenant-id=tenant") == {
        "authorization": "Bearer token",
        "x-tenant-id": "tenant",
    }
    for value in (
        "http://observe.example.test",
        "https://observe.example.test/v1/traces",
        "https://user:secret@observe.example.test",
        "https://observe.example.test:8443",
    ):
        with pytest.raises(LaneError):
            endpoint(value)
    for value in (
        "Host=escape.example",
        "Content-Length=3",
        "Authorization=Bearer%0D%0AInjected:value",
        "missing-equals",
    ):
        with pytest.raises(LaneError):
            parse_otlp_headers(value)
