"""Controlled HTTPX transport for the four evidence-delivery backends."""

from __future__ import annotations

import base64
import gzip
import json
import time
from pathlib import Path

import httpx

ENDPOINT = "https://observe.example.test"
WORKSPACE = "workspace-one"
PUBLIC_KEY = "pk-lf-test-public"
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
RUN_ID = "00000000-0000-4000-8000-000000000001"
SESSION_ID = "00000000-0000-4000-8000-000000000002"
ENVIRONMENT = {
    "LANGSMITH_API_KEY": "lsv2_test-secret-0123456789",
    "LANGSMITH_ENDPOINT": ENDPOINT,
    "LANGSMITH_WORKSPACE_ID": WORKSPACE,
    "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
    "OTEL_EXPORTER_OTLP_HEADERS": (
        "Authorization=Bearer%20otel-test-secret-0123456789,x-tenant-id=tenant-one"
    ),
    "LANGFUSE_BASE_URL": ENDPOINT,
    "LANGFUSE_PUBLIC_KEY": PUBLIC_KEY,
    "LANGFUSE_SECRET_KEY": "sk-lf-test-secret-0123456789",
    "GRAFANA_URL": ENDPOINT,
    "GRAFANA_SERVICE_ACCOUNT_TOKEN": "grafana-test-secret-0123456789",
}
CONFIG_KEYS = {
    "LangSmith": [
        "LANGSMITH_API_KEY",
        "LANGSMITH_ENDPOINT",
        "LANGSMITH_WORKSPACE_ID",
    ],
    "OpenTelemetry": ["OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS"],
    "Langfuse": ["LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"],
    "Grafana": ["GRAFANA_SERVICE_ACCOUNT_TOKEN", "GRAFANA_URL"],
}
RESOURCES = {
    "LangSmith": "LangSmith:" + WORKSPACE,
    "OpenTelemetry": "OpenTelemetry:tenant-one",
    "Langfuse": "Langfuse:" + PUBLIC_KEY,
    "Grafana": "Grafana:dashboard-one",
}
BACKENDS = {
    "LangSmith": ("langsmith.feedback-rest", "v1"),
    "OpenTelemetry": ("opentelemetry.otlp-http-json", "1.11"),
    "Langfuse": ("langfuse.otlp-http", "v4"),
    "Grafana": ("grafana.annotations-rest", "v1"),
}
SECRETS = (
    ENVIRONMENT["LANGSMITH_API_KEY"],
    "otel-test-secret-0123456789",
    ENVIRONMENT["LANGFUSE_SECRET_KEY"],
    ENVIRONMENT["GRAFANA_SERVICE_ACCOUNT_TOKEN"],
)


class ExportTransport:
    def __init__(self, tool_id: str, kind: str, signal: str | None, configuration=None):
        self.tool_id = tool_id
        self.kind = kind
        self.signal = signal
        self.configuration = configuration or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if marker := self.configuration.get("marker"):
            Path(marker).write_text(str(len(self.requests)), encoding="utf-8")
        if delay := self.configuration.get("delay_seconds"):
            time.sleep(delay)
        assert request.method == "POST"
        assert request.url.scheme == "https"
        assert request.url.host == "observe.example.test"
        assert request.headers["Content-Type"] == "application/json"
        raw = request.read()
        body = json.loads(raw)
        serialized = json.dumps(body)
        assert "raw prompt" not in serialized and "raw response" not in serialized

        if self.tool_id == "LangSmith":
            assert request.url.path == "/api/v1/feedback"
            assert request.headers["X-Api-Key"] == ENVIRONMENT["LANGSMITH_API_KEY"]
            assert request.headers["X-Tenant-Id"] == WORKSPACE
            assert body["run_id"] == RUN_ID and body["session_id"] == SESSION_ID
            assert body["score"] == 0.75 and body["extend_trace_retention"] is False
            assert body["feedback_source"]["type"] == "api"
            response: object = {}
        elif self.tool_id == "OpenTelemetry":
            expected = {
                "trace": ("/v1/traces", "resourceSpans"),
                "metric": ("/v1/metrics", "resourceMetrics"),
                "log": ("/v1/logs", "resourceLogs"),
            }[str(self.signal)]
            assert (request.url.path, next(iter(body))) == expected
            assert request.headers["Authorization"] == "Bearer otel-test-secret-0123456789"
            assert request.headers["x-tenant-id"] == "tenant-one"
            assert "input_sha256" in serialized and HASH_A in serialized
            response = {}
        elif self.tool_id == "Langfuse":
            assert request.url.path == "/api/public/otel/v1/traces"
            kind, encoded = request.headers["Authorization"].split(" ", 1)
            assert kind == "Basic"
            assert base64.b64decode(encoded).decode() == (
                PUBLIC_KEY + ":" + ENVIRONMENT["LANGFUSE_SECRET_KEY"]
            )
            assert request.headers["x-langfuse-ingestion-version"] == "4"
            assert next(iter(body)) == "resourceSpans"
            assert f'"stringValue": "{self.signal}"' in json.dumps(body, sort_keys=True)
            response = {}
        else:
            assert request.url.path == "/api/annotations"
            assert request.headers["Authorization"] == (
                "Bearer " + ENVIRONMENT["GRAFANA_SERVICE_ACCOUNT_TOKEN"]
            )
            assert body["dashboardUID"] == "dashboard-one"
            assert body["text"].startswith("Evidence Lane sample_action result ")
            assert "signal:" + str(self.signal) in body["tags"]
            response = {"id": 17, "message": "Annotation added"}

        if self.configuration.get("partial_success"):
            response = {"partialSuccess": {"rejectedSpans": "1", "errorMessage": "fixture"}}
        if self.configuration.get("provider_failure"):
            response = (
                {"id": 0, "message": "not added"}
                if self.tool_id == "Grafana"
                else {"error": "fixture"}
                if self.tool_id == "LangSmith"
                else {"partialSuccess": {"rejectedSpans": "1"}}
            )
        if self.configuration.get("reflect_secret"):
            response = {"message": SECRETS[0]}
        content = json.dumps(response).encode()
        if self.configuration.get("invalid_json"):
            content = b"not-json"
        if self.configuration.get("oversize"):
            content += b" " * 5000
        status = int(self.configuration.get("status", 200))
        headers = {"Content-Type": "application/json"}
        if self.configuration.get("content_length"):
            headers["Content-Length"] = str(len(content))
        if self.configuration.get("content_encoding"):
            headers["Content-Encoding"] = str(self.configuration["content_encoding"])
            if self.configuration["content_encoding"] == "gzip":
                content = gzip.compress(content)
        if 300 <= status < 400:
            headers["Location"] = "https://other.invalid/escape"
        return httpx.Response(status, headers=headers, content=content, request=request)
