from __future__ import annotations

import asyncio
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.cli import _parser
from evidence_lane_plugin.current_route_registry import current_implementation_registry
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.public_surface_registry import (
    PublicSurfaceRegistryError,
    derive_public_surface_registry,
)
from evidence_lane_plugin.registry import ActionRegistry
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def test_public_routes_derive_from_the_started_engine_registry(tmp_path):
    with pytest.raises(PublicSurfaceRegistryError):
        derive_public_surface_registry(ActionRegistry())
    with Engine(tmp_path) as engine:
        surface = derive_public_surface_registry(engine.registry)
        routes = current_implementation_registry(engine.registry)
        assert surface["counts"]["tools"] == len(engine.registry.schemas())
        assert surface["route_digest"] == routes["digest"]
        assert {action["name"] for action in surface["actions"]} == {item["name"] for item in engine.registry.schemas()}
        assert not surface["native_installation_verified"]
        assert not routes["removed_routes_have_fallback"]
        assert all(route["implementation"] == "internal_sdk.dispatch_authenticated" for route in routes["routes"])


@pytest.mark.parametrize("old_command", ["activate-installation", "validate-pv"])
def test_removed_pv_flash_commands_cannot_activate_from_imported_cli(old_command):
    with pytest.raises(SystemExit) as error:
        _parser().parse_args([old_command])
    assert error.value.code == 2


@pytest.mark.parametrize("entrypoint", ["package_launcher", "imported_cli"])
def test_corrected_imported_entrypoint_runs_native_mcp_to_same_engine(tmp_path, entrypoint):
    plugin = Path(__file__).parents[1] / "plugins/evidence-lane-plugin"
    arguments = ([str(plugin / "scripts/run_mcp.py"), "--transport", "stdio"] if entrypoint == "package_launcher"
                 else ["-m", "evidence_lane_plugin.cli", "serve"])
    arguments += ["--runtime-root", str(tmp_path), "--host-profile", "codex_cli"]

    async def exercise():
        parameters = StdioServerParameters(command=sys.executable, args=arguments,
                                           env={"PYTHONPATH": str(plugin / "src")})
        async with (stdio_client(parameters) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session):
            initialized = await session.initialize()
            assert initialized.serverInfo.version == "4.0.0"
            catalog = await session.list_tools()
            assert [tool.name for tool in catalog.tools] == [item["name"] for item in engine.registry.schemas()]
            result = await session.call_tool("engine_health", {"arguments": {}})
            assert result.isError is False
            assert result.structuredContent["result"]["instance_id"] == engine.instance_id
            assert "FLASH" not in json.dumps(result.model_dump())
            assert engine.clients.status()[0]["host_observation"]["client"]["configured_profile"] == "codex_cli"

    with Engine(tmp_path) as engine, LocalEndpoint(engine):
        asyncio.run(exercise())
