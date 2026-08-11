from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import httpx
import pytest
from evidence_lane_plugin.auth import (
    READ_SCOPE,
    REMOTE_GIT_SCOPE,
    WRITE_SCOPE,
    OAuthAuthorizationError,
    OAuthJWTConfig,
    OAuthJWTVerifier,
    OAuthToolAuthorizationPolicy,
)
from evidence_lane_plugin.constants import ENGINE_VERSION
from evidence_lane_plugin.mcp_apps import (
    GOVERNED_PANEL_URI,
    MCP_APP_MIME_TYPE,
    build_project_panel_snapshot,
    governed_panel_html,
)
from evidence_lane_plugin.mcp_server import (
    CHATGPT_PRO_GOVERNED_EXPOSURE_PROFILE,
    CHATGPT_PRO_GOVERNED_TOOL_COUNT,
    CHATGPT_PRO_READ_EXPOSURE_PROFILE,
    CHATGPT_PRO_READ_TOOL_NAMES,
    NATIVE_MCP_SERVER_IDENTITY,
    NATIVE_MCP_TOOL_NAMESPACE,
    create_mcp_server,
    run_server,
)
from evidence_lane_plugin.mcp_stdio_compat import (
    _discovery_fallback,
    _rewrite_namespaced_tool_calls,
)
from evidence_lane_plugin.service import EvidenceLaneService
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.auth.provider import AccessToken
from mcp.types import CallToolResult

from .conftest import build_and_approve_pv1


def test_mcp_tool_inventory_and_annotations(tmp_path: Path) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store")
    )
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == {
        "runtime_doctor",
        "render_runtime_panel",
        "render_project_panel",
        "session_flash_status",
        "runtime_activation_status",
        "lifecycle_transition_law",
        "lane_catalog",
        "mode_classify",
        "source_intake_classify",
        "source_custom_schema_compile",
        "source_intake_schema_configure",
        "source_identity_register",
        "source_graph_build",
        "source_graph_diff",
        "source_graph_impact",
        "source_git_history_build",
        "source_git_commit_impact",
        "source_sqlite_inspect",
        "hil_intent_classify",
        "lane_status",
        "lane_search",
        "lane_fetch",
        "lane_configure_routes",
        "pv_enroll_project",
        "git_sync_selected",
        "project_register",
        "pv_plan_tasks",
        "pv_plan_steer_delta",
        "pv_task_backlog",
        "pv_task_transition",
        "session_boot",
        "session_resume",
        "pv_build_initial",
        "task_classify",
        "task_record_activity",
        "task_confirm_source_update",
        "pv_refresh",
        "task_complete_and_refresh",
        "hil_decide",
        "pv_fuse",
        "pv_state_travel_prepare",
        "pv_state_travel_resume",
        "pv_rollback",
        "hil_return_to_accepted",
        "pv_begin_next_turn",
        "session_close",
        "pv_status",
        "prompt_index_status",
        "search",
        "fetch",
        "pv_summary",
        "pv_query",
        "pv_diff",
        "remote_git_prepare_push",
        "remote_git_execute_push",
        "connector_plugin_register",
        "connector_plugin_drop",
        "connector_plugin_catalog",
        "connector_plugin_settings",
        "connector_plugin_route",
        "storage_connector_inspect",
        "storage_connector_select",
    }
    assert by_name["search"].annotations.readOnlyHint is True
    assert by_name["fetch"].annotations.readOnlyHint is True
    assert by_name["session_flash_status"].annotations.readOnlyHint is True
    assert by_name["runtime_activation_status"].annotations.readOnlyHint is True
    assert by_name["lane_catalog"].annotations.readOnlyHint is True
    assert by_name["render_runtime_panel"].annotations.readOnlyHint is True
    assert by_name["render_project_panel"].annotations.readOnlyHint is True
    assert by_name["mode_classify"].annotations.readOnlyHint is False
    assert by_name["source_sqlite_inspect"].annotations.readOnlyHint is False
    assert by_name["source_custom_schema_compile"].annotations.readOnlyHint is False
    assert by_name["source_intake_schema_configure"].annotations.readOnlyHint is False
    assert by_name["source_identity_register"].annotations.readOnlyHint is False
    assert by_name["source_graph_build"].annotations.readOnlyHint is False
    assert by_name["source_graph_diff"].annotations.readOnlyHint is False
    assert by_name["source_graph_impact"].annotations.readOnlyHint is False
    assert by_name["source_git_history_build"].annotations.readOnlyHint is False
    assert by_name["source_git_commit_impact"].annotations.readOnlyHint is False
    assert by_name["hil_intent_classify"].annotations.readOnlyHint is False
    assert by_name["pv_task_backlog"].annotations.readOnlyHint is True
    assert by_name["pv_task_transition"].annotations.readOnlyHint is False
    assert by_name["pv_task_transition"].annotations.destructiveHint is False
    assert by_name["hil_decide"].annotations.destructiveHint is True
    assert by_name["pv_rollback"].annotations.destructiveHint is True
    assert by_name["pv_fuse"].annotations.destructiveHint is True
    assert by_name["pv_state_travel_prepare"].annotations.destructiveHint is False
    assert by_name["pv_state_travel_resume"].annotations.destructiveHint is False
    assert by_name["hil_return_to_accepted"].annotations.destructiveHint is True
    assert by_name["remote_git_execute_push"].annotations.openWorldHint is True
    assert (
        "preferred_plugin_id"
        in by_name["connector_plugin_route"].inputSchema["properties"]
    )
    for tool in tools:
        assert tool.title
        assert tool.description
        assert tool.annotations is not None
        assert tool.inputSchema["type"] == "object"


def test_chatgpt_pro_profile_exposes_all_actions_and_blocks_every_write(
    tmp_path: Path,
) -> None:
    service = EvidenceLaneService(data_root=tmp_path / "chatgpt-pro-store")
    server = create_mcp_server(
        service=service,
        exposure_profile=CHATGPT_PRO_GOVERNED_EXPOSURE_PROFILE,
    )
    tools = asyncio.run(server.list_tools())
    names = tuple(sorted(tool.name for tool in tools))
    assert len(names) == CHATGPT_PRO_GOVERNED_TOOL_COUNT == 62
    assert set(CHATGPT_PRO_READ_TOOL_NAMES).issubset(names)
    assert sum(tool.annotations.readOnlyHint is True for tool in tools) == 21
    assert sum(tool.annotations.readOnlyHint is False for tool in tools) == 41
    for visible_but_blocked in (
        "pv_build_initial",
        "pv_refresh",
        "pv_fuse",
        "pv_rollback",
        "pv_task_transition",
        "remote_git_execute_push",
        "connector_plugin_register",
        "storage_connector_select",
    ):
        assert visible_but_blocked in names
    blocked_tool = next(
        tool for tool in server._tool_manager.list_tools()  # type: ignore[attr-defined]
        if tool.name == "pv_build_initial"
    )
    blocked = blocked_tool.fn(project_id="unregistered", session_id="none")
    assert blocked == {
        "schema": "evidence-lane.chatgpt-pro-unavailable-action.v1",
        "status": "UNAVAILABLE_ON_CHATGPT_PRO",
        "requested_tool": "pv_build_initial",
        "exposure_profile": "CHATGPT_PRO_GOVERNED",
        "lifecycle_effect": "NONE",
        "mutation_performed": False,
        "pointer_moved": False,
        "simulated": False,
        "visible_controls": [
            "Boot",
            "Rollback",
            "Build",
            "Refresh",
            "Mode",
            "Source Intake",
        ],
        "reason": (
            "The ChatGPT Pro connection exposes this governed action for "
            "discoverability but does not have lifecycle-write authority."
        ),
        "required_host": (
            "CODEX_FULL_LIFECYCLE_OR_OTHER_EXPLICITLY_WRITE_CAPABLE_HOST"
        ),
    }
    assert server._evidence_lane_exposure_profile == "CHATGPT_PRO_GOVERNED"  # type: ignore[attr-defined]
    instructions = server._mcp_server.instructions
    assert "complete Evidence Lane action catalog" in instructions
    assert "Exactly twenty-one read operations execute" in instructions
    assert "UNAVAILABLE_ON_CHATGPT_PRO" in instructions
    assert "whole product instead of turning the conversation into a code review" in (
        instructions
    )
    assert "Project Mutation sector" in instructions
    assert "Codex remains the separate Git-installed full-lifecycle host" in instructions
    assert "Vercel and the owned HTTPS domain serve this ChatGPT read path only" in instructions
    assert "cannot create or resume a runtime session, Build, Refresh" in instructions
    assert "CHATGPT_PRO_READ_ATTACH" in instructions
    assert "Meshy" in instructions and "Three.js/WebGL" in instructions


def test_chatgpt_pro_profile_rejects_a_conflicting_manual_allowlist(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="complete registered tool inventory"):
        create_mcp_server(
            service=EvidenceLaneService(data_root=tmp_path / "conflict-store"),
            exposure_profile=CHATGPT_PRO_GOVERNED_EXPOSURE_PROFILE,
            allowed_tool_names='["runtime_doctor"]',
        )


def test_legacy_chatgpt_read_profile_aliases_to_governed_visible_surface(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "legacy-alias-store"),
        exposure_profile=CHATGPT_PRO_READ_EXPOSURE_PROFILE,
    )
    tools = asyncio.run(server.list_tools())
    assert len(tools) == CHATGPT_PRO_GOVERNED_TOOL_COUNT
    assert server._evidence_lane_exposure_profile == "CHATGPT_PRO_GOVERNED"  # type: ignore[attr-defined]


def test_modern_discovery_probe_receives_exact_legacy_fallback() -> None:
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "discover-test",
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28"
                }
            },
        }
    )
    response = _discovery_fallback(request)
    assert response is not None
    assert response.id == "discover-test"
    assert response.error.code == -32601
    assert response.error.message == "Method not found"
    assert _discovery_fallback('{"jsonrpc":"2.0","id":1,"method":"tools/list"}') is None


def test_all_registered_tools_accept_generated_evidence_lane_namespaces(
    tmp_path: Path,
) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "namespace-store")
    )
    tools = asyncio.run(server.list_tools())
    canonical_names = frozenset(tool.name for tool in tools)
    assert len(canonical_names) == CHATGPT_PRO_GOVERNED_TOOL_COUNT == 62

    for namespace in (
        "evidence_lane",
        "evidence_lane_7fb71d7f",
        "evidence_lane_f1c20919f536",
    ):
        for canonical_name in canonical_names:
            assert server._tool_manager.get_tool(  # type: ignore[attr-defined]
                f"{namespace}.{canonical_name}"
            ) is server._tool_manager.get_tool(canonical_name)  # type: ignore[attr-defined]
            request = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": f"{namespace}:{canonical_name}",
                    "method": "tools/call",
                    "params": {
                        "name": f"{namespace}.{canonical_name}",
                        "arguments": {},
                    },
                }
            )
            rewritten = json.loads(
                _rewrite_namespaced_tool_calls(request, canonical_names)
            )
            assert rewritten["params"]["name"] == canonical_name
            assert rewritten["params"]["arguments"] == {}

    bare_request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "bare",
            "method": "tools/call",
            "params": {"name": "runtime_doctor", "arguments": {}},
        }
    )
    assert (
        _rewrite_namespaced_tool_calls(bare_request, canonical_names)
        == bare_request
    )

    for rejected_name in (
        "codex_apps.runtime_doctor",
        "google_drive.runtime_doctor",
        "plugin_runtime.runtime_doctor",
        "evidence_lane_chatgpt_read_only.runtime_doctor",
        "evidence_lane_v1_2_hil.runtime_doctor",
        "evidence_lane.not_a_registered_tool",
        "evidence_lane_7fb71d7f.not_a_registered_tool",
        "arbitrary_namespace.render_project_panel",
    ):
        assert server._tool_manager.get_tool(rejected_name) is None  # type: ignore[attr-defined]
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": rejected_name,
                "method": "tools/call",
                "params": {"name": rejected_name, "arguments": {}},
            }
        )
        assert _rewrite_namespaced_tool_calls(request, canonical_names) == request


def test_native_route_receipt_seals_the_exact_unique_catalog(tmp_path: Path) -> None:
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "native-route-store")
    )
    canonical_names = frozenset(
        tool.name for tool in asyncio.run(server.list_tools())
    )
    receipt = server._evidence_lane_native_route_receipt  # type: ignore[attr-defined]
    assert receipt["status"] == "PASS"
    assert receipt["server_identity"] == NATIVE_MCP_SERVER_IDENTITY == "evidence-lane"
    assert receipt["canonical_tool_namespace"] == NATIVE_MCP_TOOL_NAMESPACE
    assert receipt["tool_count"] == CHATGPT_PRO_GOVERNED_TOOL_COUNT == 62
    assert receipt["tool_names_unique"] is True
    assert receipt["runtime_global_tool_count"] == 6
    assert receipt["project_scoped_tool_count"] == 56
    assert receipt["project_route_argument"] == "project_id"
    assert receipt["project_route_argument_required"] is True
    assert receipt["project_route_schema_status"] == "PASS"
    assert receipt["project_scoped_tools_missing_project_id"] == []
    assert receipt["transport_project_binding"] == "NONE_TRANSPORT_ONLY"
    assert receipt["cross_project_fallback_allowed"] is False
    assert receipt["surface_placement"] == {
        "codex": "NATIVE_PLUGIN_FULL_LIFECYCLE_ONLY",
        "chatgpt": "BROWSER_PLUGIN_CONNECTOR_OR_PRIVATE_DEV_TUNNEL_ONLY",
        "chatgpt_connector_inside_codex_allowed": False,
    }
    assert re.fullmatch(r"[0-9A-F]{64}", receipt["tool_catalog_sha256"])
    assert receipt["mcp_apps_resource_uri"] == GOVERNED_PANEL_URI
    assert receipt["host_display_namespace_is_authority"] is False
    assert receipt["catalog_reload_required_after_package_change"] is True
    assert "chatgpt_connector" in receipt["rejected_lifecycle_surfaces"]

    doctor_tool = server._tool_manager.get_tool("runtime_doctor")  # type: ignore[attr-defined]
    assert doctor_tool is not None
    doctor = doctor_tool.fn()
    assert doctor["status"] == "PASS"
    assert doctor["data"]["mcp_route_identity"] == receipt
    assert doctor["data"]["store_routing"]["status"] == "PASS"
    assert doctor["data"]["store_routing"]["registered_project_count"] == 0

    batch = json.dumps(
        [
            {
                "jsonrpc": "2.0",
                "id": "one",
                "method": "tools/call",
                "params": {
                    "name": "evidence_lane.runtime_doctor",
                    "arguments": {},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": "two",
                "method": "tools/call",
                "params": {
                    "name": "evidence_lane_7fb71d7f.pv_status",
                    "arguments": {"project_id": "example"},
                },
            },
        ]
    )
    rewritten_batch = json.loads(
        _rewrite_namespaced_tool_calls(batch, canonical_names)
    )
    assert [item["params"]["name"] for item in rewritten_batch] == [
        "runtime_doctor",
        "pv_status",
    ]


def test_packaged_skill_tool_references_match_live_canonical_catalog(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "evidence-lane-plugin"
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "skill-catalog-store")
    )
    canonical_names = frozenset(
        tool.name for tool in asyncio.run(server.list_tools())
    )
    assert len(canonical_names) == CHATGPT_PRO_GOVERNED_TOOL_COUNT == 62
    tool_families = frozenset(name.partition("_")[0] for name in canonical_names)
    skill_paths = sorted((plugin / "skills").glob("*/SKILL.md"))
    command_paths = sorted((plugin / "commands").glob("*.md"))
    contract_paths = [*skill_paths, *command_paths]
    assert len(skill_paths) == 15
    assert [path.name for path in command_paths] == ["evi-plan.md"]

    referenced_tools: set[str] = set()
    for path in contract_paths:
        content = path.read_text(encoding="utf-8")
        assert re.search(
            r"evidence_lane(?:_[a-z0-9]+)*\.[a-z][a-z0-9_]+",
            content,
        ) is None, path
        identifiers = re.findall(r"`([a-z][a-z0-9_]+)`", content)
        for identifier in identifiers:
            if identifier.partition("_")[0] not in tool_families:
                continue
            assert identifier in canonical_names, (path, identifier)
            referenced_tools.add(identifier)

    assert {
        "runtime_doctor",
        "session_flash_status",
        "runtime_activation_status",
        "render_runtime_panel",
        "render_project_panel",
        "pv_status",
        "storage_connector_inspect",
        "storage_connector_select",
        "pv_state_travel_prepare",
        "pv_state_travel_resume",
        "pv_fuse",
    }.issubset(referenced_tools)


def test_mcp_apps_resource_and_render_tool_metadata(tmp_path: Path) -> None:
    public_site = "https://preview.example.test"
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store"),
        public_site_url=public_site,
    )
    resources = asyncio.run(server.list_resources())
    assert len(resources) == 1
    resource = resources[0]
    assert str(resource.uri) == GOVERNED_PANEL_URI
    assert GOVERNED_PANEL_URI.endswith("/governed-console-v3.html")
    assert resource.mimeType == MCP_APP_MIME_TYPE
    assert resource.meta == {
        "ui": {
            "prefersBorder": True,
            "domain": public_site,
            "csp": {"connectDomains": [], "resourceDomains": []},
        }
    }
    contents = list(asyncio.run(server.read_resource(GOVERNED_PANEL_URI)))
    assert len(contents) == 1
    assert contents[0].mime_type == MCP_APP_MIME_TYPE
    assert "ui/notifications/tool-result" in contents[0].content
    assert 'method: "ui/initialize"' in contents[0].content
    assert 'method: "ui/notifications/initialized"' in contents[0].content
    assert 'protocolVersion: "2026-01-26"' in contents[0].content
    assert "window.openai" in contents[0].content
    assert "setWidgetState" in contents[0].content
    assert "innerHTML" not in contents[0].content
    assert "Prompt template (host-owned; never auto-submitted)" in contents[0].content
    assert "hil.choices" in contents[0].content

    by_name = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    for name in ("render_runtime_panel", "render_project_panel"):
        tool = by_name[name]
        assert tool.meta["ui"] == {
            "resourceUri": GOVERNED_PANEL_URI,
            "visibility": ["model", "app"],
        }
        assert tool.meta["openai/outputTemplate"] == GOVERNED_PANEL_URI
        assert tool.outputSchema is not None
        assert tool.outputSchema["type"] == "object"


def test_project_panel_always_explains_exact_six_way_hil_without_mutation() -> None:
    snapshot = build_project_panel_snapshot(
        project_id="example",
        project_status={
            "status": "PASS",
            "persistent_state_envelope": {
                "accepted_pv": "PV10",
                "pointer_generation": 10,
                "pending_candidate": None,
                "pending_hil": False,
            },
            "active_session": {"state": "TASK_CLASSIFIED"},
            "lane_projection": {
                "authority": "ACCEPTED_IMMUTABLE_AUTHORITY",
                "pv_ref": "PV10",
                "canonical_lane_count": 18,
                "emitted_lane_count": 1,
                "absent_lane_ids": ["local_code"],
                "bundle_sha256": "A" * 64,
                "topology_status": "PASS",
                "lanes": [
                    {
                        "id": "github_code",
                        "label": "GitHub code",
                        "value": "EMITTED | PASS | 4 sealed files | accepted PV10",
                    }
                ],
            },
            "project_route": {
                "relative_project_route": "projects/example",
                "storage_mode": "AUTO",
                "active_host_profile": "CODEX_LOCAL_PC_OR_LAPTOP",
            },
        },
        public_site_url="https://preview.example.test",
    )
    hil = snapshot["hil"]
    assert hil["pending"] is False
    assert hil["decision_state"] == "NO_PENDING_CANDIDATE"
    assert [choice["token"] for choice in hil["choices"]] == [
        "APPROVE",
        "APPROVE_WITH_DELTA",
        "MORE_RESEARCH",
        "ROLLBACK",
        "REJECT",
        "FAIL",
    ]
    assert {choice["availability"] for choice in hil["choices"]} == {
        "INFORMATION_ONLY_NO_PENDING_CANDIDATE"
    }
    assert all(choice["consequence"] for choice in hil["choices"])
    assert hil["suggested_next_prompt"] is None
    assert hil["prompt_template"].startswith("/evi-build")
    assert hil["exact_case_sensitive_token_required"] is True
    assert hil["auto_submit"] is False
    assert hil["render_changes_authority"] is False
    assert snapshot["lane_projection"] == {
        "authority": "ACCEPTED_IMMUTABLE_AUTHORITY",
        "pv_ref": "PV10",
        "canonical_lane_count": 18,
        "emitted_lane_count": 1,
        "absent_lane_ids": ["local_code"],
        "bundle_sha256": "A" * 64,
        "topology_status": "PASS",
    }
    assert snapshot["lanes"][0]["id"] == "github_code"


def test_mcp_apps_view_executes_initialize_and_tool_result_lifecycle() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the executable MCP Apps View contract")

    panel_html = governed_panel_html("https://preview.example.test")
    script = panel_html.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = f"""
const vm = require("node:vm");
const operations = [];
const sent = [];
let messageHandler = null;

class Element {{
  constructor(id = "") {{
    this.id = id;
    this.children = [];
    this.dataset = {{}};
    this.textContent = "";
    this.attributes = {{}};
    this.replaceCount = 0;
  }}
  append(...items) {{ this.children.push(...items); }}
  replaceChildren(...items) {{
    this.children = [...items];
    this.replaceCount += 1;
  }}
  setAttribute(name, value) {{ this.attributes[name] = value; }}
  addEventListener() {{}}
  get childElementCount() {{ return this.children.length; }}
}}

const elements = Object.fromEntries(
  ["title", "summary", "status", "content", "links"].map((id) => [id, new Element(id)]),
);
const buttons = ["overview", "lanes", "hil"].map((tab) => {{
  const button = new Element();
  button.dataset.tab = tab;
  return button;
}});
const parentWindow = {{
  postMessage(message, origin) {{
    operations.push("post:" + String(message.method || "response"));
    sent.push({{ message, origin }});
  }},
}};
const widgetStates = [];
const windowObject = {{
  parent: parentWindow,
  openai: {{
    widgetState: {{ activeTab: "overview" }},
    toolOutput: {{
      schema: "evidence-lane.mcp-app-panel.v1",
      title: "Compatibility snapshot",
      summary: "Loaded from window.openai",
      status: "PASS",
      facts: [{{ label: "Release", value: "1.5.0" }}],
      lanes: [],
      links: [],
    }},
    setWidgetState(state) {{ widgetStates.push(state); }},
  }},
  addEventListener(type, handler) {{
    if (type === "message") {{
      operations.push("listener:message");
      messageHandler = handler;
    }}
  }},
}};
const documentObject = {{
  getElementById(id) {{ return elements[id]; }},
  querySelectorAll() {{ return buttons; }},
  createElement() {{ return new Element(); }},
}};
const sandbox = {{
  window: windowObject,
  document: documentObject,
  URL,
  console,
}};

vm.runInNewContext({json.dumps(script)}, sandbox);
if (typeof messageHandler !== "function") throw new Error("message listener missing");

const initMessages = sent.filter((entry) => entry.message.method === "ui/initialize");
if (initMessages.length !== 1) throw new Error("expected exactly one initialize request");
const init = initMessages[0];
if (init.origin !== "*") throw new Error("unexpected postMessage target origin");
if (init.message.params.appInfo.name !== "Evidence Lane") throw new Error("wrong app name");
if (init.message.params.appInfo.version !== "2.0.0") throw new Error("wrong app version");
if (Object.keys(init.message.params.appCapabilities).length !== 0) throw new Error("wrong app capabilities");
if (init.message.params.protocolVersion !== "2026-01-26") throw new Error("wrong protocol version");
if (operations.indexOf("listener:message") > operations.indexOf("post:ui/initialize")) {{
  throw new Error("initialize sent before receiver registration");
}}

messageHandler({{ source: {{}}, data: {{ jsonrpc: "2.0", id: init.message.id, result: {{}} }} }});
messageHandler({{ source: parentWindow, data: {{ jsonrpc: "2.0", id: "foreign-id", result: {{}} }} }});
if (sent.some((entry) => entry.message.method === "ui/notifications/initialized")) {{
  throw new Error("foreign or mismatched response initialized the app");
}}
messageHandler({{ source: parentWindow, data: {{ jsonrpc: "2.0", id: init.message.id, result: {{}} }} }});
messageHandler({{ source: parentWindow, data: {{ jsonrpc: "2.0", id: init.message.id, result: {{}} }} }});
const initializedMessages = sent.filter((entry) => entry.message.method === "ui/notifications/initialized");
if (initializedMessages.length !== 1) throw new Error("initialized notification was not exactly-once");

const beforeDuplicate = elements.content.replaceCount;
const result = {{
  structuredContent: {{
    schema: "evidence-lane.mcp-app-panel.v1",
    title: "Governed runtime",
    summary: "Verified structured result",
    status: "PASS",
    facts: [{{ label: "Engine commit", value: "abc123" }}],
    lanes: [],
    links: [],
  }},
}};
messageHandler({{
  source: parentWindow,
  data: {{ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }},
}});
const afterFirstResult = elements.content.replaceCount;
messageHandler({{
  source: parentWindow,
  data: {{ jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result }},
}});
if (elements.title.textContent !== "Governed runtime") throw new Error("structured title not rendered");
if (elements.summary.textContent !== "Verified structured result") throw new Error("structured summary not rendered");
if (elements.status.textContent !== "PASS") throw new Error("structured status not rendered");
if (afterFirstResult !== beforeDuplicate + 1) throw new Error("first structured result did not render");
if (elements.content.replaceCount !== afterFirstResult) throw new Error("duplicate result rendered twice");
if (!widgetStates.length) throw new Error("window.openai widget-state compatibility missing");
if (operations[0] !== "listener:message") throw new Error("receiver was not the first bridge operation");
"""
    completed = subprocess.run(
        [node, "-e", harness],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_mcp_server_advertises_exact_release_and_cube_icon(tmp_path: Path) -> None:
    public_site = "https://preview.example.test"
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "store"),
        public_site_url=public_site,
    )
    identity = server._mcp_server
    assert identity.version == ENGINE_VERSION == "2.0.0"
    assert str(identity.website_url) == public_site
    assert identity.icons is not None
    assert len(identity.icons) == 1
    icon = identity.icons[0]
    assert icon.src == f"{public_site}/evidence-lane-icon.png"
    assert icon.mimeType == "image/png"
    assert icon.sizes == ["256x256"]


def test_plugin_manifest_has_evidence_lane_identity_only() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "evidence-lane-plugin"
    manifest = json.loads(
        (plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "evidence-lane-plugin"
    assert manifest["interface"]["displayName"] == "Evidence Lane"
    assert manifest["author"]["name"] == "Praveen Rathee"
    assert manifest["repository"].endswith("/evidence_lane_plugin")
    assert "apps" not in manifest
    assert manifest["mcpServers"] == "./.mcp.json"
    assert manifest["interface"]["logo"] == "./assets/evidence-lane-icon.png"
    assert manifest["interface"]["composerIcon"] == ("./assets/evidence-lane-icon.png")
    compact_icon = plugin / "assets" / "evidence-lane-icon.png"
    assert compact_icon.is_file()
    assert compact_icon.stat().st_size <= 10 * 1024
    assert compact_icon.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "hooks" not in manifest
    assert isinstance(manifest["interface"]["defaultPrompt"], list)
    assert 1 <= len(manifest["interface"]["defaultPrompt"]) <= 3
    assert all(len(prompt) <= 128 for prompt in manifest["interface"]["defaultPrompt"])
    assert any(
        "six-way HIL" in prompt for prompt in manifest["interface"]["defaultPrompt"]
    )
    hooks = json.loads((plugin / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert set(hooks["hooks"]) == {"SessionStart", "UserPromptSubmit", "Stop"}
    stop_handler = hooks["hooks"]["Stop"][0]["hooks"][0]
    assert "stop_response.py" in stop_handler["command"]
    stop_source = (plugin / "hooks" / "stop_response.py").read_text(encoding="utf-8")
    assert '"decision"' not in stop_source
    assert '"continue": True' in stop_source
    assert not (plugin / ".app.json").exists()
    chatgpt_connection = json.loads(
        (plugin / "chatgpt-app-connection.json").read_text(encoding="utf-8")
    )
    assert chatgpt_connection["host"] == "CHATGPT"
    assert chatgpt_connection["delivery"] == "REGISTERED_REMOTE_MCP_ONLY"
    assert {
        key: chatgpt_connection["connection"][key]
        for key in (
            "name",
            "id",
            "mcp_endpoint",
            "exposure_profile",
            "visible_tool_count",
            "active_read_tool_count",
            "fail_closed_write_tool_count",
        )
    } == {
        "name": "evidence-lane-chatgpt-governed",
        "id": "plugin_asdk_app_6a7743d238e48191be8b69c87fb71d7f",
        "mcp_endpoint": "https://mcp.evidencelane.org/mcp",
        "exposure_profile": "CHATGPT_PRO_GOVERNED",
        "visible_tool_count": 62,
        "active_read_tool_count": 21,
        "fail_closed_write_tool_count": 41,
    }
    assert chatgpt_connection["connection"]["genuinely_write_capable_tool_count"] == 0
    assert chatgpt_connection["publisher"]["display_name"] == "Praveen Rathee"
    assert chatgpt_connection["presentation"]["required_visible_release"] == "1.5.0"
    assert chatgpt_connection["skills"]["codex_package_skill_count"] == 15
    assert chatgpt_connection["skills"]["verified_live_chatgpt_visible_skill_count"] == 0
    assert chatgpt_connection["install_allowed"] is False
    assert chatgpt_connection["codex_install_manifest_reference"] is False
    assert chatgpt_connection["google_drive_bundled"] is False
    assert chatgpt_connection["direct_stdio_fallback_allowed"] is False
    marketplace = json.loads(
        (root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    assert marketplace["name"] == "evidence-lane-github"
    assert marketplace["interface"]["displayName"] == "Evidence Lane GitHub"
    scan_roots = [
        root / ".agents",
        root / "docs",
        root / "tests",
        plugin / ".codex-plugin",
        plugin / "commands",
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
        plugin / "chatgpt-app-connection.json",
        plugin / "pyproject.toml",
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
    plugin_project = tomllib.loads(
        (root / "plugins" / "evidence-lane-plugin" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )
    assert plugin_manifest["version"].split("+", 1)[0] == ENGINE_VERSION
    assert project["project"]["version"] == ENGINE_VERSION
    assert plugin_project["project"]["version"] == ENGINE_VERSION
    assert (
        plugin_project["project"]["dependencies"] == project["project"]["dependencies"]
    )
    assert plugin_project["tool"]["setuptools"]["package-dir"] == {"": "src"}


def test_session_start_hook_is_advisory(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "session_start.py"
    environment = os.environ.copy()
    store = tmp_path / "persistent-store"
    project = store / "projects" / "hook-plan-test"
    (project / "accepted").mkdir(parents=True)
    (project / "active_pointer.json").write_text(
        json.dumps(
            {
                "accepted_pv": None,
                "accepted_manifest_sha256": None,
                "generation": 0,
            }
        ),
        encoding="utf-8",
    )
    (project / "task_backlog.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"task_id": "queued", "status": "QUEUED"},
                    {"task_id": "done", "status": "DONE"},
                    {"task_id": "accepted", "status": "ACCEPTED"},
                    {"task_id": "dropped", "status": "DROPPED"},
                ]
            }
        ),
        encoding="utf-8",
    )
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(store)
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input='{"source":"startup","session_id":"test"}',
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    payload = json.loads(completed.stdout)
    assert payload["continue"] is True
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert (
        "Never infer HIL approval" in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "PERSISTENT_STATE_ENVELOPE="
        in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "PLUGIN_RUNTIME_ENVELOPE=" in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        '"version_state":"FRESH"' in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert (
        "APPROVE is the only candidate promotion"
        in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert "HOST_SESSION_ID=test" in payload["hookSpecificOutput"]["additionalContext"]
    assert (
        "visibly render the engine's suggested_next_prompt"
        in payload["hookSpecificOutput"]["additionalContext"]
    )
    assert "do not auto-submit it" in payload["hookSpecificOutput"]["additionalContext"]
    context_lines = payload["hookSpecificOutput"]["additionalContext"].splitlines()
    runtime = json.loads(
        next(
            line.removeprefix("PLUGIN_RUNTIME_ENVELOPE=")
            for line in context_lines
            if line.startswith("PLUGIN_RUNTIME_ENVELOPE=")
        )
    )
    assert runtime["release_policy_state"] == "FRESH"
    assert runtime["host_storage_tunnel_matrix"]["routing_axes_independent"] is True
    assert runtime["host_storage_tunnel_matrix"]["headless_api"][
        "tunnel_requirement"
    ] == "NOT_REQUIRED_FOR_API_LAYER"
    persistent = json.loads(
        next(
            line.removeprefix("PERSISTENT_STATE_ENVELOPE=")
            for line in context_lines
            if line.startswith("PERSISTENT_STATE_ENVELOPE=")
        )
    )
    assert persistent["state"] == "EXACT_HOST_SESSION_PROJECT_BINDING_REQUIRED"
    assert persistent["projects"] == []
    assert persistent["project_count_returned"] == 0
    assert persistent["cross_project_disclosure"] is False
    change_display = json.loads(
        next(
            line.removeprefix("PERSISTENT_CHANGE_DISPLAY=")
            for line in context_lines
            if line.startswith("PERSISTENT_CHANGE_DISPLAY=")
        )
    )
    assert change_display == {
        "state": "UNAVAILABLE_UNTIL_EXACT_STRICT_PROJECT_TASK_BINDING",
        "cross_project_disclosure": False,
        "composer_mutated": False,
        "auto_submit": False,
    }


def test_prompt_hook_indexes_entry_without_raw_prompt_and_resolves_rollback(
    service,
    source_repository: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    turn_task = {
        "task_id": "prompt-index-v2-turn-control",
        "task_class": "verify_result",
        "requested_outcome": "Seal a no-source-change PV2 and verify governed prompt rollback.",
        "permitted_paths": [
            "plugins/evidence-lane-plugin/hooks/**",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/codex_turn_control.py",
            "plugins/evidence-lane-plugin/src/evidence_lane_plugin/prompt_index.py",
            "tests/**",
        ],
        "permitted_tools": ["repository_read", "test"],
        "acceptance_checks": [
            "Every visible turn has one PREPARE and one COMMIT.",
            "PROMPT rollback resolves the v2 secret-redacted projection.",
        ],
        "stop_condition": "Stop at the physically final HIL row.",
    }
    final_hil = {
        **turn_task,
        "task_id": "prompt-index-v2-final-hil",
        "requested_outcome": "Present the physically final six-way HIL.",
        "panel_role": "PHYSICALLY_FINAL_HIL",
    }
    service.plan_tasks(
        "book-faires",
        tasks=[turn_task, final_hil],
        planned_by="human-test",
        plan_id="prompt-index-v2-plan",
    )
    service.classify_mode(
        "book-faires",
        "Verify the bounded prompt index and rollback contract.",
        explicit_modes=["AL", "RS", "PL"],
        session_id=session_id,
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=turn_task["task_class"],
        requested_outcome=turn_task["requested_outcome"],
        permitted_paths=turn_task["permitted_paths"],
        permitted_tools=turn_task["permitted_tools"],
        acceptance_checks=turn_task["acceptance_checks"],
        stop_condition=turn_task["stop_condition"],
        backlog_task_id=turn_task["task_id"],
    )
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    fused = service.fuse(
        "book-faires",
        session_id,
        approval="APPROVE",
        decided_by="human-test",
        decision_id="decision_prompt_index_pv2",
    )
    handoff = fused["state_travel_handoff"]["state_travel"]
    service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="host-session-prompt-index-bootstrap",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"source": "fresh-prompt-index-task"},
    )
    post_fuse_task = {
        **turn_task,
        "task_id": "prompt-index-v2-post-fuse-turn-control",
        "requested_outcome": (
            "Verify governed prompt rollback from the accepted PV2 entry."
        ),
    }
    service.record_steer_delta(
        "book-faires",
        delta_text=(
            "Add the accepted-PV2 prompt rollback verification before the final HIL."
        ),
        actor="human-test",
        delta_id="prompt-index-v2-post-fuse-delta",
        new_task_contract=post_fuse_task,
    )
    service.classify_mode(
        "book-faires",
        "Verify the accepted-PV2 prompt index and rollback contract.",
        explicit_modes=["AL", "RS", "PL"],
        session_id=session_id,
    )
    service.sessions.classify(
        "book-faires",
        session_id,
        task_class=post_fuse_task["task_class"],
        requested_outcome=post_fuse_task["requested_outcome"],
        permitted_paths=post_fuse_task["permitted_paths"],
        permitted_tools=post_fuse_task["permitted_tools"],
        acceptance_checks=post_fuse_task["acceptance_checks"],
        stop_condition=post_fuse_task["stop_condition"],
        backlog_task_id=post_fuse_task["task_id"],
    )
    execution_profile = {
        "model": "gpt-5.6-sol",
        "submodel": "sol",
        "reasoning_effort": "ultra",
        "reasoning_speed": "standard",
        "service_tier": "standard",
    }
    active_handoff = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": execution_profile},
    )["state_travel"]
    service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=active_handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="host-session-prompt-index-pv2",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"execution_profile": execution_profile},
    )

    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "prompt_submit.py"
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
    secret_prompt = "Rollback checkpoint token=super-secret-value"
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "host-session-prompt-index-pv2",
                "turn_id": "turn-prompt-index-1",
                "cwd": str(source_repository),
                "prompt": secret_prompt,
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    hook_payload = json.loads(completed.stdout)
    context = hook_payload["hookSpecificOutput"]["additionalContext"]
    indexed = json.loads(context.removeprefix("EVIDENCE_LANE_PROMPT_ENTRY="))
    assert hook_payload["continue"] is True, json.dumps(indexed, indent=2)
    assert indexed["state"] == "PREPARED_NOT_COMMITTED"
    assert indexed["prompt_index"] == 1
    assert indexed["entry_pv"] == "PV2"
    records = list((service.store.root / "prompt-index").rglob("*.json"))
    assert len(records) == 1
    stored = records[0].read_text(encoding="utf-8")
    assert secret_prompt not in stored
    assert "super-secret-value" not in stored
    assert "Rollback checkpoint [REDACTED]" in stored
    assert '"redacted_visible_prompt_stored":true' in stored

    stop_hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "stop_response.py"
    secret_response = "Candidate sealed. sk-proj-THIS_IS_A_FAKE_TEST_KEY_1234567890"
    stopped = subprocess.run(
        [sys.executable, str(stop_hook)],
        input=json.dumps(
            {
                "session_id": "host-session-prompt-index-pv2",
                "turn_id": "turn-prompt-index-1",
                "cwd": str(source_repository),
                "last_assistant_message": secret_response,
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    stop_payload = json.loads(stopped.stdout)
    assert stop_payload["continue"] is True
    committed_notice = json.loads(
        stop_payload["systemMessage"].removeprefix(
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY="
        )
    )
    assert committed_notice["turn_receipt"]["state"] == "COMMITTED"
    assert committed_notice["phase"] == "TURN_COMMIT"
    assert "decision" not in stop_payload
    response_records = list((service.store.root / "response-index").rglob("*.json"))
    assert len(response_records) == 1
    response_record = json.loads(response_records[0].read_text(encoding="utf-8"))
    assert secret_response not in json.dumps(response_record)
    assert "THIS_IS_A_FAKE_TEST_KEY" not in json.dumps(response_record)
    assert response_record["visible_assistant_response_after_redaction"] == (
        "Candidate sealed. [REDACTED]"
    )
    assert response_record["prompt_record_sha256"] == indexed["record_sha256"]
    assert response_record["private_reasoning_stored"] is False
    assert response_record["hook_continuation_requested"] is False
    lineage_path = (
        service.store.project_root("book-faires") / "lineage" / f"{session_id}.jsonl"
    )
    lineage_events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    response_events = [
        event
        for event in lineage_events
        if event["event_type"] == "turn.visible_assistant_response"
    ]
    assert len(response_events) == 1
    assert (
        response_events[0]["visible_payload"]["prompt_record_sha256"]
        == indexed["record_sha256"]
    )
    assert response_events[0]["visible_payload"]["private_reasoning_excluded"] is True
    assert response_events[0]["visible_payload"]["hook_continuation_requested"] is False
    repeated = subprocess.run(
        [sys.executable, str(stop_hook)],
        input=json.dumps(
            {
                "session_id": "host-session-prompt-index-pv2",
                "turn_id": "turn-prompt-index-1",
                "cwd": str(source_repository),
                "last_assistant_message": secret_response,
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    repeated_payload = json.loads(repeated.stdout)
    assert repeated_payload["continue"] is True
    repeated_notice = json.loads(
        repeated_payload["systemMessage"].removeprefix(
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY="
        )
    )
    assert repeated_notice["turn_receipt"]["state"] == (
        "COMMITTED_IDEMPOTENT_REUSE"
    )
    assert len(list((service.store.root / "response-index").rglob("*.json"))) == 1
    repeated_events = [
        json.loads(line)
        for line in lineage_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert (
        sum(
            event["event_type"] == "turn.visible_assistant_response"
            for event in repeated_events
        )
        == 1
    )

    second_handoff = service.prepare_state_travel(
        "book-faires",
        session_id,
        resume_contract={"execution_profile": execution_profile},
    )["state_travel"]
    service.resume_state_travel(
        project_id="book-faires",
        session_id=session_id,
        handoff_id=second_handoff["handoff_id"],
        host="CODEX_DESKTOP",
        host_session_id="host-session-second-task",
        ephemeral=False,
        client_can_edit_source=True,
        server_has_durable_filesystem=True,
        runtime_context={"execution_profile": execution_profile},
    )
    second = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "host-session-second-task",
                "turn_id": "turn-prompt-index-2",
                "cwd": str(source_repository),
                "prompt": "Second task checkpoint.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    second_context = json.loads(second.stdout)["hookSpecificOutput"][
        "additionalContext"
    ]
    second_indexed = json.loads(
        second_context.removeprefix("EVIDENCE_LANE_PROMPT_ENTRY=")
    )
    assert second_indexed["prompt_index"] == 2
    second_stopped = subprocess.run(
        [sys.executable, str(stop_hook)],
        input=json.dumps(
            {
                "session_id": "host-session-second-task",
                "turn_id": "turn-prompt-index-2",
                "cwd": str(source_repository),
                "last_assistant_message": "Second task checkpoint committed.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    second_notice = json.loads(
        json.loads(second_stopped.stdout)["systemMessage"].removeprefix(
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY="
        )
    )
    assert second_notice["turn_receipt"]["state"] == "COMMITTED"

    status = service.prompt_index_status("book-faires", session_id)
    assert status["raw_prompt_stored"] is False
    assert status["redacted_visible_prompt_stored"] is True
    assert [row["prompt_index"] for row in status["records"]] == [1, 2]
    assert {row["entry_pv"] for row in status["records"]} == {"PV2"}
    service.sessions.confirm_source_update(
        "book-faires",
        session_id,
        confirmation="HOST_SANDBOX_FINAL_STATE_CONFIRMED",
    )
    service.refresh("book-faires", session_id)
    service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PV1",
        decision_id="decision_prompt_index_backward",
    )
    forward = service.rollback(
        "book-faires",
        session_id,
        decided_by="human-test",
        rollback_to="PROMPT 1",
        decision_id="decision_prompt_index_forward",
    )
    assert forward["pointer"]["accepted_pv"] == "PV2"
    assert forward["decision"]["resolution_reference"]["kind"] == "PROMPT_INDEX"
    assert forward["decision"]["resolution_reference"]["prompt_index"] == 1


def test_prompt_hook_does_not_index_unbound_chats(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    hook = root / "plugins" / "evidence-lane-plugin" / "hooks" / "prompt_submit.py"
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(tmp_path / "empty-store")
    completed = subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(
            {
                "session_id": "unbound-host-session",
                "turn_id": "unbound-turn",
                "cwd": str(tmp_path),
                "prompt": "This is not an Evidence Lane task.",
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    payload = json.loads(completed.stdout)
    indexed = json.loads(
        payload["hookSpecificOutput"]["additionalContext"].removeprefix(
            "EVIDENCE_LANE_PROMPT_ENTRY="
        )
    )
    assert indexed["state"] == "NOT_INDEXED"
    assert indexed["reason"] == "NO_BOUND_EVIDENCE_LANE_SESSION"
    assert not (tmp_path / "empty-store" / "prompt-index").exists()


def test_accepted_entry_survives_process_restart_without_rebuild(service) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.close(
        "book-faires",
        session_id,
        reason="Recreate the next prompt in a separate disposable process.",
    )
    repository_root = Path(__file__).resolve().parents[1]
    accepted_root = service.store.accepted_path("book-faires", "PV1")

    def member_hashes(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(item for item in root.rglob("*") if item.is_file())
        }

    accepted_before = member_hashes(accepted_root)
    script = """
import json
import sys
from evidence_lane_plugin.service import EvidenceLaneService

service = EvidenceLaneService(data_root=sys.argv[1])
boot = service.boot_session(
    project_id="book-faires",
    user_id="user-test",
    workspace_id="workspace-test",
    host="CODEX_DESKTOP",
    agent_id="codex-single-agent",
    sandbox_id="sandbox-process-restart",
    ephemeral=False,
    runtime_context={"permission_mode": "test-process-restart"},
)
session_id = boot["session"]["session_id"]
print(json.dumps({
    "entry_action": boot["entry_action"],
    "entry_pv": boot["persistent_state_envelope"]["entry_pv"],
    "accepted_pv": boot["persistent_state_envelope"]["accepted_pv"],
    "entry_manifest_sha256": (
        boot["persistent_state_envelope"]["entry_manifest_sha256"]
    ),
    "entry_package_sha256": (
        boot["persistent_state_envelope"]["entry_package_sha256"]
    ),
    "next_candidate_pv": service.store.next_pv_id("book-faires"),
    "accepted_history": service.store.accepted_ids("book-faires"),
}))
service.sessions.close(
    "book-faires",
    session_id,
    reason="Complete the disposable process-restart proof.",
)
"""
    environment = os.environ.copy()
    source_path = repository_root / "plugins" / "evidence-lane-plugin" / "src"
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in [str(source_path), environment.get("PYTHONPATH", "")] if part
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(service.store.root)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=repository_root,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr
    restarted = json.loads(completed.stdout)
    assert restarted["entry_action"] == "CLASSIFY_ONE_TASK"
    assert restarted["entry_pv"] == "PV1"
    assert restarted["accepted_pv"] == "PV1"
    assert restarted["entry_manifest_sha256"]
    assert restarted["entry_package_sha256"]
    assert restarted["next_candidate_pv"] == "PV2"
    assert restarted["accepted_history"] == ["PV1"]
    assert service.store.accepted_ids("book-faires") == ["PV1"]
    assert member_hashes(accepted_root) == accepted_before


def test_session_start_survives_cachebuster_and_remains_read_only(
    service,
    tmp_path: Path,
) -> None:
    session_id, _ = build_and_approve_pv1(service)
    service.sessions.close(
        "book-faires",
        session_id,
        reason="Verify read-only startup after disposable cache replacement.",
    )
    repository_root = Path(__file__).resolve().parents[1]
    source_plugin = repository_root / "plugins" / "evidence-lane-plugin"

    def stage_cache(version: str) -> Path:
        staged = tmp_path / "plugin-cache" / version
        copies = {
            source_plugin / "hooks" / "session_start.py": (
                staged / "hooks" / "session_start.py"
            ),
            source_plugin / ".codex-plugin" / "plugin.json": (
                staged / ".codex-plugin" / "plugin.json"
            ),
                source_plugin / "src" / "evidence_lane_plugin" / "constants.py": (
                    staged / "src" / "evidence_lane_plugin" / "constants.py"
                ),
                source_plugin / "scripts" / "codex-release-channel.json": (
                    staged / "scripts" / "codex-release-channel.json"
                ),
                source_plugin
            / "src"
            / "evidence_lane_plugin"
            / "session_flash"
            / "env15"
            / "UNIVERSAL_FLASH_PROMPT.md": (
                staged
                / "src"
                / "evidence_lane_plugin"
                / "session_flash"
                / "env15"
                / "UNIVERSAL_FLASH_PROMPT.md"
            ),
        }
        for source, target in copies.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        manifest_path = staged / ".codex-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["version"] = version
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        return staged

    def store_hashes() -> dict[str, str]:
        return {
            path.relative_to(service.store.root).as_posix(): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in sorted(
                item for item in service.store.root.rglob("*") if item.is_file()
            )
        }

    def run_hook(staged: Path) -> tuple[dict, dict]:
        environment = os.environ.copy()
        environment["EVIDENCE_LANE_DATA_ROOT"] = str(service.store.root)
        completed = subprocess.run(
            [sys.executable, str(staged / "hooks" / "session_start.py")],
            input='{"source":"cachebuster-persistence-test"}',
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        payload = json.loads(completed.stdout)
        context = payload["hookSpecificOutput"]["additionalContext"]
        lines = context.splitlines()
        runtime = json.loads(
            next(
                line.removeprefix("PLUGIN_RUNTIME_ENVELOPE=")
                for line in lines
                if line.startswith("PLUGIN_RUNTIME_ENVELOPE=")
            )
        )
        persistent = json.loads(
            next(
                line.removeprefix("PERSISTENT_STATE_ENVELOPE=")
                for line in lines
                if line.startswith("PERSISTENT_STATE_ENVELOPE=")
            )
        )
        return runtime, persistent

    first_cache = stage_cache(f"{ENGINE_VERSION}+codex.test-cache-one")
    second_cache = stage_cache(f"{ENGINE_VERSION}+codex.test-cache-two")
    build_root = service.store.project_root("book-faires") / ".build"
    assert not any(build_root.rglob("*"))
    before = store_hashes()
    first_runtime, first_persistent = run_hook(first_cache)
    after_first = store_hashes()
    second_runtime, second_persistent = run_hook(second_cache)
    after_second = store_hashes()

    assert first_runtime["version_state"] == "FRESH"
    assert second_runtime["version_state"] == "FRESH"
    assert first_runtime["plugin_manifest_version"].endswith("test-cache-one")
    assert second_runtime["plugin_manifest_version"].endswith("test-cache-two")
    assert first_persistent["persistence_class"] == "USER_OWNED_LOCAL_STORE"
    assert second_persistent["persistence_class"] == "USER_OWNED_LOCAL_STORE"
    assert first_persistent["projects"] == second_persistent["projects"]
    assert second_persistent["projects"] == []
    assert second_persistent["cross_project_disclosure"] is False
    assert second_persistent["state"] == (
        "EXACT_HOST_SESSION_PROJECT_BINDING_REQUIRED"
    )
    assert second_persistent["project_count_returned"] == 0
    assert before == after_first == after_second


def test_command_surface_covers_lifecycle_and_all_lane_commands() -> None:
    root = Path(__file__).resolve().parents[1]
    plugin = root / "plugins" / "evidence-lane-plugin"
    commands = plugin / "commands"
    skills = plugin / "skills"
    public_order = [
        "evi-boot",
        "evi-rollback",
        "evi-build",
        "evi-refresh",
        "evi-mode",
        "evi-source-intake",
    ]
    expected_skills = {
        "evi",
        "evi-state-travel",
        "evi-exit-boot",
        "evi-plugin",
        "evi-storage",
        "evi-change-storage-connector",
        "evi-additional-plugin",
        "evi-drop-additional-plugin",
        *public_order,
    }
    assert [path.name for path in commands.glob("*.md")] == ["evi-plan.md"]
    plan_command = (commands / "evi-plan.md").read_text(encoding="utf-8")
    assert "Type /pl, finish the plan, then run /evi-plan again." in plan_command
    assert "host_mode=PLAN" in plan_command
    assert "pv_plan_steer_delta" in plan_command
    assert "does not apply Codex UI assumptions to ChatGPT" in plan_command
    for name in expected_skills:
        skill_file = skills / name / "SKILL.md"
        assert skill_file.is_file(), name
        assert f"name: {name}" in skill_file.read_text(encoding="utf-8")

    root_skill = (skills / "evi" / "SKILL.md").read_text(encoding="utf-8")
    positions = [root_skill.index(f"`/{name}`") for name in public_order]
    assert positions == sorted(positions)
    assert "State Travel" in root_skill
    assert root_skill.index("State Travel") < positions[0]
    assert "eligibility alone must not" in root_skill
    assert "explicitly requests it" in root_skill
    assert "genuinely exhausted" in root_skill
    assert "all eighteen canonical lanes" in root_skill.lower()
    assert "Project Engulf" in root_skill
    assert "`/evi-plugin` is an administrative sidecar" in root_skill
    assert "not a seventh primary" in (skills / "evi-plugin" / "SKILL.md").read_text(
        encoding="utf-8"
    )

    skill_text = "\n".join(
        path.read_text(encoding="utf-8") for path in skills.glob("*/SKILL.md")
    )
    readme_text = (root / "README.md").read_text(encoding="utf-8")
    assert "/pv-" not in skill_text.lower()
    assert "/ev " not in skill_text.lower()
    assert "/git" not in skill_text.lower()
    assert "/local" not in skill_text.lower()
    assert "source-command-evi" not in skill_text.lower()
    assert "/pv-" not in readme_text.lower()
    assert "/ev " not in readme_text.lower()


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
            names = {tool.name for tool in tools.tools}
            assert "task_complete_and_refresh" in names
            assert "pv_state_travel_resume" in names
            result = await session.call_tool("runtime_doctor", {})
            assert result.isError is False
            assert result.structuredContent["status"] == "PASS"
            namespaced = await session.call_tool(
                "evidence_lane_f1c20919f536.runtime_doctor",
                {},
            )
            assert namespaced.isError is False
            assert namespaced.structuredContent["status"] == "PASS"

    # A genuinely clean Git/cache snapshot may need the governed, hash-locked
    # plugin-local bootstrap before stdio becomes ready. The shipped MCP
    # manifest permits 900 seconds for that same first start.
    asyncio.run(asyncio.wait_for(exercise(), timeout=900))


def test_real_stdio_chatgpt_pro_profile_has_complete_visible_inventory(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    runner = root / "plugins" / "evidence-lane-plugin" / "scripts" / "run_mcp.py"

    async def exercise() -> None:
        environment = os.environ.copy()
        environment["EVIDENCE_LANE_DATA_ROOT"] = str(tmp_path / "chatgpt-stdio-store")
        environment["EVIDENCE_LANE_MCP_EXPOSURE_PROFILE"] = (
            "CHATGPT_PRO_GOVERNED"
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(runner), "--transport", "stdio"],
            env=environment,
        )
        async with (
            stdio_client(parameters) as streams,
            ClientSession(*streams) as session,
        ):
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "Evidence Lane"
            assert initialized.serverInfo.version == ENGINE_VERSION == "2.0.0"
            tools = await session.list_tools()
            names = tuple(sorted(tool.name for tool in tools.tools))
            assert len(names) == CHATGPT_PRO_GOVERNED_TOOL_COUNT
            assert set(CHATGPT_PRO_READ_TOOL_NAMES).issubset(names)
            assert sum(
                tool.annotations.readOnlyHint is True for tool in tools.tools
            ) == 21
            assert sum(
                tool.annotations.readOnlyHint is False for tool in tools.tools
            ) == 41
            result = await session.call_tool("runtime_doctor", {})
            assert result.isError is False
            assert result.structuredContent["status"] == "PASS"

    asyncio.run(asyncio.wait_for(exercise(), timeout=900))


def test_non_loopback_http_fails_closed_without_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ENVIRONMENT", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ALLOWED_CLIENT_IDS", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ALLOWED_ROLES", raising=False)
    with pytest.raises(RuntimeError, match="requires static bearer or OAuth"):
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


def test_server_start_installs_but_leaves_flash_and_runtime_detached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "remote-store"
    monkeypatch.setenv("EVIDENCE_LANE_DATA_ROOT", str(store))
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BASE_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ENVIRONMENT", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ALLOWED_CLIENT_IDS", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_ALLOWED_ROLES", raising=False)
    starts: list[dict[str, object]] = []
    lifecycle_events: list[str] = []

    class FakeServer:
        def run(self, *, transport: str) -> None:
            lifecycle_events.append("event_loop")
            starts[-1]["transport"] = transport

    def fake_create_mcp_server(**kwargs: object) -> FakeServer:
        lifecycle_events.append("create_server")
        application = kwargs["service"]
        assert isinstance(application, EvidenceLaneService)
        starts.append(
            {
                "installation": application.sessions.installation_status(),
                "flash": application.session_flash_status(),
            }
        )
        return FakeServer()

    def fake_prewarm_native_dependencies() -> tuple[str, ...]:
        lifecycle_events.append("native_prewarm")
        return ("rapidocr+onnxruntime",)

    async def fake_run_discovery_compatible_stdio(server: FakeServer) -> None:
        server.run(transport="stdio")

    monkeypatch.setattr(
        "evidence_lane_plugin.mcp_server.create_mcp_server",
        fake_create_mcp_server,
    )
    monkeypatch.setattr(
        "evidence_lane_plugin.mcp_server.prewarm_native_dependencies",
        fake_prewarm_native_dependencies,
    )
    monkeypatch.setattr(
        "evidence_lane_plugin.mcp_server.run_discovery_compatible_stdio",
        fake_run_discovery_compatible_stdio,
    )

    run_server(transport="stdio")
    run_server(transport="stdio")

    assert len(starts) == 2
    first_installation = starts[0]["installation"]
    second_installation = starts[1]["installation"]
    first_flash = starts[0]["flash"]
    second_flash = starts[1]["flash"]
    assert isinstance(first_installation, dict)
    assert isinstance(second_installation, dict)
    assert isinstance(first_flash, dict)
    assert isinstance(second_flash, dict)
    assert first_installation["state"] == "INSTALLED_UNTIL_USER_REMOVES_PLUGIN"
    assert first_installation["version"] == ENGINE_VERSION
    assert first_installation["hil_approval_inferred"] is False
    assert second_installation["installed_at"] == first_installation["installed_at"]
    assert first_flash["flash_state"] == "NOT_FLASHED"
    assert first_flash["receipt"] is None
    assert first_flash["runtime_activation"]["state"] == "DETACHED"
    assert second_flash["flash_state"] == "NOT_FLASHED"
    assert second_flash["runtime_activation"]["state"] == "DETACHED"
    assert [start["transport"] for start in starts] == ["stdio", "stdio"]
    assert lifecycle_events == [
        "native_prewarm",
        "create_server",
        "event_loop",
        "native_prewarm",
        "create_server",
        "event_loop",
    ]


def test_oauth_jwt_verifier_requires_asymmetric_algorithms_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="asymmetric"):
        OAuthJWTConfig(
            issuer_url="https://issuer.example/",
            jwks_url="https://issuer.example/.well-known/jwks.json",
            audience="https://mcp.example/mcp",
            required_scopes=(READ_SCOPE,),
            deployment_environment="staging",
            allowed_client_ids=("chatgpt",),
            allowed_roles=("owner", "tester"),
            algorithms=("HS256",),
        )

    config = OAuthJWTConfig(
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="https://mcp.example/mcp",
        required_scopes=(READ_SCOPE,),
        deployment_environment="staging",
        allowed_client_ids=("chatgpt",),
        allowed_roles=("owner", "tester"),
    )
    verifier = OAuthJWTVerifier(config)

    class SigningKey:
        key = object()

    monkeypatch.setattr(
        verifier._jwk_client,
        "get_signing_key_from_jwt",
        lambda _: SigningKey(),
    )
    claims = {
        "iss": config.issuer_url,
        "aud": config.audience,
        "sub": "user-123",
        "azp": "chatgpt",
        "exp": 4_102_444_800,
        "nbf": 1,
        "jti": "token-123",
        "scope": READ_SCOPE,
        "evidence_lane_environment": "staging",
        "evidence_lane_roles": ["tester"],
        "evidence_lane_projects": ["project-a"],
    }
    monkeypatch.setattr(
        "evidence_lane_plugin.auth.decode",
        lambda *args, **kwargs: claims,
    )
    accepted = asyncio.run(verifier.verify_token("header.payload.signature"))
    assert accepted is not None
    assert accepted.subject == "user-123"
    assert accepted.client_id == "chatgpt"
    assert accepted.resource == config.audience

    claims["scope"] = ""
    assert asyncio.run(verifier.verify_token("header.payload.signature")) is None

    claims["scope"] = READ_SCOPE
    claims["evidence_lane_environment"] = "production"
    assert asyncio.run(verifier.verify_token("header.payload.signature")) is None

    claims["evidence_lane_environment"] = "staging"
    claims["azp"] = "unapproved-client"
    assert asyncio.run(verifier.verify_token("header.payload.signature")) is None

    claims["azp"] = "chatgpt"
    claims["evidence_lane_projects"] = ["*"]
    assert asyncio.run(verifier.verify_token("header.payload.signature")) is None


def test_oauth_tool_policy_enforces_scope_role_environment_and_project() -> None:
    staging_config = OAuthJWTConfig(
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="https://mcp.example/mcp",
        required_scopes=(READ_SCOPE,),
        deployment_environment="staging",
        allowed_client_ids=("chatgpt", "codex"),
        allowed_roles=("owner", "tester"),
    )
    policy = OAuthToolAuthorizationPolicy(staging_config)

    tester_claims = {
        "evidence_lane_environment": "staging",
        "evidence_lane_roles": ["tester"],
        "evidence_lane_projects": ["project-a"],
    }
    tester = AccessToken(
        token="redacted-test-token",
        client_id="chatgpt",
        scopes=[READ_SCOPE, WRITE_SCOPE],
        subject="tester-1",
        claims=tester_claims,
    )
    policy.authorize_access_token(
        tester,
        tool_name="pv_status",
        lifecycle=False,
        project_id="project-a",
    )
    policy.authorize_access_token(
        tester,
        tool_name="pv_build_initial",
        lifecycle=True,
        project_id="project-a",
    )

    with pytest.raises(OAuthAuthorizationError, match="PROJECT_NOT_AUTHORIZED"):
        policy.authorize_access_token(
            tester,
            tool_name="pv_status",
            lifecycle=False,
            project_id="project-b",
        )
    with pytest.raises(OAuthAuthorizationError, match="EXACT_PROJECT_ID_REQUIRED"):
        policy.authorize_access_token(
            tester,
            tool_name="pv_status",
            lifecycle=False,
            project_id="",
        )
    with pytest.raises(OAuthAuthorizationError, match="OAUTH_SCOPE_REQUIRED"):
        policy.authorize_access_token(
            AccessToken(
                token="redacted-read-token",
                client_id="chatgpt",
                scopes=[READ_SCOPE],
                subject="tester-1",
                claims=tester_claims,
            ),
            tool_name="pv_build_initial",
            lifecycle=True,
            project_id="project-a",
        )
    with pytest.raises(OAuthAuthorizationError, match="OAUTH_SCOPE_REQUIRED"):
        policy.authorize_access_token(
            AccessToken(
                token="redacted-owner-token",
                client_id="codex",
                scopes=[READ_SCOPE, WRITE_SCOPE],
                subject="owner-1",
                claims={
                    "evidence_lane_environment": "staging",
                    "evidence_lane_roles": ["owner"],
                    "evidence_lane_projects": ["project-a"],
                },
            ),
            tool_name="remote_git_execute_push",
            lifecycle=True,
            project_id="project-a",
        )
    with pytest.raises(OAuthAuthorizationError, match="OWNER_ROLE_REQUIRED"):
        policy.authorize_access_token(
            AccessToken(
                token="redacted-test-token",
                client_id="chatgpt",
                scopes=[READ_SCOPE, WRITE_SCOPE, REMOTE_GIT_SCOPE],
                subject="tester-1",
                claims=tester_claims,
            ),
            tool_name="remote_git_execute_push",
            lifecycle=True,
            project_id="project-a",
        )

    owner = AccessToken(
        token="redacted-owner-token",
        client_id="codex",
        scopes=[READ_SCOPE, WRITE_SCOPE, REMOTE_GIT_SCOPE],
        subject="owner-1",
        claims={
            "evidence_lane_environment": "staging",
            "evidence_lane_roles": ["owner"],
            "evidence_lane_projects": ["*"],
        },
    )
    policy.authorize_access_token(
        owner,
        tool_name="remote_git_execute_push",
        lifecycle=True,
        project_id="project-b",
    )

    production_policy = OAuthToolAuthorizationPolicy(
        OAuthJWTConfig(
            issuer_url="https://issuer.example/",
            jwks_url="https://issuer.example/.well-known/jwks.json",
            audience="https://mcp.example/mcp",
            required_scopes=(READ_SCOPE,),
            deployment_environment="production",
            allowed_client_ids=("chatgpt",),
            allowed_roles=("owner", "tester"),
        )
    )
    with pytest.raises(OAuthAuthorizationError, match="OWNER_ROLE_REQUIRED"):
        production_policy.authorize_access_token(
            AccessToken(
                token="redacted-test-token",
                client_id="chatgpt",
                scopes=[READ_SCOPE, WRITE_SCOPE],
                subject="tester-1",
                claims={
                    **tester_claims,
                    "evidence_lane_environment": "production",
                },
            ),
            tool_name="pv_build_initial",
            lifecycle=True,
            project_id="project-a",
        )


def test_oauth_server_declares_exact_per_tool_security_schemes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = OAuthJWTConfig(
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="https://mcp.example/mcp",
        required_scopes=(READ_SCOPE,),
        deployment_environment="staging",
        allowed_client_ids=("chatgpt", "codex"),
        allowed_roles=("owner", "tester"),
    )
    mismatched_audience = OAuthJWTConfig(
        issuer_url="https://issuer.example/",
        jwks_url="https://issuer.example/.well-known/jwks.json",
        audience="https://mcp.example/other",
        required_scopes=(READ_SCOPE,),
        deployment_environment="staging",
        allowed_client_ids=("chatgpt",),
        allowed_roles=("owner", "tester"),
    )
    with pytest.raises(ValueError, match="externally visible MCP resource URL"):
        create_mcp_server(
            service=EvidenceLaneService(data_root=tmp_path / "mismatch"),
            base_url="https://mcp.example",
            oauth_config=mismatched_audience,
        )
    server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "full"),
        base_url="https://mcp.example",
        oauth_config=config,
    )
    tools = asyncio.run(server.list_tools())
    by_name = {tool.name: tool for tool in tools}
    assert by_name["pv_status"].model_extra["securitySchemes"] == [
        {"type": "oauth2", "scopes": [READ_SCOPE]}
    ]
    assert by_name["pv_build_initial"].model_extra["securitySchemes"] == [
        {"type": "oauth2", "scopes": [READ_SCOPE, WRITE_SCOPE]}
    ]
    assert by_name["remote_git_execute_push"].model_extra[
        "securitySchemes"
    ] == [
        {
            "type": "oauth2",
            "scopes": [READ_SCOPE, WRITE_SCOPE, REMOTE_GIT_SCOPE],
        }
    ]
    assert by_name["pv_status"].meta["securitySchemes"] == by_name[
        "pv_status"
    ].model_extra["securitySchemes"]
    receipt = server._evidence_lane_native_route_receipt
    assert (
        receipt["transport_project_binding"]
        == "OAUTH_SUBJECT_CLIENT_ENVIRONMENT_ROLE_AND_EXACT_PROJECT"
    )
    assert receipt["oauth_authorization_policy"]["per_tool_security_schemes"]

    async def exercise_oauth_discovery() -> None:
        transport = httpx.ASGITransport(app=server.streamable_http_app())
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://mcp.example",
        ) as client:
            metadata_response = await client.get(
                "/.well-known/oauth-protected-resource/mcp"
            )
            assert metadata_response.status_code == 200
            metadata = metadata_response.json()
            assert metadata["resource"] == "https://mcp.example/mcp"
            assert metadata["authorization_servers"] == [
                "https://issuer.example/"
            ]
            assert metadata["scopes_supported"] == [READ_SCOPE]
            challenge = await client.post("/mcp")
            assert challenge.status_code == 401
            assert (
                'resource_metadata="https://mcp.example/'
                '.well-known/oauth-protected-resource/mcp"'
                in challenge.headers["www-authenticate"]
            )

    asyncio.run(exercise_oauth_discovery())

    chatgpt_server = create_mcp_server(
        service=EvidenceLaneService(data_root=tmp_path / "chatgpt"),
        base_url="https://mcp.example",
        oauth_config=config,
        exposure_profile=CHATGPT_PRO_GOVERNED_EXPOSURE_PROFILE,
    )
    chatgpt_tools = {
        tool.name: tool for tool in asyncio.run(chatgpt_server.list_tools())
    }
    assert chatgpt_tools["pv_build_initial"].model_extra[
        "securitySchemes"
    ] == [{"type": "oauth2", "scopes": [READ_SCOPE]}]

    monkeypatch.setattr(
        "evidence_lane_plugin.auth.get_access_token",
        lambda: None,
    )
    status_tool = server._tool_manager.get_tool("pv_status")
    assert status_tool is not None
    missing_token_block = asyncio.run(
        status_tool.run({"project_id": "project-a"}, convert_result=True)
    )
    assert isinstance(missing_token_block, CallToolResult)
    assert missing_token_block.isError
    assert missing_token_block.structuredContent["mutation_performed"] is False
    assert missing_token_block.meta is not None
    missing_token_challenge = missing_token_block.meta[
        "mcp/www_authenticate"
    ][0]
    assert 'error="invalid_token"' in missing_token_challenge
    assert (
        'resource_metadata="https://mcp.example/'
        '.well-known/oauth-protected-resource/mcp"'
        in missing_token_challenge
    )

    monkeypatch.setattr(
        "evidence_lane_plugin.auth.get_access_token",
        lambda: AccessToken(
            token="redacted-owner-token",
            client_id="codex",
            scopes=[READ_SCOPE, WRITE_SCOPE],
            subject="owner-1",
            claims={
                "evidence_lane_environment": "staging",
                "evidence_lane_roles": ["owner"],
                "evidence_lane_projects": ["project-a"],
            },
        ),
    )
    remote_tool = server._tool_manager.get_tool("remote_git_execute_push")
    assert remote_tool is not None
    scope_block = asyncio.run(
        remote_tool.run(
            {
                "project_id": "project-a",
                "action_id": "unused-action",
                "executed_by": "owner-1",
            },
            convert_result=True,
        )
    )
    assert isinstance(scope_block, CallToolResult)
    assert scope_block.isError
    assert scope_block.structuredContent["mutation_performed"] is False
    assert scope_block.meta is not None
    scope_challenge = scope_block.meta["mcp/www_authenticate"][0]
    assert 'error="insufficient_scope"' in scope_challenge
    assert (
        'resource_metadata="https://mcp.example/'
        '.well-known/oauth-protected-resource/mcp"'
        in scope_challenge
    )
    assert READ_SCOPE in scope_challenge
    assert WRITE_SCOPE in scope_challenge
    assert REMOTE_GIT_SCOPE in scope_challenge

    monkeypatch.setattr(
        "evidence_lane_plugin.auth.get_access_token",
        lambda: AccessToken(
            token="redacted-tester-token",
            client_id="chatgpt",
            scopes=[READ_SCOPE],
            subject="tester-1",
            claims={
                "evidence_lane_environment": "staging",
                "evidence_lane_roles": ["tester"],
                "evidence_lane_projects": ["project-a"],
            },
        ),
    )
    project_block = asyncio.run(
        status_tool.run({"project_id": "project-b"}, convert_result=True)
    )
    assert isinstance(project_block, CallToolResult)
    assert project_block.isError
    assert project_block.structuredContent["code"] == "OAUTH_PROJECT_NOT_AUTHORIZED"
    assert project_block.meta is None


def test_partial_remote_oauth_configuration_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("EVIDENCE_LANE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", "https://issuer.example/")
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", raising=False)
    monkeypatch.delenv("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", raising=False)
    with pytest.raises(RuntimeError, match="Incomplete OAuth configuration"):
        run_server(
            transport="streamable-http",
            host="0.0.0.0",
            port=8765,
        )
