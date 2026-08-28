"""Build a deterministic, non-lifecycle Evidence Lane package rehearsal.

This command deliberately does not call Git or the Evidence Lane lifecycle.  It
packages the current plugin bytes for local verification, records the sealed
base commit/tree as an anchor, and gives the working source its own manifest
identity.  The result is not a governed candidate and cannot be accepted or
promoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.non-lifecycle-local-package-rehearsal.v1"
BOUNDARY = "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
EXPECTED_LANE_COUNT = 18
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
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "host_profile": "CODEX_DESKTOP",
        "desktop_app_variants": {
            "stable": "OpenAI.Codex_2p2nqsd0c76g0!App",
            "beta": "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
            "shared_plugin_contract": True,
            "shared_host_wide_tunnel": True,
            "per_app_tunnel_allowed": False,
            "per_project_or_task_tunnel_allowed": False,
            "helper_requires_exact_requested_app_id": True,
            "cross_app_fallback_allowed": False,
        },
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
            "tunnel_setup_frequency": "NONE",
            "tunnel_key_retention": "NOT_APPLICABLE",
            "tunnel_runtime_lifetime": "NOT_APPLICABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
            "tunnel_key_retention": "CURRENT_WINDOWS_USER_DPAPI_PROFILE",
            "tunnel_runtime_lifetime": "WINDOWS_LOGON_MANAGED_PERSISTENT_HOST",
        },
    },
    "codex_cli_local_or_persistent": {
        "pv_storage": "DURABLE_LOCAL_SQLITE",
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE",
        },
    },
    "interactive_codex_app_ephemeral_vm": {
        "pv_storage": "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR",
        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
        "native_mcp_available": {
            "tunnel_requirement": "NOT_REQUIRED_NATIVE_MCP_AVAILABLE",
        },
        "host_tool_gap": {
            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
            "tunnel_setup_frequency": "ONCE_PER_EPHEMERAL_VM_INSTANCE",
            "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
            "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
        },
    },
    "desktop_container_surface_scope": {
        "supported_container_channels": [
            "CHATGPT_DESKTOP_STABLE_OR_CURRENT",
            "CHATGPT_DESKTOP_BETA",
        ],
        "active_surface": "CODEX",
        "chatgpt_chat_work_scope": "OUT_OF_SCOPE_DEFERRED",
        "authority_binding": ("EXACT_HOST_SESSION_PLUS_NATIVE_EVIDENCE_LANE_MCP_ROUTE"),
        "process_package_title_cwd_authority": False,
    },
}
EXPECTED_BEHAVIOR_OWNERSHIP = {
    "hooks": "LIFECYCLE_CAPTURE_AND_SEALED_EVENTS_ONLY",
    "skills": "NATIVE_PV_READS_AND_HOST_BEHAVIOR",
    "prompt_and_steer_native_reads": [
        "pv_status",
        "pv_task_backlog",
        "pv_query",
        "search",
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
    "chat_only_execution_change_allowed": False,
    "source_intake_may_dispatch_one_stable_idempotent_linked_steer": True,
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
    "stable_install_command": "scripts/codex_release/install_codex_stable.py",
    "stable_update_helper": "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",
    "install_completed_before_restart_helper": True,
    "restart_helper_installs_plugin": False,
    "stable_update_reopens_same_bound_host_app": False,
    "stable_update_requires_user_restart_after_terminal_response": True,
    "stable_update_rebinds_exact_task_via_native_binding": True,
    "release_authority_schema": (
        "evidence-lane.codex-git-ci-vercel-release-authority.v2"
    ),
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
    "stable_install_source": "EXACT_GIT_MAIN_COMMIT_PACKAGE_ONLY",
    "local_or_dirty_worktree_stable_install_allowed": False,
    "all_configured_commit_checks_required_before_stable_install": True,
    "one_stable_update_per_integration_bundle": True,
    "git_marketplace_name": "evidence-lane-github",
    "git_marketplace_display_name": "Main Git Plugin Version",
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
    "resource_uri": "ui://evidence-lane/governed-console-v6.html",
    "manifest_icon_fields": ["interface.composerIcon", "interface.logo"],
    "required_at_stage": True,
    "required_at_runtime_prewarm": True,
}

EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".ruff_cache",
        ".runtime",
        ".tox",
        ".venv",
        ".vercel",
        "_evidence_lane_rehearsal",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "migrated-command-skills",
        "node_modules",
    }
)
EXCLUDED_FILE_NAMES = frozenset(
    {
        ".DS_Store",
        ".coverage",
        "Thumbs.db",
    }
)
EXCLUDED_FILE_SUFFIXES = frozenset(
    {".log", ".map", ".pyc", ".pyo", ".tmp", ".tsbuildinfo"}
)
FORBIDDEN_3D_SUFFIXES = frozenset({".glb", ".gltf"})
DEPENDENCY_FILENAMES = frozenset(
    {
        ".mcp.json",
        "authorities/authority-surface-registry.v1.json",
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.torch-cpu.lock.txt",
        "requirements.lock.txt",
        "requirements.toolchain.lock.txt",
        "skills/evi-plan/SKILL.md",
        "env/authority-manifest.v1.json",
        "env/env_sqlite.sqlite",
        "hooks/EvidenceLaneHookHost.build.json",
        "hooks/EvidenceLaneHookHost.exe",
        "hooks/hooks.json",
        "hooks/logical-actions.json",
        "hooks/subhook_emit.py",
        "hooks/subhook_pipeline.py",
        "hooks/subhook_seal.py",
        "hooks/subhook_transport.py",
        "hooks/subhook_validate.py",
        "authorities/project_sectors/lane-surface-registry.v1.json",
        "manifests/executable-surface-registry.v1.json",
        "mcp/evidence_lane_mcp.py",
        "mcp/mcp-manifest.v1.json",
        "schemas/public-action-schemas.v001.json",
        "schemas/schema-manifest.v1.json",
        "sdk/evidence_lane_sdk.py",
        "sdk/env_uop/action-plane.v1.json",
        "sdk/env_uop/runtime.py",
        "sdk/internal/public-action-registry.v1.json",
        "sdk/internal/authority-surface-routing.v1.json",
        "sdk/internal/runtime.py",
        "sdk/internal/workflow-registry.v1.json",
        "sdk/routing/current-route-registry.v1.json",
        "sdk/routing/mcp-action-routing.v1.json",
        "sdk/routing/router.py",
        "sdk/sdk-manifest.v1.json",
        "src/evidence_lane_plugin/adaptive_delta_entry.py",
        "src/evidence_lane_plugin/authority_support.py",
        "src/evidence_lane_plugin/current_route_registry.py",
        "src/evidence_lane_plugin/internal_sdk.py",
        "src/evidence_lane_plugin/mcp_server.py",
        "src/evidence_lane_plugin/mode_governance.py",
        "src/evidence_lane_plugin/runtime-public-catalog.v1.json",
        "scripts/windows_tunnel/EvidenceLaneTunnelHost.build.json",
        "scripts/windows_tunnel/EvidenceLaneTunnelHost.exe",
        "uop/authority-manifest.v1.json",
        "uop/uop_sqlite.sqlite",
        "requirements.txt",
        "yarn.lock",
    }
)
REQUIRED_MEMBERS = frozenset(
    {
        ".codex-plugin/plugin.json",
        ".mcp.json",
        "COPYRIGHT.md",
        "LICENSE.md",
        "README.md",
        "THIRD_PARTY_NOTICES.md",
        "assets/evidence-lane-icon.png",
        "pyproject.toml",
        "requirements.torch-cpu.lock.txt",
        "requirements.lock.txt",
        "requirements.toolchain.lock.txt",
        "scripts/codex-release-channel.json",
        "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",
        "scripts/codex_release/accept_codex_stable.py",
        "scripts/codex_release/build_codex_exact_commit_package.py",
        "scripts/codex_release/install_codex_stable.py",
        "scripts/codex_release/seal_codex_git_ci_release_authority.py",
        "scripts/codex_release/seal_external_release_receipts.py",
        "authorities/authority-surface-registry.v1.json",
        "authorities/project_sectors/lane-surface-registry.v1.json",
        "manifests/executable-surface-registry.v1.json",
        "toolchains/search-tools.v1.json",
        "toolchains/native-tools.v1.json",
        "toolchains/tool-execution-routing.v1.json",
        "toolchains/TOOLCHAIN_EXECUTION_MATRIX.md",
        "scripts/codex_release/install_native_toolchain.py",
        "scripts/generate_toolchain_execution_matrix.py",
        "skills/evi-plan/SKILL.md",
        "skills/evi-plan/agents/openai.yaml",
        "tests/test_installed_package_surface_smoke.py",
        "schemas/schema-manifest.v1.json",
        "toolchains/bin/windows-x86_64/rg.exe",
        "toolchains/licenses/ripgrep-15.2.0/LICENSE-MIT",
        "toolchains/licenses/ripgrep-15.2.0/UNLICENSE",
    }
)
COHERENCE_REQUIRED_MEMBERS = frozenset(
    {
        "requirements.torch-cpu.lock.txt",
        "authorities/authority-surface-registry.v1.json",
        "skills/evi-plan/SKILL.md",
        "skills/evi-plan/agents/openai.yaml",
        "env/authority-manifest.v1.json",
        "env/env_sqlite.sqlite",
        "hooks/EvidenceLaneHookHost.build.json",
        "hooks/EvidenceLaneHookHost.exe",
        "hooks/hooks.json",
        "hooks/logical-actions.json",
        "hooks/subhook_emit.py",
        "hooks/subhook_pipeline.py",
        "hooks/subhook_seal.py",
        "hooks/subhook_transport.py",
        "hooks/subhook_validate.py",
        "authorities/project_sectors/lane-surface-registry.v1.json",
        "manifests/executable-surface-registry.v1.json",
        "mcp/evidence_lane_mcp.py",
        "mcp/mcp-manifest.v1.json",
        "schemas/public-action-schemas.v001.json",
        "schemas/schema-manifest.v1.json",
        "sdk/evidence_lane_sdk.py",
        "sdk/env_uop/action-plane.v1.json",
        "sdk/env_uop/runtime.py",
        "sdk/internal/public-action-registry.v1.json",
        "sdk/internal/authority-surface-routing.v1.json",
        "sdk/internal/runtime.py",
        "sdk/internal/workflow-registry.v1.json",
        "sdk/routing/current-route-registry.v1.json",
        "sdk/routing/mcp-action-routing.v1.json",
        "sdk/routing/router.py",
        "sdk/sdk-manifest.v1.json",
        "src/evidence_lane_plugin/adaptive_delta_entry.py",
        "src/evidence_lane_plugin/current_route_registry.py",
        "src/evidence_lane_plugin/internal_sdk.py",
        "src/evidence_lane_plugin/mcp_server.py",
        "src/evidence_lane_plugin/mode_governance.py",
        "src/evidence_lane_plugin/runtime-public-catalog.v1.json",
        "scripts/windows_tunnel/EvidenceLaneTunnelHost.build.json",
        "scripts/windows_tunnel/EvidenceLaneTunnelHost.exe",
        "uop/authority-manifest.v1.json",
        "uop/uop_sqlite.sqlite",
        "tests/test_installed_package_surface_smoke.py",
    }
)
SEPARATE_HOST_RELATIVE_FILES = frozenset({"release-channels.json"})
SEPARATE_HOST_FILE_SUFFIXES = ("-app-connection.json", "-app-submission.json")
SEPARATE_HOST_PREFIXES = ("evidence/",)
MAINTAINER_TEST_PREFIXES = ("tests/tools/",)
SYNTHETIC_ROOT = "manifests/package"
SYNTHETIC_BASE_REQUIRED_MEMBERS = frozenset(
    {
        "manifests/package/source-manifest.json",
        "manifests/package/skill-inventory.json",
        "manifests/package/package-surface-coherence.json",
        "manifests/package/exit-slip.json",
    }
)
SYNTHETIC_SYSTEMWIDE_AUDIT_MEMBER = "manifests/package/systemwide-route-audit.json"

SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openai_key", re.compile(rb"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}")),
    ("github_token", re.compile(rb"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("github_fine_grained_token", re.compile(rb"github_pat_[A-Za-z0-9_]{20,}")),
    ("google_api_key", re.compile(rb"AIza[0-9A-Za-z_-]{30,}")),
    ("slack_token", re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}")),
)


class PackageBoundaryError(RuntimeError):
    """Raised when source bytes violate the local-package boundary."""


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _is_environment_file(name: str) -> bool:
    lowered = name.casefold()
    return lowered == ".env" or (
        lowered.startswith(".env.") and lowered != ".env.example"
    )


def _excluded_file(path: Path) -> bool:
    name = path.name
    lowered = name.casefold()
    return (
        name in EXCLUDED_FILE_NAMES
        or path.suffix.casefold() in EXCLUDED_FILE_SUFFIXES
        or _is_environment_file(name)
        or (lowered.startswith(".codex-") and lowered != ".codex-plugin")
        or lowered.endswith(".zip")
    )


def _installed_test_paths(plugin_root: Path) -> frozenset[str]:
    policy_path = plugin_root / "tests" / "test-surface-policy.v1.json"
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageBoundaryError(
            "The installed-test surface policy is missing or invalid."
        ) from exc
    declared = policy.get("installed_executable_tests")
    if (
        policy.get("schema") != "evidence-lane.installed-test-surface-policy.v1"
        or policy.get("status") != "PASS"
        or not isinstance(declared, list)
        or not declared
        or policy.get("installed_verification_environment")
        != {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_ADDOPTS": "-p no:cacheprovider",
        }
        or policy.get("installed_verification_may_run_broad_regression") is not False
        or policy.get("post_verification_forbidden_cache_artifact_count") != 0
        or any(
            not isinstance(relative, str)
            or not relative.startswith("tests/")
            or not (plugin_root / relative).is_file()
            for relative in declared
        )
    ):
        raise PackageBoundaryError(
            "The installed-test surface policy does not name exact executable tests."
        )
    return frozenset(["tests/test-surface-policy.v1.json", *map(str, declared)])


def _release_paths(plugin_root: Path) -> list[Path]:
    paths: list[Path] = []
    installed_test_paths = _installed_test_paths(plugin_root)
    for raw_root, raw_directories, raw_files in os.walk(plugin_root, topdown=True):
        root = Path(raw_root)
        allowed_directories: list[str] = []
        for name in sorted(raw_directories):
            if name in EXCLUDED_DIRECTORY_NAMES or name.casefold().endswith(
                ".egg-info"
            ):
                continue
            candidate = root / name
            if candidate.is_symlink():
                raise PackageBoundaryError(
                    f"Symlinked directories are forbidden in the package: {candidate}"
                )
            allowed_directories.append(name)
        raw_directories[:] = allowed_directories

        for name in sorted(raw_files):
            candidate = root / name
            relative = candidate.relative_to(plugin_root).as_posix()
            if (
                relative in SEPARATE_HOST_RELATIVE_FILES
                or relative.endswith(SEPARATE_HOST_FILE_SUFFIXES)
                or relative.startswith(SEPARATE_HOST_PREFIXES)
                or relative.startswith(MAINTAINER_TEST_PREFIXES)
                or (
                    relative.startswith("tests/")
                    and relative not in installed_test_paths
                )
            ):
                continue
            if _excluded_file(candidate):
                continue
            if candidate.is_symlink():
                raise PackageBoundaryError(
                    f"Symlinked files are forbidden in the package: {candidate}"
                )
            if candidate.suffix.casefold() in FORBIDDEN_3D_SUFFIXES:
                raise PackageBoundaryError(
                    f"Generated 3D assets are forbidden in the package: {candidate}"
                )
            paths.append(candidate)
    return sorted(paths, key=lambda item: item.relative_to(plugin_root).as_posix())


def _scan_secret_bytes(relative: str, content: bytes) -> None:
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(content):
            raise PackageBoundaryError(
                f"High-confidence {label} material detected in {relative}."
            )


def _source_inventory(
    plugin_root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Path]]:
    records: list[dict[str, Any]] = []
    sources: dict[str, Path] = {}
    for path in _release_paths(plugin_root):
        relative = path.relative_to(plugin_root).as_posix()
        content = path.read_bytes()
        _scan_secret_bytes(relative, content)
        if path.name.casefold() in DEPENDENCY_FILENAMES and re.search(
            rb"(?i)\bmeshy(?:[-_/][A-Za-z0-9_.-]+)?\b", content
        ):
            raise PackageBoundaryError(
                f"Meshy is present in dependency or MCP configuration: {relative}"
            )
        records.append(
            {
                "path": relative,
                "bytes": len(content),
                "sha256": _sha256_bytes(content),
            }
        )
        sources[relative] = path
    return records, sources


def _search_toolchain_identity(
    plugin_root: Path,
    release_channels: dict[str, Any],
) -> dict[str, Any]:
    dependency_contract = dict(
        (release_channels.get("dependency_toolchains") or {}).get("search_v1") or {}
    )
    if dependency_contract != {
        "required": True,
        "scope": "ALL_GOVERNED_PROJECTS",
        "manifest": "toolchains/search-tools.v1.json",
        "package_local_tools": [
            "ripgrep@15.2.0/windows-x86_64",
        ],
        "authoritative_full_text_backend": "SQLITE_FTS5",
        "model_context_policy": "BOUNDED_QUERY_RESULTS_ONLY",
        "pv_package_loaded_into_model_context": False,
        "resolution_order": [
            "PACKAGE_LOCAL_VERIFIED_BINARY",
            "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY",
            "DETERMINISTIC_BUILTIN_FALLBACK",
        ],
        "fallbacks_required": True,
        "path_lookup_allowed": False,
        "auto_download_during_mcp_handshake": False,
    }:
        raise PackageBoundaryError("The governed search dependency contract drifted.")
    manifest_path = plugin_root / dependency_contract["manifest"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageBoundaryError(
            "The governed search dependency manifest is missing."
        ) from exc
    tools = manifest.get("tools")
    if (
        manifest.get("schema") != "evidence-lane.search-toolchain-manifest.v1"
        or manifest.get("version") != 1
        or manifest.get("scope") != "ALL_GOVERNED_PROJECTS"
        or manifest.get("resolution_order") != dependency_contract["resolution_order"]
        or manifest.get("auto_download_during_mcp_handshake") is not False
        or manifest.get("path_lookup_allowed") is not False
        or manifest.get("shell_execution_allowed") is not False
        or not isinstance(tools, list)
        or [row.get("tool_id") for row in tools if isinstance(row, dict)] != ["ripgrep"]
        or manifest.get("fts_authority")
        != {
            "backend": "SQLITE_FTS5",
            "query_mode": "BOUNDED_FTS5",
            "scope": "PLAN_LANE_CHATLINEAGE_AND_PROJECT_SECTORS",
            "pointer_and_locator_required": True,
            "model_context_policy": "BOUNDED_QUERY_RESULTS_ONLY",
            "pv_package_loaded_into_model_context": False,
            "fallback": "FAIL_CLOSED_WHEN_SQLITE_FTS5_UNAVAILABLE",
        }
    ):
        raise PackageBoundaryError("The governed search dependency manifest drifted.")
    expected_tools = {
        "ripgrep": {
            "version": "15.2.0",
            "role": "BOUNDED_LITERAL_CONTENT_AND_FILE_SEARCH",
            "license_spdx": "MIT OR Unlicense",
            "fallback_backend": "PYTHON_BOUNDED_LITERAL_SCAN",
        },
    }
    records: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise PackageBoundaryError(
                "A governed search dependency record is invalid."
            )
        expected = expected_tools[str(tool.get("tool_id") or "")]
        if any(tool.get(key) != value for key, value in expected.items()):
            raise PackageBoundaryError("A governed search dependency identity drifted.")
        package_binaries = tool.get("package_binaries")
        if not isinstance(package_binaries, dict) or set(package_binaries) != {
            "windows-x86_64"
        }:
            raise PackageBoundaryError("A governed search binary platform drifted.")
        binary_record = package_binaries["windows-x86_64"]
        if not isinstance(binary_record, dict):
            raise PackageBoundaryError("A governed search binary record is invalid.")
        binary = (plugin_root / str(binary_record.get("path") or "")).resolve()
        licenses = [
            (plugin_root / str(item)).resolve()
            for item in binary_record.get("licenses") or []
        ]
        try:
            binary.relative_to(plugin_root)
            for license_path in licenses:
                license_path.relative_to(plugin_root)
        except ValueError as exc:
            raise PackageBoundaryError(
                "A governed search dependency escaped the package."
            ) from exc
        if (
            not binary.is_file()
            or binary.stat().st_size != binary_record.get("size_bytes")
            or _sha256_file(binary) != binary_record.get("sha256")
            or not licenses
            or any(not item.is_file() for item in licenses)
        ):
            raise PackageBoundaryError("A governed search dependency hash drifted.")
        records.append(
            {
                "tool_id": tool["tool_id"],
                **expected,
                "platform_id": "windows-x86_64",
                "binary_sha256": binary_record["sha256"],
                "size_bytes": binary_record["size_bytes"],
                "license_sha256": sorted(_sha256_file(item) for item in licenses),
            }
        )
    body = {
        "schema": "evidence-lane.packaged-search-toolchain.v1",
        "status": "PASS",
        "scope": "ALL_GOVERNED_PROJECTS",
        "manifest_sha256": _sha256_file(manifest_path),
        "fts_authority": manifest["fts_authority"],
        "resolution_order": dependency_contract["resolution_order"],
        "records": records,
        "record_count": len(records),
        "path_lookup_allowed": False,
        "shell_execution_allowed": False,
        "auto_download_during_mcp_handshake": False,
        "deterministic_fallbacks_required": True,
        "raw_paths_included": False,
    }
    body["identity_sha256"] = _sha256_bytes(_json_bytes(body))
    return body


def _validate_sha1(label: str, value: str) -> str:
    normalized = value.strip().casefold()
    if re.fullmatch(r"[0-9a-f]{40}", normalized) is None:
        raise PackageBoundaryError(f"{label} must be an exact 40-character SHA-1.")
    return normalized


def _skill_inventory(plugin_root: Path) -> dict[str, Any]:
    skill_files = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if not skill_files:
        raise PackageBoundaryError("At least one governed skill is required.")
    rows = []
    for path in skill_files:
        relative = path.relative_to(plugin_root).as_posix()
        rows.append(
            {
                "name": path.parent.name,
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    return {
        "schema": f"{SCHEMA}.skill-inventory",
        "count": len(rows),
        "skills": rows,
    }


def _package_surface_coherence(plugin_root: Path) -> dict[str, Any]:
    """Prove every generated/runtime surface comes from the same source tree."""

    from generate_public_schema_catalog import build_catalog

    public_path = plugin_root / "schemas" / "public-action-schemas.v001.json"
    committed_public = json.loads(public_path.read_text(encoding="utf-8"))
    regenerated_public = build_catalog()
    if committed_public != regenerated_public:
        raise PackageBoundaryError(
            "The public MCP/schema catalog is stale relative to current source."
        )

    runtime_path = (
        plugin_root / "src" / "evidence_lane_plugin" / "runtime-public-catalog.v1.json"
    )
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    mcp_path = plugin_root / "src" / "evidence_lane_plugin" / "mcp_server.py"
    release_path = plugin_root / "scripts" / "codex-release-channel.json"
    expected_counts = {
        "tools": int(committed_public["tool_count"]),
        "read": int(committed_public["read_tool_count"]),
        "write": int(committed_public["write_tool_count"]),
        "skills": len(list((plugin_root / "skills").glob("*/SKILL.md"))),
    }
    if (
        runtime.get("schema") != "evidence-lane.runtime-public-catalog.v1"
        or {key: runtime.get(key) for key in expected_counts} != expected_counts
        or runtime.get("mcp_source_sha256") != _sha256_file(mcp_path)
        or runtime.get("release_channel_sha256") != _sha256_file(release_path)
        or len(committed_public.get("tools") or []) != expected_counts["tools"]
    ):
        raise PackageBoundaryError(
            "The runtime MCP catalog mixes stale and current source identities."
        )

    tool_matrix_path = plugin_root / "toolchains" / "tool-requirement-matrix.v1.json"
    tool_routing_path = plugin_root / "toolchains" / "tool-execution-routing.v1.json"
    tool_license_path = plugin_root / "toolchains" / "tool-license-inventory.v1.json"
    license_policy_path = plugin_root / "toolchains" / "license-policy.v1.json"
    tunnel_toolchain_path = (
        plugin_root / "toolchains" / "tunnel-runtime-toolchain.v1.json"
    )
    tool_matrix = json.loads(tool_matrix_path.read_text(encoding="utf-8"))
    tool_routing = json.loads(tool_routing_path.read_text(encoding="utf-8"))
    tool_licenses = json.loads(tool_license_path.read_text(encoding="utf-8"))
    license_policy = json.loads(license_policy_path.read_text(encoding="utf-8"))
    tunnel_toolchain = json.loads(tunnel_toolchain_path.read_text(encoding="utf-8"))
    tool_rows = list(tool_matrix.get("requirements") or [])
    routing_rows = list(tool_routing.get("rows") or [])
    license_rows = list(tool_licenses.get("rows") or [])
    tunnel_rows = list(tunnel_toolchain.get("requirements") or [])
    tool_names = {str(row.get("tool")) for row in tool_rows}
    if (
        len(tool_rows) != 95
        or tool_routing.get("status") != "PASS"
        or tool_routing.get("primary_and_fallback_order_explicit") is not True
        or len(routing_rows) != len(tool_rows)
        or {str(row.get("tool")) for row in routing_rows} != tool_names
        or tool_licenses.get("all_tool_requirements_classified") is not True
        or tool_licenses.get("all_tool_requirements_have_physical_license_records")
        is not True
        or len(license_rows) != len(tool_rows)
        or {str(row.get("tool")) for row in license_rows} != tool_names
        or int(tool_licenses.get("requirement_license_record_count") or 0)
        != len(tool_rows)
        or any(
            not (plugin_root / str(row.get("license_record") or "")).is_file()
            or _sha256_file(plugin_root / str(row.get("license_record") or ""))
            != row.get("license_record_sha256")
            for row in license_rows
        )
        or license_policy.get("status") != "PASS"
        or int(license_policy.get("requirement_license_record_count") or 0)
        != len(tool_rows)
        or license_policy.get("all_tool_requirements_have_physical_license_records")
        is not True
        or tunnel_toolchain.get("status") != "PASS"
        or tunnel_toolchain.get("primary_and_fallback_order_explicit") is not True
        or len(tunnel_rows) != len(tool_rows)
        or {str(row.get("tool")) for row in tunnel_rows} != tool_names
    ):
        raise PackageBoundaryError(
            "The 95-tool routing, license, or tunnel-prewarm surface is incomplete."
        )

    executable_registry_path = (
        plugin_root / "manifests" / "executable-surface-registry.v1.json"
    )
    executable_registry = json.loads(
        executable_registry_path.read_text(encoding="utf-8")
    )
    executable_registry_body = {
        key: value
        for key, value in executable_registry.items()
        if key != "receipt_sha256"
    }
    directory_rows = list(executable_registry.get("installed_directories") or [])
    member_rows = list(executable_registry.get("members") or [])
    listed_members = {str(row.get("path")): row for row in member_rows}
    actual_members: dict[str, dict[str, Any]] = {}
    excluded_parts = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    installed_test_paths = _installed_test_paths(plugin_root)
    for directory in [str(row.get("path")) for row in directory_rows]:
        root = plugin_root / directory
        for path in root.rglob("*"):
            if (
                not path.is_file()
                or path == executable_registry_path
                or set(path.relative_to(plugin_root).parts) & excluded_parts
                or path.relative_to(plugin_root).as_posix().startswith("tests/tools/")
                or (
                    path.relative_to(plugin_root).as_posix().startswith("tests/")
                    and path.relative_to(plugin_root).as_posix()
                    not in installed_test_paths
                )
                or path.suffix.casefold() in {".pyc", ".pyo"}
            ):
                continue
            relative = path.relative_to(plugin_root).as_posix()
            actual_members[relative] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
    for relative in executable_registry.get("installed_root_files") or []:
        path = plugin_root / str(relative)
        actual_members[str(relative)] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
    if (
        executable_registry.get("schema")
        != "evidence-lane.executable-package-surface-registry.v1"
        or executable_registry.get("status") != "PASS"
        or executable_registry.get("installed_directory_count") != len(directory_rows)
        or len({str(row.get("path")) for row in directory_rows}) != len(directory_rows)
        or executable_registry.get("member_count") != len(actual_members)
        or len(listed_members) != len(member_rows)
        or set(listed_members) != set(actual_members)
        or any(
            row.get("bytes") != actual_members[relative]["bytes"]
            or row.get("sha256") != actual_members[relative]["sha256"]
            for relative, row in listed_members.items()
        )
        or executable_registry.get("local_cache_or_output_included") is not False
        or executable_registry.get("historical_fallback_used") is not False
        or executable_registry.get("all_installed_members_hash_bound") is not True
        or executable_registry.get("receipt_sha256")
        != _sha256_bytes(_json_bytes(executable_registry_body))
    ):
        raise PackageBoundaryError(
            "The executable plugin tree registry is stale, incomplete, or shallow."
        )

    hooks_path = plugin_root / "hooks" / "hooks.json"
    logical_path = plugin_root / "hooks" / "logical-actions.json"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    logical = json.loads(logical_path.read_text(encoding="utf-8"))
    events = dict(hooks.get("hooks") or {})
    logical_actions = dict(logical.get("logicalActions") or {})
    handler_count = sum(
        len(group.get("hooks") or [])
        for groups in events.values()
        for group in groups
        if isinstance(group, dict)
    )
    logical_count = sum(len(rows) for rows in logical_actions.values())
    hook_control = dict(committed_public.get("hook_control") or {})
    required_subhandlers = [
        "subhook_validate.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_emit.py",
        "subhook_pipeline.py",
    ]
    if (
        len(events) != 11
        or set(events) != set(logical_actions)
        or handler_count != logical_count
        or handler_count != 44
        or hook_control.get("event_count") != len(events)
        or hook_control.get("current_handler_action_count") != handler_count
        or hook_control.get("current_logical_action_count") != logical_count
        or any(
            not (plugin_root / "hooks" / name).is_file()
            for name in required_subhandlers
        )
    ):
        raise PackageBoundaryError(
            "The hook event, handler, subhandler, or generated schema surface is stale."
        )

    compiled_hosts = []
    for receipt_relative in (
        "hooks/EvidenceLaneHookHost.build.json",
        "scripts/windows_tunnel/EvidenceLaneTunnelHost.build.json",
    ):
        receipt = json.loads(
            (plugin_root / receipt_relative).read_text(encoding="utf-8")
        )
        source = plugin_root / str(receipt.get("source") or "")
        output = plugin_root / str(receipt.get("output") or "")
        if (
            receipt.get("status") != "PASS"
            or not source.is_file()
            or not output.is_file()
            or receipt.get("source_sha256") != _sha256_file(source)
            or receipt.get("output_sha256") != _sha256_file(output)
        ):
            raise PackageBoundaryError(
                "A compiled Windows host is stale relative to its current source."
            )
        compiled_hosts.append(
            {
                "receipt": receipt_relative,
                "source_sha256": receipt["source_sha256"],
                "output_sha256": receipt["output_sha256"],
            }
        )

    flash_root = plugin_root
    flash_manifest_path = plugin_root / "env" / "SESSION_FLASH_MANIFEST.json"
    flash_manifest = json.loads(flash_manifest_path.read_text(encoding="utf-8"))
    member_rows = list(flash_manifest.get("members") or [])
    env_members = [
        row for row in member_rows if str(row.get("path")).startswith("env/")
    ]
    uop_members = [
        row for row in member_rows if str(row.get("path")).startswith("uop/")
    ]
    for row in member_rows:
        member = flash_root / str(row.get("path") or "")
        if (
            not member.is_file()
            or member.stat().st_size != row.get("bytes")
            or _sha256_file(member) != str(row.get("sha256") or "").upper()
        ):
            raise PackageBoundaryError(
                "The packaged ENV/UOP authority manifest contains stale members."
            )
    sqlite_tables: dict[str, list[str]] = {}
    for authority, relative, required_table in (
        ("ENV", "env/env_sqlite.sqlite", "operator_activation_run"),
        ("UOP", "uop/uop_sqlite.sqlite", "uop_delta_operator"),
    ):
        database = flash_root / relative
        connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
        try:
            integrity = [
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            ]
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
        finally:
            connection.close()
        if integrity != ["ok"] or required_table not in tables:
            raise PackageBoundaryError(
                f"The {authority} executable SQLite authority is incomplete."
            )
        sqlite_tables[authority] = tables
    if not env_members or not uop_members:
        raise PackageBoundaryError(
            "ENV and UOP must remain separately complete inside the Flash package."
        )
    published_authorities: dict[str, dict[str, Any]] = {}
    for authority, canonical_rows in (("env", env_members), ("uop", uop_members)):
        manifest_path = plugin_root / authority / "authority-manifest.v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        published_rows = list(manifest.get("members") or [])
        canonical_hashes = {
            str(row["path"]).split("/", 1)[1]: str(row["sha256"]).upper()
            for row in canonical_rows
        }
        published_hashes = {
            str(row["path"]).split("/", 1)[1]: str(row["sha256"])
            for row in published_rows
        }
        if (
            manifest.get("status") != "PASS"
            or manifest.get("canonical_packaged_authority") is not True
            or manifest.get("duplicate_authority_copy_present") is not False
            or canonical_hashes != published_hashes
            or any(
                not (plugin_root / str(row["path"])).is_file()
                or _sha256_file(plugin_root / str(row["path"])) != row["sha256"]
                for row in published_rows
            )
        ):
            raise PackageBoundaryError(
                f"The visible {authority.upper()} package folder is stale."
            )
        published_authorities[authority.upper()] = manifest

    from evidence_lane_plugin.lanes import (
        CANONICAL_LANE_IDS,
        LANE_REGISTRY,
        lane_artifact_contract,
        lane_schema_asset,
    )

    lane_registry_path = (
        plugin_root
        / "authorities"
        / "project_sectors"
        / "lane-surface-registry.v1.json"
    )
    installed_lane_registry = json.loads(lane_registry_path.read_text(encoding="utf-8"))
    installed_lane_rows = list(installed_lane_registry.get("lanes") or [])
    if (
        installed_lane_registry.get("schema")
        != "evidence-lane.installed-lane-surface-registry.v1"
        or installed_lane_registry.get("status") != "PASS"
        or installed_lane_registry.get("lane_count") != len(CANONICAL_LANE_IDS)
        or [str(row.get("lane_id")) for row in installed_lane_rows]
        != list(CANONICAL_LANE_IDS)
        or installed_lane_registry.get(
            "each_lane_has_separate_sqlite_schema_mmd_dot_pointer_refresh_and_tools"
        )
        is not True
        or installed_lane_registry.get("source_intake_routes_one_source_to_one_lane")
        is not True
        or installed_lane_registry.get("live_project_data_stored_in_installed_package")
        is not False
    ):
        raise PackageBoundaryError(
            "The installed eighteen-lane registry is stale or incomplete."
        )
    lane_surface_receipts: list[dict[str, Any]] = []
    sqlite_template_hashes: list[str] = []
    for row in installed_lane_rows:
        lane_id = str(row["lane_id"])
        lane = LANE_REGISTRY[lane_id]
        lane_root = plugin_root / "authorities" / "project_sectors" / lane_id
        manifest_path = lane_root / "manifest.v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_members = list(manifest.get("members") or [])
        listed = {str(member.get("path")): member for member in manifest_members}
        actual = {
            path.relative_to(plugin_root).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for path in lane_root.iterdir()
            if path.is_file() and path.name != "manifest.v1.json"
        }
        required_names = {
            "README.md",
            "builder-contract.schema.json",
            "builder.py",
            "dot-artifact.schema.json",
            "reader.py",
            "reader-contract.schema.json",
            "schema.sql",
            "lane-contract.v1.json",
            "lane-pointer.schema.json",
            "manifest.schema.json",
            "mmd-artifact.schema.json",
            "refresh-receipt.schema.json",
            "sqlite-artifact.schema.json",
            "tools.json",
            "tools.schema.json",
            lane.sqlite_filename,
            lane.mmd_filename,
            lane.dot_filename,
        }
        database = lane_root / lane.sqlite_filename
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
        )
        try:
            integrity = [
                str(item[0]) for item in connection.execute("PRAGMA integrity_check")
            ]
            tables = {
                str(item[0])
                for item in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            }
            identity = dict(
                connection.execute("SELECT key, value FROM lane_meta ORDER BY key")
            )
            source_count = int(
                connection.execute("SELECT COUNT(*) FROM source_registry").fetchone()[0]
            )
        finally:
            connection.close()
        schema_asset = lane_schema_asset(lane_id)
        artifact_contract = lane_artifact_contract(lane_id)
        contract = json.loads(
            (lane_root / "lane-contract.v1.json").read_text(encoding="utf-8")
        )
        if (
            row.get("path") != lane_root.relative_to(plugin_root).as_posix()
            or row.get("manifest_sha256") != _sha256_file(manifest_path)
            or row.get("sqlite_template_sha256") != _sha256_file(database)
            or manifest.get("schema") != "evidence-lane.installed-lane-surface.v1"
            or manifest.get("status") != "PASS"
            or manifest.get("lane_id") != lane_id
            or manifest.get("sqlite_integrity") != "ok"
            or manifest.get("separate_sqlite_authority") is not True
            or manifest.get("live_project_bytes_in_template") is not False
            or manifest.get("template_contains_identity_metadata_only") is not True
            or set(listed) != set(actual)
            or len(listed) != len(manifest_members)
            or any(
                member.get("bytes") != actual[relative]["bytes"]
                or member.get("sha256") != actual[relative]["sha256"]
                for relative, member in listed.items()
            )
            or required_names
            != {path.name for path in lane_root.iterdir() if path.is_file()}
            - {"manifest.v1.json"}
            or integrity != ["ok"]
            or not set(schema_asset["tables"]).issubset(tables)
            or identity
            != {
                "lane_id": lane_id,
                "schema_id": schema_asset["schema_id"],
                "template_role": "INSTALLED_EMPTY_SCHEMA_TEMPLATE",
            }
            or source_count != 0
            or contract.get("lane")
            != json.loads(_json_bytes(lane.as_dict()).decode("utf-8"))
            or contract.get("schema_asset") != schema_asset
            or contract.get("artifact_contract") != artifact_contract
        ):
            raise PackageBoundaryError(
                f"The installed lane surface is shallow or stale: {lane_id}."
            )
        sqlite_template_hashes.append(_sha256_file(database))
        lane_surface_receipts.append(
            {
                "lane_id": lane_id,
                "manifest_sha256": _sha256_file(manifest_path),
                "sqlite_template_sha256": _sha256_file(database),
                "table_count": len(tables),
            }
        )
    if len(set(sqlite_template_hashes)) != len(CANONICAL_LANE_IDS):
        raise PackageBoundaryError(
            "Each installed lane SQLite template must carry a distinct lane identity."
        )

    from evidence_lane_plugin.authority_support import AUTHORITY_SUPPORT_PROFILES

    authority_registry_path = (
        plugin_root / "authorities" / "authority-surface-registry.v1.json"
    )
    installed_authority_registry = json.loads(
        authority_registry_path.read_text(encoding="utf-8")
    )
    authority_rows = list(installed_authority_registry.get("authorities") or [])
    expected_persistent_authorities = tuple(AUTHORITY_SUPPORT_PROFILES)
    expected_non_sqlite_authorities = ("instructions",)
    if (
        installed_authority_registry.get("schema")
        != "evidence-lane.installed-non-sector-authority-registry.v1"
        or installed_authority_registry.get("status") != "PASS"
        or installed_authority_registry.get("authority_count")
        != len(expected_persistent_authorities) + len(expected_non_sqlite_authorities)
        or installed_authority_registry.get("sqlite_authority_count")
        != len(expected_persistent_authorities)
        or installed_authority_registry.get("non_sqlite_authority_count")
        != len(expected_non_sqlite_authorities)
        or tuple(installed_authority_registry.get("non_sqlite_authority_ids") or ())
        != expected_non_sqlite_authorities
        or tuple(str(row.get("authority_id")) for row in authority_rows)
        != (*expected_persistent_authorities, *expected_non_sqlite_authorities)
        or installed_authority_registry.get("authorities_are_outside_sector_count")
        is not True
        or installed_authority_registry.get(
            "sqlite_mmd_dot_tools_pointer_runtime_complete"
        )
        is not True
    ):
        raise PackageBoundaryError(
            "The installed non-sector authority registry is stale or incomplete."
        )
    authority_surface_receipts: list[dict[str, Any]] = []
    for row in authority_rows:
        authority_id = str(row["authority_id"])
        authority_root = plugin_root / "authorities" / authority_id
        manifest_path = authority_root / "manifest.v1.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        member_rows = list(manifest.get("members") or [])
        listed = {str(item.get("path")): item for item in member_rows}
        actual = {
            path.relative_to(plugin_root).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
            for path in authority_root.rglob("*")
            if path.is_file() and path != manifest_path
        }
        source_hashes = dict(manifest.get("canonical_source_hashes") or {})
        common_invalid = (
            row.get("path") != authority_root.relative_to(plugin_root).as_posix()
            or row.get("manifest_sha256") != _sha256_file(manifest_path)
            or manifest.get("status") != "PASS"
            or manifest.get("authority_id") != authority_id
            or manifest.get("authority_merged_with_sector_or_peer") is not False
            or set(listed) != set(actual)
            or len(listed) != len(member_rows)
            or any(
                item.get("bytes") != actual[relative]["bytes"]
                or item.get("sha256") != actual[relative]["sha256"]
                for relative, item in listed.items()
            )
            or any(
                not (plugin_root / relative).is_file()
                or _sha256_file(plugin_root / relative) != sha256
                for relative, sha256 in source_hashes.items()
            )
        )
        if common_invalid:
            raise PackageBoundaryError(
                f"The installed authority surface is stale: {authority_id}."
            )
        if authority_id in expected_persistent_authorities:
            profile = AUTHORITY_SUPPORT_PROFILES[authority_id]
            database = authority_root / Path(profile.database).name
            connection = sqlite3.connect(
                f"file:{database.resolve().as_posix()}?mode=ro&immutable=1", uri=True
            )
            try:
                integrity = [
                    str(item[0])
                    for item in connection.execute("PRAGMA integrity_check")
                ]
                foreign_keys = list(connection.execute("PRAGMA foreign_key_check"))
                table_count = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    ).fetchone()[0]
                )
            finally:
                connection.close()
            required_names = {
                "README.md",
                "build-refresh-contract.schema.json",
                "builder.py",
                "dot-artifact.schema.json",
                "manifest.schema.json",
                "mmd-artifact.schema.json",
                "reader-contract.schema.json",
                "reader.py",
                "refresh-receipt.schema.json",
                "runtime.py",
                "runtime-binding.schema.json",
                "schema.sql",
                "sqlite-schema.v1.json",
                "sqlite-artifact.schema.json",
                "pointer-contract.v1.json",
                "pointer-contract.schema.json",
                "tools.schema.json",
                Path(profile.database).name,
                Path(profile.mmd).name,
                Path(profile.dot).name,
                Path(profile.tools).name,
            }
            actual_names = {
                path.name
                for path in authority_root.iterdir()
                if path.is_file() and path.name != "manifest.v1.json"
            }
            if (
                manifest.get("schema") != "evidence-lane.installed-authority-surface.v1"
                or manifest.get("live_project_bytes_in_template") is not False
                or row.get("persistent_sqlite_authority") is not True
                or row.get("database_sha256") != _sha256_file(database)
                or manifest.get("database_sha256") != _sha256_file(database)
                or manifest.get("database_integrity") != "ok"
                or integrity != ["ok"]
                or foreign_keys
                or manifest.get("table_count") != table_count
                or not required_names.issubset(actual_names)
            ):
                raise PackageBoundaryError(
                    f"The installed SQLite authority surface is incomplete: {authority_id}."
                )
        else:
            required_names = {
                "README.md",
                "authority-contract.schema.json",
                "runtime.py",
                "runtime-binding.schema.json",
                "authority-contract.v1.json",
                "dot-artifact.schema.json",
                "manifest.schema.json",
                "mmd-artifact.schema.json",
                "pointer-contract.schema.json",
                f"{authority_id}.mmd",
                f"{authority_id}.dot",
                "tools.json",
                "tools.schema.json",
            }
            actual_names = {
                path.name
                for path in authority_root.iterdir()
                if path.is_file() and path.name != "manifest.v1.json"
            }
            if (
                manifest.get("schema")
                != "evidence-lane.installed-non-sqlite-authority-surface.v1"
                or manifest.get("persistent_sqlite_authority") is not False
                or row.get("persistent_sqlite_authority") is not False
                or row.get("database_sha256") is not None
                or not required_names.issubset(actual_names)
            ):
                raise PackageBoundaryError(
                    f"The installed non-SQLite authority surface is incomplete: {authority_id}."
                )
        authority_surface_receipts.append(
            {
                "authority_id": authority_id,
                "manifest_sha256": _sha256_file(manifest_path),
                "persistent_sqlite_authority": bool(
                    row.get("persistent_sqlite_authority")
                ),
                "linked_public_action_count": int(
                    row.get("linked_public_action_count") or 0
                ),
            }
        )

    sdk_required = [
        "src/evidence_lane_plugin/internal_sdk.py",
        "src/evidence_lane_plugin/current_route_registry.py",
        "src/evidence_lane_plugin/mode_governance.py",
        "src/evidence_lane_plugin/adaptive_delta_entry.py",
        "src/evidence_lane_plugin/mcp_server.py",
    ]
    sdk_planes = dict(committed_public.get("sdk_planes") or {})
    planes = list(sdk_planes.get("planes") or [])
    if (
        sdk_planes.get("status") != "PASS"
        or {str(row.get("plane_id")) for row in planes}
        != {"PUBLIC_ACTION_SDK", "ENV_UOP_AI_ACTION_PLANE"}
        or sdk_planes.get("planes_merged") is not False
        or any(not (plugin_root / path).is_file() for path in sdk_required)
    ):
        raise PackageBoundaryError(
            "The internal SDK, outer routing SDK, or ENV/UOP action plane is incomplete."
        )

    sdk_manifest_path = plugin_root / "sdk" / "sdk-manifest.v1.json"
    sdk_manifest = json.loads(sdk_manifest_path.read_text(encoding="utf-8"))
    mcp_manifest_path = plugin_root / "mcp" / "mcp-manifest.v1.json"
    visible_mcp = json.loads(mcp_manifest_path.read_text(encoding="utf-8"))
    visible_sdk_surfaces = dict(sdk_manifest.get("visible_surfaces") or {})
    internal_action_registry = json.loads(
        (plugin_root / "sdk" / "internal" / "public-action-registry.v1.json").read_text(
            encoding="utf-8"
        )
    )
    internal_workflow_registry = json.loads(
        (plugin_root / "sdk" / "internal" / "workflow-registry.v1.json").read_text(
            encoding="utf-8"
        )
    )
    internal_authority_routing = json.loads(
        (
            plugin_root / "sdk" / "internal" / "authority-surface-routing.v1.json"
        ).read_text(encoding="utf-8")
    )
    env_uop_action_plane = json.loads(
        (plugin_root / "sdk" / "env_uop" / "action-plane.v1.json").read_text(
            encoding="utf-8"
        )
    )
    route_projection = json.loads(
        (plugin_root / "sdk" / "routing" / "current-route-registry.v1.json").read_text(
            encoding="utf-8"
        )
    )
    mcp_action_routing = json.loads(
        (plugin_root / "sdk" / "routing" / "mcp-action-routing.v1.json").read_text(
            encoding="utf-8"
        )
    )
    public_tools = list(committed_public["tools"])
    public_tool_routes = [
        {
            "name": row["name"],
            "route_contract": row["route_contract"],
            "schema_sha256": row["schema_sha256"],
        }
        for row in public_tools
    ]
    if (
        sdk_manifest.get("status") != "PASS"
        or sdk_manifest.get("public_action_count") != expected_counts["tools"]
        or sdk_manifest.get("internal_and_outer_layers_distinct") is not True
        or sdk_manifest.get("env_uop_plane_distinct_from_public_actions") is not True
        or sdk_manifest.get("lane_surface_registry_sha256")
        != _sha256_file(lane_registry_path)
        or sdk_manifest.get("authority_surface_registry_sha256")
        != _sha256_file(authority_registry_path)
        or any(
            not (plugin_root / str(row["path"])).is_file()
            or _sha256_file(plugin_root / str(row["path"])) != row["sha256"]
            for row in dict(sdk_manifest.get("modules") or {}).values()
        )
        or len(visible_sdk_surfaces)
        != int(sdk_manifest.get("visible_surface_count") or -1)
        or len(visible_sdk_surfaces) < 40
        or sdk_manifest.get("workflow_projection_count") != expected_counts["skills"]
        or sdk_manifest.get("authority_reference_count") != 3
        or sdk_manifest.get("host_runtime_contract_count") != 1
        or any(
            not (plugin_root / relative).is_file()
            or _sha256_file(plugin_root / relative) != sha256
            for relative, sha256 in visible_sdk_surfaces.items()
        )
        or internal_action_registry.get("action_count") != expected_counts["tools"]
        or internal_action_registry.get("actions") != public_tools
        or internal_authority_routing.get("sector_lane_count")
        != len(CANONICAL_LANE_IDS)
        or internal_authority_routing.get("sector_lane_registry_sha256")
        != _sha256_file(lane_registry_path)
        or internal_authority_routing.get("non_sector_authority_count")
        != len(authority_surface_receipts)
        or internal_authority_routing.get("non_sector_authority_registry_sha256")
        != _sha256_file(authority_registry_path)
        or internal_authority_routing.get("sector_and_named_authorities_merged")
        is not False
        or internal_authority_routing.get("instructions_merged_with_project_memory")
        is not False
        or {
            key: value
            for key, value in internal_workflow_registry.items()
            if key != "receipt_sha256"
        }
        != {
            key: value
            for key, value in dict(
                committed_public["runtime_workflow_sdk_registry"]
            ).items()
            if key != "receipt_sha256"
        }
        or env_uop_action_plane.get("plane")
        != next(
            row
            for row in committed_public["sdk_planes"]["planes"]
            if row["plane_id"] == "ENV_UOP_AI_ACTION_PLANE"
        )
        or {
            key: value
            for key, value in route_projection.items()
            if key != "receipt_sha256"
        }
        != {
            key: value
            for key, value in dict(
                committed_public["current_implementation_registry"]
            ).items()
            if key != "receipt_sha256"
        }
        or mcp_action_routing.get("action_count") != expected_counts["tools"]
        or mcp_action_routing.get("actions") != public_tool_routes
        or visible_mcp.get("status") != "PASS"
        or visible_mcp.get("tool_count") != expected_counts["tools"]
        or visible_mcp.get("canonical_server_sha256") != _sha256_file(mcp_path)
        or visible_mcp.get("public_schema_catalog_sha256") != _sha256_file(public_path)
        or visible_mcp.get("sdk_manifest_sha256") != _sha256_file(sdk_manifest_path)
        or visible_mcp.get("sector_lane_registry_sha256")
        != _sha256_file(lane_registry_path)
        or visible_mcp.get("non_sector_authority_registry_sha256")
        != _sha256_file(
            plugin_root / "authorities" / "authority-surface-registry.v1.json"
        )
        or visible_mcp.get("separate_command_count") != 0
        or visible_mcp.get("legacy_command_surface_present") is not False
        or visible_mcp.get("hook_manifest_sha256") != _sha256_file(hooks_path)
        or (plugin_root / "commands").exists()
        or (plugin_root / ".codex-plugin" / "migrated-command-skills").exists()
        or not (plugin_root / "skills" / "evi-plan" / "SKILL.md").is_file()
    ):
        raise PackageBoundaryError(
            "The visible SDK/MCP skill projection is stale or a retired command route remains."
        )

    schema_manifest_path = plugin_root / "schemas" / "schema-manifest.v1.json"
    schema_manifest = json.loads(schema_manifest_path.read_text(encoding="utf-8"))
    schema_manifest_body = {
        key: value for key, value in schema_manifest.items() if key != "receipt_sha256"
    }
    schema_manifest_members = list(schema_manifest.get("members") or [])
    schema_manifest_records = {
        str(row.get("path")): row for row in schema_manifest_members
    }
    actual_schema_records = {
        path.relative_to(plugin_root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256_file(path),
        }
        for path in (plugin_root / "schemas").rglob("*")
        if path.is_file() and path != schema_manifest_path
    }
    action_schema_files = sorted((plugin_root / "schemas" / "actions").glob("*.json"))
    action_schema_rows = [
        json.loads(path.read_text(encoding="utf-8")) for path in action_schema_files
    ]
    public_tools_by_name = {str(row["name"]): row for row in public_tools}
    if (
        schema_manifest.get("schema") != "evidence-lane.complete-schema-surface.v1"
        or schema_manifest.get("status") != "PASS"
        or schema_manifest.get("generated_from_current_executable_source_only")
        is not True
        or schema_manifest.get("historical_schema_fallback_allowed") is not False
        or schema_manifest.get("schema_file_count") != len(actual_schema_records)
        or schema_manifest.get("public_action_schema_count") != expected_counts["tools"]
        or schema_manifest.get("project_sector_schema_surface_count")
        != len(CANONICAL_LANE_IDS)
        or schema_manifest.get("named_authority_schema_surface_count")
        != len(authority_surface_receipts)
        or len(schema_manifest_members) != len(schema_manifest_records)
        or set(schema_manifest_records) != set(actual_schema_records)
        or any(
            row.get("bytes") != actual_schema_records[relative]["bytes"]
            or row.get("sha256") != actual_schema_records[relative]["sha256"]
            for relative, row in schema_manifest_records.items()
        )
        or schema_manifest.get("canonical_public_catalog_sha256")
        != _sha256_file(public_path)
        or schema_manifest.get("receipt_sha256")
        != _sha256_bytes(_json_bytes(schema_manifest_body))
        or len(action_schema_files) != expected_counts["tools"]
        or {row.get("name") for row in action_schema_rows}
        != set(public_tools_by_name)
        or any(
            row.get("input_schema")
            != public_tools_by_name[str(row.get("name"))]["input_schema"]
            or row.get("output_schema")
            != public_tools_by_name[str(row.get("name"))]["output_schema"]
            or row.get("route_contract")
            != public_tools_by_name[str(row.get("name"))]["route_contract"]
            or row.get("canonical_schema_sha256")
            != public_tools_by_name[str(row.get("name"))]["schema_sha256"]
            for row in action_schema_rows
        )
    ):
        raise PackageBoundaryError(
            "The complete schema surface is shallow, stale, or not hash-bound to "
            "the current executable public catalog."
        )

    schema_files = sorted(
        path.relative_to(plugin_root).as_posix()
        for path in (plugin_root / "schemas").rglob("*")
        if path.is_file()
    )
    body = {
        "schema": "evidence-lane.package-surface-coherence.v1",
        "status": "PASS",
        "plugin_version": json.loads(
            (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["version"],
        "mcp": {
            **expected_counts,
            "source_sha256": _sha256_file(mcp_path),
            "public_schema_catalog_sha256": _sha256_file(public_path),
            "public_schema_action_count": len(committed_public["tools"]),
        },
        "skills": _skill_inventory(plugin_root),
        "skill_routing": {
            "evi_plan_native_skill": True,
            "separate_command_count": 0,
            "legacy_command_surface_present": False,
        },
        "hooks": {
            "event_count": len(events),
            "handler_action_count": handler_count,
            "logical_action_count": logical_count,
            "subhandlers": required_subhandlers,
            "compiled_hosts": compiled_hosts,
        },
        "schemas": {
            "count": len(schema_files),
            "members": schema_files,
            "catalog_sha256": committed_public["catalog_sha256"],
            "manifest_sha256": _sha256_file(schema_manifest_path),
            "public_action_schema_count": len(action_schema_files),
        },
        "env_uop": {
            "manifest_sha256": _sha256_file(flash_manifest_path),
            "env_member_count": len(env_members),
            "uop_member_count": len(uop_members),
            "env_sqlite_table_count": len(sqlite_tables["ENV"]),
            "uop_sqlite_table_count": len(sqlite_tables["UOP"]),
            "authorities_separate": True,
            "combined_projection_is_execution_cache_not_source_authority": True,
            "published_authorities": published_authorities,
        },
        "lanes": {
            "lane_count": len(lane_surface_receipts),
            "registry_sha256": _sha256_file(lane_registry_path),
            "separate_sqlite_authorities": True,
            "source_intake_single_lane_routing": True,
            "surfaces": lane_surface_receipts,
        },
        "authorities": {
            "authority_count": len(authority_surface_receipts),
            "sqlite_authority_count": len(expected_persistent_authorities),
            "non_sqlite_authority_count": len(expected_non_sqlite_authorities),
            "registry_sha256": _sha256_file(authority_registry_path),
            "surfaces": authority_surface_receipts,
            "merged_with_sector_lanes": False,
        },
        "sdk": {
            "planes": planes,
            "planes_merged": False,
            "required_modules": {
                path: _sha256_file(plugin_root / path) for path in sdk_required
            },
            "visible_sdk_manifest_sha256": _sha256_file(sdk_manifest_path),
            "visible_mcp_manifest_sha256": _sha256_file(mcp_manifest_path),
            "visible_surface_count": len(visible_sdk_surfaces),
        },
        "toolchains": {
            "requirement_count": len(tool_rows),
            "matrix_sha256": _sha256_file(tool_matrix_path),
            "execution_routing_sha256": _sha256_file(tool_routing_path),
            "license_inventory_sha256": _sha256_file(tool_license_path),
            "license_policy_sha256": _sha256_file(license_policy_path),
            "tunnel_toolchain_sha256": _sha256_file(tunnel_toolchain_path),
            "physical_license_record_count": len(license_rows),
            "primary_and_fallback_order_explicit": True,
            "conditional_execution_not_run_everything": True,
        },
        "mixed_version_members_allowed": False,
        "executable_surface_registry_sha256": _sha256_file(executable_registry_path),
    }
    return {**body, "receipt_sha256": _sha256_bytes(_json_bytes(body))}


def _lane_inventory(plugin_root: Path) -> dict[str, Any]:
    repository_root = (
        plugin_root.parents[1]
        if plugin_root.parent.name == "plugins"
        else plugin_root.parent
    )
    lane_root = (
        repository_root
        / "apps"
        / "evidence-lane-app"
        / "public"
        / "dummy-lane-packages"
    )
    lanes = sorted(path for path in lane_root.iterdir() if path.is_dir())
    if len(lanes) != EXPECTED_LANE_COUNT:
        raise PackageBoundaryError(
            f"Expected {EXPECTED_LANE_COUNT} public lane proofs, found {len(lanes)}."
        )
    rows: list[dict[str, Any]] = []
    for lane in lanes:
        files = sorted(path for path in lane.iterdir() if path.is_file())
        names = {path.name for path in files}
        required_shapes = {
            "dot": sum(name.endswith(".dot") for name in names),
            "mmd": sum(name.endswith(".mmd") for name in names),
            "png_8k": sum(name.endswith(".mmd.8k.png") for name in names),
            "receipt": sum(name == "refresh_receipt.json" for name in names),
            "sqlite": sum(name.endswith(".sqlite") for name in names),
            "svg_vector": sum(name.endswith(".mmd.vector.svg") for name in names),
        }
        if any(count != 1 for count in required_shapes.values()):
            raise PackageBoundaryError(
                f"Lane {lane.name} does not have one exact six-artifact proof set: "
                f"{required_shapes}"
            )
        rows.append(
            {
                "lane_id": lane.name,
                "artifact_count": len(files),
                "artifacts": [
                    path.relative_to(plugin_root).as_posix() for path in files
                ],
            }
        )
    return {
        "schema": f"{SCHEMA}.lane-bundle-inventory",
        "lane_count": len(rows),
        "lanes": rows,
    }


def _write_zip_member(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100600 << 16
    info.flag_bits |= 0x800
    archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _verify_archive(path: Path, expected: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if names != sorted(expected):
            raise PackageBoundaryError(
                "Archive members are not exact and lexically sorted."
            )
        if len(names) != len(set(names)):
            raise PackageBoundaryError("Archive contains duplicate members.")
        for info in infos:
            member = info.filename
            pure_parts = Path(member).parts
            if member.startswith(("/", "\\")) or ".." in pure_parts or "\\" in member:
                raise PackageBoundaryError(f"Unsafe archive member: {member}")
            if info.date_time != FIXED_ZIP_TIME:
                raise PackageBoundaryError(f"Non-deterministic timestamp on {member}.")
            if ((info.external_attr >> 16) & 0o777) != 0o600:
                raise PackageBoundaryError(f"Unsafe archive mode on {member}.")
            if _sha256_bytes(archive.read(info)) != expected[member]:
                raise PackageBoundaryError(f"Archive member hash mismatch: {member}")


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise PackageBoundaryError(
                f"Refusing to overwrite different evidence: {path}"
            )
        return
    path.write_bytes(content)


def _load_systemwide_route_audit(
    path: Path | None,
) -> tuple[dict[str, Any] | None, bytes | None]:
    if path is None:
        return None, None
    exact = path.resolve()
    try:
        content = exact.read_bytes()
        receipt = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageBoundaryError(
            "The system-wide route audit receipt is invalid."
        ) from exc
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != "evidence-lane.systemwide-route-audit.v1"
        or receipt.get("status") != "PASS"
        or receipt.get("accepted_archive_queried") is not False
        or receipt.get("candidate_created_or_cleared") is not False
        or receipt.get("pointer_moved") is not False
        or receipt.get("git_index_mutated") is not False
        or receipt.get("git_ref_mutated") is not False
        or (receipt.get("consumer_parity") or {}).get("status") != "PASS"
        or (receipt.get("obsolete_route_purge") or {}).get("status") != "PASS"
        or (receipt.get("plan_supersession") or {}).get("status") != "PASS"
        or (receipt.get("systemwide_regression") or {}).get("status") != "PASS"
        or (receipt.get("skill_current_route_audit") or {}).get("status") != "PASS"
    ):
        raise PackageBoundaryError(
            "The package requires a passing, pointer-neutral system-wide route audit."
        )
    compact = {
        "schema": receipt["schema"],
        "status": receipt["status"],
        "active_row": receipt["active_row"],
        "receipt_sha256": receipt["receipt_sha256"],
        "file_sha256": _sha256_bytes(content),
        "plan_rows_sha256": receipt["plan_supersession"]["rows_sha256"],
        "current_registry_sha256": receipt["current_registry"]["registry_sha256"],
        "public_tool_count": receipt["current_registry"]["public_tool_count"],
        "obsolete_public_tools": receipt["current_registry"]["obsolete_public_tools"],
        "consumer_parity_status": receipt["consumer_parity"]["status"],
        "obsolete_route_purge_status": receipt["obsolete_route_purge"]["status"],
        "systemwide_regression_status": receipt["systemwide_regression"]["status"],
        "systemwide_regression_file_sha256": receipt["systemwide_regression"][
            "file_sha256"
        ],
        "skill_current_route_audit_status": receipt["skill_current_route_audit"][
            "status"
        ],
        "skill_current_route_audit_sha256": receipt["skill_current_route_audit"][
            "receipt_sha256"
        ],
        "accepted_archive_queried": False,
        "candidate_created_or_cleared": False,
        "pointer_moved": False,
    }
    return compact, content


def build_rehearsal(
    *,
    plugin_root: Path,
    output_dir: Path,
    base_commit: str,
    base_tree: str,
    expected_version: str,
    package_version: str | None = None,
    systemwide_route_audit_receipt: Path | None = None,
    surface_coherence_required: bool = True,
) -> dict[str, Any]:
    plugin_root = plugin_root.resolve()
    output_dir = output_dir.resolve()
    base_commit = _validate_sha1("base_commit", base_commit)
    base_tree = _validate_sha1("base_tree", base_tree)
    package_version = package_version or expected_version
    if (
        re.fullmatch(r"\d+\.\d+\.\d+\+codex\.[0-9A-Za-z.-]+", package_version) is None
        or package_version.split("+", 1)[0] != expected_version.split("+", 1)[0]
    ):
        raise PackageBoundaryError(
            "The local package version must be a fresh Codex build on the same release line."
        )
    if not plugin_root.is_dir():
        raise PackageBoundaryError(f"Plugin root does not exist: {plugin_root}")

    plugin_manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    plugin_manifest = json.loads(plugin_manifest_path.read_text(encoding="utf-8"))
    if plugin_manifest.get("version") != expected_version:
        raise PackageBoundaryError(
            "Plugin version mismatch: "
            f"expected {expected_version}, got {plugin_manifest.get('version')!r}."
        )
    if plugin_manifest.get("mcpServers") != "./.mcp.json":
        raise PackageBoundaryError(
            "The Codex install manifest must declare only the package-local native MCP."
        )
    if "apps" in plugin_manifest or (plugin_root / ".app.json").exists():
        raise PackageBoundaryError(
            "Codex and registered-app delivery must remain separate; .app.json is forbidden."
        )
    mcp_manifest = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    if set(mcp_manifest.get("mcpServers", {})) != {"evidence-lane"}:
        raise PackageBoundaryError(
            "The Codex package must declare exactly one native evidence-lane server."
        )
    release_channels = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text(
            encoding="utf-8"
        )
    )
    stable = release_channels.get("stable", {})
    derived_skill_count = len(list((plugin_root / "skills").glob("*/SKILL.md")))
    local_testing = release_channels.get("local_testing", {})
    live_slots = release_channels.get("live_slot_policy", {})
    behavior_ownership = release_channels.get("behavior_ownership", {})
    stable_activation_gate = release_channels.get("stable_activation_gate", {})
    brand_identity = release_channels.get("brand_identity", {})
    brand_icon = plugin_root / str(brand_identity.get("icon_path") or "")
    promotion = release_channels.get("promotion_gate", {})
    remote_git_policy = release_channels.get("remote_git_policy", {})
    if (
        release_channels.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != expected_version.split("+", 1)[0]
        or stable.get("slot_role") != "main-git-release"
        or stable.get("codex_marketplace_slot") != "evidence-lane-github"
        or stable.get("marketplace_display_name") != "Main Git Plugin Version"
        or stable.get("install_source") != "GIT_MAIN_EXACT_COMMIT_AFTER_GOVERNED_MERGE"
        or stable.get("stable_selector_is_persistent") is not True
        or stable.get("stable_updates_reinstall_in_place") is not True
        or stable.get("build_identity_is_receipt_not_selector") is not True
        or stable.get("native_server_identity") != "evidence-lane"
        or (
            stable.get("native_tool_count"),
            stable.get("native_read_tool_count"),
            stable.get("native_write_tool_count"),
            stable.get("skill_count"),
        )
        != (91, 30, 61, derived_skill_count)
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or local_testing.get("release_line") != expected_version.split("+", 1)[0]
        or local_testing.get("slot_role") != "versioned-local-testing"
        or local_testing.get("codex_marketplace_slot")
        != "evidence-lane-v300-testing-new"
        or local_testing.get("marketplace_display_name") != "Local Testing Slot"
        or local_testing.get("same_marketplace_selector_reused") is not True
        or local_testing.get("fresh_package_version_per_local_build") is not True
        or local_testing.get("helper_installs_plugin") is not False
        or live_slots.get("exact_slot_count") != 2
        or live_slots.get("allowed_slots")
        != [
            "main-git-release",
            "versioned-local-testing",
        ]
        or live_slots.get("allowed_marketplaces")
        != [
            "evidence-lane-github",
            "evidence-lane-v300-testing-new",
        ]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 2
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or live_slots.get("obsolete_marketplace_registrations_must_be_absent")
        is not True
        or behavior_ownership != EXPECTED_BEHAVIOR_OWNERSHIP
        or stable_activation_gate != EXPECTED_STABLE_ACTIVATION_GATE
        or brand_identity != EXPECTED_BRAND_IDENTITY
        or plugin_manifest.get("interface", {}).get("displayName")
        != brand_identity.get("display_name")
        or plugin_manifest.get("interface", {}).get("composerIcon")
        != "./assets/evidence-lane-icon.png"
        or plugin_manifest.get("interface", {}).get("logo")
        != "./assets/evidence-lane-icon.png"
        or not brand_icon.is_file()
        or _sha256_file(brand_icon) != brand_identity.get("icon_sha256")
        or promotion.get("explicit_six_way_hil_required") is not True
        or promotion.get("fail_closed_on_version_mismatch") is not True
        or release_channels.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or remote_git_policy.get("effective_release")
        != expected_version.split("+", 1)[0]
        or remote_git_policy.get("per_push_confirmation_token_required") is not False
        or remote_git_policy.get("automatic_push_scope")
        != "GITHUB_APP_GOVERNED_FEATURE_BRANCH_THEN_EXACT_MAIN_MERGE"
        or remote_git_policy.get("host_managed_credentials_only") is not True
        or remote_git_policy.get("main_push_allowed") is not False
        or remote_git_policy.get("merge_allowed") is not True
        or remote_git_policy.get("pull_request_acceptance_allowed") is not True
        or remote_git_policy.get("force_push_allowed") is not False
        or release_channels.get("delivery_boundary", {}).get(
            "external_app_artifacts_packaged_with_codex"
        )
        is not False
        or release_channels.get("delivery_boundary", {}).get(
            "remote_website_artifacts_packaged_with_codex"
        )
        is not False
    ):
        raise PackageBoundaryError(
            "The two-slot Git-main/local-testing contract drifted."
        )

    search_toolchain = _search_toolchain_identity(plugin_root, release_channels)
    route_audit, route_audit_bytes = _load_systemwide_route_audit(
        systemwide_route_audit_receipt
    )
    source_records, source_paths = _source_inventory(plugin_root)
    surface_coherence = (
        _package_surface_coherence(plugin_root)
        if surface_coherence_required
        else {
            "schema": "evidence-lane.package-surface-coherence.v1",
            "status": "NOT_RUN_MINIMAL_TEST_FIXTURE",
            "production_package_allowed": False,
        }
    )
    source_overrides: dict[str, bytes] = {}
    if package_version != expected_version:
        packaged_manifest = dict(plugin_manifest)
        packaged_manifest["version"] = package_version
        packaged_manifest_bytes = _json_bytes(packaged_manifest)
        manifest_member = ".codex-plugin/plugin.json"
        source_overrides[manifest_member] = packaged_manifest_bytes
        for record in source_records:
            if record["path"] == manifest_member:
                record["bytes"] = len(packaged_manifest_bytes)
                record["sha256"] = _sha256_bytes(packaged_manifest_bytes)
                break
        else:
            raise PackageBoundaryError("The package-local plugin manifest is missing.")
    source_names = {record["path"] for record in source_records}
    required_members = (
        REQUIRED_MEMBERS
        if surface_coherence_required
        else REQUIRED_MEMBERS - COHERENCE_REQUIRED_MEMBERS
    )
    missing = sorted(required_members - source_names)
    if missing:
        raise PackageBoundaryError(f"Required package members are missing: {missing}")

    source_manifest_sha = _sha256_bytes(_json_bytes(source_records))
    skill_inventory = _skill_inventory(plugin_root)
    source_manifest = {
        "schema": f"{SCHEMA}.source-manifest",
        "boundary": BOUNDARY,
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "git_invoked": False,
        "base_anchor": {"commit": base_commit, "tree": base_tree},
        "working_source_identity": {
            "algorithm": "SHA256_CANONICAL_MEMBER_RECORDS_V1",
            "sha256": source_manifest_sha,
            "member_count": len(source_records),
            "total_bytes": sum(record["bytes"] for record in source_records),
        },
        "search_toolchain": search_toolchain,
        "exclusion_policy": {
            "directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
            "directory_suffixes": [".egg-info"],
            "file_suffixes": sorted(EXCLUDED_FILE_SUFFIXES),
            "environment_files": "ALL_EXCEPT_DOT_ENV_EXAMPLE",
            "local_codex_files": "EXCLUDED",
            "zip_files": "EXCLUDED",
            "separate_host_files": sorted(SEPARATE_HOST_RELATIVE_FILES),
            "separate_host_file_suffixes": list(SEPARATE_HOST_FILE_SUFFIXES),
            "separate_host_prefixes": list(SEPARATE_HOST_PREFIXES),
            "maintainer_test_prefixes": list(MAINTAINER_TEST_PREFIXES),
        },
        "members": source_records,
    }
    synthetic: dict[str, bytes] = {
        f"{SYNTHETIC_ROOT}/source-manifest.json": _json_bytes(source_manifest),
        f"{SYNTHETIC_ROOT}/skill-inventory.json": _json_bytes(skill_inventory),
        f"{SYNTHETIC_ROOT}/package-surface-coherence.json": _json_bytes(
            surface_coherence
        ),
    }
    if route_audit_bytes is not None:
        synthetic[f"{SYNTHETIC_ROOT}/systemwide-route-audit.json"] = route_audit_bytes
    synthetic_hashes = {
        name: _sha256_bytes(content) for name, content in sorted(synthetic.items())
    }
    exit_slip = {
        "schema": f"{SCHEMA}.exit-slip",
        "boundary": BOUNDARY,
        "status": "LOCAL_REHEARSAL_VERIFIED_NOT_A_GOVERNED_CANDIDATE",
        "version": package_version,
        "source_version": expected_version,
        "fresh_local_package_version": package_version != expected_version,
        "base_anchor": {"commit": base_commit, "tree": base_tree},
        "working_source_manifest_sha256": source_manifest_sha,
        "skill_count": skill_inventory["count"],
        "package_surface_coherence": surface_coherence,
        "canonical_lane_count": EXPECTED_LANE_COUNT,
        "search_toolchain": search_toolchain,
        "systemwide_route_audit": route_audit,
        "synthetic_metadata_sha256": synthetic_hashes,
        "negative_proofs": {
            "cache_or_runtime_members": 0,
            "generated_glb_or_gltf_members": 0,
            "governed_candidate_created": False,
            "git_invoked": False,
            "high_confidence_secrets": 0,
            "meshy_dependency_or_mcp_binding": 0,
            "pointer_movement": False,
        },
        "next_lifecycle_boundary": (
            "A later authorized governed Refresh must bind the then-final committed "
            "source; this rehearsal cannot be accepted or fused."
        ),
    }
    synthetic[f"{SYNTHETIC_ROOT}/exit-slip.json"] = _json_bytes(exit_slip)

    all_names = sorted([*source_paths, *synthetic])
    required_synthetic = set(SYNTHETIC_BASE_REQUIRED_MEMBERS)
    if surface_coherence_required:
        required_synthetic.add(SYNTHETIC_SYSTEMWIDE_AUDIT_MEMBER)
    if not required_synthetic.issubset(synthetic):
        raise PackageBoundaryError(
            "The canonical installed package-proof manifest set is incomplete."
        )
    if any(name.startswith("_evidence_lane_rehearsal/") for name in all_names):
        raise PackageBoundaryError(
            "Local rehearsal material must never enter the sealed archive."
        )
    expected_hashes = {record["path"]: record["sha256"] for record in source_records}
    expected_hashes.update(
        {name: _sha256_bytes(content) for name, content in synthetic.items()}
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    descriptor = _sha256_bytes(
        _json_bytes(
            {
                "source_manifest_sha256": source_manifest_sha,
                "synthetic_member_sha256": {
                    name: expected_hashes[name] for name in sorted(synthetic)
                },
            }
        )
    )[:16]
    archive_name = f"evidence-lane-{package_version}-local-rehearsal-{descriptor}.zip"
    final_archive = output_dir / archive_name
    handle, raw_temporary = tempfile.mkstemp(
        prefix=".row181-package-", suffix=".tmp", dir=output_dir
    )
    os.close(handle)
    temporary = Path(raw_temporary)
    try:
        with zipfile.ZipFile(temporary, "w", allowZip64=True) as archive:
            for name in all_names:
                if name in source_overrides:
                    content = source_overrides[name]
                elif name in synthetic:
                    content = synthetic[name]
                else:
                    content = source_paths[name].read_bytes()
                    if _sha256_bytes(content) != expected_hashes[name]:
                        raise PackageBoundaryError(
                            f"Source changed while the package was being built: {name}"
                        )
                _write_zip_member(archive, name, content)
        _verify_archive(temporary, expected_hashes)
        archive_sha = _sha256_file(temporary)
        if final_archive.exists():
            if _sha256_file(final_archive) != archive_sha:
                raise PackageBoundaryError(
                    f"Refusing to overwrite different archive: {final_archive}"
                )
        else:
            temporary.replace(final_archive)
    finally:
        temporary.unlink(missing_ok=True)

    receipt = {
        "schema": f"{SCHEMA}.receipt",
        "boundary": BOUNDARY,
        "status": "PASS",
        "archive": {
            "filename": archive_name,
            "bytes": final_archive.stat().st_size,
            "sha256": _sha256_file(final_archive),
            "member_count": len(all_names),
        },
        "base_anchor": {"commit": base_commit, "tree": base_tree},
        "working_source_manifest_sha256": source_manifest_sha,
        "package_surface_coherence": surface_coherence,
        "source_version": expected_version,
        "package_version": package_version,
        "fresh_local_package_version": package_version != expected_version,
        "source_member_count": len(source_records),
        "skill_count": skill_inventory["count"],
        "canonical_lane_count": EXPECTED_LANE_COUNT,
        "search_toolchain": search_toolchain,
        "systemwide_route_audit": route_audit,
        "exclusion_policy": source_manifest["exclusion_policy"],
        "governed_candidate_created": False,
        "git_invoked": False,
        "accepted_pointer_moved": False,
        "next_lifecycle_boundary": exit_slip["next_lifecycle_boundary"],
    }
    receipt_bytes = _json_bytes(receipt)
    receipt_name = f"LOCAL_PACKAGE_REHEARSAL_{receipt['archive']['sha256'][:16]}.json"
    receipt_path = output_dir / receipt_name
    _write_immutable(receipt_path, receipt_bytes)
    return {**receipt, "receipt_path": str(receipt_path)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--base-tree", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument(
        "--package-version",
        help=(
            "Fresh package-local version for reinstalling the same mutable testing "
            "marketplace without reusing an installed cache path."
        ),
    )
    parser.add_argument(
        "--systemwide-route-audit-receipt",
        type=Path,
        help=(
            "Optional passing Plan-history/current-route audit sealed into the local "
            "package; R265 local installation requires this bound receipt."
        ),
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    receipt = build_rehearsal(
        plugin_root=args.plugin_root,
        output_dir=args.output_dir,
        base_commit=args.base_commit,
        base_tree=args.base_tree,
        expected_version=args.expected_version,
        package_version=args.package_version,
        systemwide_route_audit_receipt=args.systemwide_route_audit_receipt,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
