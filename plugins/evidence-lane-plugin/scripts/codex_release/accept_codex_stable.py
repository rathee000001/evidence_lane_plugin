"""Verify the installed Evidence Lane 2.1 Codex Git stable before final HIL.

This checker is read-only except for its explicit receipt output. It compares the
exact Git marketplace checkout with Codex's generated installed cache, validates
the enabled canonical selector, statically proves the 62/21/41 catalog and
fifteen skills, and optionally binds a post-restart native route receipt. It
never calls lifecycle, Git, tunnel, candidate, pointer, or HIL actions.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any

BASE_RELEASE = "2.1.0"
FALLBACK_RELEASE = "2.0.0"
PLUGIN_NAME = "evidence-lane-plugin"
MARKETPLACE_NAME = "evidence-lane-github"
MARKETPLACE_DISPLAY_NAME = "GitLane Stable 2.1"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
HOOK_TRUST_SCHEMA = "evidence-lane.codex-hook-trust.v1"
EXPECTED_CODEX_HOST_HOOK_EVENTS = {
    "postToolUse",
    "sessionStart",
    "stop",
    "userPromptSubmit",
}
EXPECTED_PACKAGE_HOOK_EVENTS = {
    "PostCompact",
    "PostToolUse",
    "PreCompact",
    "PreToolUse",
    "SessionEnd",
    "SessionStart",
    "Stop",
    "UserPromptSubmit",
}
EXPECTED_CATALOG = {"tools": 62, "read": 21, "write": 41, "skills": 15}
EXPECTED_BEHAVIOR_OWNERSHIP = {
    "hooks": "LIFECYCLE_CAPTURE_AND_SEALED_EVENTS_ONLY",
    "skills": "NATIVE_PV_READS_AND_HOST_BEHAVIOR",
    "prompt_and_steer_native_reads": [
        "pv_status",
        "pv_task_backlog",
        "pv_query",
    ],
    "query_must_use_native_mcp_route": True,
    "internal_hook_lookup_satisfies_native_query": False,
    "host_plan_tool": "update_plan",
    "hook_may_embed_full_plan_rows": False,
    "hook_may_call_or_instruct_host_behavior": False,
    "skill_must_refresh_after_every_prompt_or_steer": True,
    "ordinary_question_appends_plan_delta": False,
    "plan_steer_requires_executable_goal_contract_change": True,
    "plan_steer_refreshes_full_panel_and_current_change_once": True,
    "fail_closed_when_behavior_route_unavailable": True,
}
EXPECTED_STABLE_ACTIVATION_GATE = {
    "local_rehearsal_stage_only": True,
    "local_rehearsal_activation_allowed": False,
    "exact_commit_package_builder": (
        "scripts/codex_release/build_codex_exact_commit_package.py"
    ),
    "release_authority_joiner": (
        "scripts/codex_release/seal_codex_git_ci_release_authority.py"
    ),
    "external_release_receipt_sealer": (
        "scripts/codex_release/seal_external_release_receipts.py"
    ),
    "stable_update_helper": (
        "scripts/codex_release/Update-EvidenceLaneCodexStableAndResume.ps1"
    ),
    "stable_update_reopens_same_bound_host_app": True,
    "stable_update_rebinds_general_goal_recovery": True,
    "release_authority_schema": "evidence-lane.codex-git-ci-vercel-release-authority.v2",
    "exact_clean_commit_required": True,
    "governed_native_remote_push_required": True,
    "successful_github_ci_required": True,
    "successful_vercel_branch_preview_required": True,
    "production_deployment_allowed": False,
    "exact_commit_git_marketplace_required": True,
    "delivery_cadence": "DEPENDENCY_COHERENT_INTEGRATION_BUNDLES",
    "per_delta_commit_ci_install_forbidden": True,
    "bundle_count_is_derived_not_quota": True,
    "bundle_boundary_derivation_axes": [
        "SOURCE_SCHEMA_RUNTIME_COUPLING",
        "CROSS_DELTA_TEST_GRAPH",
        "INSTALLED_HOST_PROOF_BOUNDARY",
    ],
    "row_acceptance_evidence_remains_individual": True,
    "per_delta_governance_routes_preserved": [
        "PREPARE_CAPTURE_RETRIEVAL",
        "TASK_ROW_CURRENT_CHANGE_CLASSIFICATION",
        "PV_STATUS_BACKLOG_QUERY_READS",
        "CHATLINEAGE_ACTIVITY",
        "EXACT_ACCEPTANCE_EVIDENCE",
        "LIFECYCLE_TRANSITION",
        "PERSISTENT_PLAN_CURRENT_CHANGE_REPROJECTION",
    ],
    "cross_delta_verification_matrix_required": True,
    "cross_delta_matrix_dimensions": [
        "TASK_ID",
        "CHANGED_SURFACES",
        "LOCAL_TESTS",
        "REMOTE_CHECKS",
        "INSTALLED_HOST_CHECKS",
        "OUTCOME",
        "FAILURE_OWNER",
    ],
    "bundle_failure_policy": "ANY_INCLUDED_ROW_FAILURE_FAILS_BUNDLE_CLOSED",
    "bundle_commit_syncs_root_and_repository_docs": True,
    "stable_install_source": "EXACT_GIT_COMMIT_PACKAGE_ONLY",
    "local_or_dirty_worktree_stable_install_allowed": False,
    "all_configured_commit_checks_required_before_stable_install": True,
    "one_stable_update_per_integration_bundle": True,
    "git_marketplace_name": MARKETPLACE_NAME,
    "git_marketplace_display_name": MARKETPLACE_DISPLAY_NAME,
    "git_marketplace_source": "rathee000001/evidence_lane_plugin",
    "one_time_legacy_stable_selector_migration_allowed": True,
    "post_proof_obsolete_cleanup_required": True,
    "same_stable_selector_required_after_migration": True,
    "installed_runtime_prewarm_required": True,
    "runtime_ready_before_task_reopen_required": True,
    "fallback_activation_inferred": False,
}
EXPECTED_BRAND_IDENTITY = {
    "display_name": "Evidence Lane",
    "icon_path": "assets/evidence-lane-icon.png",
    "icon_sha256": "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3DEF87B8A129C4FA",
    "resource_uri": "ui://evidence-lane/governed-console-v3.html",
    "manifest_icon_fields": ["interface.composerIcon", "interface.logo"],
    "required_at_stage": True,
    "required_at_runtime_prewarm": True,
}
EXPECTED_HOST_STORAGE_TUNNEL_MATRIX = {
    "routing_axes_independent": True,
    "account_tier_affects_routing": False,
    "api_billing_affects_routing": False,
    "headless_api": {
        "local_or_persistent_pv_storage": "LOCAL_SQLITE_WHEN_DURABLE",
        "ephemeral_pv_storage": (
            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
        ),
        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
        "flash_frequency": "EVERY_INVOCATION_ENTRY",
    },
    "interactive_codex_app_local_or_persistent": {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
    },
    "interactive_codex_app_ephemeral_vm": {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
    },
}
SCHEMA = "evidence-lane.codex-installed-acceptance.v2"
_IGNORED_DIRECTORIES = frozenset({".venv", "__pycache__", ".pytest_cache"})
_IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".tmp"})
_MIGRATED_COMMAND_ROOT = ".codex-plugin/migrated-command-skills"


class AcceptanceError(RuntimeError):
    """Raised when installed stable identity is not exact."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sealed_json(path: Path, *, hash_field: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    claimed = str(value.get(hash_field) or "")
    body = {key: item for key, item in value.items() if key != hash_field}
    if not claimed or claimed != _sha256_bytes(_json_bytes(body)):
        raise AcceptanceError(f"{path.name} failed its {hash_field} seal.")
    return value


def _inventory(root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative_path = path.relative_to(root)
        if any(part in _IGNORED_DIRECTORIES for part in relative_path.parts):
            continue
        if path.suffix.casefold() in _IGNORED_SUFFIXES:
            continue
        relative = relative_path.as_posix()
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "file_count": len(rows),
        "manifest_sha256": _sha256_bytes(_json_bytes(rows)),
        "files": rows,
    }


def _expected_migrated_command_skills(marketplace: Path) -> dict[str, bytes]:
    """Derive the exact command-to-skill files generated by supported Codex install."""

    expected: dict[str, bytes] = {}
    commands = marketplace / "commands"
    if not commands.is_dir():
        return expected
    for command in sorted(commands.glob("*.md"), key=lambda item: item.name):
        text = command.read_text(encoding="utf-8")
        match = re.fullmatch(
            r"---\r?\n(?P<frontmatter>.*?)\r?\n---\r?\n(?P<body>.*)",
            text,
            flags=re.DOTALL,
        )
        if match is None:
            raise AcceptanceError(f"{command.name} has no exact command frontmatter.")
        description_match = re.search(
            r"(?m)^description:\s*(?P<description>.+?)\s*$",
            match.group("frontmatter"),
        )
        if description_match is None:
            raise AcceptanceError(f"{command.name} has no command description.")
        description = description_match.group("description").strip()
        if description.startswith('"'):
            try:
                description = str(json.loads(description))
            except json.JSONDecodeError as exc:
                raise AcceptanceError(
                    f"{command.name} has an invalid quoted description."
                ) from exc
        elif description.startswith("'") and description.endswith("'"):
            description = description[1:-1].replace("''", "'")
        if not description or "\n" in description or "\r" in description:
            raise AcceptanceError(f"{command.name} has an invalid description.")
        command_name = command.stem
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", command_name):
            raise AcceptanceError(f"{command.name} cannot form a migrated skill name.")
        skill_name = f"source-command-{command_name}"
        body = match.group("body").lstrip("\r\n").rstrip()
        generated = (
            "---\n"
            f"name: {json.dumps(skill_name, ensure_ascii=False)}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            "---\n\n"
            f"# {skill_name}\n\n"
            "Use this skill when the user asks to run the migrated source command "
            f"`{command_name}`.\n\n"
            "## Command Template\n\n"
            f"{body}\n"
        ).encode()
        relative = f"{_MIGRATED_COMMAND_ROOT}/{skill_name}/SKILL.md"
        expected[relative] = generated
    return expected


def _verified_cache_inventory(installed: Path, marketplace: Path) -> dict[str, Any]:
    """Prove source parity plus only exact Codex-generated command migrations."""

    installed_inventory = _inventory(installed)
    marketplace_inventory = _inventory(marketplace)
    installed_files = {
        str(row["path"]): row for row in installed_inventory["files"]
    }
    marketplace_files = {
        str(row["path"]): row for row in marketplace_inventory["files"]
    }
    if any(
        path == _MIGRATED_COMMAND_ROOT
        or path.startswith(f"{_MIGRATED_COMMAND_ROOT}/")
        for path in marketplace_files
    ):
        raise AcceptanceError("The marketplace must not prebuild host-generated skills.")
    source_drift = [
        path
        for path, expected in marketplace_files.items()
        if installed_files.get(path) != expected
    ]
    if source_drift:
        raise AcceptanceError("Codex cache source bytes differ from the marketplace.")
    expected_generated = _expected_migrated_command_skills(marketplace)
    actual_extra = set(installed_files).difference(marketplace_files)
    if actual_extra != set(expected_generated):
        raise AcceptanceError(
            "Codex cache extras are not the exact generated command-skill set."
        )
    generated_records: list[dict[str, Any]] = []
    for relative, expected_bytes in sorted(expected_generated.items()):
        actual = installed_files[relative]
        expected_sha256 = _sha256_bytes(expected_bytes)
        if (
            int(actual["bytes"]) != len(expected_bytes)
            or actual["sha256"] != expected_sha256
            or (installed / Path(relative)).read_bytes() != expected_bytes
        ):
            raise AcceptanceError(
                "A Codex-generated command skill differs from its exact derivation."
            )
        generated_records.append(
            {
                "skill_name": Path(relative).parent.name,
                "bytes": len(expected_bytes),
                "sha256": expected_sha256,
            }
        )
    return {
        "installed_file_count": installed_inventory["file_count"],
        "installed_manifest_sha256": installed_inventory["manifest_sha256"],
        "marketplace_file_count": marketplace_inventory["file_count"],
        "marketplace_manifest_sha256": marketplace_inventory["manifest_sha256"],
        "source_bytes_match_marketplace": True,
        "codex_generated_migration_count": len(generated_records),
        "codex_generated_migrations": generated_records,
        "cache_matches_marketplace_after_expected_host_generation": True,
    }


def _surface_inventory(plugin_root: Path, *, version: str) -> dict[str, Any]:
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *sorted((plugin_root / "hooks").glob("*.py")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if not all(path.is_file() for path in hook_paths):
        raise AcceptanceError("The installed persistent hook inventory is incomplete.")
    if {path.name for path in hook_paths} != {
        "hooks.json",
        "lifecycle_boundary.py",
        "post_tool_use.py",
        "pre_tool_use.py",
        "session_start.py",
        "prompt_submit.py",
        "stop_response.py",
    }:
        raise AcceptanceError("The installed persistent hook inventory is not exact.")

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        rows = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        if len(rows) != len({row["name"] for row in rows}):
            raise AcceptanceError("An installed hook or skill name is duplicated.")
        return {
            "count": len(rows),
            "records": rows,
            "inventory_sha256": _sha256_bytes(_json_bytes(rows)),
        }

    hook_files = inventory(hook_paths, skill=False)
    hook_configuration = json.loads(
        (plugin_root / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    hook_events = dict(hook_configuration.get("hooks") or {})
    registered_events = sorted(hook_events)
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hook_events.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    hook_inventory = {
        "count": len(registered_events),
        "count_semantics": "REGISTERED_EVENT_COUNT",
        "registered_event_count": len(registered_events),
        "registered_events": registered_events,
        "handler_count": handler_count,
        "hook_file_count": hook_files["count"],
        "records": hook_files["records"],
        "file_inventory_sha256": hook_files["inventory_sha256"],
        "event_inventory_sha256": _sha256_bytes(_json_bytes(registered_events)),
    }
    hook_inventory["inventory_sha256"] = _sha256_bytes(
        _json_bytes(hook_inventory)
    )
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": version,
        "hooks": hook_inventory,
        "skills": inventory(skill_paths, skill=True),
        "catalog": dict(EXPECTED_CATALOG),
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = _sha256_bytes(_json_bytes(core))
    return core


def _catalog(plugin_root: Path) -> dict[str, Any]:
    path = plugin_root / "src" / "evidence_lane_plugin" / "mcp_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rows: list[dict[str, str]] = []
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
            keywords = {item.arg: item.value for item in decorator.keywords if item.arg}
            name_node = keywords.get("name")
            annotation_node = keywords.get("annotations")
            if not (
                isinstance(name_node, ast.Constant)
                and isinstance(name_node.value, str)
                and isinstance(annotation_node, ast.Name)
            ):
                raise AcceptanceError("Every native tool needs a literal name and annotation.")
            rows.append(
                {
                    "name": name_node.value,
                    "annotation": annotation_node.id,
                }
            )
    rows.sort(key=lambda row: row["name"])
    names = [row["name"] for row in rows]
    read = sum(row["annotation"] == "_READ_ONLY" for row in rows)
    write = len(rows) - read
    if (
        len(rows) != EXPECTED_CATALOG["tools"]
        or len(names) != len(set(names))
        or read != EXPECTED_CATALOG["read"]
        or write != EXPECTED_CATALOG["write"]
    ):
        raise AcceptanceError("The installed native 62/21/41 tool catalog drifted.")
    return {
        "tools": len(rows),
        "read": read,
        "write": write,
        "tool_names_unique": True,
        "static_catalog_sha256": _sha256_bytes(_json_bytes(rows)),
    }


def _engine_version(plugin_root: Path) -> str:
    path = plugin_root / "src" / "evidence_lane_plugin" / "constants.py"
    match = re.search(
        r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
        path.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    if match is None:
        raise AcceptanceError("ENGINE_VERSION is unavailable.")
    return match.group("version")


def _validate_plugin(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text("utf-8"))
    release = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    stable = dict(release.get("stable") or {})
    fallback = dict(release.get("fallback") or {})
    live_slots = dict(release.get("live_slot_policy") or {})
    failover = dict(release.get("failover_operator") or {})
    goal_recovery = dict(release.get("goal_recovery") or {})
    behavior_ownership = dict(release.get("behavior_ownership") or {})
    stable_activation_gate = dict(release.get("stable_activation_gate") or {})
    brand_identity = dict(release.get("brand_identity") or {})
    promotion = dict(release.get("promotion_gate") or {})
    remote_git = dict(release.get("remote_git_policy") or {})
    skills = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    native = json.loads((plugin_root / ".mcp.json").read_text("utf-8"))
    interface = dict(manifest.get("interface") or {})
    brand_icon = plugin_root / str(brand_identity.get("icon_path") or "")
    required_release_helpers = (
        plugin_root / "scripts" / "codex_release" / "install_codex_stable.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "build_codex_exact_commit_package.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_codex_git_ci_release_authority.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "seal_external_release_receipts.py",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Update-EvidenceLaneCodexStableAndResume.ps1",
        plugin_root / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Manage-EvidenceLaneCodexGoalRecovery.ps1",
        plugin_root
        / "scripts"
        / "codex_release"
        / "Switch-EvidenceLaneCodexSlot.ps1",
        plugin_root / "scripts" / "codex_release" / "accept_codex_stable.py",
    )
    forbidden = (
        plugin_root / ".app.json",
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    external_app_artifacts = tuple(plugin_root.glob("*-app-connection.json")) + tuple(
        plugin_root.glob("*-app-submission.json")
    )
    if (
        manifest.get("name") != PLUGIN_NAME
        or not version.startswith(f"{BASE_RELEASE}+codex.")
        or manifest.get("mcpServers") != "./.mcp.json"
        or "apps" in manifest
        or project.get("project", {}).get("version") != BASE_RELEASE
        or _engine_version(plugin_root) != BASE_RELEASE
        or release.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != BASE_RELEASE
        or stable.get("slot_role") != "stable-build"
        or stable.get("codex_marketplace_slot") != MARKETPLACE_NAME
        or stable.get("byte_frozen") is not False
        or stable.get("updates_require_verified_unique_build_identity") is not True
        or stable.get("stable_selector_is_persistent") is not True
        or stable.get("stable_updates_reinstall_in_place") is not True
        or stable.get("build_identity_is_receipt_not_selector") is not True
        or stable.get("native_tool_count") != EXPECTED_CATALOG["tools"]
        or stable.get("native_read_tool_count") != EXPECTED_CATALOG["read"]
        or stable.get("native_write_tool_count") != EXPECTED_CATALOG["write"]
        or stable.get("skill_count") != EXPECTED_CATALOG["skills"]
        or len(skills) != EXPECTED_CATALOG["skills"]
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or fallback.get("release") != FALLBACK_RELEASE
        or fallback.get("slot_role") != "fallback"
        or fallback.get("codex_marketplace_slot")
        != "evidence-lane-pv11-fallback"
        or fallback.get("enabled") is not False
        or fallback.get("materialization_gate")
        != "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
        or fallback.get("accepted_pv") != "PV11"
        or fallback.get("accepted_generation") != 11
        or fallback.get("byte_frozen") is not True
        or fallback.get("package_must_equal_accepted_pv") is not True
        or live_slots.get("exact_slot_count_after_pv11_acceptance") != 2
        or live_slots.get("allowed_slots") != ["stable-build", "fallback"]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 2
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or live_slots.get("inactive_slot_remains_installed") is not True
        or failover.get("script")
        != "scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1"
        or failover.get("registry_schema")
        != "evidence-lane.codex-two-slot-registry.v1"
        or failover.get("single_transient_error_switch_allowed") is not False
        or failover.get("stop_source_tunnel_before_start_target") is not True
        or failover.get("target_tunnel_ready_before_plugin_switch") is not True
        or failover.get("controlled_exact_task_restart_required") is not True
        or failover.get("switch_failure_restores_source_slot") is not True
        or goal_recovery.get("script")
        != "scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1"
        or goal_recovery.get("scope")
        != "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_ON_THIS_WINDOWS_USER"
        or goal_recovery.get("trigger") != "AT_LOGON_CURRENT_WINDOWS_USER"
        or goal_recovery.get("exact_task_uuid_required") is not True
        or goal_recovery.get("exact_host_app_binding_required") is not True
        or goal_recovery.get("supported_host_app_ids")
        != [
            "OpenAI.Codex_2p2nqsd0c76g0!App",
            "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
        ]
        or goal_recovery.get("persisted_goal_read_route")
        != "CODEX_APP_SERVER_THREAD_READ_PLUS_THREAD_GOAL_GET"
        or goal_recovery.get("thread_resume_writer_allowed") is not False
        or goal_recovery.get("synthetic_prompt_allowed") is not False
        or goal_recovery.get("turn_start_allowed") is not False
        or goal_recovery.get("state_travel_allowed") is not False
        or goal_recovery.get("candidate_hil_pointer_or_git_mutation_allowed")
        is not False
        or goal_recovery.get("requires_stable_enabled_fallback_disabled") is not True
        or goal_recovery.get("stable_selector_growth_allowed") is not False
        or goal_recovery.get("raw_goal_objective_stored") is not False
        or behavior_ownership != EXPECTED_BEHAVIOR_OWNERSHIP
        or stable_activation_gate != EXPECTED_STABLE_ACTIVATION_GATE
        or brand_identity != EXPECTED_BRAND_IDENTITY
        or interface.get("displayName") != brand_identity.get("display_name")
        or interface.get("composerIcon") != "./assets/evidence-lane-icon.png"
        or interface.get("logo") != "./assets/evidence-lane-icon.png"
        or not brand_icon.is_file()
        or _sha256(brand_icon) != brand_identity.get("icon_sha256")
        or release.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_six_way_hil_required") is not True
        or remote_git.get("per_push_confirmation_token_required") is not False
        or remote_git.get("automatic_push_scope")
        != "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not False
        or set(native.get("mcpServers") or {}) != {"evidence-lane"}
        or not all(path.is_file() for path in required_release_helpers)
        or any(path.exists() for path in forbidden)
        or bool(external_app_artifacts)
    ):
        raise AcceptanceError("The installed v2 package identity or boundary drifted.")
    hooks = json.loads((plugin_root / "hooks" / "hooks.json").read_text("utf-8"))
    hook_events = dict(hooks.get("hooks") or {})
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in hook_events.values()
        if isinstance(groups, list)
        for group in groups
        if isinstance(group, dict)
    )
    post_groups = hook_events.get("PostToolUse") or []
    post_matcher = str(post_groups[0].get("matcher") or "") if post_groups else ""
    if (
        set(hook_events) != EXPECTED_PACKAGE_HOOK_EVENTS
        or handler_count != len(EXPECTED_PACKAGE_HOOK_EVENTS)
        or post_matcher
    ):
        raise AcceptanceError("The installed persistent turn hooks drifted.")
    persistent_notice_markers = {
        "session_start.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
        "prompt_submit.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
        "post_tool_use.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=",
        "stop_response.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
    }
    for name, marker in persistent_notice_markers.items():
        source = (plugin_root / "hooks" / name).read_text(encoding="utf-8")
        required = (
            "persistent_change_system_notice",
            "persistent_change_system_message",
            'result["systemMessage"]',
            marker,
        )
        if any(value not in source for value in required):
            raise AcceptanceError(f"{name} does not emit the persistent change notice.")
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "release_contract_sha256": _sha256(
            plugin_root / "scripts" / "codex-release-channel.json"
        ),
        "skills": len(skills),
        "catalog": _catalog(plugin_root),
        "hook_events": [
            "PostToolUse",
            "SessionStart",
            "Stop",
            "UserPromptSubmit",
        ],
        "mode": "CODE",
        "ci_cd_law": "CONTROLLED_REQUIRED",
        "host_storage_tunnel_matrix": EXPECTED_HOST_STORAGE_TUNNEL_MATRIX,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
    }


def _native_receipt(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    route = payload.get("mcp_route_identity") or payload
    if (
        route.get("schema") != "evidence-lane.native-mcp-route-receipt.v1"
        or route.get("status") != "PASS"
        or route.get("server_identity") != "evidence-lane"
        or route.get("canonical_tool_namespace") != "mcp__evidence_lane__"
        or route.get("exposure_profile") != "FULL_LIFECYCLE"
        or route.get("tool_count") != EXPECTED_CATALOG["tools"]
        or route.get("tool_names_unique") is not True
        or route.get("project_route_argument_required") is not True
        or route.get("cross_project_fallback_allowed") is not False
        or not route.get("tool_catalog_sha256")
    ):
        raise AcceptanceError("The post-restart native route receipt drifted.")
    return {
        "status": "PASS",
        "server_identity": route["server_identity"],
        "canonical_tool_namespace": route["canonical_tool_namespace"],
        "tool_count": route["tool_count"],
        "tool_catalog_sha256": route["tool_catalog_sha256"],
        "receipt_file_sha256": _sha256(path),
    }


def accept(args: argparse.Namespace) -> dict[str, Any]:
    installed = args.installed_plugin.resolve()
    marketplace = args.marketplace_plugin.resolve()
    config = args.codex_config.resolve()
    archive = args.archive.resolve()
    package_argument = getattr(args, "package_receipt", None) or getattr(
        args, "rehearsal_receipt", None
    )
    if package_argument is None:
        raise AcceptanceError("An exact package receipt is required.")
    rehearsal_path = Path(package_argument).resolve()
    install_path = args.installation_receipt.resolve()
    rehearsal = json.loads(rehearsal_path.read_text(encoding="utf-8"))
    installation = _sealed_json(install_path, hash_field="receipt_sha256")
    activation = dict(installation.get("activation") or {})
    activation_authority = dict(installation.get("activation_authority") or {})
    runtime_prewarm = dict(activation.get("runtime_prewarm") or {})
    runtime_prewarm_core = dict(runtime_prewarm)
    runtime_prewarm_sha256 = str(
        runtime_prewarm_core.pop("receipt_sha256", "")
    ).upper()
    plugin_add = dict(activation.get("plugin_add") or {})
    exact_selector = str(plugin_add.get("pluginId") or "")
    hook_trust = dict(activation.get("hook_trust") or {})
    hook_trust_core = dict(hook_trust)
    hook_trust_sha256 = str(
        hook_trust_core.pop("receipt_sha256", "")
    ).upper()
    hook_records = hook_trust.get("records")
    if not isinstance(hook_records, list):
        hook_records = []
    hook_events = {
        str(row.get("event_name") or "")
        for row in hook_records
        if isinstance(row, dict)
    }
    hook_keys = [
        str(row.get("hook_key") or "")
        for row in hook_records
        if isinstance(row, dict)
    ]
    surface_change = dict(installation.get("surface_change_display") or {})
    if (
        rehearsal.get("status") != "PASS"
        or rehearsal.get("archive", {}).get("filename") != archive.name
        or rehearsal.get("archive", {}).get("sha256") != _sha256(archive)
        or installation.get("status") != "PASS"
        or installation.get("schema") != "evidence-lane.codex-stable-installation.v2"
        or installation.get("archive_sha256") != _sha256(archive)
        or activation_authority.get("status") != "PASS"
        or activation_authority.get("boundary")
        != "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
        or activation_authority.get("vercel_preview_ready") is not True
        or activation_authority.get("production_deployment") is not False
        or activation.get("state") != "INSTALLED_RESTART_REQUIRED"
        or activation.get("runtime_ready_before_task_reopen") is not True
        or installation.get("runtime_ready_before_task_reopen") is not True
        or runtime_prewarm.get("status") != "PASS"
        or runtime_prewarm.get("runtime_ready_before_task_reopen") is not True
        or runtime_prewarm.get("task_reopened") is not False
        or runtime_prewarm.get("tool_count") != EXPECTED_CATALOG["tools"]
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(runtime_prewarm.get("tool_catalog_sha256") or ""),
        )
        is None
        or runtime_prewarm.get("resource_uri")
        != EXPECTED_BRAND_IDENTITY["resource_uri"]
        or runtime_prewarm.get("brand_icon_sha256")
        != EXPECTED_BRAND_IDENTITY["icon_sha256"]
        or runtime_prewarm_sha256
        != _sha256_bytes(_json_bytes(runtime_prewarm_core))
        or exact_selector != PLUGIN_SELECTOR
        or plugin_add.get("installedPath") is None
        or Path(plugin_add["installedPath"]).resolve() != installed
        or installation.get("generated_cache_written_directly") is not False
        or installation.get("previous_release_cache_deleted") is not False
        or installation.get("fallback_materialization_gate")
        != "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
        or installation.get("fallback_materialized") is not False
        or installation.get(
            "live_cache_cleanup_deferred_until_exact_pv11_acceptance"
        )
        is not True
        or installation.get("two_slot_operator_packaged") is not True
        or installation.get("post_proof_obsolete_cleanup_completed") is not True
        or installation.get("obsolete_cleanup_used_supported_codex_apis") is not True
        or installation.get("credential_requested_or_stored") is not False
        or surface_change.get("schema")
        != "evidence-lane.codex-installed-surface-change-display.v2"
        or surface_change.get("raw_paths_included") is not False
        or surface_change.get("private_research_question_included") is not False
        or hook_trust.get("schema") != HOOK_TRUST_SCHEMA
        or hook_trust.get("status") != "PASS"
        or hook_trust.get("plugin_selector") != exact_selector
        or hook_trust.get("hook_count") != 4
        or hook_events != EXPECTED_CODEX_HOST_HOOK_EVENTS
        or len(hook_records) != 4
        or len(hook_keys) != len(set(hook_keys))
        or hook_trust.get("after_trust_statuses") != ["trusted"]
        or hook_trust_sha256
        != _sha256_bytes(_json_bytes(hook_trust_core))
        or any(
            not isinstance(row, dict)
            or row.get("enabled") is not True
            or row.get("trust_status") != "trusted"
            or not str(row.get("hook_key") or "").startswith(
                f"{exact_selector}:"
            )
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}",
                str(row.get("current_hash") or ""),
            )
            is None
            for row in hook_records
        )
        or dict(activation.get("git_marketplace_source") or {}).get("status")
        != "PASS"
        or dict(activation.get("git_marketplace_source") or {}).get("source_type")
        != "git"
        or dict(activation.get("git_marketplace_source") or {}).get(
            "exact_commit_package_bytes_match"
        )
        is not True
        or dict(activation.get("post_proof_cleanup") or {}).get("status") != "PASS"
        or dict(activation.get("post_proof_cleanup") or {}).get(
            "exact_installed_slot_count"
        )
        != 2
    ):
        raise AcceptanceError("The archive or supported installation receipt drifted.")
    configured = tomllib.loads(config.read_text(encoding="utf-8"))
    plugin_settings = dict(configured.get("plugins") or {})
    enabled = sorted(
        selector
        for selector, value in plugin_settings.items()
        if selector.startswith(f"{PLUGIN_NAME}@") and value.get("enabled") is True
    )
    exact_mcp = dict(plugin_settings.get(exact_selector) or {}).get("mcp_servers")
    exact_mcp_settings = (
        dict(exact_mcp).get("evidence-lane")
        if isinstance(exact_mcp, dict)
        else None
    )
    inactive_mcp_enabled = [
        selector
        for selector, value in plugin_settings.items()
        if selector.startswith(f"{PLUGIN_NAME}@")
        and selector != exact_selector
        and isinstance(value, dict)
        and isinstance(value.get("mcp_servers"), dict)
        and isinstance(value["mcp_servers"].get("evidence-lane"), dict)
        and value["mcp_servers"]["evidence-lane"].get("enabled") is True
    ]
    if (
        enabled != [exact_selector]
        or not isinstance(exact_mcp_settings, dict)
        or exact_mcp_settings.get("enabled") is not True
        or inactive_mcp_enabled
    ):
        raise AcceptanceError("Exactly the v2 Evidence Lane selector must be enabled.")
    installed_identity = _validate_plugin(installed)
    marketplace_identity = _validate_plugin(marketplace)
    if (
        surface_change.get("current_plugin_version")
        != installed_identity["version"]
        or surface_change.get("current_surface_inventory_sha256")
        != installed_identity["surface_inventory"]["surface_inventory_sha256"]
        or surface_change.get("hooks", {}).get("count")
        != installed_identity["surface_inventory"]["hooks"]["count"]
        or surface_change.get("hooks", {}).get("count_semantics")
        != "REGISTERED_EVENT_COUNT"
        or surface_change.get("hooks", {}).get("registered_event_count") != 8
        or surface_change.get("hooks", {}).get("registered_events")
        != [
            "PostCompact",
            "PostToolUse",
            "PreCompact",
            "PreToolUse",
            "SessionEnd",
            "SessionStart",
            "Stop",
            "UserPromptSubmit",
        ]
        or surface_change.get("hooks", {}).get("handler_count") != 8
        or surface_change.get("hooks", {}).get("hook_file_count") != 7
        or surface_change.get("skills", {}).get("count")
        != EXPECTED_CATALOG["skills"]
        or surface_change.get("catalog", {}).get("tools")
        != EXPECTED_CATALOG["tools"]
    ):
        raise AcceptanceError("The installed surface-change display drifted.")
    package_inventory = _verified_cache_inventory(installed, marketplace)
    native = _native_receipt(args.native_route_receipt)
    state = (
        "POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL"
        if native is not None
        else "PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED"
    )
    body = {
        "schema": SCHEMA,
        "status": "PASS",
        "state": state,
        "installed_plugin": installed_identity,
        "marketplace_plugin": marketplace_identity,
        "package_inventory": package_inventory,
        "catalog": dict(EXPECTED_CATALOG),
        "surface_change_display": surface_change,
        "enabled_selector": exact_selector,
        "hook_trust_receipt_sha256": hook_trust_sha256,
        "hook_trust": {
            "status": "PASS",
            "hook_count": 4,
            "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
            "after_trust_statuses": ["trusted"],
            "selector": exact_selector,
        },
        "archive_sha256": _sha256(archive),
        "package_receipt_sha256": _sha256(rehearsal_path),
        "installation_receipt_sha256": _sha256(install_path),
        "native_route": native,
        "restart_verified": native is not None,
        "installed_host_hil_required": True,
        "hil_inferred": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "git_invoked": False,
        "tunnel_invoked": False,
        "source_mutated": False,
    }
    body["receipt_sha256"] = _sha256_bytes(_json_bytes(body))
    _write_atomic(args.output.resolve(), body)
    return body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-plugin", type=Path, required=True)
    parser.add_argument("--marketplace-plugin", type=Path, required=True)
    parser.add_argument("--codex-config", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    package = parser.add_mutually_exclusive_group(required=True)
    package.add_argument("--package-receipt", type=Path)
    package.add_argument(
        "--rehearsal-receipt",
        type=Path,
        help="Compatibility name for historical staging receipts.",
    )
    parser.add_argument("--installation-receipt", type=Path, required=True)
    parser.add_argument("--native-route-receipt", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    print(json.dumps(accept(_parser().parse_args()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
