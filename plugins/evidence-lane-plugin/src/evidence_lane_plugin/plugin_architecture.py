"""Universal plugin architecture and separated memory-authority projection."""

from __future__ import annotations

import json
from itertools import pairwise
from pathlib import Path
from typing import Any

from .codex_action_plane import classify_action_workflow_classes
from .ecosystem_toolchain import validate_ecosystem_adapter_catalog
from .graph_pipeline import SemanticGraph
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .hook_contract import HOOK_EVENT_NAMES, HOOK_EVENT_WORKFLOW_CONTRACTS
from .package_root import resolve_plugin_root

PLUGIN_ARCHITECTURE_SCHEMA = "evidence-lane.universal-plugin-architecture.v1"

TOOL_ROLE_CLASSES = (
    "TASK_EXECUTION",
    "TRANSPORT_OR_ORCHESTRATION",
    "OBSERVABILITY_OR_EVALUATION_ATTACHMENT",
    "EXTERNAL_SERVICE_OR_STORE",
)

# Tool routing and public-action classification use deliberately different
# vocabularies in a few places.  Keep the translation explicit and source-owned
# so generated architecture never silently drops a runtime, MCP, or lane tool.
ACTION_TO_TOOL_CLASS_COMPATIBILITY: dict[str, frozenset[str]] = {
    "SOURCE_ROUTING": frozenset(
        {"CODE", "DOCUMENT", "OCR_MEDIA", "DATA", "WEB_RESEARCH", "GRAPH"}
    ),
    "RUNTIME": frozenset({"RUNTIME_API"}),
    "MCP_TRANSPORT": frozenset({"MCP_COMPOSITION", "RUNTIME_API"}),
}

SURFACE_PUBLIC_ACTION_HINTS: dict[str, frozenset[str]] = {
    "all_structured_project_sectors": frozenset({"source_intake_classify"}),
    "candidate_lifecycle": frozenset(
        {
            "adaptive_delta_exit",
            "pv_build_initial",
            "pv_fuse",
            "pv_rollback",
            "task_complete_and_refresh",
        }
    ),
}

SURFACE_SKILL_WORKFLOW_HINTS: dict[str, frozenset[tuple[str, str]]] = {
    "candidate_lifecycle": frozenset(
        {
            ("evi-build", "source-intake-backed-pv0-bootstrap"),
            ("evi-fuse", "exact-learning-then-project-approval-promotion"),
            ("evidence-lane-code-lifecycle", "task-execution"),
        }
    ),
    "configured_network_adapters": frozenset(
        {("evi-toolchain", "conditional-codex-toolchain-routing")}
    ),
    "evaluation": frozenset(
        {
            ("evi-toolchain", "conditional-codex-toolchain-routing"),
            ("evidence-lane-code-lifecycle", "task-execution"),
        }
    ),
    "internal_sdk": frozenset(
        {("evi-toolchain", "conditional-codex-toolchain-routing")}
    ),
    "observability": frozenset(
        {("evi-toolchain", "conditional-codex-toolchain-routing")}
    ),
    "rag": frozenset(
        {
            ("evi-source-intake", "bounded-source-retrieval"),
            ("evi-toolchain", "conditional-codex-toolchain-routing"),
        }
    ),
    "runtime_resource_telemetry": frozenset({("evi-boot", "boot-or-resume")}),
    "security_testing": frozenset({("evidence-lane-code-lifecycle", "task-execution")}),
    "testing": frozenset({("evidence-lane-code-lifecycle", "task-execution")}),
    "workflow_graphs": frozenset(
        {("evi-toolchain", "conditional-codex-toolchain-routing")}
    ),
}

TOOL_SKILL_WORKFLOW_HINTS: dict[str, frozenset[tuple[str, str]]] = {
    "pytest": frozenset({("evidence-lane-code-lifecycle", "task-execution")}),
    "Ruff": frozenset({("evidence-lane-code-lifecycle", "task-execution")}),
    "MyPy": frozenset({("evidence-lane-code-lifecycle", "task-execution")}),
}


def _tool_classes_match_action(
    action_class_names: list[str],
    tool_class_names: set[str],
) -> bool:
    compatible = set(action_class_names)
    for action_class in action_class_names:
        compatible.update(ACTION_TO_TOOL_CLASS_COMPATIBILITY.get(action_class, ()))
    return bool(compatible.intersection(tool_class_names))


OPERATING_CYCLE_STAGES: tuple[dict[str, str], ...] = (
    {"id": "INTENT", "label": "User intent / prompt / steer"},
    {"id": "BOOT", "label": "Boot or resume exact runtime identity"},
    {"id": "FLASH", "label": "Locked ENV15/UOP15 Flash"},
    {"id": "STATE_TRAVEL", "label": "User-timed State Travel"},
    {"id": "SOURCE_INTAKE", "label": "Source Intake and exact source identity"},
    {"id": "RECIPE", "label": "Project Recipe"},
    {"id": "MODE", "label": "Ordered Mode intersections"},
    {"id": "PLAN_GOAL", "label": "Canonical Plan + Goal"},
    {"id": "DELTA_ENTRY", "label": "Governed Delta entry"},
    {"id": "ATOMIC_WORK", "label": "Atomic implementation or analysis work"},
    {"id": "MID_DELTA", "label": "Mid-Delta evidence / steer / failure"},
    {"id": "DELTA_EXIT", "label": "Verified Delta exit"},
    {"id": "REFRESH", "label": "Changed-hash authority refresh + CAS reuse"},
    {"id": "CANDIDATE", "label": "Unaccepted candidate when required"},
    {"id": "HIL", "label": "Owning HIL presentation only"},
    {"id": "FUSE", "label": "Exact approved Fuse"},
    {"id": "ROLLBACK", "label": "Governed logical Rollback"},
    {"id": "EXIT_BOOT", "label": "Explicit Exit Boot"},
    {"id": "NEXT", "label": "Next Delta or next-task State Travel"},
)


def _safe_graph_id(prefix: str, value: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "_" for character in value.upper()
    ).strip("_")
    return f"{prefix}_{normalized or 'UNNAMED'}"


def _tool_role(requirement: dict[str, Any], route: dict[str, Any]) -> str:
    classes = {str(value) for value in route["action_classes"]}
    surfaces = {str(value) for value in requirement["surfaces"]}
    role = str(requirement["role"]).casefold()
    if classes & {"OBSERVABILITY", "EVALUATION"}:
        return "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
    if str(requirement["requirement"]) == "CONFIGURED_EXTERNAL_SERVICE":
        return "EXTERNAL_SERVICE_OR_STORE"
    if {"outer_sdk", "native_mcp", "tunnel", "internal_sdk"} & surfaces and (
        not route["lanes"]
        or any(
            phrase in role
            for phrase in (
                "orchestration",
                "composition",
                "transport",
                "mcp client",
                "subordinate typed tool",
            )
        )
    ):
        return "TRANSPORT_OR_ORCHESTRATION"
    return "TASK_EXECUTION"


def _tool_selection_axis(role_class: str) -> str:
    return {
        "TASK_EXECUTION": "SOURCE_TYPE_LANE_ACTION_AND_WORKFLOW",
        "TRANSPORT_OR_ORCHESTRATION": "HOST_AND_WORKFLOW_TOPOLOGY",
        "OBSERVABILITY_OR_EVALUATION_ATTACHMENT": (
            "CONFIGURED_REDACTED_CROSS_CUTTING_ATTACHMENT"
        ),
        "EXTERNAL_SERVICE_OR_STORE": ("PURPOSE_GRANT_CREDENTIAL_LOCALITY_AND_EXPIRY"),
    }[role_class]


MEMORY_AUTHORITY_MAP: tuple[dict[str, Any], ...] = (
    {
        "memory_class": "sensory_live_capture",
        "owners": ["capture_routing", "bounded_io"],
        "retention": "milliseconds_to_seconds_unless_governed_intake_persists_evidence",
        "durable_authority": False,
        "role": "Immediate host/source event awareness before classification.",
    },
    {
        "memory_class": "task_working_context",
        "owners": ["session_authority", "codex_turn_control"],
        "retention": "active_task_or_session_boundary",
        "durable_authority": False,
        "role": "Bounded current-task context, routing identity, and in-progress state.",
    },
    {
        "memory_class": "episodic_chat_lineage",
        "owners": ["chat_lineage", "session_authority", "receipt_ledger"],
        "retention": "append_only_governed_project_history",
        "durable_authority": True,
        "role": "Ordered events, turns, outcomes, and exact receipt links.",
    },
    {
        "memory_class": "semantic_project_memory",
        "owners": ["project_memory"],
        "retention": "project_governed_until_revoked_or_superseded",
        "durable_authority": True,
        "role": "Queryable facts and links; indexes are rebuildable and not Project Truth.",
    },
    {
        "memory_class": "accepted_learning",
        "owners": ["agent_learning"],
        "retention": "accepted_learning_until_explicit_revoke",
        "durable_authority": True,
        "role": "Evidence-backed reusable procedural learning after separate Learning HIL.",
    },
    {
        "memory_class": "canon_and_project_truth",
        "owners": ["canon_input", "project_authority", "accepted_pv_pointer"],
        "retention": "append_only_or_versioned_governed_authority",
        "durable_authority": True,
        "role": "Canon exchange, project identity, and accepted Project Truth remain distinct.",
    },
    {
        "memory_class": "host_instructions_and_recall",
        "owners": ["AGENTS.md", "host_MEMORY.md"],
        "retention": "host_owned",
        "durable_authority": False,
        "role": "Host instruction/recall arm; never merged into project databases.",
    },
)

UNIVERSAL_ROUTING_STAGES: tuple[dict[str, Any], ...] = (
    {"stage": "intent_and_skill_resolution", "source": "skill_surface_registry"},
    {
        "stage": "typed_action_resolution",
        "source": "public_and_internal_action_schemas",
    },
    {"stage": "internal_execution_owner", "source": "internal_sdk_registry"},
    {"stage": "environment_decision", "source": "env_authority"},
    {"stage": "operator_and_gate_decision", "source": "uop_authority"},
    {
        "stage": "authority_and_lane_resolution",
        "source": "authority_and_sector_registries",
    },
    {"stage": "conditional_tool_resolution", "source": "tool_execution_routing"},
    {"stage": "outer_transport_resolution", "source": "outer_sdk_and_mcp_bindings"},
    {"stage": "ordered_hook_handling", "source": "hook_event_registry"},
    {"stage": "result_validation_and_receipt", "source": "schemas_and_receipt_ledger"},
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Architecture registry is not an object: {path}")
    return value


def _action_names(paths: list[str], suffix: str) -> set[str]:
    return {Path(path).name.removesuffix(suffix) for path in paths}


def _master_workflow_model(
    *,
    lanes: list[dict[str, Any]],
    authorities: list[dict[str, Any]],
    workflow_step_count: int,
    action_count: int,
    hook_events: list[dict[str, Any]],
    tool_taxonomy_counts: dict[str, int],
) -> dict[str, Any]:
    groups = [
        {"group_id": "OPERATING_CYCLE", "label": "Operating cycle + Delta lifecycle"},
        {"group_id": "EXECUTION_STACK", "label": "Typed skill/action execution stack"},
        {"group_id": "HOSTS", "label": "Codex host variants"},
        {"group_id": "LANES", "label": "Current project sector lanes"},
        {"group_id": "AUTHORITIES", "label": "Named and root authorities"},
        {"group_id": "MEMORY", "label": "Separate memory roles"},
        {"group_id": "TOOL_ROLES", "label": "Conditional tool role classes"},
        {"group_id": "HOOK_TIMING", "label": "Codex host hook timing"},
        {"group_id": "ROUND_TRIP", "label": "Generated round-trip proof"},
    ]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def node(
        node_id: str,
        label: str,
        group_id: str,
        kind: str = "semantic",
    ) -> None:
        nodes.append(
            {
                "node_id": node_id,
                "label": label,
                "group_id": group_id,
                "kind": kind,
            }
        )

    def edge(
        source: str,
        target: str,
        label: str | None = None,
        *,
        conditional: bool = False,
    ) -> None:
        edges.append(
            {
                "source": source,
                "target": target,
                "label": label,
                "conditional": conditional,
            }
        )

    for stage in OPERATING_CYCLE_STAGES:
        kind = (
            "root"
            if stage["id"] == "INTENT"
            else (
                "warn"
                if stage["id"] in {"MID_DELTA", "HIL", "ROLLBACK"}
                else "lifecycle"
            )
        )
        node(stage["id"], stage["label"], "OPERATING_CYCLE", kind)
    cycle_sequence = [stage["id"] for stage in OPERATING_CYCLE_STAGES[:15]]
    for source, target in pairwise(cycle_sequence):
        edge(source, target)
    edge("MID_DELTA", "ATOMIC_WORK", "continue bounded work", conditional=True)
    edge("HIL", "FUSE", "exact approval", conditional=True)
    edge("HIL", "ROLLBACK", "rollback decision", conditional=True)
    edge("HIL", "NEXT", "retain/research/reject/fail", conditional=True)
    edge("FUSE", "NEXT")
    edge("ROLLBACK", "NEXT")
    edge("NEXT", "DELTA_ENTRY", "next Delta", conditional=True)
    edge("NEXT", "STATE_TRAVEL", "next task", conditional=True)
    edge("NEXT", "EXIT_BOOT", "explicit close", conditional=True)

    stack_nodes = (
        (
            "SKILL",
            f"Governed skill + {workflow_step_count} current ordered workflow steps",
        ),
        (
            "ACTION",
            f"Typed public/internal action ({action_count} current registry rows)",
        ),
        ("SCHEMA", "Input/output schema and current route contract"),
        ("INTERNAL_SDK", "Internal SDK execution owner"),
        ("ENV", "ENV15 environment, host, lane, locality, availability and grant"),
        ("UOP", "UOP15 operators, formulas, work/privacy/disclosure/HIL gates"),
        ("AUTHORITY_RESOLVE", "Owning lane or named/root authority"),
        ("TOOL_RESOLVE", "Conditional tool role and ordered primary/fallback"),
        ("OUTER_SDK", "Outer SDK local or transport route"),
        ("FASTMCP", "FastMCP composition when compatible"),
        ("NATIVE_MCP", "Pinned native MCP compatibility transport"),
        ("DOMAIN_MCP", "Purpose-bound domain MCP server"),
        ("VALIDATE", "Schema, result, authority and HIL-effect validation"),
        ("RECEIPT", "Exact input/route/result/provenance receipt"),
    )
    for node_id, label in stack_nodes:
        node(
            node_id,
            label,
            "EXECUTION_STACK",
            "warn" if node_id in {"UOP", "VALIDATE"} else "semantic",
        )
    for source, target in zip(
        [value[0] for value in stack_nodes[:7]],
        [value[0] for value in stack_nodes[1:8]],
        strict=True,
    ):
        edge(source, target)
    edge("OUTER_SDK", "FASTMCP", "composition selected", conditional=True)
    edge("OUTER_SDK", "NATIVE_MCP", "compatibility required", conditional=True)
    edge("OUTER_SDK", "AUTHORITY_RESOLVE", "package-local", conditional=True)
    edge("FASTMCP", "DOMAIN_MCP", conditional=True)
    edge("NATIVE_MCP", "DOMAIN_MCP", conditional=True)
    hook_nodes: dict[str, str] = {}
    for event in hook_events:
        event_name = str(event["event"])
        contract = dict(event["workflow_contract"])
        hook_node = _safe_graph_id("HOOK", event_name)
        hook_nodes[event_name] = hook_node
        node(
            hook_node,
            f"{event_name}: {contract['host_timing']}",
            "HOOK_TIMING",
            "warn" if event_name in {"PreToolUse", "Stop"} else "semantic",
        )
    edge(hook_nodes["SessionStart"], "INTENT", "session entry", conditional=True)
    edge(hook_nodes["SubagentStart"], "SKILL", "optional observation", conditional=True)
    edge(hook_nodes["UserPromptSubmit"], "SKILL", "prepare entry", conditional=True)
    edge("TOOL_RESOLVE", hook_nodes["PreToolUse"], "prospective boundary")
    edge(
        hook_nodes["PreToolUse"],
        hook_nodes["PermissionRequest"],
        "permission requested",
        conditional=True,
    )
    edge(hook_nodes["PreToolUse"], "OUTER_SDK", "execute", conditional=True)
    edge(hook_nodes["PermissionRequest"], "OUTER_SDK", "observed", conditional=True)
    edge("DOMAIN_MCP", hook_nodes["PostToolUse"], "tool result")
    edge("AUTHORITY_RESOLVE", hook_nodes["PostToolUse"], "local result")
    edge(hook_nodes["PostToolUse"], "VALIDATE", "receipt projection")
    edge("RECEIPT", hook_nodes["PreCompact"], "compaction requested", conditional=True)
    edge(
        hook_nodes["PreCompact"],
        hook_nodes["PostCompact"],
        "host compacts",
        conditional=True,
    )
    edge(hook_nodes["PostCompact"], "SKILL", "reentry", conditional=True)
    edge(
        hook_nodes["SubagentStop"], "RECEIPT", "optional observation", conditional=True
    )
    edge("RECEIPT", hook_nodes["Stop"], "visible response stops")
    edge(
        hook_nodes["Stop"], hook_nodes["SessionEnd"], "session closes", conditional=True
    )
    edge("VALIDATE", "RECEIPT")
    edge("INTENT", "SKILL")
    edge("RECEIPT", "MID_DELTA", "work evidence")
    edge("RECEIPT", "DELTA_EXIT", "exit evidence", conditional=True)
    edge("REFRESH", "AUTHORITY_RESOLVE")

    for host in ("Stable Codex", "Codex Beta", "CODEX_CLI", "CODEX_VM"):
        node(_safe_graph_id("HOST", host), host, "HOSTS")
        edge(_safe_graph_id("HOST", host), "SKILL")
    for lane in lanes:
        lane_id = str(lane["lane_id"])
        lane_node = _safe_graph_id("LANE", lane_id)
        node(lane_node, lane_id, "LANES")
        edge("AUTHORITY_RESOLVE", lane_node)
    for authority in authorities:
        authority_id = str(authority["authority_id"])
        authority_node = _safe_graph_id("AUTH", authority_id)
        node(authority_node, authority_id, "AUTHORITIES")
        edge("AUTHORITY_RESOLVE", authority_node)
    for memory in MEMORY_AUTHORITY_MAP:
        memory_class = str(memory["memory_class"])
        memory_node = _safe_graph_id("MEMORY", memory_class)
        node(memory_node, memory_class.replace("_", " "), "MEMORY")
        edge("AUTHORITY_RESOLVE", memory_node, "typed receipt link", conditional=True)
    for role_class in TOOL_ROLE_CLASSES:
        role_node = _safe_graph_id("TOOLS", role_class)
        node(
            role_node,
            f"{role_class.replace('_', ' ').title()}: {tool_taxonomy_counts[role_class]} current rows",
            "TOOL_ROLES",
        )
        edge("TOOL_RESOLVE", role_node)
        edge(
            role_node,
            "OUTER_SDK"
            if role_class != "TASK_EXECUTION"
            else hook_nodes["PostToolUse"],
        )

    roundtrip_nodes = (
        ("REGISTRIES", "Executable registries"),
        ("GENERATED_GRAPH", "Generated MMD + DOT"),
        ("PARSED_GRAPH", "Strict parser round trip"),
        ("SQLITE_GRAPH", "Owning SQLite topology"),
        ("REGENERATED_GRAPH", "Regenerated graph + hash equality"),
    )
    for node_id, label in roundtrip_nodes:
        node(node_id, label, "ROUND_TRIP", "output")
    for source, target in zip(
        [value[0] for value in roundtrip_nodes][:-1],
        [value[0] for value in roundtrip_nodes][1:],
        strict=True,
    ):
        edge(source, target)
    edge("REGENERATED_GRAPH", "REGISTRIES", "coverage proof", conditional=True)
    edge("RECEIPT", "REGISTRIES", "refresh source graph")
    return {
        "schema": "evidence-lane.master-plugin-workflow.v1",
        "status": "PASS",
        "groups": groups,
        "nodes": nodes,
        "edges": edges,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "counts_are_current_snapshot_not_ceiling": True,
        "prompt_examples_define_workflow": False,
    }


def build_universal_plugin_architecture(plugin_root: str | Path) -> dict[str, Any]:
    root = Path(plugin_root).resolve()
    paths = {
        "catalog": root / "schemas/public-action-schemas.v001.json",
        "mcp": root / "mcp/mcp-manifest.v1.json",
        "sdk": root / "sdk/sdk-manifest.v1.json",
        "skills": root / "skills/skill-surface-registry.v1.json",
        "hooks": root / "hooks/hook-event-registry.v1.json",
        "authorities": root / "authorities/authority-surface-registry.v1.json",
        "lanes": root / "authorities/project_sectors/lane-surface-registry.v1.json",
        "schemas": root / "schemas/schema-manifest.v1.json",
        "modules": root / "src/evidence_lane_plugin/module-registry.v1.json",
        "tools": root / "toolchains/tool-requirement-matrix.v1.json",
        "routing": root / "toolchains/tool-execution-routing.v1.json",
        "tunnel": root / "toolchains/tunnel-runtime-toolchain.v1.json",
        "current_routes": root / "schemas/routing/current-route-registry.v1.json",
    }
    values = {name: _read(path) for name, path in paths.items()}

    def non_circular_surface_identity(row: dict[str, Any]) -> dict[str, Any]:
        """Keep semantic identity here; artifact registries own byte hashes.

        A workflow is generated from this architecture and is then a member of
        the lane or authority manifest. Feeding that manifest hash back into
        the architecture creates an impossible self-referential hash cycle.
        """

        return {
            key: value
            for key, value in row.items()
            if not key.endswith("_sha256") and key != "receipt_sha256"
        }

    catalog_names = {str(row["name"]) for row in values["catalog"]["tools"]}
    sdk_names = _action_names(list(values["sdk"]["action_bindings"]), ".action.v1.json")
    mcp_names = _action_names(
        list(values["mcp"]["action_bindings"]), ".binding.v1.json"
    )
    tool_names = {str(row["tool"]) for row in values["tools"]["requirements"]}
    ecosystem = validate_ecosystem_adapter_catalog(declared_tools=tool_names)
    actions = [dict(row) for row in values["catalog"]["tools"]]
    action_classes = {
        str(row["name"]): classify_action_workflow_classes(row) for row in actions
    }
    registered_skill_workflows = sorted(
        [
            {
                "skill": str(skill["name"]),
                "workflow": str(group["workflow"]),
                "order": int(group["order"]),
                "public_actions": [str(value) for value in group["tools"]],
            }
            for skill in values["skills"]["skills"]
            for group in dict(skill["workflow"])["ordered_tool_groups"]
        ],
        key=lambda row: (row["skill"], row["order"], row["workflow"]),
    )
    all_skill_workflow_bindings = sorted(
        {
            (str(row["skill"]), str(row["workflow"]))
            for row in registered_skill_workflows
        }
    )
    unknown_surface_workflow_hints = (
        set()
        .union(
            *SURFACE_SKILL_WORKFLOW_HINTS.values(),
            *TOOL_SKILL_WORKFLOW_HINTS.values(),
        )
        .difference(all_skill_workflow_bindings)
    )
    if unknown_surface_workflow_hints:
        raise ValueError(
            "Surface workflow hints are not registered: "
            + repr(sorted(unknown_surface_workflow_hints))
        )

    def workflows_for_actions(action_names: set[str]) -> list[tuple[str, str]]:
        return sorted(
            {
                (str(workflow["owner_skill"]), str(workflow["workflow"]))
                for action in actions
                if str(action["name"]) in action_names
                for workflow in dict(action.get("route_contract") or {}).get(
                    "skill_workflows", []
                )
            }
        )

    tool_routes = {str(row["tool"]): dict(row) for row in values["routing"]["rows"]}
    unified_pairings: list[dict[str, Any]] = []
    for requirement in values["tools"]["requirements"]:
        tool_id = str(requirement["tool"])
        route = tool_routes[tool_id]
        classes = {str(value) for value in route["action_classes"]}
        role_class = _tool_role(dict(requirement), route)
        surfaces = [str(value) for value in requirement["surfaces"]]
        if role_class == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT":
            eligible_action_set: set[str] = set()
        elif "DEPLOYMENT" in classes:
            eligible_action_set = {
                name
                for name in catalog_names
                if name.startswith(("remote_git_", "connector_plugin_"))
                or name == "git_sync_selected"
            }
        else:
            eligible_action_set = {
                name
                for name, workflow_classes in action_classes.items()
                if _tool_classes_match_action(workflow_classes, classes)
            }
        route_lanes = [str(value) for value in route["lanes"]]
        if route_lanes and role_class == "TASK_EXECUTION":
            eligible_action_set.add("ai_toolchain_route")
            if classes.intersection(
                {"CODE", "DOCUMENT", "OCR_MEDIA", "DATA", "WEB_RESEARCH", "GRAPH"}
            ):
                eligible_action_set.add("source_intake_classify")
        if (
            role_class != "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
            and "runtime_doctor" in surfaces
        ):
            eligible_action_set.add("runtime_doctor")
        if role_class != "OBSERVABILITY_OR_EVALUATION_ATTACHMENT":
            for surface in surfaces:
                eligible_action_set.update(SURFACE_PUBLIC_ACTION_HINTS.get(surface, ()))
        eligible_actions = sorted(eligible_action_set.intersection(catalog_names))
        skill_workflow_set = set(workflows_for_actions(set(eligible_actions)))
        if "all_workflows" in surfaces:
            skill_workflow_set.update(all_skill_workflow_bindings)
        for surface in surfaces:
            skill_workflow_set.update(SURFACE_SKILL_WORKFLOW_HINTS.get(surface, ()))
        skill_workflow_set.update(TOOL_SKILL_WORKFLOW_HINTS.get(tool_id, ()))
        skill_workflows = sorted(skill_workflow_set)
        implementation_owner = str(route["implementation_owner"])
        if role_class == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT":
            selection_binding_kind = "CROSS_CUTTING_ATTACHMENT"
        elif role_class == "TRANSPORT_OR_ORCHESTRATION":
            selection_binding_kind = "TRANSPORT_OR_ORCHESTRATION"
        elif role_class == "EXTERNAL_SERVICE_OR_STORE":
            selection_binding_kind = "EXTERNAL_SERVICE_OR_STORE"
        elif route_lanes:
            selection_binding_kind = "LANE_TASK_EXECUTION"
        elif eligible_actions:
            selection_binding_kind = "ACTION_TASK_EXECUTION"
        else:
            selection_binding_kind = "WORKFLOW_INFRASTRUCTURE"
        route_bindings: list[dict[str, Any]] = [
            {
                "binding_kind": "IMPLEMENTATION_OWNER",
                "implementation_owner": implementation_owner,
                "surfaces": surfaces,
            }
        ]
        if route_lanes:
            route_bindings.append({"binding_kind": "LANES", "lane_ids": route_lanes})
        if eligible_actions:
            route_bindings.append(
                {"binding_kind": "PUBLIC_ACTIONS", "actions": eligible_actions}
            )
        if skill_workflows:
            route_bindings.append(
                {
                    "binding_kind": "SKILL_WORKFLOWS",
                    "workflows": [
                        {"skill": skill, "workflow": workflow}
                        for skill, workflow in skill_workflows
                    ],
                }
            )
        unified_pairings.append(
            {
                "tool": tool_id,
                "role_class": role_class,
                "selection_axis": _tool_selection_axis(role_class),
                "requirement": str(requirement["requirement"]),
                "surfaces": surfaces,
                "implementation_owner": implementation_owner,
                "action_classes": sorted(classes),
                "eligible_lanes": route_lanes,
                "eligible_public_actions": eligible_actions,
                "cross_cutting_attachment_action_classes": (
                    sorted(classes)
                    if role_class == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
                    else []
                ),
                "eligible_skill_workflows": [
                    {"skill": skill, "workflow": workflow}
                    for skill, workflow in skill_workflows
                ],
                "selection_binding_kind": selection_binding_kind,
                "route_bindings": route_bindings,
                "route_binding_count": len(route_bindings),
                "internal_sdk_resolution": "CURRENT_ACTION_SCHEMA_AND_HANDLER_OWNER",
                "outer_sdk_resolution": "SELECTED_TRANSPORT_OR_LOCAL_ROUTE_ONLY",
                "env_resolution": "CONTEXT_HOST_DEPENDENCY_GRANT_AND_LOCALITY",
                "uop_resolution": "OPERATOR_FORMULA_BOOLEAN_GATE_AND_ORDERED_FALLBACK",
                "hook_resolution": "CURRENT_EVENT_ORDERED_HANDLERS_ONLY",
                "hil_resolution": "OWNING_WORKFLOW_ONLY_NEVER_INFERRED_FROM_TOOL",
                "receipt_resolution": "EXACT_INPUT_ROUTE_RESULT_AND_PROVENANCE",
                "mcp_public_action_counted_here": False,
                "run_every_workflow": False,
                "run_every_tool": False,
                "lane_execution_tool": role_class == "TASK_EXECUTION",
                "cross_cutting_attachment_is_action_owner": False,
                "condition_true_requires_execution_or_visible_failure": True,
            }
        )
    sector_pairings: list[dict[str, Any]] = []
    for lane in values["lanes"]["lanes"]:
        lane_id = str(lane["lane_id"])
        lane_rows = [
            row for row in unified_pairings if lane_id in row["eligible_lanes"]
        ]
        execution_tools = sorted(
            str(row["tool"])
            for row in lane_rows
            if row["role_class"] == "TASK_EXECUTION"
        )
        transport_adapters = sorted(
            str(row["tool"])
            for row in lane_rows
            if row["role_class"] == "TRANSPORT_OR_ORCHESTRATION"
        )
        external_services = sorted(
            str(row["tool"])
            for row in lane_rows
            if row["role_class"] == "EXTERNAL_SERVICE_OR_STORE"
        )
        cross_cutting_attachments = sorted(
            str(row["tool"])
            for row in lane_rows
            if row["role_class"] == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
        )
        tools = execution_tools
        workflows = sorted(
            {
                (str(item["skill"]), str(item["workflow"]))
                for row in lane_rows
                if row["role_class"] == "TASK_EXECUTION"
                for item in row["eligible_skill_workflows"]
            }
        )
        execution_action_classes = sorted(
            {
                str(action_class)
                for row in lane_rows
                if row["role_class"] == "TASK_EXECUTION"
                for action_class in row["action_classes"]
            }
        )
        sector_pairings.append(
            {
                **non_circular_surface_identity(dict(lane)),
                "eligible_tools": tools,
                "eligible_tool_count": len(tools),
                "task_execution_tools": execution_tools,
                "transport_orchestration_adapters": transport_adapters,
                "external_services_or_stores": external_services,
                "observability_evaluation_attachments": cross_cutting_attachments,
                "tool_role_classes_are_mutually_exclusive": True,
                "execution_action_classes": execution_action_classes,
                "eligible_skill_workflows": [
                    {"skill": skill, "workflow": workflow}
                    for skill, workflow in workflows
                ],
                "sqlite_mmd_dot_json_tools_pointer_manifest_paired": True,
                "internal_sdk_paired": True,
                "outer_sdk_paired": True,
                "env_uop_paired": True,
                "hook_registry_paired": True,
                "receipt_edge_required": True,
            }
        )
    requirement_by_tool = {
        str(row["tool"]): dict(row) for row in values["tools"]["requirements"]
    }
    unified_by_tool = {str(row["tool"]): row for row in unified_pairings}
    authority_pairings: list[dict[str, Any]] = []
    for authority in values["authorities"]["authorities"]:
        authority_id = str(authority["authority_id"])
        authority_tools = sorted(
            tool
            for tool, requirement in requirement_by_tool.items()
            if authority_id in set(requirement["surfaces"])
            or "all_named_authorities" in set(requirement["surfaces"])
            or (
                bool(authority.get("persistent_sqlite_authority"))
                and "every_sqlite_authority" in set(requirement["surfaces"])
            )
        )
        execution_tools = [
            tool
            for tool in authority_tools
            if unified_by_tool[tool]["role_class"] == "TASK_EXECUTION"
        ]
        transport_adapters = [
            tool
            for tool in authority_tools
            if unified_by_tool[tool]["role_class"] == "TRANSPORT_OR_ORCHESTRATION"
        ]
        external_services = [
            tool
            for tool in authority_tools
            if unified_by_tool[tool]["role_class"] == "EXTERNAL_SERVICE_OR_STORE"
        ]
        attachments = [
            tool
            for tool in authority_tools
            if unified_by_tool[tool]["role_class"]
            == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
        ]
        execution_action_classes = sorted(
            {
                str(action_class)
                for tool in execution_tools
                for action_class in unified_by_tool[tool]["action_classes"]
            }
        )
        authority_pairings.append(
            {
                **non_circular_surface_identity(dict(authority)),
                "eligible_tools": execution_tools,
                "eligible_tool_count": len(execution_tools),
                "task_execution_tools": execution_tools,
                "transport_orchestration_adapters": transport_adapters,
                "external_services_or_stores": external_services,
                "observability_evaluation_attachments": attachments,
                "tool_role_classes_are_mutually_exclusive": True,
                "execution_action_classes": execution_action_classes,
                "sqlite_mmd_dot_json_tools_pointer_manifest_paired": True,
                "internal_sdk_paired": True,
                "outer_sdk_paired": True,
                "env_uop_paired": True,
                "hook_registry_paired": True,
                "memory_role_remains_authority_specific": True,
                "receipt_edge_required": True,
            }
        )
    sdk_binding_by_action = {
        Path(path).name.removesuffix(".action.v1.json"): str(path)
        for path in values["sdk"]["action_bindings"]
    }
    mcp_binding_by_action = {
        Path(path).name.removesuffix(".binding.v1.json"): str(path)
        for path in values["mcp"]["action_bindings"]
    }
    schema_by_action = {
        Path(path).name.removesuffix(".schema.json"): str(path)
        for path in values["schemas"]["public_action_schema_members"]
    }
    current_route_by_action = {
        str(row["tool"]): dict(row)
        for row in values["current_routes"]["public_tool_routes"]
    }

    def partition_candidates(tool_candidates: list[str]) -> dict[str, list[str]]:
        return {
            role_class: [
                tool
                for tool in tool_candidates
                if unified_by_tool[tool]["role_class"] == role_class
            ]
            for role_class in TOOL_ROLE_CLASSES
        }

    action_boundary_hook_events = [
        {
            "event_number": int(event["event_number"]),
            "event": str(event["event"]),
            "event_sha256": str(event["event_sha256"]),
            "workflow_contract_sha256": str(event["workflow_contract_sha256"]),
            "workflow_contract": dict(event["workflow_contract"]),
        }
        for event in values["hooks"]["events"]
        if dict(event["workflow_contract"]).get("public_action_boundary") is True
    ]
    expected_action_boundary_events = {
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
    }
    if {row["event"] for row in action_boundary_hook_events} != (
        expected_action_boundary_events
    ):
        raise ValueError("Public-action hook boundary mapping is incomplete.")

    action_pairings: list[dict[str, Any]] = []
    for action in actions:
        action_name = str(action["name"])
        action_class = action_classes[action_name]
        tool_candidates = sorted(
            row["tool"]
            for row in unified_pairings
            if action_name in row["eligible_public_actions"]
        )
        candidates_by_role = partition_candidates(tool_candidates)
        governance_only_fallback = not candidates_by_role["TASK_EXECUTION"]
        if governance_only_fallback:
            tool_candidates = sorted(
                set(tool_candidates)
                | {
                    str(row["tool"])
                    for row in unified_pairings
                    if row["role_class"] == "TASK_EXECUTION"
                    and "GOVERNANCE" in row["action_classes"]
                }
            )
            candidates_by_role = partition_candidates(tool_candidates)
        route_contract = dict(action.get("route_contract") or {})
        action_pairings.append(
            {
                "action": action_name,
                "action_class": action_class,
                "schema_sha256": str(action["schema_sha256"]),
                "schema_path": schema_by_action[action_name],
                "sdk_binding": sdk_binding_by_action[action_name],
                "mcp_binding": mcp_binding_by_action[action_name],
                "current_route": current_route_by_action[action_name],
                "owner_skill": route_contract.get("owner_skill"),
                "skill_workflows": list(route_contract.get("skill_workflows") or []),
                "eligible_tools": candidates_by_role["TASK_EXECUTION"],
                "eligible_tool_count": len(candidates_by_role["TASK_EXECUTION"]),
                "governance_only_fallback": governance_only_fallback,
                "task_execution_tools": candidates_by_role["TASK_EXECUTION"],
                "transport_orchestration_adapters": candidates_by_role[
                    "TRANSPORT_OR_ORCHESTRATION"
                ],
                "external_services_or_stores": candidates_by_role[
                    "EXTERNAL_SERVICE_OR_STORE"
                ],
                "observability_evaluation_attachments": [
                    str(row["tool"])
                    for row in unified_pairings
                    if row["role_class"] == "OBSERVABILITY_OR_EVALUATION_ATTACHMENT"
                    and (
                        "all_workflows" in row["surfaces"]
                        or action_name in row["surfaces"]
                        or _tool_classes_match_action(
                            action_class,
                            set(row["cross_cutting_attachment_action_classes"]),
                        )
                    )
                ],
                "env_uop_paired": True,
                "action_boundary_hook_events": action_boundary_hook_events,
                "action_boundary_hook_event_count": len(action_boundary_hook_events),
                "ordered_hook_registry_paired": (
                    {row["event"] for row in action_boundary_hook_events}
                    == expected_action_boundary_events
                ),
                "source_module_registry_paired": True,
                "manifest_pairing_required": True,
                "separate_command_required": False,
            }
        )
    skill_pairings = [
        {
            "skill": str(skill["name"]),
            "description": str(skill["description"]),
            "member_count": int(skill["member_count"]),
            "members": list(skill["members"]),
            "workflow": dict(skill["workflow"]),
            "public_actions": sorted(
                {
                    str(tool)
                    for group in dict(skill["workflow"])["ordered_tool_groups"]
                    for tool in group["tools"]
                }
            ),
            "sdk_mcp_schema_pairing_required": True,
            "separate_command_required": False,
            "workflow_count_is_fixed_ceiling": False,
        }
        for skill in values["skills"]["skills"]
    ]
    hook_pairings = [
        {
            **dict(event),
            "sdk_event_binding_required": True,
            "ordered_handler_isolation_required": True,
            "business_logic_owner": (
                "INSTALLED_EVIDENCE_LANE_CODE_LIFECYCLE_SKILL"
                if dict(event["workflow_contract"]).get("skill_consumer") is not None
                else "NO_DURABLE_CONSUMER_BEST_EFFORT_TRANSPORT_ONLY"
            ),
            "hook_is_transport_or_lifecycle_event_handler": True,
            "hook_event_count_is_fixed_ceiling": False,
        }
        for event in values["hooks"]["events"]
    ]
    counts = {
        "public_actions": len(catalog_names),
        "sdk_action_bindings": len(sdk_names),
        "mcp_action_bindings": len(mcp_names),
        "skills": int(values["skills"]["skill_count"]),
        "hook_events": int(values["hooks"]["event_count"]),
        "hook_handler_actions": int(values["hooks"]["handler_action_count"]),
        "sector_lanes": int(values["lanes"]["lane_count"]),
        "named_root_authorities": int(values["authorities"]["authority_count"]),
        "schema_files": int(values["schemas"]["schema_file_count"]),
        "source_modules": int(values["modules"]["module_count"]),
        "tool_requirements": len(tool_names),
        "ecosystem_adapters": int(ecosystem["adapter_count"]),
    }
    tool_taxonomy_rows = [
        {
            "tool": str(row["tool"]),
            "role_class": str(row["role_class"]),
            "selection_axis": str(row["selection_axis"]),
            "requirement": str(row["requirement"]),
            "action_classes": list(row["action_classes"]),
            "eligible_lanes": list(row["eligible_lanes"]),
            "run_only_when_condition_true": True,
            "condition_true_requires_execution_or_fail_visible": True,
        }
        for row in unified_pairings
    ]
    tool_taxonomy_counts = {
        role_class: sum(
            1 for row in tool_taxonomy_rows if row["role_class"] == role_class
        )
        for role_class in TOOL_ROLE_CLASSES
    }
    master_workflow = _master_workflow_model(
        lanes=[dict(row) for row in values["lanes"]["lanes"]],
        authorities=[dict(row) for row in values["authorities"]["authorities"]],
        workflow_step_count=len(registered_skill_workflows),
        action_count=len(catalog_names),
        hook_events=[dict(row) for row in values["hooks"]["events"]],
        tool_taxonomy_counts=tool_taxonomy_counts,
    )
    if catalog_names != sdk_names or catalog_names != mcp_names:
        raise ValueError(
            "Public actions are not paired one-for-one across SDK and MCP."
        )
    if values["skills"].get("separate_command_count") != 0:
        raise ValueError("The purged separate command layer reappeared.")
    if values["routing"].get("requirement_count") != len(tool_names):
        raise ValueError("Tool routing does not cover every declared requirement.")
    if values["tunnel"].get("requirement_count") != len(tool_names):
        raise ValueError("Tunnel routing does not cover every declared requirement.")
    if any(
        not row["implementation_owner"]
        or not row["surfaces"]
        or row["route_binding_count"] != len(row["route_bindings"])
        or row["route_binding_count"] < 1
        for row in unified_pairings
    ):
        raise ValueError("One or more declared tools have no executable route binding.")
    if tuple(str(row["event"]) for row in hook_pairings) != HOOK_EVENT_NAMES or any(
        not dict(row.get("workflow_contract") or {}).get("host_timing")
        or not dict(row.get("workflow_contract") or {}).get("skill_action")
        or not row.get("workflow_contract_sha256")
        or dict(row["workflow_contract"])
        != HOOK_EVENT_WORKFLOW_CONTRACTS[str(row["event"])]
        for row in hook_pairings
    ):
        raise ValueError("Hook workflow timing/consumer pairing is incomplete.")
    body = {
        "schema": PLUGIN_ARCHITECTURE_SCHEMA,
        "status": "PASS",
        "architecture_kind": "REGISTRY_DRIVEN_UNIVERSAL_WORKFLOW",
        "routing_stages": list(UNIVERSAL_ROUTING_STAGES),
        "master_workflow": master_workflow,
        "counts": counts,
        "workflow_registry": {
            "registered_workflow_step_count": len(registered_skill_workflows),
            "rows": registered_skill_workflows,
            "selection_is_from_skill_action_schema_and_current_state": True,
            "current_human_entrypoints_are_not_a_workflow_limit": True,
            "human_entrypoints_derive_from_registered_skill_routes": True,
            "future_registered_workflows_inherit_universal_routing": True,
        },
        "tool_taxonomy": {
            "schema": "evidence-lane.tool-role-taxonomy.v1",
            "status": "PASS",
            "role_classes": list(TOOL_ROLE_CLASSES),
            "counts": tool_taxonomy_counts,
            "rows": tool_taxonomy_rows,
            "role_classes_are_mutually_exclusive": True,
            "total_equals_current_tool_registry": (
                sum(tool_taxonomy_counts.values()) == len(tool_taxonomy_rows)
            ),
            "current_total_is_not_a_ceiling": True,
        },
        "unified_tool_workflow_pairing": {
            "schema": "evidence-lane.unified-tool-workflow-pairing.v1",
            "status": "PASS",
            "tool_count": len(unified_pairings),
            "tool_partition_count": 1,
            "legacy_core_plus_extension_split": False,
            "all_tools_use_same_routing_law": True,
            "all_tools_pair_internal_sdk_outer_sdk_env_uop_hooks_hil_and_receipts": True,
            "rows": unified_pairings,
        },
        "authority_tool_workflow_pairing": {
            "schema": "evidence-lane.authority-tool-workflow-pairing.v1",
            "status": "PASS",
            "sector_count": len(sector_pairings),
            "named_root_authority_count": len(authority_pairings),
            "sector_rows": sector_pairings,
            "authority_rows": authority_pairings,
            "counts_are_derived_not_fixed": True,
            "all_surfaces_use_same_registry_driven_pairing_law": True,
        },
        "action_skill_hook_schema_pairing": {
            "schema": "evidence-lane.action-skill-hook-schema-pairing.v1",
            "status": "PASS",
            "action_count": len(action_pairings),
            "skill_count": len(skill_pairings),
            "hook_event_count": len(hook_pairings),
            "actions": action_pairings,
            "skills": skill_pairings,
            "hook_events": hook_pairings,
            "workflow_lifecycle_hook_events": hook_pairings,
            "public_action_boundary_hook_events": action_boundary_hook_events,
            "counts_are_derived_not_fixed": True,
            "first_class_workflow_requires_first_class_skill": True,
            "separate_command_layer_present": False,
            "all_pairings_generated_from_current_source_graph": True,
        },
        "memory_authority_map": list(MEMORY_AUTHORITY_MAP),
        "pairing": {
            "public_action_sdk_mcp_one_to_one": True,
            "skills_route_to_typed_actions": True,
            "hooks_are_ordered_side_effect_handlers_not_action_owners": True,
            "env_and_uop_are_distinct_executable_decision_authorities": True,
            "sector_and_named_authorities_remain_distinct": True,
            "internal_sdk_and_outer_sdk_remain_distinct": True,
            "fastmcp_is_transport_not_action_authority": True,
            "openai_agents_sdk_is_subordinate_to_codex": True,
            "remote_indexes_never_replace_sqlite_authority": True,
            "tunnel_is_multi_identity_transport_not_scheduler": True,
            "all_declared_tools_share_one_architecture": True,
        },
        "data_touch_law": {
            "resolve_schema_before_read_or_write": True,
            "resolve_project_task_lane_and_authority_identity": True,
            "env_selects_context_host_dependency_grant_and_locality": True,
            "uop_selects_operator_formula_gate_and_ordered_fallback": True,
            "only_selected_tool_or_adapter_runs": True,
            "validate_result_before_authority_write": True,
            "append_exact_receipt_after_result": True,
            "credentials_are_external_and_never_persisted": True,
        },
        "memory_rules": {
            "generic_merged_agent_memory": False,
            "cross_links_are_hash_or_receipt_bound": True,
            "retention_is_owner_specific": True,
            "promotion_requires_the_owning_hil_when_applicable": True,
        },
        "ecosystem_adapter_catalog_sha256": ecosystem["receipt_sha256"],
        "source_registries": {
            name: (
                {
                    "path": path.relative_to(root).as_posix(),
                    "hash_authority": "manifests/executable-surface-registry.v1.json",
                    "direct_hash_embedded": False,
                    "reason": "DERIVED_REGISTRY_AVOIDS_SELF_REFERENTIAL_HASH_CYCLE",
                }
                if name in {"mcp", "sdk", "authorities", "lanes", "schemas"}
                else {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": sha256_file(path),
                    "direct_hash_embedded": True,
                }
            )
            for name, path in paths.items()
        },
        "prompt_examples_define_architecture": False,
        "future_workflows_must_register_the_same_pairings": True,
        "human_entrypoint_count_defines_backend_workflow_limit": False,
        "human_entrypoints_derive_from_registered_skill_routes": True,
        "historical_fallback_allowed": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _load_or_build_architecture(
    architecture: dict[str, Any] | None,
) -> dict[str, Any]:
    if architecture is not None:
        return architecture
    root = resolve_plugin_root(__file__)
    generated = root / "toolchains" / "universal-plugin-architecture.v1.json"
    if generated.is_file():
        return _read(generated)
    return build_universal_plugin_architecture(root)


def _master_semantic_graph(architecture: dict[str, Any]) -> SemanticGraph:
    model = dict(architecture["master_workflow"])
    graph = SemanticGraph(
        "universal_plugin_architecture",
        direction="TB",
        role="EXECUTABLE_WORKFLOW",
    )
    nodes = [dict(row) for row in model["nodes"]]
    for group in model["groups"]:
        graph.begin_group(
            str(group["group_id"]),
            str(group["label"]),
            direction="TB",
        )
        for row in nodes:
            if row["group_id"] == group["group_id"]:
                graph.add_node(
                    str(row["node_id"]),
                    str(row["label"]),
                    str(row["kind"]),
                )
        graph.end_group()
    for row in model["edges"]:
        graph.add_edge(
            str(row["source"]),
            str(row["target"]),
            str(row["label"]) if row.get("label") is not None else None,
            conditional=bool(row.get("conditional")),
        )
    return graph


def render_universal_architecture_mmd(
    architecture: dict[str, Any] | None = None,
) -> str:
    graph = _master_semantic_graph(_load_or_build_architecture(architecture))
    mmd, _receipt = graph.render_mermaid()
    return mmd


def render_universal_architecture_dot(
    architecture: dict[str, Any] | None = None,
) -> str:
    graph = _master_semantic_graph(_load_or_build_architecture(architecture))
    dot, _receipt = graph.render_dot()
    return dot


def build_dedicated_skill_workflows(
    architecture: dict[str, Any],
) -> dict[str, Any]:
    """Compile one complete action/SDK/ENV/UOP/tool workflow per skill."""

    surface = dict(architecture["action_skill_hook_schema_pairing"])
    actions = {str(row["action"]): dict(row) for row in surface["actions"]}
    unified = {
        str(row["tool"]): dict(row)
        for row in architecture["unified_tool_workflow_pairing"]["rows"]
    }
    hook_events = [str(row["event"]) for row in surface["hook_events"]]
    from .current_route_registry import current_implementation_registry
    from .lanes import LANE_REGISTRY

    code_source_route = next(
        row
        for row in current_implementation_registry()["capabilities"]
        if row["capability"] == "code_source_and_study_brain_routing"
    )
    skill_models = []
    for skill in surface["skills"]:
        skill_name = str(skill["skill"])
        groups = [dict(row) for row in dict(skill["workflow"])["ordered_tool_groups"]]
        workflow_names = list(dict.fromkeys(str(row["workflow"]) for row in groups))
        steps = []
        for group in groups:
            workflow_name = str(group["workflow"])
            for action_name in group["tools"]:
                action = actions[str(action_name)]
                role_tools = {
                    "task_execution": list(action["task_execution_tools"]),
                    "transport_orchestration": list(
                        action["transport_orchestration_adapters"]
                    ),
                    "external_services_or_stores": list(
                        action["external_services_or_stores"]
                    ),
                    "observability_evaluation_attachments": list(
                        action["observability_evaluation_attachments"]
                    ),
                }
                all_tools = {tool for values in role_tools.values() for tool in values}
                candidate_lanes = sorted(
                    {
                        str(lane)
                        for tool in all_tools
                        for lane in unified[tool]["eligible_lanes"]
                    }
                )
                steps.append(
                    {
                        "workflow": workflow_name,
                        "order": int(group["order"]),
                        "action": str(action_name),
                        "action_class": str(action["action_class"]),
                        "schema_path": str(action["schema_path"]),
                        "schema_sha256": str(action["schema_sha256"]),
                        "sdk_binding": str(action["sdk_binding"]),
                        "mcp_binding": str(action["mcp_binding"]),
                        "current_route": dict(action["current_route"]),
                        "candidate_lanes": candidate_lanes,
                        "tool_roles": role_tools,
                        "env_contract": (
                            "SELECT_CONTEXT_HOST_LANE_AUTHORITY_LOCALITY_AVAILABILITY_GRANT"
                        ),
                        "uop_contract": (
                            "APPLY_OPERATORS_FORMULAS_WORK_PRIVACY_DISCLOSURE_HIL_AND_FALLBACK_GATES"
                        ),
                        "hook_contract": {
                            "registry_events": hook_events,
                            "run_only_events_emitted_by_current_action": True,
                            "hooks_are_not_action_owners": True,
                        },
                        "receipt_required": True,
                        "hil_inference_allowed": False,
                    }
                )
        core = {
            "schema": "evidence-lane.dedicated-skill-workflow.v1",
            "status": "PASS",
            "skill": skill_name,
            "description": str(skill["description"]),
            "workflow_names": workflow_names,
            "workflow_count": len(workflow_names),
            "ordered_group_count": len(groups),
            "action_step_count": len(steps),
            "steps": steps,
            "missing_tool_behavior": dict(skill["workflow"])["missing_tool_behavior"],
            "internal_sdk_is_execution_owner": True,
            "env_and_uop_are_distinct": True,
            "outer_sdk_and_mcp_are_transport_only": True,
            "authority_and_lane_resolve_per_action_and_state": True,
            "every_condition_true_step_runs_or_fails_visible": True,
            "workflow_or_count_ceiling": False,
        }
        if skill_name == "evi-source-intake":
            core["lane_dispatch_contract"] = {
                "schema": "evidence-lane.source-intake-lane-dispatch.v1",
                "status": "PASS",
                "lanes": [
                    {
                        "lane_id": lane_id,
                        "display_label": lane.display_label,
                        "aliases": list(lane.aliases),
                        "source_types": list(lane.source_types),
                        "extensions": list(lane.extensions),
                        "parser_id": lane.parser_id,
                        "chunker": lane.chunker_version,
                        "fts_table": lane.fts_table,
                        "schema_tables": list(lane.schema_contract),
                        "mutation_policy": lane.mutation_policy,
                    }
                    for lane_id, lane in LANE_REGISTRY.items()
                ],
                "lane_count": len(LANE_REGISTRY),
                "lane_count_is_behavior_ceiling": False,
                "prompt_content_and_exact_overrides_drive_dispatch": True,
                "every_registered_lane_is_conditionally_reachable": True,
                "code_source_route": dict(code_source_route),
                "github_code_materializes_to_local_code_only_when_authorized": True,
                "additional_code_sources_are_lane_scoped_study_brains": True,
                "study_brain_federation_requires_explicit_hash_only_bigger_universe_route": True,
                "non_code_projects_use_same_recipe_mode_lane_dispatch": True,
                "project_root_identity_is_not_inferred_from_lane_selection": True,
            }
        skill_models.append(
            {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
        )
    body = {
        "schema": "evidence-lane.dedicated-skill-workflow-registry.v1",
        "status": "PASS",
        "skill_count": len(skill_models),
        "workflow_count": sum(row["workflow_count"] for row in skill_models),
        "action_step_count": sum(row["action_step_count"] for row in skill_models),
        "skills": skill_models,
        "skill_set_is_derived_from_current_registry": True,
        "stale_skill_workflow_retained": False,
        "counts_are_current_snapshot_not_ceiling": True,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def render_dedicated_skill_workflow(
    skill: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    graph = SemanticGraph(
        _safe_graph_id("skill", str(skill["skill"])).lower(),
        direction="TB",
        role="EXECUTABLE_WORKFLOW",
    )
    graph.add_node("ENTRY", f"{skill['skill']} intent", "root")
    graph.add_node("EXIT", "Exact result / stop / next workflow", "output")
    step_nodes: dict[tuple[str, int, str], str] = {}
    for workflow_name in skill["workflow_names"]:
        workflow_steps = [
            row for row in skill["steps"] if row["workflow"] == workflow_name
        ]
        group_id = _safe_graph_id("WF", str(workflow_name))
        graph.begin_group(group_id, str(workflow_name), direction="TB")
        workflow_node = f"{group_id}_START"
        graph.add_node(workflow_node, str(workflow_name), "lifecycle")
        for ordinal, row in enumerate(workflow_steps, start=1):
            action_node = f"{group_id}_ACTION_{ordinal:03d}"
            graph.add_node(
                action_node,
                f"{row['order']}. {row['action']} [{row['action_class']}]",
                "semantic",
            )
            step_nodes[(str(workflow_name), int(row["order"]), str(row["action"]))] = (
                action_node
            )
        graph.end_group()
        graph.add_edge("ENTRY", workflow_node)
        if workflow_steps:
            graph.add_edge(
                workflow_node,
                step_nodes[
                    (
                        str(workflow_name),
                        int(workflow_steps[0]["order"]),
                        str(workflow_steps[0]["action"]),
                    )
                ],
            )
        for prior, current in pairwise(workflow_steps):
            graph.add_edge(
                step_nodes[
                    (str(workflow_name), int(prior["order"]), str(prior["action"]))
                ],
                step_nodes[
                    (
                        str(workflow_name),
                        int(current["order"]),
                        str(current["action"]),
                    )
                ],
                "ordered action",
            )
    graph.begin_group("EXECUTION", "Shared typed execution contract", direction="TB")
    execution_nodes = (
        ("SCHEMA", "Current input/output schema", "semantic"),
        ("INTERNAL_SDK", "Internal SDK execution owner", "semantic"),
        ("ENV", "ENV15 environment selection", "semantic"),
        ("UOP", "UOP15 governance and gates", "warn"),
        ("AUTHORITY", "Owning authority and lane", "semantic"),
        ("TOOLS", "Conditional tool role resolution", "semantic"),
        ("OUTER", "Outer SDK / FastMCP / native compatibility", "semantic"),
        ("HOOKS", "Only emitted ordered hooks", "semantic"),
        ("VALIDATE", "Validate schema, result and effects", "warn"),
        ("RECEIPT", "Exact receipt", "output"),
    )
    for node_id, label, kind in execution_nodes:
        graph.add_node(node_id, label, kind)
    graph.end_group()
    for source, target in zip(
        [row[0] for row in execution_nodes][:-1],
        [row[0] for row in execution_nodes][1:],
        strict=True,
    ):
        graph.add_edge(source, target)
    lane_dispatch = skill.get("lane_dispatch_contract")
    if isinstance(lane_dispatch, dict):
        graph.begin_group(
            "LANE_DISPATCH",
            "Prompt/content-driven registered lane dispatch",
            direction="TB",
        )
        for lane in lane_dispatch["lanes"]:
            lane_node = _safe_graph_id("LANE", str(lane["lane_id"]))
            graph.add_node(
                lane_node,
                (
                    f"{lane['lane_id']}: {lane['parser_id']} -> "
                    f"{lane['chunker']} -> {lane['fts_table']}"
                ),
                "semantic",
            )
        graph.end_group()
        classify_nodes = [
            node_id
            for (_workflow, _order, action), node_id in step_nodes.items()
            if action == "source_intake_classify"
        ]
        for lane in lane_dispatch["lanes"]:
            lane_node = _safe_graph_id("LANE", str(lane["lane_id"]))
            for classify_node in classify_nodes:
                graph.add_edge(
                    classify_node,
                    lane_node,
                    "selected from prompt/content/override",
                    conditional=True,
                )
            graph.add_edge(lane_node, "SCHEMA", "lane-owned execution")
    for action_node in step_nodes.values():
        graph.add_edge(action_node, "SCHEMA", "typed execution")
    graph.add_edge("RECEIPT", "EXIT")
    return graph.render_pair()


def build_surface_workflows(architecture: dict[str, Any]) -> dict[str, Any]:
    """Compile one real workflow contract per sector and named authority."""

    from .lanes import CORE_SCHEMA_TABLES, LANE_REGISTRY

    unified = {
        str(row["tool"]): dict(row)
        for row in architecture["unified_tool_workflow_pairing"]["rows"]
    }
    hook_events = [
        str(row["event"])
        for row in architecture["action_skill_hook_schema_pairing"]["hook_events"]
    ]

    def model(
        row: dict[str, Any],
        *,
        kind: str,
        identity_key: str,
    ) -> dict[str, Any]:
        surface_id = str(row[identity_key])
        tools_by_role = {
            "task_execution": list(row["task_execution_tools"]),
            "transport_orchestration": list(row["transport_orchestration_adapters"]),
            "external_services_or_stores": list(row["external_services_or_stores"]),
            "observability_evaluation_attachments": list(
                row["observability_evaluation_attachments"]
            ),
        }
        task_execution_tools = set(tools_by_role["task_execution"])
        public_actions = sorted(
            {
                str(action)
                for tool in task_execution_tools
                for action in unified[tool]["eligible_public_actions"]
            }
        )
        workflows = sorted(
            {
                (str(item["skill"]), str(item["workflow"]))
                for tool in task_execution_tools
                for item in unified[tool]["eligible_skill_workflows"]
            }
        )
        tool_phase_order = {
            "GOVERNANCE": "GOVERN",
            "CODE": "PARSE_FACTS",
            "DOCUMENT": "PARSE_FACTS",
            "OCR_MEDIA": "PARSE_FACTS",
            "DATA": "PARSE_FACTS",
            "WEB_RESEARCH": "PARSE_FACTS",
            "RETRIEVAL": "INDEX_QUERY",
            "GRAPH": "TOPOLOGY",
            "EVALUATION": "VALIDATE",
            "OBSERVABILITY": "OBSERVE",
            "DEPLOYMENT": "DELIVER",
            "RUNTIME_API": "TRANSPORT",
            "MCP_COMPOSITION": "TRANSPORT",
        }
        tool_phase_bindings = [
            {
                "tool": tool,
                "role_class": unified[tool]["role_class"],
                "action_classes": list(unified[tool]["action_classes"]),
                "phases": sorted(
                    {
                        tool_phase_order[action_class]
                        for action_class in unified[tool]["action_classes"]
                        if action_class in tool_phase_order
                    }
                ),
                "condition_true_requires_execution_or_visible_failure": True,
            }
            for role_tools in tools_by_role.values()
            for tool in role_tools
        ]
        non_circular_pairing = {
            key: value
            for key, value in row.items()
            if key
            not in {
                "manifest_sha256",
                "sqlite_template_sha256",
                "database_sha256",
            }
        }
        surface_execution_contract: dict[str, Any]
        if kind == "PROJECT_SECTOR":
            lane = LANE_REGISTRY[surface_id]
            lane_specific_tables = [
                table
                for table in lane.schema_contract
                if table not in CORE_SCHEMA_TABLES and table != lane.fts_table
            ]
            surface_execution_contract = {
                "contract_kind": "LANE_SPECIFIC_EXECUTION",
                "source_types": list(lane.source_types),
                "extensions": list(lane.extensions),
                "parser_id": lane.parser_id,
                "chunker": lane.chunker_version,
                "schema_fact_tables": lane_specific_tables,
                "fts_table": lane.fts_table,
                "retrieval_strategy": (
                    "CONTENTLESS_FTS5_BM25_THEN_BOUNDED_QUERY_TIME_TFIDF"
                ),
                "mutation_policy": lane.mutation_policy,
                "phases": [
                    {
                        "id": "SOURCE_CLASSIFY",
                        "label": "Classify prompt/content/override into this lane",
                    },
                    {
                        "id": "EXACT_SOURCE_CAS",
                        "label": "Hash and store exact source bytes once",
                    },
                    {"id": "LANE_PARSE", "label": f"Parse with {lane.parser_id}"},
                    {
                        "id": "LANE_CHUNK",
                        "label": f"Chunk once with {lane.chunker_version}",
                    },
                    {
                        "id": "STRUCTURED_FACTS",
                        "label": f"Project lane facts into {len(lane_specific_tables)} lane tables",
                    },
                    {
                        "id": "SQLITE_COMMIT",
                        "label": "Write owning SQLite generation atomically",
                    },
                    {
                        "id": "RETRIEVAL_INDEX",
                        "label": f"Refresh {lane.fts_table} and reusable indexes",
                    },
                    {
                        "id": "TOPOLOGY_GRAPH",
                        "label": "Derive lane-specific MMD and DOT topology",
                    },
                    {
                        "id": "RESULT_VALIDATE",
                        "label": "Validate schema, tools, hashes and authority effects",
                    },
                    {
                        "id": "ATOMIC_REFRESH",
                        "label": "Swap changed generation; reuse unchanged atoms",
                    },
                ],
                "edges": [
                    ["SOURCE_CLASSIFY", "EXACT_SOURCE_CAS", False],
                    ["EXACT_SOURCE_CAS", "LANE_PARSE", False],
                    ["LANE_PARSE", "LANE_CHUNK", False],
                    ["LANE_CHUNK", "STRUCTURED_FACTS", False],
                    ["STRUCTURED_FACTS", "SQLITE_COMMIT", False],
                    ["SQLITE_COMMIT", "RETRIEVAL_INDEX", False],
                    ["SQLITE_COMMIT", "TOPOLOGY_GRAPH", False],
                    ["RETRIEVAL_INDEX", "RESULT_VALIDATE", False],
                    ["TOPOLOGY_GRAPH", "RESULT_VALIDATE", False],
                    ["RESULT_VALIDATE", "ATOMIC_REFRESH", True],
                ],
            }
        else:
            surface_execution_contract = {
                "contract_kind": "AUTHORITY_SPECIFIC_EXECUTION",
                "persistent_sqlite_authority": bool(
                    row.get("persistent_sqlite_authority")
                ),
                "authority_role": row.get("authority_role"),
                "memory_role": row.get("memory_role"),
                "phases": [
                    {
                        "id": "AUTHORITY_BIND",
                        "label": "Bind exact authority identity and contract",
                    },
                    {
                        "id": "AUTHORITY_OPERATION",
                        "label": "Run the authority-owned read or write",
                    },
                    {
                        "id": "AUTHORITY_INDEX",
                        "label": "Refresh applicable SQLite/FTS/index projection",
                    },
                    {
                        "id": "AUTHORITY_GRAPH",
                        "label": "Refresh authority-specific MMD and DOT",
                    },
                    {
                        "id": "RESULT_VALIDATE",
                        "label": "Validate effects, boundaries and hashes",
                    },
                    {
                        "id": "ATOMIC_REFRESH",
                        "label": "Commit changed generation; reuse unchanged atoms",
                    },
                ],
                "edges": [
                    ["AUTHORITY_BIND", "AUTHORITY_OPERATION", False],
                    ["AUTHORITY_OPERATION", "AUTHORITY_INDEX", True],
                    ["AUTHORITY_OPERATION", "AUTHORITY_GRAPH", True],
                    ["AUTHORITY_OPERATION", "RESULT_VALIDATE", False],
                    ["AUTHORITY_INDEX", "RESULT_VALIDATE", False],
                    ["AUTHORITY_GRAPH", "RESULT_VALIDATE", False],
                    ["RESULT_VALIDATE", "ATOMIC_REFRESH", True],
                ],
            }
        core = {
            "schema": "evidence-lane.surface-workflow.v1",
            "status": "PASS",
            "surface_kind": kind,
            "surface_id": surface_id,
            "source_pairing": non_circular_pairing,
            "tool_roles": tools_by_role,
            "tool_role_counts": {
                key: len(value) for key, value in tools_by_role.items()
            },
            "tool_phase_bindings": tool_phase_bindings,
            "surface_execution_contract": surface_execution_contract,
            "public_actions": public_actions,
            "public_action_count": len(public_actions),
            "skill_workflows": [
                {"skill": skill, "workflow": workflow} for skill, workflow in workflows
            ],
            "skill_workflow_count": len(workflows),
            "hook_events": hook_events,
            "hook_event_count": len(hook_events),
            "execution_contract": {
                "entry": "EXACT_SURFACE_IDENTITY_AND_CURRENT_STATE",
                "schema": "CURRENT_SURFACE_AND_ACTION_SCHEMAS",
                "internal_sdk": "EXECUTION_OWNER",
                "env": "SELECT_CONTEXT_HOST_LOCALITY_AVAILABILITY_AND_GRANT",
                "uop": "APPLY_OPERATORS_FORMULAS_GATES_AND_ORDERED_FALLBACK",
                "tools": "RUN_ALL_CONDITION_TRUE_STEPS_OR_FAIL_VISIBLE",
                "outer_sdk_mcp": "LOCAL_OR_TRANSPORT_ONLY_NOT_AUTHORITY",
                "hooks": "RUN_ONLY_EMITTED_ORDERED_EVENTS",
                "write": "VALIDATE_THEN_ATOMIC_CHANGED_HASH_REFRESH",
                "receipt": "EXACT_INPUT_ROUTE_RESULT_PROVENANCE",
            },
            "project_pv_baseline_connection": {
                "path": [
                    "PROJECT_PV_ROOT",
                    "AUTHORITY_REGISTRY",
                    f"{kind}:{surface_id}",
                ],
                "connection_only": True,
                "project_registration_or_pv_creation_performed": False,
            },
            "sqlite_mmd_dot_json_tools_manifest_refresh_together": True,
            "unchanged_content_addressed_atoms_reused": True,
            "superseded_route_retained": False,
            "hil_inference_allowed": False,
            "workflow_or_count_ceiling": False,
        }
        return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}

    pairing = architecture["authority_tool_workflow_pairing"]
    sectors = [
        model(dict(row), kind="PROJECT_SECTOR", identity_key="lane_id")
        for row in pairing["sector_rows"]
    ]
    authorities = [
        model(dict(row), kind="NAMED_ROOT_AUTHORITY", identity_key="authority_id")
        for row in pairing["authority_rows"]
    ]
    body = {
        "schema": "evidence-lane.surface-workflow-registry.v1",
        "status": "PASS",
        "sector_count": len(sectors),
        "authority_count": len(authorities),
        "sectors": sectors,
        "authorities": authorities,
        "counts_are_current_snapshot_not_ceiling": True,
        "stale_surface_workflow_retained": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def render_surface_workflow(
    surface: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    graph = SemanticGraph(
        _safe_graph_id(
            "surface",
            f"{surface['surface_kind']}_{surface['surface_id']}",
        ).lower(),
        direction="TB",
        role="EXECUTABLE_WORKFLOW",
    )
    graph.add_node(
        "ENTRY",
        f"{surface['surface_kind']} {surface['surface_id']} exact identity",
        "root",
    )
    graph.add_node("EXIT", "Result / explicit stop / next refresh", "output")
    graph.begin_group("FLOW", "Current surface execution flow", direction="TB")
    flow_nodes = (
        ("SCHEMA", "Resolve current surface + action schemas", "semantic"),
        (
            "SKILLS",
            f"Resolve {surface['skill_workflow_count']} current skill workflows",
            "semantic",
        ),
        (
            "ACTIONS",
            f"Resolve {surface['public_action_count']} current typed actions",
            "semantic",
        ),
        ("INTERNAL_SDK", "Internal SDK execution owner", "semantic"),
        ("ENV", "ENV15 environment selection", "semantic"),
        ("UOP", "UOP15 governance and gates", "warn"),
        ("TOOLS", "Conditional tool role resolution", "semantic"),
        ("OUTER", "Outer SDK / FastMCP / native MCP / local route", "semantic"),
        ("HOOKS", "Only emitted ordered hooks", "semantic"),
        ("VALIDATE", "Validate result, authority and HIL effects", "warn"),
        ("RECEIPT", "Exact refresh and provenance receipt", "output"),
    )
    for node_id, label, kind in flow_nodes:
        graph.add_node(node_id, label, kind)
    graph.end_group()
    graph.add_edge("ENTRY", flow_nodes[0][0])
    route_nodes = [row[0] for row in flow_nodes[:8]]
    for source, target in pairwise(route_nodes):
        graph.add_edge(source, target)
    baseline = surface["project_pv_baseline_connection"]
    graph.begin_group(
        "PROJECT_PV_BASELINE",
        "Project/PV baseline connection (identity only)",
        direction="TB",
    )
    baseline_nodes = []
    for ordinal, label in enumerate(baseline["path"], start=1):
        node_id = f"BASELINE_{ordinal:02d}"
        baseline_nodes.append(node_id)
        graph.add_node(node_id, str(label), "semantic")
    graph.end_group()
    for source, target in pairwise(baseline_nodes):
        graph.add_edge(source, target)
    graph.add_edge(baseline_nodes[-1], "ENTRY", "bind current surface identity")

    surface_contract = surface["surface_execution_contract"]
    phase_rows = list(surface_contract["phases"])
    graph.begin_group(
        "SURFACE_PHASES",
        (
            f"{surface['surface_id']} "
            f"{surface_contract['contract_kind'].replace('_', ' ').lower()}"
        ),
        direction="TB",
    )
    phase_nodes = {}
    for phase in phase_rows:
        node_id = _safe_graph_id("PHASE", str(phase["id"]))
        phase_nodes[str(phase["id"])] = node_id
        graph.add_node(node_id, str(phase["label"]), "semantic")
    graph.end_group()
    phase_targets = {
        str(target) for _source, target, _conditional in surface_contract["edges"]
    }
    phase_roots = [
        str(phase["id"])
        for phase in phase_rows
        if str(phase["id"]) not in phase_targets
    ]
    for root_phase in phase_roots:
        graph.add_edge("OUTER", phase_nodes[root_phase], "surface-local route")
    for source, target, conditional in surface_contract["edges"]:
        graph.add_edge(
            phase_nodes[str(source)],
            phase_nodes[str(target)],
            "when applicable" if conditional else None,
            conditional=bool(conditional),
        )
    result_phase = phase_nodes["RESULT_VALIDATE"]
    atomic_phase = phase_nodes["ATOMIC_REFRESH"]
    graph.add_edge(result_phase, "HOOKS", "read/no-change result", conditional=True)
    graph.add_edge(atomic_phase, "HOOKS", "changed generation")
    graph.add_edge("HOOKS", "VALIDATE")
    graph.add_edge("VALIDATE", "RECEIPT")
    graph.add_edge("RECEIPT", "EXIT")
    graph.add_edge("RECEIPT", "ENTRY", "next conditional refresh", conditional=True)
    graph.begin_group("TOOL_ROLES", "Mutually exclusive tool roles", direction="TB")
    for role_name, count in surface["tool_role_counts"].items():
        graph.add_node(
            _safe_graph_id("ROLE", str(role_name)),
            f"{str(role_name).replace('_', ' ')}: {count} current rows",
            "semantic",
        )
    graph.end_group()
    for role_name in surface["tool_role_counts"]:
        role_node = _safe_graph_id("ROLE", str(role_name))
        graph.add_edge("TOOLS", role_node)
        graph.add_edge(
            role_node, "OUTER", "selected when condition true", conditional=True
        )
    return graph.render_pair()


def _memory_architecture_graph() -> SemanticGraph:
    graph = SemanticGraph(
        "memory_authorities",
        direction="TB",
        role="AUTHORITY_TRAVERSAL",
    )
    graph.add_node("C", "Classified input or result", "root")
    graph.begin_group(
        "MEMORY_SHELLS",
        "Separate memory and authority shells — linked, never merged",
        direction="TB",
    )
    for index, row in enumerate(MEMORY_AUTHORITY_MAP, start=1):
        node = f"M{index}"
        label = str(row["memory_class"]).replace("_", " ")
        owners = ", ".join(str(value) for value in row["owners"])
        graph.add_node(node, f"{label}\n{owners}", "semantic")
    graph.end_group()
    for index, _row in enumerate(MEMORY_AUTHORITY_MAP, start=1):
        graph.add_edge("C", f"M{index}")
    graph.add_edge("M2", "M5", "accepted link only", conditional=True)
    graph.add_edge("M3", "M4", "hash/receipt links", conditional=True)
    graph.add_edge("M4", "M6", "never auto-promotes", conditional=True)
    return graph


def render_memory_architecture_mmd() -> str:
    return _memory_architecture_graph().render_mermaid()[0]


def render_memory_architecture_dot() -> str:
    return _memory_architecture_graph().render_dot()[0]


__all__ = [
    "MEMORY_AUTHORITY_MAP",
    "PLUGIN_ARCHITECTURE_SCHEMA",
    "UNIVERSAL_ROUTING_STAGES",
    "build_universal_plugin_architecture",
    "render_memory_architecture_dot",
    "render_memory_architecture_mmd",
    "render_universal_architecture_dot",
    "render_universal_architecture_mmd",
]
