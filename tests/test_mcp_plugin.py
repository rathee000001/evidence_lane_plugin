from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.mcp_server import create_mcp_server, run_server
from evidence_lane_plugin.service import EvidenceLaneService
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def test_mcp_tool_inventory_and_annotations(tmp_path: Path) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store")
    )
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "runtime_doctor",
        "session_flash_status",
        "project_register",
        "session_boot",
        "pv_build_initial",
        "task_classify",
        "task_record_activity",
        "task_confirm_source_update",
        "pv_refresh",
        "hil_decide",
        "hil_return_to_accepted",
        "pv_begin_next_turn",
        "session_close",
        "pv_status",
        "search",
        "fetch",
        "pv_summary",
        "pv_query",
        "pv_diff",
        "remote_git_prepare_push",
        "remote_git_execute_push",
    }
    assert by_name["search"].annotations.readOnlyHint is True
    assert by_name["fetch"].annotations.readOnlyHint is True
    assert by_name["session_flash_status"].annotations.readOnlyHint is True
    assert by_name["hil_decide"].annotations.destructiveHint is True
    assert by_name["hil_return_to_accepted"].annotations.destructiveHint is True
    assert by_name["remote_git_execute_push"].annotations.openWorldHint is True
    for tool in tools:
        assert tool.description
        assert tool.inputSchema["type"] == "object"


def test_plugin_manifest_has_evidence_lane_identity_only() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "evidence-lane-plugin"
    manifest = json.loads(
        (plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "evidence-lane-plugin"
    assert manifest["interface"]["displayName"] == "Evidence Lane Plugin"
    assert manifest["author"]["name"] == "Praveen Rathee"
    assert manifest["repository"].endswith("/evidence_lane_plugin")
    assert isinstance(manifest["interface"]["defaultPrompt"], list)
    scan_roots = [
        root / ".agents",
        root / "docs",
        root / "tests",
        plugin / ".codex-plugin",
        plugin / "hooks",
        plugin / "scripts",
        plugin / "skills",
        plugin / "src" / "evidence_lane_plugin",
    ]
    source_files = [
        root / ".env.example",
        root / ".gitignore",
        root / "LICENSE.md",
        root / "pyproject.toml",
        root / "README.md",
        root / "requirements.in",
        root / "SECURITY.md",
        plugin / ".mcp.json",
        plugin / "requirements.lock.txt",
    ]
    source_files.extend(
        path
        for scan_root in scan_roots
        for path in scan_root.rglob("*")
        if path.is_file()
    )
    contents = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in source_files
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
        and path.stat().st_size < 2_000_000
    ).lower()
    forbidden = "data" + "machine"
    assert forbidden not in contents.replace(" ", "")


def test_declared_versions_match_runtime_source_of_truth() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin_manifest = json.loads(
        (
            root / "plugins" / "evidence-lane-plugin" / ".codex-plugin" / "plugin.json"
        ).read_text(encoding="utf-8")
    )
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert plugin_manifest["version"].split("+", 1)[0] == ENGINE_VERSION
    assert project["project"]["version"] == ENGINE_VERSION


def test_session_start_hook_is_advisory(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "session_start.py"
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input='{"source":"startup","session_id":"test"}',
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    payload = json.loads(completed.stdout)
    assert payload["continue"] is True
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert (
        "Never infer HIL approval" in payload["hookSpecificOutput"]["additionalContext"]
    )


def test_real_stdio_transport_lists_tools_and_calls_doctor(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    runner = root / "plugins" / "evidence-lane-plugin" / "scripts" / "run_mcp.py"

    async def exercise() -> None:
        environment = os.environ.copy()
        environment["EVIDENCE_LANE_DATA_ROOT"] = str(tmp_path / "stdio-store")
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(runner), "--transport", "stdio"],
            env=environment,
        )
        async with (
            stdio_client(parameters) as streams,
            ClientSession(*streams) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert any(tool.name == "pv_refresh" for tool in tools.tools)
            result = await session.call_tool("runtime_doctor", {})
            assert result.isError is False
            assert result.structuredContent["status"] == "PASS"

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))


def test_non_loopback_http_fails_closed_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="requires EVIDENCE_LANE_MCP_BEARER_TOKEN"):
        run_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
        )

    monkeypatch.setenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", "test-only-token")
    with pytest.raises(RuntimeError, match="requires an HTTPS"):
        run_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
        )
