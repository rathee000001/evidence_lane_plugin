"""Build the skill/MCP/service/SDK route matrix for research Steps 6-9."""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from research_db import append_audit_event, connect, transition_step, upsert_fts, utc_now


WORKSPACE = Path(r"F:\test codex")
SOURCE = WORKSPACE / "plugins" / "evidence-lane-plugin"
SOURCE_PACKAGE = SOURCE / "src" / "evidence_lane_plugin"
INSTALLED = Path(
    r"C:\Users\rathe\.codex\plugins\cache\evidence-lane-github\evidence-lane-plugin"
    r"\2.1.0+codex.20260812193232"
)
INSTALLED_PACKAGE = INSTALLED / "src" / "evidence_lane_plugin"
TEST_ROOT = WORKSPACE / "tests"


def parse_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def extract_mcp_tools(path: Path) -> tuple[dict[str, dict[str, object]], list[tuple[object, ...]]]:
    tree = parse_tree(path)
    direct: dict[str, dict[str, object]] = {}
    sdk_actions: list[tuple[object, ...]] = []
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "SDK_NATIVE_ACTIONS"
        ):
            sdk_actions = list(ast.literal_eval(node.value))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
            ):
                continue
            tool_name = node.name
            for keyword in decorator.keywords:
                if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                    tool_name = str(keyword.value.value)
            calls: list[str] = []
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    try:
                        calls.append(ast.unparse(child.func))
                    except Exception:
                        continue
                elif isinstance(child, ast.Attribute):
                    try:
                        calls.append(ast.unparse(child))
                    except Exception:
                        continue
            direct[tool_name] = {
                "line": node.lineno,
                "calls": sorted(set(calls)),
            }
    return direct, sdk_actions


def extract_sdk_contract(path: Path) -> tuple[list[dict[str, object]], set[tuple[str, str]]]:
    tree = parse_tree(path)
    operations: list[dict[str, object]] = []
    handlers: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        value = None
        target_name = None
        if isinstance(node, ast.Assign):
            value = node.value
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                target_name = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            value = node.value
            target_name = node.target.id
        if target_name == "SDK_MODULES" and isinstance(value, ast.Tuple):
            for element in value.elts:
                if not isinstance(element, ast.Call) or len(element.args) < 4:
                    continue
                module_id = ast.literal_eval(element.args[0])
                abi = ast.literal_eval(element.args[1])
                authority = ast.literal_eval(element.args[2])
                operation_call = element.args[3]
                if not isinstance(operation_call, ast.Call):
                    continue
                for operation_node in operation_call.args:
                    operation_effect = str(ast.literal_eval(operation_node))
                    operation, effect = operation_effect.split(":", 1)
                    operations.append(
                        {
                            "module": module_id,
                            "abi": abi,
                            "authority": authority,
                            "operation": operation,
                            "effect": effect,
                            "line": getattr(operation_node, "lineno", element.lineno),
                        }
                    )
        if target_name == "handlers" and isinstance(value, ast.Dict):
            for key in value.keys:
                if not isinstance(key, ast.Tuple) or len(key.elts) != 2:
                    continue
                try:
                    handlers.add((str(ast.literal_eval(key.elts[0])), str(ast.literal_eval(key.elts[1]))))
                except Exception:
                    continue
    return operations, handlers


def extract_service_methods(path: Path) -> dict[str, int]:
    tree = parse_tree(path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "EvidenceLaneService":
            return {
                child.name: child.lineno
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not child.name.startswith("_")
            }
    raise RuntimeError("EvidenceLaneService was not found")


def text_catalog(paths: Iterable[Path], root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(paths):
        result[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8", errors="replace")
    return result


def references(name: str, catalog: dict[str, str]) -> list[str]:
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")
    return [path for path, text in catalog.items() if pattern.search(text)]


def domain_for(name: str) -> str:
    prefixes = {
        "canon_": "canon",
        "learning_": "agent_learning",
        "source_": "source_intake",
        "lane_": "lanes",
        "pv_plan": "plan",
        "pv_task": "plan",
        "task_": "plan",
        "pv_state_travel": "state_travel",
        "pv_boot": "lifecycle",
        "session_": "runtime",
        "runtime_": "runtime",
        "lifecycle_": "lifecycle",
        "connector_": "connectors",
        "storage_": "storage",
        "mode_": "env_uop",
        "git_": "git",
        "remote_git_": "git",
        "hil_": "hil",
        "prompt_": "prompt_index",
        "render_": "panels",
        "project_": "project_registry",
    }
    for prefix, domain in prefixes.items():
        if name.startswith(prefix):
            return domain
    if name in {"search", "fetch", "pv_query", "pv_diff", "pv_summary", "pv_status"}:
        return "project_truth"
    return "general"


def service_calls(call_names: Iterable[str], known_methods: set[str]) -> list[str]:
    result = set()
    for call in call_names:
        tail = call.rsplit(".", 1)[-1]
        if tail in known_methods:
            result.add(tail)
    return sorted(result)


SDK_PUBLIC_BRIDGES: dict[tuple[str, str], str] = {
    ("project_truth", "status"): "pv_status",
    ("project_truth", "search"): "search",
    ("project_truth", "fetch"): "fetch",
    ("project_truth", "query"): "pv_query",
    ("project_truth", "diff"): "pv_diff",
    ("lifecycle_hooks", "doctor"): "runtime_doctor",
    ("lifecycle_hooks", "transition_law"): "lifecycle_transition_law",
    ("lifecycle_hooks", "runtime_status"): "runtime_activation_status",
    ("plan_delta_tasks", "status"): "pv_status",
    ("plan_delta_tasks", "backlog"): "pv_task_backlog",
    ("plan_delta_tasks", "classify_delta"): "task_classify",
    ("plan_delta_tasks", "transition_task"): "pv_task_transition",
    ("plan_delta_tasks", "project_host_plan"): "pv_plan_tasks",
    ("source_lane_retrieval", "search"): "lane_search",
    ("source_lane_retrieval", "fetch"): "lane_fetch",
    ("source_lane_retrieval", "query"): "pv_query",
    ("source_lane_retrieval", "source_intake"): "source_intake_classify",
    ("env_uop_operator_runtime", "status"): "session_flash_status",
    ("env_uop_operator_runtime", "classify_mode"): "mode_classify",
    ("storage_connectors", "inspect"): "storage_connector_inspect",
    ("storage_connectors", "select"): "storage_connector_select",
    ("storage_connectors", "plugin_catalog"): "connector_plugin_catalog",
    ("storage_connectors", "plugin_route"): "connector_plugin_route",
    ("hil_candidate_pointer", "status"): "pv_status",
    ("hil_candidate_pointer", "prepare_candidate"): "pv_refresh",
    ("hil_candidate_pointer", "render_hil"): "prompt_index_status",
    ("hil_candidate_pointer", "record_decision"): "pv_hil_decide",
    ("hil_candidate_pointer", "fuse"): "pv_fuse",
    ("hil_candidate_pointer", "rollback"): "pv_rollback",
    ("provider_host_adapters", "capabilities"): "runtime_doctor",
    ("provider_host_adapters", "binding_status"): "runtime_activation_status",
}


def main() -> None:
    now = utc_now()
    source_mcp_path = SOURCE_PACKAGE / "mcp_server.py"
    installed_mcp_path = INSTALLED_PACKAGE / "mcp_server.py"
    sdk_path = SOURCE_PACKAGE / "internal_sdk.py"
    service_path = SOURCE_PACKAGE / "service.py"

    source_direct, source_sdk_actions = extract_mcp_tools(source_mcp_path)
    installed_direct, installed_sdk_actions = extract_mcp_tools(installed_mcp_path)
    source_tools = set(source_direct) | {str(row[0]) for row in source_sdk_actions}
    installed_tools = set(installed_direct) | {str(row[0]) for row in installed_sdk_actions}
    sdk_action_by_tool = {
        str(row[0]): {
            "module": str(row[3]),
            "operation": str(row[4]),
            "read_only": bool(row[5]),
        }
        for row in source_sdk_actions
    }
    sdk_operations, sdk_handlers = extract_sdk_contract(sdk_path)
    service_methods = extract_service_methods(service_path)

    source_skills = text_catalog((SOURCE / "skills").glob("*/SKILL.md"), SOURCE)
    installed_skills = text_catalog((INSTALLED / "skills").glob("*/SKILL.md"), INSTALLED)
    source_commands = text_catalog((SOURCE / "commands").glob("*.md"), SOURCE)
    tests = text_catalog(TEST_ROOT.glob("test_*.py"), WORKSPACE)
    sdk_text = sdk_path.read_text(encoding="utf-8")

    mcp_rows: list[tuple[object, ...]] = []
    unnamed_tools: list[str] = []
    source_only_tools: list[str] = []
    service_reached: set[str] = set(re.findall(r"\bservice\.([A-Za-z_][A-Za-z0-9_]*)\s*\(", sdk_text))
    for tool_name in sorted(source_tools):
        sdk_action = sdk_action_by_tool.get(tool_name)
        if tool_name in source_direct:
            locator_line = int(source_direct[tool_name]["line"])
            calls = service_calls(source_direct[tool_name]["calls"], set(service_methods))
            service_reached.update(calls)
            core_symbol = ",".join(f"EvidenceLaneService.{name}" for name in calls) or "mcp_server.inline_route"
            core_status = "IMPLEMENTED_ROUTE_BODY"
            sdk_module = None
            sdk_operation = None
            sdk_status = "NO_EXPLICIT_SDK_ROUTE"
        else:
            sdk_module = str(sdk_action["module"])
            sdk_operation = str(sdk_action["operation"])
            handler_present = (sdk_module, sdk_operation) in sdk_handlers
            core_symbol = f"internal_sdk:{sdk_module}.{sdk_operation}"
            core_status = "IMPLEMENTED_SDK_HANDLER" if handler_present else "SDK_HANDLER_ABSENT"
            sdk_status = "REGISTERED" if handler_present else "DECLARED_HANDLER_MISSING"
            locator_line = next(
                int(item["line"])
                for item in sdk_operations
                if item["module"] == sdk_module and item["operation"] == sdk_operation
            )

        skill_refs = references(tool_name, source_skills)
        installed_skill_refs = references(tool_name, installed_skills)
        command_refs = references(tool_name, source_commands)
        test_refs = references(tool_name, tests)
        if not skill_refs:
            unnamed_tools.append(tool_name)
        installed = tool_name in installed_tools
        if not installed:
            source_only_tools.append(tool_name)

        if not installed and sdk_action and (sdk_module, sdk_operation) in sdk_handlers:
            gap_class = "IMPLEMENTED_CORE_BUT_UNROUTED_INSTALLED"
            decision = "INSTALL_AFTER_FULL_PARITY_AND_GOVERNED_RELEASE"
        elif not skill_refs:
            gap_class = "PUBLIC_TOOL_NOT_BOUND_TO_SKILL"
            decision = "ADD_EXPLICIT_SKILL_ROUTE_OR_DOCUMENT_INTENTIONAL_LOW_LEVEL_ONLY"
        else:
            gap_class = "NO_GAP_AT_THIS_LAYER"
            decision = "RETAIN_AND_VERIFY_DEEP_ROUTE"

        notes = {
            "source_skill_refs": skill_refs,
            "installed_skill_refs": installed_skill_refs,
            "source_command_refs": command_refs,
            "direct_service_calls": calls if tool_name in source_direct else [],
        }
        mcp_rows.append(
            (
                f"MCP-{tool_name}",
                domain_for(tool_name),
                tool_name,
                core_symbol,
                core_status,
                f"plugins/evidence-lane-plugin/src/evidence_lane_plugin/mcp_server.py:{locator_line}",
                tool_name,
                "SOURCE_PUBLIC",
                sdk_module,
                sdk_operation,
                sdk_status,
                ";".join(skill_refs) or None,
                "EXPLICITLY_NAMED" if skill_refs else "NOT_NAMED",
                ";".join(command_refs) or None,
                "EXPLICITLY_NAMED" if command_refs else "NOT_NAMED",
                "INSTALLED_PUBLIC" if installed else "SOURCE_ONLY_NOT_INSTALLED",
                "LIVE_PUBLIC" if installed else "NOT_LIVE_ON_INSTALLED_2_1",
                "REFERENCED_BY_TEST" if test_refs else "NO_EXACT_TEST_NAME_REFERENCE",
                ";".join(test_refs[:12]),
                gap_class,
                decision,
                f"DELTA-ROUTE-{domain_for(tool_name).upper()}",
                json.dumps(notes, sort_keys=True, separators=(",", ":")),
                now,
            )
        )

    sdk_rows: list[tuple[object, ...]] = []
    missing_handlers: list[str] = []
    for operation in sdk_operations:
        module = str(operation["module"])
        name = str(operation["operation"])
        key = (module, name)
        registered = key in sdk_handlers
        if not registered:
            missing_handlers.append(f"{module}.{name}")
        bridge = next(
            (tool for tool, action in sdk_action_by_tool.items() if (action["module"], action["operation"]) == key),
            SDK_PUBLIC_BRIDGES.get(key),
        )
        bridge_source = bridge in source_tools if bridge else False
        bridge_installed = bridge in installed_tools if bridge else False
        skill_refs = references(bridge, source_skills) if bridge else []
        test_refs = references(name, tests) + references(module, tests)
        test_refs = sorted(set(test_refs))

        if not registered:
            gap_class = "DECLARED_SDK_OPERATION_HANDLER_MISSING"
            decision = "IMPLEMENT_HANDLER_AND_CONTRACT_TEST"
        elif bridge and not bridge_installed:
            gap_class = "SDK_HANDLER_PUBLIC_SOURCE_ONLY"
            decision = "VERIFY_PUBLIC_ROUTE_THEN_INSTALL_AT_GOVERNED_BOUNDARY"
        elif not bridge:
            gap_class = "SDK_OPERATION_WITHOUT_PUBLIC_MCP_BRIDGE"
            decision = "DECIDE_INTERNAL_ONLY_OR_ADD_SUPPORTED_BRIDGE"
        else:
            gap_class = "NO_GAP_AT_THIS_LAYER"
            decision = "RETAIN_AND_VERIFY_EFFECT_CONTRACT"

        sdk_rows.append(
            (
                f"SDK-{module}-{name}",
                module,
                f"{module}.{name}",
                f"internal_sdk:{module}.{name}",
                "HANDLER_REGISTERED" if registered else "HANDLER_ABSENT",
                f"plugins/evidence-lane-plugin/src/evidence_lane_plugin/internal_sdk.py:{operation['line']}",
                bridge,
                "SOURCE_PUBLIC" if bridge_source else "NO_SOURCE_MCP_BRIDGE",
                module,
                name,
                "REGISTERED" if registered else "DECLARED_HANDLER_MISSING",
                ";".join(skill_refs) or None,
                "BRIDGE_NAMED" if skill_refs else "NOT_NAMED_OR_NO_BRIDGE",
                None,
                "NOT_DIRECT_COMMAND",
                "INSTALLED_PUBLIC_BRIDGE" if bridge_installed else "NOT_INSTALLED_OR_NO_BRIDGE",
                "LIVE_PUBLIC_BRIDGE" if bridge_installed else "NOT_LIVE_AS_SDK_OPERATION",
                "REFERENCED_BY_TEST" if test_refs else "NO_EXACT_REFERENCE",
                ";".join(test_refs[:12]),
                gap_class,
                decision,
                f"DELTA-SDK-{module.upper().replace('_', '-')}",
                json.dumps(
                    {
                        "abi": operation["abi"],
                        "authority": operation["authority"],
                        "effect": operation["effect"],
                        "bridge": bridge,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                now,
            )
        )

    service_rows: list[tuple[object, ...]] = []
    service_unreached: list[str] = []
    for method, line in sorted(service_methods.items()):
        reached = method in service_reached
        method_test_refs = references(method, tests)
        if not reached:
            service_unreached.append(method)
        service_rows.append(
            (
                f"SERVICE-{method}",
                domain_for(method),
                f"EvidenceLaneService.{method}",
                f"EvidenceLaneService.{method}",
                "IMPLEMENTED",
                f"plugins/evidence-lane-plugin/src/evidence_lane_plugin/service.py:{line}",
                None,
                "REACHED_BY_MCP_OR_SDK" if reached else "NO_DIRECT_MCP_OR_SDK_CALL_FOUND",
                None,
                None,
                "N/A",
                None,
                "N/A",
                None,
                "N/A",
                "SOURCE_IMPLEMENTATION",
                "REACHABLE" if reached else "ELIGIBILITY_REVIEW_REQUIRED",
                "REFERENCED_BY_TEST" if method_test_refs else "NO_EXACT_REFERENCE",
                ";".join(method_test_refs[:12]),
                "NO_GAP_AT_THIS_LAYER" if reached else "IMPLEMENTED_SERVICE_METHOD_ROUTE_REVIEW",
                "RETAIN" if reached else "CLASSIFY_INTERNAL_ONLY_OR_ADD_EXPLICIT_ROUTE",
                f"DELTA-SERVICE-{domain_for(method).upper()}",
                "Public method inventory; absence of a direct call is a review signal, not automatic proof of a missing product route.",
                now,
            )
        )

    yaml_paths = sorted((SOURCE / "skills").glob("*/agents/openai.yaml"))
    dependency_declared = []
    for path in yaml_paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?m)^\s*(dependencies|mcp_servers|mcp)\s*:", text):
            dependency_declared.append(path.relative_to(SOURCE).as_posix())

    with connect() as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO capability_route VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            mcp_rows + sdk_rows + service_rows,
        )
        finding_rows = [
            (
                "GAP-SKILL-UNNAMED-PUBLIC-TOOLS",
                "skill_routing",
                "HIGH",
                "PUBLIC_TOOL_NOT_BOUND_TO_SKILL",
                f"{len(unnamed_tools)} source MCP actions are not named by any source SKILL.md: {', '.join(unnamed_tools)}.",
                "capability_route:MCP-* skill_status=NOT_NAMED",
                "Models must discover low-level tool names without deterministic workflow instructions.",
                "Bind each eligible operation to an owning skill or document that it is intentionally low-level and how its owning workflow reaches it.",
                "DELTA-SKILL-MCP-ROUTING",
                "OPEN",
                "HIGH",
                now,
            ),
            (
                "GAP-SKILL-MCP-DEPENDENCY-METADATA",
                "skill_routing",
                "MEDIUM",
                "OFFICIAL_PLUGIN_CONTRACT_GAP",
                f"The source contains {len(yaml_paths)} skill openai.yaml files and none declares an MCP dependency; the remaining skills have no openai.yaml metadata.",
                "AUTH-OAI-BUILD-SKILLS;plugins/evidence-lane-plugin/skills/*/agents/openai.yaml",
                "A skill that depends on Evidence Lane MCP is not explicitly gated by that server in skill metadata.",
                "Add minimal official metadata/dependency declarations only for skills that require the package MCP; keep read-only instructions progressively disclosed.",
                "DELTA-SKILL-METADATA",
                "OPEN",
                "MEDIUM",
                now,
            ),
            (
                "GAP-MCP-INSTALLED-MISSING-SOURCE-ACTIONS",
                "mcp_surface",
                "HIGH",
                "IMPLEMENTED_CORE_BUT_UNROUTED_INSTALLED",
                f"{len(source_only_tools)} source actions are absent from installed/live 2.1: {', '.join(source_only_tools)}.",
                "capability_route:MCP-* installed_status=SOURCE_ONLY_NOT_INSTALLED",
                "Canon and Agent Learning cannot be invoked through the current installed catalog.",
                "Complete route/test parity, then promote exact tested bytes through the governed package/install boundary.",
                "DELTA-MCP-INSTALLED-PARITY",
                "OPEN",
                "HIGH",
                now,
            ),
            (
                "GAP-SDK-DECLARED-HANDLERS-MISSING",
                "sdk",
                "HIGH",
                "DECLARED_SDK_OPERATION_HANDLER_MISSING",
                f"{len(missing_handlers)} of {len(sdk_operations)} declared SDK operations have no registered local handler: {', '.join(missing_handlers)}.",
                "capability_route:SDK-* sdk_status=DECLARED_HANDLER_MISSING",
                "The ABI advertises operations that fail with HOST_CAPABILITY_UNAVAILABLE in the default local adapter even when equivalent service/MCP behavior may exist.",
                "For each operation, either register an effect-correct handler, narrow the ABI, or explicitly bind an external provider adapter; test the production construction path.",
                "DELTA-SDK-HANDLER-PARITY",
                "OPEN",
                "HIGH",
                now,
            ),
            (
                "GAP-SERVICE-ROUTE-ELIGIBILITY-REVIEW",
                "service",
                "MEDIUM",
                "IMPLEMENTED_CORE_BUT_UNROUTED_REVIEW",
                f"{len(service_unreached)} public EvidenceLaneService methods have no direct MCP/SDK call detected: {', '.join(service_unreached)}.",
                "capability_route:SERVICE-* runtime_status=ELIGIBILITY_REVIEW_REQUIRED",
                "Some may be intentional internal orchestration; treating all as product gaps would overstate the evidence.",
                "Classify each method against declared public workflows and only create implementation Deltas for eligible operations.",
                "DELTA-SERVICE-ROUTE-REVIEW",
                "OPEN",
                "MEDIUM",
                now,
            ),
        ]
        connection.executemany(
            "INSERT OR REPLACE INTO gap_finding VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            finding_rows,
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP06-SKILL-COMMAND-ROUTES",
            doc_type="research_finding",
            title="Skill, command, and root-routing parity",
            body=(
                f"source skills={len(source_skills)}; installed skills={len(installed_skills)}; "
                f"source commands={len(source_commands)}; public actions unnamed by skills={len(unnamed_tools)}; "
                f"skill openai.yaml files={len(yaml_paths)}; declared MCP dependencies={len(dependency_declared)}. "
                f"Unnamed tools: {', '.join(unnamed_tools)}"
            ),
            evidence_locator="capability_route:MCP-*;gap_finding:GAP-SKILL-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP07-MCP-SURFACE",
            doc_type="research_finding",
            title="Source and installed MCP action surface",
            body=(
                f"source actions={len(source_tools)}; direct={len(source_direct)}; sdk-native={len(source_sdk_actions)}; "
                f"installed actions={len(installed_tools)}; source-only={len(source_only_tools)}. "
                f"Source-only actions: {', '.join(source_only_tools)}"
            ),
            evidence_locator="capability_route:MCP-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP08-CORE-SERVICE",
            doc_type="research_finding",
            title="MCP to service/core reachability",
            body=(
                f"public service methods={len(service_methods)}; directly reached from MCP/SDK source={len(service_methods)-len(service_unreached)}; "
                f"route eligibility review={len(service_unreached)}: {', '.join(service_unreached)}"
            ),
            evidence_locator="capability_route:SERVICE-*",
        )
        upsert_fts(
            connection,
            doc_id="FTS-STEP09-SDK-ABI",
            doc_type="research_finding",
            title="Internal SDK ABI and handler parity",
            body=(
                f"declared operations={len(sdk_operations)}; registered handlers={len(sdk_handlers)}; missing handlers={len(missing_handlers)}. "
                f"Missing: {', '.join(missing_handlers)}"
            ),
            evidence_locator="capability_route:SDK-*;gap_finding:GAP-SDK-DECLARED-HANDLERS-MISSING",
        )

        for step, locator, event in [
            (6, "FTS-STEP06-SKILL-COMMAND-ROUTES", "EVT-STEP06-SKILL-COMMAND-COMPLETE"),
            (7, "FTS-STEP07-MCP-SURFACE", "EVT-STEP07-MCP-SURFACE-COMPLETE"),
            (8, "FTS-STEP08-CORE-SERVICE", "EVT-STEP08-CORE-SERVICE-COMPLETE"),
            (9, "FTS-STEP09-SDK-ABI", "EVT-STEP09-SDK-ABI-COMPLETE"),
        ]:
            transition_step(
                connection,
                step_no=step,
                to_status="completed",
                evidence_locator=locator,
                event_id=event,
                occurred_at=now,
            )
        transition_step(
            connection,
            step_no=10,
            to_status="in_progress",
            evidence_locator="GitHub App/CI route audit started",
            event_id="EVT-STEP10-GITHUB-CI-START",
            occurred_at=now,
        )
        append_audit_event(
            connection,
            event_id="AUDIT-STEPS06-09-ROUTE-MATRIX",
            event_type="RESEARCH_STEPS_COMPLETED",
            payload={
                "steps": [6, 7, 8, 9],
                "source_skills": len(source_skills),
                "installed_skills": len(installed_skills),
                "source_mcp_actions": len(source_tools),
                "installed_mcp_actions": len(installed_tools),
                "source_only_actions": source_only_tools,
                "public_actions_unnamed_by_skills": unnamed_tools,
                "sdk_declared_operations": len(sdk_operations),
                "sdk_registered_handlers": len(sdk_handlers),
                "sdk_missing_handlers": missing_handlers,
                "service_public_methods": len(service_methods),
                "service_route_review": service_unreached,
                "canonical_plan_mutated": False,
            },
            occurred_at=now,
        )
        check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"quick_check failed: {check}")

    print(
        json.dumps(
            {
                "status": "PASS",
                "completed_steps": [6, 7, 8, 9],
                "in_progress_step": 10,
                "source_skills": len(source_skills),
                "installed_skills": len(installed_skills),
                "source_mcp_actions": len(source_tools),
                "installed_mcp_actions": len(installed_tools),
                "unnamed_by_skills": len(unnamed_tools),
                "source_only_actions": len(source_only_tools),
                "sdk_declared": len(sdk_operations),
                "sdk_registered": len(sdk_handlers),
                "sdk_missing": len(missing_handlers),
                "service_public_methods": len(service_methods),
                "service_route_review": len(service_unreached),
                "quick_check": "ok",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
