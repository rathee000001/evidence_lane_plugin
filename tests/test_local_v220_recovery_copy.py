from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
INSTALLER = PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py"
RESTART = PLUGIN / "scripts" / "codex_release" / "Restart-EvidenceLaneCodex.ps1"
RECOVERY = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)


def _module():
    spec = importlib.util.spec_from_file_location("local_v220_recovery", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recovery_marketplace_has_a_distinct_fixed_identity() -> None:
    module = _module()
    payload = json.loads(
        module._marketplace_bytes(module.LOCAL_RECOVERY_MARKETPLACE_NAME)
    )
    assert payload["name"] == "evidence-lane-v220-stable-recovery"
    assert payload["interface"]["displayName"] == "Branch Commit Git Recovery"
    assert module.LOCAL_RECOVERY_SELECTOR == (
        "evidence-lane-plugin@evidence-lane-v220-stable-recovery"
    )


def test_three_slot_registry_binds_branch_recovery_without_copying_local_bytes(
    tmp_path: Path,
) -> None:
    module = _module()
    roots: dict[str, Path] = {}
    rows: list[dict[str, object]] = []
    versions = {
        "main-git-release": "2.1.0+codex.20260812193232",
        "branch-commit-recovery": "2.2.0+codex.20260814082900",
        "mutable-local-testing": "2.2.0+codex.20260815220729.local.row224.r26",
    }
    for role, selector in module.THREE_SLOT_SELECTORS.items():
        root = tmp_path / role
        root.mkdir()
        roots[role] = root
        rows.append(
            {
                "pluginId": selector,
                "marketplaceName": selector.split("@", 1)[1],
                "version": versions[role],
                "enabled": role == "mutable-local-testing",
                "source": {"path": str(root)},
                "marketplaceSource": {
                    "sourceType": "git" if role == "main-git-release" else "local"
                },
            }
        )

    receipt = module._materialize_three_slot_registry(
        plugin_list={"installed": rows},
        codex_home=tmp_path / "codex",
        data_root=tmp_path / "pv",
        active_slot="mutable-local-testing",
        config_sha256="A" * 64,
    )

    assert receipt["schema"] == module.THREE_SLOT_REGISTRY_SCHEMA
    assert receipt["exact_live_slot_count"] == 3
    assert receipt["active_slot"] == "mutable-local-testing"
    assert receipt["failure_target_slot"] == "branch-commit-recovery"
    assert receipt["mutable_local_failure_never_targets_main_git"] is True
    assert receipt["pre_2_2_fallback_allowed"] is False
    assert receipt["slots"]["branch-commit-recovery"]["byte_frozen"] is True
    assert receipt["slots"]["mutable-local-testing"]["byte_frozen"] is False
    assert Path(receipt["registry_path"]).is_file()
    assert module._sha256(Path(receipt["registry_path"])) == receipt[
        "registry_file_sha256"
    ]


def test_recovery_copy_cli_is_retired_during_mutable_local_builds() -> None:
    module = _module()
    source = inspect.getsource(module.main)
    assert "Per-build local recovery copying is retired" in source
    assert "branch-commit recovery" in source
    assert "prior governed Git checkpoint" in source


def test_source_inventory_excludes_path_bound_python_bytecode(tmp_path: Path) -> None:
    module = _module()
    primary = tmp_path / "primary"
    recovery = tmp_path / "recovery"
    for root, marker in ((primary, b"primary-root"), (recovery, b"recovery-root")):
        (root / "src").mkdir(parents=True)
        (root / "src" / "module.py").write_bytes(b"VALUE = 1\n")
        cache = root / "src" / "__pycache__"
        cache.mkdir()
        (cache / "module.cpython-314.pyc").write_bytes(marker)

    primary_inventory = module._source_inventory(primary)
    recovery_inventory = module._source_inventory(recovery)
    assert primary_inventory["file_count"] == 1
    assert recovery_inventory["file_count"] == 1
    assert primary_inventory["manifest_sha256"] == recovery_inventory["manifest_sha256"]
    assert primary_inventory["ignored_python_runtime_artifact_count"] == 1
    assert recovery_inventory["ignored_python_runtime_artifact_count"] == 1
    assert primary_inventory["python_runtime_artifacts_are_source_authority"] is False


def test_branch_recovery_remains_prior_checkpoint_during_local_install() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    restart = RESTART.read_text(encoding="utf-8")

    assert "Per-build local recovery copying is retired" in installer
    assert '"branch_recovery_mutation_allowed_during_local_build"' in installer
    assert "$restartAuthority.three_slot_registry = $exactThreeSlotRegistry" in restart
    assert "$restartAuthority.branch_commit_recovery_selector = $branchRecoverySelector" in restart
    assert "$restartAuthority.mutable_local_failure_target = $branchRecoverySelector" in restart
    assert "$restartAuthority.mutable_local_failure_never_targets_main_git = $true" in restart
    assert "$restartAuthority.branch_commit_recovery_remains_prior_checkpoint = $true" in restart
    assert "$restartAuthority.branch_commit_recovery_byte_identical_before_checkpoint = $false" in restart
    assert "$restartAuthority.pre_2_2_recovery_allowed = $false" in restart


def test_recovery_copy_reuses_primary_kill_switch_without_rotating_it(
    tmp_path: Path,
) -> None:
    module = _module()
    recovery_plugin = tmp_path / "recovery-plugin"
    hooks = recovery_plugin / "hooks"
    hooks.mkdir(parents=True)
    shutil.copy2(PLUGIN / "hooks" / "event_isolation.py", hooks)
    shutil.copy2(PLUGIN / "hooks" / "event_isolation_policy.json", hooks)

    primary = module._initialize_installed_hook_event_isolation(
        recovery_plugin,
        data_root=tmp_path,
        installation_id="primary-local-installation",
    )
    kill_switch = Path(primary["kill_switch_receipt_path"])
    before_bytes = kill_switch.read_bytes()
    before_sha256 = hashlib.sha256(before_bytes).hexdigest().upper()
    before_payload = json.loads(before_bytes)["payload"]

    verified = module._verify_recovery_copy_hook_event_isolation(
        recovery_plugin,
        data_root=tmp_path,
        primary_hook_isolation=primary,
    )

    after_bytes = kill_switch.read_bytes()
    after_payload = json.loads(after_bytes)["payload"]
    assert after_bytes == before_bytes
    assert hashlib.sha256(after_bytes).hexdigest().upper() == before_sha256
    assert after_payload["generation"] == before_payload["generation"]
    assert after_payload["installation_id"] == "primary-local-installation"
    assert verified["state"] == (
        "PRIMARY_INACTIVE_KILL_SWITCH_REUSED_UNCHANGED"
    )
    assert verified["kill_switch_receipt_sha256"] == before_sha256
    assert verified["global_kill_switch_rotated"] is False
    assert verified["recovery_copy_disabled"] is True
    assert verified["verified_by_recovery_bytes"] is True
    assert verified["bytes_unchanged"] is True


def test_recovery_materialization_never_initializes_the_shared_kill_switch() -> None:
    module = _module()
    source = inspect.getsource(module._materialize_local_recovery_copy)

    assert "_verify_recovery_copy_hook_event_isolation(" in source
    assert "_initialize_installed_hook_event_isolation(" not in source
    assert "primary_hook_isolation=primary_hook_isolation" in source


@pytest.mark.parametrize("script", [RESTART, RECOVERY])
def test_local_recovery_powershell_routes_parse(script: Path) -> None:
    command = (
        "$errors=$null; "
        f"[Management.Automation.Language.Parser]::ParseFile('{script}',"
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
