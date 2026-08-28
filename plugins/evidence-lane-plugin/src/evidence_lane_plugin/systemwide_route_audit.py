"""System-wide Plan-history and installed-route supersession audit."""

from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from .current_route_registry import current_implementation_registry
from .errors import EvidenceLaneError
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .internal_sdk import (
    SDK_MODULES,
    runtime_workflow_sdk_registry,
    whole_plugin_sdk_governance_registry,
)
from .mcp_server import SDK_NATIVE_ACTIONS, create_mcp_server
from .model_compatibility import (
    classify_model_compatibility,
    model_compatibility_catalog,
)
from .models import HostKind
from .persistence import route_persistence
from .public_surface_registry import derive_public_surface_registry
from .runtime_host_classifier import classify_runtime_host
from .service import EvidenceLaneService, inspect_service_route_parity
from .source_fingerprint import tracked_worktree_file_manifest

SYSTEMWIDE_ROUTE_AUDIT_SCHEMA = "evidence-lane.systemwide-route-audit.v1"

_PLAN_FAMILY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ENV_UOP": ("env", "uop", "operator", "flash"),
    "RUNTIME_ATTACHMENT": ("runtime", "attachment", "reattach", "host task"),
    "SESSION_LIFECYCLE": ("session", "boot", "turn", "entry", "exit"),
    "PROJECT_BOOTSTRAP": ("bootstrap", "initial build", "pv0", "pv00"),
    "SOURCE_INTAKE": ("source intake", "source-intake", "source boundary"),
    "LIVE_ROOT_QUERY": ("live root", "live-root", "query", "retrieval"),
    "POINTER": ("pointer", "rollback", "accepted pv"),
    "BOUNDED_OUTPUT": ("bounded", "model context", "receipt"),
    "CHATLINEAGE": ("chatlineage", "chat lineage", "prompt", "steer"),
    "SOURCE_GRAPH": ("source graph", "graph diff", "impact"),
    "LANES": ("lane", "sector", "engulf"),
    "RETRIEVAL": ("fts", "sqlite", "bm25", "query", "search", "fetch"),
    "SIX_AUTHORITY": ("six authority", "six-authority", "agents.md", "memory.md"),
    "PUBLIC_SURFACE": ("public", "surface", "schema", "registry", "action"),
    "MCP_SDK_SKILL": ("mcp", "sdk", "skill"),
    "PLAN": ("plan", "delta", "task", "step task"),
    "PROMPT_STEER": ("prompt", "steer", "discussion"),
    "DELTA_ENTRY": ("delta entry", "entry formula", "classify"),
    "DELTA_EXIT": ("delta exit", "adaptive exit", "exit formula", "refresh"),
    "LANE_REFRESH": ("lane refresh", "sector refresh", "changed section"),
    "SUB_PV": ("sub-pv", "sub pv", "subpv"),
    "HIL": ("hil", "human", "approve", "acceptance"),
    "PROJECT_OVERLAY": ("project overlay", "blast radius", "overlay"),
    "AI_LEARNING": ("learning", "lesson", "weave"),
    "MEMORY": ("memory", "dossier", "compact"),
    "FUSE": ("fuse", "promotion", "candidate acceptance"),
    "CANDIDATE": ("candidate", "proposal"),
    "LIVE_ROOT": ("live root", "live-root", "working overlay"),
    "ACCEPTED_STORAGE": ("accepted", "archive", "zip", "snapshot"),
    "STATE_TRAVEL": ("state travel", "state-travel", "handoff", "resume"),
    "GOAL": ("goal", "unfinished work", "continuation"),
    "STEP_TASK_LIST": ("step task", "task list", "fixed window", "projection"),
    "METRICS": ("metric", "token", "usage", "telemetry"),
    "HOOKS": ("hook", "pretool", "posttool", "sessionstart"),
    "HOST_LIFECYCLE": ("host", "restart", "renderer", "compaction"),
    "INSTALL": ("install", "marketplace", "slot"),
    "PACKAGE": ("package", "archive", "cache"),
    "LOCAL_SLOT": ("local slot", "local testing", "local-test"),
    "RESTART": ("restart", "reopen", "helper"),
    "REMOTE_ADAPTER": ("remote adapter", "remote-adapter", "website"),
    "CANON": ("canon", "backfire", "consequence"),
    "CROSS_TASK": ("cross-task", "linked task", "task edge"),
    "CONSEQUENCE_GRAPH": ("consequence", "backfire", "canon graph"),
    "CONNECTOR": ("connector", "plugin grant", "additional plugin"),
    "PLUGIN": ("plugin", "marketplace"),
    "STORAGE": ("storage", "drive", "durable", "sqlite"),
    "RUNTIME_CLASSIFICATION": ("runtime", "ephemeral", "persistent"),
    "MODE": ("mode", "intersection", "classification"),
    "TASK_LIFECYCLE": ("task", "delta", "transition"),
    "ROLLBACK": ("rollback",),
    "GIT": ("git", "commit", "branch"),
    "GITHUB_APP": ("github app", "github-app", "github"),
    "RELEASE": ("release", "ci", "publication", "vercel", "devpost"),
    "RENDERER": ("render", "panel", "ui", "page"),
    "PUBLIC_PROOF": ("proof", "benchmark", "forensic", "evaluation", "test"),
}

_TREE_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".vercel",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
}
_TREE_EXCLUDED_SUFFIXES = (".pyc", ".pyo", ".tmp", ".tsbuildinfo")
_SDK_AUDIT_SOURCE_SUFFIXES = {
    ".cjs",
    ".cs",
    ".dot",
    ".exe",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".mmd",
    ".ps1",
    ".psm1",
    ".py",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}

_SKILL_REQUIRED_CURRENT_MARKERS = {
    "evi": (
        "PLAN-stamped",
        "adaptive Delta exit",
        "compatibility execution is absent",
    ),
    "evi-build": ("PV0", "native Plan", "dual"),
    "evi-fuse": ("hil_intent_classify", "hil_decide", "pv_fuse", "exact dual"),
    "evi-additional-plugin": (
        "connector_plugin_catalog",
        "connector_plugin_register",
    ),
    "evi-boot": ("runtime_doctor", "session_flash_status", "session_resume"),
    "evi-canon": ("canon_graph", "Receiver-owned Canon HIL"),
    "evi-drop-additional-plugin": ("connector_plugin_drop", "DROP:<plugin-id>"),
    "evi-exit-boot": ("session_close", "active governed"),
    "evi-learning": ("learning_retrieve", "sub-PV", "Learning HIL"),
    "evi-instructions": ("AGENTS.md", "MEMORY.md", "separate", "pv_query"),
    "evi-memory": (
        "project_memory_query",
        "project_memory_record_link",
    ),
    "evi-mode": ("mode_classify", "Chat Lineage"),
    "evi-plugin": ("connector_plugin_catalog", "connector_plugin_settings"),
    "evi-refresh": (
        "REFRESH_WORKING_SECTORS",
        "adaptive_delta_exit",
        "task_complete_and_refresh",
        "compatibility is not part of the installed surface",
    ),
    "evi-rollback": ("logical rollback cursor", "sub-PV", "Hard filesystem restore"),
    "evi-source-intake": (
        "PRIMARY_PROJECT_CODE",
        "LANE_SCOPED_STUDY_BRAIN",
        "Initial Build",
        "Delta exit Refresh",
    ),
    "evi-state-travel": (
        "compatibility routes are absent",
        "render_runtime_panel",
        "build_rich_goal_completion_metrics_receipt",
    ),
    "evi-storage": (
        "storage_connector_inspect",
        "storage_connector_select",
        "append-only",
    ),
    "evi-universe": ("Project Universe", "connector brain", "search"),
    "evi-formula": ("formula_engine_run", "ENV/UOP", "bounded execution budget"),
    "evi-brain-scaling": ("brain_scaling_select", "token budget", "hash-addressed"),
    "evi-project-recipe": ("project_recipe_compile", "Source Intake", "lane set"),
    "evi-toolchain": ("ai_toolchain_route", "Codex", "eligible fallback"),
    "evi-bigger-universe": (
        "bigger_universe_register",
        "bigger_universe_link",
        "hash-only",
    ),
    "evidence-lane-code-lifecycle": (
        "Plan-stamped logical Rollback",
        "Hard restore is a separate",
        "adaptive_delta_exit",
    ),
}
_SKILL_FORBIDDEN_STALE_PHRASES = (
    "/evi-refresh creates an unaccepted candidate",
    "/evi-rollback moves only the accepted pointer",
    "pointer-only rollback",
    "state-travel-final-authority-render-once",
    "family retains eight compatibility action names",
)
_AGENT_FORBIDDEN_STALE_PHRASES = (
    "Build a candidate and stop at HIL",
    "Seal changed source as a candidate",
    "Move only the accepted PV pointer",
    "Compatibility sidecar for project-scoped storage",
)

_ENV_UOP_GOVERNED_SIX_WAY_ARMS = [
    "PROJECT_SECTORS_AND_ROOT_FILES",
    "AI_LEARNING",
    "CANON_GRAPH",
    "PROJECT_MEMORY_DB",
    "HOST_CONVERSATION_MEMORY_MD",
    "AGENTS_MD",
]
_LINKED_OPERATIONAL_AUTHORITIES = ["PROJECT_UNIVERSE", "CONNECTOR_BRAIN"]
_HIL_ONLY_AUTHORITIES = ["PROJECT_OVERLAY"]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"SYSTEMWIDE_ROUTE_JSON_OBJECT_REQUIRED:{path.name}")
    return value


def _systemwide_regression_receipt(path: str | Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "status": "NOT_SUPPLIED_UNIT_AUDIT",
            "required_before_local_package": True,
        }
    exact = Path(path).resolve()
    receipt = _json(exact)
    full = dict(receipt.get("full_regression") or {})
    closure = dict(receipt.get("targeted_closure") or {})
    skips = list(receipt.get("skips") or [])
    failure_rows = list(receipt.get("failures") or [])
    failure_test_count = sum(
        len(row.get("tests") or [])
        if isinstance(row, dict) and isinstance(row.get("tests"), list)
        else 1
        for row in failure_rows
    )
    valid = (
        receipt.get("schema") == "evidence-lane.systemwide-regression-receipt.v1"
        and receipt.get("status") == "PASS_WITH_TARGETED_FAILURE_CLOSURE"
        and full.get("authorized_run_count") == 1
        and full.get("full_rerun_count") == 0
        and int(full.get("passed") or 0) > 0
        and int(full.get("failed") or 0) == failure_test_count
        and closure.get("status") == "PASS"
        and closure.get("failed") == 0
        and closure.get("skipped") == 0
        and closure.get("full_suite_rerun") is False
        and len(skips) == int(full.get("skipped") or -1)
        and any(
            row.get("disposition") == "REQUIRED_POST_LOCAL_INSTALL_R265_TARGETED_PROOF"
            for row in skips
        )
        and (receipt.get("boundaries") or {}).get("accepted_archive_queried") is False
        and (receipt.get("boundaries") or {}).get("pointer_moved") is False
        and (receipt.get("boundaries") or {}).get("git_or_main_mutated") is False
    )
    if not valid:
        raise RuntimeError("SYSTEMWIDE_REGRESSION_RECEIPT_INVALID")
    return {
        "status": "PASS",
        "schema": receipt["schema"],
        "file_sha256": sha256_file(exact),
        "full_run_count": full["authorized_run_count"],
        "full_rerun_count": full["full_rerun_count"],
        "full_passed": full["passed"],
        "full_failed_then_targeted_closed": full["failed"],
        "full_skipped": full["skipped"],
        "targeted_closure_passed": closure["passed"],
        "post_install_targeted_proof_required": True,
    }


def _read_only_plan_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _plan_family_rows(text: str) -> list[str]:
    lowered = " ".join(str(text).casefold().split())
    return sorted(
        family
        for family, keywords in _PLAN_FAMILY_KEYWORDS.items()
        if any(keyword in lowered for keyword in keywords)
    )


def audit_plan_supersession(
    plan_runtime_sqlite: str | Path,
    *,
    active_row: int,
) -> dict[str, Any]:
    """Trace immutable Plan history and executable rows through active_row."""

    path = Path(plan_runtime_sqlite).resolve()
    connection = _read_only_plan_connection(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT projection_lane, row_number, history_number, lifecycle_status,
                   task_id, requested_outcome, plan_group, linked_delta_ids_json,
                   task_contract_sha256
              FROM plan_execution_row
             WHERE projection_lane = 'HISTORY'
                OR (projection_lane != 'HISTORY' AND row_number <= ?)
             ORDER BY CASE WHEN projection_lane = 'HISTORY' THEN 0 ELSE 1 END,
                      COALESCE(history_number, row_number), task_id
            """,
            (int(active_row),),
        ).fetchall()
        task_rows = connection.execute(
            """
            SELECT sequence, task_id, current_status, supersedes_task_id,
                   superseded_by_task_id, task_contract_sha256
              FROM delta_task
             ORDER BY sequence
            """
        ).fetchall()
        canonical_counts = connection.execute(
            """
            SELECT COUNT(*) AS canonical_count,
                   SUM(CASE WHEN projection_lane = 'HISTORY' THEN 1 ELSE 0 END)
                       AS history_count,
                   SUM(CASE WHEN projection_lane != 'HISTORY' THEN 1 ELSE 0 END)
                       AS executable_count,
                   MAX(CASE WHEN projection_lane != 'HISTORY' THEN row_number END)
                       AS physical_final_row
              FROM plan_execution_row
            """
        ).fetchone()
    finally:
        connection.close()

    by_task = {str(row["task_id"]): row for row in task_rows}
    cycles: list[list[str]] = []
    terminal_by_task: dict[str, str] = {}
    for task_id in by_task:
        chain: list[str] = []
        current = task_id
        while current:
            if current in chain:
                cycles.append(chain[chain.index(current) :] + [current])
                break
            chain.append(current)
            row = by_task.get(current)
            successor = str(row["superseded_by_task_id"] or "") if row else ""
            if not successor:
                terminal_by_task[task_id] = current
                break
            current = successor

    compact_rows: list[dict[str, Any]] = []
    unmatched: list[str] = []
    family_counts: Counter[str] = Counter()
    for row in rows:
        combined = " ".join(
            str(row[key] or "")
            for key in ("task_id", "requested_outcome", "plan_group")
        )
        families = _plan_family_rows(combined)
        if not families:
            unmatched.append(str(row["task_id"]))
        family_counts.update(families)
        task_id = str(row["task_id"])
        compact_rows.append(
            {
                "projection_lane": str(row["projection_lane"]),
                "row_number": row["row_number"],
                "history_number": row["history_number"],
                "status": str(row["lifecycle_status"]),
                "task_id": task_id,
                "task_contract_sha256": str(row["task_contract_sha256"]),
                "plan_families": families,
                "terminal_supersession_owner": terminal_by_task.get(task_id, task_id),
            }
        )

    status_counts = Counter(row["status"] for row in compact_rows)
    body = {
        "schema": "evidence-lane.plan-supersession-audit.v1",
        "status": "PASS" if not cycles and not unmatched else "BLOCKED",
        "plan_runtime_sqlite_sha256": sha256_file(path),
        "scope": {
            "historical_projection_included": True,
            "executable_row_start": min(
                int(row["row_number"])
                for row in compact_rows
                if row["projection_lane"] != "HISTORY"
            ),
            "executable_row_end": int(active_row),
            "physical_final_row": int(canonical_counts["physical_final_row"]),
            "queued_rows_after_active": int(canonical_counts["physical_final_row"])
            - int(active_row),
            "accepted_archive_queried": False,
        },
        "canonical_record_count": int(canonical_counts["canonical_count"]),
        "canonical_history_record_count": int(canonical_counts["history_count"]),
        "canonical_executable_row_count": int(canonical_counts["executable_count"]),
        "in_scope_record_count": len(compact_rows),
        "status_counts": dict(sorted(status_counts.items())),
        "supersession_link_count": sum(
            bool(row["supersedes_task_id"] or row["superseded_by_task_id"])
            for row in task_rows
        ),
        "supersession_cycle_count": len(cycles),
        "supersession_cycles": cycles,
        "plan_family_counts": dict(sorted(family_counts.items())),
        "unclassified_row_count": len(unmatched),
        "unclassified_task_ids": unmatched,
        "rows_sha256": sha256_bytes(canonical_json_bytes(compact_rows)),
        "raw_plan_text_returned": False,
        "full_rows_stored_in_receipt": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _current_plugin_tree_manifest(plugin_root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(plugin_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(plugin_root)
        if any(part in _TREE_EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix.casefold() in _TREE_EXCLUDED_SUFFIXES:
            continue
        entries.append(
            {
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    body = {
        "schema": "evidence-lane.current-plugin-tree-manifest.v1",
        "status": "PASS",
        "file_count": len(entries),
        "path_set_sha256": sha256_bytes(
            canonical_json_bytes([row["path"] for row in entries])
        ),
        "file_manifest_sha256": sha256_bytes(canonical_json_bytes(entries)),
        "entries": entries,
        "git_index_mutated": False,
        "git_ref_mutated": False,
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def _module_name_from_path(source_root: Path, path: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _internal_python_import_graph(plugin_root: Path) -> dict[str, Any]:
    source_root = plugin_root / "src" / "evidence_lane_plugin"
    module_paths = {
        _module_name_from_path(source_root, path): path
        for path in source_root.rglob("*.py")
    }
    graph: dict[str, set[str]] = {name: set() for name in module_paths}
    parse_errors: list[str] = []
    for module_name, path in module_paths.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            parse_errors.append(f"{module_name}:{type(exc).__name__}")
            continue
        package_parts = module_name.split(".")[:-1]
        for node in ast.walk(tree):
            target: str | None = None
            if isinstance(node, ast.ImportFrom) and node.level:
                keep = max(0, len(package_parts) - (node.level - 1))
                relative_prefix_parts = package_parts[:keep]
                suffix = node.module.split(".") if node.module else []
                target = ".".join([*relative_prefix_parts, *suffix])
                if not node.module:
                    graph[module_name].update(
                        ".".join([*relative_prefix_parts, alias.name])
                        for alias in node.names
                        if ".".join([*relative_prefix_parts, alias.name])
                        in module_paths
                    )
            elif isinstance(node, ast.ImportFrom) and node.module:
                package_prefix = "evidence_lane_plugin."
                if node.module.startswith(package_prefix):
                    target = node.module[len(package_prefix) :]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    package_prefix = "evidence_lane_plugin."
                    if alias.name.startswith(package_prefix):
                        graph[module_name].add(alias.name[len(package_prefix) :])
            if target:
                graph[module_name].update(
                    name
                    for name in module_paths
                    if name == target or name.startswith(target + ".")
                )
    runtime_roots = {
        "internal_sdk",
        "service",
        "engine",
        "mcp_server",
        "current_route_registry",
        "public_surface_registry",
        "systemwide_route_audit",
        "cli",
        "__main__",
        "",
    } & set(module_paths)
    script_adapter_roots: set[str] = set()
    for script in (plugin_root / "scripts").rglob("*.py"):
        try:
            tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            elif isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            for name in names:
                package_prefix = "evidence_lane_plugin."
                if name.startswith(package_prefix):
                    target = name[len(package_prefix) :]
                    script_adapter_roots.update(
                        module
                        for module in module_paths
                        if module == target or module.startswith(target + ".")
                    )
    declared_dynamic_adapter_roots = {
        "hook_skill_runtime",
    } & set(module_paths)
    roots = runtime_roots | script_adapter_roots | declared_dynamic_adapter_roots
    reachable = set(roots)
    pending = list(roots)
    while pending:
        current = pending.pop()
        for target in graph.get(current, set()):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    allowed_data_only_modules = {"version"}
    unreachable = sorted(set(module_paths) - reachable - allowed_data_only_modules)
    rows = [
        {
            "module": name,
            "path": path.relative_to(plugin_root).as_posix(),
            "sha256": sha256_file(path),
            "reachable_from_internal_sdk_runtime_roots": name in reachable,
            "direct_internal_import_count": len(graph.get(name, set())),
        }
        for name, path in sorted(module_paths.items())
    ]
    core = {
        "schema": "evidence-lane.internal-sdk-import-reachability.v1",
        "status": "PASS" if not parse_errors and not unreachable else "BLOCKED",
        "module_count": len(module_paths),
        "runtime_root_modules": sorted(runtime_roots),
        "script_adapter_root_modules": sorted(script_adapter_roots),
        "declared_dynamic_adapter_roots": sorted(declared_dynamic_adapter_roots),
        "reachable_module_count": len(reachable),
        "unreachable_modules": unreachable,
        "parse_errors": parse_errors,
        "modules": rows,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _sdk_file_class(relative: str) -> str | None:
    if relative.startswith("_evidence_lane_rehearsal/"):
        return "SDK_GOVERNED_EXCLUDED_REHEARSAL_EVIDENCE"
    if relative.startswith("tests/"):
        return "SDK_GOVERNED_TEST_CONTRACT"
    if relative.startswith("authorities/"):
        return "INTERNAL_SDK_AUTHORITY_PACKAGE"
    if relative.startswith("manifests/"):
        return "SDK_GOVERNED_PACKAGE_MANIFEST"
    if relative.startswith("src/evidence_lane_plugin/"):
        return (
            "INTERNAL_SDK_IMPLEMENTATION"
            if relative.endswith(".py")
            else "INTERNAL_SDK_DATA_CONTRACT"
        )
    if relative.startswith("hooks/"):
        return "SDK_GOVERNED_HOOK_ADAPTER"
    if relative.startswith("skills/"):
        return "SDK_GOVERNED_INTENT_ADAPTER"
    if relative.startswith("scripts/windows_tunnel/"):
        return "SDK_GOVERNED_THIN_TUNNEL_ADAPTER"
    if relative.startswith("tunnel/"):
        return "SDK_GOVERNED_THIN_TUNNEL_ADAPTER"
    if relative.startswith("scripts/codex_release/"):
        return "SDK_GOVERNED_HOST_TRANSACTION_ADAPTER"
    if relative.startswith("scripts/"):
        return "SDK_GOVERNED_BUILD_OR_PROJECTION_ADAPTER"
    if relative.startswith("remote_adapter/"):
        return "SDK_GOVERNED_REMOTE_PROJECTION_ADAPTER"
    if relative.startswith("schemas/"):
        return "INTERNAL_SDK_SCHEMA_CONTRACT"
    if relative.startswith(("env/", "uop/")):
        return "INTERNAL_SDK_DATA_CONTRACT"
    if relative.startswith("sdk/"):
        return "SDK_GOVERNED_PACKAGE_CONTRACT"
    if relative.startswith("mcp/"):
        return "SDK_GOVERNED_HOST_MANIFEST"
    if relative.startswith("toolchains/"):
        return "SDK_GOVERNED_PINNED_TOOLCHAIN"
    if relative.startswith(".codex-plugin/") or relative == ".mcp.json":
        return "SDK_GOVERNED_HOST_MANIFEST"
    if relative.startswith("assets/"):
        return "SDK_GOVERNED_PRESENTATION_ASSET"
    if relative.startswith("evidence/"):
        return "SDK_GOVERNED_DERIVED_EVIDENCE_ARTIFACT"
    if Path(relative).name in {
        "README.md",
        "LICENSE.md",
        "COPYRIGHT.md",
        "THIRD_PARTY_NOTICES.md",
        "pyproject.toml",
        "requirements.in",
        "requirements.lock.txt",
        "release-channels.json",
    }:
        return "SDK_GOVERNED_PACKAGE_CONTRACT"
    return None


def audit_whole_plugin_sdk_files(plugin_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unclassified: list[str] = []
    class_counts: Counter[str] = Counter()
    for path in sorted(plugin_root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative_path = path.relative_to(plugin_root)
        if any(part in _TREE_EXCLUDED_DIRS for part in relative_path.parts):
            continue
        if path.suffix.casefold() in _TREE_EXCLUDED_SUFFIXES:
            continue
        relative = relative_path.as_posix()
        if (
            path.suffix.casefold() not in _SDK_AUDIT_SOURCE_SUFFIXES
            and path.name not in {"Dockerfile", ".mcp.json"}
        ):
            continue
        classification = _sdk_file_class(relative)
        if classification is None:
            unclassified.append(relative)
            classification = "UNCLASSIFIED"
        class_counts[classification] += 1
        rows.append(
            {
                "path": relative,
                "classification": classification,
                "sha256": sha256_file(path),
                "behavior_owner": "INTERNAL_SDK",
                "outer_lifecycle_reasoning_allowed": False,
            }
        )
    import_graph = _internal_python_import_graph(plugin_root)
    core = {
        "schema": "evidence-lane.whole-plugin-sdk-file-audit.v1",
        "status": (
            "PASS"
            if not unclassified and import_graph["status"] == "PASS"
            else "BLOCKED"
        ),
        "law": "EVERY_PLUGIN_FILE_INTERNAL_SDK_OR_SDK_GOVERNED_ADAPTER",
        "file_count": len(rows),
        "classification_counts": dict(sorted(class_counts.items())),
        "unclassified_files": unclassified,
        "internal_python_reachability": import_graph,
        "files": rows,
        "accepted_archive_queried": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _audit_skill_current_routes(
    plugin_root: Path,
    routing: dict[str, Any],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    skill_names = {path.parent.name for path in skill_paths}
    workflow_names = set(dict(routing.get("workflows") or {}))
    unclassified_skills = sorted(skill_names - set(_SKILL_REQUIRED_CURRENT_MARKERS))
    missing_workflows = sorted(skill_names - workflow_names)
    orphan_workflows = sorted(workflow_names - skill_names)
    issues.extend(f"UNCLASSIFIED_SKILL:{name}" for name in unclassified_skills)
    issues.extend(f"SKILL_WORKFLOW_MISSING:{name}" for name in missing_workflows)
    issues.extend(f"ORPHAN_SKILL_WORKFLOW:{name}" for name in orphan_workflows)
    for path in skill_paths:
        name = path.parent.name
        text = path.read_text(encoding="utf-8")
        lowered = text.casefold()
        agent_path = path.parent / "agents" / "openai.yaml"
        agent_text = (
            agent_path.read_text(encoding="utf-8") if agent_path.is_file() else ""
        )
        stale_agent = [
            phrase
            for phrase in _AGENT_FORBIDDEN_STALE_PHRASES
            if phrase.casefold() in agent_text.casefold()
        ]
        missing = [
            marker
            for marker in _SKILL_REQUIRED_CURRENT_MARKERS.get(name, ())
            if marker.casefold() not in lowered
        ]
        stale = [
            phrase
            for phrase in _SKILL_FORBIDDEN_STALE_PHRASES
            if phrase.casefold() in lowered
        ]
        if missing:
            issues.extend(f"{name}:MISSING:{marker}" for marker in missing)
        if stale:
            issues.extend(f"{name}:STALE:{marker}" for marker in stale)
        if not agent_path.is_file():
            issues.append(f"{name}:AGENT_METADATA_MISSING")
        if stale_agent:
            issues.extend(f"{name}:STALE_AGENT:{marker}" for marker in stale_agent)
        rows.append(
            {
                "skill": name,
                "sha256": sha256_file(path),
                "required_marker_count": len(
                    _SKILL_REQUIRED_CURRENT_MARKERS.get(name, ())
                ),
                "missing_current_markers": missing,
                "stale_active_phrases": stale,
                "agent_metadata_sha256": (
                    sha256_file(agent_path) if agent_path.is_file() else None
                ),
                "stale_agent_phrases": stale_agent,
            }
        )
    routing_owners = {
        str(owner) for owner in dict(routing.get("tool_owners") or {}).values()
    }
    unknown_owners = sorted(routing_owners - skill_names)
    issues.extend(f"UNKNOWN_TOOL_OWNER:{name}" for name in unknown_owners)
    core = {
        "schema": "evidence-lane.skill-current-route-audit.v1",
        "status": "PASS" if not issues else "BLOCKED",
        "skill_count": len(skill_paths),
        "skill_count_semantics": "DERIVED_CURRENT_INVENTORY_NO_NUMERIC_CEILING",
        "skills": rows,
        "workflow_count": len(workflow_names),
        "unclassified_skills": unclassified_skills,
        "missing_skill_workflows": missing_workflows,
        "orphan_skill_workflows": orphan_workflows,
        "tool_owner_skill_count": len(routing_owners),
        "unknown_tool_owner_skills": unknown_owners,
        "issue_count": len(issues),
        "issues": issues,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _audit_host_storage_tunnel_matrix(plugin_root: Path) -> dict[str, Any]:
    release = _json(plugin_root / "scripts" / "codex-release-channel.json")
    declared = dict(release.get("host_storage_tunnel_matrix") or {})

    def sample(
        host: HostKind,
        *,
        ephemeral: bool,
        durable: bool,
        interaction_profile: str,
        native_mcp: bool,
    ) -> dict[str, Any]:
        route = route_persistence(
            host,
            ephemeral=ephemeral,
            server_has_durable_filesystem=durable,
            runtime_context={
                "interaction_profile": interaction_profile,
                "native_capabilities": {"native_mcp": native_mcp},
            },
            host_session_id="host-matrix-forensic-audit",
        )
        return {
            "mode": route.mode,
            "primary_runtime_authority": route.primary_runtime_authority,
            "host_profile": route.host_profile,
            "tunnel_requirement": route.tunnel_requirement,
            "tunnel_setup_frequency": route.tunnel_setup_frequency,
            "tunnel_key_retention": route.tunnel_key_retention,
            "tunnel_runtime_lifetime": route.tunnel_runtime_lifetime,
            "host_tool_transport": route.host_tool_transport,
            "native_mcp_available": route.native_mcp_available,
            "routing_axes_independent": route.routing_axes_independent,
            "account_tier_affects_routing": route.account_tier_affects_routing,
            "api_billing_affects_routing": route.api_billing_affects_routing,
            "chatgpt_scope": route.runtime_classifier["chatgpt_chat_work_scope"],
        }

    scenarios = {
        "codex_desktop_native": sample(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            durable=True,
            interaction_profile="CODEX_APP_INTERACTIVE",
            native_mcp=True,
        ),
        "codex_desktop_tool_gap": sample(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            durable=True,
            interaction_profile="CODEX_APP_INTERACTIVE",
            native_mcp=False,
        ),
        "codex_cli_native": sample(
            HostKind.CODEX_CLI,
            ephemeral=False,
            durable=True,
            interaction_profile="CODEX_CLI_NATIVE",
            native_mcp=True,
        ),
        "headless_api": sample(
            HostKind.CODEX_CLI,
            ephemeral=False,
            durable=True,
            interaction_profile="DIRECT_CLI_API",
            native_mcp=True,
        ),
        "ephemeral_vm_durable_mount": sample(
            HostKind.CODEX_VM,
            ephemeral=True,
            durable=True,
            interaction_profile="CODEX_APP_INTERACTIVE",
            native_mcp=True,
        ),
        "ephemeral_vm_connector_required": sample(
            HostKind.CODEX_VM,
            ephemeral=True,
            durable=False,
            interaction_profile="CODEX_APP_INTERACTIVE",
            native_mcp=True,
        ),
    }
    hidden_surface_error = None
    try:
        classify_runtime_host(
            HostKind.CODEX_DESKTOP,
            ephemeral=False,
            server_has_durable_filesystem=True,
            runtime_context={
                "host_surface": {
                    "container_channel": "CHATGPT_DESKTOP_BETA",
                    "active_surface": "CHATGPT_CHAT",
                    "active_surface_evidence": ("HOST_SESSION_TASK_CAPABILITY_RECEIPT"),
                }
            },
            host_session_id="hidden-chatgpt-surface-audit",
        )
    except EvidenceLaneError as exc:
        hidden_surface_error = exc.code

    service_text = (
        plugin_root / "src" / "evidence_lane_plugin" / "service.py"
    ).read_text(encoding="utf-8")
    desktop_matrix = dict(
        declared.get("interactive_codex_app_local_or_persistent") or {}
    )
    desktop_variants = dict(desktop_matrix.get("desktop_app_variants") or {})
    checks = {
        "declared_axes_independent": declared.get("routing_axes_independent") is True,
        "desktop_native_local": scenarios["codex_desktop_native"][
            "primary_runtime_authority"
        ]
        == "LOCAL_DURABLE_SQLITE",
        "desktop_native_no_tunnel": scenarios["codex_desktop_native"][
            "tunnel_requirement"
        ]
        == "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        "desktop_gap_release_tunnel": scenarios["codex_desktop_tool_gap"][
            "tunnel_setup_frequency"
        ]
        == "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        "desktop_stable_app_declared": desktop_variants.get("stable")
        == "OpenAI.Codex_2p2nqsd0c76g0!App",
        "desktop_beta_app_declared": desktop_variants.get("beta")
        == "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
        "desktop_apps_share_plugin": desktop_variants.get("shared_plugin_contract")
        is True,
        "desktop_apps_share_host_tunnel": desktop_variants.get(
            "shared_host_wide_tunnel"
        )
        is True,
        "per_app_tunnel_forbidden": desktop_variants.get("per_app_tunnel_allowed")
        is False,
        "per_project_task_tunnel_forbidden": desktop_variants.get(
            "per_project_or_task_tunnel_allowed"
        )
        is False,
        "exact_app_helper_binding_required": desktop_variants.get(
            "helper_requires_exact_requested_app_id"
        )
        is True,
        "cross_app_fallback_forbidden": desktop_variants.get(
            "cross_app_fallback_allowed"
        )
        is False,
        "cli_native_local": scenarios["codex_cli_native"]["primary_runtime_authority"]
        == "LOCAL_DURABLE_SQLITE",
        "headless_api_no_tunnel": scenarios["headless_api"]["tunnel_requirement"]
        == "NOT_REQUIRED_FOR_API_LAYER",
        "ephemeral_durable_mount_local": scenarios["ephemeral_vm_durable_mount"][
            "primary_runtime_authority"
        ]
        == "DURABLE_MOUNT_SQLITE",
        "ephemeral_without_mount_connector": scenarios[
            "ephemeral_vm_connector_required"
        ]["primary_runtime_authority"]
        == "CONFIGURED_TRANSACTIONAL_RUNTIME_REQUIRED",
        "chatgpt_surface_hidden": hidden_surface_error == "ACTIVE_SURFACE_OUT_OF_SCOPE",
        "activation_calls_automatic_matrix": "automatic = route_persistence("
        in service_text,
        "chatgpt_app_manifest_absent": not (plugin_root / ".app.json").exists(),
    }
    core = {
        "schema": "evidence-lane.host-storage-tunnel-forensic-audit.v1",
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "scenarios": scenarios,
        "chatgpt_surface_policy": "HIDDEN_UNTIL_SEPARATE_SUBMISSION_AND_ACCEPTANCE",
        "chatgpt_block_code": hidden_surface_error,
        "automatic_activation_classification": True,
        "counts_or_account_tier_used_as_routing_authority": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def _audit_model_compatibility(plugin_root: Path) -> dict[str, Any]:
    catalog = model_compatibility_catalog()
    scenarios = {
        "sol_xhigh_standard_proven": classify_model_compatibility(
            {
                "model": "gpt-5.6-sol",
                "submodel": "sol",
                "reasoning_effort": "xhigh",
                "reasoning_speed": "standard",
            },
            installed_host_tooling_proven=True,
        ),
        "terra_medium_standard_unproven": classify_model_compatibility(
            {
                "model": "gpt-5.6-terra",
                "submodel": "terra",
                "reasoning_effort": "medium",
                "reasoning_speed": "standard",
            }
        ),
        "luna_max_standard_proven": classify_model_compatibility(
            {
                "model": "gpt-5.6-luna",
                "submodel": "luna",
                "reasoning_effort": "max",
                "reasoning_speed": "standard",
            },
            installed_host_tooling_proven=True,
        ),
        "legacy_5_4_high_proven": classify_model_compatibility(
            {
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "reasoning_speed": "standard",
            },
            installed_host_tooling_proven=True,
        ),
        "mini_5_4_high_unproven": classify_model_compatibility(
            {
                "model": "gpt-5.4-mini",
                "reasoning_effort": "high",
                "reasoning_speed": "standard",
            }
        ),
        "codex_spark_blocked": classify_model_compatibility(
            {
                "model": "5.3 Codex Spark",
                "reasoning_effort": "xhigh",
                "reasoning_speed": "standard",
            }
        ),
        "instant_blocked": classify_model_compatibility(
            {
                "model": "gpt-5.3-chat",
                "reasoning_effort": "medium",
                "reasoning_speed": "instant",
            },
            installed_host_tooling_proven=True,
        ),
    }
    classifier_text = (
        plugin_root / "src" / "evidence_lane_plugin" / "runtime_host_classifier.py"
    ).read_text(encoding="utf-8")
    checks = {
        "catalog_pass": catalog["status"] == "PASS",
        "current_sol_proven": scenarios["sol_xhigh_standard_proven"]["status"]
        == "SUPPORTED_PROFILE",
        "terra_unproven_conditional": scenarios["terra_medium_standard_unproven"][
            "status"
        ]
        == "CONDITIONAL_INSTALLED_PROOF_REQUIRED",
        "luna_proven": scenarios["luna_max_standard_proven"]["status"]
        == "SUPPORTED_PROFILE",
        "legacy_5_4_proven": scenarios["legacy_5_4_high_proven"]["status"]
        == "SUPPORTED_PROFILE",
        "mini_5_4_unproven_conditional": scenarios["mini_5_4_high_unproven"]["status"]
        == "CONDITIONAL_INSTALLED_PROOF_REQUIRED",
        "codex_spark_blocked": scenarios["codex_spark_blocked"]["status"]
        == "BLOCKED_INCOMPATIBLE_PROFILE",
        "instant_blocked": scenarios["instant_blocked"]["status"]
        == "BLOCKED_INCOMPATIBLE_PROFILE",
        "activation_classifier_integrated": "classify_model_compatibility("
        in classifier_text,
        "chatgpt_surface_hidden": catalog["chatgpt_surface_exposed"] is False,
    }
    core = {
        "schema": "evidence-lane.model-compatibility-forensic-audit.v1",
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "catalog_sha256": catalog["catalog_sha256"],
        "scenarios": scenarios,
        "model_name_alone_is_qualification": False,
        "installed_host_matrix_required_for_each_profile": True,
        "chatgpt_surface_exposed": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def build_systemwide_route_audit(
    *,
    plugin_root: str | Path,
    repository_root: str | Path,
    plan_runtime_sqlite: str | Path,
    active_row: int,
    systemwide_regression_receipt: str | Path | None = None,
) -> dict[str, Any]:
    """Audit every current public consumer against Plan supersession history."""

    root = Path(plugin_root).resolve()
    repository = Path(repository_root).resolve()
    routing = _json(root / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json")
    public_catalog = _json(root / "schemas" / "public-action-schemas.v001.json")
    remote_projection = (
        repository
        / "apps"
        / "evidence-lane-app"
        / "app"
        / "_data"
        / "public-action-registry.json"
    )
    remote = _json(remote_projection)
    registry = current_implementation_registry()
    routing_tools = {str(name) for name in routing["tool_owners"]}
    registry_tools = {str(row["tool"]) for row in registry["public_tool_routes"]}
    public_tools = {str(row["name"]) for row in public_catalog["tools"]}
    remote_tools = {str(row["name"]) for row in remote["actions"]}
    sdk_tools = {str(row[0]) for row in SDK_NATIVE_ACTIONS}

    with tempfile.TemporaryDirectory(prefix="evidence-lane-route-audit-") as temp:
        server = create_mcp_server(service=EvidenceLaneService(data_root=Path(temp)))
        listed_mcp_tools = list(server._tool_manager.list_tools())
        mcp_tool_map = {tool.name: tool for tool in listed_mcp_tools}
        mcp_tools = set(mcp_tool_map)
        internal_sdk_public_dispatch = dict(
            server._evidence_lane_internal_sdk_public_dispatch_review  # type: ignore[attr-defined]
        )

    opaque_sdk_payload_tools = []
    for name in sorted(sdk_tools):
        properties = set(dict(mcp_tool_map[name].parameters.get("properties") or {}))
        if "payload" in properties and properties <= {
            "project_id",
            "session_id",
            "request_id",
            "payload",
        }:
            opaque_sdk_payload_tools.append(name)
    semantic_required_fields = {
        "canon_seal_envelope": {
            "source",
            "destination",
            "direction",
            "canon_type",
            "payload",
            "expected_return_contract_sha256",
            "route_trace",
        },
        "canon_dispatch_linked_task": {
            "source",
            "task_mode",
            "scope_class",
            "user_subagent_authorized",
            "direction",
            "host_creation_receipt",
        },
        "canon_backfire_hil": {
            "admitted_canon_id",
            "failure_class",
            "recipient",
            "return_route",
            "backfire_trace",
        },
        "canon_seal_result": {
            "edge_id",
            "result_payload",
            "schema_id",
            "schema_sha256",
        },
        "project_memory_query": {"query", "as_of", "sectors", "limit"},
        "project_memory_record_link": {
            "source",
            "target",
            "edge_type",
            "evidence_sha256",
        },
    }
    semantic_schema_missing_fields = {
        name: sorted(
            fields - set(dict(mcp_tool_map[name].parameters.get("properties") or {}))
        )
        for name, fields in semantic_required_fields.items()
        if fields - set(dict(mcp_tool_map[name].parameters.get("properties") or {}))
    }
    semantic_public_schema = {
        "schema": "evidence-lane.semantic-public-schema-audit.v1",
        "status": (
            "PASS"
            if not opaque_sdk_payload_tools and not semantic_schema_missing_fields
            else "BLOCKED"
        ),
        "sdk_native_action_count": len(sdk_tools),
        "opaque_payload_tool_count": len(opaque_sdk_payload_tools),
        "opaque_payload_tools": opaque_sdk_payload_tools,
        "critical_schema_missing_fields": semantic_schema_missing_fields,
        "canon_caller_mediated_native_dispatch_required": True,
        "counts_are_derived_outputs_not_fixed_targets": True,
    }

    obsolete = {str(name) for name in registry["obsolete_public_tools"]}
    purged_public_route_names = {
        "pv_refresh",
        "pv_state_travel_prepare",
        "pv_state_travel_resume",
    }
    obsolete_workflow_violations: list[str] = []
    for workflow_name, workflow in routing["workflows"].items():
        for group in workflow["ordered_tool_groups"]:
            for tool in group["tools"]:
                if tool in purged_public_route_names:
                    obsolete_workflow_violations.append(
                        f"{workflow_name}:{group['workflow']}:{tool}"
                    )

    service_parity = inspect_service_route_parity()
    purged_service_methods = {
        "prepare_state_travel",
        "refresh",
        "resume_state_travel",
    }
    observed_service_methods = {str(row["method"]) for row in service_parity["methods"]}
    surface = derive_public_surface_registry(root)
    env_uop_six_way_governance = {
        "status": (
            "PASS"
            if public_catalog.get("lane_count") == 18
            and len(public_catalog.get("canonical_lanes") or []) == 18
            and public_catalog.get("env_uop_governed_six_way_arms")
            == _ENV_UOP_GOVERNED_SIX_WAY_ARMS
            and public_catalog.get("linked_operational_authorities")
            == _LINKED_OPERATIONAL_AUTHORITIES
            and public_catalog.get("hil_only_authorities") == _HIL_ONLY_AUTHORITIES
            and public_catalog.get("ordinary_live_authority_count") == 8
            else "BLOCKED"
        ),
        "sector_lane_count": int(public_catalog.get("lane_count") or 0),
        "project_engulf_in_project_sector_arm": True,
        "six_way_arms": public_catalog.get("env_uop_governed_six_way_arms"),
        "linked_operational_authorities": public_catalog.get(
            "linked_operational_authorities"
        ),
        "hil_only_authorities": public_catalog.get("hil_only_authorities"),
        "authority_merge_allowed": False,
        "env_and_uop_are_governance_not_authority_arms": True,
        "live_project_root_files_governed_through_project_sector_arm": True,
        "accepted_pointer_role": "BASELINE_IDENTITY_ONLY",
        "accepted_archive_query_allowed": False,
    }
    public_set_equality = (
        routing_tools == registry_tools == public_tools == remote_tools == mcp_tools
    )
    sdk_subset_registered = sdk_tools.issubset(routing_tools)
    sdk_route_registry_shared = (
        surface["current_implementation_registry"]["routes_match_tool_registry"] is True
    )
    all_public_actions_enter_internal_sdk = (
        internal_sdk_public_dispatch.get("status") == "PASS"
        and internal_sdk_public_dispatch.get("all_public_actions_enter_internal_sdk")
        is True
        and internal_sdk_public_dispatch.get("internal_sdk_public_action_count")
        == len(routing_tools)
        and internal_sdk_public_dispatch.get("outer_router_action_count")
        == len(mcp_tools)
    )
    sdk_module_operations = {
        module.module_id: {operation.name for operation in module.operations}
        for module in SDK_MODULES
    }
    env_uop_sdk_complete = sdk_module_operations.get("env_uop_operator_runtime") == {
        "status",
        "classify_mode",
        "compile_formula",
        "route_operator",
    }
    obsolete_public_routes_purged = (
        not obsolete
        and "obsolete_tool_tombstones" not in routing
        and not obsolete_workflow_violations
    )
    service_compatibility_routes_purged = not (
        purged_service_methods & observed_service_methods
    )
    plan = audit_plan_supersession(plan_runtime_sqlite, active_row=active_row)
    registry_plan_families = {
        str(family)
        for capability in registry["capabilities"]
        for family in capability.get("plan_families", [])
    }
    observed_plan_families = set(dict(plan.get("plan_family_counts") or {}))
    missing_plan_family_routes = sorted(observed_plan_families - registry_plan_families)
    plan_capability_route_coverage = {
        "schema": "evidence-lane.plan-capability-route-coverage.v1",
        "status": "PASS" if not missing_plan_family_routes else "BLOCKED",
        "observed_plan_family_count": len(observed_plan_families),
        "current_registry_plan_family_count": len(registry_plan_families),
        "missing_current_route_families": missing_plan_family_routes,
        "historical_rows_are_execution_authority": False,
        "latest_non_superseded_current_route_required": True,
    }
    regression = _systemwide_regression_receipt(systemwide_regression_receipt)
    skill_audit = _audit_skill_current_routes(root, routing)
    host_matrix_audit = _audit_host_storage_tunnel_matrix(root)
    model_compatibility_audit = _audit_model_compatibility(root)
    whole_plugin_sdk = whole_plugin_sdk_governance_registry()
    runtime_workflow_sdk = runtime_workflow_sdk_registry()
    tracked = tracked_worktree_file_manifest(repository)
    plugin_prefix = root.relative_to(repository).as_posix().rstrip("/") + "/"
    tracked_plugin_entries = [
        row for row in tracked["entries"] if str(row["path"]).startswith(plugin_prefix)
    ]
    tracked_plugin = {
        "schema": "evidence-lane.tracked-plugin-worktree-manifest.v1",
        "status": "PASS",
        "selection": tracked["selection"],
        "entry_count": len(tracked_plugin_entries),
        "entries_sha256": sha256_bytes(canonical_json_bytes(tracked_plugin_entries)),
        "tracked_deletion_count": sum(
            row["state"] == "TRACKED_DELETED_IN_WORKTREE"
            for row in tracked_plugin_entries
        ),
        "untracked_paths_included": False,
        "git_index_mutated": False,
        "git_ref_mutated": False,
    }
    tree = _current_plugin_tree_manifest(root)
    sdk_file_audit = audit_whole_plugin_sdk_files(root)

    status = (
        "PASS"
        if public_set_equality
        and sdk_subset_registered
        and sdk_route_registry_shared
        and all_public_actions_enter_internal_sdk
        and env_uop_sdk_complete
        and obsolete_public_routes_purged
        and service_compatibility_routes_purged
        and service_parity["status"] == "PASS"
        and surface["status"] == "PASS"
        and surface["release_catalog_matches_derived"] is True
        and surface["routing_catalog_matches_derived"] is True
        and env_uop_six_way_governance["status"] == "PASS"
        and semantic_public_schema["status"] == "PASS"
        and plan["status"] == "PASS"
        and plan_capability_route_coverage["status"] == "PASS"
        and skill_audit["status"] == "PASS"
        and host_matrix_audit["status"] == "PASS"
        and model_compatibility_audit["status"] == "PASS"
        and whole_plugin_sdk["status"] == "PASS"
        and runtime_workflow_sdk["status"] == "PASS"
        and sdk_file_audit["status"] == "PASS"
        else "BLOCKED"
    )
    core = {
        "schema": SYSTEMWIDE_ROUTE_AUDIT_SCHEMA,
        "status": status,
        "scope": "PLAN_HISTORY_THROUGH_ACTIVE_ROW_AND_ALL_PUBLIC_CONSUMERS",
        "active_row": int(active_row),
        "plan_supersession": plan,
        "plan_capability_route_coverage": plan_capability_route_coverage,
        "systemwide_regression": regression,
        "skill_current_route_audit": skill_audit,
        "host_storage_tunnel_matrix": host_matrix_audit,
        "model_compatibility": model_compatibility_audit,
        "whole_plugin_sdk_governance": whole_plugin_sdk,
        "runtime_workflow_sdk_registry": runtime_workflow_sdk,
        "whole_plugin_sdk_file_audit": sdk_file_audit,
        "semantic_public_schema": semantic_public_schema,
        "current_registry": {
            "capability_count": registry["capability_count"],
            "public_tool_count": registry["public_tool_count"],
            "obsolete_public_tools": registry["obsolete_public_tools"],
            "registry_sha256": registry["registry_sha256"],
        },
        "consumer_parity": {
            "status": (
                "PASS"
                if public_set_equality
                and sdk_subset_registered
                and sdk_route_registry_shared
                and all_public_actions_enter_internal_sdk
                and env_uop_sdk_complete
                and semantic_public_schema["status"] == "PASS"
                else "BLOCKED"
            ),
            "tool_count": len(routing_tools),
            "routing_sha256": sha256_file(
                root / "skills" / "evi" / "references" / "mcp-tool-routing.v1.json"
            ),
            "public_catalog_sha256": sha256_file(
                root / "schemas" / "public-action-schemas.v001.json"
            ),
            "remote_projection_sha256": sha256_file(remote_projection),
            "routing_equals_registry": routing_tools == registry_tools,
            "routing_equals_public_schema": routing_tools == public_tools,
            "routing_equals_remote_adapter": routing_tools == remote_tools,
            "specialized_sdk_action_count": len(sdk_tools),
            "sdk_subset_registered": sdk_subset_registered,
            "sdk_route_registry_shared": sdk_route_registry_shared,
            "all_public_actions_enter_internal_sdk": (
                all_public_actions_enter_internal_sdk
            ),
            "internal_sdk_public_dispatch": internal_sdk_public_dispatch,
            "env_uop_sdk_module_complete": env_uop_sdk_complete,
            "env_uop_sdk_operations": sorted(
                sdk_module_operations.get("env_uop_operator_runtime", set())
            ),
            "semantic_public_schema_status": semantic_public_schema["status"],
            "routing_equals_mcp": routing_tools == mcp_tools,
        },
        "obsolete_route_purge": {
            "status": (
                "PASS"
                if obsolete_public_routes_purged and service_compatibility_routes_purged
                else "BLOCKED"
            ),
            "obsolete_public_tools": sorted(obsolete),
            "routing_tombstone_section_present": (
                "obsolete_tool_tombstones" in routing
            ),
            "active_workflow_violations": obsolete_workflow_violations,
            "purged_service_methods_present": sorted(
                purged_service_methods & observed_service_methods
            ),
            "public_and_service_compatibility_routes_purged": (
                obsolete_public_routes_purged and service_compatibility_routes_purged
            ),
        },
        "surface_registry": {
            "status": surface["status"],
            "registry_sha256": surface["registry_sha256"],
            "catalog": surface["catalog"],
            "release_catalog_matches_derived": surface[
                "release_catalog_matches_derived"
            ],
            "routing_catalog_matches_derived": surface[
                "routing_catalog_matches_derived"
            ],
        },
        "env_uop_six_way_governance": env_uop_six_way_governance,
        "source_fingerprints": {
            "tracked_plugin": tracked_plugin,
            "current_plugin_tree": {
                key: value for key, value in tree.items() if key != "entries"
            },
        },
        "accepted_archive_queried": False,
        "candidate_created_or_cleared": False,
        "pointer_moved": False,
        "git_index_mutated": False,
        "git_ref_mutated": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}
