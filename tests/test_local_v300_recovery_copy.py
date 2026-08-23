from __future__ import annotations

import importlib.util
import inspect
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


def test_recovery_marketplace_is_obsolete_not_a_live_slot() -> None:
    module = _module()
    assert module.LOCAL_RECOVERY_SELECTOR in module.OBSOLETE_LIVE_SELECTORS
    assert module.LOCAL_RECOVERY_SELECTOR not in module.TWO_SLOT_SELECTORS.values()
    assert set(module.TWO_SLOT_SELECTORS) == {
        "stable-git-main",
        "versioned-local-testing",
    }


def test_two_slot_registry_binds_main_and_local_without_recovery(
    tmp_path: Path,
) -> None:
    module = _module()
    roots: dict[str, Path] = {}
    rows: list[dict[str, object]] = []
    versions = {
        "stable-git-main": "3.0.0+codex.20260821000000.main",
        "versioned-local-testing": "3.0.0+codex.20260821010000.local",
    }
    for role, selector in module.TWO_SLOT_SELECTORS.items():
        root = tmp_path / role
        root.mkdir()
        roots[role] = root
        rows.append(
            {
                "pluginId": selector,
                "marketplaceName": selector.split("@", 1)[1],
                "version": versions[role],
                "enabled": role == "versioned-local-testing",
                "source": {"path": str(root)},
                "marketplaceSource": {
                    "sourceType": "git" if role == "stable-git-main" else "local"
                },
            }
        )

    receipt = module._materialize_two_slot_registry(
        plugin_list={"installed": rows},
        codex_home=tmp_path / "codex",
        data_root=tmp_path / "pv",
        active_slot="versioned-local-testing",
        config_sha256="A" * 64,
    )

    assert receipt["schema"] == module.TWO_SLOT_REGISTRY_SCHEMA
    assert receipt["exact_live_slot_count"] == 2
    assert receipt["active_slot"] == "versioned-local-testing"
    assert receipt["failure_target_slot"] == "stable-git-main"
    assert receipt["branch_recovery_install_allowed"] is False
    assert receipt["slots"]["stable-git-main"]["byte_frozen"] is True
    assert receipt["slots"]["versioned-local-testing"]["byte_frozen"] is False
    assert Path(receipt["registry_path"]).is_file()
    assert module._sha256(Path(receipt["registry_path"])) == receipt[
        "registry_file_sha256"
    ]


def test_recovery_copy_cli_is_retired_during_mutable_local_builds() -> None:
    module = _module()
    source = inspect.getsource(module.main)
    assert "Branch/local recovery slot creation is retired" in source
    assert "_materialize_local_recovery_copy(args)" not in source


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


def test_branch_recovery_is_purge_only_during_local_install() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "_purge_retired_branch_recovery" in installer
    assert '"plugin", "remove", LOCAL_RECOVERY_SELECTOR' in installer
    assert "generated_cache_deleted_directly" in installer


def test_recovery_materialization_never_initializes_the_shared_kill_switch() -> None:
    module = _module()
    source = inspect.getsource(module.main)
    assert "_materialize_local_recovery_copy(args)" not in source
    with pytest.raises(module.InstallationError, match="three-slot/branch-recovery"):
        module._materialize_three_slot_registry()


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
