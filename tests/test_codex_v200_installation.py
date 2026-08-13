from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import queue
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
SCRIPT = PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py"
RESTART = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Restart-EvidenceLaneCodex.ps1"
)
STABLE_UPDATE = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Update-EvidenceLaneCodexStableAndResume.ps1"
)
GOAL_RECOVERY = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)
ACCEPTANCE = PLUGIN / "scripts" / "codex_release" / "accept_codex_stable.py"
HOOK_NOTICE_MARKERS = {
    "lifecycle_boundary.py": "EVIDENCE_LANE_LIFECYCLE_BOUNDARY=",
    "session_start.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
    "prompt_submit.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
    "pre_tool_use.py": "EVIDENCE_LANE_PRE_TOOL_USE=",
    "post_tool_use.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=",
    "stop_response.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
}


def _module():
    spec = importlib.util.spec_from_file_location("install_codex_stable", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _acceptance_module():
    spec = importlib.util.spec_from_file_location("accept_codex_stable", ACCEPTANCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _fixture_hook_source(marker: str) -> str:
    return (
        "persistent_change_system_notice = None\n"
        "persistent_change_system_message = None\n"
        "result = {}\n"
        'result["systemMessage"] = ""\n'
        f'print("{marker}")\n'
    )


def _fixture_catalog_source() -> str:
    functions = ["_READ_ONLY = object()", "_WRITE = object()"]
    for index in range(62):
        annotation = "_READ_ONLY" if index < 21 else "_WRITE"
        functions.extend(
            [
                f'@server.tool(name="tool_{index:02d}", annotations={annotation})',
                f"def tool_{index:02d}():",
                "    return None",
                "",
            ]
        )
    return "\n".join(functions)


def _fixture_archive(tmp_path: Path) -> tuple[Path, Path, str]:
    installer = _module()
    source = tmp_path / "source"
    version = "2.1.0+codex.20260812193232"
    _write(
        source / ".codex-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "evidence-lane-plugin",
                "version": version,
                "mcpServers": "./.mcp.json",
                "interface": {
                    "displayName": "Evidence Lane",
                    "composerIcon": "./assets/evidence-lane-icon.png",
                    "logo": "./assets/evidence-lane-icon.png",
                },
            }
        ),
    )
    (source / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / "assets" / "evidence-lane-icon.png",
        source / "assets" / "evidence-lane-icon.png",
    )
    _write(
        source / ".mcp.json",
        json.dumps({"mcpServers": {"evidence-lane": {"command": "python"}}}),
    )
    _write(
        source / "scripts" / "codex-release-channel.json",
        json.dumps(
            {
                "schema": "evidence-lane.codex-release-channel.v2",
                "stable": {
                    "release": "2.1.0",
                    "slot_role": "stable-build",
                    "codex_marketplace_slot": "evidence-lane-github",
                    "marketplace_display_name": "GitLane Stable 2.1",
                    "install_source": "GIT_EXACT_COMMIT",
                    "byte_frozen": False,
                    "updates_require_verified_unique_build_identity": True,
                    "stable_selector_is_persistent": True,
                    "stable_updates_reinstall_in_place": True,
                    "build_identity_is_receipt_not_selector": True,
                    "native_tool_count": 62,
                    "native_read_tool_count": 21,
                    "native_write_tool_count": 41,
                    "skill_count": 15,
                    "codex_apps_allowed": False,
                    "generated_namespace_allowed": False,
                    "direct_stdio_fallback_allowed": False,
                    "google_drive_bundled": False,
                },
                "fallback": {
                    "release": "2.0.0",
                    "slot_role": "fallback",
                    "codex_marketplace_slot": "evidence-lane-pv11-fallback",
                    "enabled": False,
                    "materialization_gate": (
                        "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
                    ),
                    "accepted_pv": "PV11",
                    "accepted_generation": 11,
                    "byte_frozen": True,
                    "package_must_equal_accepted_pv": True,
                    "prewarmed_means_installed_verified_and_stopped": True,
                    "simultaneous_mcp_allowed": False,
                    "simultaneous_tunnel_allowed": False,
                },
                "live_slot_policy": {
                    "exact_slot_count_after_pv11_acceptance": 2,
                    "allowed_slots": ["stable-build", "fallback"],
                    "max_enabled_plugin_count": 1,
                    "exact_registered_plugin_count": 2,
                    "stable_selector_growth_allowed": False,
                    "max_active_native_mcp_count": 1,
                    "max_active_tunnel_count": 1,
                    "inactive_slot_remains_installed": True,
                    "manual_loaded_cache_deletion_allowed": False,
                },
                "failover_operator": {
                    "script": (
                        "scripts/codex_release/"
                        "Switch-EvidenceLaneCodexSlot.ps1"
                    ),
                    "registry_schema": "evidence-lane.codex-two-slot-registry.v1",
                    "single_transient_error_switch_allowed": False,
                    "stop_source_tunnel_before_start_target": True,
                    "target_tunnel_ready_before_plugin_switch": True,
                    "controlled_exact_task_restart_required": True,
                    "switch_failure_restores_source_slot": True,
                },
                "goal_recovery": {
                    "script": (
                        "scripts/codex_release/"
                        "Manage-EvidenceLaneCodexGoalRecovery.ps1"
                    ),
                    "scope": (
                        "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_"
                        "ON_THIS_WINDOWS_USER"
                    ),
                    "trigger": "AT_LOGON_CURRENT_WINDOWS_USER",
                    "exact_task_uuid_required": True,
                    "exact_host_app_binding_required": True,
                    "supported_host_app_ids": [
                        "OpenAI.Codex_2p2nqsd0c76g0!App",
                        "OpenAI.CodexBeta_2p2nqsd0c76g0!App",
                    ],
                    "persisted_goal_read_route": (
                        "CODEX_APP_SERVER_THREAD_READ_PLUS_THREAD_GOAL_GET"
                    ),
                    "thread_resume_writer_allowed": False,
                    "synthetic_prompt_allowed": False,
                    "turn_start_allowed": False,
                    "state_travel_allowed": False,
                    "candidate_hil_pointer_or_git_mutation_allowed": False,
                    "requires_stable_enabled_fallback_disabled": True,
                    "stable_selector_growth_allowed": False,
                    "raw_goal_objective_stored": False,
                },
                "behavior_ownership": installer.EXPECTED_BEHAVIOR_OWNERSHIP,
                "stable_activation_gate": installer.EXPECTED_STABLE_ACTIVATION_GATE,
                "brand_identity": {
                    "display_name": "Evidence Lane",
                    "icon_path": "assets/evidence-lane-icon.png",
                    "icon_sha256": (
                        "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3D"
                        "EF87B8A129C4FA"
                    ),
                    "resource_uri": (
                        "ui://evidence-lane/governed-console-v4.html"
                    ),
                    "manifest_icon_fields": [
                        "interface.composerIcon",
                        "interface.logo",
                    ],
                    "required_at_stage": True,
                    "required_at_runtime_prewarm": True,
                },
                "remote_git_policy": {
                    "effective_release": "2.1.0",
                    "per_push_confirmation_token_required": False,
                    "automatic_push_scope": (
                        "EXACT_SOLE_REGISTERED_NON_PROTECTED_TEST_BRANCH"
                    ),
                    "host_managed_credentials_only": True,
                    "main_push_allowed": False,
                    "merge_allowed": False,
                    "pull_request_acceptance_allowed": False,
                    "force_push_allowed": False,
                },
                "promotion_gate": {
                    "mode": "CODE",
                    "ci_cd_law": "CONTROLLED_REQUIRED",
                    "explicit_six_way_hil_required": True,
                },
                "host_storage_tunnel_matrix": {
                    "routing_axes_independent": True,
                    "account_tier_affects_routing": False,
                    "api_billing_affects_routing": False,
                    "headless_api": {
                        "local_or_persistent_pv_storage": (
                            "LOCAL_SQLITE_WHEN_DURABLE"
                        ),
                        "ephemeral_pv_storage": (
                            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
                        ),
                        "tunnel_requirement": "NOT_REQUIRED_FOR_API_LAYER",
                        "flash_frequency": "EVERY_INVOCATION_ENTRY",
                    },
                    "interactive_codex_app_local_or_persistent": {
                        "pv_storage": "DURABLE_LOCAL_SQLITE",
                        "tunnel_setup_frequency": (
                            "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"
                        ),
                        "tunnel_key_retention": "HOST_MANAGED_PERSISTENT_PROFILE",
                    },
                    "interactive_codex_app_ephemeral_vm": {
                        "pv_storage": (
                            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
                        ),
                        "tunnel_setup_frequency": (
                            "ONCE_PER_EPHEMERAL_VM_INSTANCE"
                        ),
                        "tunnel_key_retention": "CURRENT_VM_LIFETIME_ONLY",
                        "tunnel_runtime_lifetime": "CURRENT_VM_LIFETIME_ONLY",
                    },
                },
            }
        ),
    )
    _write(source / "scripts" / "codex_release" / "install_codex_stable.py", "# fixture\n")
    _write(
        source
        / "scripts"
        / "codex_release"
        / "build_codex_exact_commit_package.py",
        "# fixture\n",
    )
    _write(
        source
        / "scripts"
        / "codex_release"
        / "seal_codex_git_ci_release_authority.py",
        "# fixture\n",
    )
    _write(
        source
        / "scripts"
        / "codex_release"
        / "seal_external_release_receipts.py",
        "# fixture\n",
    )
    _write(
        source
        / "scripts"
        / "codex_release"
        / "Update-EvidenceLaneCodexStableAndResume.ps1",
        "# fixture\n",
    )
    _write(
        source / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1",
        "# fixture\n",
    )
    _write(
        source
        / "scripts"
        / "codex_release"
        / "Manage-EvidenceLaneCodexGoalRecovery.ps1",
        "# fixture\n",
    )
    _write(
        source
        / "scripts"
        / "codex_release"
        / "Switch-EvidenceLaneCodexSlot.ps1",
        "# fixture\n",
    )
    _write(source / "scripts" / "codex_release" / "accept_codex_stable.py", "# fixture\n")
    _write(
        source / "hooks" / "hooks.json",
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command"}]}],
                    "UserPromptSubmit": [{"hooks": [{"type": "command"}]}],
                    "PreToolUse": [{"hooks": [{"type": "command"}]}],
                    "PostToolUse": [{"hooks": [{"type": "command"}]}],
                    "PreCompact": [{"hooks": [{"type": "command"}]}],
                    "PostCompact": [{"hooks": [{"type": "command"}]}],
                    "Stop": [{"hooks": [{"type": "command"}]}],
                    "SessionEnd": [{"hooks": [{"type": "command"}]}],
                }
            }
        ),
    )
    for name, marker in HOOK_NOTICE_MARKERS.items():
        _write(
            source / "hooks" / name,
            _fixture_hook_source(marker),
        )
    _write(
        source / "pyproject.toml",
        '[project]\nname = "evidence-lane-plugin"\nversion = "2.1.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "constants.py",
        'ENGINE_VERSION = "2.1.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "mcp_server.py",
        _fixture_catalog_source(),
    )
    for number in range(15):
        _write(source / "skills" / f"skill-{number:02d}" / "SKILL.md", "# Test\n")
    _write(source / "README.md", "# Evidence Lane\n")
    _write(
        source / "_evidence_lane_rehearsal" / "exit-slip.json",
        json.dumps({"state": "LOCAL_REHEARSAL_ONLY"}),
    )
    archive = tmp_path / "evidence-lane-v200.zip"
    with zipfile.ZipFile(archive, "w") as package:
        files = sorted(
            (item for item in source.rglob("*") if item.is_file()),
            key=lambda item: item.relative_to(source).as_posix(),
        )
        for path in files:
            package.write(path, path.relative_to(source).as_posix())
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest().upper()
    receipt = tmp_path / "LOCAL_PACKAGE_REHEARSAL.json"
    _write(
        receipt,
        json.dumps(
            {
                "schema": (
                    "evidence-lane.non-lifecycle-local-package-rehearsal.v1.receipt"
                ),
                "boundary": "NON_LIFECYCLE_LOCAL_PACKAGE_REHEARSAL",
                "status": "PASS",
                "archive": {"filename": archive.name, "sha256": archive_sha},
                "base_anchor": {"commit": "1" * 40, "tree": "2" * 40},
                "working_source_manifest_sha256": "A" * 64,
                "governed_candidate_created": False,
                "git_invoked": False,
                "accepted_pointer_moved": False,
            }
        ),
    )
    return archive, receipt, version


def _release_authority_receipt(
    tmp_path: Path,
    *,
    archive: Path,
    package_receipt: Path,
) -> tuple[Path, str]:
    package = json.loads(package_receipt.read_text("utf-8"))
    commit = str(package["base_anchor"]["commit"])
    tree = str(package["base_anchor"]["tree"])
    core = {
        "schema": "evidence-lane.codex-git-ci-vercel-release-authority.v2",
        "status": "PASS",
        "boundary": "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest().upper(),
        "package_receipt_sha256": hashlib.sha256(
            package_receipt.read_bytes()
        ).hexdigest().upper(),
        "working_source_manifest_sha256": package[
            "working_source_manifest_sha256"
        ],
        "plugin_source_manifest_sha256": package["exact_commit_export"][
            "plugin_source_manifest_sha256"
        ],
        "plugin_source_member_count": package["exact_commit_export"][
            "plugin_source_member_count"
        ],
        "source": {
            "branch": "agent/evi-v200-test",
            "commit": commit,
            "tree": tree,
            "exact_commit_export": True,
            "exact_commit_projection_clean": True,
            "working_checkout_clean_required": False,
            "untracked_bytes_excluded": True,
        },
        "remote_git": {
            "route": "NATIVE_GOVERNED_REMOTE_GIT",
            "push_status": "EXECUTED",
            "remote_branch_commit": commit,
            "protected_branch": False,
            "native_receipt_sha256": "B" * 64,
        },
        "github_ci": {
            "status": "PASS",
            "repository": "rathee000001/evidence_lane_plugin",
            "head_sha": commit,
            "required_checks_complete": True,
            "required_check_count": 8,
            "successful_check_count": 8,
            "failed_check_count": 0,
            "receipt_sha256": "C" * 64,
        },
        "vercel_preview": {
            "status": "PASS",
            "project_id": "prj_fixture",
            "team_id": "team_fixture",
            "deployment_id": "dpl_fixture",
            "url": "fixture.vercel.app",
            "state": "READY",
            "target": "PREVIEW",
            "repository": "rathee000001/evidence_lane_plugin",
            "branch": "agent/evi-v200-test",
            "head_sha": commit,
            "git_integration": True,
            "manual_deploy": False,
            "production_deployment": False,
            "receipt_sha256": "D" * 64,
        },
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "hil_inferred": False,
    }
    core["receipt_sha256"] = hashlib.sha256(
        (
            json.dumps(
                core,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()
    path = tmp_path / "CODEX_GIT_CI_RELEASE_AUTHORITY.json"
    _write(
        path,
        json.dumps(
            core,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
    )
    return path, hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _exact_package_receipt(
    tmp_path: Path,
    *,
    archive: Path,
    local_receipt: Path,
) -> Path:
    local = json.loads(local_receipt.read_text("utf-8"))
    core = {
        "schema": "evidence-lane.codex-exact-commit-package.v1.receipt",
        "boundary": "EXACT_GIT_COMMIT_PACKAGE_UNACCEPTED",
        "status": "PASS",
        "archive": local["archive"],
        "base_anchor": local["base_anchor"],
        "working_source_manifest_sha256": local[
            "working_source_manifest_sha256"
        ],
        "source_member_count": 1,
        "skill_count": 15,
        "canonical_lane_count": 18,
        "exact_commit_export": {
            "branch": "agent/evi-v200-test",
            "commit": local["base_anchor"]["commit"],
            "tree": local["base_anchor"]["tree"],
            "plugin_path": "plugins/evidence-lane-plugin",
            "git_archive_sha256": "D" * 64,
            "git_archive_member_count": 1,
            "plugin_source_manifest_sha256": "E" * 64,
            "plugin_source_member_count": 1,
            "projection_clean": True,
            "working_checkout_bytes_used": False,
            "untracked_bytes_used": False,
        },
        "local_rehearsal_receipt_sha256": hashlib.sha256(
            local_receipt.read_bytes()
        ).hexdigest().upper(),
        "git_invoked": True,
        "git_write_invoked": False,
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "hil_inferred": False,
    }
    core["receipt_sha256"] = hashlib.sha256(
        (
            json.dumps(
                core,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()
    path = tmp_path / "EXACT_COMMIT_PACKAGE.json"
    _write(
        path,
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )
    assert core["archive"]["sha256"] == hashlib.sha256(
        archive.read_bytes()
    ).hexdigest().upper()
    return path


def test_installer_stages_supported_marketplace_without_writing_cache(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, receipt, version = _fixture_archive(tmp_path)
    codex_home = tmp_path / "codex-home"
    data_root = tmp_path / "pv"

    result = module.install(
        argparse.Namespace(
            archive=archive,
            rehearsal_receipt=receipt,
            codex_home=codex_home,
            data_root=data_root,
            codex_executable=None,
            activate=False,
        )
    )

    marketplace = codex_home / "local-marketplaces" / "evidence-lane-github"
    installed = marketplace / "plugins" / "evidence-lane-plugin"
    assert result["status"] == "PASS"
    assert result["plugin"]["version"] == version
    assert result["marketplace"]["state"] == "STAGED"
    assert result["activation"]["state"] == "STAGED_RESTART_NOT_YET_REQUIRED"
    assert result["activation_authority"] == {
        "status": "NOT_APPLICABLE",
        "reason": "STAGING_ONLY_LOCAL_REHEARSAL",
    }
    assert result["runtime_ready_before_task_reopen"] is False
    assert result["generated_cache_written_directly"] is False
    assert result["previous_release_cache_deleted"] is False
    assert result["fallback_materialization_gate"] == (
        "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
    )
    assert result["fallback_materialized"] is False
    assert result["live_cache_cleanup_deferred_until_exact_pv11_acceptance"] is True
    assert result["two_slot_operator_packaged"] is True
    assert result["credential_requested_or_stored"] is False
    assert result["surface_change_display"]["state"] == "INITIAL_V2_BASELINE"
    assert result["surface_change_display"]["hooks"]["count"] == 8
    assert result["surface_change_display"]["hooks"]["count_semantics"] == (
        "REGISTERED_EVENT_COUNT"
    )
    assert result["surface_change_display"]["hooks"]["hook_file_count"] == 7
    assert result["surface_change_display"]["hooks"]["handler_count"] == 8
    assert result["surface_change_display"]["hooks"]["registered_events"] == [
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Stop",
        "UserPromptSubmit",
    ]
    assert result["surface_change_display"]["skills"]["count"] == 15
    assert result["surface_change_display"]["catalog"] == {
        "tools": 62,
        "read": 21,
        "write": 41,
        "skills": 15,
        "changed_from_previous": False,
    }
    assert (data_root / "installations" / "codex-v200" / "CURRENT_INSTALLATION.json").is_file()
    assert not (codex_home / "plugins" / "cache").exists()
    assert not (installed / "_evidence_lane_rehearsal").exists()
    assert json.loads(
        (marketplace / ".agents" / "plugins" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )["name"] == "evidence-lane-github"

    repeated = module.install(
        argparse.Namespace(
            archive=archive,
            rehearsal_receipt=receipt,
            codex_home=codex_home,
            data_root=data_root,
            codex_executable=None,
            activate=False,
        )
    )
    assert repeated["marketplace"]["state"] == "ALREADY_STAGED_EXACT"
    assert repeated["surface_change_display"] == result["surface_change_display"]


def test_installer_rejects_build_specific_stable_selector_growth(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, receipt, _ = _fixture_archive(tmp_path)
    codex_home = tmp_path / "codex-home"
    marketplace_name = "evidence-lane-v200-task2-build-606841c9"

    with pytest.raises(module.InstallationError, match="build hash"):
        module.install(
            argparse.Namespace(
                archive=archive,
                rehearsal_receipt=receipt,
                marketplace_name=marketplace_name,
                codex_home=codex_home,
                data_root=tmp_path / "pv",
                codex_executable=None,
                activate=False,
            )
        )

    assert not (codex_home / "plugins" / "cache").exists()


def test_two_consecutive_updates_reuse_one_stable_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    legacy_marketplace = "evidence-lane-v200-task2-build-stable"
    legacy_selector = f"evidence-lane-plugin@{legacy_marketplace}"
    stable_marketplace = "evidence-lane-github"
    stable_selector = f"evidence-lane-plugin@{stable_marketplace}"
    fallback_marketplace = "evidence-lane-pv11-fallback"
    fallback_selector = f"evidence-lane-plugin@{fallback_marketplace}"
    obsolete_marketplace = "evidence-lane-v200-task2-build-obsolete"
    obsolete_selector = f"evidence-lane-plugin@{obsolete_marketplace}"
    state = {
        "plugins": {
            legacy_selector: True,
            fallback_selector: False,
            obsolete_selector: False,
        },
        "marketplaces": {
            legacy_marketplace,
            stable_marketplace,
            fallback_marketplace,
            obsolete_marketplace,
        },
    }

    def fake_run(
        executable: Path,
        codex_home: Path,
        arguments: list[str],
    ) -> dict[str, object]:
        del executable, codex_home
        if arguments == ["plugin", "list", "--json"]:
            return {
                "installed": [
                    {"pluginId": selector, "enabled": enabled}
                    for selector, enabled in sorted(state["plugins"].items())
                ]
            }
        if arguments[:2] == ["plugin", "remove"]:
            selector = arguments[2]
            state["plugins"].pop(selector)
            return {"pluginId": selector}
        if arguments == ["plugin", "marketplace", "list", "--json"]:
            return {
                "marketplaces": [
                    {
                        "name": name,
                        "root": str(tmp_path / name),
                        **(
                            {
                                "marketplaceSource": {
                                    "sourceType": "git",
                                    "source": (
                                        "https://github.com/rathee000001/"
                                        "evidence_lane_plugin.git"
                                    ),
                                }
                            }
                            if name == stable_marketplace
                            else {}
                        ),
                    }
                    for name in sorted(state["marketplaces"])
                ]
            }
        if arguments[:3] == ["plugin", "marketplace", "remove"]:
            name = arguments[3]
            state["marketplaces"].remove(name)
            return {"marketplaceName": name}
        raise AssertionError(arguments)

    monkeypatch.setattr(module, "_run_codex", fake_run)
    authority = {
        "registry": {
            "slots": {
                "stable-build": {"plugin_selector": legacy_selector},
                "fallback": {"plugin_selector": fallback_selector},
            }
        }
    }
    first = module._prepare_in_place_stable_reinstall(
        executable=tmp_path / "codex.exe",
        codex_home=tmp_path / "codex-home",
        plugin_selector=stable_selector,
        marketplace_name=stable_marketplace,
        two_slot_authority=authority,
    )
    assert first["one_time_legacy_selector_migration"] is True
    assert first["target_marketplace_preexisting"] is True
    assert first["target_marketplace_source_verified"] is True
    assert first["target_marketplace_removed_for_exact_ref_refresh"] is True
    assert first["obsolete_cleanup_deferred_until_new_route_proof"] is True
    assert state["plugins"] == {
        legacy_selector: True,
        fallback_selector: False,
        obsolete_selector: False,
    }
    assert state["marketplaces"] == {
        legacy_marketplace,
        fallback_marketplace,
        obsolete_marketplace,
    }

    state["plugins"][legacy_selector] = False
    state["plugins"][stable_selector] = True
    state["marketplaces"].add(stable_marketplace)
    cleanup, final_list = module._cleanup_obsolete_after_new_route_proof(
        executable=tmp_path / "codex.exe",
        codex_home=tmp_path / "codex-home",
        plugin_selector=stable_selector,
        fallback_selector=fallback_selector,
    )
    assert cleanup["removed_obsolete_selectors"] == sorted(
        [legacy_selector, obsolete_selector]
    )
    assert cleanup["removed_obsolete_marketplaces"] == sorted(
        [legacy_marketplace, obsolete_marketplace]
    )
    assert len(final_list["installed"]) == 2
    assert state["plugins"] == {stable_selector: True, fallback_selector: False}
    assert state["marketplaces"] == {stable_marketplace, fallback_marketplace}

    authority["registry"]["slots"]["stable-build"]["plugin_selector"] = stable_selector
    second = module._prepare_in_place_stable_reinstall(
        executable=tmp_path / "codex.exe",
        codex_home=tmp_path / "codex-home",
        plugin_selector=stable_selector,
        marketplace_name=stable_marketplace,
        two_slot_authority=authority,
    )
    assert second["same_selector_refresh"] is True
    assert second["stable_removed_for_same_selector_reinstall"] is True
    assert second["target_marketplace_removed_for_exact_ref_refresh"] is True
    assert first["stable_selector"] == second["stable_selector"]
    assert first["new_stable_selector_created"] is False
    assert second["new_stable_selector_created"] is False
    assert state["plugins"] == {fallback_selector: False}
    assert state["marketplaces"] == {fallback_marketplace}


def test_legacy_migration_rejects_wrong_canonical_marketplace_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    legacy_selector = "evidence-lane-plugin@evidence-lane-v200-task2-build-stable"
    stable_selector = "evidence-lane-plugin@evidence-lane-github"
    fallback_selector = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    calls: list[list[str]] = []

    def fake_run(
        executable: Path,
        codex_home: Path,
        arguments: list[str],
    ) -> dict[str, object]:
        del executable, codex_home
        calls.append(arguments)
        if arguments == ["plugin", "list", "--json"]:
            return {
                "installed": [
                    {"pluginId": legacy_selector, "enabled": True},
                    {"pluginId": fallback_selector, "enabled": False},
                ]
            }
        if arguments == ["plugin", "marketplace", "list", "--json"]:
            return {
                "marketplaces": [
                    {
                        "name": "evidence-lane-github",
                        "root": str(tmp_path / "wrong-source"),
                        "marketplaceSource": {
                            "sourceType": "git",
                            "source": "https://github.com/example/wrong.git",
                        },
                    }
                ]
            }
        raise AssertionError(arguments)

    monkeypatch.setattr(module, "_run_codex", fake_run)
    authority = {
        "registry": {
            "slots": {
                "stable-build": {"plugin_selector": legacy_selector},
                "fallback": {"plugin_selector": fallback_selector},
            }
        }
    }
    with pytest.raises(module.InstallationError, match="governed Git source"):
        module._prepare_in_place_stable_reinstall(
            executable=tmp_path / "codex.exe",
            codex_home=tmp_path / "codex-home",
            plugin_selector=stable_selector,
            marketplace_name="evidence-lane-github",
            two_slot_authority=authority,
        )

    assert not any(
        arguments[:3] == ["plugin", "marketplace", "remove"]
        for arguments in calls
    )


def test_activation_requires_explicit_sealed_hook_trust(tmp_path: Path) -> None:
    module = _module()
    archive, receipt, _ = _fixture_archive(tmp_path)

    with pytest.raises(module.InstallationError, match="sealed-hook trust"):
        module.install(
            argparse.Namespace(
                archive=archive,
                rehearsal_receipt=receipt,
                codex_home=tmp_path / "codex-home",
                data_root=tmp_path / "pv",
                codex_executable=tmp_path / "codex.exe",
                activate=True,
                trust_sealed_hooks=False,
            )
        )


def test_activation_rejects_local_rehearsal_without_git_ci_authority(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, receipt, _ = _fixture_archive(tmp_path)

    with pytest.raises(module.InstallationError, match="not eligible"):
        module.install(
            argparse.Namespace(
                archive=archive,
                rehearsal_receipt=receipt,
                codex_home=tmp_path / "codex-home",
                data_root=tmp_path / "pv",
                codex_executable=tmp_path / "codex.exe",
                activate=True,
                trust_sealed_hooks=True,
            )
        )


def test_git_ci_release_authority_binds_exact_commit_archive_and_checks(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, local_receipt, _ = _fixture_archive(tmp_path)
    receipt = _exact_package_receipt(
        tmp_path,
        archive=archive,
        local_receipt=local_receipt,
    )
    authority_path, authority_file_sha256 = _release_authority_receipt(
        tmp_path,
        archive=archive,
        package_receipt=receipt,
    )
    package = json.loads(receipt.read_text("utf-8"))

    authority = module._load_release_authority(
        authority_path=authority_path,
        authority_file_sha256=authority_file_sha256,
        archive=archive,
        package_receipt_path=receipt,
        package_receipt=package,
    )

    assert authority["status"] == "PASS"
    assert authority["source"]["commit"] == package["base_anchor"]["commit"]
    assert authority["source"]["tree"] == package["base_anchor"]["tree"]
    assert authority["github_ci"]["successful_check_count"] == 8


def test_exact_commit_package_accepts_read_only_git_export(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, local_receipt, _ = _fixture_archive(tmp_path)
    receipt = _exact_package_receipt(
        tmp_path,
        archive=archive,
        local_receipt=local_receipt,
    )

    loaded = module._load_receipt(receipt, archive, activation=True)

    assert loaded["git_invoked"] is True
    assert loaded["git_write_invoked"] is False


def test_exact_commit_package_rejects_git_write_receipt(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, local_receipt, _ = _fixture_archive(tmp_path)
    receipt = _exact_package_receipt(
        tmp_path,
        archive=archive,
        local_receipt=local_receipt,
    )
    mutated = json.loads(receipt.read_text("utf-8"))
    mutated["git_write_invoked"] = True
    mutated_core = dict(mutated)
    mutated_core.pop("receipt_sha256")
    mutated["receipt_sha256"] = hashlib.sha256(
        (
            json.dumps(
                mutated_core,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest().upper()
    mutated_receipt = tmp_path / "EXACT_COMMIT_PACKAGE_WITH_GIT_WRITE.json"
    _write(
        mutated_receipt,
        json.dumps(mutated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )

    with pytest.raises(module.InstallationError, match="not eligible"):
        module._load_receipt(mutated_receipt, archive, activation=True)


def test_git_marketplace_verifies_full_commit_tree_and_package_subset(
    tmp_path: Path,
) -> None:
    module = _module()
    package_root = tmp_path / "package"
    marketplace_plugin = (
        tmp_path / "marketplace" / "plugins" / "evidence-lane-plugin"
    )
    _write(package_root / "README.md", "package member\n")
    _write(marketplace_plugin / "README.md", "package member\n")
    _write(marketplace_plugin / "remote_adapter" / "package.json", "{}\n")
    package_inventory = module._source_inventory(package_root)
    git_inventory = module._source_inventory(marketplace_plugin)

    result = module._assert_exact_git_marketplace_source(
        extracted_inventory=package_inventory,
        marketplace_root=tmp_path / "marketplace",
        expected_git_manifest_sha256=git_inventory["manifest_sha256"],
        expected_git_file_count=git_inventory["file_count"],
    )

    assert result["exact_git_commit_tree_match"] is True
    assert result["exact_commit_package_bytes_match"] is True
    assert result["file_count"] == 2
    assert result["package_subset_file_count"] == 1
    assert result["git_only_file_count"] == 1


def test_git_marketplace_rejects_full_tree_or_package_subset_drift(
    tmp_path: Path,
) -> None:
    module = _module()
    package_root = tmp_path / "package"
    marketplace_plugin = (
        tmp_path / "marketplace" / "plugins" / "evidence-lane-plugin"
    )
    _write(package_root / "README.md", "package member\n")
    _write(marketplace_plugin / "README.md", "package member\n")
    _write(marketplace_plugin / "remote_adapter" / "package.json", "{}\n")
    package_inventory = module._source_inventory(package_root)
    git_inventory = module._source_inventory(marketplace_plugin)

    _write(marketplace_plugin / "remote_adapter" / "package.json", "drift\n")
    with pytest.raises(module.InstallationError, match="complete exact Git"):
        module._assert_exact_git_marketplace_source(
            extracted_inventory=package_inventory,
            marketplace_root=tmp_path / "marketplace",
            expected_git_manifest_sha256=git_inventory["manifest_sha256"],
            expected_git_file_count=git_inventory["file_count"],
        )

    _write(marketplace_plugin / "remote_adapter" / "package.json", "{}\n")
    _write(package_root / "README.md", "package drift\n")
    with pytest.raises(module.InstallationError, match="package subset"):
        module._assert_exact_git_marketplace_source(
            extracted_inventory=module._source_inventory(package_root),
            marketplace_root=tmp_path / "marketplace",
            expected_git_manifest_sha256=git_inventory["manifest_sha256"],
            expected_git_file_count=git_inventory["file_count"],
        )


def test_installed_runtime_is_prewarmed_before_task_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    plugin_root = tmp_path / "installed"
    _write(plugin_root / "scripts" / "bootstrap.py", "# fixture\n")
    (plugin_root / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / "assets" / "evidence-lane-icon.png",
        plugin_root / "assets" / "evidence-lane-icon.png",
    )
    runtime_python = (
        plugin_root / ".venv" / "Scripts" / "python.exe"
        if module.os.name == "nt"
        else plugin_root / ".venv" / "bin" / "python"
    )
    _write(runtime_python, "fixture runtime")
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if len(calls) == 1:
            return subprocess.CompletedProcess(arguments, 0, b"bootstrap ok\n", b"")
        payload = {
            "engine_version": "2.1.0",
            "native_server_identity": "evidence-lane",
            "read_tool_count": 21,
            "tool_count": 62,
            "tool_catalog_sha256": "A" * 64,
            "route_status": "PASS",
            "resource_uri": "ui://evidence-lane/governed-console-v4.html",
            "native_dependency_prewarm_completed": True,
        }
        return subprocess.CompletedProcess(
            arguments,
            0,
            (json.dumps(payload) + "\n").encode("utf-8"),
            b"",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    receipt = module._prewarm_installed_runtime(plugin_root)

    assert receipt["status"] == "PASS"
    assert receipt["runtime_ready_before_task_reopen"] is True
    assert receipt["native_dependency_prewarm_completed"] is True
    assert receipt["tool_count"] == 62
    assert receipt["tool_catalog_sha256"] == "A" * 64
    assert receipt["resource_uri"] == (
        "ui://evidence-lane/governed-console-v4.html"
    )
    assert receipt["task_reopened"] is False
    assert receipt["bootstrap_attempt_count"] == 1
    assert receipt["bootstrap_attempts"][0]["returncode"] == 0
    assert len(receipt["receipt_sha256"]) == 64
    assert calls[0] == [module.sys.executable, str(plugin_root / "scripts" / "bootstrap.py")]
    assert calls[1][0] == str(runtime_python)


def test_installed_runtime_bootstrap_retries_once_on_same_sealed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    plugin_root = tmp_path / "installed"
    _write(plugin_root / "scripts" / "bootstrap.py", "# fixture\n")
    (plugin_root / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / "assets" / "evidence-lane-icon.png",
        plugin_root / "assets" / "evidence-lane-icon.png",
    )
    runtime_python = (
        plugin_root / ".venv" / "Scripts" / "python.exe"
        if module.os.name == "nt"
        else plugin_root / ".venv" / "bin" / "python"
    )
    _write(runtime_python, "fixture runtime")
    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if len(calls) == 1:
            return subprocess.CompletedProcess(arguments, 1, b"first stdout\n", b"first stderr\n")
        if len(calls) == 2:
            return subprocess.CompletedProcess(arguments, 0, b"second bootstrap ok\n", b"")
        payload = {
            "engine_version": "2.1.0",
            "native_server_identity": "evidence-lane",
            "read_tool_count": 21,
            "tool_count": 62,
            "tool_catalog_sha256": "A" * 64,
            "route_status": "PASS",
            "resource_uri": "ui://evidence-lane/governed-console-v4.html",
            "native_dependency_prewarm_completed": True,
        }
        return subprocess.CompletedProcess(
            arguments,
            0,
            (json.dumps(payload) + "\n").encode("utf-8"),
            b"",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    receipt = module._prewarm_installed_runtime(plugin_root)

    assert receipt["status"] == "PASS"
    assert receipt["bootstrap_attempt_count"] == 2
    assert [row["returncode"] for row in receipt["bootstrap_attempts"]] == [1, 0]
    assert receipt["bootstrap_attempts"][0]["stdout_sha256"] == hashlib.sha256(
        b"first stdout\n"
    ).hexdigest().upper()
    assert calls[0] == calls[1]
    assert calls[2][0] == str(runtime_python)


def test_supported_codex_api_trusts_only_exact_selector_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    selector = "evidence-lane-plugin@evidence-lane-v200-task2-build-test"
    events = ["postToolUse", "sessionStart", "stop", "userPromptSubmit"]
    hashes = {
        event: f"sha256:{index:064x}"
        for index, event in enumerate(events, start=1)
    }
    codex_home = tmp_path / "codex-home"
    _write(
        codex_home / "config.toml",
        "\n".join(
            [
                f'[plugins."{selector}"]',
                "enabled = true",
                f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                "enabled = false",
            ]
        ),
    )

    class FakeStdout:
        def __init__(self) -> None:
            self.lines: queue.Queue[str | None] = queue.Queue()

        def __iter__(self):
            while True:
                line = self.lines.get(timeout=5)
                if line is None:
                    return
                yield line

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = FakeStdout()
            self.stderr: list[str] = []
            self.returncode: int | None = None
            self.trusted = False
            self.writes: list[dict[str, object]] = []
            self.stdin = self

        def write(self, value: str) -> int:
            request = json.loads(value)
            self.writes.append(request)
            request_id = request.get("id")
            if request.get("method") == "initialize":
                response = {"id": request_id, "result": {"codexHome": "sealed"}}
            elif request.get("method") == "hooks/list":
                hooks = [
                    {
                        "key": f"{selector}:hooks/hooks.json:{event}:0:0",
                        "eventName": event,
                        "pluginId": selector,
                        "source": "plugin",
                        "isManaged": False,
                        "enabled": True,
                        "currentHash": hashes[event],
                        "trustStatus": "trusted" if self.trusted else "untrusted",
                    }
                    for event in events
                ]
                response = {
                    "id": request_id,
                    "result": {
                        "data": [
                            {
                                "cwd": str(tmp_path),
                                "hooks": hooks,
                                "warnings": [],
                                "errors": [],
                            }
                        ]
                    },
                }
            elif request.get("method") == "config/batchWrite":
                edit = request["params"]["edits"][0]
                value_map = edit["value"]
                if edit["keyPath"] == "plugins":
                    assert value_map[selector]["enabled"] is True
                    assert value_map[selector]["mcp_servers"]["evidence-lane"][
                        "enabled"
                    ] is True
                    _write(
                        codex_home / "config.toml",
                        "\n".join(
                            [
                                f'[plugins."{selector}"]',
                                "enabled = true",
                                (
                                    f'[plugins."{selector}".mcp_servers.'
                                    '"evidence-lane"]'
                                ),
                                "enabled = true",
                            ]
                        ),
                    )
                else:
                    assert edit["keyPath"] == "hooks.state"
                    assert set(value_map) == {
                        f"{selector}:hooks/hooks.json:{event}:0:0"
                        for event in events
                    }
                    assert {
                        item["trusted_hash"] for item in value_map.values()
                    } == set(hashes.values())
                    self.trusted = True
                response = {
                    "id": request_id,
                    "result": {
                        "status": "ok",
                        "version": f"sha256:{'a' * 64}",
                    },
                }
            else:
                return len(value)
            self.stdout.lines.put(json.dumps(response) + "\n")
            return len(value)

        def flush(self) -> None:
            return None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0
            self.stdout.lines.put(None)

        def kill(self) -> None:
            self.terminate()

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.returncode = 0
            return 0

    fake = FakeProcess()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: fake)

    result, channel = module._trust_sealed_plugin_hooks(
        executable=tmp_path / "codex.exe",
        codex_home=codex_home,
        data_root=tmp_path / "pv",
        hook_cwd=tmp_path,
        plugin_selector=selector,
    )

    assert result["status"] == "PASS"
    assert result["hook_count"] == 4
    assert result["registered_events"] == events
    assert result["before_trust_statuses"] == ["untrusted"]
    assert result["after_trust_statuses"] == ["trusted"]
    assert result["unrelated_hook_state_mutated"] is False
    assert channel["supported_codex_api"] == "config/batchWrite"
    assert [row["method"] for row in fake.writes] == [
        "initialize",
        "initialized",
        "config/batchWrite",
        "hooks/list",
        "config/batchWrite",
        "hooks/list",
    ]


def test_stable_activation_advances_registry_without_changing_fallback(
    tmp_path: Path,
) -> None:
    module = _module()
    data_root = tmp_path / "pv"
    codex_home = tmp_path / "codex"
    registry_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "CODEX_TWO_SLOT_REGISTRY.json"
    )
    baseline = data_root / "installations" / "codex-v200" / "INSTALL_OLD.json"
    fallback_install = (
        data_root
        / "installations"
        / "codex-v200"
        / "two-slot"
        / "PV11_FALLBACK_INSTALLATION.json"
    )
    _write(baseline, "old stable\n")
    _write(fallback_install, "accepted fallback\n")
    stable_marketplace = "evidence-lane-v200-task2-build-stable"
    stable_selector = f"evidence-lane-plugin@{stable_marketplace}"
    fallback_selector = "evidence-lane-plugin@evidence-lane-pv11-fallback"
    fallback = {
        "slot_role": "fallback",
        "plugin_selector": fallback_selector,
        "plugin_version": "2.0.0+codex.test",
        "package_sha256": "F" * 64,
        "byte_frozen": True,
        "accepted_pv": "PV11",
        "accepted_generation": 11,
        "enabled": False,
        "native_mcp_enabled": False,
        "install_receipt": str(fallback_install),
        "install_receipt_sha256": module._sha256(fallback_install),
    }
    stable = {
        "slot_role": "stable-build",
        "plugin_selector": stable_selector,
        "plugin_version": "2.1.0+codex.test",
        "package_sha256": "A" * 64,
        "byte_frozen": False,
        "enabled": True,
        "native_mcp_enabled": True,
        "install_receipt": str(baseline),
        "install_receipt_sha256": module._sha256(baseline),
    }
    registry: dict[str, object] = {
        "schema": "evidence-lane.codex-two-slot-registry.v1",
        "status": "PASS",
        "state": "STABLE_ACTIVE_FALLBACK_PREWARMED_DISABLED",
        "post_fuse_materialized": True,
        "accepted_pv": "PV11",
        "accepted_generation": 11,
        "accepted_package_sha256": "F" * 64,
        "exact_live_slot_count": 2,
        "max_enabled_plugin_count": 1,
        "slots": {"fallback": fallback, "stable-build": stable},
    }
    registry["registry_body_sha256"] = module._ordered_json_sha256(registry)
    registry["seal"] = {
        "algorithm": "SHA256",
        "body_sha256": module._ordered_json_sha256(registry),
    }
    _write(registry_path, json.dumps(registry, indent=2) + "\n")
    authority = module._load_two_slot_update_authority(
        data_root=data_root,
        comparison_baseline={
            "installation_receipt": str(baseline),
            "installation_receipt_sha256": module._sha256(baseline),
        },
    )
    assert authority is not None

    installed_path = (
        codex_home
        / "plugins"
        / "cache"
        / stable_marketplace
        / "evidence-lane-plugin"
        / "2.1.0+codex.test"
    )
    marketplace_root = codex_home / "local-marketplaces" / stable_marketplace
    _write(
        installed_path / ".codex-plugin" / "plugin.json",
        json.dumps({"version": "2.1.0+codex.test"}),
    )
    _write(
        marketplace_root / ".agents" / "plugins" / "marketplace.json",
        json.dumps({"name": stable_marketplace}),
    )
    new_install = data_root / "installations" / "codex-v200" / "INSTALL_NEW.json"
    _write(new_install, "new stable\n")
    _write(
        codex_home / "config.toml",
        "\n".join(
            [
                f'[plugins."{stable_selector}"]',
                "enabled = true",
                f'[plugins."{stable_selector}".mcp_servers."evidence-lane"]',
                "enabled = true",
                f'[plugins."{fallback_selector}"]',
                "enabled = false",
                f'[plugins."{fallback_selector}".mcp_servers."evidence-lane"]',
                "enabled = false",
            ]
        ),
    )
    plugin_list = {
        "installed": [
            {
                "pluginId": stable_selector,
                "version": "2.1.0+codex.test",
                "enabled": True,
            },
            {
                "pluginId": fallback_selector,
                "version": "2.0.0+codex.test",
                "enabled": False,
            },
        ]
    }
    result = module._advance_two_slot_stable_registry(
        authority=authority,
        plugin_selector=stable_selector,
        plugin_version="2.1.0+codex.test",
        installed_path=installed_path,
        marketplace_root=marketplace_root,
        install_receipt=new_install,
        archive_sha256="B" * 64,
        source_manifest_sha256="C" * 64,
        codex_home=codex_home,
        data_root=data_root,
        plugin_list=plugin_list,
    )

    updated = json.loads(registry_path.read_text(encoding="utf-8"))
    assert result["status"] == "PASS"
    assert updated["slots"]["fallback"] == fallback
    assert updated["slots"]["stable-build"]["plugin_selector"] == stable_selector
    assert updated["live_registered_selectors"] == [
        fallback_selector,
        stable_selector,
    ]
    assert updated["exact_registered_plugin_count"] == 2
    assert result["stable_selector_reused"] is True
    assert result["new_stable_selector_created"] is False
    assert updated["registry_body_sha256"] == module._ordered_json_sha256(
        {
            key: value
            for key, value in updated.items()
            if key not in {"registry_body_sha256", "seal"}
        }
    )
    assert updated["seal"]["body_sha256"] == module._ordered_json_sha256(
        {key: value for key, value in updated.items() if key != "seal"}
    )


def test_explicit_host_stable_baseline_survives_two_pass_install(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, receipt, _ = _fixture_archive(tmp_path)
    source = tmp_path / "source"
    prior_source = tmp_path / "prior-source"
    shutil.copytree(source, prior_source)
    for name in ("lifecycle_boundary.py", "post_tool_use.py", "pre_tool_use.py"):
        (prior_source / "hooks" / name).unlink()
    prior_hooks_path = prior_source / "hooks" / "hooks.json"
    prior_hooks = json.loads(prior_hooks_path.read_text(encoding="utf-8"))
    for event in (
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
    ):
        del prior_hooks["hooks"][event]
    prior_hooks_path.write_text(json.dumps(prior_hooks), encoding="utf-8")
    prior_version = "2.0.0+codex.host-stable"
    prior_manifest_path = prior_source / ".codex-plugin" / "plugin.json"
    prior_manifest = json.loads(prior_manifest_path.read_text(encoding="utf-8"))
    prior_manifest["version"] = prior_version
    prior_manifest_path.write_text(json.dumps(prior_manifest), encoding="utf-8")
    prior_surface = module._surface_inventory(
        prior_source,
        version=prior_version,
    )
    legacy_surface = json.loads(json.dumps(prior_surface))
    legacy_surface["hooks"] = {
        "count": prior_surface["hooks"]["hook_file_count"],
        "records": prior_surface["hooks"]["records"],
        "inventory_sha256": prior_surface["hooks"]["file_inventory_sha256"],
    }
    legacy_surface_core = dict(legacy_surface)
    legacy_surface_core.pop("surface_inventory_sha256")
    legacy_surface["surface_inventory_sha256"] = hashlib.sha256(
        module._json_bytes(legacy_surface_core)
    ).hexdigest().upper()
    baseline_archive_sha256 = "A" * 64
    baseline = {
        "schema": module.INSTALL_SCHEMA,
        "status": "PASS",
        "plugin": {
            "plugin_id": "evidence-lane-plugin",
            "version": prior_version,
            "surface_inventory": legacy_surface,
        },
        "archive_sha256": baseline_archive_sha256,
        "activation": {"state": "INSTALLED_RESTART_REQUIRED"},
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    baseline["receipt_sha256"] = hashlib.sha256(
        module._json_bytes(baseline)
    ).hexdigest().upper()
    data_root = tmp_path / "pv"
    archived_stage = (
        data_root
        / "installations"
        / "codex-v200"
        / "marketplace-archives"
        / "host-stable"
    )
    shutil.copytree(
        prior_source,
        archived_stage / "plugins" / "evidence-lane-plugin",
    )
    _write(
        archived_stage / "EVIDENCE_LANE_STAGE.json",
        json.dumps(
            {
                "schema": "evidence-lane.codex-marketplace-stage.v2",
                "archive_sha256": baseline_archive_sha256,
            }
        ),
    )
    baseline_path = (
        data_root
        / "installations"
        / "codex-v200"
        / "INSTALL_HOST_STABLE.json"
    )
    _write(baseline_path, json.dumps(baseline))
    baseline_file_sha256 = hashlib.sha256(
        baseline_path.read_bytes()
    ).hexdigest().upper()
    arguments = argparse.Namespace(
        archive=archive,
        rehearsal_receipt=receipt,
        baseline_installation_receipt=baseline_path,
        baseline_installation_receipt_sha256=baseline_file_sha256,
        codex_home=tmp_path / "codex-home",
        data_root=data_root,
        codex_executable=None,
        activate=False,
    )

    preflight = module.install(arguments)
    activation_pass = module.install(arguments)

    assert preflight["surface_change_display"]["previous_plugin_version"] == (
        prior_version
    )
    assert preflight["surface_change_display"]["hooks"]["added_events"] == [
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
    ]
    assert preflight["surface_change_display"]["hooks"]["added_files"] == [
        "lifecycle_boundary.py",
        "post_tool_use.py",
        "pre_tool_use.py",
    ]
    assert preflight["comparison_baseline"]["surface_enrichment"] == (
        "VERIFIED_ARCHIVED_MARKETPLACE_EVENT_INVENTORY"
    )
    assert activation_pass["marketplace"]["state"] == "ALREADY_STAGED_EXACT"
    assert activation_pass["comparison_baseline"] == preflight[
        "comparison_baseline"
    ]
    assert activation_pass["surface_change_display"] == preflight[
        "surface_change_display"
    ]


def test_installer_surface_diff_reports_changed_hook_without_raw_paths(
    tmp_path: Path,
) -> None:
    module = _module()
    _fixture_archive(tmp_path)
    source = tmp_path / "source"
    previous = module._surface_inventory(
        source,
        version="2.0.0+codex.previous",
    )
    prompt_hook = source / "hooks" / "prompt_submit.py"
    prompt_hook.write_text(
        prompt_hook.read_text(encoding="utf-8") + "# changed fixture\n",
        encoding="utf-8",
    )
    current = module._surface_inventory(
        source,
        version="2.0.0+codex.current",
    )
    display = module._surface_change_display(previous=previous, current=current)

    assert display["state"] == "VERSIONED_UPDATE"
    assert display["hooks"]["added"] == []
    assert display["hooks"]["changed"] == ["prompt_submit.py"]
    assert display["hooks"]["removed"] == []
    assert display["skills"]["added"] == []
    assert display["skills"]["changed"] == []
    assert display["skills"]["removed"] == []
    assert display["raw_paths_included"] is False
    assert display["private_research_question_included"] is False


def test_installer_rejects_native_catalog_drift_before_staging(tmp_path: Path) -> None:
    module = _module()
    _fixture_archive(tmp_path)
    source = tmp_path / "source"
    server = source / "src" / "evidence_lane_plugin" / "mcp_server.py"
    server.write_text(
        server.read_text(encoding="utf-8").replace(
            '@server.tool(name="tool_61", annotations=_WRITE)',
            "# removed tool 61",
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.InstallationError, match="62/21/41"):
        module._validate_plugin(source)


def test_restart_helper_is_exact_process_and_same_task_only() -> None:
    text = RESTART.read_text(encoding="utf-8")

    assert '[switch]$ConfirmRestart' in text
    assert 'OpenAI.CodexBeta_2p2nqsd0c76g0!App' in text
    assert 'OpenAI.Codex_2p2nqsd0c76g0!App' in text
    assert 'process_name = "ChatGPT (Beta).exe"' in text
    assert 'process_name = "ChatGPT.exe"' in text
    assert 'OpenAI\\.CodexBeta_' in text
    assert 'OpenAI\\.Codex_' in text
    assert "Get-CodexHostPackageProcesses" in text
    assert "prior_bound_host_process_tree_fully_stopped = $true" in text
    assert "Resolve-RootCodexTarget" in text
    assert 'Stop-Process -Id $TargetProcessId -Force' in text
    assert 'Stop-Process -Name' not in text
    assert "utf8NoBOM" not in text
    assert "$utf8NoBom = [System.Text.UTF8Encoding]::new($false)" in text
    assert "[System.IO.File]::WriteAllText(" in text
    assert 'user_reentry_action = "NONE_AUTO_OPEN_EXACT_TASK"' in text
    assert '$taskUri = "codex://threads/$TaskId"' in text
    assert 'if ((Split-Path -Leaf $installationDirectory) -eq "two-slot")' in text
    assert '$taskBindingRoot = Split-Path -Parent $installationDirectory' in text
    assert '$taskBindingDirectory = Join-Path $taskBindingRoot "task-bindings"' in text
    assert 'task_navigation_mode = "CODEX_THREAD_DEEPLINK"' in text
    assert "Assert-CodexThreadProtocol" in text
    assert "ConvertTo-WindowsCommandLineArgument" in text
    assert "-ArgumentList $argumentLine" in text
    assert "Invoke-CodexHostActivation" in text
    assert "EvidenceLaneCodexHostActivation" in text
    assert "Start-Process -FilePath $taskUri" not in text
    assert 'schema = "evidence-lane.codex-task-binding.v1"' in text
    assert 'state = "EXACT_TASK_BINDING_PREPARED"' in text
    assert 'claim_scope = "EXACT_CODEX_THREAD_ID_ONLY"' in text
    assert "task_binding_receipt_sha256" in text
    assert (
        'state = "BOUND_CODEX_HOST_ROOT_RELAUNCHED_EXACT_TASK_REQUESTED_AWAITING_NATIVE_PROOF"'
        in text
    )
    assert 'state = "BOUND_CODEX_HOST_RELAUNCH_FAILED"' in text
    assert "operator_recovery_required = $true" in text
    assert "manual_open_can_satisfy_helper_success = $false" in text
    assert "RELAUNCH_REQUESTED_USER_MUST_OPEN_SAME_TASK" not in text
    assert "coordinate_clicking_used = $false" in text
    assert "user_opened_host_manually = $false" in text
    assert "task_2_used = $false" in text
    assert 'native_workspace_binding_source = "EXISTING_CODEX_TASK_STATE"' in text
    assert "native_workspace_binding_mutated = $false" in text
    assert "native_local_workspace_and_changes_proof_pending = $true" in text
    assert "codex_native_changes_ui_mutated = $false" in text
    assert "active_task_ui_independently_proven = $false" in text
    assert "native_catalog_and_project_session_proof_pending = $true" in text
    assert "lifecycle_resume_call_required = $false" in text
    assert "state_travel_required = $false" in text
    assert "hot_reload_claimed = $false" in text


def test_same_slot_update_requires_git_ci_and_prewarm_before_task_reopen() -> None:
    text = STABLE_UPDATE.read_text(encoding="utf-8")

    assert "[string]$ReleaseAuthorityReceipt" in text
    assert "[string]$ReleaseAuthorityReceiptSha256" in text
    assert '"--release-authority-receipt"' in text
    assert '"--release-authority-receipt-sha256"' in text
    assert "$install.runtime_ready_before_task_reopen -ne $true" in text
    assert "$install.activation.runtime_prewarm.status -cne \"PASS\"" in text
    assert "$install.stable_selector_migrated_to_canonical_git -eq $true" in text
    assert '$script:CanonicalStableSelector = "evidence-lane-plugin@evidence-lane-github"' in text
    assert '$script:ExpectedGitRepository = "rathee000001/evidence_lane_plugin"' in text
    assert 'Join-Path $PSScriptRoot "install_codex_stable.py"' in text
    assert 'target = "PREVIEW"' not in text
    assert '$releaseAuthority.vercel_preview.target -cne "PREVIEW"' in text
    assert "goal_recovery_manager_rebound = $true" in text
    assert 'OpenAI.Codex_2p2nqsd0c76g0!App' in text
    assert 'OpenAI.CodexBeta_2p2nqsd0c76g0!App' in text
    assert "runtime_ready_before_task_reopen = $true" in text
    assert "fallback_activated = $false" in text
    assert '$ErrorActionPreference = "Continue"' in text
    assert "Select-Object -Last 80" in text
    assert '"The exact GitLane installer failed:`n"' in text
    assert "exact_task_reopen_requested = $null -ne $taskActivation" in text
    assert "operator_recovery_required = $null -eq $taskActivation" in text
    assert text.index("$taskActivation = Open-ExactTask") < text.index("Write-Json $resultPath $failure")
    assert text.index("runtime_prewarm.status") < text.rindex("Open-ExactTask")


def test_git_marketplace_fetch_has_a_longer_bounded_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    observed: list[int] = []

    def fake_subprocess_run(*args: object, **kwargs: object) -> object:
        del args
        observed.append(int(kwargs["timeout"]))

        class Completed:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return Completed()

    monkeypatch.setattr(module.subprocess, "run", fake_subprocess_run)
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"")

    module._run_codex(
        executable,
        tmp_path / "codex-home",
        ["plugin", "marketplace", "add", "repository", "--json"],
    )
    module._run_codex(
        executable,
        tmp_path / "codex-home",
        ["plugin", "list", "--json"],
    )

    assert observed == [480, 120]


def test_same_slot_update_helper_parses_as_powershell() -> None:
    command = (
        "$errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{STABLE_UPDATE}',"
        "[ref]$null,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_restart_helper_parses_as_powershell() -> None:
    command = (
        "$errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{RESTART}',"
        "[ref]$null,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_restart_helper_quotes_spaced_child_script_path() -> None:
    text = RESTART.read_text(encoding="utf-8")
    start = text.index("function ConvertTo-WindowsCommandLineArgument")
    end = text.index("\nfunction Assert-CodexThreadProtocol", start)
    function_source = text[start:end]
    command = function_source + r'''
$value = 'F:\test codex\plugins\evidence-lane-plugin\scripts\codex_release\Restart-EvidenceLaneCodex.ps1'
$expected = ([char]34) + $value + ([char]34)
$actual = ConvertTo-WindowsCommandLineArgument $value
if ($actual -cne $expected) {
    Write-Error "Spaced child script path was not preserved: $actual"
    exit 1
}
'''
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_goal_recovery_helper_is_general_logon_exact_task_and_read_only() -> None:
    text = GOAL_RECOVERY.read_text(encoding="utf-8")

    assert '[ValidateSet("Register", "RecoverNow", "RecoverAtLogon", "Status", "Unregister")]' in text
    assert 'New-ScheduledTaskTrigger -AtLogOn -User $identity' in text
    assert 'scope = "ALL_EXACT_EVIDENCE_LANE_GOVERNED_CODEX_GOAL_TASKS_ON_THIS_WINDOWS_USER"' in text
    assert 'method = "thread/read"' in text
    assert 'method = "thread/goal/get"' in text
    assert 'method = "thread/resume"' not in text
    assert 'method = "turn/start"' not in text
    assert '$taskUri = "codex://threads/$ExactTaskId"' in text
    assert "EvidenceLaneGoalRecoveryActivation" in text
    assert "Test-RecoveredThisBoot" in text
    assert 'if ($Action -eq "RecoverNow")' in text
    assert 'recovery_mode = "EXPLICIT_EXACT_TASK_NOW"' in text
    assert 'state = "EXACT_TASK_OPEN_REQUESTED_ACTIVE_GOAL_PERSISTED_HOST_CONTINUATION_PENDING"' in text
    assert 'stable_selector_growth_allowed = $false' in text
    assert 'fallback_must_remain_disabled = $true' in text
    assert 'synthetic_prompt_allowed = $false' in text
    assert 'state_travel_allowed = $false' in text
    assert 'raw_goal_objective_stored = $false' in text
    assert 'report_implemented_active_and_queued_after_host_continues = $true' in text
    assert 'OpenAI.Codex_2p2nqsd0c76g0!App' in text
    assert 'OpenAI.CodexBeta_2p2nqsd0c76g0!App' in text
    assert "Read-TaskHostProfile" in text
    assert "Resolve-GoalBindingHostProfile" in text
    assert "taskBindingWasSuperseded" in text
    assert "cannot safely supersede the legacy Goal binding" in text
    assert 'exact_bound_host_app_required = $true' in text
    assert 'stable_and_beta_hosts_supported = $true' in text


def test_goal_recovery_helper_parses_as_powershell() -> None:
    command = (
        "$errors=$null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{GOAL_RECOVERY}',"
        "[ref]$null,[ref]$errors) | Out-Null; "
        "if ($errors.Count) { $errors | ForEach-Object { $_.Message }; exit 1 }"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_goal_recovery_status_is_non_mutating_on_empty_root(tmp_path: Path) -> None:
    root = tmp_path / "goal-recovery"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(GOAL_RECOVERY),
            "-Action",
            "Status",
            "-RecoveryRoot",
            str(root),
            "-ScheduledTaskName",
            "Evidence Lane Codex Goal Recovery Fixture",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["manager_installed"] is False
    assert payload["scheduled_task_present"] is False
    assert payload["binding_count"] == 0
    assert payload["synthetic_prompt_allowed"] is False
    assert not root.exists()


def test_installed_acceptance_checker_declares_read_only_final_hil_boundary() -> None:
    text = ACCEPTANCE.read_text(encoding="utf-8")

    assert '"POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL"' in text
    assert '"PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED"' in text
    assert '"installed_host_hil_required": True' in text
    assert '"hil_inferred": False' in text
    assert '"git_invoked": False' in text
    assert '"tunnel_invoked": False' in text
    assert "subprocess" not in text


def test_installed_acceptance_checker_verifies_real_fixture_before_and_after_restart(
    tmp_path: Path,
) -> None:
    acceptance = _acceptance_module()
    archive, rehearsal, version = _fixture_archive(tmp_path)
    marketplace = tmp_path / "codex" / "local-marketplaces" / (
        "evidence-lane-github"
    ) / "plugins" / "evidence-lane-plugin"
    installed = tmp_path / "codex" / "plugins" / "cache" / (
        "evidence-lane-github"
    ) / "evidence-lane-plugin" / version
    source = tmp_path / "source"
    (source / "_evidence_lane_rehearsal").rename(
        tmp_path / "excluded-rehearsal-metadata"
    )
    _write(
        source / "pyproject.toml",
        '[project]\nname = "evidence-lane-plugin"\nversion = "2.1.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "constants.py",
        'ENGINE_VERSION = "2.1.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "mcp_server.py",
        _fixture_catalog_source(),
    )
    _write(
        source / "hooks" / "hooks.json",
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command"}]}],
                    "UserPromptSubmit": [{"hooks": [{"type": "command"}]}],
                    "PreToolUse": [{"hooks": [{"type": "command"}]}],
                    "PostToolUse": [{"hooks": [{"type": "command"}]}],
                    "PreCompact": [{"hooks": [{"type": "command"}]}],
                    "PostCompact": [{"hooks": [{"type": "command"}]}],
                    "Stop": [{"hooks": [{"type": "command"}]}],
                    "SessionEnd": [{"hooks": [{"type": "command"}]}],
                }
            }
        ),
    )
    for name, marker in HOOK_NOTICE_MARKERS.items():
        _write(
            source / "hooks" / name,
            _fixture_hook_source(marker),
        )
    _write(
        source / "commands" / "evi-plan.md",
        "---\n"
        "description: Pair a finished Codex plan with the canonical Plan Lane.\n"
        "---\n\n"
        "# Evidence Lane Plan Lane\n\n"
        "Keep the canonical plan visible.\n",
    )
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, marketplace)
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, installed)
    for relative, generated in acceptance._expected_migrated_command_skills(
        marketplace
    ).items():
        generated_path = installed / relative
        generated_path.parent.mkdir(parents=True, exist_ok=True)
        generated_path.write_bytes(generated)

    selector = "evidence-lane-plugin@evidence-lane-github"
    config = tmp_path / "codex" / "config.toml"
    _write(
        config,
        f'[plugins."{selector}"]\n'
        "enabled = true\n\n"
        f'[plugins."{selector}".mcp_servers.evidence-lane]\n'
        "enabled = true\n",
    )
    hook_trust: dict[str, object] = {
        "schema": "evidence-lane.codex-hook-trust.v1",
        "status": "PASS",
        "plugin_selector": selector,
        "hook_count": 4,
        "registered_events": [
            "postToolUse",
            "sessionStart",
            "stop",
            "userPromptSubmit",
        ],
        "records": [
            {
                "event_name": event,
                "hook_key": f"{selector}:hooks/hooks.json:{event}:0:0",
                "current_hash": f"sha256:{index:064x}",
                "enabled": True,
                "trust_status": "trusted",
            }
            for index, event in enumerate(
                ["postToolUse", "sessionStart", "stop", "userPromptSubmit"],
                start=1,
            )
        ],
        "before_trust_statuses": ["untrusted"],
        "after_trust_statuses": ["trusted"],
    }
    hook_trust["receipt_sha256"] = acceptance._sha256_bytes(
        acceptance._json_bytes(hook_trust)
    )
    runtime_prewarm = {
        "schema": "evidence-lane.codex-installed-runtime-prewarm.v1",
        "status": "PASS",
        "runtime_ready_before_task_reopen": True,
        "plugin_root_sha256": "D" * 64,
        "runtime_python_sha256": "E" * 64,
        "bootstrap_stdout_sha256": "F" * 64,
        "bootstrap_stderr_sha256": "0" * 64,
        "probe_stdout_sha256": "1" * 64,
        "probe_stderr_sha256": "2" * 64,
        "engine_version": "2.1.0",
        "native_server_identity": "evidence-lane",
        "tool_count": 62,
        "tool_catalog_sha256": "A" * 64,
        "resource_uri": "ui://evidence-lane/governed-console-v4.html",
        "brand_icon_sha256": (
            "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3D"
            "EF87B8A129C4FA"
        ),
        "catalog_expected": {"tools": 62, "read": 21, "write": 41, "skills": 15},
        "native_dependency_prewarm_completed": True,
        "duration_ms": 1,
        "task_reopened": False,
    }
    runtime_prewarm["receipt_sha256"] = acceptance._sha256_bytes(
        acceptance._json_bytes(runtime_prewarm)
    )
    installation = {
        "schema": "evidence-lane.codex-stable-installation.v2",
        "status": "PASS",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest().upper(),
        "activation_authority": {
            "status": "PASS",
            "boundary": "GOVERNED_GIT_BRANCH_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT",
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "branch": "agent/evi-v200-test",
            "receipt_sha256": "3" * 64,
            "receipt_file_sha256": "4" * 64,
            "vercel_preview_ready": True,
            "production_deployment": False,
        },
        "activation": {
            "state": "INSTALLED_RESTART_REQUIRED",
            "runtime_ready_before_task_reopen": True,
            "runtime_prewarm": runtime_prewarm,
            "plugin_add": {
                "pluginId": selector,
                "installedPath": str(installed),
            },
            "hook_trust": hook_trust,
            "git_marketplace_source": {
                "status": "PASS",
                "source_type": "git",
                "exact_commit_package_bytes_match": True,
            },
            "post_proof_cleanup": {
                "status": "PASS",
                "exact_installed_slot_count": 2,
            },
        },
        "generated_cache_written_directly": False,
        "previous_release_cache_deleted": False,
        "fallback_materialization_gate": (
            "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
        ),
        "fallback_materialized": False,
        "live_cache_cleanup_deferred_until_exact_pv11_acceptance": True,
        "two_slot_operator_packaged": True,
        "post_proof_obsolete_cleanup_completed": True,
        "obsolete_cleanup_used_supported_codex_apis": True,
        "credential_requested_or_stored": False,
        "runtime_ready_before_task_reopen": True,
    }
    installed_surface = acceptance._surface_inventory(installed, version=version)
    installation["surface_change_display"] = {
        "schema": "evidence-lane.codex-installed-surface-change-display.v2",
        "state": "INITIAL_V2_BASELINE",
        "previous_plugin_version": None,
        "current_plugin_version": version,
        "hooks": {
            "count": installed_surface["hooks"]["count"],
            "count_semantics": "REGISTERED_EVENT_COUNT",
            "registered_event_count": installed_surface["hooks"][
                "registered_event_count"
            ],
            "registered_events": installed_surface["hooks"][
                "registered_events"
            ],
            "handler_count": installed_surface["hooks"]["handler_count"],
            "hook_file_count": installed_surface["hooks"]["hook_file_count"],
            "added": [
                row["name"] for row in installed_surface["hooks"]["records"]
            ],
            "added_files": [
                row["name"] for row in installed_surface["hooks"]["records"]
            ],
            "changed": [],
            "changed_files": [],
            "removed": [],
            "removed_files": [],
            "added_events": installed_surface["hooks"]["registered_events"],
            "removed_events": [],
            "inventory_sha256": installed_surface["hooks"]["inventory_sha256"],
            "file_inventory_sha256": installed_surface["hooks"][
                "file_inventory_sha256"
            ],
            "event_inventory_sha256": installed_surface["hooks"][
                "event_inventory_sha256"
            ],
        },
        "skills": {
            "count": installed_surface["skills"]["count"],
            "added": [
                row["name"] for row in installed_surface["skills"]["records"]
            ],
            "changed": [],
            "removed": [],
            "inventory_sha256": installed_surface["skills"]["inventory_sha256"],
        },
        "catalog": {
            "tools": 62,
            "read": 21,
            "write": 41,
            "skills": 15,
            "changed_from_previous": False,
        },
        "previous_surface_inventory_sha256": None,
        "current_surface_inventory_sha256": installed_surface[
            "surface_inventory_sha256"
        ],
        "raw_paths_included": False,
        "private_research_question_included": False,
    }
    change_body = installation["surface_change_display"]
    change_body["change_display_sha256"] = acceptance._sha256_bytes(
        acceptance._json_bytes(change_body)
    )
    installation["receipt_sha256"] = acceptance._sha256_bytes(
        acceptance._json_bytes(installation)
    )
    installation_path = tmp_path / "installation.json"
    _write(installation_path, acceptance._json_bytes(installation).decode("utf-8"))

    common = {
        "installed_plugin": installed,
        "marketplace_plugin": marketplace,
        "codex_config": config,
        "archive": archive,
        "rehearsal_receipt": rehearsal,
        "installation_receipt": installation_path,
    }
    pre = acceptance.accept(
        argparse.Namespace(
            **common,
            native_route_receipt=None,
            output=tmp_path / "pre-restart-acceptance.json",
        )
    )
    assert pre["state"] == (
        "PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED"
    )
    assert pre["catalog"] == {"tools": 62, "read": 21, "write": 41, "skills": 15}
    assert pre["installed_plugin"]["version"] == version
    assert pre["package_inventory"]["source_bytes_match_marketplace"] is True
    assert pre["package_inventory"]["codex_generated_migration_count"] == 1
    assert pre["package_inventory"]["codex_generated_migrations"][0][
        "skill_name"
    ] == "source-command-evi-plan"
    assert pre["restart_verified"] is False
    assert pre["enabled_selector"] == selector
    assert pre["hook_trust"]["status"] == "PASS"
    assert pre["hil_inferred"] is False

    native_route = tmp_path / "native-route.json"
    _write(
        native_route,
        json.dumps(
            {
                "schema": "evidence-lane.native-mcp-route-receipt.v1",
                "status": "PASS",
                "server_identity": "evidence-lane",
                "canonical_tool_namespace": "mcp__evidence_lane__",
                "exposure_profile": "FULL_LIFECYCLE",
                "tool_count": 62,
                "tool_names_unique": True,
                "project_route_argument_required": True,
                "cross_project_fallback_allowed": False,
                "tool_catalog_sha256": "A" * 64,
            }
        ),
    )
    post = acceptance.accept(
        argparse.Namespace(
            **common,
            native_route_receipt=native_route,
            output=tmp_path / "post-restart-acceptance.json",
        )
    )
    assert post["state"] == (
        "POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL"
    )
    assert post["restart_verified"] is True
    assert post["native_route"]["canonical_tool_namespace"] == (
        "mcp__evidence_lane__"
    )
    assert post["installed_host_hil_required"] is True
    assert post["candidate_created_or_accepted"] is False
    assert post["pointer_moved"] is False

    migrated = next(
        (installed / ".codex-plugin" / "migrated-command-skills").rglob("SKILL.md")
    )
    migrated.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(acceptance.AcceptanceError, match="exact derivation"):
        acceptance.accept(
            argparse.Namespace(
                **common,
                native_route_receipt=None,
                output=tmp_path / "tampered-acceptance.json",
            )
        )
