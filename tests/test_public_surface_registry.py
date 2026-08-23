from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import evidence_lane_plugin.mcp_server as mcp_server_module
import pytest
from evidence_lane_plugin.auth import (
    READ_SCOPE,
    OAuthJWTConfig,
    OAuthToolAuthorizationPolicy,
)
from evidence_lane_plugin.errors import EvidenceLaneError
from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
from evidence_lane_plugin.mcp_server import (
    FULL_LIFECYCLE_EXPOSURE_PROFILE,
    _MCPExposureBoundary,
    create_mcp_server,
    run_server,
)
from evidence_lane_plugin.public_surface_registry import (
    CODEX_READ_TOOL_NAMES,
    derive_public_surface_registry,
    derive_runtime_catalog_constants,
)
from evidence_lane_plugin.service import EvidenceLaneService

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _public_entry_preflight_receipt(
    tool_name: str = "task_classify",
    *,
    lifecycle: bool = True,
) -> dict[str, Any]:
    body = {
        "schema": "evidence-lane.public-entry-binding.v1",
        "status": "PASS",
        "tool_name": tool_name,
        "effect_class": "WRITE" if lifecycle else "READ",
        "binding": {"binding_mode": "TEST_EXACT_ENTRY"},
        "env_uop_entry_status": "ATTESTED",
        "runtime_instance_attestation_receipt_sha256": "A" * 64,
        "caller_supplied_runtime_identity": False,
        "callback_entered": False,
    }
    body["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def _assert_self_hashed_receipt(receipt: dict[str, Any]) -> None:
    claimed = str(receipt["receipt_sha256"])
    unsigned = {
        key: value for key, value in receipt.items() if key != "receipt_sha256"
    }
    actual = sha256_bytes(canonical_json_bytes(unsigned))
    assert actual == claimed


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


def test_live_surface_counts_are_registry_derived_and_route_reconciled(
    tmp_path: Path,
) -> None:
    registry = derive_public_surface_registry(PLUGIN)
    assert derive_runtime_catalog_constants(PLUGIN) == {
        key: registry["catalog"][key]
        for key in ("tools", "read", "write", "skills")
    }
    assert registry["status"] == "PASS"
    assert registry["release_catalog_matches_derived"] is True
    assert registry["catalog"] == {
        "tools": len(registry["tools"]["names"]),
        "read": len(registry["tools"]["read_names"]),
        "write": len(registry["tools"]["write_names"]),
        "skills": len(registry["skills"]["records"]),
        "commands": len(registry["commands"]["records"]),
        "hook_events": len(registry["hooks"]["event_names"]),
        "hook_handlers": len(registry["hooks"]["event_names"]),
        "providers": len(registry["providers"]["names"]),
    }
    assert registry["tools"]["read_names"] == sorted(CODEX_READ_TOOL_NAMES)
    assert len(registry["registry_sha256"]) == 64

    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store")
    )
    route = server._evidence_lane_native_route_receipt
    assert route["status"] == "PASS"
    assert {
        "tools": route["tool_count"],
        "read": route["read_tool_count"],
        "write": route["write_tool_count"],
        "skills": route["skill_count"],
        "commands": route["command_count"],
        "hook_events": route["hook_event_count"],
        "hook_handlers": route["hook_handler_count"],
        "providers": route["provider_count"],
    } == registry["catalog"]
    public_surface = route["public_surface_registry"]
    assert {
        key: public_surface[key]
        for key in (
            "schema",
            "status",
            "registry_sha256",
            "release_catalog_matches_derived",
            "routing_catalog_matches_derived",
        )
    } == {
        "schema": registry["schema"],
        "status": "PASS",
        "registry_sha256": registry["registry_sha256"],
        "release_catalog_matches_derived": True,
        "routing_catalog_matches_derived": True,
    }
    assert public_surface["route_law"] == "PUBLIC_SURFACE_SINGLE_INSTALLED_ROOT_LAW"
    assert public_surface["package_identity"]["identity_matches"] is True
    assert (
        public_surface["package_identity"]["runtime_module_root_is_registry_root"]
        is True
    )
    assert (
        public_surface["package_identity"][
            "caller_selected_cross_package_root_allowed"
        ]
        is False
    )


def test_installed_wheel_layout_uses_the_sealed_packaged_runtime_catalog(
    tmp_path: Path,
) -> None:
    installed_root = tmp_path / "site-packages"

    assert derive_runtime_catalog_constants(installed_root) == {
        "tools": 88,
        "read": 27,
        "write": 61,
        "skills": 17,
    }


def test_stale_release_total_blocks_the_derived_surface(tmp_path: Path) -> None:
    surface = tmp_path / "plugin"
    for skill in (PLUGIN / "skills").glob("*/SKILL.md"):
        destination = surface / "skills" / skill.parent.name / "SKILL.md"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(skill, destination)
    routing_source = (
        PLUGIN / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    )
    routing_destination = (
        surface / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
    )
    routing_destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(routing_source, routing_destination)
    (surface / "commands").mkdir(parents=True, exist_ok=True)
    for command in (PLUGIN / "commands").glob("*.md"):
        shutil.copy2(command, surface / "commands" / command.name)
    (surface / "hooks").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN / "hooks" / "hooks.json", surface / "hooks" / "hooks.json")
    shutil.copy2(PLUGIN / ".mcp.json", surface / ".mcp.json")
    (surface / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / ".codex-plugin" / "plugin.json",
        surface / ".codex-plugin" / "plugin.json",
    )
    shutil.copy2(PLUGIN / "pyproject.toml", surface / "pyproject.toml")
    release_destination = surface / "scripts" / "codex-release-channel.json"
    release_destination.parent.mkdir(parents=True, exist_ok=True)
    release = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    release["stable"]["native_tool_count"] -= 1
    release_destination.write_text(json.dumps(release), encoding="utf-8")

    registry = derive_public_surface_registry(surface)
    assert registry["status"] == "BLOCKED"
    assert registry["release_catalog_matches_derived"] is False
    assert registry["release_claimed_catalog"]["tools"] + 1 == registry["catalog"][
        "tools"
    ]


def test_every_public_tool_authorizes_before_backend_access() -> None:
    class Backend:
        invoke_count = 0

        def invoke(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.invoke_count += 1
            raise AssertionError("backend access occurred before authorization")

    backend = Backend()
    boundary = _MCPExposureBoundary(
        backend,  # type: ignore[arg-type]
        FULL_LIFECYCLE_EXPOSURE_PROFILE,
        OAuthToolAuthorizationPolicy(_oauth_config()),
    )
    registry = derive_public_surface_registry(PLUGIN)
    for tool_name in registry["tools"]["names"]:
        result = boundary.invoke(
            tool_name,
            lambda: (_ for _ in ()).throw(
                AssertionError("handler ran before authorization")
            ),
            project_id="project-a",
            lifecycle=tool_name not in CODEX_READ_TOOL_NAMES,
        )
        assert result.isError is True
        assert result.structuredContent["status"] == "AUTHORIZATION_BLOCKED"
        assert result.structuredContent["mutation_performed"] is False
    assert backend.invoke_count == 0


def test_task_classify_denial_precedes_session_or_plan_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EvidenceLaneService(data_root=tmp_path / "store")
    server = create_mcp_server(
        service=service,
        base_url="https://mcp.example",
        oauth_config=_oauth_config(),
    )

    def forbidden_load(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("task_classify read session state before authorization")

    monkeypatch.setattr(service.sessions, "load", forbidden_load)
    tool = server._tool_manager.get_tool("task_classify")
    assert tool is not None
    result = tool.fn(
        project_id="project-a",
        session_id="session-a",
        task_class="modify_code",
        requested_outcome="verify authorization boundary",
        permitted_paths=["tests"],
        permitted_tools=["test"],
        acceptance_checks=["authorization runs first"],
        stop_condition="stop on mismatch",
    )
    assert result.isError is True
    assert result.structuredContent["requested_tool"] == "task_classify"
    assert result.structuredContent["mutation_performed"] is False


def test_public_entry_receipt_is_resealed_after_callback_entry(tmp_path: Path) -> None:
    service = EvidenceLaneService(data_root=tmp_path / "store")
    server = create_mcp_server(service=service)
    tool = server._tool_manager.get_tool("runtime_doctor")
    assert tool is not None

    result = tool.fn()
    entry = result["entry_authorization"]

    assert entry["callback_entered"] is True
    assert len(entry["preflight_receipt_sha256"]) == 64
    _assert_self_hashed_receipt(entry)


def test_task_classify_reuses_one_preflight_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = EvidenceLaneService(data_root=tmp_path / "store")
    server = create_mcp_server(service=service)
    preflight_calls = 0

    def preflight_once(
        self: _MCPExposureBoundary,
        tool_name: str,
        *,
        lifecycle: bool,
        project_id: str | None,
        session_id: str | None,
    ) -> tuple[None, dict[str, Any]]:
        nonlocal preflight_calls
        preflight_calls += 1
        return None, _public_entry_preflight_receipt()

    monkeypatch.setattr(_MCPExposureBoundary, "preflight", preflight_once)
    monkeypatch.setattr(
        service.sessions,
        "load",
        lambda *args, **kwargs: SimpleNamespace(
            metadata={"active_backlog_task_id": ""}
        ),
    )
    monkeypatch.setattr(service, "task_backlog", lambda *args, **kwargs: {"tasks": []})
    monkeypatch.setattr(service, "status", lambda *args, **kwargs: {"status": "PASS"})
    monkeypatch.setattr(
        service.sessions,
        "classify",
        lambda *args, **kwargs: {"status": "PASS"},
    )
    monkeypatch.setattr(
        mcp_server_module,
        "build_project_panel_snapshot",
        lambda **kwargs: {},
    )
    tool = server._tool_manager.get_tool("task_classify")
    assert tool is not None

    result = tool.fn(
        project_id="project-a",
        session_id="session-a",
        task_class="modify_code",
        requested_outcome="reuse one exact preflight",
        permitted_paths=["tests"],
        permitted_tools=["test"],
        acceptance_checks=["one preflight receipt"],
        stop_condition="stop on mismatch",
    )

    assert result["status"] == "PASS"
    assert preflight_calls == 1
    _assert_self_hashed_receipt(result["entry_authorization"])


def test_preflighted_entry_rejects_a_receipt_for_another_tool(tmp_path: Path) -> None:
    boundary = _MCPExposureBoundary(
        EvidenceLaneService(data_root=tmp_path / "store"),
        FULL_LIFECYCLE_EXPOSURE_PROFILE,
    )

    with pytest.raises(EvidenceLaneError) as raised:
        boundary.invoke_preflighted(
            "runtime_doctor",
            lambda: {"status": "PASS"},
            entry_binding=_public_entry_preflight_receipt(),
            lifecycle=False,
        )

    assert raised.value.code == "PUBLIC_ENTRY_PREFLIGHT_RECEIPT_INVALID"


def test_sse_transport_is_removed_even_on_loopback() -> None:
    with pytest.raises(RuntimeError, match="SSE transport is disabled"):
        run_server(transport="sse", host="127.0.0.1")
