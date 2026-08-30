from __future__ import annotations

import hashlib
import json
import runpy
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "evidence-lane-plugin" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_release_candidate_rehearsal import (
    BOUNDARY,
    EXPECTED_BEHAVIOR_OWNERSHIP,
    EXPECTED_STABLE_ACTIVATION_GATE,
    PackageBoundaryError,
    _package_surface_coherence,
    build_rehearsal,
)

VERSION = "3.0.0+codex.20260816074428"
COMMIT = "a" * 40
TREE = "b" * 40


def _write(root: Path, relative: str, content: bytes | str = b"fixture") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(content)


def _plugin_fixture(tmp_path: Path) -> Path:
    plugin = tmp_path / "plugin"
    adapter = tmp_path / "apps" / "evidence-lane-app"
    _write(
        plugin,
        ".mcp.json",
        '{"mcpServers":{"evidence-lane":{"command":"python","args":[]}}}\n',
    )
    _write(plugin, "COPYRIGHT.md", "# Copyright\n")
    _write(plugin, "LICENSE.md", "# Proprietary license\n")
    _write(plugin, "README.md", "# Evidence Lane plugin\n")
    _write(plugin, "THIRD_PARTY_NOTICES.md", "# Third-party notices\n")
    _write(
        plugin,
        "tests/tools/should_not_ship.py",
        "raise AssertionError('maintainer-only')\n",
    )
    _write(plugin, "tests/test_host_panel_governed_counts.py", "# installed test\n")
    _write(
        plugin, "tests/test_installed_package_surface_smoke.py", "# installed test\n"
    )
    _write(
        plugin,
        "tests/test-surface-policy.v1.json",
        json.dumps(
            {
                "schema": "evidence-lane.installed-test-surface-policy.v1",
                "status": "PASS",
                "installed_executable_tests": [
                    "tests/test_host_panel_governed_counts.py",
                    "tests/test_installed_package_surface_smoke.py",
                ],
                "installed_verification_environment": {
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTEST_ADDOPTS": "-p no:cacheprovider",
                },
                "installed_verification_may_run_broad_regression": False,
                "post_verification_forbidden_cache_artifact_count": 0,
            }
        )
        + "\n",
    )
    _write(
        plugin,
        ".codex-plugin/plugin.json",
        json.dumps(
            {
                "name": "evidence-lane-plugin",
                "version": VERSION,
                "mcpServers": "./.mcp.json",
                "interface": {
                    "displayName": "Evidence Lane",
                    "composerIcon": "./assets/evidence-lane-icon.png",
                    "logo": "./assets/evidence-lane-icon.png",
                },
            }
        )
        + "\n",
    )
    _write(
        plugin,
        "assets/evidence-lane-icon.png",
        (
            ROOT
            / "plugins"
            / "evidence-lane-plugin"
            / "assets"
            / "evidence-lane-icon.png"
        ).read_bytes(),
    )
    _write(
        plugin,
        "chatgpt-app-connection.json",
        json.dumps(
            {
                "host": "CHATGPT",
                "delivery": "REGISTERED_REMOTE_MCP_ONLY",
                "codex_install_manifest_reference": False,
                "google_drive_bundled": False,
                "direct_stdio_fallback_allowed": False,
            }
        )
        + "\n",
    )
    _write(plugin, "chatgpt-app-submission.json", "{}\n")
    _write(
        plugin,
        "release-channels.json",
        json.dumps(
            {
                "schema": "evidence-lane.release-channels.v1",
                "stable": {
                    "release": "1.5.0",
                    "native_server_identity": "evidence-lane",
                    "native_tool_count": 62,
                    "native_read_tool_count": 21,
                    "native_write_tool_count": 41,
                    "skill_count": 15,
                    "codex_apps_allowed": False,
                    "google_drive_bundled": False,
                },
                "future_test": {
                    "enabled": False,
                    "may_replace_stable_before_acceptance": False,
                },
                "promotion_gate": {
                    "explicit_authority_hil_required": True,
                    "fail_closed_on_version_mismatch": True,
                },
                "history": {"append_only": True, "hash_chained": True},
            }
        )
        + "\n",
    )
    _write(
        plugin,
        "scripts/codex-release-channel.json",
        json.dumps(
            {
                "schema": "evidence-lane.codex-release-channel.v2",
                "stable": {
                    "release": "3.0.0",
                    "slot_role": "main-git-release",
                    "codex_marketplace_slot": "evidence-lane-github",
                    "marketplace_display_name": "Main Git Plugin Version",
                    "install_source": "GIT_MAIN_EXACT_COMMIT_AFTER_GOVERNED_MERGE",
                    "stable_selector_is_persistent": True,
                    "stable_updates_reinstall_in_place": True,
                    "build_identity_is_receipt_not_selector": True,
                    "native_server_identity": "evidence-lane",
                    "native_tool_count": 91,
                    "native_read_tool_count": 30,
                    "native_write_tool_count": 61,
                    "skill_count": 19,
                    "codex_apps_allowed": False,
                    "generated_namespace_allowed": False,
                    "direct_stdio_fallback_allowed": False,
                    "google_drive_bundled": False,
                },
                "local_testing": {
                    "release_line": "3.0.0",
                    "slot_role": "versioned-local-testing",
                    "codex_marketplace_slot": "evidence-lane-v300-testing-new",
                    "marketplace_display_name": "Local Testing Slot",
                    "same_marketplace_selector_reused": True,
                    "fresh_package_version_per_local_build": True,
                    "helper_installs_plugin": False,
                },
                "dependency_toolchains": {
                    "search_v1": {
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
                    }
                },
                "live_slot_policy": {
                    "exact_slot_count": 2,
                    "allowed_slots": [
                        "main-git-release",
                        "versioned-local-testing",
                    ],
                    "allowed_marketplaces": [
                        "evidence-lane-github",
                        "evidence-lane-v300-testing-new",
                    ],
                    "max_enabled_plugin_count": 1,
                    "exact_registered_plugin_count": 2,
                    "stable_selector_growth_allowed": False,
                    "max_active_native_mcp_count": 1,
                    "max_active_tunnel_count": 1,
                    "obsolete_marketplace_registrations_must_be_absent": True,
                },
                "behavior_ownership": EXPECTED_BEHAVIOR_OWNERSHIP,
                "stable_activation_gate": EXPECTED_STABLE_ACTIVATION_GATE,
                "brand_identity": {
                    "display_name": "Evidence Lane",
                    "icon_path": "assets/evidence-lane-icon.png",
                    "icon_sha256": (
                        "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3D"
                        "EF87B8A129C4FA"
                    ),
                    "resource_uri": ("ui://evidence-lane/governed-console-v6.html"),
                    "manifest_icon_fields": [
                        "interface.composerIcon",
                        "interface.logo",
                    ],
                    "required_at_stage": True,
                    "required_at_runtime_prewarm": True,
                },
                "remote_git_policy": {
                    "effective_release": "3.0.0",
                    "per_push_confirmation_token_required": False,
                    "automatic_push_scope": (
                        "GITHUB_APP_GOVERNED_FEATURE_BRANCH_THEN_EXACT_MAIN_MERGE"
                    ),
                    "host_managed_credentials_only": True,
                    "main_push_allowed": False,
                    "merge_allowed": True,
                    "pull_request_acceptance_allowed": True,
                    "force_push_allowed": False,
                },
                "delivery_boundary": {
                    "external_app_artifacts_packaged_with_codex": False,
                    "remote_website_artifacts_packaged_with_codex": False,
                },
                "host_storage_tunnel_matrix": {
                    "routing_axes_independent": True,
                    "account_tier_affects_routing": False,
                    "api_billing_affects_routing": False,
                    "headless_api": {
                        "local_or_persistent_pv_storage": ("LOCAL_SQLITE_WHEN_DURABLE"),
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
                            "tunnel_requirement": ("NOT_REQUIRED_NATIVE_MCP_AVAILABLE"),
                            "tunnel_setup_frequency": "NONE",
                            "tunnel_key_retention": "NOT_APPLICABLE",
                            "tunnel_runtime_lifetime": "NOT_APPLICABLE",
                        },
                        "host_tool_gap": {
                            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
                            "tunnel_setup_frequency": (
                                "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"
                            ),
                            "tunnel_key_retention": (
                                "CURRENT_WINDOWS_USER_DPAPI_PROFILE"
                            ),
                            "tunnel_runtime_lifetime": (
                                "WINDOWS_LOGON_MANAGED_PERSISTENT_HOST"
                            ),
                        },
                    },
                    "codex_cli_local_or_persistent": {
                        "pv_storage": "DURABLE_LOCAL_SQLITE",
                        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
                        "native_mcp_available": {
                            "tunnel_requirement": ("NOT_REQUIRED_NATIVE_MCP_AVAILABLE"),
                        },
                        "host_tool_gap": {
                            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
                            "tunnel_setup_frequency": (
                                "ONE_TIME_PER_PERSISTENT_HOST_AND_RELEASE"
                            ),
                        },
                    },
                    "interactive_codex_app_ephemeral_vm": {
                        "pv_storage": (
                            "DURABLE_MOUNT_ELSE_CONFIGURED_TRANSACTIONAL_CONNECTOR"
                        ),
                        "routing_basis": "MEASURED_NATIVE_MCP_CAPABILITY",
                        "native_mcp_available": {
                            "tunnel_requirement": ("NOT_REQUIRED_NATIVE_MCP_AVAILABLE"),
                        },
                        "host_tool_gap": {
                            "tunnel_requirement": "REQUIRED_FOR_HOST_TOOL_GAP",
                            "tunnel_setup_frequency": (
                                "ONCE_PER_EPHEMERAL_VM_INSTANCE"
                            ),
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
                },
                "promotion_gate": {
                    "explicit_authority_hil_required": True,
                    "fail_closed_on_version_mismatch": True,
                },
            }
        )
        + "\n",
    )
    rg_bytes = b"fixture-ripgrep-15.2.0\n"
    _write(plugin, "toolchains/bin/windows-x86_64/rg.exe", rg_bytes)
    _write(plugin, "toolchains/licenses/ripgrep-15.2.0/LICENSE-MIT", "MIT\n")
    _write(plugin, "toolchains/licenses/ripgrep-15.2.0/UNLICENSE", "Unlicense\n")
    _write(
        plugin,
        "toolchains/search-tools.v1.json",
        json.dumps(
            {
                "schema": "evidence-lane.search-toolchain-manifest.v1",
                "version": 1,
                "scope": "ALL_GOVERNED_PROJECTS",
                "resolution_order": [
                    "PACKAGE_LOCAL_VERIFIED_BINARY",
                    "EXPLICIT_CONFIGURED_VERIFIED_HOST_BINARY",
                    "DETERMINISTIC_BUILTIN_FALLBACK",
                ],
                "auto_download_during_mcp_handshake": False,
                "path_lookup_allowed": False,
                "shell_execution_allowed": False,
                "fts_authority": {
                    "backend": "SQLITE_FTS5",
                    "query_mode": "BOUNDED_FTS5",
                    "scope": "PLAN_LANE_CHATLINEAGE_AND_PROJECT_SECTORS",
                    "pointer_and_locator_required": True,
                    "model_context_policy": "BOUNDED_QUERY_RESULTS_ONLY",
                    "pv_package_loaded_into_model_context": False,
                    "fallback": "FAIL_CLOSED_WHEN_SQLITE_FTS5_UNAVAILABLE",
                },
                "tools": [
                    {
                        "tool_id": "ripgrep",
                        "role": "BOUNDED_LITERAL_CONTENT_AND_FILE_SEARCH",
                        "version": "15.2.0",
                        "license_spdx": "MIT OR Unlicense",
                        "fallback_backend": "PYTHON_BOUNDED_LITERAL_SCAN",
                        "package_binaries": {
                            "windows-x86_64": {
                                "path": "toolchains/bin/windows-x86_64/rg.exe",
                                "sha256": hashlib.sha256(rg_bytes).hexdigest().upper(),
                                "size_bytes": len(rg_bytes),
                                "licenses": [
                                    "toolchains/licenses/ripgrep-15.2.0/LICENSE-MIT",
                                    "toolchains/licenses/ripgrep-15.2.0/UNLICENSE",
                                ],
                            }
                        },
                    },
                ],
            }
        )
        + "\n",
    )
    _write(
        plugin,
        "scripts/codex_release/install_codex_stable.py",
        "# fixture installer\n",
    )
    _write(
        plugin,
        "scripts/codex_release/build_codex_exact_commit_package.py",
        "# fixture exact package builder\n",
    )
    _write(
        plugin,
        "scripts/codex_release/seal_codex_git_ci_release_authority.py",
        "# fixture release authority joiner\n",
    )
    _write(
        plugin,
        "scripts/codex_release/seal_external_release_receipts.py",
        "# fixture external receipt sealer\n",
    )
    _write(
        plugin,
        "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1",
        "# fixture restart helper\n",
    )
    _write(
        plugin,
        "scripts/codex_release/accept_codex_stable.py",
        "# fixture acceptance checker\n",
    )
    _write(plugin, "evidence/prompt_studio/manifest.json", "{}\n")
    _write(plugin, "evidence/prompt_studio/studio_search.sqlite", b"sqlite")
    _write(adapter, "app/manifest.ts", "export const manifest = {};\n")
    _write(adapter, "package.json", '{"dependencies":{}}\n')
    _write(adapter, "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(plugin, "pyproject.toml", '[project]\nname="fixture"\nversion="3.0.0"\n')
    _write(plugin, "requirements.lock.txt", "mcp==1.28.1\n")
    _write(plugin, "requirements.toolchain.lock.txt", "mcp==1.28.1\n")
    _write(
        plugin,
        "scripts/codex_release/install_native_toolchain.py",
        "# fixture native toolchain installer\n",
    )
    _write(
        plugin,
        "scripts/generate_toolchain_execution_matrix.py",
        "# fixture toolchain matrix generator\n",
    )
    _write(plugin, "toolchains/TOOLCHAIN_EXECUTION_MATRIX.md", "# Matrix\n")
    _write(
        plugin,
        "toolchains/tool-execution-routing.v1.json",
        json.dumps(
            {
                "schema": "evidence-lane.tool-execution-routing.v1",
                "status": "PASS",
                "primary_and_fallback_order_explicit": True,
                "rows": [],
            }
        )
        + "\n",
    )
    _write(plugin, "toolchains/native-tools.v1.json", "{}\n")
    _write(plugin, "src/evidence_lane_plugin/__init__.py", "VERSION = 'fixture'\n")
    for index in range(19):
        _write(
            plugin,
            f"skills/skill-{index:02d}/SKILL.md",
            f"---\nname: skill-{index:02d}\n---\nFixture.\n",
        )
    for index in range(18):
        lane = f"public/dummy-lane-packages/lane-{index:02d}"
        _write(adapter, f"{lane}/lane-{index:02d}.dot", "digraph fixture {}\n")
        _write(adapter, f"{lane}/lane-{index:02d}.mmd", "flowchart LR\n")
        _write(adapter, f"{lane}/lane-{index:02d}.mmd.8k.png", b"png")
        _write(adapter, f"{lane}/lane-{index:02d}.mmd.vector.svg", "<svg/>\n")
        _write(adapter, f"{lane}/lane-{index:02d}_sector_v001.sqlite", b"sqlite")
        _write(adapter, f"{lane}/refresh_receipt.json", "{}\n")
    return plugin


def test_rehearsal_rejects_combined_native_and_registered_app_manifest(
    tmp_path: Path,
) -> None:
    plugin = _plugin_fixture(tmp_path)
    manifest_path = plugin / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["apps"] = "./.app.json"
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    _write(plugin, ".app.json", '{"apps":{"evidence-lane":{}}}\n')

    with pytest.raises(PackageBoundaryError, match="must remain separate"):
        _build(plugin, tmp_path / "combined-manifest")


def _build(plugin: Path, output: Path) -> dict[str, object]:
    return build_rehearsal(
        plugin_root=plugin,
        output_dir=output,
        base_commit=COMMIT,
        base_tree=TREE,
        expected_version=VERSION,
        surface_coherence_required=False,
    )


def test_rehearsal_seals_systemwide_route_audit_when_supplied(
    tmp_path: Path,
) -> None:
    plugin = _plugin_fixture(tmp_path)
    audit_path = tmp_path / "systemwide-route-audit.json"
    audit = {
        "schema": "evidence-lane.systemwide-route-audit.v1",
        "status": "PASS",
        "active_row": 265,
        "receipt_sha256": "A" * 64,
        "accepted_archive_queried": False,
        "candidate_created_or_cleared": False,
        "pointer_moved": False,
        "git_index_mutated": False,
        "git_ref_mutated": False,
        "consumer_parity": {"status": "PASS"},
        "obsolete_route_purge": {"status": "PASS"},
        "plan_supersession": {"status": "PASS", "rows_sha256": "B" * 64},
        "current_registry": {
            "registry_sha256": "C" * 64,
            "public_tool_count": 91,
            "obsolete_public_tools": [
                "pv_refresh",
                "pv_state_travel_prepare",
                "pv_state_travel_resume",
            ],
        },
        "systemwide_regression": {
            "status": "PASS",
            "file_sha256": "D" * 64,
        },
        "skill_current_route_audit": {
            "status": "PASS",
            "receipt_sha256": "E" * 64,
        },
    }
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    receipt = build_rehearsal(
        plugin_root=plugin,
        output_dir=tmp_path / "with-route-audit",
        base_commit=COMMIT,
        base_tree=TREE,
        expected_version=VERSION,
        systemwide_route_audit_receipt=audit_path,
        surface_coherence_required=False,
    )
    assert receipt["systemwide_route_audit"]["status"] == "PASS"
    archive = Path(receipt["receipt_path"]).parent / receipt["archive"]["filename"]
    with zipfile.ZipFile(archive) as package:
        assert "manifests/package/systemwide-route-audit.json" in (package.namelist())


def test_current_plugin_package_surface_is_one_coherent_version() -> None:
    receipt = _package_surface_coherence(ROOT / "plugins" / "evidence-lane-plugin")
    assert receipt["status"] == "PASS"
    assert receipt["mixed_version_members_allowed"] is False
    assert receipt["mcp"]["tools"] == 91
    assert receipt["skills"]["count"] == 26
    assert receipt["skill_routing"]["separate_command_count"] == 0
    assert receipt["skill_routing"]["legacy_command_surface_present"] is False
    assert receipt["hooks"]["event_count"] == 11
    assert receipt["hooks"]["handler_action_count"] == 44


def test_live_release_policy_matches_every_package_and_install_validator() -> None:
    plugin = ROOT / "plugins" / "evidence-lane-plugin"
    contract = json.loads(
        (plugin / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    validators = [
        {
            "EXPECTED_BEHAVIOR_OWNERSHIP": EXPECTED_BEHAVIOR_OWNERSHIP,
            "EXPECTED_STABLE_ACTIVATION_GATE": EXPECTED_STABLE_ACTIVATION_GATE,
        },
        runpy.run_path(
            str(plugin / "scripts" / "codex_release" / "install_codex_stable.py")
        ),
        runpy.run_path(
            str(plugin / "scripts" / "codex_release" / "accept_codex_stable.py")
        ),
    ]
    for validator in validators:
        assert (
            validator["EXPECTED_BEHAVIOR_OWNERSHIP"] == contract["behavior_ownership"]
        )
        assert (
            validator["EXPECTED_STABLE_ACTIVATION_GATE"]
            == contract["stable_activation_gate"]
        )


def test_rehearsal_is_deterministic_posix_safe_and_non_lifecycle(
    tmp_path: Path,
) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(plugin, ".venv/secret.txt", "sk-this-is-excluded-and-never-scanned-123456")
    adapter = tmp_path / "apps" / "evidence-lane-app"
    _write(adapter, "node_modules/cache.js", "ignored\n")
    _write(adapter, "leaked.js.map", "{}\n")
    _write(adapter, "tsconfig.tsbuildinfo", "{}\n")
    _write(plugin, "src/evidence_lane_plugin/__pycache__/cache.pyc", b"ignored")
    _write(plugin, "src/evidence_lane_plugin.egg-info/SOURCES.txt", "ignored\n")
    _write(
        plugin,
        ".codex-plugin/migrated-command-skills/source-command-evi-plan/SKILL.md",
        "Codex-generated compatibility adapter.\n",
    )
    _write(
        plugin,
        "_evidence_lane_rehearsal/exit-slip.json",
        '{"status":"STALE_PACKAGED_METADATA_MUST_BE_REPLACED"}\n',
    )
    _write(plugin, ".env", "OPENAI_API_KEY=sk-this-is-excluded-1234567890\n")

    first = _build(plugin, tmp_path / "first")
    second = _build(plugin, tmp_path / "second")
    assert first["archive"]["sha256"] == second["archive"]["sha256"]  # type: ignore[index]
    assert (
        first["working_source_manifest_sha256"]
        == second["working_source_manifest_sha256"]
    )
    assert first["boundary"] == BOUNDARY
    assert first["governed_candidate_created"] is False
    assert first["git_invoked"] is False
    assert first["accepted_pointer_moved"] is False
    assert first["skill_count"] == 19
    assert first["canonical_lane_count"] == 18
    assert "tests" not in first["exclusion_policy"]["directory_names"]
    assert first["exclusion_policy"]["maintainer_test_prefixes"] == ["tests/tools/"]
    search_toolchain = first["search_toolchain"]
    assert search_toolchain["status"] == "PASS"
    assert search_toolchain["scope"] == "ALL_GOVERNED_PROJECTS"
    assert search_toolchain["record_count"] == 1
    assert [row["tool_id"] for row in search_toolchain["records"]] == [
        "ripgrep",
    ]
    assert search_toolchain["fts_authority"]["backend"] == "SQLITE_FTS5"
    assert search_toolchain["deterministic_fallbacks_required"] is True
    assert search_toolchain["raw_paths_included"] is False
    assert Path(str(first["receipt_path"])).name.startswith("LOCAL_PACKAGE_REHEARSAL_")

    first_archive = (
        Path(str(first["receipt_path"])).parent
        / first["archive"][  # type: ignore[index]
            "filename"
        ]
    )
    second_archive = (
        Path(str(second["receipt_path"])).parent
        / second["archive"][  # type: ignore[index]
            "filename"
        ]
    )
    assert first_archive.read_bytes() == second_archive.read_bytes()
    with zipfile.ZipFile(first_archive) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        assert names == sorted(names)
        assert all("\\" not in name and ".." not in Path(name).parts for name in names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in infos)
        assert all(((info.external_attr >> 16) & 0o777) == 0o600 for info in infos)
        assert not any(".venv" in name for name in names)
        assert not any("node_modules" in name for name in names)
        assert not any("__pycache__" in name for name in names)
        assert not any(".egg-info" in name for name in names)
        assert not any("migrated-command-skills" in name for name in names)
        assert not any(name.startswith("tests/tools/") for name in names)
        assert names.count("manifests/package/exit-slip.json") == 1
        assert not any(name.startswith("_evidence_lane_rehearsal/") for name in names)
        assert not any(name.endswith(".map") for name in names)
        assert not any(name.endswith(".tsbuildinfo") for name in names)
        assert ".env" not in names
        for required in (
            "README.md",
            "LICENSE.md",
            "COPYRIGHT.md",
            "THIRD_PARTY_NOTICES.md",
        ):
            assert required in names
        assert "scripts/codex-release-channel.json" in names
        assert "scripts/codex_release/install_codex_stable.py" in names
        assert "scripts/codex_release/build_codex_exact_commit_package.py" in names
        assert "scripts/codex_release/seal_codex_git_ci_release_authority.py" in names
        assert "scripts/codex_release/Prepare-EvidenceLaneCodexRestart.ps1" in names
        assert "scripts/codex_release/Restart-EvidenceLaneCodex.ps1" not in names
        assert "scripts/codex_release/drain_codex_task_turns.py" not in names
        assert (
            "scripts/codex_release/Manage-EvidenceLaneCodexGoalRecovery.ps1"
            not in names
        )
        assert "scripts/codex_release/Switch-EvidenceLaneCodexSlot.ps1" not in names
        assert "scripts/codex_release/accept_codex_stable.py" in names
        assert "chatgpt-app-connection.json" not in names
        assert "chatgpt-app-submission.json" not in names
        assert "release-channels.json" not in names
        assert not any(name.startswith("evidence/") for name in names)
        assert not any(name.startswith("remote_adapter/") for name in names)
        exit_slip = json.loads(archive.read("manifests/package/exit-slip.json"))
        assert exit_slip["status"] == (
            "LOCAL_REHEARSAL_VERIFIED_NOT_A_GOVERNED_CANDIDATE"
        )


@pytest.mark.parametrize("suffix", [".glb", ".gltf"])
def test_rehearsal_rejects_generated_3d_assets(tmp_path: Path, suffix: str) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(plugin, f"assets/generated-model{suffix}", b"3d")
    with pytest.raises(PackageBoundaryError, match="Generated 3D assets"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_rejects_high_confidence_secret_material(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(
        plugin,
        "config.txt",
        "sk-proj-this-is-not-a-real-key-but-must-be-rejected-123456\n",
    )
    with pytest.raises(PackageBoundaryError, match="openai_key"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_rejects_meshy_dependency_or_mcp_binding(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    _write(
        plugin,
        "pyproject.toml",
        '[project]\nname="fixture"\nversion="3.0.0"\ndependencies=["meshy-sdk==1.0.0"]\n',
    )
    with pytest.raises(PackageBoundaryError, match="Meshy"):
        _build(plugin, tmp_path / "output")


def test_rehearsal_fails_closed_on_skill_inventory_drift(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    (plugin / "skills" / "skill-18" / "SKILL.md").unlink()
    with pytest.raises(
        PackageBoundaryError, match="two-slot Git-main/local-testing contract drifted"
    ):
        _build(plugin, tmp_path / "output")


def test_rehearsal_fails_closed_without_package_legal_notices(tmp_path: Path) -> None:
    plugin = _plugin_fixture(tmp_path)
    (plugin / "THIRD_PARTY_NOTICES.md").unlink()
    with pytest.raises(PackageBoundaryError, match="Required package members"):
        _build(plugin, tmp_path / "output")
