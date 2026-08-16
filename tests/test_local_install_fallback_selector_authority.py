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


def test_release_and_recovery_routes_treat_selector_as_locator_only() -> None:
    policy = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))["fallback"]
    assert policy["authority_identity"] == "fallback"
    assert policy["selector_role"] == "OPERATIONAL_LOCATOR_ONLY"
    assert policy["selector_is_authority"] is False
    assert policy["generation_neutral_selector"] == "evidence-lane-fallback"
    assert policy["legacy_generation_alias_active"] is True
    assert "accepted_manifest_sha256" in policy["authorization_fields"]
    assert "accepted_package_sha256" in policy["authorization_fields"]

    recovery = GOAL_RECOVERY.read_text(encoding="utf-8")
    update = STABLE_UPDATE.read_text(encoding="utf-8")
    switch = SLOT_SWITCH.read_text(encoding="utf-8")
    assert "function Get-FallbackReleaseAuthority" in recovery
    assert "fallback_selector_used_for_authorization = $false" in recovery
    assert "fallback_authority_sha256" in recovery
    assert (
        '$fallback = @($evidencePlugins | Where-Object { $_.pluginId -ceq '
        "$fallbackSelector })"
    ) in update
    assert (
        'Where-Object { $_.pluginId -eq '
        '"evidence-lane-plugin@evidence-lane-pv11-fallback" }'
    ) not in update
    assert "function Get-FallbackReleaseAuthority" in switch
    assert 'fallback_accepted_pv = [string]$registryBody.accepted_pv' in switch
    assert 'fallback_accepted_pv = "PV11"' not in switch


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
