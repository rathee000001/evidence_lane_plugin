from __future__ import annotations

import importlib.util
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
INSTALLER = PLUGIN / "scripts" / "codex_release" / "install_codex_stable.py"
GOAL_RECOVERY = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Manage-EvidenceLaneCodexGoalRecovery.ps1"
)
STABLE_UPDATE = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Update-EvidenceLaneCodexStableAndResume.ps1"
)
SLOT_SWITCH = (
    PLUGIN
    / "scripts"
    / "codex_release"
    / "Switch-EvidenceLaneCodexSlot.ps1"
)
RELEASE_POLICY = PLUGIN / "scripts" / "codex-release-channel.json"


def _installer_module():
    spec = importlib.util.spec_from_file_location("row201_installer", INSTALLER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _authority_fixture(selector: str) -> tuple[dict[str, object], dict[str, object]]:
    registry: dict[str, object] = {
        "accepted_generation": 12,
        "accepted_manifest_sha256": "A" * 64,
        "accepted_package_sha256": "B" * 64,
        "accepted_plugin_version": "2.1.0+codex.fixture",
        "accepted_pv": "PV12",
        "accepted_universal_pv_package_sha256": "C" * 64,
    }
    fallback: dict[str, object] = {
        "accepted_generation": 12,
        "accepted_pv": "PV12",
        "byte_frozen": True,
        "cache_authority_manifest_sha256": "D" * 64,
        "enabled": False,
        "install_receipt_sha256": "E" * 64,
        "native_mcp_enabled": False,
        "package_sha256": "B" * 64,
        "plugin_manifest_sha256": "F" * 64,
        "plugin_selector": selector,
        "plugin_version": "2.1.0+codex.fixture",
        "slot_role": "fallback",
    }
    return registry, fallback


def test_fallback_authority_is_selector_independent_and_tamper_evident() -> None:
    module = _installer_module()
    legacy_registry, legacy = _authority_fixture(
        "evidence-lane-plugin@evidence-lane-pv11-fallback"
    )
    neutral_registry, neutral = _authority_fixture(
        "evidence-lane-plugin@evidence-lane-fallback"
    )

    legacy_authority = module._fallback_release_authority(
        registry=legacy_registry,
        fallback=legacy,
    )
    neutral_authority = module._fallback_release_authority(
        registry=neutral_registry,
        fallback=neutral,
    )

    assert legacy_authority["fallback_authority_sha256"] == neutral_authority[
        "fallback_authority_sha256"
    ]
    assert legacy_authority["authority_id"] == neutral_authority["authority_id"]
    assert legacy_authority["selector_class"] == "LEGACY_GENERATION_ALIAS_LOCATOR"
    assert neutral_authority["selector_class"] == "GENERATION_NEUTRAL_LOCATOR"
    assert legacy_authority["selector_is_authority"] is False
    assert legacy_authority["manifest_evidence_complete"] is True

    changed_manifest = deepcopy(legacy_registry)
    changed_manifest["accepted_manifest_sha256"] = "0" * 64
    changed_authority = module._fallback_release_authority(
        registry=changed_manifest,
        fallback=legacy,
    )
    assert changed_authority["fallback_authority_sha256"] != legacy_authority[
        "fallback_authority_sha256"
    ]

    mismatched_package = deepcopy(legacy)
    mismatched_package["package_sha256"] = "1" * 64
    with pytest.raises(module.InstallationError, match="PV/package boundary"):
        module._fallback_release_authority(
            registry=legacy_registry,
            fallback=mismatched_package,
        )


def test_release_and_recovery_routes_enforce_current_three_slot_authority() -> None:
    policy = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))
    assert "fallback" not in policy
    assert policy["stable"]["slot_role"] == "main-git-release"
    assert policy["stable"]["codex_marketplace_slot"] == "evidence-lane-github"
    assert policy["branch_recovery"]["slot_role"] == "branch-commit-recovery"
    assert (
        policy["branch_recovery"]["codex_marketplace_slot"]
        == "evidence-lane-v300-stable-recovery"
    )
    assert policy["local_testing"]["slot_role"] == "mutable-local-testing"
    assert (
        policy["local_testing"]["codex_marketplace_slot"]
        == "evidence-lane-v300-testing-new"
    )
    assert policy["live_slot_policy"]["exact_slot_count"] == 3
    assert policy["live_slot_policy"]["max_enabled_plugin_count"] == 1
    assert "evidence-lane-pv11-fallback" in policy["live_slot_policy"][
        "forbidden_obsolete_marketplaces"
    ]

    recovery = GOAL_RECOVERY.read_text(encoding="utf-8")
    update = STABLE_UPDATE.read_text(encoding="utf-8")
    switch = SLOT_SWITCH.read_text(encoding="utf-8")
    assert "function Read-ThreeSlotAuthority" in recovery
    assert "mutable_local_failure_never_targets_main_git = $true" in recovery
    assert "pre_3_0_automatic_recovery_allowed = $false" in recovery
    assert '$script:LocalTestingSelector = "evidence-lane-plugin@$($script:LocalTestingMarketplaceName)"' in update
    assert '$script:LocalRecoverySelector = "evidence-lane-plugin@evidence-lane-v300-stable-recovery"' in update
    assert (
        'Where-Object { $_.pluginId -eq '
        '"evidence-lane-plugin@evidence-lane-pv11-fallback" }'
    ) not in update
    assert '"main-git-release" = "evidence-lane-plugin@evidence-lane-github"' in switch
    assert '"branch-commit-recovery" = "evidence-lane-plugin@evidence-lane-v300-stable-recovery"' in switch
    assert '"mutable-local-testing" = "evidence-lane-plugin@evidence-lane-v300-testing-new"' in switch
    assert "pre_3_0_fallback_allowed = $false" in switch


@pytest.mark.parametrize("script", [GOAL_RECOVERY, STABLE_UPDATE, SLOT_SWITCH])
def test_fallback_authority_powershell_routes_parse(script: Path) -> None:
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
