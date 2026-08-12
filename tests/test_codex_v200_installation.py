from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
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
ACCEPTANCE = PLUGIN / "scripts" / "codex_release" / "accept_codex_stable.py"
HOOK_NOTICE_MARKERS = {
    "session_start.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
    "prompt_submit.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
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
    source = tmp_path / "source"
    version = "2.0.0+codex.20260812010807"
    _write(
        source / ".codex-plugin" / "plugin.json",
        json.dumps(
            {
                "name": "evidence-lane-plugin",
                "version": version,
                "mcpServers": "./.mcp.json",
            }
        ),
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
                    "release": "2.0.0",
                    "slot_role": "stable-build",
                    "codex_marketplace_slot": "evidence-lane-v200-github",
                    "byte_frozen": False,
                    "updates_require_verified_unique_build_identity": True,
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
                "remote_git_policy": {
                    "effective_release": "2.0.0",
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
        source / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1",
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
                    "PostToolUse": [
                        {
                            "matcher": "mcp__evidence_lane__pv_plan_steer_delta",
                            "hooks": [{"type": "command"}],
                        }
                    ],
                    "Stop": [{"hooks": [{"type": "command"}]}],
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
        '[project]\nname = "evidence-lane-plugin"\nversion = "2.0.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "constants.py",
        'ENGINE_VERSION = "2.0.0"\n',
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
                "status": "PASS",
                "archive": {"filename": archive.name, "sha256": archive_sha},
                "governed_candidate_created": False,
                "accepted_pointer_moved": False,
            }
        ),
    )
    return archive, receipt, version


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

    marketplace = codex_home / "local-marketplaces" / "evidence-lane-v200-github"
    installed = marketplace / "plugins" / "evidence-lane-plugin"
    assert result["status"] == "PASS"
    assert result["plugin"]["version"] == version
    assert result["marketplace"]["state"] == "STAGED"
    assert result["activation"]["state"] == "STAGED_RESTART_NOT_YET_REQUIRED"
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
    assert result["surface_change_display"]["hooks"]["count"] == 4
    assert result["surface_change_display"]["hooks"]["count_semantics"] == (
        "REGISTERED_EVENT_COUNT"
    )
    assert result["surface_change_display"]["hooks"]["hook_file_count"] == 5
    assert result["surface_change_display"]["hooks"]["handler_count"] == 4
    assert result["surface_change_display"]["hooks"]["registered_events"] == [
        "PostToolUse",
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
    )["name"] == "evidence-lane-v200-github"

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


def test_explicit_host_stable_baseline_survives_two_pass_install(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, receipt, _ = _fixture_archive(tmp_path)
    source = tmp_path / "source"
    prior_source = tmp_path / "prior-source"
    shutil.copytree(source, prior_source)
    (prior_source / "hooks" / "post_tool_use.py").unlink()
    prior_hooks_path = prior_source / "hooks" / "hooks.json"
    prior_hooks = json.loads(prior_hooks_path.read_text(encoding="utf-8"))
    del prior_hooks["hooks"]["PostToolUse"]
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
        "PostToolUse"
    ]
    assert preflight["surface_change_display"]["hooks"]["added_files"] == [
        "post_tool_use.py"
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


def test_channel_switch_disables_prior_plugin_without_deleting_cache(
    tmp_path: Path,
) -> None:
    module = _module()
    config = tmp_path / "codex" / "config.toml"
    _write(
        config,
        """[plugins.\"evidence-lane-plugin@evidence-lane-v150-github\"]
enabled = true

[plugins.\"evidence-lane-plugin@evidence-lane-v150-github\".mcp_servers.\"evidence-lane\"]
enabled = true

[plugins.\"evidence-lane-plugin@evidence-lane-v200-github\"]
enabled = true

[plugins.\"github@openai-curated\"]
enabled = true
""",
    )
    receipt = module._set_exclusive_evidence_lane_channel(
        config_path=config,
        data_root=tmp_path / "pv",
    )
    updated = config.read_text(encoding="utf-8")

    assert updated.count("enabled = false") == 2
    assert updated.count("enabled = true") == 3
    assert "github@openai-curated" in updated
    assert (
        '[plugins."evidence-lane-plugin@evidence-lane-v200-github".'
        'mcp_servers."evidence-lane"]\nenabled = true'
    ) in updated
    assert Path(receipt["backup"]).is_file()
    assert receipt["previous_release_cache_deleted"] is False

    replay = module._set_exclusive_evidence_lane_channel(
        config_path=config,
        data_root=tmp_path / "pv",
    )
    replayed = config.read_text(encoding="utf-8")
    assert replayed == updated
    assert replay["changed_selectors"] == []
    assert replayed.count(
        '[plugins."evidence-lane-plugin@evidence-lane-v200-github".'
        'mcp_servers."evidence-lane"]'
    ) == 1


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
    assert "Get-RootCodexProcess $TargetProcessId" in text
    assert 'Stop-Process -Id $TargetProcessId -Force' in text
    assert 'Stop-Process -Name' not in text
    assert "utf8NoBOM" not in text
    assert "$utf8NoBom = [System.Text.UTF8Encoding]::new($false)" in text
    assert "[System.IO.File]::WriteAllText(" in text
    assert 'user_reentry_action = "NONE_AUTO_OPEN_EXACT_TASK"' in text
    assert '$taskUri = "codex://threads/$TaskId"' in text
    assert 'task_navigation_mode = "CODEX_THREAD_DEEPLINK"' in text
    assert "Assert-CodexThreadProtocol" in text
    assert "ConvertTo-WindowsCommandLineArgument" in text
    assert "-ArgumentList $argumentLine" in text
    assert "Start-Process -FilePath $taskUri" in text
    assert 'schema = "evidence-lane.codex-task-binding.v1"' in text
    assert 'state = "EXACT_TASK_BINDING_PREPARED"' in text
    assert 'claim_scope = "EXACT_CODEX_THREAD_ID_ONLY"' in text
    assert "task_binding_receipt_sha256" in text
    assert 'state = "EXACT_TASK_RELAUNCH_REQUESTED_CODEX_ROOT_OBSERVED"' in text
    assert "RELAUNCH_REQUESTED_USER_MUST_OPEN_SAME_TASK" not in text
    assert "coordinate_clicking_used = $false" in text
    assert "active_task_ui_independently_proven = $false" in text
    assert "lifecycle_resume_call_required = $false" in text
    assert "state_travel_required = $false" in text
    assert "hot_reload_claimed = $false" in text


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
        "evidence-lane-v200-github"
    ) / "plugins" / "evidence-lane-plugin"
    installed = tmp_path / "codex" / "plugins" / "cache" / (
        "evidence-lane-v200-github"
    ) / "evidence-lane-plugin" / version
    source = tmp_path / "source"
    (source / "_evidence_lane_rehearsal").rename(
        tmp_path / "excluded-rehearsal-metadata"
    )
    _write(
        source / "pyproject.toml",
        '[project]\nname = "evidence-lane-plugin"\nversion = "2.0.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "constants.py",
        'ENGINE_VERSION = "2.0.0"\n',
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
                    "PostToolUse": [
                        {
                            "matcher": "mcp__evidence_lane__pv_plan_steer_delta",
                            "hooks": [{"type": "command"}],
                        }
                    ],
                    "Stop": [{"hooks": [{"type": "command"}]}],
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

    config = tmp_path / "codex" / "config.toml"
    _write(
        config,
        "[plugins.\"evidence-lane-plugin@evidence-lane-v200-github\"]\n"
        "enabled = true\n",
    )
    installation = {
        "schema": "evidence-lane.codex-stable-installation.v2",
        "status": "PASS",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest().upper(),
        "activation": {
            "state": "INSTALLED_RESTART_REQUIRED",
            "plugin_add": {"installedPath": str(installed)},
        },
        "generated_cache_written_directly": False,
        "previous_release_cache_deleted": False,
        "fallback_materialization_gate": (
            "POST_EXACT_PV11_APPROVE_AND_NATIVE_FUSE"
        ),
        "fallback_materialized": False,
        "live_cache_cleanup_deferred_until_exact_pv11_acceptance": True,
        "two_slot_operator_packaged": True,
        "credential_requested_or_stored": False,
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
