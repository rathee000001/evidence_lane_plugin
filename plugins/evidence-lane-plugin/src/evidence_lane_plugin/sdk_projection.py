"""Deterministic complete SDK member projections from current v4 owners.

The SDK keeps executable Python entrypoints small, while publishing the
reviewable module, route, authority, lifecycle, host and workflow contracts
that those entrypoints consume.  Earlier v1 files are evidence only: every
projection below is rebuilt from current v4 source, registries and policies.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .current_route_registry import current_implementation_registry
from .registry import WORKFLOWS

Json = dict[str, Any]
Output = Json | str


def _encoded(value: Output) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )


def _digest(value: Output) -> str:
    return hashlib.sha256(_encoded(value)).hexdigest()


def _read(root: Path, relative: str) -> Json:
    value = json.loads((root / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"SDK projection source is not an object: {relative}")
    return value


def _ref(root: Path, relative: str, outputs: Mapping[str, Output]) -> Json:
    if relative in outputs:
        content = _encoded(outputs[relative])
    else:
        path = root / relative
        if not path.is_file():
            raise RuntimeError(f"SDK projection source is missing: {relative}")
        content = path.read_bytes()
    return {
        "path": relative,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _member_registry(
    schema: str,
    members: Sequence[str],
    outputs: Mapping[str, Output],
    root: Path,
    **extra: Any,
) -> Json:
    return {
        "schema": schema,
        "status": "PASS",
        **extra,
        "member_count": len(members),
        "members": [_ref(root, path, outputs) for path in sorted(members)],
    }


def _action_summary(action: Mapping[str, Any]) -> Json:
    return {
        key: action[key]
        for key in (
            "name",
            "description",
            "permission",
            "profile",
            "workflow",
            "project_required",
            "mutates",
            "queued",
            "requires_delta",
            "queryable_in_delta",
            "cross_project_read",
            "studio_read",
        )
    }


def _workflow_mermaid(title: str, skill: str, action_count: int) -> str:
    safe_title = title.replace('"', "'")
    safe_skill = skill.replace('"', "'")
    return "\n".join(
        [
            "flowchart LR",
            f'  user["User intent: {safe_title}"] --> skill["${safe_skill}"]',
            '  skill --> registry["Typed action and workflow registries"]',
            f'  registry --> sdk["SDK/MCP selection: {action_count} eligible actions"]',
            '  sdk --> engine["Persistent engine API"]',
            '  engine --> owner["Owning authority or sector lane"]',
            '  owner --> worker["Bounded tool worker when required"]',
            '  worker --> receipt["Validated result and receipt"]',
            '  receipt --> studio["Read-only Studio observation"]',
            "",
        ]
    )


def _workflow_dot(title: str, skill: str, action_count: int) -> str:
    def quoted(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    return "\n".join(
        [
            "digraph evidence_lane_workflow {",
            "  rankdir=LR;",
            f'  user [label="User intent: {quoted(title)}"];',
            f'  skill [label="${quoted(skill)}"];',
            '  registry [label="Typed registries"];',
            f'  sdk [label="SDK/MCP: {action_count} actions"];',
            '  engine [label="Persistent engine API"];',
            '  owner [label="Authority or sector owner"];',
            '  worker [label="Bounded worker"];',
            '  receipt [label="Validated receipt"];',
            '  studio [label="Read-only Studio"];',
            "  user -> skill -> registry -> sdk -> engine -> owner -> worker -> receipt -> studio;",
            "}",
            "",
        ]
    )


def _internal_outputs(
    root: Path,
    actions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    public_actions = []
    for action in actions:
        name = action["name"]
        public_actions.append(
            {
                **_action_summary(action),
                "schema": f"schemas/actions/{name}.v4.schema.json",
                "sdk_binding": f"sdk/actions/{name}.action.v4.json",
                "mcp_binding": f"mcp/actions/{name}.binding.v4.json",
                "automatic_retry": False,
            }
        )
    outputs["sdk/internal/public-action-registry.v4.json"] = {
        "schema": "evidence-lane.sdk-public-action-registry.v4",
        "status": "PASS",
        **envelope,
        "action_count": len(public_actions),
        "read_action_count": sum(not row["mutates"] for row in public_actions),
        "write_action_count": sum(bool(row["mutates"]) for row in public_actions),
        "actions": public_actions,
    }

    workflow_rows = []
    for workflow in WORKFLOWS:
        reference = skill_action_reference_from_actions(actions, workflow.name)
        workflow_rows.append(
            {
                "workflow": workflow.name,
                "skill": workflow.skill,
                "title": workflow.title,
                "description": workflow.description,
                "actions": [row["name"] for row in reference],
                "action_count": len(reference),
                "workflow_package": f"sdk/workflows/skills/{workflow.skill}/workflow.v4.json",
            }
        )
    outputs["sdk/internal/workflow-registry.v4.json"] = {
        "schema": "evidence-lane.runtime-workflow-sdk-registry.v4",
        "status": "PASS",
        **envelope,
        "workflow_count": len(workflow_rows),
        "workflows": workflow_rows,
    }

    outputs["sdk/internal/authority-surface-routing.v4.json"] = {
        "schema": "evidence-lane.sdk-authority-surface-routing.v4",
        "status": "PASS",
        **envelope,
        "authority_registry": _ref(
            root, "authorities/authority-surface-registry.v4.json", outputs
        ),
        "sector_registry": _ref(
            root,
            "authorities/project_sectors/sector-runtime-registry.v4.json",
            outputs,
        ),
        "env_policy": _ref(root, "env/codex-environment-policy.v4.json", outputs),
        "uop_policy": _ref(root, "uop/codex-operation-policy.v4.json", outputs),
        "public_action_registry": "sdk/internal/public-action-registry.v4.json",
        "workflow_registry": "sdk/internal/workflow-registry.v4.json",
        "outer_routing_registry": "sdk/routing/current-route-registry.v4.json",
        "sector_and_authority_databases_merged": False,
        "host_recall_merged_into_project_memory": False,
    }

    module_paths = []
    adapter_actions: dict[str, list[str]] = {}
    for action in actions:
        for route in action.get("toolchain", {}).get("routes", []):
            adapter = str(route.get("adapter", ""))
            match = re.match(r"evidence_lane_plugin\.([A-Za-z0-9_]+):", adapter)
            if match:
                adapter_actions.setdefault(match.group(1), []).append(action["name"])
    for source in sorted((root / "src/evidence_lane_plugin").glob("*.py")):
        stem = source.stem
        relative = source.relative_to(root).as_posix()
        target = f"sdk/internal/modules/{stem}.module.v4.json"
        outputs[target] = {
            "schema": "evidence-lane.sdk-internal-module-binding.v4",
            "status": "PASS",
            **envelope,
            "module": f"evidence_lane_plugin.{stem}",
            "source": _ref(root, relative, outputs),
            "role": "CANONICAL_PLUGIN_IMPLEMENTATION",
            "registered_action_handlers": sorted(set(adapter_actions.get(stem, []))),
            "public_action_registry": "sdk/internal/public-action-registry.v4.json",
            "workflow_registry": "sdk/internal/workflow-registry.v4.json",
            "execution_logic_duplicated_in_binding": False,
        }
        module_paths.append(target)
    outputs["sdk/internal/module-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-internal-module-registry.v4",
        module_paths,
        outputs,
        root,
        **envelope,
        source_module_count=len(module_paths),
        complete_current_source_package=True,
    )


def skill_action_reference_from_actions(
    actions: Sequence[Mapping[str, Any]], workflow_name: str
) -> list[Json]:
    """Return current action summaries for one workflow in registry order."""

    return [
        _action_summary(action) for action in actions if action["workflow"] == workflow_name
    ]


def _routing_outputs(
    root: Path,
    registry: Any,
    actions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    current = current_implementation_registry(registry)
    routes = {row["action"]: row for row in current["routes"]}
    members = []
    for action in actions:
        name = action["name"]
        target = f"sdk/routing/actions/{name}.route.v4.json"
        outputs[target] = {
            "schema": "evidence-lane.sdk-action-route.v4",
            "status": "PASS",
            **envelope,
            "action": name,
            "outer_route": routes[name],
            "toolchain_routes": action["toolchain"]["routes"],
            "contract_schema": f"schemas/actions/{name}.v4.schema.json",
            "sdk_binding": f"sdk/actions/{name}.action.v4.json",
            "mcp_binding": f"mcp/actions/{name}.binding.v4.json",
            "execution_owner": "connected_engine_registry",
            "automatic_retry": False,
        }
        members.append(target)
    outputs["sdk/routing/mcp-action-routing.v4.json"] = _member_registry(
        "evidence-lane.sdk-mcp-action-routing.v4",
        members,
        outputs,
        root,
        **envelope,
        route_count=len(members),
        canonical_registry="sdk/routing/current-route-registry.v4.json",
    )


def _authority_outputs(
    root: Path,
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    authority_registry = _read(root, "authorities/authority-surface-registry.v4.json")
    sector_registry = _read(
        root, "authorities/project_sectors/sector-runtime-registry.v4.json"
    )
    authority_members = []
    for row in authority_registry["authorities"]:
        target = f"sdk/authorities/named/{row['authority_id']}.authority.v4.json"
        outputs[target] = {
            "schema": "evidence-lane.sdk-authority-binding.v4",
            "status": "PASS",
            **envelope,
            **row,
            "manifest": _ref(root, row["manifest"]["path"], outputs),
            "project_state": "separate_lane_sqlite_and_files",
            "root_pv_role": "coordinate_published_lane_head",
            "runtime_execution_inferred": False,
        }
        authority_members.append(target)
    workflow_owner_members = []
    for row in authority_registry["workflow_owners"]:
        target = f"sdk/authorities/workflow-owners/{row['owner_id']}.owner.v4.json"
        outputs[target] = {
            "schema": "evidence-lane.sdk-workflow-authority-owner.v4",
            "status": "PASS",
            **envelope,
            **row,
            "manifest": _ref(root, row["manifest"]["path"], outputs),
            "separate_from_project_memory": True,
        }
        workflow_owner_members.append(target)
    sector_members = []
    for row in sector_registry["implemented_sectors"]:
        target = f"sdk/authorities/project-sectors/{row['lane_id']}.sector.v4.json"
        outputs[target] = {
            "schema": "evidence-lane.sdk-project-sector-binding.v4",
            "status": "PASS",
            **envelope,
            **row,
            "manifest": _ref(root, row["manifest"], outputs),
            "project_state": "separate_lane_sqlite_and_files",
            "runtime_execution_inferred": False,
        }
        sector_members.append(target)
    outputs["sdk/authorities/named-authorities.ref.v4.json"] = _member_registry(
        "evidence-lane.sdk-named-authorities.v4",
        authority_members,
        outputs,
        root,
        **envelope,
        authority_count=len(authority_members),
    )
    outputs["sdk/authorities/project-sectors.ref.v4.json"] = _member_registry(
        "evidence-lane.sdk-project-sectors.v4",
        sector_members,
        outputs,
        root,
        **envelope,
        sector_count=len(sector_members),
        other_sector_definitions=sector_registry["other_sector_definitions"],
    )
    outputs["sdk/authorities/workflow-owners.ref.v4.json"] = _member_registry(
        "evidence-lane.sdk-workflow-authority-owners.v4",
        workflow_owner_members,
        outputs,
        root,
        **envelope,
        owner_count=len(workflow_owner_members),
    )
    outputs["sdk/authorities/env-uop.ref.v4.json"] = {
        "schema": "evidence-lane.sdk-env-uop-authority-ref.v4",
        "status": "PASS",
        **envelope,
        "env": _ref(root, "env/codex-environment-policy.v4.json", outputs),
        "uop": _ref(root, "uop/codex-operation-policy.v4.json", outputs),
        "sdk": "sdk/env_uop/action-plane.v4.json",
        "formula_engine_retained": False,
    }
    outputs["sdk/authorities/root-pv.ref.v4.json"] = {
        "schema": "evidence-lane.sdk-root-pv-ref.v4",
        "status": "PASS",
        **envelope,
        "manifest": _ref(root, authority_registry["root_pv"], outputs),
        "role": "coordinate_exact_separate_lane_heads_and_receipts",
        "universal_business_database": False,
    }
    outputs["sdk/authorities/removed-authorities.v4.json"] = {
        "schema": "evidence-lane.sdk-removed-authorities.v4",
        "status": "PASS",
        **envelope,
        "removed": [
            "project_overlay",
            "connector_brain",
            "accepted_folder",
            "pv_learning_hil",
        ],
        "restored_by_sdk_projection": False,
    }


def _delta_outputs(
    root: Path,
    actions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    action_names = {row["name"] for row in actions}
    definitions = {
        "entry": {
            "actions": ["delta_enter", "delta_enter_planned"],
            "owners": [
                "src/evidence_lane_plugin/adaptive_delta_entry.py",
                "src/evidence_lane_plugin/coordination.py",
            ],
            "purpose": "Admit one bounded task under the current Plan contract and writer lease.",
        },
        "mid-query": {
            "actions": ["delta_query", "delta_status"],
            "owners": [
                "src/evidence_lane_plugin/adaptive_delta_entry.py",
                "src/evidence_lane_plugin/coordination.py",
            ],
            "purpose": "Read current authorities without changing the Plan or execution owner.",
        },
        "exit": {
            "actions": [],
            "owners": [
                "src/evidence_lane_plugin/adaptive_delta_exit.py",
                "src/evidence_lane_plugin/agent_learning.py",
            ],
            "purpose": "Verify effects, publish the result and update Learning after a valid exit.",
        },
        "prompt-steer": {
            "actions": ["steer_preview", "steer_submit", "plan_refresh"],
            "owners": [
                "src/evidence_lane_plugin/steering.py",
                "src/evidence_lane_plugin/plan_runtime.py",
            ],
            "purpose": "Checkpoint affected work and atomically replace the remaining Plan revision.",
        },
        "handoff-project-work": {
            "actions": [
                "continuation_offer",
                "continuation_accept",
                "continuation_cancel",
                "continuation_context",
                "continuation_read",
            ],
            "owners": [
                "src/evidence_lane_plugin/task_binding_registry.py",
                "src/evidence_lane_plugin/session_authority.py",
            ],
            "purpose": "Transfer exact task ownership between explicitly selected clients.",
        },
        "run-project-lifecycle": {
            "actions": [
                "session_boot",
                "session_resume",
                "session_status",
                "session_exit_boundary",
                "session_exit",
            ],
            "owners": [
                "src/evidence_lane_plugin/session.py",
                "src/evidence_lane_plugin/state_law.py",
            ],
            "purpose": "Coordinate project-session and Plan-task state transitions.",
        },
    }
    members = []
    for name, definition in definitions.items():
        if not set(definition["actions"]) <= action_names:
            raise RuntimeError(f"Delta SDK references missing actions: {name}")
        target = f"sdk/delta/{name}.workflow.v4.json"
        outputs[target] = {
            "schema": "evidence-lane.sdk-delta-workflow.v4",
            "status": "PASS",
            **envelope,
            "workflow": name,
            "purpose": definition["purpose"],
            "actions": definition["actions"],
            "owners": [_ref(root, path, outputs) for path in definition["owners"]],
            "plan_owned": True,
            "automatic_mutation_retry": False,
            "removed_hil_overlay_or_fuse": name not in {"hil-overlay", "fuse"},
        }
        members.append(target)
    outputs["sdk/delta/delta-workflow-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-delta-workflow-registry.v4",
        members,
        outputs,
        root,
        **envelope,
        workflow_count=len(members),
        removed_workflows=["hil-overlay", "fuse", "accepted-pv-rollback"],
    )


def _env_uop_outputs(
    root: Path,
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    definitions = {
        "action-plane.v4.json": {
            "purpose": "Bind current typed actions to ENV classification and UOP gates.",
            "sources": [
                "src/evidence_lane_plugin/codex_action_plane.py",
                "src/evidence_lane_plugin/env_uop_tool_routing.py",
            ],
        },
        "cross-plane-contract.v4.json": {
            "purpose": "Keep host, project, action, tool, lane and disclosure decisions explicit across ENV and UOP.",
            "sources": [
                "src/evidence_lane_plugin/codex_env_uop_policy.py",
                "src/evidence_lane_plugin/mode_governance.py",
            ],
        },
        "env-authority.v4.json": {
            "purpose": "Index current Codex host, client, project, skill, action, lane, tool, Plan, hook and Studio facts.",
            "sources": [
                "env/codex-environment-policy.v4.json",
                "env/SESSION_FLASH_MANIFEST.json",
                "src/evidence_lane_plugin/env_uop_graph.py",
            ],
        },
        "uop-authority.v4.json": {
            "purpose": "Apply permission, task, action, tool, fallback, verification, projection and disclosure gates.",
            "sources": [
                "uop/codex-operation-policy.v4.json",
                "uop/uop_sqlite.sqlite",
                "src/evidence_lane_plugin/env_uop_tool_routing.py",
            ],
        },
        "operations/classify-work.operation.v4.json": {
            "purpose": "Classify user intent and relevant source/workflow classes without authorizing execution.",
            "sources": [
                "src/evidence_lane_plugin/mode_governance.py",
                "src/evidence_lane_plugin/prompt_index.py",
            ],
        },
        "operations/route-operation.operation.v4.json": {
            "purpose": "Select the exact registered action route and host-compatible execution owner.",
            "sources": [
                "src/evidence_lane_plugin/codex_action_plane.py",
                "src/evidence_lane_plugin/current_route_registry.py",
            ],
        },
        "operations/select-toolchain.operation.v4.json": {
            "purpose": "Choose current primary/fallback tools from measured readiness and task scope.",
            "sources": [
                "src/evidence_lane_plugin/env_uop_tool_routing.py",
                "toolchains/operation-toolchains.v4.json",
            ],
        },
        "operations/disclose-and-verify.operation.v4.json": {
            "purpose": "Record checked gates, selected route, evidence basis, fallback and result verification.",
            "sources": [
                "src/evidence_lane_plugin/codex_env_uop_policy.py",
                "src/evidence_lane_plugin/tool_routes.py",
            ],
        },
    }
    members = []
    for relative, definition in definitions.items():
        target = "sdk/env_uop/" + relative
        outputs[target] = {
            "schema": "evidence-lane.sdk-env-uop-contract.v4",
            "status": "PASS",
            **envelope,
            "contract": Path(relative).stem,
            "purpose": definition["purpose"],
            "sources": [_ref(root, path, outputs) for path in definition["sources"]],
            "formula_engine": False,
            "legacy_env15_uop15_translation": False,
            "runtime_execution_inferred": False,
        }
        members.append(target)
    outputs["sdk/env_uop/env-uop-contract-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-env-uop-contract-registry.v4",
        members,
        outputs,
        root,
        **envelope,
        contract_count=len(members),
        direct_current_codex_contracts=True,
        formula_engine_removed=True,
    )


def _host_outputs(
    root: Path,
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    definitions = {
        "github-app-connection.contract.v4.json": (
            "Describe later GitHub App distribution without claiming a configured installation.",
            ["src/evidence_lane_plugin/github_app_distribution.py"],
        ),
        "goal-metrics.contract.v4.json": (
            "Read supported Goal usage evidence without mutating Goal state.",
            ["src/evidence_lane_plugin/goal_usage.py"],
        ),
        "hook-control.contract.v4.json": (
            "Bind supported native hook events to attributed capture without making hooks the lifecycle engine.",
            ["hooks/hooks.json", "src/evidence_lane_plugin/hook_contract.py"],
        ),
        "local-install.contract.v4.json": (
            "Install one exact side-by-side Windows release and publish its pointer only after validation.",
            [
                "authorities/session_authority/installation-layout.v4.json",
                "scripts/first_detection.py",
            ],
        ),
        "mcp-startup-isolation.contract.v4.json": (
            "Keep unrelated Codex task startup independent from Evidence Lane availability and bound initialization latency.",
            [".mcp.json", "scripts/run_mcp.py", "src/evidence_lane_plugin/launcher.py"],
        ),
        "plan-relock.contract.v4.json": (
            "Commit the project Plan database first, atomically project every row to the exact bound PLAN.md, then update the native list.",
            ["src/evidence_lane_plugin/plan_runtime.py"],
        ),
        "public-backend-readiness.v4.json": (
            "Report measured backend readiness without treating configuration as activation.",
            ["manifests/engine-connection.v4.json", "src/evidence_lane_plugin/runtime_health.py"],
        ),
        "runtime-install-restart.contract.v4.json": (
            "Separate plugin installation, engine activation, Studio visibility and later Codex restart proof.",
            [
                "scripts/run_mcp.py",
                "src/evidence_lane_plugin/launcher.py",
                "src/evidence_lane_plugin/startup.py",
            ],
        ),
        "work-handoff.contract.v4.json": (
            "Bind task continuation to explicit clients, projects, sessions and exact ownership receipts.",
            [
                "src/evidence_lane_plugin/task_binding_registry.py",
                "src/evidence_lane_plugin/session_authority.py",
            ],
        ),
        "studio-window.contract.v4.json": (
            "Keep Studio visible and read-only while the engine remains independent of window close/minimize.",
            ["src/evidence_lane_plugin/studio_window.py", "src/evidence_lane_plugin/studio_gateway.py"],
        ),
        "supported-hosts.v4.json": (
            "Select Windows Studio/local engine or reduced Mac/Linux and explicit remote routes from measured facts.",
            ["src/evidence_lane_plugin/host_routing.py"],
        ),
        "client-context.contract.v4.json": (
            "Keep each client session and explicit project permissions distinct from engine and host identity.",
            ["src/evidence_lane_plugin/connections.py", "src/evidence_lane_plugin/mcp_adapter.py"],
        ),
    }
    members = []
    for filename, (purpose, sources) in definitions.items():
        target = "sdk/host/" + filename
        outputs[target] = {
            "schema": "evidence-lane.sdk-host-contract.v4",
            "status": "PASS",
            **envelope,
            "contract": filename.removesuffix(".json"),
            "purpose": purpose,
            "sources": [_ref(root, path, outputs) for path in sources],
            "configured_state_is_runtime_activation": False,
            "native_identity_inferred": False,
        }
        members.append(target)
    outputs["sdk/host/host-contract-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-host-contract-registry.v4",
        members,
        outputs,
        root,
        **envelope,
        contract_count=len(members),
    )


def _workflow_outputs(
    root: Path,
    actions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    package_members = []
    top_members = []
    for workflow in WORKFLOWS:
        selected = skill_action_reference_from_actions(actions, workflow.name)
        skill = workflow.skill
        base = f"sdk/workflows/skills/{skill}"
        document_path = f"{base}/workflow.v4.json"
        mmd_path = f"{base}/workflow.mmd"
        dot_path = f"{base}/workflow.dot"
        action_names = [row["name"] for row in selected]
        outputs[document_path] = {
            "schema": "evidence-lane.sdk-skill-workflow.v4",
            "status": "PASS",
            **envelope,
            "workflow": workflow.name,
            "skill": skill,
            "title": workflow.title,
            "description": workflow.description,
            "default_prompt": workflow.default_prompt,
            "action_count": len(action_names),
            "actions": selected,
            "action_bindings": [
                f"sdk/actions/{name}.action.v4.json" for name in action_names
            ],
            "stages": [
                "intent_and_skill_resolution",
                "typed_action_resolution",
                "sdk_and_mcp_transport",
                "persistent_engine_api",
                "project_and_lane_admission",
                "bounded_operation_execution",
                "coordinated_publication",
                "read_only_studio_observation",
            ],
            "mermaid": mmd_path,
            "dot": dot_path,
            "installation_verified": False,
        }
        outputs[mmd_path] = _workflow_mermaid(workflow.title, skill, len(action_names))
        outputs[dot_path] = _workflow_dot(workflow.title, skill, len(action_names))
        package_members.extend([document_path, mmd_path, dot_path])
        top_path = f"sdk/workflows/{workflow.name}.workflow.v4.json"
        outputs[top_path] = {
            "schema": "evidence-lane.sdk-surface-workflow-ref.v4",
            "status": "PASS",
            **envelope,
            "workflow": workflow.name,
            "skill": skill,
            "title": workflow.title,
            "package": _ref(root, document_path, outputs),
            "mermaid": _ref(root, mmd_path, outputs),
            "dot": _ref(root, dot_path, outputs),
            "source_skill": _ref(root, f"skills/{skill}/SKILL.md", outputs),
        }
        top_members.append(top_path)
    outputs["sdk/workflows/workflow-package-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-workflow-package-registry.v4",
        package_members,
        outputs,
        root,
        **envelope,
        workflow_count=len(WORKFLOWS),
        per_skill_json_mmd_dot=True,
        removed_workflows=["formula", "pv-build-hil", "pv-fuse-hil", "pv-rollback"],
    )
    outputs["sdk/workflows/surface-workflow-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-surface-workflow-registry.v4",
        top_members,
        outputs,
        root,
        **envelope,
        workflow_count=len(top_members),
    )


def _support_family_outputs(
    root: Path,
    actions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    outputs: dict[str, Output],
) -> None:
    outputs["sdk/hooks/hook-runtime.ref.v4.json"] = {
        "schema": "evidence-lane.sdk-hook-runtime-ref.v4",
        "status": "PASS",
        **envelope,
        "runtime": _ref(root, "sdk/hooks/hook-runtime.v4.json", outputs),
        "events": [
            _ref(root, path.relative_to(root).as_posix(), outputs)
            for path in sorted((root / "sdk/hooks/events").glob("*.json"))
        ],
        "hooks_are_lifecycle_engine": False,
    }
    plan_definitions = {
        "plan-contracts.v4.json": [
            "src/evidence_lane_plugin/plan_runtime.py",
            "authorities/plan/manifest.v4.json",
        ],
        "semantic-steer.v4.json": [
            "src/evidence_lane_plugin/steering.py",
            "schemas/actions/plan_refresh.v4.schema.json",
        ],
        "host-projection.v4.json": [
            "src/evidence_lane_plugin/plan_runtime.py",
            "schemas/actions/plan_host_sync.v4.schema.json",
        ],
    }
    plan_members = []
    for name, sources in plan_definitions.items():
        target = "sdk/plan/" + name
        outputs[target] = {
            "schema": "evidence-lane.sdk-plan-contract.v4",
            "status": "PASS",
            **envelope,
            "contract": name.removesuffix(".json"),
            "sources": [_ref(root, source, outputs) for source in sources],
            "database_first": True,
            "full_host_projection_only": True,
            "public_set_status_action": False,
        }
        plan_members.append(target)
    outputs["sdk/plan/plan-contract-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-plan-contract-registry.v4",
        plan_members,
        outputs,
        root,
        **envelope,
    )
    recovery_definitions = {
        "coherent-backup.contract.v4.json": [
            "src/evidence_lane_plugin/recovery_snapshot.py",
            "src/evidence_lane_plugin/database_recovery.py",
        ],
        "database-recovery.contract.v4.json": [
            "src/evidence_lane_plugin/database_recovery.py"
        ],
        "job-recovery.contract.v4.json": [
            "src/evidence_lane_plugin/job_recovery.py"
        ],
        "git-restore.contract.v4.json": [
            "src/evidence_lane_plugin/git_job_recovery.py",
            "src/evidence_lane_plugin/remote_git.py",
        ],
    }
    recovery_members = []
    for name, sources in recovery_definitions.items():
        target = "sdk/recovery/" + name
        outputs[target] = {
            "schema": "evidence-lane.sdk-recovery-contract.v4",
            "status": "PASS",
            **envelope,
            "contract": name.removesuffix(".json"),
            "sources": [_ref(root, source, outputs) for source in sources],
            "accepted_pv_rollback": False,
            "restore_to_fresh_or_explicit_target": True,
        }
        recovery_members.append(target)
    outputs["sdk/recovery/recovery-contract-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-recovery-contract-registry.v4",
        recovery_members,
        outputs,
        root,
        **envelope,
    )
    skill_rows = []
    for workflow in WORKFLOWS:
        action_names = [
            row["name"]
            for row in skill_action_reference_from_actions(actions, workflow.name)
        ]
        skill_rows.append(
            {
                "skill": workflow.skill,
                "workflow": workflow.name,
                "skill_source": _ref(root, f"skills/{workflow.skill}/SKILL.md", outputs),
                "action_count": len(action_names),
                "actions": action_names,
                "workflow_package": f"sdk/workflows/skills/{workflow.skill}/workflow.v4.json",
            }
        )
    outputs["sdk/skills/skill-bindings.v4.json"] = {
        "schema": "evidence-lane.sdk-skill-bindings.v4",
        "status": "PASS",
        **envelope,
        "skill_count": len(skill_rows),
        "skills": skill_rows,
        "fixed_count_ceiling": False,
        "maintainer_skills_exposed": False,
    }
    transport_definitions = {
        "local-api.transport.v4.json": [
            "src/evidence_lane_plugin/local_transport.py",
            "schemas/transports/local-connect.v4.schema.json",
        ],
        "mcp-stdio.transport.v4.json": [
            "src/evidence_lane_plugin/mcp_adapter.py",
            "src/evidence_lane_plugin/mcp_server.py",
            ".mcp.json",
        ],
        "remote-api.transport.v4.json": [
            "src/evidence_lane_plugin/remote_api.py",
            "src/evidence_lane_plugin/remote_transport.py",
        ],
        "hook-capture.transport.v4.json": [
            "src/evidence_lane_plugin/capture_routing.py",
            "hooks/hooks.json",
        ],
    }
    transport_members = []
    for name, sources in transport_definitions.items():
        target = "sdk/transports/" + name
        outputs[target] = {
            "schema": "evidence-lane.sdk-transport-contract.v4",
            "status": "PASS",
            **envelope,
            "transport": name.removesuffix(".json"),
            "sources": [_ref(root, source, outputs) for source in sources],
            "credentials_recorded": False,
            "project_administration_inferred": False,
        }
        transport_members.append(target)
    outputs["sdk/transports/transport-registry.v4.json"] = _member_registry(
        "evidence-lane.sdk-transport-registry.v4",
        transport_members,
        outputs,
        root,
        **envelope,
    )


def complete_sdk_outputs(
    root: Path,
    registry: Any,
    actions: Sequence[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    existing_outputs: Mapping[str, Output],
) -> dict[str, Output]:
    """Build every non-actions SDK family from current executable owners."""

    outputs: dict[str, Output] = dict(existing_outputs)
    _internal_outputs(root, actions, envelope, outputs)
    _routing_outputs(root, registry, actions, envelope, outputs)
    _authority_outputs(root, envelope, outputs)
    _delta_outputs(root, actions, envelope, outputs)
    _env_uop_outputs(root, envelope, outputs)
    _host_outputs(root, envelope, outputs)
    _workflow_outputs(root, actions, envelope, outputs)
    _support_family_outputs(root, actions, envelope, outputs)
    return {path: value for path, value in outputs.items() if path not in existing_outputs}


__all__ = ["complete_sdk_outputs"]
