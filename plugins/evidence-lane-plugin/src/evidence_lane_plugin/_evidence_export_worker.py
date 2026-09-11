"""Owned one-request HTTPX worker for hash-only evidence delivery."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5


def _string_attribute(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _attributes(request: Any, project_id: str, task_id: str) -> list[dict[str, Any]]:
    values = {
        "evidence_lane.project_sha256": _digest(project_id),
        "evidence_lane.task_sha256": _digest(task_id),
        "evidence_lane.lane": request.lane_id,
        "evidence_lane.resource_sha256": _digest(request.resource_id),
        "evidence_lane.action": request.action_name,
        "evidence_lane.input_sha256": request.input_sha256,
        "evidence_lane.result_sha256": request.result_sha256,
        "evidence_lane.redaction_receipt_sha256": request.redaction_receipt_sha256,
        "evidence_lane.correlation_sha256": _digest(request.correlation_id),
    }
    values.update(
        {
            "evidence_lane.attribute." + key: value
            for key, value in request.attribute_sha256.items()
        }
    )
    if request.tool_id == "Langfuse":
        values.update(
            {
                "langfuse.observation.type": request.signal,
                "langfuse.trace.name": request.action_name,
                "langfuse.trace.metadata.evidence_lane_project_sha256": _digest(project_id),
                "langfuse.observation.metadata.evidence_lane_result_sha256": request.result_sha256,
            }
        )
    return [_string_attribute(key, value) for key, value in sorted(values.items())]


def _resource(request: Any, project_id: str, task_id: str) -> dict[str, Any]:
    from evidence_lane_plugin.external_evidence_delivery import resource_scope

    scope = resource_scope(request.tool_id, request.resource_id)
    if scope is None:
        raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
    return {
        "attributes": [
            _string_attribute("service.name", "evidence-lane"),
            _string_attribute("service.namespace", _digest(scope)),
            _string_attribute("evidence_lane.project_sha256", _digest(project_id)),
            _string_attribute("evidence_lane.task_sha256", _digest(task_id)),
        ]
    }


def _trace_payload(request: Any, project_id: str, task_id: str, binding: str) -> dict[str, Any]:
    end = request.observed_at_unix_nano
    start = end - request.duration_nanoseconds
    if start < 0:
        raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
    trace_id = hashlib.sha256((binding + ":trace").encode()).hexdigest()[:32]
    span_id = hashlib.sha256((binding + ":span").encode()).hexdigest()[:16]
    return {
        "resourceSpans": [
            {
                "resource": _resource(request, project_id, task_id),
                "scopeSpans": [
                    {
                        "scope": {"name": "evidence-lane", "version": "4"},
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": request.action_name,
                                "kind": 1,
                                "startTimeUnixNano": str(start),
                                "endTimeUnixNano": str(end),
                                "attributes": _attributes(request, project_id, task_id),
                                "status": {"code": 1},
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _metric_payload(request: Any, project_id: str, task_id: str) -> dict[str, Any]:
    if request.metric_value is None:
        raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
    return {
        "resourceMetrics": [
            {
                "resource": _resource(request, project_id, task_id),
                "scopeMetrics": [
                    {
                        "scope": {"name": "evidence-lane", "version": "4"},
                        "metrics": [
                            {
                                "name": "evidence_lane." + request.action_name,
                                "unit": "1",
                                "gauge": {
                                    "dataPoints": [
                                        {
                                            "attributes": _attributes(request, project_id, task_id),
                                            "timeUnixNano": str(request.observed_at_unix_nano),
                                            "asDouble": request.metric_value,
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _log_payload(request: Any, project_id: str, task_id: str) -> dict[str, Any]:
    binding = request.result_sha256[:16]
    return {
        "resourceLogs": [
            {
                "resource": _resource(request, project_id, task_id),
                "scopeLogs": [
                    {
                        "scope": {"name": "evidence-lane", "version": "4"},
                        "logRecords": [
                            {
                                "timeUnixNano": str(request.observed_at_unix_nano),
                                "observedTimeUnixNano": str(request.observed_at_unix_nano),
                                "severityNumber": 9,
                                "severityText": "INFO",
                                "body": {
                                    "stringValue": "Evidence Lane result " + binding
                                },
                                "attributes": _attributes(request, project_id, task_id),
                            }
                        ],
                    }
                ],
            }
        ]
    }


def _feedback_payload(
    request: Any,
    project_id: str,
    task_id: str,
    binding: str,
) -> tuple[dict[str, Any], str]:
    feedback_id = str(uuid5(NAMESPACE_URL, "evidence-lane:" + binding))
    observed = datetime.fromtimestamp(
        request.observed_at_unix_nano / 1_000_000_000,
        UTC,
    ).isoformat()
    payload = {
        "id": feedback_id,
        "run_id": request.run_id,
        "session_id": request.session_id,
        "key": request.feedback_key,
        "score": request.score,
        "feedback_source": {
            "type": "api",
            "metadata": {
                "evidence_lane_project_sha256": _digest(project_id),
                "evidence_lane_task_sha256": _digest(task_id),
                "evidence_lane_lane": request.lane_id,
                "evidence_lane_action": request.action_name,
                "action_schema_sha256": request.action_schema_sha256,
                "route_sha256": request.route_sha256,
                "dataset_sha256": request.dataset_sha256,
                "redaction_receipt_sha256": request.redaction_receipt_sha256,
            },
        },
        "created_at": observed,
        "modified_at": observed,
        "extend_trace_retention": False,
    }
    return payload, feedback_id


def _annotation_payload(request: Any, project_id: str, task_id: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "time": request.observed_at_unix_nano // 1_000_000,
        "tags": [
            "evidence-lane",
            "lane:" + request.lane_id,
            "signal:" + request.signal,
            "project:" + _digest(project_id)[:16],
            "task:" + _digest(task_id)[:16],
        ],
        "text": (
            "Evidence Lane "
            + request.action_name
            + " result "
            + request.result_sha256[:16]
        ),
    }
    if request.dashboard_uid is not None:
        payload["dashboardUID"] = request.dashboard_uid
    return payload


def _request_parts(
    request: Any,
    kind: str,
    project_id: str,
    task_id: str,
    binding: str,
    configuration: dict[str, str],
) -> tuple[str, dict[str, str], bytes, str | None]:
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
    }
    feedback_id = None
    if kind == "evaluation":
        path = "/api/v1/feedback"
        headers.update(
            {
                "X-Api-Key": configuration["LANGSMITH_API_KEY"],
                "X-Tenant-Id": configuration["LANGSMITH_WORKSPACE_ID"],
            }
        )
        payload, feedback_id = _feedback_payload(request, project_id, task_id, binding)
    elif request.tool_id == "OpenTelemetry":
        headers.update(_configured_otlp_headers(configuration))
        if request.signal == "trace":
            path = "/v1/traces"
            payload = _trace_payload(request, project_id, task_id, binding)
        elif request.signal == "metric":
            path = "/v1/metrics"
            payload = _metric_payload(request, project_id, task_id)
        else:
            path = "/v1/logs"
            payload = _log_payload(request, project_id, task_id)
    elif request.tool_id == "Langfuse":
        path = "/api/public/otel/v1/traces"
        credential = base64.b64encode(
            (
                configuration["LANGFUSE_PUBLIC_KEY"]
                + ":"
                + configuration["LANGFUSE_SECRET_KEY"]
            ).encode()
        ).decode()
        headers.update(
            {
                "Authorization": "Basic " + credential,
                "x-langfuse-ingestion-version": "4",
            }
        )
        payload = _trace_payload(request, project_id, task_id, binding)
    else:
        path = "/api/annotations"
        headers["Authorization"] = (
            "Bearer " + configuration["GRAFANA_SERVICE_ACCOUNT_TOKEN"]
        )
        payload = _annotation_payload(request, project_id, task_id)
    content = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode()
    return path, headers, content, feedback_id


def _configured_otlp_headers(configuration: dict[str, str]) -> dict[str, str]:
    from evidence_lane_plugin.external_evidence_delivery import parse_otlp_headers

    return parse_otlp_headers(configuration["OTEL_EXPORTER_OTLP_HEADERS"])


def _provider_result(
    request: Any,
    kind: str,
    response: dict[str, Any],
    feedback_id: str | None,
) -> dict[str, Any]:
    if kind == "evaluation":
        if (
            not isinstance(response, dict)
            or response.get("error")
            or response.get("detail")
            or response.get("id") not in {None, feedback_id}
            or feedback_id is None
        ):
            raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
        return {
            "accepted": True,
            "feedback_id": feedback_id,
            "run_id": request.run_id,
            "session_id": request.session_id,
            "key": request.feedback_key,
            "score": request.score,
        }
    if request.tool_id in {"OpenTelemetry", "Langfuse"}:
        if not isinstance(response, dict) or any(
            key in response for key in ("partialSuccess", "partial_success")
        ):
            raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
        return {"accepted": True, "partial_success": False, "rejected_count": 0}
    if (
        not isinstance(response, dict)
        or set(response) != {"id", "message"}
        or type(response["id"]) is not int
        or response["id"] <= 0
        or response["message"] != "Annotation added"
    ):
        raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
    return {"annotation_id": response["id"], "message": response["message"]}


def execute(
    request: Any,
    *,
    kind: str,
    project_id: str,
    task_id: str,
    endpoint: str,
    configuration: dict[str, str],
    expires_at: str,
) -> dict[str, Any]:
    import httpx

    from evidence_lane_plugin.external_evidence_delivery import (
        EXPORT_BACKENDS,
        evidence_binding,
        secret_values,
        verify_resource_configuration,
    )
    from evidence_lane_plugin.external_evidence_delivery import (
        endpoint as normalize_endpoint,
    )
    from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes

    if normalize_endpoint(endpoint) != endpoint:
        raise ValueError("EVIDENCE_EXPORT_ENDPOINT_CHANGED")
    expected = set(EXPORT_BACKENDS[request.tool_id]["config"])
    endpoint_key = {
        "LangSmith": "LANGSMITH_ENDPOINT",
        "OpenTelemetry": "OTEL_EXPORTER_OTLP_ENDPOINT",
        "Langfuse": "LANGFUSE_BASE_URL",
        "Grafana": "GRAFANA_URL",
    }[request.tool_id]
    expected.remove(endpoint_key)
    if set(configuration) != expected:
        raise ValueError("EVIDENCE_EXPORT_PROVIDER_FAILURE")
    verify_resource_configuration(request.tool_id, request.resource_id, configuration)
    expiry = datetime.fromisoformat(expires_at) if expires_at != "NO_EXPIRY" else None
    if expiry is not None and (expiry.tzinfo is None or datetime.now(UTC) >= expiry):
        raise ValueError("EVIDENCE_EXPORT_GRANT_EXPIRED")
    binding = evidence_binding(
        request,
        kind=kind,
        project_id=project_id,
        task_id=task_id,
    )
    path, headers, content, feedback_id = _request_parts(
        request,
        kind,
        project_id,
        task_id,
        binding,
        configuration,
    )
    if len(content) > request.max_request_bytes:
        raise ValueError("EVIDENCE_EXPORT_REQUEST_BUDGET")
    with (
        httpx.Client(
            base_url=endpoint,
            verify=True,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(min(5.0, request.timeout_seconds)),
        ) as client,
        client.stream("POST", path, headers=headers, content=content) as response,
    ):
        if response.status_code != 200 or response.is_redirect:
            raise ValueError("EVIDENCE_EXPORT_HTTP_STATUS")
        content_encoding = response.headers.get("Content-Encoding", "identity").casefold()
        if content_encoding not in {"", "identity"}:
            raise ValueError("EVIDENCE_EXPORT_RESPONSE_INVALID")
        length = response.headers.get("Content-Length")
        if length is not None and (
            not length.isascii()
            or not length.isdigit()
            or int(length) > request.max_response_bytes
        ):
            raise ValueError("EVIDENCE_EXPORT_RESPONSE_BUDGET")
        response_type = (
            response.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
        )
        raw = bytearray()
        for chunk in response.iter_bytes(65_536):
            if len(raw) + len(chunk) > request.max_response_bytes:
                raise ValueError("EVIDENCE_EXPORT_RESPONSE_BUDGET")
            raw.extend(chunk)
        if response_type != "application/json" and (response_type or raw):
            raise ValueError("EVIDENCE_EXPORT_RESPONSE_INVALID")
        if not raw:
            decoded: dict[str, Any] = {}
        else:
            try:
                decoded = json.loads(raw)
            except (ValueError, UnicodeError):
                raise ValueError("EVIDENCE_EXPORT_RESPONSE_INVALID") from None
        provider = _provider_result(request, kind, decoded, feedback_id)
        observation = {
            "method": "POST",
            "path_sha256": sha256_bytes(path.encode()).lower(),
            "request_bytes": len(content),
            "request_sha256": sha256_bytes(content).lower(),
            "response_bytes": len(raw),
            "response_sha256": sha256_bytes(bytes(raw)).lower(),
            "status_code": response.status_code,
            "endpoint_sha256": sha256_bytes(endpoint.encode()).lower(),
            "content_type": "application/json",
        }
    result = {
        "schema": "evidence-lane.external-evidence-export.v4",
        "status": "PASS",
        "kind": kind,
        "tool_id": request.tool_id,
        "project_id": project_id,
        "task_id": task_id,
        "lane_id": request.lane_id,
        "resource_id": request.resource_id,
        "evidence_binding_sha256": binding,
        "provider_result": provider,
        "request_observation": observation,
        "raw_prompt_exported": False,
        "raw_response_exported": False,
        "raw_source_exported": False,
        "hil_token_exported": False,
        "project_authority_mutated": False,
        "remote_receipt_is_project_authority": False,
    }
    encoded = canonical_json_bytes(result)
    if any(value.encode() in encoded for value in secret_values(request.tool_id, configuration)):
        raise ValueError("EVIDENCE_EXPORT_RESPONSE_INVALID")
    return result


def main() -> None:
    request_path = Path.cwd() / "request.json"
    with request_path.open("rb") as stream:
        raw = stream.read(262_145)
    if (
        len(sys.argv) != 2
        or len(raw) > 262_144
        or hashlib.sha256(raw).hexdigest() != sys.argv[1]
    ):
        raise ValueError("EVIDENCE_EXPORT_REQUEST_BINDING_INVALID")
    body = json.loads(raw)
    if set(body) != {
        "schema",
        "kind",
        "action",
        "arguments",
        "project_id",
        "task_id",
        "expires_at",
        "programs",
    }:
        raise ValueError("EVIDENCE_EXPORT_REQUEST_INVALID")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evidence_lane_plugin.evaluation_toolchain import (
        EVALUATION_FEEDBACK_EXPORT,
        EvaluationFeedbackRequest,
    )
    from evidence_lane_plugin.external_evidence_delivery import programs
    from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
    from evidence_lane_plugin.observability_toolchain import (
        OBSERVABILITY_EXPORT,
        ObservabilityExportRequest,
    )

    current_programs = programs()
    if (
        body["schema"] != "evidence-lane.external-evidence-request.v4"
        or body["programs"] != current_programs
        or body["kind"] not in {"evaluation", "observability"}
        or body["action"]
        != (
            EVALUATION_FEEDBACK_EXPORT
            if body["kind"] == "evaluation"
            else OBSERVABILITY_EXPORT
        )
        or not isinstance(body["project_id"], str)
        or not 1 <= len(body["project_id"]) <= 128
        or not isinstance(body["task_id"], str)
        or not 1 <= len(body["task_id"]) <= 128
        or any(
            ord(character) < 32
            for value in (body["project_id"], body["task_id"])
            for character in value
        )
    ):
        raise ValueError("EVIDENCE_EXPORT_PROGRAM_BINDING_INVALID")
    model = (
        EvaluationFeedbackRequest
        if body["kind"] == "evaluation"
        else ObservabilityExportRequest
    )
    request = model.model_validate(body["arguments"])
    endpoint = os.environ.pop("_EVI_EXPORT_ENDPOINT", "")
    configuration = json.loads(os.environ.pop("_EVI_EXPORT_CONFIG", ""))
    result = execute(
        request,
        kind=body["kind"],
        project_id=body["project_id"],
        task_id=body["task_id"],
        endpoint=endpoint,
        configuration=configuration,
        expires_at=body["expires_at"],
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "request_sha256": sys.argv[1],
                "program_sha256": sha256_bytes(
                    canonical_json_bytes(current_programs)
                ).lower(),
                "result": result,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        main()
    except Exception as error:  # noqa: BLE001 - vendor text and credentials stay private
        code = str(error)
        if code not in {
            "EVIDENCE_EXPORT_HTTP_STATUS",
            "EVIDENCE_EXPORT_REQUEST_BUDGET",
            "EVIDENCE_EXPORT_RESPONSE_BUDGET",
            "EVIDENCE_EXPORT_PROVIDER_FAILURE",
            "EVIDENCE_EXPORT_GRANT_EXPIRED",
            "EVIDENCE_EXPORT_ENDPOINT_CHANGED",
            "EVIDENCE_EXPORT_RESPONSE_INVALID",
        }:
            code = "EVIDENCE_EXPORT_WORKER_FAILED"
        print(json.dumps({"status": "error", "error_code": code}))
        raise SystemExit(1) from None
