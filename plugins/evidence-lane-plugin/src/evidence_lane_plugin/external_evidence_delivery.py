"""Exact connector and owned-process boundary for optional evidence exports."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from uuid import NAMESPACE_URL, uuid5

from pydantic import Field

from .errors import LaneError
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .registry import Contract

EXPORT_BACKENDS: dict[str, dict[str, Any]] = {
    "LangSmith": {
        "backend_id": "langsmith.feedback-rest",
        "version": "v1",
        "config": (
            "LANGSMITH_API_KEY",
            "LANGSMITH_ENDPOINT",
            "LANGSMITH_WORKSPACE_ID",
        ),
    },
    "OpenTelemetry": {
        "backend_id": "opentelemetry.otlp-http-json",
        "version": "1.11",
        "config": ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS"),
    },
    "Langfuse": {
        "backend_id": "langfuse.otlp-http",
        "version": "v4",
        "config": (
            "LANGFUSE_BASE_URL",
            "LANGFUSE_PUBLIC_KEY",
            "LANGFUSE_SECRET_KEY",
        ),
    },
    "Grafana": {
        "backend_id": "grafana.annotations-rest",
        "version": "v1",
        "config": ("GRAFANA_SERVICE_ACCOUNT_TOKEN", "GRAFANA_URL"),
    },
}

_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
_RESOURCE_SCOPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/+~-]{0,255}")
_FORBIDDEN_HEADERS = {
    "accept",
    "accept-encoding",
    "connection",
    "content-length",
    "content-type",
    "host",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class ExternalEvidenceResult(Contract):
    tool_id: Literal["LangSmith", "OpenTelemetry", "Langfuse", "Grafana"]
    kind: Literal["evaluation", "observability"]
    delivery: dict
    receipt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def endpoint(value: str) -> str:
    """Normalize one credential-free HTTPS origin without an operation path."""

    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.port not in {None, 443}
        or ":" in parsed.hostname
        or len(value) > 2048
        or any(character.isspace() for character in value)
    ):
        raise LaneError(
            "EVIDENCE_EXPORT_ENDPOINT_INVALID",
            "Configure one credential-free HTTPS service origin.",
        )
    return "https://" + parsed.hostname.casefold()


def parse_otlp_headers(value: str) -> dict[str, str]:
    """Parse the OTLP comma-separated header setting without forwarding hop headers."""

    result: dict[str, str] = {}
    if not value or len(value) > 8192:
        raise LaneError(
            "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
            "The configured OTLP headers are unavailable or invalid.",
        )
    for item in value.split(","):
        if "=" not in item:
            raise LaneError(
                "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
                "The configured OTLP headers are unavailable or invalid.",
            )
        raw_name, raw_value = item.split("=", 1)
        name = unquote(raw_name).strip()
        header_value = unquote(raw_value).strip()
        folded = name.casefold()
        if (
            _HEADER_NAME.fullmatch(name) is None
            or folded in _FORBIDDEN_HEADERS
            or folded in result
            or not header_value
            or len(header_value) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in header_value)
        ):
            raise LaneError(
                "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
                "The configured OTLP headers are unavailable or invalid.",
            )
        result[folded] = header_value
    if not 1 <= len(result) <= 16:
        raise LaneError(
            "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
            "The configured OTLP headers are unavailable or invalid.",
        )
    return result


def resource_scope(tool_id: str, value: str) -> str | None:
    try:
        prefix, scope = value.split(":", 1)
    except ValueError:
        return None
    if (
        prefix != tool_id
        or _RESOURCE_SCOPE.fullmatch(scope) is None
        or ".." in scope
        or "\\" in scope
    ):
        return None
    return scope


def configuration(tool_id: str, registration: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    expected = tuple(sorted(EXPORT_BACKENDS[tool_id]["config"]))
    keys = tuple(sorted(registration.get("config_env_keys", [])))
    if keys != expected:
        raise LaneError(
            "EVIDENCE_EXPORT_CONFIGURATION_REQUIRED",
            "Configure the exact environment references required by this export adapter.",
        )
    values = {key: os.environ.get(key, "") for key in keys}
    if any(
        not value
        or len(value) > 8192
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        for value in values.values()
    ):
        raise LaneError(
            "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
            "A configured export value is unavailable or invalid.",
        )
    endpoint_key = {
        "LangSmith": "LANGSMITH_ENDPOINT",
        "OpenTelemetry": "OTEL_EXPORTER_OTLP_ENDPOINT",
        "Langfuse": "LANGFUSE_BASE_URL",
        "Grafana": "GRAFANA_URL",
    }[tool_id]
    origin = endpoint(values.pop(endpoint_key))
    if tool_id == "OpenTelemetry":
        parse_otlp_headers(values["OTEL_EXPORTER_OTLP_HEADERS"])
    elif tool_id == "LangSmith":
        if not 3 <= len(values["LANGSMITH_WORKSPACE_ID"]) <= 256:
            raise LaneError(
                "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
                "The configured LangSmith workspace identity is invalid.",
            )
    elif tool_id == "Langfuse" and not 3 <= len(values["LANGFUSE_PUBLIC_KEY"]) <= 256:
        raise LaneError(
            "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
            "The configured Langfuse project identity is invalid.",
        )
    if tool_id in {"LangSmith", "Langfuse", "Grafana"} and any(
        len(value) < 16 for value in secret_values(tool_id, values)
    ):
        raise LaneError(
            "EVIDENCE_EXPORT_CONFIGURATION_UNAVAILABLE",
            "The configured export credential is invalid.",
        )
    return origin, values


def secret_values(tool_id: str, values: Mapping[str, str]) -> tuple[str, ...]:
    if tool_id == "LangSmith":
        return (values["LANGSMITH_API_KEY"],)
    if tool_id == "Langfuse":
        return (values["LANGFUSE_SECRET_KEY"],)
    if tool_id == "Grafana":
        return (values["GRAFANA_SERVICE_ACCOUNT_TOKEN"],)
    headers = parse_otlp_headers(values["OTEL_EXPORTER_OTLP_HEADERS"])
    return tuple(
        value
        for name, value in headers.items()
        if any(part in name for part in ("authorization", "api-key", "apikey", "secret", "token"))
        or value.casefold().startswith(("bearer ", "basic "))
    )


def verify_resource_configuration(tool_id: str, resource_id: str, values: Mapping[str, str]) -> None:
    scope = resource_scope(tool_id, resource_id)
    if scope is None:
        raise LaneError(
            "EVIDENCE_EXPORT_RESOURCE_INVALID",
            "Select one canonical provider-prefixed export resource.",
        )
    expected = (
        values["LANGSMITH_WORKSPACE_ID"]
        if tool_id == "LangSmith"
        else values["LANGFUSE_PUBLIC_KEY"]
        if tool_id == "Langfuse"
        else None
    )
    if expected is not None and scope != expected:
        raise LaneError(
            "EVIDENCE_EXPORT_RESOURCE_CONFIGURATION_MISMATCH",
            "The selected connector resource does not match its configured project identity.",
        )


def readiness(tool_id: str):
    def check(context: Any, registration: Mapping[str, Any]) -> dict[str, Any]:
        del context
        try:
            version = importlib.metadata.version("httpx")
        except importlib.metadata.PackageNotFoundError:
            version = None
        if version != "0.28.1":
            return {
                "ready": False,
                "backend_version": EXPORT_BACKENDS[tool_id]["version"],
            }
        origin, values = configuration(tool_id, registration)
        import httpx

        with httpx.Client(
            base_url=origin,
            verify=True,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(5),
        ):
            pass
        return {
            "ready": bool(values),
            "backend_version": EXPORT_BACKENDS[tool_id]["version"],
            "basis": "exact_config_references_and_httpx_constructor",
            "service_contacted": False,
        }

    return check


def programs() -> dict[str, str]:
    return {
        name: sha256_file(Path(__file__).with_name(name)).lower()
        for name in (
            "external_evidence_delivery.py",
            "_evidence_export_worker.py",
            "evaluation_toolchain.py",
            "observability_toolchain.py",
            "bounded_io.py",
            "_bounded_process_child.py",
        )
    }


def evidence_binding(
    request: Any,
    *,
    kind: str,
    project_id: str,
    task_id: str,
) -> str:
    body = {
        "kind": kind,
        "project_id": project_id,
        "task_id": task_id,
        "arguments": request.model_dump(mode="json"),
    }
    return sha256_bytes(canonical_json_bytes(body)).lower()


def validate_result(
    value: dict[str, Any],
    request: Any,
    *,
    kind: str,
    project_id: str,
    task_id: str,
) -> None:
    try:
        required = {
            "schema",
            "status",
            "kind",
            "tool_id",
            "project_id",
            "task_id",
            "lane_id",
            "resource_id",
            "evidence_binding_sha256",
            "provider_result",
            "request_observation",
            "raw_prompt_exported",
            "raw_response_exported",
            "raw_source_exported",
            "hil_token_exported",
            "project_authority_mutated",
            "remote_receipt_is_project_authority",
        }
        if (
            not isinstance(value, dict)
            or set(value) != required
            or value["schema"] != "evidence-lane.external-evidence-export.v4"
            or value["status"] != "PASS"
            or value["kind"] != kind
            or value["tool_id"] != request.tool_id
            or value["project_id"] != project_id
            or value["task_id"] != task_id
            or value["lane_id"] != request.lane_id
            or value["resource_id"] != request.resource_id
            or value["evidence_binding_sha256"]
            != evidence_binding(
                request,
                kind=kind,
                project_id=project_id,
                task_id=task_id,
            )
            or any(
                value[key] is not False
                for key in (
                    "raw_prompt_exported",
                    "raw_response_exported",
                    "raw_source_exported",
                    "hil_token_exported",
                    "project_authority_mutated",
                    "remote_receipt_is_project_authority",
                )
            )
        ):
            raise ValueError
        observation = value["request_observation"]
        if (
            not isinstance(observation, dict)
            or set(observation)
            != {
                "method",
                "path_sha256",
                "request_bytes",
                "request_sha256",
                "response_bytes",
                "response_sha256",
                "status_code",
                "endpoint_sha256",
                "content_type",
            }
            or observation["method"] != "POST"
            or observation["status_code"] != 200
            or observation["content_type"] != "application/json"
            or any(
                type(observation[key]) is not int or observation[key] < 0
                for key in ("request_bytes", "response_bytes")
            )
            or observation["request_bytes"] > request.max_request_bytes
            or observation["response_bytes"] > request.max_response_bytes
            or any(
                not isinstance(observation[key], str)
                or _LOWER_SHA256.fullmatch(observation[key]) is None
                for key in (
                    "path_sha256",
                    "request_sha256",
                    "response_sha256",
                    "endpoint_sha256",
                )
            )
        ):
            raise ValueError
        result = value["provider_result"]
        if kind == "evaluation":
            expected_feedback_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "evidence-lane:"
                    + evidence_binding(
                        request,
                        kind=kind,
                        project_id=project_id,
                        task_id=task_id,
                    ),
                )
            )
            if (
                not isinstance(result, dict)
                or set(result)
                != {"accepted", "feedback_id", "run_id", "session_id", "key", "score"}
                or result["accepted"] is not True
                or result["run_id"] != request.run_id
                or result["session_id"] != request.session_id
                or result["key"] != request.feedback_key
                or result["score"] != request.score
                or result["feedback_id"] != expected_feedback_id
            ):
                raise ValueError
        elif request.tool_id in {"OpenTelemetry", "Langfuse"}:
            if (
                not isinstance(result, dict)
                or set(result) != {"accepted", "partial_success", "rejected_count"}
                or result["accepted"] is not True
                or result["partial_success"] is not False
                or result["rejected_count"] != 0
            ):
                raise ValueError
        elif (
            not isinstance(result, dict)
            or set(result) != {"annotation_id", "message"}
            or type(result["annotation_id"]) is not int
            or result["annotation_id"] <= 0
            or result["message"] != "Annotation added"
        ):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise LaneError(
            "EVIDENCE_EXPORT_RESULT_INVALID",
            "The export worker returned an invalid complete result.",
        ) from None


def execute(engine: Any, context: Any, request: Any, *, kind: str, action: str) -> ExternalEvidenceResult:
    from .bounded_io import run_owned_bounded_process
    from .connector_governance import connector_service
    from .tool_routes import routes_for

    if context.execution is None or context.execution.guard is None:
        raise LaneError("DELTA_REQUIRED", "Evidence export requires the current project Delta.")
    context.execution.check()
    spec = engine.registry.get(action)
    route = next(
        (
            row
            for row in routes_for(spec)
            if dict(row.argument_values).get("tool_id") == (request.tool_id,)
        ),
        None,
    )
    if route is None:
        raise LaneError(
            "EVIDENCE_EXPORT_ROUTE_INVALID",
            "The selected export provider has no exact registered route.",
        )
    admission = context.tool_admission
    if not isinstance(admission, dict) or not isinstance(admission.get("extension"), dict):
        raise LaneError(
            "EVIDENCE_EXPORT_ADMISSION_REQUIRED",
            "Use the exact admitted export connector operation.",
        )
    proof = engine.registry.tool_router.extensions.authorize(
        spec,
        route,
        context,
        request,
        admission["extension"],
    )
    service = connector_service(engine, context.execution.store)
    registration = next(
        (
            row
            for row in service.active_catalog()
            if row["plugin_id"] == proof["plugin_id"]
            and row["version"] == proof["registration_version"]
            and row["digest"] == proof["registration_digest"]
        ),
        None,
    )
    if registration is None:
        raise LaneError("PLUGIN_VERSION_CONFLICT", "The admitted export registration changed.")
    origin, config = configuration(request.tool_id, registration)
    verify_resource_configuration(request.tool_id, request.resource_id, config)
    context.execution.check()
    code = programs()
    project_id = context.project_id
    task_id = context.execution.task_id
    if not isinstance(project_id, str) or not isinstance(task_id, str):
        raise LaneError("DELTA_REQUIRED", "Evidence export requires exact project and task identity.")
    body = {
        "schema": "evidence-lane.external-evidence-request.v4",
        "kind": kind,
        "action": action,
        "arguments": request.model_dump(mode="json"),
        "project_id": project_id,
        "task_id": task_id,
        "expires_at": proof["expires_at"],
        "programs": code,
    }
    encoded = canonical_json_bytes(body)
    if len(encoded) > 262_144:
        raise LaneError(
            "EVIDENCE_EXPORT_REQUEST_BUDGET",
            "The export request exceeds its private descriptor bound.",
        )
    request_sha = sha256_bytes(encoded).lower()
    program_sha = sha256_bytes(canonical_json_bytes(code)).lower()
    secrets = tuple(value.encode() for value in secret_values(request.tool_id, config))
    with tempfile.TemporaryDirectory(prefix="evidence-lane-export-") as directory:
        (Path(directory) / "request.json").write_bytes(encoded)
        environment = {
            name: value
            for name, value in os.environ.items()
            if name.upper()
            in {
                "PATH",
                "PATHEXT",
                "SYSTEMROOT",
                "WINDIR",
                "COMSPEC",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
            }
        }
        environment.update(
            TEMP=directory,
            TMP=directory,
            _EVI_EXPORT_ENDPOINT=origin,
            _EVI_EXPORT_CONFIG=json.dumps(config, separators=(",", ":")),
        )
        process = run_owned_bounded_process(
            [
                sys.executable,
                "-I",
                "-B",
                str(Path(__file__).with_name("_evidence_export_worker.py")),
                request_sha,
            ],
            cwd=directory,
            env=environment,
            timeout_seconds=request.timeout_seconds,
            max_stdout_bytes=request.max_response_bytes + 131_072,
            max_stderr_bytes=65_536,
            check=context.execution.check,
        )
        context.execution.check()
        try:
            response = json.loads(process.stdout)
            if process.returncode != 0:
                error = response.get("error_code")
                if error not in {
                    "EVIDENCE_EXPORT_HTTP_STATUS",
                    "EVIDENCE_EXPORT_REQUEST_BUDGET",
                    "EVIDENCE_EXPORT_RESPONSE_BUDGET",
                    "EVIDENCE_EXPORT_PROVIDER_FAILURE",
                    "EVIDENCE_EXPORT_GRANT_EXPIRED",
                    "EVIDENCE_EXPORT_ENDPOINT_CHANGED",
                    "EVIDENCE_EXPORT_RESPONSE_INVALID",
                }:
                    error = "EVIDENCE_EXPORT_WORKER_FAILED"
                raise LaneError(
                    error,
                    "The optional evidence export failed without changing project authority.",
                )
            if (
                response["status"] != "ok"
                or response["request_sha256"] != request_sha
                or response["program_sha256"] != program_sha
                or programs() != code
            ):
                raise LaneError(
                    "EVIDENCE_EXPORT_WORKER_BINDING_CHANGED",
                    "The export worker request or program identity changed.",
                )
            value = response["result"]
            raw = canonical_json_bytes(value)
            if len(raw) > request.max_response_bytes or any(
                secret and secret in raw for secret in secrets
            ):
                raise LaneError(
                    "EVIDENCE_EXPORT_RESULT_SECRET_OR_BUDGET",
                    "The export result violated its output boundary.",
                )
            validate_result(
                value,
                request,
                kind=kind,
                project_id=project_id,
                task_id=task_id,
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            raise LaneError(
                "EVIDENCE_EXPORT_RESULT_INVALID",
                "The export worker returned an invalid complete result.",
            ) from None
    result_value = {
        **value,
        "grant": proof,
        "request_sha256": request_sha,
        "program_sha256": program_sha,
        "owned_worker_joined": True,
        "credential_values_persisted": False,
        "automatic_retry": False,
        "http_request_count": 1,
    }
    receipt = sha256_bytes(canonical_json_bytes(result_value)).lower()
    return ExternalEvidenceResult(
        tool_id=request.tool_id,
        kind=kind,
        delivery=result_value,
        receipt_sha256=receipt,
    )


def verify_external_evidence(context: Any, request: Any, output: ExternalEvidenceResult) -> list[dict[str, Any]]:
    valid = (
        output.tool_id == request.tool_id
        and output.receipt_sha256
        == sha256_bytes(canonical_json_bytes(output.delivery)).lower()
        and output.delivery.get("owned_worker_joined") is True
        and output.delivery.get("project_authority_mutated") is False
        and output.delivery.get("remote_receipt_is_project_authority") is False
        and output.delivery.get("http_request_count") == 1
    )
    return [
        {
            "check_id": name,
            "passed": valid,
            "evidence": {
                "tool_id": output.tool_id,
                "kind": output.kind,
                "receipt_sha256": output.receipt_sha256,
                "remote_read_repeated_for_verification": False,
            },
        }
        for name in context.requested_checks
    ]


__all__ = [
    "EXPORT_BACKENDS",
    "ExternalEvidenceResult",
    "configuration",
    "endpoint",
    "evidence_binding",
    "execute",
    "parse_otlp_headers",
    "programs",
    "readiness",
    "resource_scope",
    "secret_values",
    "validate_result",
    "verify_external_evidence",
    "verify_resource_configuration",
]
