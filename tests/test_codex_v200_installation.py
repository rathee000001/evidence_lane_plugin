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
RESTART = PLUGIN / "scripts" / "codex_release" / "Prepare-EvidenceLaneCodexRestart.ps1"
PURGED_COMBINED_UPDATE = (
    PLUGIN / "scripts" / "codex_release" / "Update-EvidenceLaneCodexStableAndResume.ps1"
)
ACCEPTANCE = PLUGIN / "scripts" / "codex_release" / "accept_codex_stable.py"
HOOK_NOTICE_MARKERS = {
    "lifecycle_boundary.py": "EVIDENCE_LANE_LIFECYCLE_BOUNDARY=",
    "optional_event_observer.py": "EVIDENCE_LANE_OPTIONAL_EVENT_OBSERVATION=",
    "permission_request.py": "EVIDENCE_LANE_OPTIONAL_EVENT_OBSERVATION=",
    "session_start.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_NOTICE=",
    "prompt_submit.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
    "pre_tool_use.py": "EVIDENCE_LANE_PRE_TOOL_USE=",
    "post_tool_use.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=",
    "stop_response.py": "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY=",
    "subagent_start.py": "EVIDENCE_LANE_OPTIONAL_EVENT_OBSERVATION=",
    "subagent_stop.py": "EVIDENCE_LANE_OPTIONAL_EVENT_OBSERVATION=",
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


def test_local_update_never_attempts_historical_windows_root_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    codex_home = tmp_path / "codex-home"
    marketplace_root = (
        codex_home / "local-marketplaces" / module.LOCAL_TESTING_MARKETPLACE_NAME
    )
    marketplace_root.mkdir(parents=True)
    _write(marketplace_root / "stale.txt", "old")
    extracted = tmp_path / "extracted"
    _write(extracted / ".codex-plugin" / "plugin.json", '{"name":"fixture"}')
    _write(extracted / "skills" / "evi" / "SKILL.md", "fixture")
    surface = {
        "plugin_version": "3.0.0+codex.fixture",
        "hooks": {
            "count": 0,
            "records": [],
            "inventory_sha256": "H" * 64,
            "registered_event_count": 0,
            "registered_events": [],
            "handler_count": 0,
            "hook_file_count": 0,
            "file_inventory_sha256": "F" * 64,
            "event_inventory_sha256": "E" * 64,
        },
        "skills": {"count": 0, "records": [], "inventory_sha256": "S" * 64},
        "search_toolchain": {
            "status": "PASS",
            "record_count": 0,
            "manifest_sha256": "M" * 64,
            "inventory_sha256": "I" * 64,
            "fts_authority": {},
            "resolution_order": [],
            "records": [],
            "fallbacks_required": True,
        },
        "catalog": {"tools": 0, "read": 0, "write": 0, "skills": 0},
        "surface_inventory_sha256": "X" * 64,
    }
    identity = {"surface_inventory": surface}
    real_replace = module.os.replace

    def reject_root_rotation(source: object, target: object) -> None:
        assert Path(source).resolve() != marketplace_root.resolve(), (
            "historical Windows whole-marketplace rotation was attempted"
        )
        real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", reject_root_rotation)
    result = module._stage_marketplace(
        extracted=extracted,
        marketplace_root=marketplace_root,
        data_root=tmp_path / "data",
        identity=identity,
        archive_sha256="A" * 64,
        marketplace_name=module.LOCAL_TESTING_MARKETPLACE_NAME,
        comparison_surface=identity["surface_inventory"],
        comparison_baseline={"status": "PASS"},
    )
    assert result["marketplace_rotation_mode"] == ("PLUGIN_CREATOR_LOCAL_SOURCE_UPDATE")
    assert result["route_law"] == module.PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW
    assert result["windows_root_rotation_attempted"] is False
    assert not (marketplace_root / "stale.txt").exists()
    assert (
        marketplace_root
        / "plugins"
        / module.PLUGIN_NAME
        / ".codex-plugin"
        / "plugin.json"
    ).is_file()


def test_plugin_creator_local_cache_boundary_seals_exact_task_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    codex_home = tmp_path / "codex-home"
    data_root = tmp_path / "pv"
    authority_root = data_root / "installations" / "codex-v200"
    stage_path = authority_root / "INSTALL_STAGE.json"
    _write(stage_path, "{}")
    selector = f"{module.PLUGIN_NAME}@{module.LOCAL_TESTING_MARKETPLACE_NAME}"
    old_version = "3.0.0+codex.old"
    target_version = "3.0.0+codex.new"
    marketplace_root = (
        codex_home / "local-marketplaces" / module.LOCAL_TESTING_MARKETPLACE_NAME
    )
    marketplace_plugin = marketplace_root / "plugins" / module.PLUGIN_NAME
    marketplace_plugin.mkdir(parents=True)
    identity = {
        "plugin_id": module.PLUGIN_NAME,
        "version": target_version,
        "manifest_sha256": "A" * 64,
        "catalog": {"tools": 91, "read": 30, "write": 61, "skills": 26},
        "surface_inventory": {"surface_inventory_sha256": "B" * 64},
    }
    stage = {
        "schema": module.INSTALL_SCHEMA,
        "status": "PASS",
        "archive_sha256": "C" * 64,
        "plugin": identity,
        "marketplace": {
            "name": module.LOCAL_TESTING_MARKETPLACE_NAME,
            "root": str(marketplace_root),
            "route_law": module.PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW,
            "marketplace_rotation_mode": "PLUGIN_CREATOR_LOCAL_SOURCE_UPDATE",
            "windows_root_rotation_attempted": False,
        },
        "activation": {
            "state": "STAGED_RESTART_NOT_YET_REQUIRED",
            "plugin_add_invoked": False,
        },
        "restart_required": False,
        "runtime_ready_before_task_reopen": False,
        "generated_cache_written_directly": False,
        "previous_release_cache_deleted": False,
        "candidate_created_or_accepted": False,
        "pointer_moved": False,
        "hil_inferred": False,
    }
    monkeypatch.setattr(
        module,
        "_load_self_sealed_json",
        lambda **_kwargs: (stage, stage_path.resolve(), "D" * 64),
    )
    monkeypatch.setattr(module, "_validate_plugin", lambda _path: identity)
    monkeypatch.setattr(
        module,
        "_source_inventory",
        lambda _path: {"manifest_sha256": "E" * 64},
    )
    plugin_list = {
        "installed": [
            {
                "pluginId": selector,
                "version": old_version,
                "enabled": True,
            },
            {
                "pluginId": f"{module.PLUGIN_NAME}@{module.MARKETPLACE_NAME}",
                "version": "3.0.0+codex.main",
                "enabled": False,
            },
        ]
    }
    monkeypatch.setattr(module, "_run_codex", lambda *_args, **_kwargs: plugin_list)

    def materialize(**_kwargs: object) -> dict[str, object]:
        target = (
            codex_home
            / "plugins"
            / "cache"
            / module.LOCAL_TESTING_MARKETPLACE_NAME
            / module.PLUGIN_NAME
            / target_version
        )
        target.mkdir(parents=True)
        return {
            "status": "PASS",
            "route": "CODEX_PLUGIN_ADD",
            "invocation_count": 1,
            "outcome": ("OLD_SELECTED_TARGET_CACHE_MATERIALIZED_HOST_RESTART_REQUIRED"),
        }

    monkeypatch.setattr(
        module,
        "_run_plugin_creator_local_cache_materialization",
        materialize,
    )
    monkeypatch.setattr(
        module,
        "_disabled_hook_state",
        lambda **_kwargs: {
            "status": "PASS",
            "selector": selector,
            "hook_count": 11,
            "all_enabled": False,
            "hooks_enabled_by_update": False,
            "config_sha256": "F" * 64,
        },
    )
    monkeypatch.setattr(
        module,
        "_prewarm_installed_runtime",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "runtime_projection_root": "runtime-root",
            "runtime_python_sha256": "7" * 64,
            "runtime_ready_before_task_reopen": True,
        },
    )
    monkeypatch.setattr(
        module,
        "_initialize_installed_hook_event_isolation",
        lambda *_args, **_kwargs: {
            "status": "PASS",
            "verified_before_install_activation": True,
            "persistent_kill_switch": True,
            "kill_switch_receipt_path": "kill-switch.json",
            "kill_switch_receipt_sha256": "8" * 64,
            "policy_sha256": "9" * 64,
        },
    )

    result = module._seal_plugin_creator_local_cache_restart(
        stage_receipt_path=stage_path,
        stage_receipt_sha256="D" * 64,
        executable=tmp_path / "codex.exe",
        codex_home=codex_home,
        data_root=data_root,
    )

    assert result["activation"]["state"] == (
        module.PLUGIN_CREATOR_LOCAL_CACHE_RESTART_STATE
    )
    assert result["activation"]["plugin_add"]["old_active_version"] == old_version
    assert result["activation_authority"]["route_law"] == (
        module.PLUGIN_CREATOR_LOCAL_UPDATE_ONLY_LAW
    )
    assert result["activation_authority"]["current_route_dispatch"] == {
        "status": "PASS",
        "route": "PLUGIN_CREATOR_ACTIVE_LOCAL_SLOT_UPDATE",
        "selector": selector,
        "old_active_version": old_version,
        "target_version": target_version,
        "enabled_evidence_lane_count": 1,
        "main_selector_enabled": False,
        "standalone_plugin_add_invoked": False,
        "cache_materialization_deferred_to_staged_receipt_route": True,
    }
    assert result["restart_required"] is True
    assert result["runtime_ready_before_task_reopen"] is False
    assert result["hooks_enabled_by_update"] is False
    assert result["accepted_two_slot_registry_mutated"] is False
    assert result["state_travel_invoked"] is False
    assert result["activation"]["runtime_prewarm"]["status"] == "PASS"
    assert result["activation"]["hook_event_isolation"]["status"] == "PASS"
    assert (
        result["activation"]["hook_event_isolation"][
            "verified_before_install_activation"
        ]
        is True
    )


def test_disabled_hook_state_counts_every_handler_and_event(tmp_path: Path) -> None:
    module = _module()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    selector = f"{module.PLUGIN_NAME}@{module.LOCAL_TESTING_MARKETPLACE_NAME}"
    sections: list[str] = []
    for event in sorted(module.EXPECTED_PACKAGE_HOOK_EVENTS):
        token = module.re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
        for ordinal in range(4):
            key = f"{selector}:hooks/hooks.json:{token}:0:{ordinal}"
            sections.extend(
                [
                    f'[hooks.state."{key}"]',
                    f'trusted_hash = "sha256:{ordinal:064x}"',
                    "enabled = false",
                    "",
                ]
            )
    (codex_home / "config.toml").write_text("\n".join(sections), encoding="utf-8")

    result = module._disabled_hook_state(
        codex_home=codex_home,
        selector=selector,
        expected_hooks={"count": 11, "handler_count": 44},
    )

    assert result["status"] == "PASS"
    assert result["hook_count"] == 11
    assert result["hook_handler_count"] == 44
    assert result["all_enabled"] is False


def test_native_hook_event_control_requires_proof_before_enablement(
    tmp_path: Path,
) -> None:
    module = _module()
    with pytest.raises(
        module.InstallationError,
        match="Every hook enabled by native control requires",
    ):
        module._set_native_hook_event_states(
            executable=tmp_path / "codex.exe",
            codex_home=tmp_path / "codex-home",
            data_root=tmp_path / "data",
            hook_cwd=tmp_path,
            plugin_selector=(
                f"{module.PLUGIN_NAME}@{module.LOCAL_TESTING_MARKETPLACE_NAME}"
            ),
            desired_event_states={"preToolUse": True},
            verified_event_receipts={},
            changed_by="test",
        )

    source = SCRIPT.read_text(encoding="utf-8")
    assert '"hooks/list"' in source
    assert '"config/read"' in source
    assert '"config/batchWrite"' in source
    assert '"direct_config_file_write": False' in source
    assert '"windows_ui_control_used": False' in source


def test_restart_preparation_accepts_only_dedicated_plugin_creator_cache_state() -> (
    None
):
    text = RESTART.read_text(encoding="utf-8")
    assert "PLUGIN_CREATOR_LOCAL_CACHE_MATERIALIZED_RESTART_REQUIRED" in text
    assert "TERMINAL_SAFE_RESTART_PREPARED_NOT_EXECUTED" in text
    assert "current_turn_terminal_event_required_before_app_close = $true" in text
    assert "drain_utility_allowed = $false" in text
    assert "programmatic_process_stop_allowed = $false" in text
    assert "scheduled_restart_child_allowed = $false" in text
    assert "machine_wide_protocol_handler_allowed = $false" in text
    assert '"turn/interrupt"' not in text
    assert "NATIVE_ACTIVE_GOAL_EXACT_TASK_BINDING" not in text
    assert "Manage-EvidenceLaneCodexGoalRecovery" not in text
    assert "Switch-EvidenceLaneCodexSlot" not in text
    assert "LocalTestCommitReceipt" not in text
    assert "ActivateForProtocol" not in text
    assert "ShellExecuteEx" not in text
    assert "Stop-Process" not in text
    assert "New-ScheduledTaskAction" not in text


def test_plugin_creator_cache_materialization_classifies_loaded_old_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    target = "3.0.0+codex.target"
    old = "3.0.0+codex.old"

    def fake_run(arguments: list[str], **_kwargs: object):
        return subprocess.CompletedProcess(
            arguments,
            1,
            "",
            (
                "failed to activate updated plugin cache version "
                f"{target} while {old} remains active"
            ),
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    result = module._run_plugin_creator_local_cache_materialization(
        executable=tmp_path / "codex.exe",
        codex_home=tmp_path / "codex-home",
        plugin_selector=(
            f"{module.PLUGIN_NAME}@{module.LOCAL_TESTING_MARKETPLACE_NAME}"
        ),
        target_version=target,
        old_active_version=old,
    )

    assert result["status"] == "PASS"
    assert result["route"] == "CODEX_PLUGIN_ADD"
    assert result["invocation_count"] == 1
    assert result["outcome"] == (
        "OLD_SELECTED_TARGET_CACHE_MATERIALIZED_HOST_RESTART_REQUIRED"
    )


def test_codex_cli_resolution_uses_npm_native_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    appdata = tmp_path / "AppData" / "Roaming"
    executable = appdata / module.NPM_CODEX_CLI_RELATIVE_PATH
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture")
    monkeypatch.setenv("APPDATA", str(appdata))

    calls: list[list[str]] = []

    def fake_run(arguments: list[str], **_kwargs: object):
        calls.append(arguments)
        return subprocess.CompletedProcess(
            arguments,
            0,
            "codex-cli 0.145.0\n",
            "",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    resolved = module._resolve_codex_cli_executable(
        None,
        verify_version=True,
    )

    assert resolved == executable.resolve()
    assert calls == [[str(executable.resolve()), "--version"]]


def test_codex_cli_resolution_rejects_packaged_windowsapps_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    packaged = tmp_path / "WindowsApps" / "OpenAI.Codex_fixture" / "codex.exe"
    packaged.parent.mkdir(parents=True)
    packaged.write_bytes(b"fixture")
    probed = False

    def fail_if_probed(*_args: object, **_kwargs: object):
        nonlocal probed
        probed = True
        raise AssertionError("the packaged app route must not be probed")

    monkeypatch.setattr(module.subprocess, "run", fail_if_probed)
    with pytest.raises(module.InstallationError, match="forbidden install route"):
        module._resolve_codex_cli_executable(
            packaged,
            verify_version=True,
        )
    assert probed is False


def _fixture_hook_source(marker: str) -> str:
    return (
        "render_persistent_notice = None\n"
        "result = {}\n"
        'result["systemMessage"] = ""\n'
        f'print("{marker}")\n'
    )


def _fixture_catalog_source() -> str:
    functions = ["_READ_ONLY = object()", "_WRITE = object()"]
    for index in range(91):
        annotation = "_READ_ONLY" if index < 30 else "_WRITE"
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
    version = "3.0.0+codex.20260816074428"
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
    current_release_contract = json.loads(
        (PLUGIN / "scripts" / "codex-release-channel.json").read_text("utf-8")
    )
    _write(
        source / "scripts" / "codex-release-channel.json",
        json.dumps(current_release_contract),
    )
    _write(
        source / "scripts" / "codex_release" / "install_codex_stable.py", "# fixture\n"
    )
    _write(
        source / "scripts" / "codex_release" / "build_codex_exact_commit_package.py",
        "# fixture\n",
    )
    _write(
        source / "scripts" / "codex_release" / "seal_codex_git_ci_release_authority.py",
        "# fixture\n",
    )
    _write(
        source / "scripts" / "codex_release" / "seal_external_release_receipts.py",
        "# fixture\n",
    )
    _write(
        source / "scripts" / "codex_release" / "seal_github_app_production_delivery.py",
        "# fixture\n",
    )
    _write(
        source / "scripts" / "codex_release" / "Prepare-EvidenceLaneCodexRestart.ps1",
        "# fixture\n",
    )
    _write(
        source / "scripts" / "codex_release" / "accept_codex_stable.py", "# fixture\n"
    )
    (source / "hooks").mkdir(parents=True, exist_ok=True)
    shutil.copy2(PLUGIN / "hooks" / "hooks.json", source / "hooks" / "hooks.json")
    shutil.copy2(
        PLUGIN / "hooks" / "logical-actions.json",
        source / "hooks" / "logical-actions.json",
    )
    for name, marker in HOOK_NOTICE_MARKERS.items():
        _write(
            source / "hooks" / name,
            _fixture_hook_source(marker),
        )
    _write(source / "hooks" / "invoke_hook.py", "# sealed fixture wrapper\n")
    _write(source / "hooks" / "invoke_hook.ps1", "# sealed fixture wrapper\n")
    shutil.copy2(
        PLUGIN / "hooks" / "behavior_handoff.py",
        source / "hooks" / "behavior_handoff.py",
    )
    shutil.copy2(
        PLUGIN / "hooks" / "event_isolation.py",
        source / "hooks" / "event_isolation.py",
    )
    shutil.copy2(
        PLUGIN / "hooks" / "event_isolation_policy.json",
        source / "hooks" / "event_isolation_policy.json",
    )
    shutil.copy2(
        PLUGIN / "hooks" / "EvidenceLaneHookHost.exe",
        source / "hooks" / "EvidenceLaneHookHost.exe",
    )
    for name in (
        "subhook_emit.py",
        "subhook_pipeline.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_validate.py",
    ):
        shutil.copy2(PLUGIN / "hooks" / name, source / "hooks" / name)
    shutil.copytree(PLUGIN / "toolchains", source / "toolchains")
    _write(
        source / "pyproject.toml",
        '[project]\nname = "evidence-lane-plugin"\nversion = "3.0.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "constants.py",
        'ENGINE_VERSION = "3.0.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "mcp_server.py",
        _fixture_catalog_source(),
    )
    for number in range(26):
        _write(source / "skills" / f"skill-{number:02d}" / "SKILL.md", "# Test\n")
    _write(source / "README.md", "# Evidence Lane\n")
    package_proofs = {
        "source-manifest.json": {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.source-manifest"
            )
        },
        "skill-inventory.json": {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.skill-inventory"
            )
        },
        "package-surface-coherence.json": {
            "schema": "evidence-lane.package-surface-coherence.v1",
            "status": "PASS",
        },
        "systemwide-route-audit.json": {
            "schema": "evidence-lane.systemwide-route-audit.v1",
            "status": "PASS",
        },
        "exit-slip.json": {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.exit-slip"
            )
        },
    }
    for name, proof in package_proofs.items():
        _write(source / "manifests" / "package" / name, json.dumps(proof))
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
                "systemwide_route_audit": {
                    "schema": "evidence-lane.systemwide-route-audit.v1",
                    "status": "PASS",
                    "active_row": 265,
                    "receipt_sha256": "B" * 64,
                    "file_sha256": "C" * 64,
                    "plan_rows_sha256": "D" * 64,
                    "current_registry_sha256": "E" * 64,
                    "public_tool_count": 91,
                    "obsolete_public_tools": [],
                    "consumer_parity_status": "PASS",
                    "obsolete_route_purge_status": "PASS",
                    "systemwide_regression_status": "PASS",
                    "systemwide_regression_file_sha256": "F" * 64,
                    "skill_current_route_audit_status": "PASS",
                    "skill_current_route_audit_sha256": "1" * 64,
                    "accepted_archive_queried": False,
                    "candidate_created_or_cleared": False,
                    "pointer_moved": False,
                },
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
        "boundary": "GOVERNED_GIT_MAIN_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT",
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest().upper(),
        "package_receipt_sha256": hashlib.sha256(package_receipt.read_bytes())
        .hexdigest()
        .upper(),
        "working_source_manifest_sha256": package["working_source_manifest_sha256"],
        "plugin_source_manifest_sha256": package["exact_commit_export"][
            "plugin_source_manifest_sha256"
        ],
        "plugin_source_member_count": package["exact_commit_export"][
            "plugin_source_member_count"
        ],
        "source": {
            "branch": "main",
            "commit": commit,
            "tree": tree,
            "exact_commit_export": True,
            "exact_commit_projection_clean": True,
            "working_checkout_clean_required": False,
            "untracked_bytes_excluded": True,
        },
        "remote_git": {
            "route": "github_app_main_fast_forward_v3",
            "promotion_status": "FAST_FORWARDED",
            "source_branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
            "target_branch": "main",
            "remote_branch_commit": commit,
            "protected_branch": True,
            "force_push": False,
            "source_tree_reused": True,
            "blob_reupload_count": 0,
            "native_receipt_sha256": "B" * 64,
        },
        "github_ci": {
            "status": "PASS",
            "repository": "rathee000001/evidence_lane_plugin",
            "branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
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
            "branch": "agent/evi-v300-systemwide-release-hil-v3.0.0",
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
    core["receipt_sha256"] = (
        hashlib.sha256(
            (
                json.dumps(
                    core,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        .hexdigest()
        .upper()
    )
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


def test_local_slot_requires_systemwide_route_audit_receipt() -> None:
    module = _module()
    with pytest.raises(module.InstallationError, match="system-wide"):
        module._require_local_systemwide_route_audit({})
    audit = module._require_local_systemwide_route_audit(
        {
            "systemwide_route_audit": {
                "schema": "evidence-lane.systemwide-route-audit.v1",
                "status": "PASS",
                "public_tool_count": 91,
                "consumer_parity_status": "PASS",
                "obsolete_route_purge_status": "PASS",
                "systemwide_regression_status": "PASS",
                "systemwide_regression_file_sha256": "C" * 64,
                "skill_current_route_audit_status": "PASS",
                "skill_current_route_audit_sha256": "D" * 64,
                "accepted_archive_queried": False,
                "candidate_created_or_cleared": False,
                "pointer_moved": False,
                "receipt_sha256": "A" * 64,
                "file_sha256": "B" * 64,
            }
        }
    )
    assert audit["status"] == "PASS"


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
        "working_source_manifest_sha256": local["working_source_manifest_sha256"],
        "source_member_count": 1,
        "skill_count": 26,
        "canonical_lane_count": 18,
        "exact_commit_export": {
            "branch": "main",
            "commit": local["base_anchor"]["commit"],
            "tree": local["base_anchor"]["tree"],
            "source_ref": "refs/remotes/origin/main",
            "stable_main_only": True,
            "local_main_attested": True,
            "origin_main_attested": True,
            "plugin_path": "plugins/evidence-lane-plugin",
            "git_archive_sha256": "D" * 64,
            "git_archive_member_count": 1,
            "plugin_source_manifest_sha256": "E" * 64,
            "plugin_source_member_count": 1,
            "projection_clean": True,
            "working_checkout_bytes_used": False,
            "untracked_bytes_used": False,
        },
        "local_rehearsal_receipt_sha256": hashlib.sha256(local_receipt.read_bytes())
        .hexdigest()
        .upper(),
        "git_invoked": True,
        "git_write_invoked": False,
        "governed_candidate_created": False,
        "accepted_pointer_moved": False,
        "hil_inferred": False,
    }
    core["receipt_sha256"] = (
        hashlib.sha256(
            (
                json.dumps(
                    core,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        .hexdigest()
        .upper()
    )
    path = tmp_path / "EXACT_COMMIT_PACKAGE.json"
    _write(
        path,
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )
    assert (
        core["archive"]["sha256"]
        == hashlib.sha256(archive.read_bytes()).hexdigest().upper()
    )
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
    assert result["live_slot_contract"] == {
        "schema": "evidence-lane.codex-two-slot-main-local-registry.v1",
        "status": "NOT_APPLICABLE",
        "exact_live_slot_count": 2,
        "stable_slot": "stable-git-main",
        "stable_selector": ("evidence-lane-plugin@evidence-lane-github"),
        "local_slot": "versioned-local-testing",
        "local_testing_selector": (
            "evidence-lane-plugin@evidence-lane-v300-testing-new"
        ),
        "obsolete_selector_present": False,
        "pre_3_0_fallback_allowed": False,
    }
    assert result["credential_requested_or_stored"] is False
    assert result["surface_change_display"]["state"] == "INITIAL_V2_BASELINE"
    assert result["surface_change_display"]["hooks"]["count"] == 11
    assert result["surface_change_display"]["hooks"]["count_semantics"] == (
        "REGISTERED_EVENT_COUNT"
    )
    assert result["surface_change_display"]["hooks"]["hook_file_count"] == len(
        [
            PLUGIN / "hooks" / "hooks.json",
            PLUGIN / "hooks" / "logical-actions.json",
            *PLUGIN.joinpath("hooks").glob("*.exe"),
            *PLUGIN.joinpath("hooks").glob("*.py"),
            *PLUGIN.joinpath("hooks").glob("*.ps1"),
        ]
    )
    assert result["surface_change_display"]["hooks"]["handler_count"] == 44
    assert (
        result["surface_change_display"]["hooks"]["handler_count_semantics"]
        == "TOTAL_NESTED_HANDLER_ACTION_COUNT"
    )
    assert result["surface_change_display"]["hooks"]["logical_action_count"] == 44
    assert (
        result["surface_change_display"]["hooks"]["logical_action_count_semantics"]
        == "NUMBERED_SERIAL_TRANSPORT_STEPS_INSIDE_HANDLER_ACTIONS"
    )
    assert (
        result["surface_change_display"]["hooks"]["event_action_inventory"][3][
            "actions"
        ][0]["action_number"]
        == "4.1"
    )
    assert (
        result["surface_change_display"]["hooks"]["logical_action_inventory"][3][
            "logical_actions"
        ][0]["logical_action_number"]
        == "4.L1"
    )
    assert result["surface_change_display"]["hooks"]["registered_events"] == [
        "PermissionRequest",
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Stop",
        "SubagentStart",
        "SubagentStop",
        "UserPromptSubmit",
    ]
    assert result["surface_change_display"]["skills"]["count"] == 26
    assert result["surface_change_display"]["search_toolchain"]["status"] == "PASS"
    assert result["surface_change_display"]["search_toolchain"]["record_count"] == 1
    assert [
        row["tool_id"]
        for row in result["surface_change_display"]["search_toolchain"]["records"]
    ] == ["ripgrep"]
    assert (
        result["surface_change_display"]["search_toolchain"]["fts_authority"]["backend"]
        == "SQLITE_FTS5"
    )
    assert (
        result["surface_change_display"]["search_toolchain"]["fallbacks_required"]
        is True
    )
    assert (
        result["surface_change_display"]["search_toolchain"]["raw_paths_included"]
        is False
    )
    assert result["surface_change_display"]["catalog"] == {
        "tools": 91,
        "read": 30,
        "write": 61,
        "skills": 26,
        "changed_from_previous": False,
    }
    assert (
        data_root / "installations" / "codex-v200" / "CURRENT_INSTALLATION.json"
    ).is_file()
    assert not (codex_home / "plugins" / "cache").exists()
    assert not (installed / "_evidence_lane_rehearsal").exists()
    assert (
        json.loads(
            (marketplace / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )["name"]
        == "evidence-lane-github"
    )

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


def test_installer_seals_search_dependencies_and_rejects_binary_tamper(
    tmp_path: Path,
) -> None:
    module = _module()
    _fixture_archive(tmp_path)
    source = tmp_path / "source"

    inventory = module._surface_inventory(
        source,
        version="3.0.0+codex.20260816074428",
    )["search_toolchain"]
    assert inventory["status"] == "PASS"
    assert inventory["record_count"] == 1
    assert [row["tool_id"] for row in inventory["records"]] == [
        "ripgrep",
    ]
    assert inventory["fts_authority"]["model_context_policy"] == (
        "BOUNDED_QUERY_RESULTS_ONLY"
    )
    assert all(len(row["binary_sha256"]) == 64 for row in inventory["records"])
    assert all(row["license_sha256"] for row in inventory["records"])

    rg_binary = source / "toolchains" / "bin" / "windows-x86_64" / "rg.exe"
    rg_binary.write_bytes(rg_binary.read_bytes() + b"tamper")
    with pytest.raises(module.InstallationError, match="identity drifted"):
        module._surface_inventory(
            source,
            version="3.0.0+codex.20260816074428",
        )


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
    local_testing_marketplace = "evidence-lane-v300-testing-new"
    local_testing_selector = f"evidence-lane-plugin@{local_testing_marketplace}"
    obsolete_marketplace = "evidence-lane-v200-task2-build-obsolete"
    obsolete_selector = f"evidence-lane-plugin@{obsolete_marketplace}"
    state = {
        "plugins": {
            legacy_selector: True,
            local_testing_selector: False,
            obsolete_selector: False,
        },
        "marketplaces": {
            legacy_marketplace,
            stable_marketplace,
            local_testing_marketplace,
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
                "stable-git-main": {"plugin_selector": legacy_selector},
                "versioned-local-testing": {"plugin_selector": local_testing_selector},
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
        local_testing_selector: False,
        obsolete_selector: False,
    }
    assert state["marketplaces"] == {
        legacy_marketplace,
        local_testing_marketplace,
        obsolete_marketplace,
    }

    state["plugins"][legacy_selector] = False
    state["plugins"][stable_selector] = True
    state["marketplaces"].add(stable_marketplace)
    cleanup, final_list = module._cleanup_obsolete_after_new_route_proof(
        executable=tmp_path / "codex.exe",
        codex_home=tmp_path / "codex-home",
        plugin_selector=stable_selector,
        local_testing_selector=local_testing_selector,
    )
    assert cleanup["removed_obsolete_selectors"] == sorted(
        [legacy_selector, obsolete_selector]
    )
    assert cleanup["removed_obsolete_marketplaces"] == sorted(
        [legacy_marketplace, obsolete_marketplace]
    )
    assert len(final_list["installed"]) == 2
    assert state["plugins"] == {
        stable_selector: True,
        local_testing_selector: False,
    }
    assert state["marketplaces"] == {
        stable_marketplace,
        local_testing_marketplace,
    }

    authority["registry"]["slots"]["stable-git-main"]["plugin_selector"] = (
        stable_selector
    )
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
    assert state["plugins"] == {local_testing_selector: False}
    assert state["marketplaces"] == {local_testing_marketplace}


def test_legacy_migration_rejects_wrong_canonical_marketplace_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    legacy_selector = "evidence-lane-plugin@evidence-lane-v200-task2-build-stable"
    stable_selector = "evidence-lane-plugin@evidence-lane-github"
    local_testing_selector = "evidence-lane-plugin@evidence-lane-v300-testing-new"
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
                    {"pluginId": local_testing_selector, "enabled": False},
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
                "stable-git-main": {"plugin_selector": legacy_selector},
                "versioned-local-testing": {"plugin_selector": local_testing_selector},
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
        arguments[:3] == ["plugin", "marketplace", "remove"] for arguments in calls
    )


def test_installer_defers_hook_enablement_to_native_post_restart_control() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "PENDING_NATIVE_POST_RESTART_VERIFICATION" in text
    assert "native_post_restart_verification_required" in text
    assert "_trust_sealed_plugin_hooks" not in text
    assert "--trust-sealed-hooks" not in text
    assert "_set_native_hook_event_states" in text


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
    mutated["receipt_sha256"] = (
        hashlib.sha256(
            (
                json.dumps(
                    mutated_core,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
        )
        .hexdigest()
        .upper()
    )
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
    marketplace_plugin = tmp_path / "marketplace" / "plugins" / "evidence-lane-plugin"
    _write(package_root / "README.md", "package member\n")
    _write(marketplace_plugin / "README.md", "package member\n")
    _write(marketplace_plugin / "evidence" / "manifest.json", "{}\n")
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
    marketplace_plugin = tmp_path / "marketplace" / "plugins" / "evidence-lane-plugin"
    _write(package_root / "README.md", "package member\n")
    _write(marketplace_plugin / "README.md", "package member\n")
    _write(marketplace_plugin / "evidence" / "manifest.json", "{}\n")
    package_inventory = module._source_inventory(package_root)
    git_inventory = module._source_inventory(marketplace_plugin)

    _write(marketplace_plugin / "evidence" / "manifest.json", "drift\n")
    with pytest.raises(module.InstallationError, match="complete exact Git"):
        module._assert_exact_git_marketplace_source(
            extracted_inventory=package_inventory,
            marketplace_root=tmp_path / "marketplace",
            expected_git_manifest_sha256=git_inventory["manifest_sha256"],
            expected_git_file_count=git_inventory["file_count"],
        )

    _write(marketplace_plugin / "evidence" / "manifest.json", "{}\n")
    _write(package_root / "README.md", "package drift\n")
    with pytest.raises(module.InstallationError, match="package subset"):
        module._assert_exact_git_marketplace_source(
            extracted_inventory=module._source_inventory(package_root),
            marketplace_root=tmp_path / "marketplace",
            expected_git_manifest_sha256=git_inventory["manifest_sha256"],
            expected_git_file_count=git_inventory["file_count"],
        )


def _seed_current_runtime_prewarm_fixture(plugin_root: Path) -> None:
    for relative in (
        "scripts/generate_runtime_license_bundle.py",
        "scripts/codex_release/install_native_toolchain.py",
        "requirements.torch-cpu.lock.txt",
        "requirements.torch-nvidia.lock.txt",
        "requirements.onnx-directml.lock.txt",
        "toolchains/native-tools.v1.json",
        "toolchains/tool-requirement-matrix.v1.json",
        "toolchains/tool-license-inventory.v1.json",
        "schemas/public-action-schemas.v001.json",
    ):
        source = PLUGIN / relative
        target = plugin_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _runtime_license_payload(module: object, plugin_root: Path) -> dict[str, object]:
    tool_license_inventory = json.loads(
        (plugin_root / "toolchains" / "tool-license-inventory.v1.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "schema": "evidence-lane.installed-runtime-license-bundle.v1",
        "status": "PASS",
        "accelerator_profile": "CPU",
        "requirements_lock_sha256": module._sha256(
            plugin_root / "requirements.lock.txt"
        ),
        "requirements_torch_cpu_lock_sha256": module._sha256(
            plugin_root / "requirements.torch-cpu.lock.txt"
        ),
        "requirements_torch_nvidia_lock_sha256": module._sha256(
            plugin_root / "requirements.torch-nvidia.lock.txt"
        ),
        "requirements_onnx_directml_lock_sha256": module._sha256(
            plugin_root / "requirements.onnx-directml.lock.txt"
        ),
        "requirements_toolchain_lock_sha256": module._sha256(
            plugin_root / "requirements.toolchain.lock.txt"
        ),
        "tool_license_inventory_sha256": module._sha256(
            plugin_root / "toolchains" / "tool-license-inventory.v1.json"
        ),
        "tool_license_entry_count": tool_license_inventory["tool_requirement_count"],
        "all_tool_requirements_license_classified": True,
        "all_tool_requirements_have_physical_license_records": True,
        "tool_requirement_license_record_count": tool_license_inventory[
            "requirement_license_record_count"
        ],
        "mcp_inventory_separate": True,
        "distribution_count": 1,
        "receipt_sha256": "B" * 64,
        "manifest_sha256": "C" * 64,
    }


def _native_toolchain_payload() -> dict[str, object]:
    rows = [
        {"tool_id": tool_id, "status": "PASS"}
        for tool_id in (
            "jq",
            "graphviz",
            "poppler",
            "tesseract",
            "ffmpeg",
            "ripgrep",
            "tree_sitter_languages",
            "seven_zip",
        )
    ]
    rows.append(
        {
            "tool_id": "ghostscript",
            "status": "SKIPPED_LICENSE_GRANT_REQUIRED",
        }
    )
    return {
        "schema": "evidence-lane.installed-native-toolchain.v1",
        "status": "PASS",
        "hidden_runtime_only": True,
        "workspace_install_used": False,
        "path_mutated": False,
        "tools": rows,
        "receipt_sha256": "D" * 64,
    }


def _runtime_probe_payload(module: object, plugin_root: Path) -> dict[str, object]:
    return {
        "engine_version": "3.0.0",
        "native_server_identity": "evidence-lane",
        "read_tool_count": 30,
        "tool_count": 91,
        "tool_catalog_sha256": "A" * 64,
        "route_status": "PASS",
        "resource_uri": "ui://evidence-lane/governed-console-v6.html",
        "native_dependency_prewarm_completed": True,
        "runtime_toolchain": {
            "schema": "evidence-lane.runtime-toolchain-prewarm.v1",
            "status": "PASS",
            "matrix_sha256": module._sha256(
                plugin_root / "toolchains" / "tool-requirement-matrix.v1.json"
            ),
            "requirement_count": len(
                json.loads(
                    (
                        plugin_root / "toolchains" / "tool-requirement-matrix.v1.json"
                    ).read_text(encoding="utf-8")
                )["requirements"]
            ),
            "failure_count": 0,
            "receipt_sha256": "E" * 64,
        },
    }


def _runtime_bootstrap_payload(
    module: object,
    plugin_root: Path,
    runtime_root: Path,
    runtime_python: Path,
) -> dict[str, object]:
    return {
        "schema": "evidence-lane.codex-runtime-bootstrap.v1",
        "status": "PASS",
        "runtime_identity": {
            "schema": "evidence-lane.codex-native-runtime.v1",
            "runtime_key": "A" * 64,
            "accelerator_profile": "CPU",
            "requirements_lock_sha256": module._sha256(
                plugin_root / "requirements.lock.txt"
            ),
            "requirements_torch_cpu_lock_sha256": module._sha256(
                plugin_root / "requirements.torch-cpu.lock.txt"
            ),
            "requirements_torch_nvidia_lock_sha256": module._sha256(
                plugin_root / "requirements.torch-nvidia.lock.txt"
            ),
            "requirements_onnx_directml_lock_sha256": module._sha256(
                plugin_root / "requirements.onnx-directml.lock.txt"
            ),
            "selected_torch_lock_sha256": module._sha256(
                plugin_root / "requirements.torch-cpu.lock.txt"
            ),
            "requirements_toolchain_lock_sha256": module._sha256(
                plugin_root / "requirements.toolchain.lock.txt"
            ),
        },
        "runtime_projection_root": str(runtime_root),
        "runtime_environment": str(runtime_root / "venv"),
        "runtime_python": str(runtime_python),
        "toolchain_inspected": False,
        "native_toolchain_required": False,
        "runtime_authority": {
            "schema": "evidence-lane.codex-installed-runtime-authority-prewarm.v1",
            "status": "PASS",
            "installation_version": "3.0.0",
            "flash_plugin_version": "3.0.0+codex.fixture",
            "flash_action": "BUILD_IDENTITY_MIGRATED",
            "flash_receipt_sha256": "F" * 64,
            "flash_build_migration_receipt_sha256": "9" * 64,
            "flash_migration_scope": "NEW_PLUGIN_BUILD_FULL_FLASH_AUTHORITY",
            "project_state_mutated": False,
            "candidate_mutated": False,
            "pointer_moved": False,
        },
    }


def _runtime_prewarm_payload(
    module: object,
    plugin_root: Path,
    runtime_root: Path,
    runtime_python: Path,
) -> dict[str, object]:
    bootstrap = _runtime_bootstrap_payload(
        module,
        plugin_root,
        runtime_root,
        runtime_python,
    )
    bootstrap["schema"] = "evidence-lane.codex-native-runtime-prewarm.v1"
    bootstrap.pop("toolchain_inspected")
    bootstrap.pop("native_toolchain_required")
    bootstrap["runtime_toolchain"] = _runtime_probe_payload(
        module,
        plugin_root,
    )["runtime_toolchain"]
    return bootstrap


def test_installed_runtime_is_prewarmed_before_task_reopen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    plugin_root = tmp_path / "installed"
    _write(plugin_root / "scripts" / "run_mcp.py", "# fixture\n")
    _write(plugin_root / "requirements.lock.txt", "fixture lock\n")
    _write(
        plugin_root / "requirements.toolchain.lock.txt",
        "fixture toolchain lock\n",
    )
    _seed_current_runtime_prewarm_fixture(plugin_root)
    (plugin_root / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / "assets" / "evidence-lane-icon.png",
        plugin_root / "assets" / "evidence-lane-icon.png",
    )
    data_root = tmp_path / "pv"
    runtime_root = data_root / "runtime" / "codex" / ("A" * 32)
    runtime_python = (
        runtime_root / "venv" / "Scripts" / "python.exe"
        if module.os.name == "nt"
        else runtime_root / "venv" / "bin" / "python"
    )
    _write(runtime_python, "fixture runtime")
    stale_license_root = data_root / "runtime" / "licenses" / ("A" * 64)
    _write(
        stale_license_root / "manifest.v1.json",
        json.dumps(
            {
                "schema": "evidence-lane.installed-runtime-license-bundle.v1",
                "status": "PASS",
                "tool_license_inventory_sha256": "0" * 64,
                "tool_license_entry_count": len(
                    json.loads(
                        (
                            plugin_root
                            / "toolchains"
                            / "tool-requirement-matrix.v1.json"
                        ).read_text(encoding="utf-8")
                    )["requirements"]
                ),
            }
        ),
    )
    _write(stale_license_root / "stale-license.txt", "stale")
    calls: list[list[str]] = []

    def fake_run(
        arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        if len(calls) == 1:
            assert kwargs["env"]["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] == str(
                data_root.resolve()
            )
            bootstrap = _runtime_bootstrap_payload(
                module, plugin_root, runtime_root, runtime_python
            )
            return subprocess.CompletedProcess(
                arguments, 0, (json.dumps(bootstrap) + "\n").encode(), b""
            )
        script = Path(arguments[1]).name if len(arguments) > 1 else ""
        if script == "generate_runtime_license_bundle.py":
            payload = _runtime_license_payload(module, plugin_root)
        elif script == "install_native_toolchain.py":
            payload = _native_toolchain_payload()
            output = Path(arguments[arguments.index("--output") + 1])
            _write(output, json.dumps(payload))
        elif script == "run_mcp.py" and "--prewarm-only" in arguments:
            payload = _runtime_prewarm_payload(
                module, plugin_root, runtime_root, runtime_python
            )
        elif script == "run_mcp.py" and "--transport" in arguments:
            tools = list(
                reversed(
                    json.loads(
                        (
                            plugin_root / "schemas" / "public-action-schemas.v001.json"
                        ).read_text(encoding="utf-8")
                    )["tools"]
                )
            )
            responses = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "serverInfo": {
                            "name": "Evidence Lane",
                            "version": "3.0.0",
                        }
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {"tools": tools},
                },
            ]
            return subprocess.CompletedProcess(
                arguments,
                0,
                "".join(json.dumps(row) + "\n" for row in responses).encode("utf-8"),
                b"",
            )
        else:
            payload = _runtime_probe_payload(module, plugin_root)
        return subprocess.CompletedProcess(
            arguments,
            0,
            (json.dumps(payload) + "\n").encode("utf-8"),
            b"",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    receipt = module._prewarm_installed_runtime(
        plugin_root,
        data_root=data_root,
        expected_plugin_version="3.0.0+codex.fixture",
    )

    assert receipt["status"] == "PASS"
    assert receipt["runtime_ready_before_task_reopen"] is True
    assert receipt["native_dependency_prewarm_completed"] is True
    assert receipt["tool_count"] == 91
    assert receipt["tool_catalog_sha256"] == "A" * 64
    assert receipt["resource_uri"] == ("ui://evidence-lane/governed-console-v6.html")
    assert receipt["task_reopened"] is False
    assert receipt["bootstrap_attempt_count"] == 1
    assert receipt["bootstrap_attempts"][0]["returncode"] == 0
    assert receipt["runtime_licenses"]["stale_runtime_license_bundle_purged"] is True
    assert receipt["runtime_authority"]["flash_plugin_version"] == (
        "3.0.0+codex.fixture"
    )
    assert receipt["runtime_authority"]["project_state_mutated"] is False
    assert not (stale_license_root / "stale-license.txt").exists()
    assert len(receipt["receipt_sha256"]) == 64
    assert calls[0] == [
        module.sys.executable,
        str(plugin_root / "scripts" / "run_mcp.py"),
        "--bootstrap-only",
    ]
    assert calls[1][0] == str(runtime_python)
    assert calls[3] == [
        module.sys.executable,
        str(plugin_root / "scripts" / "run_mcp.py"),
        "--prewarm-only",
    ]


def test_installed_runtime_bootstrap_does_not_retry_same_sealed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    plugin_root = tmp_path / "installed"
    _write(plugin_root / "scripts" / "run_mcp.py", "# fixture\n")
    _write(plugin_root / "requirements.lock.txt", "fixture lock\n")
    _write(
        plugin_root / "requirements.toolchain.lock.txt",
        "fixture toolchain lock\n",
    )
    _seed_current_runtime_prewarm_fixture(plugin_root)
    (plugin_root / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / "assets" / "evidence-lane-icon.png",
        plugin_root / "assets" / "evidence-lane-icon.png",
    )
    data_root = tmp_path / "pv"
    calls: list[list[str]] = []

    def fake_run(
        arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        calls.append(arguments)
        assert kwargs["env"]["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] == str(
            data_root.resolve()
        )
        return subprocess.CompletedProcess(
            arguments,
            1,
            b"first stdout\n",
            b"first stderr\n",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.InstallationError, match="failed on the sealed"):
        module._prewarm_installed_runtime(
            plugin_root,
            data_root=data_root,
            expected_plugin_version="3.0.0+codex.fixture",
        )

    assert calls == [
        [
            module.sys.executable,
            str(plugin_root / "scripts" / "run_mcp.py"),
            "--bootstrap-only",
        ]
    ]


def test_installed_runtime_rejects_projection_outside_durable_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    plugin_root = tmp_path / "installed"
    _write(plugin_root / "scripts" / "run_mcp.py", "# fixture\n")
    _write(plugin_root / "requirements.lock.txt", "fixture lock\n")
    _write(
        plugin_root / "requirements.toolchain.lock.txt",
        "fixture toolchain lock\n",
    )
    for name in (
        "requirements.torch-cpu.lock.txt",
        "requirements.torch-nvidia.lock.txt",
        "requirements.onnx-directml.lock.txt",
    ):
        shutil.copy2(PLUGIN / name, plugin_root / name)
    (plugin_root / "assets").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        PLUGIN / "assets" / "evidence-lane-icon.png",
        plugin_root / "assets" / "evidence-lane-icon.png",
    )
    escaped_root = tmp_path / "outside-authority"
    runtime_python = (
        escaped_root / "venv" / "Scripts" / "python.exe"
        if module.os.name == "nt"
        else escaped_root / "venv" / "bin" / "python"
    )
    _write(runtime_python, "fixture runtime")
    bootstrap = _runtime_bootstrap_payload(
        module,
        plugin_root,
        escaped_root,
        runtime_python,
    )

    def fake_run(
        arguments: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[bytes]:
        assert kwargs["env"]["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] == str(
            (tmp_path / "pv").resolve()
        )
        return subprocess.CompletedProcess(
            arguments,
            0,
            (json.dumps(bootstrap) + "\n").encode(),
            b"",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    with pytest.raises(module.InstallationError, match="durable authority"):
        module._prewarm_installed_runtime(
            plugin_root,
            data_root=tmp_path / "pv",
            expected_plugin_version="3.0.0+codex.fixture",
        )


def test_stable_activation_advances_main_registry_without_changing_local_identity(
    tmp_path: Path,
) -> None:
    module = _module()
    data_root = tmp_path / "pv"
    codex_home = tmp_path / "codex"
    registry_path = (
        data_root
        / "installations"
        / "codex-v300"
        / "two-slot-main-local"
        / "CODEX_TWO_SLOT_MAIN_LOCAL_REGISTRY.json"
    )
    baseline = data_root / "installations" / "codex-v200" / "INSTALL_OLD.json"
    _write(baseline, "old stable\n")
    stable_marketplace = "evidence-lane-github"
    stable_selector = f"evidence-lane-plugin@{stable_marketplace}"
    local_selector = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    local_testing = {
        "slot_role": "versioned-local-testing",
        "plugin_selector": local_selector,
        "plugin_version": "3.0.0+codex.local",
        "marketplace_name": "evidence-lane-v300-testing-new",
        "installed_path_sha256": "F" * 64,
        "marketplace_source_type": "local",
        "byte_frozen": False,
        "enabled": True,
        "native_mcp_enabled": True,
        "update_gate": "FRESH_VERSIONED_LOCAL_PACKAGE",
    }
    stable = {
        "slot_role": "stable-git-main",
        "plugin_selector": stable_selector,
        "plugin_version": "3.0.0+codex.test",
        "marketplace_name": stable_marketplace,
        "installed_path_sha256": "A" * 64,
        "marketplace_source_type": "git",
        "byte_frozen": True,
        "enabled": False,
        "native_mcp_enabled": False,
        "update_gate": "GOVERNED_VERIFIED_MAIN_MERGE",
    }
    registry: dict[str, object] = {
        "schema": "evidence-lane.codex-two-slot-main-local-registry.v1",
        "status": "PASS",
        "active_slot": "versioned-local-testing",
        "active_selector": local_selector,
        "exact_live_slot_count": 2,
        "max_enabled_plugin_count": 1,
        "failure_target_slot": "stable-git-main",
        "local_failure_targets_verified_main_only": True,
        "obsolete_selector_present": False,
        "pre_3_0_fallback_allowed": False,
        "slots": {
            "stable-git-main": stable,
            "versioned-local-testing": local_testing,
        },
    }
    registry["receipt_sha256"] = (
        hashlib.sha256(module._json_bytes(registry)).hexdigest().upper()
    )
    _write(registry_path, module._json_bytes(registry).decode("utf-8"))
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
        / "3.0.0+codex.test"
    )
    marketplace_root = codex_home / "local-marketplaces" / stable_marketplace
    _write(
        installed_path / ".codex-plugin" / "plugin.json",
        json.dumps({"version": "3.0.0+codex.test"}),
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
                f'[plugins."{local_selector}"]',
                "enabled = false",
                f'[plugins."{local_selector}".mcp_servers."evidence-lane"]',
                "enabled = false",
            ]
        ),
    )
    plugin_list = {
        "installed": [
            {
                "pluginId": stable_selector,
                "version": "3.0.0+codex.test",
                "enabled": True,
            },
            {
                "pluginId": local_selector,
                "version": "3.0.0+codex.local",
                "enabled": False,
            },
        ]
    }
    result = module._advance_two_slot_stable_registry(
        authority=authority,
        plugin_selector=stable_selector,
        plugin_version="3.0.0+codex.test",
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
    updated_local = updated["slots"]["versioned-local-testing"]
    assert {
        key: value
        for key, value in updated_local.items()
        if key not in {"enabled", "native_mcp_enabled"}
    } == {
        key: value
        for key, value in local_testing.items()
        if key not in {"enabled", "native_mcp_enabled"}
    }
    assert updated_local["enabled"] is False
    assert updated["slots"]["stable-git-main"]["plugin_selector"] == stable_selector
    assert updated["live_registered_selectors"] == [
        stable_selector,
        local_selector,
    ]
    assert updated["exact_live_slot_count"] == 2
    assert updated["obsolete_selector_present"] is False
    assert result["stable_selector_reused"] is True
    assert result["new_stable_selector_created"] is False
    updated_core = {
        key: value for key, value in updated.items() if key != "receipt_sha256"
    }
    assert (
        updated["receipt_sha256"]
        == hashlib.sha256(module._json_bytes(updated_core)).hexdigest().upper()
    )


def test_explicit_host_stable_baseline_survives_two_pass_install(
    tmp_path: Path,
) -> None:
    module = _module()
    archive, receipt, _ = _fixture_archive(tmp_path)
    source = tmp_path / "source"
    prior_source = tmp_path / "prior-source"
    shutil.copytree(source, prior_source)
    for name in (
        "invoke_hook.ps1",
        "invoke_hook.py",
        "lifecycle_boundary.py",
        "post_tool_use.py",
        "pre_tool_use.py",
        "behavior_handoff.py",
        "event_isolation.py",
        "EvidenceLaneHookHost.exe",
        "optional_event_observer.py",
        "permission_request.py",
        "subagent_start.py",
        "subagent_stop.py",
        "subhook_emit.py",
        "subhook_pipeline.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_validate.py",
    ):
        (prior_source / "hooks" / name).unlink()
    shutil.rmtree(prior_source / "toolchains")
    prior_channel_path = prior_source / "scripts" / "codex-release-channel.json"
    prior_channel = json.loads(prior_channel_path.read_text(encoding="utf-8"))
    prior_channel.pop("dependency_toolchains")
    prior_channel_path.write_text(json.dumps(prior_channel), encoding="utf-8")
    prior_hooks_path = prior_source / "hooks" / "hooks.json"
    prior_hooks = json.loads(prior_hooks_path.read_text(encoding="utf-8"))
    (prior_source / "hooks" / "logical-actions.json").unlink()
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
        allow_pre_logical_action_upgrade_baseline=True,
    )
    assert prior_surface["hooks"]["logical_action_registry_state"] == (
        "LEGACY_ABSENT_UPGRADE_BASELINE"
    )
    legacy_surface = json.loads(json.dumps(prior_surface))
    legacy_surface["hooks"] = {
        "count": prior_surface["hooks"]["hook_file_count"],
        "records": prior_surface["hooks"]["records"],
        "inventory_sha256": prior_surface["hooks"]["file_inventory_sha256"],
    }
    legacy_surface_core = dict(legacy_surface)
    legacy_surface_core.pop("surface_inventory_sha256")
    legacy_surface["surface_inventory_sha256"] = (
        hashlib.sha256(module._json_bytes(legacy_surface_core)).hexdigest().upper()
    )
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
    baseline["receipt_sha256"] = (
        hashlib.sha256(module._json_bytes(baseline)).hexdigest().upper()
    )
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
        data_root / "installations" / "codex-v200" / "INSTALL_HOST_STABLE.json"
    )
    _write(baseline_path, json.dumps(baseline))
    baseline_file_sha256 = (
        hashlib.sha256(baseline_path.read_bytes()).hexdigest().upper()
    )
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
        "EvidenceLaneHookHost.exe",
        "behavior_handoff.py",
        "event_isolation.py",
        "invoke_hook.ps1",
        "invoke_hook.py",
        "lifecycle_boundary.py",
        "logical-actions.json",
        "optional_event_observer.py",
        "permission_request.py",
        "post_tool_use.py",
        "pre_tool_use.py",
        "subagent_start.py",
        "subagent_stop.py",
        "subhook_emit.py",
        "subhook_pipeline.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_validate.py",
    ]
    assert preflight["comparison_baseline"]["surface_enrichment"] == (
        "VERIFIED_ARCHIVED_MARKETPLACE_EVENT_INVENTORY"
    )
    assert activation_pass["marketplace"]["state"] == "ALREADY_STAGED_EXACT"
    assert activation_pass["comparison_baseline"] == preflight["comparison_baseline"]
    assert (
        activation_pass["surface_change_display"] == preflight["surface_change_display"]
    )


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
            '@server.tool(name="tool_82", annotations=_WRITE)',
            "# removed tool 82",
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.InstallationError, match="91/30/61"):
        module._validate_plugin(source)


def test_installer_catalog_counts_specialized_and_decorated_actions() -> None:
    module = _module()
    catalog = module._catalog(PLUGIN)
    assert catalog["tools"] == 91
    assert catalog["read"] == 30
    assert catalog["write"] == 61
    assert catalog["skills"] == 26
    assert catalog["tool_names_unique"] is True


def test_installer_accepts_current_local_v300_package_contract(
    tmp_path: Path,
) -> None:
    module = _module()
    packaged = tmp_path / "packaged-plugin"
    shutil.copytree(
        PLUGIN,
        packaged,
        ignore=shutil.ignore_patterns(
            "_evidence_lane_rehearsal",
            "remote_adapter",
            "evidence",
            "release-channels.json",
        ),
    )
    proof_rows = {
        "source-manifest.json": {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.source-manifest"
            )
        },
        "skill-inventory.json": {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.skill-inventory"
            )
        },
        "package-surface-coherence.json": {
            "schema": "evidence-lane.package-surface-coherence.v1",
            "status": "PASS",
        },
        "systemwide-route-audit.json": {
            "schema": "evidence-lane.systemwide-route-audit.v1",
            "status": "PASS",
        },
        "exit-slip.json": {
            "schema": (
                "evidence-lane.non-lifecycle-local-package-rehearsal.v1.exit-slip"
            )
        },
    }
    for name, proof in proof_rows.items():
        _write(packaged / "manifests" / "package" / name, json.dumps(proof))
    identity = module._validate_plugin(packaged)

    assert identity["version"].startswith("3.0.0+codex.")
    assert identity["catalog"] == {
        "tools": 91,
        "read": 30,
        "write": 61,
        "skills": 26,
        "tool_names_unique": True,
        "static_catalog_sha256": identity["catalog"]["static_catalog_sha256"],
    }


def test_surface_inventory_accepts_pre_isolation_local_slot_for_upgrade(
    tmp_path: Path,
) -> None:
    module = _module()
    historical = tmp_path / "historical-local-slot"
    shutil.copytree(
        PLUGIN,
        historical,
        ignore=shutil.ignore_patterns(
            "remote_adapter",
            "evidence",
            "release-channels.json",
        ),
    )
    (historical / "hooks" / "behavior_handoff.py").unlink()
    (historical / "hooks" / "event_isolation.py").unlink()
    (historical / "hooks" / "EvidenceLaneHookHost.exe").unlink()
    (historical / "hooks" / "optional_event_observer.py").unlink()
    (historical / "hooks" / "permission_request.py").unlink()
    (historical / "hooks" / "subagent_start.py").unlink()
    (historical / "hooks" / "subagent_stop.py").unlink()
    (historical / "hooks" / "logical-actions.json").unlink()
    for name in (
        "subhook_emit.py",
        "subhook_pipeline.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_validate.py",
    ):
        (historical / "hooks" / name).unlink()

    inventory = module._surface_inventory(
        historical,
        version="3.0.0+codex.pre-isolation-slot",
        allow_pre_logical_action_upgrade_baseline=True,
    )

    assert {row["name"] for row in inventory["hooks"]["records"]} == {
        "hooks.json",
        "invoke_hook.ps1",
        "invoke_hook.py",
        "lifecycle_boundary.py",
        "post_tool_use.py",
        "pre_tool_use.py",
        "prompt_submit.py",
        "session_start.py",
        "stop_response.py",
    }


def test_local_testing_marketplace_has_distinct_stable_slot_identity() -> None:
    module = _module()
    manifest = json.loads(
        module._marketplace_bytes(module.LOCAL_TESTING_MARKETPLACE_NAME)
    )
    assert manifest["name"] == "evidence-lane-v300-testing-new"
    assert manifest["interface"]["displayName"] == "Local Testing Slot"


def test_restart_preparation_is_exact_process_and_same_task_only() -> None:
    text = RESTART.read_text(encoding="utf-8")

    assert '[ValidateSet("Prepare")]' in text
    assert "[int]$TargetProcessId" in text
    assert "Get-CimInstance Win32_Process" in text
    assert "Stop-Process" not in text
    assert "ScheduledTask" not in text
    assert "Start-Process" not in text
    assert "ActivateForProtocol" not in text
    assert 'schema = "evidence-lane.codex-task-binding.v1"' in text
    assert 'state = "EXACT_TASK_TERMINAL_SAFE_RESTART_PREPARED"' in text
    assert "Write-SealedJson -Path $taskBindingPath" in text
    assert "post_restart_native_proof_required = $true" in text
    assert "LocalTestCommitReceipt" not in text
    assert "TwoSlotRegistry" not in text
    assert "GoalRecovery" not in text
    assert "global_plugin_update_rehydration" not in text
    assert "native_workspace_binding_mutated = $false" in text
    assert "state_travel_replayed = $false" in text


def test_combined_same_slot_update_helper_is_purged() -> None:
    assert not PURGED_COMBINED_UPDATE.exists()


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


def test_restart_preparation_has_no_child_command_line_route() -> None:
    text = RESTART.read_text(encoding="utf-8")
    assert "ConvertTo-WindowsCommandLineArgument" not in text
    assert "Relaunch" not in text
    assert "Register-ScheduledTask" not in text


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
    marketplace = (
        tmp_path
        / "codex"
        / "local-marketplaces"
        / ("evidence-lane-github")
        / "plugins"
        / "evidence-lane-plugin"
    )
    installed = (
        tmp_path
        / "codex"
        / "plugins"
        / "cache"
        / ("evidence-lane-github")
        / "evidence-lane-plugin"
        / version
    )
    source = tmp_path / "source"
    assert not (source / "_evidence_lane_rehearsal").exists()
    _write(
        source / "pyproject.toml",
        '[project]\nname = "evidence-lane-plugin"\nversion = "3.0.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "constants.py",
        'ENGINE_VERSION = "3.0.0"\n',
    )
    _write(
        source / "src" / "evidence_lane_plugin" / "mcp_server.py",
        _fixture_catalog_source(),
    )
    _write(
        source / "hooks" / "hooks.json",
        json.dumps(
            {
                "description": "Synthetic host-native hook fixture.",
                "hooks": {
                    "SessionStart": [{"hooks": [{"type": "command"}]}],
                    "SubagentStart": [{"hooks": [{"type": "command"}]}],
                    "UserPromptSubmit": [{"hooks": [{"type": "command"}]}],
                    "PreToolUse": [{"hooks": [{"type": "command"}]}],
                    "PermissionRequest": [{"hooks": [{"type": "command"}]}],
                    "PostToolUse": [{"hooks": [{"type": "command"}]}],
                    "PreCompact": [{"hooks": [{"type": "command"}]}],
                    "PostCompact": [{"hooks": [{"type": "command"}]}],
                    "SubagentStop": [{"hooks": [{"type": "command"}]}],
                    "Stop": [{"hooks": [{"type": "command"}]}],
                    "SessionEnd": [{"hooks": [{"type": "command"}]}],
                },
            }
        ),
    )
    shutil.copy2(
        PLUGIN / "hooks" / "logical-actions.json",
        source / "hooks" / "logical-actions.json",
    )
    for name, marker in HOOK_NOTICE_MARKERS.items():
        _write(
            source / "hooks" / name,
            _fixture_hook_source(marker),
        )
    for number in range(26):
        _write(
            source / "skills" / f"skill-{number:02d}" / "SKILL.md",
            "---\n"
            f"name: skill-{number:02d}\n"
            f"description: Current governed fixture skill {number:02d}.\n"
            "---\n\n"
            f"# Skill {number:02d}\n",
        )
    marketplace.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, marketplace)
    installed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, installed)
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
        "status": "PENDING_NATIVE_POST_RESTART_VERIFICATION",
        "plugin_selector": selector,
        "hook_count": 11,
        "records": [],
        "all_enabled": False,
        "hooks_enabled_by_update": False,
        "native_post_restart_verification_required": True,
    }

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
        "engine_version": "3.0.0",
        "native_server_identity": "evidence-lane",
        "tool_count": 91,
        "tool_catalog_sha256": "A" * 64,
        "resource_uri": "ui://evidence-lane/governed-console-v6.html",
        "brand_icon_sha256": (
            "5F3ED419B62661F703F5DF763B4DC562645F621935AA99FC3DEF87B8A129C4FA"
        ),
        "catalog_expected": {"tools": 91, "read": 30, "write": 61, "skills": 26},
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
            "boundary": "GOVERNED_GIT_MAIN_CLEAN_CI_VERCEL_PREVIEW_EXACT_COMMIT",
            "source_commit": "1" * 40,
            "source_tree": "2" * 40,
            "branch": "main",
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
        "live_slot_contract": {
            "schema": "evidence-lane.codex-two-slot-main-local-registry.v1",
            "status": "PASS",
            "exact_live_slot_count": 2,
            "stable_slot": "stable-git-main",
            "stable_selector": selector,
            "local_slot": "versioned-local-testing",
            "local_testing_selector": (
                "evidence-lane-plugin@evidence-lane-v300-testing-new"
            ),
            "obsolete_selector_present": False,
            "pre_3_0_fallback_allowed": False,
        },
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
            "registered_events": installed_surface["hooks"]["registered_events"],
            "handler_count": installed_surface["hooks"]["handler_count"],
            "handler_count_semantics": installed_surface["hooks"][
                "handler_count_semantics"
            ],
            "event_order": installed_surface["hooks"]["event_order"],
            "event_action_inventory": installed_surface["hooks"][
                "event_action_inventory"
            ],
            "event_action_inventory_sha256": installed_surface["hooks"][
                "event_action_inventory_sha256"
            ],
            "logical_action_count": installed_surface["hooks"]["logical_action_count"],
            "logical_action_count_semantics": installed_surface["hooks"][
                "logical_action_count_semantics"
            ],
            "logical_action_inventory": installed_surface["hooks"][
                "logical_action_inventory"
            ],
            "logical_action_inventory_sha256": installed_surface["hooks"][
                "logical_action_inventory_sha256"
            ],
            "hook_file_count": installed_surface["hooks"]["hook_file_count"],
            "added": [row["name"] for row in installed_surface["hooks"]["records"]],
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
            "added": [row["name"] for row in installed_surface["skills"]["records"]],
            "changed": [],
            "removed": [],
            "inventory_sha256": installed_surface["skills"]["inventory_sha256"],
        },
        "search_toolchain": {
            "status": installed_surface["search_toolchain"]["status"],
            "record_count": installed_surface["search_toolchain"]["record_count"],
            "manifest_sha256": installed_surface["search_toolchain"]["manifest_sha256"],
            "inventory_sha256": installed_surface["search_toolchain"][
                "inventory_sha256"
            ],
            "fts_authority": installed_surface["search_toolchain"]["fts_authority"],
            "resolution_order": installed_surface["search_toolchain"][
                "resolution_order"
            ],
            "records": installed_surface["search_toolchain"]["records"],
            "fallbacks_required": True,
            "changed_from_previous": False,
            "raw_paths_included": False,
        },
        "catalog": {
            "tools": 91,
            "read": 30,
            "write": 61,
            "skills": len(list((installed / "skills").glob("*/SKILL.md"))),
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
            hook_control_receipt=None,
            output=tmp_path / "pre-restart-acceptance.json",
        )
    )
    assert pre["state"] == ("PRE_RESTART_INSTALLED_PACKAGE_VERIFIED_RESTART_REQUIRED")
    assert pre["catalog"] == {"tools": 91, "read": 30, "write": 61, "skills": 26}
    assert pre["installed_plugin"]["version"] == version
    assert pre["package_inventory"]["source_bytes_match_marketplace"] is True
    assert "codex_generated_migration_count" not in pre["package_inventory"]
    assert "codex_generated_migrations" not in pre["package_inventory"]
    assert pre["restart_verified"] is False
    assert pre["enabled_selector"] == selector
    assert pre["hook_trust"]["status"] == "PENDING_NATIVE_CONTROL"
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
                "tool_count": 91,
                "tool_names_unique": True,
                "project_route_argument_required": True,
                "cross_project_fallback_allowed": False,
                "tool_catalog_sha256": "A" * 64,
            }
        ),
    )
    hook_events = [
        "permissionRequest",
        "postCompact",
        "postToolUse",
        "preCompact",
        "preToolUse",
        "sessionEnd",
        "sessionStart",
        "stop",
        "subagentStart",
        "subagentStop",
        "userPromptSubmit",
    ]
    hook_control_body = {
        "schema": "evidence-lane.native-hook-event-control.v1",
        "status": "PASS",
        "plugin_selector": selector,
        "after_states": {event: True for event in hook_events},
        "verified_event_receipts": {event: "A" * 64 for event in hook_events},
        "direct_config_file_write": False,
        "windows_ui_control_used": False,
        "unrelated_plugin_state_mutated": False,
    }
    hook_control_body["receipt_sha256"] = acceptance._sha256_bytes(
        acceptance._json_bytes(hook_control_body)
    )
    hook_control = tmp_path / "hook-control.json"
    _write(
        hook_control,
        acceptance._json_bytes(hook_control_body).decode("utf-8"),
    )
    post = acceptance.accept(
        argparse.Namespace(
            **common,
            native_route_receipt=native_route,
            hook_control_receipt=hook_control,
            output=tmp_path / "post-restart-acceptance.json",
        )
    )
    assert post["state"] == ("POST_RESTART_INSTALLED_PACKAGE_VERIFIED_READY_FOR_HIL")
    assert post["restart_verified"] is True
    assert post["native_route"]["canonical_tool_namespace"] == ("mcp__evidence_lane__")
    assert post["installed_host_hil_required"] is True
    assert post["candidate_created_or_accepted"] is False
    assert post["pointer_moved"] is False

    assert not (installed / "commands").exists()
    assert not (installed / ".codex-plugin" / "migrated-command-skills").exists()
    installed_skill = next((installed / "skills").glob("*/SKILL.md"))
    installed_skill.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(acceptance.AcceptanceError):
        acceptance.accept(
            argparse.Namespace(
                **common,
                native_route_receipt=None,
                hook_control_receipt=None,
                output=tmp_path / "tampered-acceptance.json",
            )
        )
