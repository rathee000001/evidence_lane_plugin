"""Evaluation and observability exports through current grants, Delta and MCP."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
from evidence_lane_plugin import bounded_io
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.connector_governance import PluginRegistration, connector_service
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.evaluation_toolchain import EVALUATION_FEEDBACK_EXPORT
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.observability_toolchain import OBSERVABILITY_EXPORT
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest

from .evidence_export_http_fixture import (
    BACKENDS,
    CONFIG_KEYS,
    ENVIRONMENT,
    HASH_A,
    HASH_B,
    HASH_C,
    RESOURCES,
    RUN_ID,
    SECRETS,
    SESSION_ID,
)
from .test_delta_entry import bind_fixture_flash
from .test_native_workflow_bindings import native


@pytest.fixture
def system(tmp_path, monkeypatch):
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    source = tmp_path / "source"
    source.mkdir()
    state = {
        "configuration": {},
        "directories": [],
        "request_counts": [],
        "signal": None,
        "timing": {},
    }
    original = bounded_io.run_owned_bounded_process
    wrapper = Path(__file__).with_name("evidence_export_http_fixture_worker.py")
    with Engine(tmp_path / "runtime") as engine:
        bind_fixture_flash(engine, tmp_path, monkeypatch)
        entry = engine.directory.register(
            tmp_path / "state",
            source_root=source,
            create=True,
            read_only=False,
        )
        store = engine.directory.open(entry["project_id"], write=True)
        _, session = engine.clients.connect(
            ConnectRequest(
                hello=ClientHello(configured_profile="codex_cli"),
                projects=[
                    ProjectSelection(
                        project_id=store.project_id,
                        permissions=["read", "write", "publish", "tools", "admin"],
                    )
                ],
            )
        )

        def controlled(command, **kwargs):
            if len(command) < 2 or Path(command[-2]).name != "_evidence_export_worker.py":
                return original(command, **kwargs)
            directory = Path(kwargs["cwd"])
            body = json.loads((directory / "request.json").read_bytes())
            tool_id = body["arguments"]["tool_id"]
            marker = directory / "started.marker"
            (directory / "fixture.json").write_text(
                json.dumps(
                    {
                        "tool_id": tool_id,
                        "kind": body["kind"],
                        "signal": body["arguments"].get("signal"),
                        "configuration": dict(
                            state["configuration"],
                            marker=str(marker),
                        ),
                    }
                ),
                encoding="utf-8",
            )
            request_text = (directory / "request.json").read_text(encoding="utf-8")
            assert all(secret not in request_text for secret in SECRETS)
            assert all(secret not in command for secret in SECRETS)
            state["directories"].append(directory)
            checked = kwargs["check"]
            execution = checked.__self__
            signalled = False

            def check():
                nonlocal signalled
                if marker.exists() and state["signal"] == "revoke" and not signalled:
                    signalled = True
                    state["timing"]["signal_started"] = time.monotonic()
                    with execution.slot.mutex:
                        connector_service(engine, store).revoke(
                            "opentelemetry-export",
                            execution.guard.context,
                            execution.lease,
                        )
                    state["timing"]["signal_recorded"] = time.monotonic()
                checked()

            kwargs["check"] = check
            try:
                state["timing"]["worker_started"] = time.monotonic()
                return original([*command[:-2], str(wrapper), command[-1]], **kwargs)
            finally:
                state["timing"]["worker_joined"] = time.monotonic()
                state["request_counts"].append(
                    int(marker.read_text(encoding="utf-8")) if marker.exists() else 0
                )

        monkeypatch.setattr(bounded_io, "run_owned_bounded_process", controlled)
        yield engine, store, session, state


def call(system, action, arguments=None, **kwargs):
    engine, store, session, _ = system
    return PublicActionSDKDispatcher(engine).execute(
        ActionRequest(
            action=action,
            project_id=store.project_id,
            arguments=arguments or {},
            **kwargs,
        ),
        session,
    )


def action_for(tool_id: str) -> str:
    return EVALUATION_FEEDBACK_EXPORT if tool_id == "LangSmith" else OBSERVABILITY_EXPORT


def configure(system, tool_id: str, **changes):
    action = action_for(tool_id)
    capability = "evaluation_feedback" if tool_id == "LangSmith" else "observability_export"
    role = (
        "evaluation_feedback_receipt"
        if tool_id == "LangSmith"
        else "observability_export_receipt"
    )
    backend_id, version = BACKENDS[tool_id]
    registration = PluginRegistration.model_validate(
        {
            "plugin_id": tool_id.casefold() + "-export",
            "name": tool_id + " evidence export",
            "plugin_kind": "connector",
            "description": "Deliver one bounded evidence record.",
            "purpose": "Export selected evaluation or observability evidence.",
            "config_env_keys": CONFIG_KEYS[tool_id],
            "capabilities": [capability],
            "allowed_lanes": ["receipts"],
            "allowed_actions": [action],
            "resource_ids": [RESOURCES[tool_id]],
            "expires_at": "NO_EXPIRY",
            "role": role,
            "role_schema": {
                "tool_id": "text",
                "kind": "text",
                "delivery": "json",
                "receipt_sha256": "blob_hash",
            },
            "host_profiles": ["codex_cli"],
            "backend_runtime": "python",
            "backend_id": backend_id,
            "backend_version": version,
        }
        | changes
    )
    result = call(
        system,
        "connector_configure",
        {"registration": registration.model_dump()},
    )
    assert result.status == "ok", result.error
    return result


def plan(system, tool_id: str):
    engine, store, _, _ = system
    action = action_for(tool_id)
    definition = TaskDefinition(
        task_id="evidence-export",
        title="Export optional evidence",
        requested_outcome="Deliver one bounded attributed evidence record",
        profile="core",
        allowed_actions=[action],
        permitted_tools=["Python", "HTTPX", tool_id],
        acceptance_checks=["external_evidence_receipt"],
    )
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(
            PlanCreate(title="Evidence export", tasks=[definition]),
            lease,
            actor_id="fixture",
        )
    return PlanStore(store).task("evidence-export", expected_revision=1)


def arguments(tool_id: str, signal: str | None = None):
    if tool_id == "LangSmith":
        return {
            "tool_id": tool_id,
            "lane_id": "receipts",
            "resource_id": RESOURCES[tool_id],
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
    value = {
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
        "duration_nanoseconds": 100
        if tool_id == "Langfuse" or signal == "trace"
        else 0,
        "attribute_sha256": {"source": "d" * 64},
    }
    if signal == "metric":
        value["metric_value"] = 1.5
    if tool_id == "Grafana":
        value["dashboard_uid"] = "dashboard-one"
    return value


def enter(system, task, tool_id: str, signal: str | None = None):
    return call(
        system,
        "delta_enter",
        {
            "task_id": "evidence-export",
            "plan_revision": 1,
            "contract_digest": task.contract_digest,
            "action": action_for(tool_id),
            "arguments": arguments(tool_id, signal),
        },
        expected_revision=1,
    )


def finished(system, job_id):
    engine, store, _, _ = system
    deadline = time.monotonic() + 25
    while engine.health().accepted_requests:
        if time.monotonic() >= deadline:
            pytest.fail("The evidence export Delta did not finish")
        time.sleep(0.01)
    with store.lane("plan").connection(read_only=True) as connection:
        return dict(
            connection.execute(
                "SELECT * FROM delta_runs WHERE job_id=?",
                (job_id,),
            ).fetchone()
        )


@pytest.mark.parametrize(
    ("tool_id", "signal"),
    [
        ("LangSmith", None),
        ("OpenTelemetry", "metric"),
        ("Langfuse", "evaluator"),
        ("Grafana", "trace_link"),
    ],
)
def test_each_registered_provider_runs_under_exact_dynamic_lane_grant(
    system,
    tool_id: str,
    signal: str | None,
) -> None:
    configured = configure(system, tool_id)
    task = plan(system, tool_id)
    entered = enter(system, task, tool_id, signal)
    assert entered.status == "queued", entered.error
    row = finished(system, entered.job_id)
    assert row["state"] == "verified", row
    body = json.loads(system[1].lane("plan").read_object(row["result_object"]))
    proof = body["tool_execution"]["extension_binding"]
    delivery = body["result"]["delivery"]
    assert proof["lane"] == "receipts"
    assert proof["registration_digest"] == configured.result["digest"]
    assert proof["backend_id"] == BACKENDS[tool_id][0]
    assert delivery["owned_worker_joined"] is True
    assert delivery["http_request_count"] == 1
    assert delivery["project_authority_mutated"] is False
    assert delivery["remote_receipt_is_project_authority"] is False
    serialized = json.dumps(body)
    assert all(secret not in serialized for secret in SECRETS)
    assert system[3]["request_counts"] == [1]
    assert all(not path.exists() for path in system[3]["directories"])


@pytest.mark.parametrize(
    "change",
    [
        {"resource_ids": ["OpenTelemetry:other"]},
        {"allowed_lanes": ["research"]},
        {"backend_id": "different.backend"},
        {"backend_version": "other"},
        {"config_env_keys": ["OTEL_EXPORTER_OTLP_ENDPOINT"]},
    ],
)
def test_scope_backend_lane_and_configuration_mismatch_stop_before_io(system, change):
    configure(system, "OpenTelemetry", **change)
    task = plan(system, "OpenTelemetry")
    result = enter(system, task, "OpenTelemetry", "trace")
    assert result.error.code == "TOOL_ROUTE_UNAVAILABLE"
    assert system[3]["directories"] == []


def test_resource_and_configured_project_mismatch_blocks_before_worker(system):
    configure(system, "Langfuse", resource_ids=["Langfuse:other"])
    task = plan(system, "Langfuse")
    value = arguments("Langfuse", "span") | {"resource_id": "Langfuse:other"}
    result = call(
        system,
        "delta_enter",
        {
            "task_id": "evidence-export",
            "plan_revision": 1,
            "contract_digest": task.contract_digest,
            "action": OBSERVABILITY_EXPORT,
            "arguments": value,
        },
        expected_revision=1,
    )
    assert result.status == "queued"
    row = finished(system, result.job_id)
    assert row["state"] == "blocked"
    assert row["error_code"] == "EVIDENCE_EXPORT_RESOURCE_CONFIGURATION_MISMATCH"
    assert system[3]["directories"] == []


def test_provider_partial_success_blocks_delta_result(system):
    configure(system, "OpenTelemetry")
    system[3]["configuration"] = {"partial_success": True}
    task = plan(system, "OpenTelemetry")
    entered = enter(system, task, "OpenTelemetry", "trace")
    assert entered.status == "queued"
    row = finished(system, entered.job_id)
    assert row["state"] == "blocked"
    assert row["result_object"] is None
    assert row["error_code"] == "EVIDENCE_EXPORT_PROVIDER_FAILURE"


def test_current_connector_revocation_terminates_running_export(system):
    configure(system, "OpenTelemetry")
    system[3].update(signal="revoke", configuration={"delay_seconds": 5})
    task = plan(system, "OpenTelemetry")
    entered = enter(system, task, "OpenTelemetry", "trace")
    assert entered.status == "queued"
    row = finished(system, entered.job_id)
    assert row["state"] == "blocked" and row["result_object"] is None
    assert row["error_code"] == "PLUGIN_ROUTE_DENIED"
    timing = system[3]["timing"]
    assert timing["worker_joined"] - timing["signal_started"] < 5
    assert system[3]["request_counts"] == [1]
    assert all(not path.exists() for path in system[3]["directories"])


def test_packaged_mcp_declares_routes_and_executes_owned_adapter(system):
    engine, store, _, _ = system
    configure(system, "Grafana", host_profiles=["codex_cli", "codex_desktop"])
    task = plan(system, "Grafana")
    with LocalEndpoint(engine):

        async def exercise():
            async with native(
                engine.root,
                store.project_id,
                permissions=("read", "write", "publish", "tools", "admin"),
                entrypoint="package",
            ) as session:
                tools = await session.list_tools()
                names = {row.name for row in tools.tools}
                assert EVALUATION_FEEDBACK_EXPORT in names
                assert OBSERVABILITY_EXPORT in names
                contract = engine.registry.get(OBSERVABILITY_EXPORT).schema()["toolchain"]
                assert len(contract["routes"]) == 3
                assert {row["extension"]["backend_id"] for row in contract["routes"]} == {
                    BACKENDS[name][0]
                    for name in ("OpenTelemetry", "Langfuse", "Grafana")
                }
                response = await session.call_tool(
                    "delta_enter",
                    {
                        "project_id": store.project_id,
                        "expected_revision": 1,
                        "arguments": {
                            "task_id": "evidence-export",
                            "plan_revision": 1,
                            "contract_digest": task.contract_digest,
                            "action": OBSERVABILITY_EXPORT,
                            "arguments": arguments("Grafana", "trace_link"),
                        },
                    },
                )
                result = response.structuredContent
                assert result["status"] == "queued", result
                assert finished(system, result["job_id"])["state"] == "verified"

        asyncio.run(exercise())
