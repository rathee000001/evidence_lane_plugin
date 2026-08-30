from __future__ import annotations

import asyncio
import json
from pathlib import Path

from evidence_lane_plugin.ai_toolchain import (
    ACTION_CLASS_TOOL_ORDER,
    LANE_ACTION_CLASSES,
)
from evidence_lane_plugin.context_index_routing import (
    bind_context_results_to_sqlite_identity,
    build_context_index_operation,
    context_index_catalog,
)
from evidence_lane_plugin.current_route_registry import current_implementation_registry
from evidence_lane_plugin.deployment_toolchain import (
    build_deployment_route,
    deployment_tool_catalog,
)
from evidence_lane_plugin.ecosystem_toolchain import (
    ECOSYSTEM_ADAPTERS,
    EXCLUDED_AGENT_OWNERS,
    build_fastmcp_gateway,
    build_openai_agents_function_tool,
    inspect_ecosystem_adapter_runtime,
    resolve_ecosystem_adapters,
    validate_ecosystem_adapter_catalog,
)
from evidence_lane_plugin.env_uop_tool_routing import (
    env_uop_tool_routing_catalog,
    route_env_uop_data_touch,
)
from evidence_lane_plugin.evaluation_toolchain import (
    build_evaluation_plan,
    evaluation_tool_catalog,
    seal_evaluation_result,
)
from evidence_lane_plugin.graph_pipeline import semantic_graph_from_mermaid
from evidence_lane_plugin.mcp_adapter_routing import (
    build_mcp_adapter_route,
    mcp_adapter_catalog,
)
from evidence_lane_plugin.mcp_server import create_mcp_server
from evidence_lane_plugin.observability_toolchain import (
    build_observability_export,
    observability_tool_catalog,
)
from evidence_lane_plugin.plugin_architecture import (
    MEMORY_AUTHORITY_MAP,
    TOOL_ROLE_CLASSES,
    build_universal_plugin_architecture,
    render_universal_architecture_mmd,
)
from evidence_lane_plugin.tunnel_identity_routing import (
    build_tunnel_identity_route,
    tunnel_identity_routing_catalog,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"


def _json(relative: str) -> dict[str, object]:
    return json.loads((PLUGIN / relative).read_text(encoding="utf-8"))


def test_non_agent_ecosystem_is_executable_and_matrix_bound() -> None:
    matrix = _json("toolchains/tool-requirement-matrix.v1.json")
    declared = {str(row["tool"]) for row in matrix["requirements"]}  # type: ignore[index]
    receipt = validate_ecosystem_adapter_catalog(declared_tools=declared)
    assert len(declared) == len(matrix["requirements"])
    assert receipt["status"] == "PASS"
    assert receipt["adapter_count"] == len(ECOSYSTEM_ADAPTERS)
    assert set(ECOSYSTEM_ADAPTERS).issubset(declared)
    assert not set(EXCLUDED_AGENT_OWNERS).intersection(declared)
    assert receipt["codex_is_sole_agent_authority"] is True
    assert receipt["openai_agents_sdk_is_subordinate"] is True


def test_study_brain_and_bigger_universe_use_full_registered_pairings() -> None:
    routes = {
        str(row["capability"]): row
        for row in current_implementation_registry()["capabilities"]
    }
    study_route = routes["code_source_and_study_brain_routing"]
    assert study_route["owner"] == "evi-source-intake"
    assert study_route["current_route"] == (
        "ONE_PRIMARY_CODE_PROJECT_PLUS_LANE_SCOPED_STUDY_BRAINS"
    )
    assert {
        "ADDITIONAL_LOCAL_CODE_IS_LANE_STUDY_BRAIN",
        "BUILD_REFRESH_ARTIFACT_OWNERSHIP",
    }.issubset(study_route["verification"])

    architecture = build_universal_plugin_architecture(PLUGIN)
    action_rows = {
        str(row["action"]): row
        for row in architecture["action_skill_hook_schema_pairing"]["actions"]
    }
    skill_rows = {
        str(row["skill"]): row
        for row in architecture["action_skill_hook_schema_pairing"]["skills"]
    }
    for action in ("bigger_universe_register", "bigger_universe_link"):
        row = action_rows[action]
        assert row["owner_skill"] == "evi-bigger-universe"
        assert row["env_uop_paired"] is True
        assert row["ordered_hook_registry_paired"] is True
        assert row["source_module_registry_paired"] is True
        assert row["manifest_pairing_required"] is True
        assert row["separate_command_required"] is False
        assert row["schema_path"].startswith("schemas/actions/")
        assert row["sdk_binding"].startswith("sdk/actions/")
        assert row["mcp_binding"].startswith("mcp/actions/")
        assert row["eligible_tools"]
    bigger_universe = skill_rows["evi-bigger-universe"]
    assert set(bigger_universe["public_actions"]) == {
        "bigger_universe_register",
        "bigger_universe_link",
    }
    assert bigger_universe["workflow_count_is_fixed_ceiling"] is False


def test_openai_agents_sdk_and_fastmcp_have_no_independent_authority() -> None:
    catalog = validate_ecosystem_adapter_catalog()
    rows = {row["tool_id"]: row for row in catalog["adapters"]}
    agents = rows["OpenAI_Agents_SDK"]
    fastmcp = rows["FastMCP"]
    for row in (agents, fastmcp):
        assert row["agent_authority"] is False
        assert row["lifecycle_authority"] is False
        assert row["project_authority"] is False
        assert row["memory_authority"] is False
        assert row["scheduler_authority"] is False
    assert agents["category"] == "subordinate_orchestration_sdk"
    assert fastmcp["category"] == "mcp_runtime"


def test_ecosystem_resolution_selects_at_most_one_granted_adapter() -> None:
    result = resolve_ecosystem_adapters(
        capability="vector_query",
        action_class="RETRIEVAL",
        lane_id="research",
        project_id="project-a",
        task_id="task-a",
        granted_tools={"Pinecone", "Weaviate"},
        available_tools={"Pinecone", "Weaviate", "Milvus"},
    )
    assert result["status"] == "PASS"
    assert result["selected_tools"] == ["Pinecone"]
    assert result["selected_tool_count_maximum"] == 1
    assert result["run_every_tool"] is False
    assert result["adapter_has_agent_authority"] is False
    assert result["adapter_has_scheduler_authority"] is False


def test_required_ecosystem_sdks_are_importable_without_credentials() -> None:
    for tool in (
        "OpenAI_Agents_SDK",
        "FastMCP",
        "LangSmith",
        "Langfuse",
        "OpenTelemetry",
    ):
        result = inspect_ecosystem_adapter_runtime(tool)
        assert result["status"] == "PASS"
        assert result["state"] == "ACTIVE"
        assert result["credential_values_read"] is False
        assert result["network_probe_performed"] is False


def test_openai_agents_function_tool_delegates_to_existing_dispatcher() -> None:
    action = _json("schemas/public-action-schemas.v001.json")["tools"][0]
    observed: list[tuple[str, dict[str, object]]] = []

    def dispatch(name: str, arguments: dict[str, object]) -> dict[str, object]:
        observed.append((name, arguments))
        return {"status": "PASS", "owner": "internal_sdk"}

    tool = build_openai_agents_function_tool(action=action, dispatcher=dispatch)
    result = asyncio.run(tool.on_invoke_tool(None, '{"project_id":"p"}'))
    assert tool.name == action["name"]
    assert tool._is_agent_tool is False
    assert tool._is_codex_tool is True
    assert tool.strict_json_schema is False
    assert tool.output_json_schema is None
    assert tool._evidence_lane_output_schema == action["output_schema"]
    assert tool._evidence_lane_output_validation_owner == "EVIDENCE_LANE_INTERNAL_SDK"
    assert tool._evidence_lane_input_validation_owner == "EVIDENCE_LANE_INTERNAL_SDK"
    assert observed == [(action["name"], {"project_id": "p"})]
    assert result == {"status": "PASS", "owner": "internal_sdk"}


def test_fastmcp_gateway_proxies_the_unchanged_native_action_catalog() -> None:
    native = create_mcp_server()
    gateway = build_fastmcp_gateway(native)
    native_names = {tool.name for tool in asyncio.run(native.list_tools())}
    gateway_names = {tool.name for tool in asyncio.run(gateway.list_tools())}
    catalog = _json("schemas/public-action-schemas.v001.json")
    assert len(native_names) == catalog["tool_count"]
    assert gateway_names == native_names
    contract = gateway._evidence_lane_contract
    assert contract["fastmcp_version"] == "3.4.7"
    assert contract["native_action_names_unchanged"] is True
    assert contract["agent_authority"] is False
    assert contract["scheduler_authority"] is False


def test_outer_mcp_routes_are_scoped_and_fail_closed(tmp_path: Path) -> None:
    catalog = mcp_adapter_catalog()
    assert catalog["adapter_count"] == len(catalog["adapters"])
    github = build_mcp_adapter_route(
        tool_id="GitHub_MCP_Server",
        project_id="project-a",
        task_id="task-a",
        requested_capabilities=["repository_read", "checks"],
        granted_capabilities=["repository_read", "checks", "pull_requests"],
    )
    assert github["status"] == "PASS"
    assert github["selected_server_tools_or_toolsets"] == ["repos", "actions"]
    assert github["composition_framework_order"] == ["FastMCP", "MCP_Python_SDK"]
    assert github["composition_primary"] == "FastMCP"
    assert github["compatibility_transport_fallback"] == "MCP_Python_SDK"
    assert github["domain_server_is_not_framework_fallback"] is True
    assert github["credential_values_read"] is False
    assert github["run_every_server_tool"] is False
    filesystem = build_mcp_adapter_route(
        tool_id="Filesystem_MCP_Server",
        project_id="project-a",
        task_id="task-a",
        requested_capabilities=["bounded_read", "search"],
        granted_capabilities=["bounded_read", "search"],
        allowed_roots=[str(tmp_path / "project")],
    )
    assert filesystem["status"] == "PASS"
    assert filesystem["allowed_roots"] == [str((tmp_path / "project").resolve())]
    assert filesystem["root_scope_required"] is True
    blocked_slack = build_mcp_adapter_route(
        tool_id="Slack_MCP_Server",
        project_id="project-a",
        task_id="task-a",
        requested_capabilities=["approved_send"],
        granted_capabilities=["approved_send"],
    )
    assert blocked_slack["status"] == "BLOCKED_EXTERNAL_WRITE_APPROVAL_REQUIRED"
    assert blocked_slack["scheduler_authority"] is False


def test_context_indexes_share_one_sqlite_owned_identity_contract() -> None:
    catalog = context_index_catalog()
    ids = {row["tool_id"] for row in catalog["indexes"]}
    assert {
        "SQLite_FTS5_BM25",
        "FAISS_CPU",
        "Pinecone",
        "Weaviate",
        "Milvus",
        "OpenSearch",
    } == ids
    blocked = build_context_index_operation(
        tool_id="Pinecone",
        operation="query",
        project_id="project-a",
        authority_id="project_memory",
        lane_id="research",
        sqlite_identity_sha256="A" * 64,
        source_sha256="B" * 64,
        granted_tools=[],
    )
    assert blocked["status"] == "BLOCKED_PROJECT_GRANT_REQUIRED"
    routed = build_context_index_operation(
        tool_id="OpenSearch",
        operation="upsert_changed",
        project_id="project-a",
        authority_id="source_authority",
        lane_id="research",
        sqlite_identity_sha256="A" * 64,
        source_sha256="B" * 64,
        granted_tools=["OpenSearch"],
    )
    assert routed["status"] == "PASS"
    assert routed["sqlite_remains_durable_authority"] is True
    assert routed["changed_hash_only_write"] is True
    assert routed["network_call_performed"] is False
    binding = bind_context_results_to_sqlite_identity(
        sqlite_identity_sha256="A" * 64,
        tool_id="OpenSearch",
        result_ids=["row-2", "row-1", "row-2"],
    )
    assert binding["result_ids"] == ["row-2", "row-1"]
    assert binding["results_are_evidence_locators_not_authority"] is True


def test_evaluation_routes_bind_schema_route_dataset_and_redaction() -> None:
    catalog = evaluation_tool_catalog()
    assert catalog["tool_count"] == len(catalog["tools"])
    plan = build_evaluation_plan(
        tool_id="LangSmith",
        mode="experiment",
        project_id="project-a",
        task_id="task-a",
        action_name="lane_search",
        action_schema_sha256="A" * 64,
        route_sha256="B" * 64,
        dataset_sha256="C" * 64,
        redaction_receipt_sha256="D" * 64,
        evaluator_ids=["correctness", "provenance"],
        granted_tools=["LangSmith"],
    )
    assert plan["status"] == "PASS"
    assert plan["credential_values_read"] is False
    assert plan["network_or_command_executed"] is False
    assert plan["production_authority_write_allowed"] is False
    result = seal_evaluation_result(
        plan_sha256=plan["plan_sha256"],
        score_by_evaluator={"correctness": 0.9, "provenance": 1.0},
        passed=True,
    )
    assert result["status"] == "PASS"
    assert result["production_state_mutated"] is False
    assert result["result_is_evidence_not_project_authority"] is True


def test_observability_exports_only_redacted_hash_bound_metadata() -> None:
    catalog = observability_tool_catalog()
    assert catalog["tool_count"] == len(catalog["tools"])
    export = build_observability_export(
        tool_id="OpenTelemetry",
        signal="traces",
        project_id="project-a",
        task_id="task-a",
        action_name="lane_search",
        correlation_id="corr-a",
        input_sha256="A" * 64,
        result_sha256="B" * 64,
        redaction_receipt_sha256="C" * 64,
        attributes={"lane": "research", "duration_ms": 12},
        granted_tools=["OpenTelemetry"],
    )
    assert export["status"] == "PASS"
    assert export["raw_prompt_exported"] is False
    assert export["raw_response_exported"] is False
    assert export["raw_source_exported"] is False
    assert export["hil_token_exported"] is False
    assert export["credential_values_read"] is False
    assert export["network_call_performed"] is False


def test_deployment_tools_are_evidence_routes_not_lifecycle_owners() -> None:
    catalog = deployment_tool_catalog()
    assert catalog["tool_count"] == len(catalog["tools"])
    blocked = build_deployment_route(
        tool_id="Kubernetes",
        operation="deploy",
        project_id="project-a",
        task_id="task-a",
        artifact_sha256="A" * 64,
        source_commit_sha256="B" * 64,
        route_receipt_sha256="C" * 64,
        granted_tools=["Kubernetes"],
        external_write_approved=False,
    )
    assert blocked["status"] == "BLOCKED_EXTERNAL_WRITE_APPROVAL_REQUIRED"
    planned = build_deployment_route(
        tool_id="Google_Cloud_Run",
        operation="plan",
        project_id="project-a",
        task_id="task-a",
        artifact_sha256="A" * 64,
        source_commit_sha256="B" * 64,
        route_receipt_sha256="C" * 64,
        granted_tools=["Google_Cloud_Run"],
    )
    assert planned["status"] == "PASS"
    assert planned["deployment_result_is_evidence_only"] is True
    assert planned["plan_authority"] is False
    assert planned["goal_authority"] is False
    assert planned["tunnel_scheduler_authority"] is False
    assert planned["network_or_command_executed"] is False


def test_tunnel_routes_multiple_project_tasks_by_exact_identity() -> None:
    catalog = tunnel_identity_routing_catalog()
    assert catalog["count_is_fixed_ceiling"] is False
    assert catalog["scheduled_task_owner"] is False
    common = {
        "session_id": "session-a",
        "action_name": "lane_search",
        "action_schema_sha256": "A" * 64,
        "lane_id": "research",
        "host_profile": "CODEX_DESKTOP",
        "capability": "vector_query",
        "granted_tools": ["Pinecone"],
        "available_tools": ["Pinecone"],
    }
    first = build_tunnel_identity_route(
        project_id="project-a", task_id="task-a", **common
    )
    second = build_tunnel_identity_route(
        project_id="project-b", task_id="task-b", **common
    )
    assert first["correlation_identity_sha256"] != second["correlation_identity_sha256"]
    assert first["ecosystem_adapter_route"]["selected_tools"] == ["Pinecone"]
    assert first["shared_process_allowed"] is True
    assert first["cross_task_state_allowed"] is False
    assert first["cross_project_state_allowed"] is False
    assert first["scheduled_task_owner"] is False
    assert first["run_every_tool"] is False


def test_env_uop_selects_one_data_touch_route_without_authority_override() -> None:
    catalog = env_uop_tool_routing_catalog()
    assert catalog["workflow_names_hardcoded"] is False
    assert catalog["tool_count_is_fixed_ceiling"] is False
    decision = route_env_uop_data_touch(
        project_id="project-a",
        task_id="task-a",
        action_name="lane_search",
        action_schema_sha256="A" * 64,
        authority_id="project_memory",
        lane_id="research",
        host_profile="CODEX_DESKTOP",
        operation="query",
        data_locality="project_root",
        ordered_candidate_tools=["SQLite_FTS5_BM25", "Pinecone"],
        available_tools=["SQLite_FTS5_BM25", "Pinecone"],
        granted_tools=["Pinecone"],
        grant_required_tools=["Pinecone"],
        formula_sha256="B" * 64,
        operator_id="RETRIEVAL_PRIMARY_FALLBACK",
        work_gate_open=True,
    )
    assert decision["status"] == "PASS"
    assert decision["selected_tools"] == ["SQLite_FTS5_BM25"]
    assert decision["selected_tool_count_maximum"] == 1
    assert decision["uop_decision"]["can_override_env"] is False
    assert decision["uop_decision"]["can_override_project_authority"] is False
    assert decision["uop_decision"]["can_infer_hil"] is False
    assert decision["run_every_tool"] is False


def test_all_tool_routes_reference_declared_tools_and_cross_cutting_classes() -> None:
    matrix = _json("toolchains/tool-requirement-matrix.v1.json")
    declared = {str(row["tool"]) for row in matrix["requirements"]}  # type: ignore[index]
    routed = {tool for ordered in ACTION_CLASS_TOOL_ORDER.values() for tool in ordered}
    assert routed.issubset(declared)
    assert {"EVALUATION", "OBSERVABILITY", "DEPLOYMENT"}.issubset(
        ACTION_CLASS_TOOL_ORDER
    )
    assert all("OBSERVABILITY" in classes for classes in LANE_ACTION_CLASSES.values())
    lane_registry = _json("authorities/project_sectors/lane-surface-registry.v1.json")
    assert len(LANE_ACTION_CLASSES) == lane_registry["lane_count"]


def test_universal_architecture_pairs_current_executable_registries() -> None:
    architecture = build_universal_plugin_architecture(PLUGIN)
    counts = architecture["counts"]
    assert architecture["status"] == "PASS"
    assert architecture["architecture_kind"] == "REGISTRY_DRIVEN_UNIVERSAL_WORKFLOW"
    public = _json("schemas/public-action-schemas.v001.json")
    skills = _json("skills/skill-surface-registry.v1.json")
    hooks = _json("hooks/hook-event-registry.v1.json")
    lanes = _json("authorities/project_sectors/lane-surface-registry.v1.json")
    matrix = _json("toolchains/tool-requirement-matrix.v1.json")
    assert counts["public_actions"] == public["tool_count"]
    assert counts["sdk_action_bindings"] == public["tool_count"]
    assert counts["mcp_action_bindings"] == public["tool_count"]
    assert counts["skills"] == skills["skill_count"]
    assert counts["hook_events"] == hooks["event_count"]
    assert counts["hook_handler_actions"] == hooks["handler_action_count"]
    assert counts["sector_lanes"] == lanes["lane_count"]
    assert counts["tool_requirements"] == len(matrix["requirements"])
    unified = architecture["unified_tool_workflow_pairing"]
    assert unified["tool_count"] == len(matrix["requirements"])
    assert unified["tool_partition_count"] == 1
    assert unified["legacy_core_plus_extension_split"] is False
    assert unified["all_tools_use_same_routing_law"] is True
    assert {row["tool"] for row in unified["rows"]} == {
        str(row["tool"])
        for row in _json("toolchains/tool-requirement-matrix.v1.json")["requirements"]
    }
    assert all(
        row["internal_sdk_resolution"]
        and row["outer_sdk_resolution"]
        and row["env_resolution"]
        and row["uop_resolution"]
        and row["hook_resolution"]
        and row["hil_resolution"]
        and row["receipt_resolution"]
        and row["implementation_owner"]
        and row["surfaces"]
        and row["selection_binding_kind"]
        and row["route_binding_count"] == len(row["route_bindings"])
        and row["route_binding_count"] > 0
        and row["condition_true_requires_execution_or_visible_failure"] is True
        and row["run_every_tool"] is False
        and row["run_every_workflow"] is False
        for row in unified["rows"]
    )
    unified_rows = {str(row["tool"]): row for row in unified["rows"]}
    assert "ai_toolchain_route" in unified_rows["FastMCP"]["eligible_public_actions"]
    assert (
        "source_intake_classify"
        in unified_rows["DOCX_OpenXML"]["eligible_public_actions"]
    )
    assert unified_rows["pytest"]["selection_binding_kind"] == (
        "WORKFLOW_INFRASTRUCTURE"
    )
    assert unified_rows["LangSmith"]["selection_binding_kind"] == (
        "CROSS_CUTTING_ATTACHMENT"
    )
    assert unified_rows["LangSmith"]["eligible_skill_workflows"]
    taxonomy = architecture["tool_taxonomy"]
    assert taxonomy["role_classes"] == list(TOOL_ROLE_CLASSES)
    assert sum(taxonomy["counts"].values()) == len(matrix["requirements"])
    assert taxonomy["role_classes_are_mutually_exclusive"] is True
    assert all(
        row["condition_true_requires_execution_or_fail_visible"] is True
        and row["run_only_when_condition_true"] is True
        for row in taxonomy["rows"]
    )
    assert all(
        row["eligible_public_actions"] == []
        and row["cross_cutting_attachment_action_classes"]
        and row["cross_cutting_attachment_is_action_owner"] is False
        for row in unified["rows"]
        if row["role_class"] == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
    )
    authority_pairing = architecture["authority_tool_workflow_pairing"]
    assert authority_pairing["sector_count"] == lanes["lane_count"]
    assert (
        authority_pairing["named_root_authority_count"]
        == _json("authorities/authority-surface-registry.v1.json")["authority_count"]
    )
    assert all(
        row["eligible_tool_count"] == len(row["eligible_tools"])
        and row["eligible_tool_count"] > 0
        and row["sqlite_mmd_dot_json_tools_pointer_manifest_paired"] is True
        and row["internal_sdk_paired"] is True
        and row["outer_sdk_paired"] is True
        and row["env_uop_paired"] is True
        and row["hook_registry_paired"] is True
        and row["receipt_edge_required"] is True
        for row in (
            authority_pairing["sector_rows"] + authority_pairing["authority_rows"]
        )
    )
    surfaces = architecture["action_skill_hook_schema_pairing"]
    assert surfaces["action_count"] == public["tool_count"]
    assert surfaces["skill_count"] == skills["skill_count"]
    assert surfaces["hook_event_count"] == hooks["event_count"]
    assert surfaces["counts_are_derived_not_fixed"] is True
    assert surfaces["first_class_workflow_requires_first_class_skill"] is True
    assert surfaces["separate_command_layer_present"] is False
    public_names = {str(row["name"]) for row in public["tools"]}
    assert {row["action"] for row in surfaces["actions"]} == public_names
    assert all(
        (PLUGIN / row["schema_path"]).is_file()
        and (PLUGIN / row["sdk_binding"]).is_file()
        and (PLUGIN / row["mcp_binding"]).is_file()
        and row["current_route"]["status"] == "CURRENT_ROUTE"
        and row["eligible_tool_count"] == len(row["eligible_tools"])
        and row["eligible_tool_count"] > 0
        and row["action_boundary_hook_event_count"] == 3
        and {event["event"] for event in row["action_boundary_hook_events"]}
        == {"PreToolUse", "PermissionRequest", "PostToolUse"}
        and all(
            event["workflow_contract"]["public_action_boundary"] is True
            and event["workflow_contract"]["skill_action"]
            and event["workflow_contract"]["host_timing"]
            for event in row["action_boundary_hook_events"]
        )
        and row["separate_command_required"] is False
        for row in surfaces["actions"]
    )
    assert all(
        set(row["public_actions"]).issubset(public_names)
        and row["sdk_mcp_schema_pairing_required"] is True
        and row["separate_command_required"] is False
        and row["workflow_count_is_fixed_ceiling"] is False
        for row in surfaces["skills"]
    )
    assert all(
        row["sdk_event_binding_required"] is True
        and row["ordered_handler_isolation_required"] is True
        and row["workflow_contract_sha256"]
        and row["workflow_contract"]["host_timing"]
        and row["workflow_contract"]["skill_action"]
        and row["workflow_contract"]["workflow_phases"]
        and row["hook_event_count_is_fixed_ceiling"] is False
        for row in surfaces["hook_events"]
    )
    assert architecture["prompt_examples_define_architecture"] is False
    assert architecture["future_workflows_must_register_the_same_pairings"] is True
    assert all(architecture["pairing"].values())
    assert all(architecture["data_touch_law"].values())
    master = architecture["master_workflow"]
    assert master["status"] == "PASS"
    assert master["counts_are_current_snapshot_not_ceiling"] is True
    node_ids = {row["node_id"] for row in master["nodes"]}
    assert {
        "INTENT",
        "BOOT",
        "FLASH",
        "STATE_TRAVEL",
        "DELTA_ENTRY",
        "MID_DELTA",
        "DELTA_EXIT",
        "REFRESH",
        "HIL",
        "FUSE",
        "ROLLBACK",
        "ENV",
        "UOP",
        "INTERNAL_SDK",
        "OUTER_SDK",
        "FASTMCP",
        "NATIVE_MCP",
        "RECEIPT",
        "REGISTRIES",
        "REGENERATED_GRAPH",
    }.issubset(node_ids)
    assert {
        "HOOK_SESSIONSTART",
        "HOOK_USERPROMPTSUBMIT",
        "HOOK_PRETOOLUSE",
        "HOOK_PERMISSIONREQUEST",
        "HOOK_POSTTOOLUSE",
        "HOOK_PRECOMPACT",
        "HOOK_POSTCOMPACT",
        "HOOK_STOP",
        "HOOK_SESSIONEND",
    }.issubset(node_ids)
    assert "HOOKS" not in node_ids
    master_edges = {(str(row["source"]), str(row["target"])) for row in master["edges"]}
    assert ("TOOL_RESOLVE", "HOOK_PRETOOLUSE") in master_edges
    assert ("HOOK_POSTTOOLUSE", "VALIDATE") in master_edges
    assert ("RECEIPT", "HOOK_STOP") in master_edges
    rendered = render_universal_architecture_mmd(architecture)
    restored = semantic_graph_from_mermaid(
        rendered,
        name="master_architecture_roundtrip",
    )
    assert len(restored.nodes) == master["node_count"]
    assert len(restored.edges) == master["edge_count"]


def test_memory_authorities_remain_separate_and_retention_owned() -> None:
    classes = {row["memory_class"] for row in MEMORY_AUTHORITY_MAP}
    assert classes == {
        "sensory_live_capture",
        "task_working_context",
        "episodic_chat_lineage",
        "semantic_project_memory",
        "accepted_learning",
        "canon_and_project_truth",
        "host_instructions_and_recall",
    }
    owners = [owner for row in MEMORY_AUTHORITY_MAP for owner in row["owners"]]
    assert "project_memory" in owners
    assert "agent_learning" in owners
    assert "host_MEMORY.md" in owners
    assert owners.count("session_authority") == 2
    assert len(set(owners)) == len(owners) - 1


def test_architecture_json_mmd_and_dot_are_generated() -> None:
    architecture = _json("toolchains/universal-plugin-architecture.v1.json")
    assert architecture["status"] == "PASS"
    for relative in (
        "toolchains/UNIVERSAL_PLUGIN_ARCHITECTURE.mmd",
        "toolchains/UNIVERSAL_PLUGIN_ARCHITECTURE.dot",
        "toolchains/MEMORY_AUTHORITY_ARCHITECTURE.mmd",
        "toolchains/MEMORY_AUTHORITY_ARCHITECTURE.dot",
        "toolchains/unified-tool-workflow-pairing.v1.json",
        "toolchains/authority-tool-workflow-pairing.v1.json",
        "toolchains/action-skill-hook-schema-pairing.v1.json",
    ):
        body = (PLUGIN / relative).read_text(encoding="utf-8")
        assert body.strip()
