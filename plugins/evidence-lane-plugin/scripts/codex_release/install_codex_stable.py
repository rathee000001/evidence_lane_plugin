"""Stage and activate one sealed Evidence Lane 2.1 Codex marketplace package.

The script uses the supported Codex marketplace and plugin commands. Stable
package bytes are reinstalled under one persistent stable selector; build
hashes belong in receipts, never in new plugin identities.  The separately
sealed PV11 fallback selector remains installed and disabled.  The script never
writes the generated plugin cache directly and never asks for or stores Git,
OpenAI, OAuth, PAT, or tunnel credentials.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid
import zipfile
from pathlib import Path
from typing import Any

BASE_RELEASE = "2.1.0"
FALLBACK_RELEASE = "2.0.0"
MARKETPLACE_NAME = "evidence-lane-github"
MARKETPLACE_DISPLAY_NAME = "GitLane Stable 2.1"
MARKETPLACE_SOURCE = "rathee000001/evidence_lane_plugin"
PLUGIN_NAME = "evidence-lane-plugin"
PLUGIN_SELECTOR = f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"
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
    "git_marketplace_source": MARKETPLACE_SOURCE,
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
INSTALL_SCHEMA = "evidence-lane.codex-stable-installation.v2"
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


class InstallationError(RuntimeError):
    """Raised before restart when a stable-install invariant is not exact."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _source_inventory(root: Path) -> dict[str, Any]:
    """Return one deterministic full-file inventory for exact-source comparison."""

    rows = []
    for path in sorted(row for row in root.rglob("*") if row.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("_evidence_lane_rehearsal/"):
            continue
        rows.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "file_count": len(rows),
        "manifest_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
        "files": rows,
    }


def _assert_exact_git_marketplace_source(
    *,
    extracted_inventory: dict[str, Any],
    marketplace_root: Path,
    expected_git_manifest_sha256: str,
    expected_git_file_count: int,
) -> dict[str, Any]:
    plugin_root = marketplace_root / "plugins" / PLUGIN_NAME
    if not plugin_root.is_dir():
        raise InstallationError("The Git marketplace lacks the Evidence Lane plugin root.")
    marketplace_inventory = _source_inventory(plugin_root)
    normalized_expected_manifest = expected_git_manifest_sha256.strip().upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", normalized_expected_manifest) is None
        or not isinstance(expected_git_file_count, int)
        or expected_git_file_count < 1
        or marketplace_inventory["file_count"] != expected_git_file_count
        or marketplace_inventory["manifest_sha256"]
        != normalized_expected_manifest
    ):
        raise InstallationError(
            "The Git marketplace bytes do not match the complete exact Git commit tree."
        )
    marketplace_by_path = {
        str(row["path"]): row for row in marketplace_inventory["files"]
    }
    package_files = extracted_inventory.get("files")
    if not isinstance(package_files, list) or any(
        not isinstance(row, dict)
        or marketplace_by_path.get(str(row.get("path") or "")) != row
        for row in package_files
    ) or len(package_files) != extracted_inventory.get("file_count"):
        raise InstallationError(
            "The install-package subset does not match the exact Git marketplace."
        )
    return {
        "status": "PASS",
        "source_type": "git",
        "repository": MARKETPLACE_SOURCE,
        "marketplace_name": MARKETPLACE_NAME,
        "file_count": marketplace_inventory["file_count"],
        "manifest_sha256": marketplace_inventory["manifest_sha256"],
        "package_subset_file_count": extracted_inventory["file_count"],
        "git_only_file_count": (
            marketplace_inventory["file_count"] - extracted_inventory["file_count"]
        ),
        "exact_git_commit_tree_match": True,
        "exact_commit_package_bytes_match": True,
    }


def _surface_inventory(plugin_root: Path, *, version: str) -> dict[str, Any]:
    hook_paths = [
        plugin_root / "hooks" / "hooks.json",
        *sorted((plugin_root / "hooks").glob("*.py")),
    ]
    skill_paths = sorted((plugin_root / "skills").glob("*/SKILL.md"))
    if not all(path.is_file() for path in hook_paths):
        raise InstallationError("The persistent hook inventory is incomplete.")
    hook_names = {path.name for path in hook_paths}
    if frozenset(hook_names) not in {
        frozenset(
            {
                "hooks.json",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "hooks.json",
                "post_tool_use.py",
                "session_start.py",
                "prompt_submit.py",
                "stop_response.py",
            }
        ),
        frozenset(
            {
                "hooks.json",
                "lifecycle_boundary.py",
                "post_tool_use.py",
                "pre_tool_use.py",
                "prompt_submit.py",
                "session_start.py",
                "stop_response.py",
            }
        ),
    }:
        raise InstallationError("The persistent hook inventory is not exact.")
    if len(skill_paths) != EXPECTED_CATALOG["skills"]:
        raise InstallationError("The governed skill inventory is not exact.")

    def inventory(paths: list[Path], *, skill: bool) -> dict[str, Any]:
        rows = [
            {
                "name": path.parent.name if skill else path.name,
                "sha256": _sha256(path),
            }
            for path in paths
        ]
        if len(rows) != len({row["name"] for row in rows}):
            raise InstallationError("A hook or skill inventory name is duplicated.")
        return {
            "count": len(rows),
            "records": rows,
            "inventory_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
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
        "event_inventory_sha256": hashlib.sha256(
            _json_bytes(registered_events)
        ).hexdigest().upper(),
    }
    hook_inventory["inventory_sha256"] = hashlib.sha256(
        _json_bytes(hook_inventory)
    ).hexdigest().upper()
    core = {
        "schema": "evidence-lane.codex-installed-surface-inventory.v2",
        "plugin_version": version,
        "hooks": hook_inventory,
        "skills": inventory(skill_paths, skill=True),
        "catalog": dict(EXPECTED_CATALOG),
        "raw_paths_included": False,
    }
    core["surface_inventory_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
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
                raise InstallationError(
                    "Every native tool needs a literal name and annotation."
                )
            rows.append(
                {"name": name_node.value, "annotation": annotation_node.id}
            )
    rows.sort(key=lambda row: row["name"])
    read = sum(row["annotation"] == "_READ_ONLY" for row in rows)
    write = len(rows) - read
    if (
        len(rows) != EXPECTED_CATALOG["tools"]
        or len({row["name"] for row in rows}) != len(rows)
        or read != EXPECTED_CATALOG["read"]
        or write != EXPECTED_CATALOG["write"]
    ):
        raise InstallationError("The native 62/21/41 tool catalog drifted.")
    return {
        "tools": len(rows),
        "read": read,
        "write": write,
        "skills": EXPECTED_CATALOG["skills"],
        "tool_names_unique": True,
        "static_catalog_sha256": hashlib.sha256(_json_bytes(rows)).hexdigest().upper(),
    }


def _surface_change_display(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    def changes(kind: str) -> dict[str, Any]:
        current_rows = {
            row["name"]: row["sha256"] for row in current[kind]["records"]
        }
        previous_rows = (
            {
                row["name"]: row["sha256"]
                for row in previous[kind]["records"]
            }
            if previous is not None
            else {}
        )
        added_files = sorted(current_rows.keys() - previous_rows.keys())
        changed_files = sorted(
            name
            for name in current_rows.keys() & previous_rows.keys()
            if current_rows[name] != previous_rows[name]
        )
        removed_files = sorted(previous_rows.keys() - current_rows.keys())
        result = {
            "count": current[kind]["count"],
            "count_semantics": current[kind].get(
                "count_semantics", "SURFACE_RECORD_COUNT"
            ),
            "added": sorted(current_rows.keys() - previous_rows.keys()),
            "changed": changed_files,
            "removed": removed_files,
            "added_files": added_files,
            "changed_files": changed_files,
            "removed_files": removed_files,
            "inventory_sha256": current[kind]["inventory_sha256"],
        }
        if kind == "hooks":
            current_events = set(current[kind]["registered_events"])
            previous_events = (
                set(previous[kind]["registered_events"])
                if previous is not None
                else set()
            )
            result.update(
                {
                    "registered_event_count": current[kind][
                        "registered_event_count"
                    ],
                    "registered_events": current[kind]["registered_events"],
                    "handler_count": current[kind]["handler_count"],
                    "hook_file_count": current[kind]["hook_file_count"],
                    "added_events": sorted(current_events - previous_events),
                    "removed_events": sorted(previous_events - current_events),
                    "file_inventory_sha256": current[kind][
                        "file_inventory_sha256"
                    ],
                    "event_inventory_sha256": current[kind][
                        "event_inventory_sha256"
                    ],
                }
            )
        return result

    core = {
        "schema": "evidence-lane.codex-installed-surface-change-display.v2",
        "state": "INITIAL_V2_BASELINE" if previous is None else "VERSIONED_UPDATE",
        "previous_plugin_version": (
            previous.get("plugin_version") if previous is not None else None
        ),
        "current_plugin_version": current["plugin_version"],
        "hooks": changes("hooks"),
        "skills": changes("skills"),
        "catalog": {
            **current["catalog"],
            "changed_from_previous": (
                previous is not None and previous.get("catalog") != current["catalog"]
            ),
        },
        "previous_surface_inventory_sha256": (
            previous.get("surface_inventory_sha256")
            if previous is not None
            else None
        ),
        "current_surface_inventory_sha256": current[
            "surface_inventory_sha256"
        ],
        "raw_paths_included": False,
        "private_research_question_included": False,
    }
    core["change_display_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    return core


def _verified_staged_surface_change_display(
    value: Any,
    *,
    current: dict[str, Any],
) -> dict[str, Any]:
    """Verify the preflight diff before reusing it during activation.

    Installation is deliberately a two-pass operation.  Once the first pass has
    staged the new marketplace, recomputing a diff during the activation pass
    compares the package with itself and erases the actual version change.  The
    stage receipt therefore carries the original content-addressed display and
    the second pass must verify and reuse it byte-for-byte.
    """

    if not isinstance(value, dict):
        raise InstallationError(
            "The exact staged marketplace lacks its sealed surface change display."
        )
    core = dict(value)
    observed_sha256 = str(core.pop("change_display_sha256", "")).upper()
    expected_sha256 = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    hooks = dict(value.get("hooks") or {})
    skills = dict(value.get("skills") or {})
    catalog = dict(value.get("catalog") or {})
    if (
        value.get("schema")
        != "evidence-lane.codex-installed-surface-change-display.v2"
        or observed_sha256 != expected_sha256
        or value.get("current_plugin_version") != current["plugin_version"]
        or value.get("current_surface_inventory_sha256")
        != current["surface_inventory_sha256"]
        or value.get("raw_paths_included") is not False
        or value.get("private_research_question_included") is not False
        or hooks.get("count") != current["hooks"]["count"]
        or hooks.get("registered_event_count")
        != current["hooks"]["registered_event_count"]
        or hooks.get("registered_events")
        != current["hooks"]["registered_events"]
        or hooks.get("handler_count") != current["hooks"]["handler_count"]
        or hooks.get("hook_file_count") != current["hooks"]["hook_file_count"]
        or hooks.get("inventory_sha256")
        != current["hooks"]["inventory_sha256"]
        or skills.get("count") != current["skills"]["count"]
        or skills.get("inventory_sha256")
        != current["skills"]["inventory_sha256"]
        or any(catalog.get(key) != current["catalog"][key] for key in EXPECTED_CATALOG)
    ):
        raise InstallationError("The exact staged surface change display drifted.")
    return value


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _load_receipt(
    receipt_path: Path,
    archive: Path,
    *,
    activation: bool,
) -> dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    sealed = receipt.get("archive") or {}
    schema = receipt.get("schema")
    boundary = receipt.get("boundary")
    exact_commit_package = (
        schema == "evidence-lane.codex-exact-commit-package.v1.receipt"
        and boundary == "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
    )
    exact_export = dict(receipt.get("exact_commit_export") or {})
    plugin_source_manifest_sha256 = str(
        exact_export.get("plugin_source_manifest_sha256") or ""
    ).upper()
    plugin_source_member_count = exact_export.get("plugin_source_member_count")
    exact_export_valid = (not exact_commit_package) or (
        re.fullmatch(r"[A-F0-9]{64}", plugin_source_manifest_sha256) is not None
        and isinstance(plugin_source_member_count, int)
        and plugin_source_member_count >= int(receipt.get("source_member_count") or 0)
        and exact_export.get("git_archive_member_count")
        == plugin_source_member_count
        and exact_export.get("plugin_path") == "plugins/evidence-lane-plugin"
        and exact_export.get("projection_clean") is True
        and exact_export.get("working_checkout_bytes_used") is False
        and exact_export.get("untracked_bytes_used") is False
    )
    local_rehearsal = (
        schema == "evidence-lane.non-lifecycle-local-package-rehearsal.v1.receipt"
        and boundary == "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL"
    )
    git_boundary_valid = (
        exact_commit_package
        and receipt.get("git_invoked") is True
        and receipt.get("git_write_invoked") is False
    ) or (
        local_rehearsal
        and receipt.get("git_invoked") is False
        and receipt.get("git_write_invoked") in (None, False)
    )
    self_seal_valid = True
    if exact_commit_package:
        receipt_sha256 = str(receipt.get("receipt_sha256") or "").upper()
        core = dict(receipt)
        core.pop("receipt_sha256", None)
        self_seal_valid = (
            re.fullmatch(r"[A-F0-9]{64}", receipt_sha256) is not None
            and hashlib.sha256(_json_bytes(core)).hexdigest().upper()
            == receipt_sha256
        )
    if (
        not (exact_commit_package or local_rehearsal)
        or (activation and not exact_commit_package)
        or not self_seal_valid
        or not exact_export_valid
        or receipt.get("status") != "PASS"
        or sealed.get("filename") != archive.name
        or sealed.get("sha256") != _sha256(archive)
        or receipt.get("governed_candidate_created") is not False
        or not git_boundary_valid
        or receipt.get("accepted_pointer_moved") is not False
    ):
        raise InstallationError(
            "The package receipt does not seal this archive or is not eligible "
            "for the requested staging/activation boundary."
        )
    return receipt


def _load_release_authority(
    *,
    authority_path: Path,
    authority_file_sha256: str,
    archive: Path,
    package_receipt_path: Path,
    package_receipt: dict[str, Any],
) -> dict[str, Any]:
    expected_file_sha256 = authority_file_sha256.strip().upper()
    if (
        re.fullmatch(r"[A-F0-9]{64}", expected_file_sha256) is None
        or _sha256(authority_path) != expected_file_sha256
    ):
        raise InstallationError(
            "The governed Git/CI/Vercel release-authority file seal drifted."
        )
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    receipt_sha256 = str(authority.get("receipt_sha256") or "").upper()
    authority_body = dict(authority)
    authority_body.pop("receipt_sha256", None)
    calculated_receipt_sha256 = hashlib.sha256(
        _json_bytes(authority_body)
    ).hexdigest().upper()
    source = dict(authority.get("source") or {})
    remote = dict(authority.get("remote_git") or {})
    ci = dict(authority.get("github_ci") or {})
    preview = dict(authority.get("vercel_preview") or {})
    source_commit = str(source.get("commit") or "").lower()
    source_tree = str(source.get("tree") or "").lower()
    branch = str(source.get("branch") or "")
    base_anchor = dict(package_receipt.get("base_anchor") or {})
    package_export = dict(package_receipt.get("exact_commit_export") or {})
    required_check_count = int(ci.get("required_check_count") or 0)
    successful_check_count = int(ci.get("successful_check_count") or 0)
    if (
        authority.get("schema")
        != "evidence-lane.codex-git-ci-vercel-release-authority.v2"
        or package_receipt.get("schema")
        != "evidence-lane.codex-exact-commit-package.v1.receipt"
        or package_receipt.get("boundary")
        != "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED"
        or authority.get("status") != "PASS"
        or authority.get("boundary")
        != "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT"
        or receipt_sha256 != calculated_receipt_sha256
        or authority.get("archive_sha256") != _sha256(archive)
        or authority.get("package_receipt_sha256") != _sha256(package_receipt_path)
        or authority.get("working_source_manifest_sha256")
        != package_receipt.get("working_source_manifest_sha256")
        or authority.get("plugin_source_manifest_sha256")
        != package_export.get("plugin_source_manifest_sha256")
        or authority.get("plugin_source_member_count")
        != package_export.get("plugin_source_member_count")
        or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        or re.fullmatch(r"[0-9a-f]{40}", source_tree) is None
        or branch in {"main", "master"}
        or not branch
        or source.get("exact_commit_export") is not True
        or source.get("exact_commit_projection_clean") is not True
        or source.get("working_checkout_clean_required") is not False
        or source.get("untracked_bytes_excluded") is not True
        or str(base_anchor.get("commit") or "").lower() != source_commit
        or str(base_anchor.get("tree") or "").lower() != source_tree
        or remote.get("route") != "NATIVE_GOVERNED_REMOTE_GIT"
        or remote.get("push_status") != "EXECUTED"
        or str(remote.get("remote_branch_commit") or "").lower() != source_commit
        or remote.get("protected_branch") is not False
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(remote.get("native_receipt_sha256") or "").upper(),
        )
        is None
        or ci.get("status") != "PASS"
        or str(ci.get("head_sha") or "").lower() != source_commit
        or ci.get("required_checks_complete") is not True
        or required_check_count < 1
        or successful_check_count != required_check_count
        or int(ci.get("failed_check_count") or 0) != 0
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(ci.get("receipt_sha256") or "").upper(),
        )
        is None
        or str(ci.get("repository") or "") != MARKETPLACE_SOURCE
        or preview.get("status") != "PASS"
        or preview.get("state") != "READY"
        or preview.get("target") != "PREVIEW"
        or preview.get("repository") != MARKETPLACE_SOURCE
        or preview.get("branch") != branch
        or str(preview.get("head_sha") or "").lower() != source_commit
        or preview.get("git_integration") is not True
        or preview.get("manual_deploy") is not False
        or preview.get("production_deployment") is not False
        or not str(preview.get("deployment_id") or "").startswith("dpl_")
        or not str(preview.get("url") or "").endswith(".vercel.app")
        or re.fullmatch(
            r"[A-F0-9]{64}",
            str(preview.get("receipt_sha256") or "").upper(),
        )
        is None
        or authority.get("governed_candidate_created") is not False
        or authority.get("accepted_pointer_moved") is not False
        or authority.get("hil_inferred") is not False
    ):
        raise InstallationError(
            "Activation requires one exact clean Git commit, governed native push, "
            "successful GitHub CI, and one READY non-production Vercel Git preview "
            "bound to this archive."
        )
    return authority


def _enrich_legacy_hook_surface(
    *,
    receipt: dict[str, Any],
    legacy_surface: dict[str, Any],
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recover event semantics from exact archived marketplace bytes.

    Early v2 installation receipts counted hook files and did not yet include
    registered event names.  The archived marketplace is acceptable only when
    its stage archive SHA matches the installation receipt and every hook and
    skill file hash reproduces the legacy receipt inventory.
    """

    archive_sha256 = str(receipt.get("archive_sha256") or "").upper()
    archive_root = (
        data_root
        / "installations"
        / "codex-v200"
        / "marketplace-archives"
    )
    matches: list[tuple[dict[str, Any], Path]] = []
    for stage_path in sorted(archive_root.glob("*/EVIDENCE_LANE_STAGE.json")):
        stage = json.loads(stage_path.read_text(encoding="utf-8"))
        if (
            stage.get("schema") != "evidence-lane.codex-marketplace-stage.v2"
            or str(stage.get("archive_sha256") or "").upper() != archive_sha256
        ):
            continue
        plugin_root = stage_path.parent / "plugins" / PLUGIN_NAME
        manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
        if not manifest_path.is_file():
            raise InstallationError(
                "The archived legacy marketplace plugin manifest is missing."
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        version = str((receipt.get("plugin") or {}).get("version") or "")
        if manifest.get("version") != version:
            raise InstallationError(
                "The archived legacy marketplace version does not match its receipt."
            )
        enriched = _surface_inventory(plugin_root, version=version)
        expected_hook_files = {
            "count": enriched["hooks"]["hook_file_count"],
            "records": enriched["hooks"]["records"],
            "inventory_sha256": enriched["hooks"]["file_inventory_sha256"],
        }
        if (
            dict(legacy_surface.get("hooks") or {}) != expected_hook_files
            or dict(legacy_surface.get("skills") or {}) != enriched["skills"]
            or dict(legacy_surface.get("catalog") or {}) != enriched["catalog"]
        ):
            raise InstallationError(
                "The archived legacy marketplace bytes do not reproduce the sealed receipt."
            )
        matches.append((enriched, stage_path))
    if len(matches) != 1:
        raise InstallationError(
            "Exactly one archived legacy marketplace must match the baseline archive."
        )
    enriched, stage_path = matches[0]
    return enriched, {
        "surface_enrichment": "VERIFIED_ARCHIVED_MARKETPLACE_EVENT_INVENTORY",
        "archived_stage_receipt": str(stage_path.resolve()),
        "archived_stage_receipt_sha256": _sha256(stage_path),
        "receipt_surface_inventory_sha256": legacy_surface[
            "surface_inventory_sha256"
        ],
    }


def _load_comparison_baseline(
    *,
    path: Path,
    expected_sha256: str,
    data_root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load one explicitly sealed prior host-stable installation surface."""

    expected = str(expected_sha256 or "").strip().upper()
    authority_root = data_root / "installations" / "codex-v200"
    resolved = path.resolve()
    if (
        re.fullmatch(r"[A-F0-9]{64}", expected) is None
        or not resolved.is_file()
        or not _inside(resolved, authority_root)
        or not resolved.name.startswith("INSTALL_")
        or _sha256(resolved) != expected
    ):
        raise InstallationError(
            "The comparison baseline installation receipt is unavailable or unsealed."
        )
    receipt = json.loads(resolved.read_text(encoding="utf-8"))
    receipt_core = dict(receipt)
    receipt_sha256 = str(receipt_core.pop("receipt_sha256", "")).upper()
    surface = dict((receipt.get("plugin") or {}).get("surface_inventory") or {})
    surface_core = dict(surface)
    surface_sha256 = str(
        surface_core.pop("surface_inventory_sha256", "")
    ).upper()
    plugin = dict(receipt.get("plugin") or {})
    activation = dict(receipt.get("activation") or {})
    if (
        receipt.get("schema") != INSTALL_SCHEMA
        or receipt.get("status") != "PASS"
        or receipt_sha256
        != hashlib.sha256(_json_bytes(receipt_core)).hexdigest().upper()
        or receipt.get("candidate_created_or_accepted") is not False
        or receipt.get("pointer_moved") is not False
        or receipt.get("hil_inferred") is not False
        or activation.get("state") != "INSTALLED_RESTART_REQUIRED"
        or plugin.get("plugin_id") != PLUGIN_NAME
        or plugin.get("version") != surface.get("plugin_version")
        or surface.get("schema")
        != "evidence-lane.codex-installed-surface-inventory.v2"
        or surface.get("raw_paths_included") is not False
        or surface_sha256
        != hashlib.sha256(_json_bytes(surface_core)).hexdigest().upper()
        or dict(surface.get("catalog") or {}) != EXPECTED_CATALOG
    ):
        raise InstallationError("The comparison baseline installation receipt drifted.")
    enrichment: dict[str, Any] = {
        "surface_enrichment": "NOT_REQUIRED_EVENT_INVENTORY_ALREADY_SEALED",
        "receipt_surface_inventory_sha256": surface["surface_inventory_sha256"],
    }
    if "registered_events" not in dict(surface.get("hooks") or {}):
        surface, enrichment = _enrich_legacy_hook_surface(
            receipt=receipt,
            legacy_surface=surface,
            data_root=data_root,
        )
    identity = {
        "installation_receipt": str(resolved),
        "installation_receipt_sha256": expected,
        "plugin_version": plugin["version"],
        "surface_inventory_sha256": surface["surface_inventory_sha256"],
        "baseline_role": "EXACT_PRIOR_HOST_STABLE_INSTALLATION",
        **enrichment,
    }
    return surface, identity


def _safe_extract(archive_path: Path, target: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        names = archive.namelist()
        if names != sorted(names) or len(names) != len(set(names)):
            raise InstallationError("Archive members are not sorted and unique.")
        for info in archive.infolist():
            relative = Path(info.filename)
            if relative.is_absolute() or ".." in relative.parts or "\\" in info.filename:
                raise InstallationError(f"Unsafe archive member: {info.filename}")
            destination = (target / relative).resolve()
            if not _inside(destination, target):
                raise InstallationError(f"Archive member escaped extraction: {info.filename}")
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output)


def _validate_plugin(plugin_root: Path) -> dict[str, Any]:
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(manifest.get("version") or "")
    contract = json.loads(
        (plugin_root / "scripts" / "codex-release-channel.json").read_text(
            encoding="utf-8"
        )
    )
    stable = contract.get("stable") or {}
    fallback = contract.get("fallback") or {}
    live_slots = contract.get("live_slot_policy") or {}
    failover = contract.get("failover_operator") or {}
    goal_recovery = contract.get("goal_recovery") or {}
    behavior_ownership = contract.get("behavior_ownership") or {}
    stable_activation_gate = contract.get("stable_activation_gate") or {}
    brand_identity = contract.get("brand_identity") or {}
    remote_git = contract.get("remote_git_policy") or {}
    promotion = contract.get("promotion_gate") or {}
    skill_count = len(list((plugin_root / "skills").glob("*/SKILL.md")))
    project = tomllib.loads((plugin_root / "pyproject.toml").read_text("utf-8"))
    constants = (
        plugin_root / "src" / "evidence_lane_plugin" / "constants.py"
    ).read_text(encoding="utf-8")
    engine_match = re.search(
        r'^ENGINE_VERSION\s*=\s*"(?P<version>[^"]+)"',
        constants,
        flags=re.MULTILINE,
    )
    catalog = _catalog(plugin_root)
    interface = dict(manifest.get("interface") or {})
    brand_icon = plugin_root / str(brand_identity.get("icon_path") or "")
    release_helpers = (
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
    if (
        manifest.get("name") != PLUGIN_NAME
        or not version.startswith(f"{BASE_RELEASE}+codex.")
        or manifest.get("mcpServers") != "./.mcp.json"
        or "apps" in manifest
        or (plugin_root / ".app.json").exists()
        or contract.get("schema") != "evidence-lane.codex-release-channel.v2"
        or stable.get("release") != BASE_RELEASE
        or stable.get("slot_role") != "stable-build"
        or stable.get("codex_marketplace_slot") != MARKETPLACE_NAME
        or stable.get("marketplace_display_name") != MARKETPLACE_DISPLAY_NAME
        or stable.get("install_source") != "GIT_EXACT_COMMIT"
        or stable.get("byte_frozen") is not False
        or stable.get("updates_require_verified_unique_build_identity") is not True
        or stable.get("stable_selector_is_persistent") is not True
        or stable.get("stable_updates_reinstall_in_place") is not True
        or stable.get("build_identity_is_receipt_not_selector") is not True
        or stable.get("native_tool_count") != EXPECTED_CATALOG["tools"]
        or stable.get("native_read_tool_count") != EXPECTED_CATALOG["read"]
        or stable.get("native_write_tool_count") != EXPECTED_CATALOG["write"]
        or stable.get("skill_count") != EXPECTED_CATALOG["skills"]
        or skill_count != EXPECTED_CATALOG["skills"]
        or stable.get("codex_apps_allowed") is not False
        or stable.get("generated_namespace_allowed") is not False
        or stable.get("direct_stdio_fallback_allowed") is not False
        or stable.get("google_drive_bundled") is not False
        or project.get("project", {}).get("version") != BASE_RELEASE
        or engine_match is None
        or engine_match.group("version") != BASE_RELEASE
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
        or fallback.get("prewarmed_means_installed_verified_and_stopped")
        is not True
        or fallback.get("simultaneous_mcp_allowed") is not False
        or fallback.get("simultaneous_tunnel_allowed") is not False
        or live_slots.get("exact_slot_count_after_pv11_acceptance") != 2
        or live_slots.get("allowed_slots") != ["stable-build", "fallback"]
        or live_slots.get("max_enabled_plugin_count") != 1
        or live_slots.get("exact_registered_plugin_count") != 2
        or live_slots.get("stable_selector_growth_allowed") is not False
        or live_slots.get("max_active_native_mcp_count") != 1
        or live_slots.get("max_active_tunnel_count") != 1
        or live_slots.get("inactive_slot_remains_installed") is not True
        or live_slots.get("manual_loaded_cache_deletion_allowed") is not False
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
        or contract.get("host_storage_tunnel_matrix")
        != EXPECTED_HOST_STORAGE_TUNNEL_MATRIX
        or remote_git.get("effective_release") != BASE_RELEASE
        or remote_git.get("per_push_confirmation_token_required") is not False
        or remote_git.get("automatic_push_scope")
        != "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
        or remote_git.get("host_managed_credentials_only") is not True
        or remote_git.get("main_push_allowed") is not False
        or remote_git.get("merge_allowed") is not False
        or remote_git.get("pull_request_acceptance_allowed") is not False
        or remote_git.get("force_push_allowed") is not False
        or promotion.get("mode") != "CODE"
        or promotion.get("ci_cd_law") != "CONTROLLED_REQUIRED"
        or promotion.get("explicit_six_way_hil_required") is not True
        or not all(path.is_file() for path in release_helpers)
    ):
        raise InstallationError("The extracted v2 plugin or release contract drifted.")
    native = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
    if set(native.get("mcpServers") or {}) != {"evidence-lane"}:
        raise InstallationError("The package must contain one native evidence-lane MCP.")
    forbidden = (
        plugin_root / "release-channels.json",
        plugin_root / "remote_adapter",
        plugin_root / "evidence",
    )
    external_app_artifacts = tuple(plugin_root.glob("*-app-connection.json")) + tuple(
        plugin_root.glob("*-app-submission.json")
    )
    if any(path.exists() for path in forbidden) or external_app_artifacts:
        raise InstallationError("A separate app, website, or evidence surface leaked in.")
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
        raise InstallationError("The persistent hook event set drifted.")
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
            raise InstallationError(f"{name} does not emit the persistent change notice.")
    return {
        "plugin_id": PLUGIN_NAME,
        "version": version,
        "manifest_sha256": _sha256(manifest_path),
        "catalog": catalog,
        "surface_inventory": _surface_inventory(plugin_root, version=version),
    }


def _marketplace_bytes(marketplace_name: str) -> bytes:
    return _json_bytes(
        {
            "name": marketplace_name,
            "interface": {"displayName": MARKETPLACE_DISPLAY_NAME},
            "plugins": [
                {
                    "name": PLUGIN_NAME,
                    "source": {
                        "source": "local",
                        "path": "./plugins/evidence-lane-plugin",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Developer Tools",
                }
            ],
        }
    )


def _stage_marketplace(
    *,
    extracted: Path,
    marketplace_root: Path,
    data_root: Path,
    identity: dict[str, Any],
    archive_sha256: str,
    marketplace_name: str = MARKETPLACE_NAME,
    comparison_surface: dict[str, Any] | None = None,
    comparison_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    codex_home = marketplace_root.parent.parent.resolve()
    expected_parent = codex_home / "local-marketplaces"
    if marketplace_root.parent.resolve() != expected_parent.resolve():
        raise InstallationError("Marketplace root is outside Codex local-marketplaces.")
    cache_root = codex_home / "plugins" / "cache"
    if _inside(marketplace_root, cache_root):
        raise InstallationError("Generated Codex plugin cache is immutable to this installer.")
    staging = marketplace_root.parent / (
        f".{marketplace_root.name}.staging-{uuid.uuid4().hex}"
    )
    staging_plugin = staging / "plugins" / PLUGIN_NAME
    staging_plugin.parent.mkdir(parents=True, exist_ok=True)
    prior_plugin = marketplace_root / "plugins" / PLUGIN_NAME
    previous_surface = comparison_surface
    if previous_surface is None and prior_plugin.is_dir():
        prior_manifest = json.loads(
            (prior_plugin / ".codex-plugin" / "plugin.json").read_text(
                encoding="utf-8"
            )
        )
        previous_surface = _surface_inventory(
            prior_plugin,
            version=str(prior_manifest.get("version") or "UNVERIFIED"),
        )
    surface_change = _surface_change_display(
        previous=previous_surface,
        current=identity["surface_inventory"],
    )
    try:
        shutil.copytree(
            extracted,
            staging_plugin,
            ignore=shutil.ignore_patterns("_evidence_lane_rehearsal"),
        )
        marketplace_path = staging / ".agents" / "plugins" / "marketplace.json"
        _write_atomic(marketplace_path, _marketplace_bytes(marketplace_name))
        prepared = {
            "schema": "evidence-lane.codex-marketplace-stage.v2",
            "state": "STAGED_NOT_HOST_ACTIVE",
            "marketplace": marketplace_name,
            "plugin": identity,
            "archive_sha256": archive_sha256,
            "comparison_baseline": comparison_baseline,
            "surface_change_display": surface_change,
            "generated_cache_written_directly": False,
            "prior_release_deleted": False,
        }
        _write_atomic(staging / "EVIDENCE_LANE_STAGE.json", _json_bytes(prepared))
        if marketplace_root.exists():
            current_stage = marketplace_root / "EVIDENCE_LANE_STAGE.json"
            if current_stage.is_file():
                current = json.loads(current_stage.read_text(encoding="utf-8"))
                if current.get("archive_sha256") == archive_sha256:
                    if current.get("comparison_baseline") != comparison_baseline:
                        raise InstallationError(
                            "The exact staged marketplace comparison baseline drifted."
                        )
                    preserved_change = _verified_staged_surface_change_display(
                        current.get("surface_change_display"),
                        current=identity["surface_inventory"],
                    )
                    shutil.rmtree(staging)
                    return {
                        "state": "ALREADY_STAGED_EXACT",
                        "prior_marketplace_archived": False,
                        "comparison_baseline": comparison_baseline,
                        "surface_change_display": preserved_change,
                    }
            archive_root = (
                data_root
                / "installations"
                / "codex-v200"
                / "marketplace-archives"
            )
            archive_root.mkdir(parents=True, exist_ok=True)
            prior_sha = _sha256(current_stage) if current_stage.is_file() else "UNSEALED"
            archive_target = archive_root / f"{marketplace_root.name}-{prior_sha[:16]}"
            if archive_target.exists():
                raise InstallationError("The exact prior marketplace archive already exists.")
            if not _inside(marketplace_root, expected_parent):
                raise InstallationError("Refusing to move an uncontained marketplace root.")
            os.replace(marketplace_root, archive_target)
            prior_archived = True
        else:
            prior_archived = False
        os.replace(staging, marketplace_root)
        return {
            "state": "STAGED",
            "prior_marketplace_archived": prior_archived,
            "comparison_baseline": comparison_baseline,
            "surface_change_display": surface_change,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _run_codex(
    executable: Path,
    codex_home: Path,
    arguments: list[str],
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    timeout_seconds = (
        480
        if arguments[:3] == ["plugin", "marketplace", "add"]
        else 120
    )
    completed = subprocess.run(
        [str(executable), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise InstallationError(
            f"Codex command failed ({arguments[:3]}): {completed.stderr.strip()}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise InstallationError("Codex did not return the requested JSON receipt.") from exc


def _prewarm_installed_runtime(plugin_root: Path) -> dict[str, Any]:
    """Build and probe the installed cache before any task can be reopened."""

    bootstrap = plugin_root / "scripts" / "bootstrap.py"
    brand_icon = plugin_root / str(EXPECTED_BRAND_IDENTITY["icon_path"])
    if not bootstrap.is_file():
        raise InstallationError("The installed package has no governed bootstrap.")
    if (
        not brand_icon.is_file()
        or _sha256(brand_icon) != EXPECTED_BRAND_IDENTITY["icon_sha256"]
    ):
        raise InstallationError(
            "The installed Evidence Lane icon is missing or changed before prewarm."
        )
    started = time.monotonic()
    bootstrap_attempts: list[dict[str, Any]] = []
    boot: subprocess.CompletedProcess[bytes] | None = None
    for attempt in range(1, 3):
        try:
            boot = subprocess.run(
                [sys.executable, str(bootstrap)],
                check=False,
                cwd=plugin_root,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=900,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallationError(
                "The installed runtime bootstrap did not complete before task reopen."
            ) from exc
        bootstrap_attempts.append(
            {
                "attempt": attempt,
                "returncode": int(boot.returncode),
                "stdout_sha256": hashlib.sha256(boot.stdout).hexdigest().upper(),
                "stderr_sha256": hashlib.sha256(boot.stderr).hexdigest().upper(),
            }
        )
        if boot.returncode == 0:
            break
    if boot is None or boot.returncode != 0:
        raise InstallationError(
            "The installed runtime bootstrap failed twice on the same sealed bytes "
            f"before task reopen (attempts={bootstrap_attempts})."
        )
    runtime_python = (
        plugin_root / ".venv" / "Scripts" / "python.exe"
        if os.name == "nt"
        else plugin_root / ".venv" / "bin" / "python"
    )
    if not runtime_python.is_file():
        raise InstallationError("The governed bootstrap did not create its runtime.")
    environment = os.environ.copy()
    source = str((plugin_root / "src").resolve())
    existing_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source, existing_pythonpath) if part
    )
    probe_source = (
        "import json; "
        "from evidence_lane_plugin.constants import ENGINE_VERSION; "
        "from evidence_lane_plugin.lane_engine import prewarm_native_dependencies; "
        "from evidence_lane_plugin.mcp_server import "
        "CODEX_READ_TOOL_NAMES,NATIVE_MCP_SERVER_IDENTITY,create_mcp_server; "
        "prewarm_native_dependencies(); "
        "server=create_mcp_server(); "
        "route=server._evidence_lane_native_route_receipt; "
        "print(json.dumps({"
        "'engine_version':ENGINE_VERSION,"
        "'native_server_identity':NATIVE_MCP_SERVER_IDENTITY,"
        "'read_tool_count':len(CODEX_READ_TOOL_NAMES),"
        "'tool_count':route['tool_count'],"
        "'tool_catalog_sha256':route['tool_catalog_sha256'],"
        "'route_status':route['status'],"
        "'resource_uri':route['mcp_apps_resource_uri'],"
        "'native_dependency_prewarm_completed':True"
        "},sort_keys=True))"
    )
    try:
        probe = subprocess.run(
            [str(runtime_python), "-c", probe_source],
            check=False,
            cwd=plugin_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallationError(
            "The installed native runtime probe did not complete before task reopen."
        ) from exc
    if probe.returncode != 0:
        raise InstallationError(
            "The installed native runtime probe failed before task reopen "
            f"(exit {probe.returncode})."
        )
    try:
        output_lines = [
            line for line in probe.stdout.decode("utf-8").splitlines() if line.strip()
        ]
        result = json.loads(output_lines[-1])
    except (IndexError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallationError(
            "The installed native runtime probe returned no exact JSON receipt."
        ) from exc
    expected_result = {
        "engine_version": BASE_RELEASE,
        "native_server_identity": "evidence-lane",
        "read_tool_count": EXPECTED_CATALOG["read"],
        "tool_count": EXPECTED_CATALOG["tools"],
        "route_status": "PASS",
        "resource_uri": EXPECTED_BRAND_IDENTITY["resource_uri"],
        "native_dependency_prewarm_completed": True,
    }
    result_without_catalog_seal = dict(result)
    catalog_seal = result_without_catalog_seal.pop("tool_catalog_sha256", None)
    if result_without_catalog_seal != expected_result:
        raise InstallationError("The installed native runtime identity drifted.")
    if re.fullmatch(r"[A-F0-9]{64}", str(catalog_seal)) is None:
        raise InstallationError("The installed native catalog seal is missing.")
    core = {
        "schema": "evidence-lane.codex-installed-runtime-prewarm.v1",
        "status": "PASS",
        "runtime_ready_before_task_reopen": True,
        "plugin_root_sha256": hashlib.sha256(
            str(plugin_root.resolve()).encode("utf-8")
        ).hexdigest().upper(),
        "runtime_python_sha256": _sha256(runtime_python),
        "bootstrap_attempt_count": len(bootstrap_attempts),
        "bootstrap_attempts": bootstrap_attempts,
        "bootstrap_stdout_sha256": hashlib.sha256(boot.stdout).hexdigest().upper(),
        "bootstrap_stderr_sha256": hashlib.sha256(boot.stderr).hexdigest().upper(),
        "probe_stdout_sha256": hashlib.sha256(probe.stdout).hexdigest().upper(),
        "probe_stderr_sha256": hashlib.sha256(probe.stderr).hexdigest().upper(),
        "engine_version": result["engine_version"],
        "native_server_identity": result["native_server_identity"],
        "tool_count": result["tool_count"],
        "tool_catalog_sha256": catalog_seal,
        "resource_uri": result["resource_uri"],
        "brand_icon_sha256": _sha256(brand_icon),
        "catalog_expected": dict(EXPECTED_CATALOG),
        "native_dependency_prewarm_completed": True,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "task_reopened": False,
    }
    core["receipt_sha256"] = hashlib.sha256(_json_bytes(core)).hexdigest().upper()
    return core


def _resolve_stable_marketplace_name(
    *,
    requested_name: str,
    two_slot_authority: dict[str, Any] | None,
) -> str:
    """Return the one canonical Git stable name, allowing one legacy migration."""

    if two_slot_authority is None:
        if requested_name != MARKETPLACE_NAME:
            raise InstallationError(
                "A build hash must not create a new stable marketplace identity."
            )
        return MARKETPLACE_NAME

    registry = dict(two_slot_authority.get("registry") or {})
    stable = dict((registry.get("slots") or {}).get("stable-build") or {})
    prior_selector = str(stable.get("plugin_selector") or "")
    if not prior_selector.startswith(f"{PLUGIN_NAME}@"):
        raise InstallationError("The two-slot registry has no exact stable selector.")
    if requested_name != MARKETPLACE_NAME:
        raise InstallationError(
            "The stable route must use the canonical GitLane marketplace identity."
        )
    return MARKETPLACE_NAME


def _prepare_in_place_stable_reinstall(
    *,
    executable: Path,
    codex_home: Path,
    plugin_selector: str,
    marketplace_name: str,
    two_slot_authority: dict[str, Any] | None,
) -> dict[str, Any]:
    """Preflight a canonical stable update without pruning historical slots.

    During the one-time migration the currently working stable route remains
    installed until the canonical Git route has been installed, prewarmed,
    selected exclusively, and hook-trusted.  A later refresh of the already
    canonical selector removes and re-adds that same selector while Codex is
    closed; the immutable disabled fallback remains untouched.
    """

    before_plugins = _run_codex(executable, codex_home, ["plugin", "list", "--json"])
    installed = before_plugins.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence_plugins = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    fallback_selector: str | None = None
    prior_stable_selector: str | None = None
    if two_slot_authority is not None:
        registry = dict(two_slot_authority.get("registry") or {})
        slots = dict(registry.get("slots") or {})
        stable = dict(slots.get("stable-build") or {})
        fallback = dict(slots.get("fallback") or {})
        prior_stable_selector = str(stable.get("plugin_selector") or "")
        fallback_selector = str(fallback.get("plugin_selector") or "")
        by_selector = {
            str(row.get("pluginId") or ""): row for row in evidence_plugins
        }
        if (
            prior_stable_selector not in by_selector
            or by_selector[prior_stable_selector].get("enabled") is not True
            or fallback_selector not in by_selector
            or by_selector[fallback_selector].get("enabled") is not False
            or any(
                row.get("enabled") is not False
                for row in evidence_plugins
                if row.get("pluginId") != prior_stable_selector
            )
        ):
            raise InstallationError(
                "The sealed stable/fallback slots are not safe for an in-place update."
            )

    target_was_installed = any(
        row.get("pluginId") == plugin_selector for row in evidence_plugins
    )
    same_selector_refresh = prior_stable_selector == plugin_selector
    if target_was_installed and not same_selector_refresh:
        raise InstallationError(
            "The canonical Git selector is unexpectedly installed during legacy "
            "stable migration."
        )
    if same_selector_refresh and target_was_installed:
        _run_codex(
            executable,
            codex_home,
            ["plugin", "remove", plugin_selector, "--json"],
        )

    marketplace_list = _run_codex(
        executable,
        codex_home,
        ["plugin", "marketplace", "list", "--json"],
    )
    marketplaces = marketplace_list.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallationError("Codex marketplace list has an invalid shape.")
    marketplace_rows = [dict(row) for row in marketplaces if isinstance(row, dict)]
    target_marketplace_rows = [
        row for row in marketplace_rows if row.get("name") == marketplace_name
    ]
    if len(target_marketplace_rows) > 1:
        raise InstallationError(
            "Codex exposed more than one canonical Git marketplace identity."
        )
    target_marketplace_preexisting = bool(target_marketplace_rows)
    target_marketplace_source_verified = False
    if target_marketplace_preexisting:
        marketplace_source = dict(
            target_marketplace_rows[0].get("marketplaceSource") or {}
        )
        source_value = str(marketplace_source.get("source") or "").lower()
        if (
            marketplace_source.get("sourceType") != "git"
            or MARKETPLACE_SOURCE.lower() not in source_value
        ):
            raise InstallationError(
                "The pre-existing canonical marketplace is not the governed Git "
                "source."
            )
        target_marketplace_source_verified = True
    target_marketplace_removed = False
    legacy_selector_migration = (
        bool(prior_stable_selector) and prior_stable_selector != plugin_selector
    )
    if target_marketplace_preexisting and (
        same_selector_refresh or legacy_selector_migration
    ):
        _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "remove", marketplace_name, "--json"],
        )
        target_marketplace_removed = True

    receipt: dict[str, Any] = {
        "schema": "evidence-lane.codex-stable-in-place-update.v1",
        "stable_selector": plugin_selector,
        "stable_marketplace": marketplace_name,
        "prior_stable_selector": prior_stable_selector,
        "target_was_installed": target_was_installed,
        "one_time_legacy_selector_migration": legacy_selector_migration,
        "same_selector_refresh": same_selector_refresh,
        "stable_removed_for_same_selector_reinstall": (
            same_selector_refresh and target_was_installed
        ),
        "target_marketplace_removed_for_exact_ref_refresh": target_marketplace_removed,
        "target_marketplace_preexisting": target_marketplace_preexisting,
        "target_marketplace_source_verified": target_marketplace_source_verified,
        "fallback_selector": fallback_selector,
        "fallback_removed": False,
        "removed_obsolete_selectors": [],
        "removed_obsolete_marketplaces": [],
        "obsolete_cleanup_deferred_until_new_route_proof": True,
        "new_stable_selector_created": False,
        "generated_cache_written_directly": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_json_bytes(receipt)).hexdigest().upper()
    return receipt


def _cleanup_obsolete_after_new_route_proof(
    *,
    executable: Path,
    codex_home: Path,
    plugin_selector: str,
    fallback_selector: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove obsolete registrations only after the new route is proven live."""

    before = _run_codex(executable, codex_home, ["plugin", "list", "--json"])
    installed = before.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    by_selector = {str(row.get("pluginId") or ""): row for row in evidence}
    if (
        plugin_selector not in by_selector
        or by_selector[plugin_selector].get("enabled") is not True
        or fallback_selector not in by_selector
        or by_selector[fallback_selector].get("enabled") is not False
        or any(
            row.get("enabled") is not False
            for row in evidence
            if row.get("pluginId") != plugin_selector
        )
    ):
        raise InstallationError(
            "The canonical stable route is not exclusively proven before cleanup."
        )

    removed_selectors: list[str] = []
    for selector in sorted(set(by_selector) - {plugin_selector, fallback_selector}):
        _run_codex(executable, codex_home, ["plugin", "remove", selector, "--json"])
        removed_selectors.append(selector)

    fallback_marketplace = fallback_selector.split("@", 1)[1]
    marketplace_list = _run_codex(
        executable, codex_home, ["plugin", "marketplace", "list", "--json"]
    )
    marketplaces = marketplace_list.get("marketplaces")
    if not isinstance(marketplaces, list):
        raise InstallationError("Codex marketplace list has an invalid shape.")
    removed_marketplaces: list[str] = []
    for row in marketplaces:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        if (
            not name.startswith("evidence-lane-")
            or name in {MARKETPLACE_NAME, fallback_marketplace}
        ):
            continue
        _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "remove", name, "--json"],
        )
        removed_marketplaces.append(name)

    after = _run_codex(executable, codex_home, ["plugin", "list", "--json"])
    after_installed = after.get("installed")
    if not isinstance(after_installed, list):
        raise InstallationError("Codex plugin list did not expose final plugins.")
    final_evidence = [
        dict(row)
        for row in after_installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    final_by_selector = {
        str(row.get("pluginId") or ""): row for row in final_evidence
    }
    if (
        set(final_by_selector) != {plugin_selector, fallback_selector}
        or final_by_selector[plugin_selector].get("enabled") is not True
        or final_by_selector[fallback_selector].get("enabled") is not False
    ):
        raise InstallationError("Post-proof cleanup did not leave exactly two slots.")
    receipt = {
        "schema": "evidence-lane.codex-post-proof-slot-cleanup.v1",
        "status": "PASS",
        "canonical_stable_selector": plugin_selector,
        "fallback_selector": fallback_selector,
        "removed_obsolete_selectors": removed_selectors,
        "removed_obsolete_marketplaces": removed_marketplaces,
        "exact_installed_slot_count": 2,
        "enabled_slot_count": 1,
        "fallback_enabled": False,
        "generated_cache_deleted_directly": False,
    }
    receipt["receipt_sha256"] = hashlib.sha256(_json_bytes(receipt)).hexdigest().upper()
    return receipt, after


def _trust_sealed_plugin_hooks(
    *,
    executable: Path,
    codex_home: Path,
    data_root: Path,
    hook_cwd: Path,
    plugin_selector: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Trust only the exact installed selector's four current hook hashes.

    Codex intentionally treats each cache-busted plugin selector as a new hook
    authority.  Merely installing and enabling the plugin therefore does not
    make its unmanaged hooks runnable.  This helper uses the same supported
    app-server ``hooks/list`` and ``config/batchWrite`` route as Codex's hook
    review UI; it never edits ``config.toml`` directly and never trusts a hook
    outside the exact selector supplied by the caller.
    """

    resolved_cwd = hook_cwd.resolve()
    if not resolved_cwd.is_dir():
        raise InstallationError("The exact hook workspace is unavailable.")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    process = subprocess.Popen(
        [str(executable), "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=environment,
    )
    stdin = process.stdin
    stdout = process.stdout
    stderr = process.stderr
    if stdin is None or stdout is None or stderr is None:
        process.kill()
        raise InstallationError("Codex app-server stdio was not available.")

    responses: queue.Queue[dict[str, Any]] = queue.Queue()
    stderr_lines: list[str] = []

    def read_stdout() -> None:
        for line in stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                responses.put(payload)

    def read_stderr() -> None:
        for line in stderr:
            stderr_lines.append(line.rstrip())

    stdout_reader = threading.Thread(target=read_stdout, daemon=True)
    stderr_reader = threading.Thread(target=read_stderr, daemon=True)
    stdout_reader.start()
    stderr_reader.start()

    def send(payload: dict[str, Any]) -> None:
        try:
            stdin.write(
                json.dumps(payload, separators=(",", ":")) + "\n"
            )
            stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise InstallationError("Codex app-server closed unexpectedly.") from exc

    def wait_for(request_id: int, *, timeout: float = 20.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                payload = responses.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if payload.get("id") == request_id:
                if "error" in payload:
                    raise InstallationError(
                        "Codex app-server request failed: "
                        + json.dumps(payload["error"], sort_keys=True)
                    )
                return payload
        detail = " | ".join(stderr_lines[-3:])
        raise InstallationError(
            "Codex app-server did not return the required hook receipt"
            + (f": {detail}" if detail else ".")
        )

    def selector_hooks(reply: dict[str, Any]) -> list[dict[str, Any]]:
        data = dict(reply.get("result") or {}).get("data")
        if not isinstance(data, list):
            raise InstallationError("Codex hooks/list returned an invalid shape.")
        resolved_key = os.path.normcase(str(resolved_cwd))
        entries = [
            row
            for row in data
            if isinstance(row, dict)
            and os.path.normcase(str(Path(str(row.get("cwd") or "")).resolve()))
            == resolved_key
        ]
        if len(entries) != 1 or entries[0].get("errors"):
            raise InstallationError("Codex did not return one clean hook workspace.")
        hooks = [
            dict(row)
            for row in entries[0].get("hooks") or []
            if isinstance(row, dict) and row.get("pluginId") == plugin_selector
        ]
        events = {str(row.get("eventName") or "") for row in hooks}
        keys = [str(row.get("key") or "") for row in hooks]
        if (
            len(hooks) != 4
            or events != EXPECTED_CODEX_HOST_HOOK_EVENTS
            or len(keys) != len(set(keys))
            or any(
                row.get("source") != "plugin"
                or row.get("isManaged") is not False
                or row.get("enabled") is not True
                or not str(row.get("key") or "").startswith(
                    f"{plugin_selector}:"
                )
                or re.fullmatch(
                    r"sha256:[0-9a-f]{64}",
                    str(row.get("currentHash") or ""),
                )
                is None
                for row in hooks
            )
        ):
            raise InstallationError(
                "The exact installed selector's four-hook authority drifted."
            )
        return sorted(hooks, key=lambda row: str(row["eventName"]))

    try:
        send(
            {
                "method": "initialize",
                "id": 1000,
                "params": {
                    "clientInfo": {
                        "name": "evidence_lane_installer",
                        "title": "Evidence Lane Installer",
                        "version": BASE_RELEASE,
                    }
                },
            }
        )
        wait_for(1000)
        send({"method": "initialized", "params": {}})
        config_path = codex_home / "config.toml"
        config_raw = config_path.read_text(encoding="utf-8")
        parsed_config = tomllib.loads(config_raw)
        plugins = dict(parsed_config.get("plugins") or {})
        if plugin_selector not in plugins:
            raise InstallationError("Codex did not persist the v2 plugin selector.")
        changed_selectors: list[str] = []
        for selector, raw_settings in list(plugins.items()):
            if not selector.startswith(f"{PLUGIN_NAME}@"):
                continue
            settings = dict(raw_settings or {})
            desired = selector == plugin_selector
            servers = dict(settings.get("mcp_servers") or {})
            evidence_server = dict(servers.get("evidence-lane") or {})
            if (
                bool(settings.get("enabled")) is not desired
                or bool(evidence_server.get("enabled")) is not desired
            ):
                changed_selectors.append(selector)
            settings["enabled"] = desired
            evidence_server["enabled"] = desired
            servers["evidence-lane"] = evidence_server
            settings["mcp_servers"] = servers
            plugins[selector] = settings
        before_config_sha256 = hashlib.sha256(
            config_raw.encode("utf-8")
        ).hexdigest().upper()
        config_archive = (
            data_root
            / "installations"
            / "codex-v200"
            / "config-archives"
        )
        config_backup = config_archive / f"config-{before_config_sha256}.toml"
        if not config_backup.exists():
            _write_atomic(config_backup, config_raw.encode("utf-8"))
        send(
            {
                "method": "config/batchWrite",
                "id": 1001,
                "params": {
                    "edits": [
                        {
                            "keyPath": "plugins",
                            "value": plugins,
                            "mergeStrategy": "upsert",
                        }
                    ],
                    "filePath": None,
                    "expectedVersion": None,
                    "reloadUserConfig": True,
                },
            }
        )
        channel_reply = wait_for(1001)
        channel_result = dict(channel_reply.get("result") or {})
        channel_config_version = str(channel_result.get("version") or "")
        if (
            channel_result.get("status") != "ok"
            or re.fullmatch(
                r"sha256:[0-9a-f]{64}", channel_config_version
            )
            is None
        ):
            raise InstallationError("Codex did not seal the exclusive channel write.")
        verified_plugins = dict(
            tomllib.loads(config_path.read_text(encoding="utf-8")).get("plugins")
            or {}
        )
        for selector, settings in verified_plugins.items():
            if not selector.startswith(f"{PLUGIN_NAME}@"):
                continue
            desired = selector == plugin_selector
            mcp = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
            if (
                bool(settings.get("enabled")) is not desired
                or not isinstance(mcp, dict)
                or bool(mcp.get("enabled")) is not desired
            ):
                raise InstallationError(
                    "The supported exclusive Evidence Lane channel write did not persist."
                )
        config_receipt = {
            "before_sha256": before_config_sha256,
            "after_sha256": _sha256(config_path),
            "backup": str(config_backup),
            "changed_selectors": sorted(set(changed_selectors)),
            "supported_codex_api": "config/batchWrite",
            "config_version": channel_config_version,
            "previous_release_cache_deleted": False,
        }
        send(
            {
                "method": "hooks/list",
                "id": 1002,
                "params": {"cwds": [str(resolved_cwd)]},
            }
        )
        before = selector_hooks(wait_for(1002))
        before_statuses = sorted(
            {str(row.get("trustStatus") or "unknown") for row in before}
        )
        trust_value = {
            str(row["key"]): {"trusted_hash": str(row["currentHash"])}
            for row in before
        }
        send(
            {
                "method": "config/batchWrite",
                "id": 1003,
                "params": {
                    "edits": [
                        {
                            "keyPath": "hooks.state",
                            "value": trust_value,
                            "mergeStrategy": "upsert",
                        }
                    ],
                    "filePath": None,
                    "expectedVersion": None,
                    "reloadUserConfig": True,
                },
            }
        )
        write_reply = wait_for(1003)
        write_result = dict(write_reply.get("result") or {})
        config_version = str(write_result.get("version") or "")
        if (
            write_result.get("status") != "ok"
            or re.fullmatch(r"sha256:[0-9a-f]{64}", config_version) is None
        ):
            raise InstallationError("Codex did not seal the hook-trust write.")
        send(
            {
                "method": "hooks/list",
                "id": 1004,
                "params": {"cwds": [str(resolved_cwd)]},
            }
        )
        after = selector_hooks(wait_for(1004))
        if any(row.get("trustStatus") != "trusted" for row in after):
            raise InstallationError("The exact installed hooks remain untrusted.")
        records = [
            {
                "event_name": str(row["eventName"]),
                "hook_key": str(row["key"]),
                "current_hash": str(row["currentHash"]),
                "enabled": True,
                "trust_status": "trusted",
            }
            for row in after
        ]
        receipt = {
            "schema": HOOK_TRUST_SCHEMA,
            "status": "PASS",
            "plugin_selector": plugin_selector,
            "hook_count": len(records),
            "registered_events": sorted(EXPECTED_CODEX_HOST_HOOK_EVENTS),
            "records": records,
            "before_trust_statuses": before_statuses,
            "after_trust_statuses": ["trusted"],
            "supported_codex_api": ["hooks/list", "config/batchWrite"],
            "config_version": config_version,
            "workspace_sha256": hashlib.sha256(
                os.path.normcase(str(resolved_cwd)).encode("utf-8")
            ).hexdigest().upper(),
            "raw_workspace_path_included": False,
            "hook_commands_included": False,
            "source_paths_included": False,
            "unrelated_hook_state_mutated": False,
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            _json_bytes(receipt)
        ).hexdigest().upper()
        return receipt, config_receipt
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def _ordered_json_sha256(value: Any) -> str:
    """Match the v1 two-slot registry's preserved-property-order seal."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest().upper()


def _load_two_slot_update_authority(
    *,
    data_root: Path,
    comparison_baseline: dict[str, Any] | None,
) -> dict[str, Any] | None:
    registry_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "CODEX_TWO_SLOT_REGISTRY.json"
    )
    if not registry_path.is_file():
        return None
    if comparison_baseline is None:
        raise InstallationError(
            "The materialized two-slot registry requires the exact prior stable baseline."
        )
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise InstallationError("The two-slot registry is not a JSON object.")
    without_seal = {key: value for key, value in registry.items() if key != "seal"}
    without_internal_hashes = {
        key: value
        for key, value in registry.items()
        if key not in {"registry_body_sha256", "seal"}
    }
    seal = dict(registry.get("seal") or {})
    slots = dict(registry.get("slots") or {})
    stable = dict(slots.get("stable-build") or {})
    fallback = dict(slots.get("fallback") or {})
    baseline_path = Path(str(comparison_baseline.get("installation_receipt") or ""))
    stable_install_path = Path(str(stable.get("install_receipt") or ""))
    fallback_install_path = Path(str(fallback.get("install_receipt") or ""))
    fallback_install_sha256 = str(fallback.get("install_receipt_sha256") or "")
    if (
        registry.get("schema") != "evidence-lane.codex-two-slot-registry.v1"
        or registry.get("status") != "PASS"
        or registry.get("state") != "STABLE_ACTIVE_FALLBACK_PREWARMED_DISABLED"
        or registry.get("post_fuse_materialized") is not True
        or registry.get("accepted_pv") != "PV11"
        or registry.get("accepted_generation") != 11
        or registry.get("exact_live_slot_count") != 2
        or registry.get("max_enabled_plugin_count") != 1
        or set(slots) != {"stable-build", "fallback"}
        or seal.get("algorithm") != "SHA256"
        or seal.get("body_sha256") != _ordered_json_sha256(without_seal)
        or registry.get("registry_body_sha256")
        != _ordered_json_sha256(without_internal_hashes)
        or stable.get("slot_role") != "stable-build"
        or stable.get("byte_frozen") is not False
        or stable.get("enabled") is not True
        or stable.get("native_mcp_enabled") is not True
        or not stable_install_path.is_file()
        or stable_install_path.resolve() != baseline_path.resolve()
        or stable.get("install_receipt_sha256")
        != comparison_baseline.get("installation_receipt_sha256")
        or _sha256(stable_install_path)
        != comparison_baseline.get("installation_receipt_sha256")
        or fallback.get("slot_role") != "fallback"
        or fallback.get("byte_frozen") is not True
        or fallback.get("enabled") is not False
        or fallback.get("native_mcp_enabled") is not False
        or fallback.get("accepted_pv") != "PV11"
        or fallback.get("accepted_generation") != 11
        or fallback.get("package_sha256")
        != registry.get("accepted_package_sha256")
        or not fallback_install_path.is_file()
        or re.fullmatch(r"[A-F0-9]{64}", fallback_install_sha256) is None
        or _sha256(fallback_install_path) != fallback_install_sha256
    ):
        raise InstallationError(
            "The exact stable/fallback registry is not eligible for stable advancement."
        )
    return {
        "path": registry_path,
        "before_file_sha256": _sha256(registry_path),
        "registry": registry,
        "prior_stable_selector": stable.get("plugin_selector"),
        "fallback_snapshot_sha256": hashlib.sha256(
            _json_bytes(fallback)
        ).hexdigest().upper(),
    }


def _advance_two_slot_stable_registry(
    *,
    authority: dict[str, Any] | None,
    plugin_selector: str,
    plugin_version: str,
    installed_path: Path,
    marketplace_root: Path,
    install_receipt: Path,
    archive_sha256: str,
    source_manifest_sha256: str,
    codex_home: Path,
    data_root: Path,
    plugin_list: dict[str, Any],
) -> dict[str, Any]:
    if authority is None:
        return {
            "status": "NOT_APPLICABLE",
            "reason": "POST_FUSE_TWO_SLOT_REGISTRY_NOT_MATERIALIZED",
        }
    installed = plugin_list.get("installed")
    if not isinstance(installed, list):
        raise InstallationError("Codex plugin list did not expose installed plugins.")
    evidence_plugins = [
        dict(row)
        for row in installed
        if isinstance(row, dict)
        and str(row.get("pluginId") or "").startswith(f"{PLUGIN_NAME}@")
    ]
    enabled = [row for row in evidence_plugins if row.get("enabled") is True]
    registry = dict(authority["registry"])
    slots = dict(registry["slots"])
    fallback = dict(slots["fallback"])
    fallback_selector = str(fallback.get("plugin_selector") or "")
    prior_stable_selector = str(authority.get("prior_stable_selector") or "")
    canonical_migration = (
        prior_stable_selector != plugin_selector
        and plugin_selector == PLUGIN_SELECTOR
        and prior_stable_selector.startswith(f"{PLUGIN_NAME}@evidence-lane-v200-")
    )
    by_selector = {
        str(row.get("pluginId") or ""): row for row in evidence_plugins
    }
    if (
        (plugin_selector != prior_stable_selector and not canonical_migration)
        or set(by_selector) != {plugin_selector, fallback_selector}
        or len(enabled) != 1
        or enabled[0].get("pluginId") != plugin_selector
        or plugin_selector not in by_selector
        or fallback_selector not in by_selector
        or by_selector[fallback_selector].get("enabled") is not False
        or any(
            row.get("enabled") is not False
            for row in evidence_plugins
            if row.get("pluginId") != plugin_selector
        )
    ):
        raise InstallationError(
            "Codex does not expose one stable enabled and one fallback disabled."
        )
    config_path = codex_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    plugin_config = dict(config.get("plugins") or {})
    for selector, settings in plugin_config.items():
        if not selector.startswith(f"{PLUGIN_NAME}@"):
            continue
        expected = selector == plugin_selector
        mcp = dict(settings.get("mcp_servers") or {}).get("evidence-lane")
        if (
            bool(settings.get("enabled")) is not expected
            or not isinstance(mcp, dict)
            or bool(mcp.get("enabled")) is not expected
        ):
            raise InstallationError(
                "The plugin and native MCP activation are not exactly exclusive."
            )
    expected_cache = (
        codex_home
        / "plugins"
        / "cache"
        / plugin_selector.split("@", 1)[1]
        / PLUGIN_NAME
    )
    if (
        not installed_path.is_dir()
        or not _inside(installed_path, expected_cache)
        or not install_receipt.is_file()
        or re.fullmatch(r"[A-F0-9]{64}", source_manifest_sha256) is None
    ):
        raise InstallationError("The new stable slot authority is incomplete.")
    marketplace_catalog = marketplace_root / ".agents" / "plugins" / "marketplace.json"
    plugin_manifest = installed_path / ".codex-plugin" / "plugin.json"
    if not marketplace_catalog.is_file() or not plugin_manifest.is_file():
        raise InstallationError("The new stable marketplace identity is incomplete.")

    stable = dict(slots["stable-build"])
    stable.update(
        {
            "byte_frozen": False,
            "cache_authority_manifest_sha256": source_manifest_sha256,
            "cache_root": str(installed_path),
            "enabled": True,
            "install_receipt": str(install_receipt),
            "install_receipt_sha256": _sha256(install_receipt),
            "marketplace_catalog_sha256": _sha256(marketplace_catalog),
            "marketplace_root": str(marketplace_root),
            "native_mcp_enabled": True,
            "package_sha256": archive_sha256,
            "plugin_manifest_sha256": _sha256(plugin_manifest),
            "plugin_selector": plugin_selector,
            "plugin_version": plugin_version,
            "slot_role": "stable-build",
        }
    )
    slots["stable-build"] = stable
    slots["fallback"] = fallback
    registry["slots"] = slots
    registry["active_slot"] = "stable-build"
    registry["state"] = "STABLE_ACTIVE_FALLBACK_PREWARMED_DISABLED"
    registry["live_registered_selectors"] = sorted(by_selector)
    registry["exact_registered_plugin_count"] = 2
    registry["activation_proof"] = {
        "command": "codex plugin list --json",
        "config_path": str(config_path),
        "config_sha256": _sha256(config_path),
        "fallback_native_mcp_enabled": False,
        "fallback_plugin_enabled": False,
        "plugin_list_json_utf8_sha256": hashlib.sha256(
            _json_bytes(plugin_list)
        ).hexdigest().upper(),
        "plugin_list_json_canonicalized": True,
        "stable_native_mcp_enabled": True,
        "stable_plugin_enabled": True,
    }
    fallback_snapshot_sha256 = hashlib.sha256(
        _json_bytes(fallback)
    ).hexdigest().upper()
    if fallback_snapshot_sha256 != authority["fallback_snapshot_sha256"]:
        raise InstallationError("The immutable fallback registry object changed.")
    registry["registry_body_sha256"] = _ordered_json_sha256(
        {
            key: value
            for key, value in registry.items()
            if key not in {"registry_body_sha256", "seal"}
        }
    )
    seal = dict(registry.get("seal") or {})
    seal["body_sha256"] = _ordered_json_sha256(
        {key: value for key, value in registry.items() if key != "seal"}
    )
    registry["seal"] = seal
    registry_path = Path(authority["path"])
    _write_atomic(
        registry_path,
        (
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8"),
    )
    after_file_sha256 = _sha256(registry_path)
    update = {
        "schema": "evidence-lane.codex-two-slot-stable-update.v1",
        "status": "PASS",
        "before_registry_file_sha256": authority["before_file_sha256"],
        "after_registry_file_sha256": after_file_sha256,
        "registry_body_sha256": registry["registry_body_sha256"],
        "registry_seal_body_sha256": seal["body_sha256"],
        "prior_stable_selector": authority["prior_stable_selector"],
        "current_stable_selector": plugin_selector,
        "stable_selector_reused": (
            authority["prior_stable_selector"] == plugin_selector
        ),
        "stable_selector_migrated_to_canonical_git": canonical_migration,
        "same_stable_selector_required_after_migration": True,
        "legacy_stable_removed_after_new_route_proof": canonical_migration,
        "new_stable_selector_created": False,
        "current_stable_install_receipt_sha256": _sha256(install_receipt),
        "fallback_selector": fallback_selector,
        "fallback_snapshot_sha256": fallback_snapshot_sha256,
        "fallback_enabled": False,
        "fallback_byte_frozen": True,
        "enabled_plugin_count": 1,
        "registered_plugin_count": 2,
        "active_tunnel_count": 0,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_invoked": False,
    }
    update["receipt_sha256"] = hashlib.sha256(
        _json_bytes(update)
    ).hexdigest().upper()
    update_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / f"STABLE_REGISTRY_UPDATE_{archive_sha256[:16]}.json"
    )
    _write_atomic(update_path, _json_bytes(update))
    return {
        **update,
        "registry_path": str(registry_path),
        "receipt_path": str(update_path),
        "receipt_file_sha256": _sha256(update_path),
    }


def install(args: argparse.Namespace) -> dict[str, Any]:
    archive = args.archive.resolve()
    receipt_argument = getattr(args, "package_receipt", None) or getattr(
        args, "rehearsal_receipt", None
    )
    if receipt_argument is None:
        raise InstallationError("An exact package receipt is required.")
    receipt_path = Path(receipt_argument).resolve()
    codex_home = args.codex_home.resolve()
    data_root = args.data_root.resolve()
    requested_marketplace_name = str(
        getattr(args, "marketplace_name", MARKETPLACE_NAME)
    )
    trust_sealed_hooks = bool(getattr(args, "trust_sealed_hooks", False))
    if bool(args.activate) != trust_sealed_hooks:
        raise InstallationError(
            "Activation and explicit sealed-hook trust must be requested together."
        )
    if _inside(archive, codex_home / "plugins" / "cache"):
        raise InstallationError("A generated cache package cannot be installation input.")
    rehearsal = _load_receipt(
        receipt_path,
        archive,
        activation=bool(args.activate),
    )
    release_authority_path = getattr(args, "release_authority_receipt", None)
    release_authority_file_sha256 = getattr(
        args, "release_authority_receipt_sha256", None
    )
    if (release_authority_path is None) != (
        release_authority_file_sha256 is None
    ):
        raise InstallationError(
            "The governed Git/CI/Vercel release-authority receipt and file SHA-256 "
            "must be supplied together."
        )
    release_authority: dict[str, Any] | None = None
    if args.activate:
        if release_authority_path is None:
            raise InstallationError(
                "A local rehearsal may be staged but activation requires the "
                "governed Git/CI/Vercel release-authority receipt."
            )
        release_authority = _load_release_authority(
            authority_path=Path(release_authority_path).resolve(),
            authority_file_sha256=str(release_authority_file_sha256),
            archive=archive,
            package_receipt_path=receipt_path,
            package_receipt=rehearsal,
        )
    baseline_path = getattr(args, "baseline_installation_receipt", None)
    baseline_sha256 = getattr(
        args, "baseline_installation_receipt_sha256", None
    )
    if (baseline_path is None) != (baseline_sha256 is None):
        raise InstallationError(
            "The comparison baseline receipt and SHA-256 must be supplied together."
        )
    comparison_surface: dict[str, Any] | None = None
    comparison_baseline: dict[str, Any] | None = None
    if baseline_path is not None:
        comparison_surface, comparison_baseline = _load_comparison_baseline(
            path=Path(baseline_path),
            expected_sha256=str(baseline_sha256),
            data_root=data_root,
        )
    two_slot_authority = _load_two_slot_update_authority(
        data_root=data_root,
        comparison_baseline=comparison_baseline,
    )
    marketplace_name = _resolve_stable_marketplace_name(
        requested_name=requested_marketplace_name,
        two_slot_authority=two_slot_authority,
    )
    if marketplace_name != MARKETPLACE_NAME:
        raise InstallationError(
            "The stable marketplace name must be the canonical GitLane slot."
        )
    plugin_selector = f"{PLUGIN_NAME}@{marketplace_name}"
    marketplace_root = codex_home / "local-marketplaces" / marketplace_name
    extracted_inventory: dict[str, Any]
    with tempfile.TemporaryDirectory(prefix="evidence-lane-v200-install-") as raw:
        extracted = Path(raw) / "plugin"
        extracted.mkdir()
        _safe_extract(archive, extracted)
        identity = _validate_plugin(extracted)
        extracted_inventory = _source_inventory(extracted)
        if args.activate:
            stage = {
                "schema": "evidence-lane.codex-git-marketplace-stage.v1",
                "state": "EXACT_COMMIT_GIT_MARKETPLACE_PENDING",
                "marketplace": MARKETPLACE_NAME,
                "marketplace_display_name": MARKETPLACE_DISPLAY_NAME,
                "repository": MARKETPLACE_SOURCE,
                "plugin": identity,
                "archive_sha256": _sha256(archive),
                "comparison_baseline": comparison_baseline,
                "surface_change_display": _surface_change_display(
                    previous=comparison_surface,
                    current=identity["surface_inventory"],
                ),
                "exact_commit_package_inventory_sha256": extracted_inventory[
                    "manifest_sha256"
                ],
                "local_marketplace_staged": False,
                "generated_cache_written_directly": False,
                "prior_release_deleted": False,
            }
        else:
            stage = _stage_marketplace(
                extracted=extracted,
                marketplace_root=marketplace_root,
                data_root=data_root,
                identity=identity,
                archive_sha256=_sha256(archive),
                marketplace_name=marketplace_name,
                comparison_surface=comparison_surface,
                comparison_baseline=comparison_baseline,
            )
    activation: dict[str, Any] = {
        "state": "STAGED_RESTART_NOT_YET_REQUIRED",
        "plugin_add_invoked": False,
    }
    config_receipt: dict[str, Any] | None = None
    plugin_list: dict[str, Any] | None = None
    installed_path: Path | None = None
    in_place_update: dict[str, Any] | None = None
    runtime_prewarm: dict[str, Any] | None = None
    git_marketplace_source: dict[str, Any] | None = None
    post_proof_cleanup: dict[str, Any] | None = None
    if args.activate:
        executable = args.codex_executable.resolve()
        if not executable.is_file():
            raise InstallationError("The exact Codex executable is unavailable.")
        if two_slot_authority is None:
            raise InstallationError(
                "Activation requires the materialized stable/fallback two-slot authority."
            )
        in_place_update = _prepare_in_place_stable_reinstall(
            executable=executable,
            codex_home=codex_home,
            plugin_selector=plugin_selector,
            marketplace_name=marketplace_name,
            two_slot_authority=two_slot_authority,
        )
        listed = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "list", "--json"],
        )
        known = {row["name"]: Path(row["root"]).resolve() for row in listed["marketplaces"]}
        if marketplace_name in known:
            raise InstallationError(
                "The canonical Git marketplace still exists after update preflight."
            )
        if release_authority is None:
            raise InstallationError("The exact release authority was not loaded.")
        source_commit = str(release_authority["source"]["commit"])
        marketplace_add = _run_codex(
            executable,
            codex_home,
            [
                "plugin",
                "marketplace",
                "add",
                MARKETPLACE_SOURCE,
                "--ref",
                source_commit,
                "--json",
            ],
        )
        refreshed_marketplaces = _run_codex(
            executable,
            codex_home,
            ["plugin", "marketplace", "list", "--json"],
        )
        refreshed_rows = refreshed_marketplaces.get("marketplaces")
        if not isinstance(refreshed_rows, list):
            raise InstallationError("Codex marketplace list has an invalid shape.")
        exact_rows = [
            dict(row)
            for row in refreshed_rows
            if isinstance(row, dict) and row.get("name") == MARKETPLACE_NAME
        ]
        if len(exact_rows) != 1:
            raise InstallationError("Codex did not configure one canonical Git marketplace.")
        marketplace_root = Path(str(exact_rows[0].get("root") or "")).resolve()
        git_marketplace_source = _assert_exact_git_marketplace_source(
            extracted_inventory=extracted_inventory,
            marketplace_root=marketplace_root,
            expected_git_manifest_sha256=str(
                rehearsal.get("exact_commit_export", {}).get(
                    "plugin_source_manifest_sha256"
                )
                or ""
            ),
            expected_git_file_count=int(
                rehearsal.get("exact_commit_export", {}).get(
                    "plugin_source_member_count"
                )
                or 0
            ),
        )
        git_marketplace_source["commit"] = source_commit
        plugin_add = _run_codex(
            executable,
            codex_home,
            ["plugin", "add", plugin_selector, "--json"],
        )
        installed_path = Path(str(plugin_add.get("installedPath") or "")).resolve()
        expected_cache = (
            codex_home / "plugins" / "cache" / marketplace_name / PLUGIN_NAME
        )
        if (
            plugin_add.get("pluginId") != plugin_selector
            or plugin_add.get("version") != identity["version"]
            or not _inside(installed_path, expected_cache)
        ):
            raise InstallationError("Codex installed a mismatched plugin cache identity.")
        runtime_prewarm = _prewarm_installed_runtime(installed_path)
        hook_trust, config_receipt = _trust_sealed_plugin_hooks(
            executable=executable,
            codex_home=codex_home,
            data_root=data_root,
            hook_cwd=Path(getattr(args, "hook_cwd", Path.cwd())),
            plugin_selector=plugin_selector,
        )
        plugin_list = _run_codex(
            executable,
            codex_home,
            ["plugin", "list", "--json"],
        )
        installed_rows = plugin_list.get("installed")
        if not isinstance(installed_rows, list):
            raise InstallationError("Codex plugin list did not expose installed plugins.")
        exact_installed = [
            dict(row)
            for row in installed_rows
            if isinstance(row, dict) and row.get("pluginId") == plugin_selector
        ]
        if len(exact_installed) != 1:
            raise InstallationError("Codex did not expose one canonical installed plugin.")
        marketplace_source = dict(exact_installed[0].get("marketplaceSource") or {})
        if (
            marketplace_source.get("sourceType") != "git"
            or MARKETPLACE_SOURCE.lower()
            not in str(marketplace_source.get("source") or "").lower()
        ):
            raise InstallationError("The installed stable route is not Git-backed.")
        fallback_selector = str(
            (two_slot_authority["registry"]["slots"]["fallback"] or {}).get(
                "plugin_selector"
            )
            or ""
        )
        post_proof_cleanup, plugin_list = _cleanup_obsolete_after_new_route_proof(
            executable=executable,
            codex_home=codex_home,
            plugin_selector=plugin_selector,
            fallback_selector=fallback_selector,
        )
        activation = {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add_invoked": True,
            "marketplace_add": marketplace_add,
            "plugin_add": plugin_add,
            "exclusive_channel": config_receipt,
            "hook_trust": hook_trust,
            "in_place_update": in_place_update,
            "runtime_prewarm": runtime_prewarm,
            "git_marketplace_source": git_marketplace_source,
            "post_proof_cleanup": post_proof_cleanup,
            "runtime_ready_before_task_reopen": True,
            "prompt_capture_runnable_after_restart": True,
            "hot_reload_claimed": False,
        }
    body = {
        "schema": INSTALL_SCHEMA,
        "status": "PASS",
        "plugin": identity,
        "catalog_expected": dict(EXPECTED_CATALOG),
        "package_receipt_sha256": _sha256(receipt_path),
        "activation_authority": (
            {
                "status": release_authority["status"],
                "boundary": release_authority["boundary"],
                "source_commit": release_authority["source"]["commit"],
                "source_tree": release_authority["source"]["tree"],
                "branch": release_authority["source"]["branch"],
                "github_repository": release_authority["github_ci"]["repository"],
                "vercel_preview_deployment_id": release_authority[
                    "vercel_preview"
                ]["deployment_id"],
                "vercel_preview_url": release_authority["vercel_preview"]["url"],
                "vercel_preview_ready": True,
                "production_deployment": False,
                "receipt_sha256": release_authority["receipt_sha256"],
                "receipt_file_sha256": str(
                    release_authority_file_sha256 or ""
                ).upper(),
            }
            if release_authority is not None
            else {
                "status": "NOT_APPLICABLE",
                "reason": "STAGING_ONLY_LOCAL_REHEARSAL",
            }
        ),
        "archive_sha256": _sha256(archive),
        "marketplace": {
            "name": marketplace_name,
            "root": str(marketplace_root),
            **stage,
        },
        "surface_change_display": stage["surface_change_display"],
        "comparison_baseline": stage.get("comparison_baseline"),
        "activation": activation,
        "fallback_materialization_gate": (
            "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
        ),
        "fallback_materialized": False,
        "live_cache_cleanup_deferred_until_exact_pv11_acceptance": True,
        "two_slot_operator_packaged": True,
        "previous_release_cache_deleted": False,
        "stable_selector_reused": bool(
            two_slot_authority is not None
            and two_slot_authority.get("prior_stable_selector") == plugin_selector
        ),
        "stable_selector_migrated_to_canonical_git": bool(
            two_slot_authority is not None
            and two_slot_authority.get("prior_stable_selector") != plugin_selector
        ),
        "post_proof_obsolete_cleanup_completed": bool(
            post_proof_cleanup and post_proof_cleanup.get("status") == "PASS"
        ),
        "obsolete_cleanup_used_supported_codex_apis": bool(args.activate),
        "stable_build_identity_location": "SEALED_INSTALL_RECEIPT_NOT_SELECTOR",
        "new_stable_selector_created": False,
        "generated_cache_written_directly": False,
        "credential_requested_or_stored": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
        "restart_required": bool(args.activate),
        "runtime_ready_before_task_reopen": bool(
            runtime_prewarm
            and runtime_prewarm.get("runtime_ready_before_task_reopen") is True
        ),
    }
    body["receipt_sha256"] = hashlib.sha256(_json_bytes(body)).hexdigest().upper()
    receipt_dir = data_root / "installations" / "codex-v200"
    install_receipt = receipt_dir / f"INSTALL_{body['archive_sha256'][:16]}.json"
    _write_atomic(install_receipt, _json_bytes(body))
    _write_atomic(receipt_dir / "CURRENT_INSTALLATION.json", _json_bytes(body))
    two_slot_update = (
        _advance_two_slot_stable_registry(
            authority=two_slot_authority,
            plugin_selector=plugin_selector,
            plugin_version=str(identity["version"]),
            installed_path=installed_path,
            marketplace_root=marketplace_root,
            install_receipt=install_receipt,
            archive_sha256=str(body["archive_sha256"]),
            source_manifest_sha256=str(
                rehearsal.get("working_source_manifest_sha256") or ""
            ),
            codex_home=codex_home,
            data_root=data_root,
            plugin_list=plugin_list,
        )
        if args.activate and installed_path is not None and plugin_list is not None
        else {
            "status": "NOT_APPLICABLE",
            "reason": "STAGING_PASS_ONLY",
        }
    )
    return {
        **body,
        "receipt_path": str(install_receipt),
        "two_slot_registry_update": two_slot_update,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    package = parser.add_mutually_exclusive_group(required=True)
    package.add_argument("--package-receipt", type=Path)
    package.add_argument(
        "--rehearsal-receipt",
        type=Path,
        help="Compatibility name for staging-only local rehearsal receipts.",
    )
    parser.add_argument(
        "--release-authority-receipt",
        type=Path,
        help=(
            "Sealed exact-commit governed-push and successful-GitHub-CI "
            "authority. Required for activation; never required for staging."
        ),
    )
    parser.add_argument("--release-authority-receipt-sha256")
    parser.add_argument("--baseline-installation-receipt", type=Path)
    parser.add_argument("--baseline-installation-receipt-sha256")
    parser.add_argument(
        "--marketplace-name",
        default=MARKETPLACE_NAME,
        help=(
            "Compatibility input for the one stable marketplace selector. "
            "A different build-specific selector is rejected; after PV11 the "
            "sealed two-slot registry supplies the persistent stable identity."
        ),
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"),
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path(
            os.environ.get("EVIDENCE_LANE_DATA_ROOT")
            or Path.home() / "EvidenceLanePV"
        ),
    )
    parser.add_argument("--codex-executable", type=Path)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument(
        "--trust-sealed-hooks",
        action="store_true",
        help=(
            "Trust exactly the installed selector's four current hook hashes "
            "through Codex hooks/list plus config/batchWrite. Required with --activate."
        ),
    )
    parser.add_argument(
        "--hook-cwd",
        type=Path,
        default=Path.cwd(),
        help="Exact governed workspace used for Codex hook discovery.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.activate and args.codex_executable is None:
        raise InstallationError("--activate requires --codex-executable.")
    if args.activate != args.trust_sealed_hooks:
        raise InstallationError(
            "--activate and --trust-sealed-hooks must be supplied together."
        )
    if args.activate and (
        args.release_authority_receipt is None
        or args.release_authority_receipt_sha256 is None
    ):
        raise InstallationError(
            "--activate requires the governed Git/CI/Vercel release-authority receipt "
            "and its file SHA-256."
        )
    print(json.dumps(install(args), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
