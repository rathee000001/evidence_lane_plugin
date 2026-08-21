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
import tempfile
import zipfile
from pathlib import Path
from typing import Any

SCHEMA = "evidence-lane.non-lifecycle-local-package-rehearsal.v1"
BOUNDARY = "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL"
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
EXPECTED_SKILL_COUNT = 17
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
        "authority_binding": (
            "EXACT_HOST_SESSION_PLUS_NATIVE_EVIDENCE_LANE_MCP_ROUTE"
        ),
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
    "stable_install_command": "scripts/codex_release/install_codex_stable.py",
    "stable_update_helper": "scripts/codex_release/Restart-EvidenceLaneCodex.ps1",
    "install_completed_before_restart_helper": True,
    "restart_helper_installs_plugin": False,
    "stable_update_reopens_same_bound_host_app": True,
    "stable_update_rebinds_general_goal_recovery": True,
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
    "stable_install_source": "EXACT_GIT_COMMIT_PACKAGE_ONLY",
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
        "tests",
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
        "package-lock.json",
        "package.json",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "requirements.lock.txt",
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
        "requirements.lock.txt",
        "scripts/codex-release-channel.json",
        "scripts/codex_release/Restart-EvidenceLaneCodex.ps1",
        "scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1",
        "scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1",
        "scripts/codex_release/Update-EvidenceLaneCodexStableAndResume.ps1",
        "scripts/codex_release/accept_codex_stable.py",
        "scripts/codex_release/build_codex_exact_commit_package.py",
        "scripts/codex_release/install_codex_stable.py",
        "scripts/codex_release/seal_codex_git_ci_release_authority.py",
        "scripts/codex_release/seal_external_release_receipts.py",
        "toolchains/search-tools.v1.json",
        "toolchains/bin/windows-x86_64/rg.exe",
        "toolchains/licenses/ripgrep-15.2.0/LICENSE-MIT",
        "toolchains/licenses/ripgrep-15.2.0/UNLICENSE",
    }
)
SEPARATE_HOST_RELATIVE_FILES = frozenset({"release-channels.json"})
SEPARATE_HOST_FILE_SUFFIXES = ("-app-connection.json", "-app-submission.json")
SEPARATE_HOST_PREFIXES = ("evidence/", "remote_adapter/")
SYNTHETIC_ROOT = "_evidence_lane_rehearsal"

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


def _release_paths(plugin_root: Path) -> list[Path]:
    paths: list[Path] = []
    for raw_root, raw_directories, raw_files in os.walk(plugin_root, topdown=True):
        root = Path(raw_root)
        allowed_directories: list[str] = []
        for name in sorted(raw_directories):
            if name in EXCLUDED_DIRECTORY_NAMES or name.casefold().endswith(".egg-info"):
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


def _source_inventory(plugin_root: Path) -> tuple[list[dict[str, Any]], dict[str, Path]]:
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
        or manifest.get("resolution_order")
        != dependency_contract["resolution_order"]
        or manifest.get("auto_download_during_mcp_handshake") is not False
        or manifest.get("path_lookup_allowed") is not False
        or manifest.get("shell_execution_allowed") is not False
        or not isinstance(tools, list)
        or [row.get("tool_id") for row in tools if isinstance(row, dict)]
        != ["ripgrep"]
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
            raise PackageBoundaryError("A governed search dependency record is invalid.")
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
    if len(skill_files) != EXPECTED_SKILL_COUNT:
        raise PackageBoundaryError(
            f"Expected {EXPECTED_SKILL_COUNT} skills, found {len(skill_files)}."
        )
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


def _lane_inventory(plugin_root: Path) -> dict[str, Any]:
    lane_root = plugin_root / "remote_adapter" / "public" / "dummy-lane-packages"
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
                "artifacts": [path.relative_to(plugin_root).as_posix() for path in files],
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
            raise PackageBoundaryError("Archive members are not exact and lexically sorted.")
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
            raise PackageBoundaryError(f"Refusing to overwrite different evidence: {path}")
        return
    path.write_bytes(content)


def build_rehearsal(
    *,
    plugin_root: Path,
    output_dir: Path,
    base_commit: str,
    base_tree: str,
    expected_version: str,
    package_version: str | None = None,
) -> dict[str, Any]:
    plugin_root = plugin_root.resolve()
    output_dir = output_dir.resolve()
    base_commit = _validate_sha1("base_commit", base_commit)
    base_tree = _validate_sha1("base_tree", base_tree)
    package_version = package_version or expected_version
    if (
        re.fullmatch(r"\d+\.\d+\.\d+\+codex\.[0-9A-Za-z.-]+", package_version)
        is None
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
    mcp_manifest = json.loads(
        (plugin_root / ".mcp.json").read_text(encoding="utf-8")
    )
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
    branch_recovery = release_channels.get("branch_recovery", {})
    local_testing = release_channels.get("local_testing", {})
    live_slots = release_channels.get("live_slot_policy", {})
    failover = release_channels.get("failover_operator", {})
    goal_recovery = release_channels.get("goal_recovery", {})
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
        or stable.get("install_source") != "GIT_EXACT_COMMIT"
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
        != (88, 27, 61, EXPECTED_SKILL_COUNT)
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or branch_recovery.get("release") != expected_version.split("+", 1)[0]
        or branch_recovery.get("slot_role") != "branch-commit-recovery"
        or branch_recovery.get("codex_marketplace_slot")
        != "evidence-lane-v300-stable-recovery"
        or branch_recovery.get("marketplace_display_name")
        != "Branch Commit Git Recovery"
        or branch_recovery.get("byte_frozen_between_branch_checkpoints") is not True
        or branch_recovery.get("must_not_follow_uncommitted_local_bytes") is not True
        or local_testing.get("release_line") != expected_version.split("+", 1)[0]
        or local_testing.get("slot_role") != "mutable-local-testing"
        or local_testing.get("codex_marketplace_slot")
        != "evidence-lane-v300-testing-new"
        or local_testing.get("marketplace_display_name") != "Local Testing Slot"
        or local_testing.get("same_marketplace_selector_reused") is not True
        or local_testing.get("fresh_package_version_per_local_build") is not True
        or local_testing.get("helper_installs_plugin") is not False
        or live_slots.get("exact_slot_count") != 3
        or live_slots.get("allowed_slots")
        != [
            "main-git-release",
            "branch-commit-recovery",
            "mutable-local-testing",
        ]
        or live_slots.get("allowed_marketplaces")
        != [
            "evidence-lane-github",
            "evidence-lane-v300-stable-recovery",
            "evidence-lane-v300-testing-new",
        ]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 3
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or failover.get("script")
        != "scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1"
        or failover.get("registry_schema")
        != "evidence-lane.codex-three-slot-registry.v1"
        or failover.get("failure_target_slot") != "branch-commit-recovery"
        or failover.get("mutable_local_failure_never_targets_main_git") is not True
        or failover.get("single_transient_error_switch_allowed") is not False
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
        or goal_recovery.get(
            "requires_exactly_one_enabled_allowed_three_slot_selector"
        )
        is not True
        or goal_recovery.get("allowed_runtime_selectors")
        != [
            "evidence-lane-plugin@evidence-lane-github",
            "evidence-lane-plugin@evidence-lane-v300-stable-recovery",
            "evidence-lane-plugin@evidence-lane-v300-testing-new",
        ]
        or goal_recovery.get("stable_selector_growth_allowed") is not False
        or goal_recovery.get("raw_goal_objective_stored") is not False
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
        or remote_git_policy.get("per_push_confirmation_token_required")
        is not False
        or remote_git_policy.get("automatic_push_scope")
        != "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or remote_git_policy.get("host_managed_credentials_only") is not True
        or remote_git_policy.get("main_push_allowed") is not False
        or remote_git_policy.get("merge_allowed") is not False
        or remote_git_policy.get("pull_request_acceptance_allowed") is not False
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
            "The three-slot local/Git/recovery contract drifted."
        )

    search_toolchain = _search_toolchain_identity(plugin_root, release_channels)
    source_records, source_paths = _source_inventory(plugin_root)
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
    missing = sorted(REQUIRED_MEMBERS - source_names)
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
        },
        "members": source_records,
    }
    synthetic: dict[str, bytes] = {
        f"{SYNTHETIC_ROOT}/source-manifest.json": _json_bytes(source_manifest),
        f"{SYNTHETIC_ROOT}/skill-inventory.json": _json_bytes(skill_inventory),
    }
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
        "canonical_lane_count": EXPECTED_LANE_COUNT,
        "search_toolchain": search_toolchain,
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
    expected_hashes = {
        record["path"]: record["sha256"] for record in source_records
    }
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
        "source_version": expected_version,
        "package_version": package_version,
        "fresh_local_package_version": package_version != expected_version,
        "source_member_count": len(source_records),
        "skill_count": skill_inventory["count"],
        "canonical_lane_count": EXPECTED_LANE_COUNT,
        "search_toolchain": search_toolchain,
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
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
