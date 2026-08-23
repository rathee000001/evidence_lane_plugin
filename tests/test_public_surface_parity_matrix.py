from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import evidence_lane_plugin.mcp_server as mcp_server_module
import pytest
from evidence_lane_plugin.auth import (
    READ_SCOPE,
    REMOTE_GIT_SCOPE,
    WRITE_SCOPE,
    OAuthJWTConfig,
)
from evidence_lane_plugin.constants import (
    GOVERNED_SKILL_COUNT,
    NATIVE_READ_TOOL_COUNT,
    NATIVE_TOOL_COUNT,
    NATIVE_WRITE_TOOL_COUNT,
)
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.hook_contract import HOOK_EVENT_NAMES
from evidence_lane_plugin.mcp_server import SDK_NATIVE_ACTIONS, create_mcp_server
from evidence_lane_plugin.service import EvidenceLaneService
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
MATRIX_PATH = ROOT / "docs" / "V300_PUBLIC_SURFACE_PARITY_MATRIX.json"
RELEASE_GATE_PATH = (
    PLUGIN
    / "skills"
    / "evi"
    / "references"
    / "public-tool-conformance-release-gate.v1.json"
)
EVALUATION_CASES = (
    "representative",
    "edge",
    "missing",
    "empty",
    "auth",
    "write_confirmation",
    "unsupported",
)


def _case_value(schema: dict[str, object], *, edge: bool) -> object:
    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[-1 if edge else 0]
    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        options = [
            row
            for row in any_of
            if isinstance(row, dict) and row.get("type") != "null"
        ]
        if options:
            return _case_value(options[-1 if edge else 0], edge=edge)
    schema_type = schema.get("type")
    if schema_type == "string":
        return "" if edge else "value"
    if schema_type == "integer":
        return 0 if edge else 1
    if schema_type == "number":
        return 0.0 if edge else 1.0
    if schema_type == "boolean":
        return not edge
    if schema_type == "array":
        if edge:
            return []
        items = schema.get("items")
        return [
            _case_value(
                items if isinstance(items, dict) else {"type": "string"},
                edge=False,
            )
        ]
    if schema_type == "object":
        properties = schema.get("properties")
        required = schema.get("required")
        if isinstance(properties, dict) and isinstance(required, list):
            return {
                name: _case_value(properties[name], edge=edge)
                for name in required
                if isinstance(name, str) and isinstance(properties.get(name), dict)
            }
        return {} if edge else {"key": "value"}
    return None


def _arguments_validate(tool: object, arguments: dict[str, object]) -> bool:
    try:
        tool.fn_metadata.arg_model.model_validate(arguments)  # type: ignore[attr-defined]
    except ValidationError:
        return False
    return True


def _evaluate_server_contracts(
    server: object,
    *,
    oauth_enabled: bool,
) -> dict[str, object]:
    tools = sorted(
        server._tool_manager.list_tools(),  # type: ignore[attr-defined]
        key=lambda item: item.name,
    )
    tool_names = {tool.name for tool in tools}
    records: list[dict[str, object]] = []
    untested: list[str] = []
    for tool in tools:
        schema = tool.parameters
        properties = schema.get("properties") or {}
        required = list(schema.get("required") or [])
        representative = {
            name: _case_value(properties[name], edge=False) for name in required
        }
        edge = {name: _case_value(properties[name], edge=True) for name in required}
        annotations = (
            tool.annotations.model_dump(exclude_none=True)
            if tool.annotations is not None
            else {}
        )
        read_only = annotations.get("readOnlyHint") is not False
        expected_scopes = [READ_SCOPE]
        if not read_only:
            expected_scopes.append(WRITE_SCOPE)
            if tool.name in {
                "remote_git_prepare_push",
                "remote_git_execute_push",
            }:
                expected_scopes.append(REMOTE_GIT_SCOPE)
        security = (tool.meta or {}).get("securitySchemes")
        cases = {
            "representative": _arguments_validate(tool, representative),
            "edge": _arguments_validate(tool, edge),
            "missing": (
                all(
                    not _arguments_validate(
                        tool,
                        {
                            name: value
                            for name, value in representative.items()
                            if name != omitted
                        },
                    )
                    for omitted in required
                )
                if required
                else _arguments_validate(tool, {})
            ),
            "empty": _arguments_validate(tool, {}) is (not required),
            "auth": (
                security == [{"type": "oauth2", "scopes": expected_scopes}]
                if oauth_enabled
                else security is None
            ),
            "write_confirmation": (
                annotations.get("readOnlyHint") is True
                if read_only
                else annotations.get("readOnlyHint") is False
                and (not oauth_enabled or WRITE_SCOPE in expected_scopes)
            ),
            "unsupported": (
                f"{tool.name}__unsupported" not in tool_names
                and server._tool_manager.get_tool(  # type: ignore[attr-defined]
                    f"{tool.name}__unsupported"
                )
                is None
            ),
        }
        schema_metadata = {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "input_schema": tool.parameters,
            "output_schema": tool.output_schema,
            "annotations": annotations,
            "meta": tool.meta,
        }
        record = {
            "name": tool.name,
            "schema_metadata_sha256": hashlib.sha256(
                json.dumps(
                    schema_metadata,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest().upper(),
            "cases": {
                name: "PASS" if cases[name] else "BLOCKED"
                for name in EVALUATION_CASES
            },
        }
        if not all(cases.values()):
            untested.append(tool.name)
        records.append(record)
    return {
        "status": "PASS" if not untested else "BLOCKED",
        "tool_count": len(records),
        "case_classes": list(EVALUATION_CASES),
        "case_evaluation_count": len(records) * len(EVALUATION_CASES),
        "untested_public_tools": untested,
        "matrix_sha256": hashlib.sha256(
            json.dumps(
                records,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest().upper(),
    }


def test_public_surface_matrix_matches_executable_catalog() -> None:
    from evidence_lane_plugin.public_surface_registry import (
        derive_public_surface_registry,
    )

    public_surface_registry = derive_public_surface_registry(PLUGIN)
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    actions = matrix["native_actions"]
    assert (NATIVE_TOOL_COUNT, NATIVE_READ_TOOL_COUNT, NATIVE_WRITE_TOOL_COUNT) == (
        88,
        27,
        61,
    )
    assert actions == {
        "total": NATIVE_TOOL_COUNT,
        "read": NATIVE_READ_TOOL_COUNT,
        "write": NATIVE_WRITE_TOOL_COUNT,
        "increase_law": (
            "INCREASE_ONLY_WHEN_A_DISTINCT_IMPLEMENTED_NATIVE_OPERATION_HAS_ITS_OWN_"
            "CONTRACT_HANDLER_TEST_AND_PACKAGE_PROOF"
        ),
        "all_count_surfaces_must_change_together": True,
    }

    skill_names = sorted(
        path.parent.name for path in (PLUGIN / "skills").glob("*/SKILL.md")
    )
    assert len(skill_names) == GOVERNED_SKILL_COUNT == 17
    assert set(matrix["governed_skills"]["new_in_this_group"]) <= set(skill_names)

    hooks = matrix["lifecycle_hooks"]
    assert hooks["registered_event_count"] == len(HOOK_EVENT_NAMES) == 11
    assert hooks["event_names"] == list(HOOK_EVENT_NAMES)
    hook_files = [
        PLUGIN / "hooks" / "hooks.json",
        *(PLUGIN / "hooks").glob("*.py"),
        *(PLUGIN / "hooks").glob("*.ps1"),
    ]
    assert len({path.resolve() for path in hook_files}) == hooks["package_file_count"] == 15

    commands = sorted(path.name for path in (PLUGIN / "commands").glob("*.md"))
    assert commands == [
        row["name"] for row in public_surface_registry["commands"]["records"]
    ]
    assert public_surface_registry["catalog"]["commands"] == len(commands)
    assert matrix["host_commands"]["command_files"] == commands


def test_canon_and_learning_public_actions_match_sdk_registration() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    registered = {
        name: {"module": module, "operation": operation, "read": read_only}
        for name, _title, _description, module, operation, read_only in SDK_NATIVE_ACTIONS
    }
    declared: dict[str, dict[str, object]] = {}
    for module, arm in matrix["sdk_public_arms"].items():
        for effect in ("read", "write"):
            for name in arm[effect]:
                operation = (
                    name.removeprefix("learning_memory_")
                    if module == "project_memory"
                    else name.removeprefix("canon_").removeprefix("learning_")
                )
                declared[name] = {
                    "module": module,
                    "operation": operation,
                    "read": effect == "read",
                }
    assert declared == registered
    assert len(registered) == 24
    assert sum(row["read"] is True for row in registered.values()) == 6


def test_every_current_group_declares_all_delta_surface_dimensions() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    required = set(matrix["delta_surface_classification_law"]["required_dimensions"])
    for row in matrix["current_group"]:
        assert set(row) == {"group", *required}
        assert all(str(row[key]).strip() for key in required)
    assert matrix["delta_surface_classification_law"]["classification_required_before_delta_exit"] is True


def test_row196_does_not_absorb_the_later_full_vercel_guide_refresh() -> None:
    matrix = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    boundary = matrix["vercel_phase_boundary"]

    assert boundary == {
        "current_row": 196,
        "current_scope": [
            "PLUGIN_PUBLIC_SURFACE_TRUTH",
            "CURRENT_DELTA_LEDGER_AND_PLAN_PROJECTION",
        ],
        "later_row": 197,
        "later_scope": [
            "FULL_GUIDE_REFRESH_FOR_EVERY_NAVIGATION_PAGE",
            "PAGE_SPECIFIC_CURRENT_PLUGIN_NARRATIVE",
            "PAGE_SPECIFIC_HERO_ICON_ORB_RING_ANIMATION",
            (
                "PAIN_POINT_LED_HOMEPAGE_STORY_DERIVED_FROM_CURRENT_"
                "ARCHITECTURE_AND_LANES"
            ),
        ],
        "current_row_may_claim_later_row_complete": False,
        "vercel_runtime_is_project_truth_authority": False,
    }


def _oauth_config() -> OAuthJWTConfig:
    return OAuthJWTConfig(
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="https://mcp.example/mcp",
        required_scopes=(READ_SCOPE,),
        deployment_environment="staging",
        allowed_client_ids=("codex",),
        allowed_roles=("owner",),
    )


def test_every_source_public_tool_runs_the_bounded_eval_matrix(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "source-eval")
    )
    independent = _evaluate_server_contracts(server, oauth_enabled=False)
    sealed = server._evidence_lane_public_tool_evaluation_matrix  # type: ignore[attr-defined]
    compact = server._evidence_lane_native_route_receipt[  # type: ignore[attr-defined]
        "public_tool_evaluation"
    ]

    assert independent["status"] == sealed["status"] == compact["status"] == "PASS"
    assert independent["tool_count"] == sealed["tool_count"] == NATIVE_TOOL_COUNT
    assert sealed["read_tool_count"] == NATIVE_READ_TOOL_COUNT
    assert sealed["write_tool_count"] == NATIVE_WRITE_TOOL_COUNT
    assert sealed["case_classes"] == list(EVALUATION_CASES)
    assert sealed["case_evaluation_count"] == NATIVE_TOOL_COUNT * len(EVALUATION_CASES)
    assert sealed["untested_public_tools"] == []
    assert sealed["side_effect_free"] is True
    assert sealed["raw_tool_schemas_returned"] is False
    assert "records" not in compact
    assert compact["matrix_sha256"] == sealed["matrix_sha256"]


def test_every_source_public_handler_executes_with_hooks_off(
    service: EvidenceLaneService,
    source_repository: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = create_mcp_server(service=service)
    failure_boundary = (  # type: ignore[attr-defined]
        server._evidence_lane_public_handler_failure_boundary
    )
    tools = sorted(
        server._tool_manager.list_tools(),  # type: ignore[attr-defined]
        key=lambda item: item.name,
    )
    records: list[dict[str, object]] = []

    async def execute() -> None:
        for tool in tools:
            properties = tool.parameters.get("properties") or {}
            required = list(tool.parameters.get("required") or [])
            arguments = {
                name: _case_value(properties[name], edge=False)
                for name in required
            }
            if "project_id" in arguments:
                arguments["project_id"] = "book-faires"
            if "session_id" in arguments:
                arguments["session_id"] = "missing-parity-session"
            if "repository_path" in arguments:
                arguments["repository_path"] = str(source_repository)

            try:
                result = await asyncio.wait_for(
                    tool.run(arguments, convert_result=False),
                    timeout=5.0,
                )
            except TimeoutError:
                pytest.fail(f"UNBOUNDED_PUBLIC_HANDLER:{tool.name}")
            assert isinstance(result, dict), tool.name
            assert result["schema"] in {
                "evidence-lane.pv.tool-result.v1",
                "evidence-lane.pv.lifecycle-result.v1",
            }, tool.name
            assert result["status"] in {"PASS", "FAIL"}, tool.name
            assert result["execution_status"] == result["status"], tool.name
            assert isinstance(result["domain_status"], str), tool.name
            records.append(
                {
                    "name": tool.name,
                    "execution_status": result["execution_status"],
                    "domain_status": result["domain_status"],
                }
            )

    asyncio.run(execute())

    assert failure_boundary["status"] == "PASS"
    assert failure_boundary["wrapped_handler_count"] == NATIVE_TOOL_COUNT
    assert len(records) == len({row["name"] for row in records}) == NATIVE_TOOL_COUNT
    by_name = {str(row["name"]): row for row in records}
    for valid_hooks_off_read in (
        "lane_catalog",
        "lifecycle_transition_law",
        "render_runtime_panel",
        "runtime_activation_status",
        "runtime_doctor",
        "session_flash_status",
    ):
        assert by_name[valid_hooks_off_read]["execution_status"] == "PASS"

    probe_service = EvidenceLaneService(data_root=tmp_path / "entry-probe")
    dispatched: list[str] = []

    def entry_receipt(
        *,
        tool_name: str,
        lifecycle: bool,
        project_id: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        body = {
            "schema": "evidence-lane.public-entry-binding.v1",
            "status": "PASS",
            "tool_name": tool_name,
            "effect_class": "WRITE" if lifecycle else "READ",
            "binding": {"binding_mode": "SIDE_EFFECT_FREE_DISPATCH_PROBE"},
            "env_uop_entry_status": "ATTESTED",
            "runtime_instance_attestation_receipt_sha256": "A" * 64,
            "caller_supplied_runtime_identity": False,
            "callback_entered": False,
        }
        body["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
        return body

    def dispatch_without_business_effects(
        tool: str,
        callback: Callable[..., dict[str, Any]],
        /,
        *args: Any,
        lifecycle: bool = False,
        entry_authorization: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del callback, args, kwargs
        dispatched.append(tool)
        return {
            "schema": (
                "evidence-lane.pv.lifecycle-result.v1"
                if lifecycle
                else "evidence-lane.pv.tool-result.v1"
            ),
            "tool": tool,
            "status": "PASS",
            "execution_status": "PASS",
            "domain_status": "DISPATCH_PROBE",
            "data": {"status": "DISPATCH_PROBE"},
            "warnings": [],
            "error": None,
            "provenance": {"fabricated_evidence": False},
            "entry_authorization": entry_authorization,
        }

    monkeypatch.setattr(
        probe_service,
        "_public_entry_binding_receipt",
        entry_receipt,
    )
    monkeypatch.setattr(probe_service, "invoke", dispatch_without_business_effects)
    monkeypatch.setattr(
        probe_service.sessions,
        "load",
        lambda *args, **kwargs: SimpleNamespace(
            metadata={"active_backlog_task_id": ""}
        ),
    )
    monkeypatch.setattr(
        probe_service,
        "task_backlog",
        lambda *args, **kwargs: {"tasks": []},
    )
    monkeypatch.setattr(
        probe_service,
        "status",
        lambda *args, **kwargs: {"status": "PASS"},
    )
    monkeypatch.setattr(
        mcp_server_module,
        "build_project_panel_snapshot",
        lambda **kwargs: {},
    )
    probe_server = create_mcp_server(service=probe_service)
    probe_tools = sorted(
        probe_server._tool_manager.list_tools(),  # type: ignore[attr-defined]
        key=lambda item: item.name,
    )

    async def execute_dispatch_probe() -> None:
        for tool in probe_tools:
            properties = tool.parameters.get("properties") or {}
            arguments = {
                name: _case_value(properties[name], edge=False)
                for name in list(tool.parameters.get("required") or [])
            }
            if "project_id" in arguments:
                arguments["project_id"] = "dispatch-project"
            if "session_id" in arguments:
                arguments["session_id"] = "dispatch-session"
            if "repository_path" in arguments:
                arguments["repository_path"] = str(source_repository)
            result = await asyncio.wait_for(
                tool.run(arguments, convert_result=False),
                timeout=5.0,
            )
            assert result["execution_status"] == "PASS", (
                tool.name,
                result.get("error"),
            )

    asyncio.run(execute_dispatch_probe())

    assert sorted(dispatched) == [tool.name for tool in probe_tools]
    assert len(dispatched) == len(set(dispatched)) == NATIVE_TOOL_COUNT


def test_every_public_action_denies_before_entering_service_handler(
    tmp_path: Path,
    source_repository: Path,
) -> None:
    class DenialProbeService(EvidenceLaneService):
        def __init__(self, data_root: Path) -> None:
            super().__init__(data_root=data_root)
            self.handler_invocations: list[str] = []

        def invoke(
            self,
            tool: str,
            callback: Callable[..., dict[str, Any]],
            /,
            *args: Any,
            lifecycle: bool = False,
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.handler_invocations.append(tool)
            return super().invoke(
                tool,
                callback,
                *args,
                lifecycle=lifecycle,
                **kwargs,
            )

    service = DenialProbeService(tmp_path / "denied-service")
    server = create_mcp_server(
        service=service,
        base_url="https://mcp.example",
        oauth_config=_oauth_config(),
    )
    tools = sorted(
        server._tool_manager.list_tools(),  # type: ignore[attr-defined]
        key=lambda item: item.name,
    )

    def tree_identity() -> dict[str, str]:
        return {
            path.relative_to(service.store.root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(service.store.root.rglob("*"))
            if path.is_file()
        }

    before = tree_identity()

    async def execute() -> None:
        for tool in tools:
            properties = tool.parameters.get("properties") or {}
            arguments = {
                name: _case_value(properties[name], edge=False)
                for name in list(tool.parameters.get("required") or [])
            }
            if "project_id" in arguments:
                arguments["project_id"] = "denied-project"
            if "session_id" in arguments:
                arguments["session_id"] = "denied-session"
            if "repository_path" in arguments:
                arguments["repository_path"] = str(source_repository)
            result = await asyncio.wait_for(
                tool.run(arguments, convert_result=False),
                timeout=5.0,
            )
            assert result.isError is True, tool.name
            assert result.structuredContent["code"] == (
                "AUTHENTICATED_OAUTH_CONTEXT_REQUIRED"
            ), tool.name
            assert result.structuredContent["mutation_performed"] is False

    asyncio.run(execute())

    assert service.handler_invocations == []
    assert tree_identity() == before


def test_every_source_public_tool_runs_the_oauth_and_write_cases(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "oauth-eval"),
        base_url="https://mcp.example",
        oauth_config=_oauth_config(),
    )
    independent = _evaluate_server_contracts(server, oauth_enabled=True)
    sealed = server._evidence_lane_public_tool_evaluation_matrix  # type: ignore[attr-defined]

    assert independent["status"] == sealed["status"] == "PASS"
    assert independent["tool_count"] == sealed["tool_count"] == NATIVE_TOOL_COUNT
    assert sealed["oauth_enabled"] is True
    assert sealed["case_evaluation_count"] == NATIVE_TOOL_COUNT * len(EVALUATION_CASES)
    assert sealed["untested_public_tools"] == []


def test_public_tool_matrix_rejects_one_untested_contract(tmp_path: Path) -> None:
    from evidence_lane_plugin.mcp_server import (
        build_public_tool_evaluation_matrix,
    )

    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "blocked-eval")
    )
    tool = server._tool_manager.get_tool("runtime_doctor")  # type: ignore[attr-defined]
    assert tool is not None
    tool.parameters = {
        "type": "object",
        "properties": {"required_value": {"type": "string"}},
        "required": ["required_value"],
    }

    blocked = build_public_tool_evaluation_matrix(server, oauth_enabled=False)

    assert blocked["status"] == "BLOCKED"
    assert blocked["untested_public_tools"] == ["runtime_doctor"]


def test_conformance_release_gate_is_derived_from_the_sealed_matrix() -> None:
    gate = json.loads(RELEASE_GATE_PATH.read_text(encoding="utf-8"))
    generated = gate["generated_from"]
    receipt_path = ROOT / generated["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    catalogs = gate["evaluated_catalogs"]

    assert gate["schema"] == "evidence-lane.public-tool-conformance-release-gate.v1"
    assert gate["status"] == "PASS"
    assert gate["source_delta_task_id"] == receipt["delta_task_id"]
    assert generated["receipt_path"] == str(
        receipt_path.relative_to(ROOT)
    ).replace("\\", "/")
    assert generated["receipt_file_sha256"] == hashlib.sha256(
        receipt_path.read_bytes()
    ).hexdigest().upper()
    assert generated["source_matrix_sha256"] == receipt["implementation"][
        "source_catalog"
    ]["matrix_sha256"]
    assert generated["installed_matrix_sha256"] == receipt["implementation"][
        "exact_installed_catalog"
    ]["matrix_sha256"]

    assert catalogs["source"] == {
        "tool_count": receipt["implementation"]["source_catalog"]["tool_count"],
        "case_evaluation_count": receipt["implementation"]["source_catalog"][
            "case_evaluation_count"
        ],
        "status": "PASS",
    }
    assert catalogs["exact_installed"] == {
        "tool_count": receipt["implementation"]["exact_installed_catalog"][
            "tool_count"
        ],
        "case_evaluation_count": receipt["implementation"][
            "exact_installed_catalog"
        ]["case_evaluation_count"],
        "status": "PASS",
    }
    assert catalogs["source_only_preinstall_tools"] == receipt["implementation"][
        "source_only_preinstall_tools"
    ]
    assert gate["applicable_case_results"] == {
        name: "PASS" for name in EVALUATION_CASES
    }
    assert set(gate["immutable_case_locators"]) == set(EVALUATION_CASES)
    assert all(
        locator.startswith(
            (
                "tests/test_public_surface_parity_matrix.py::test_",
                "tests/test_public_surface_registry.py::test_",
            )
        )
        for locator in gate["immutable_case_locators"].values()
    )


def test_conformance_gate_binds_every_release_receipt_without_authorizing_it() -> None:
    gate = json.loads(RELEASE_GATE_PATH.read_text(encoding="utf-8"))
    chain = gate["release_receipt_chain"]
    required = set(chain["required_common_fields"])
    routing = json.loads(
        (
            PLUGIN
            / "skills"
            / "evi"
            / "references"
            / "mcp-tool-routing.v1.json"
        ).read_text(encoding="utf-8")
    )

    assert chain["ordered_stages"] == ["COMMIT", "INSTALL", "CI", "HIL"]
    assert {
        "source_matrix_sha256",
        "installed_matrix_sha256",
        "conformance_gate_file_sha256",
        "evidence_locators",
        "receipt_sha256",
    } <= required
    assert set(chain["stage_specific_fields"]) == set(chain["ordered_stages"])
    assert all(chain["stage_specific_fields"][stage] for stage in chain["ordered_stages"])
    assert chain["non_pass_behavior"] == "BLOCK_CURRENT_AND_DOWNSTREAM_STAGE"
    assert chain["matrix_hash_mismatch_behavior"] == "FAIL_CLOSED"
    assert gate["release_eligibility"] == (
        "BLOCKED_PENDING_EXACT_COMMIT_INSTALL_CI_AND_HIL_RECEIPTS"
    )
    assert gate["boundaries"] == {
        "raw_matrix_returned": False,
        "write_handler_invoked": False,
        "git_authorized": False,
        "install_authorized": False,
        "ci_claimed": False,
        "hil_invoked": False,
        "hil_inferred": False,
        "candidate_created": False,
        "pointer_moved": False,
    }
    skill_text = (PLUGIN / "skills" / "evi" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "public-tool-conformance-release-gate.v1.json" in skill_text
    assert "CONFORMANCE_GATE_FAIL_CLOSED" in skill_text
    assert routing["catalog_contract"]["conformance_release_gate"] == {
        "path": "references/public-tool-conformance-release-gate.v1.json",
        "required_before": ["COMMIT", "INSTALL", "CI", "HIL"],
        "failure_behavior": "CONFORMANCE_GATE_FAIL_CLOSED",
    }


def test_exact_installed_package_runs_the_same_public_tool_cases() -> None:
    installed_value = os.environ.get("EVIDENCE_LANE_EXACT_INSTALLED_PLUGIN_ROOT")
    if not installed_value:
        pytest.skip("Exact installed package root was not supplied for this run.")
    installed_root = Path(installed_value).resolve()
    assert (installed_root / ".codex-plugin" / "plugin.json").is_file()
    script = """
import json
import tempfile
from pathlib import Path
from evidence_lane_plugin.auth import READ_SCOPE, OAuthJWTConfig
from evidence_lane_plugin.constants import NATIVE_TOOL_COUNT
from evidence_lane_plugin.mcp_server import create_mcp_server
from evidence_lane_plugin.service import EvidenceLaneService
from tests.test_public_surface_parity_matrix import _evaluate_server_contracts

config = OAuthJWTConfig(
    issuer_url="https://issuer.example/",
    jwks_url="https://issuer.example/.well-known/jwks.json",
    audience="https://mcp.example/mcp",
    required_scopes=(READ_SCOPE,),
    deployment_environment="staging",
    allowed_client_ids=("codex",),
    allowed_roles=("owner",),
)
server = create_mcp_server(
    service=EvidenceLaneService(data_root=Path(tempfile.mkdtemp())),
    base_url="https://mcp.example",
    oauth_config=config,
)
matrix = _evaluate_server_contracts(server, oauth_enabled=True)
print(json.dumps({
    **matrix,
    "constant_tool_count": NATIVE_TOOL_COUNT,
    "module_path": __import__("evidence_lane_plugin").__file__,
}, sort_keys=True, separators=(",", ":")))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(installed_root / "src"), str(ROOT)]
    )
    environment["EVIDENCE_LANE_PLUGIN_ROOT"] = str(installed_root)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    installed = json.loads(result.stdout)
    assert Path(installed["module_path"]).resolve().is_relative_to(installed_root)
    assert installed["status"] == "PASS"
    assert installed["tool_count"] == installed["constant_tool_count"]
    assert installed["case_classes"] == list(EVALUATION_CASES)
    assert installed["case_evaluation_count"] == installed["tool_count"] * len(
        EVALUATION_CASES
    )
    assert installed["untested_public_tools"] == []
