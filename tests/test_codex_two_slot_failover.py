from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPERATOR = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "Switch-EvidenceLaneCodexSlot.ps1"
)
MAIN_SLOT = "main-git-release"
LOCAL_SLOT = "versioned-local-testing"
MAIN_SELECTOR = "evidence-lane-plugin@evidence-lane-github"
LOCAL_SELECTOR = "evidence-lane-plugin@evidence-lane-v300-testing-new"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for two-slot operator tests.")
    return executable


def _write_config(path: Path, active_slot: str) -> None:
    selectors = {MAIN_SLOT: MAIN_SELECTOR, LOCAL_SLOT: LOCAL_SELECTOR}
    lines: list[str] = []
    for slot, selector in selectors.items():
        enabled = str(slot == active_slot).lower()
        lines.extend(
            [
                f'[plugins."{selector}"]',
                f"enabled = {enabled}",
                "",
                f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                f"enabled = {enabled}",
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="")


def _fixture(
    tmp_path: Path,
    *,
    active_slot: str = LOCAL_SLOT,
    include_obsolete_inventory: bool = False,
    fail_start: bool = False,
) -> dict[str, Path | dict[str, str]]:
    user_profile = tmp_path / "user"
    config = tmp_path / "codex" / "config.toml"
    _write_config(config, active_slot)
    versions = {
        MAIN_SLOT: "3.0.0+codex.verified-main",
        LOCAL_SLOT: "3.0.0+codex.local-testing",
    }
    inventory = [
        {
            "name": "evidence-lane-plugin",
            "pluginId": MAIN_SELECTOR,
            "version": versions[MAIN_SLOT],
        },
        {
            "name": "evidence-lane-plugin",
            "pluginId": LOCAL_SELECTOR,
            "version": versions[LOCAL_SLOT],
        },
    ]
    if include_obsolete_inventory:
        inventory.append(
            {
                "name": "evidence-lane-plugin",
                "pluginId": "evidence-lane-plugin@evidence-lane-pv11-fallback",
                "version": "2.1.0+codex.retired",
            }
        )
    codex = tmp_path / "codex-fixture.cmd"
    payload = json.dumps({"installed": inventory}, separators=(",", ":"))
    codex.write_text(f"@echo off\r\necho {payload}\r\n", encoding="utf-8")

    registry = tmp_path / "CODEX_TWO_SLOT_MAIN_LOCAL_REGISTRY.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "evidence-lane.codex-two-slot-main-local-registry.v1",
                "status": "PASS",
                "active_slot": active_slot,
                "exact_live_slot_count": 2,
                "failure_target_slot": "stable-git-main",
                "versioned_local_failure_targets_verified_main_only": True,
                "pre_3_0_fallback_allowed": False,
                "slots": {
                    MAIN_SLOT: {
                        "slot_role": MAIN_SLOT,
                        "plugin_selector": MAIN_SELECTOR,
                        "plugin_version": versions[MAIN_SLOT],
                    },
                    LOCAL_SLOT: {
                        "slot_role": LOCAL_SLOT,
                        "plugin_selector": LOCAL_SELECTOR,
                        "plugin_version": versions[LOCAL_SLOT],
                    },
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    tunnel_root = (
        user_profile
        / ".codex"
        / "plugins"
        / "runtime"
        / "evidence-lane-plugin"
        / "tunnel-runtime-v300-stable-build"
    )
    tunnel_root.mkdir(parents=True)
    tunnel_log = tunnel_root / "actions.log"
    fail = (
        "if ($Action -ceq 'Start') { Write-Error 'fixture start failed'; exit 1 }\n"
        if fail_start
        else ""
    )
    (tunnel_root / "Manage-EvidenceLaneTunnel.ps1").write_text(
        "[CmdletBinding()]\n"
        "param([string]$Action,[string]$RuntimeRoot,[string]$ProfileName,"
        "[string]$ReleaseToken,[string]$TaskName)\n"
        f"Add-Content -LiteralPath '{tunnel_log}' -Value $Action\n"
        + fail
        + "[ordered]@{status='PASS';action=$Action} | ConvertTo-Json\n",
        encoding="utf-8",
    )
    return {
        "user_profile": user_profile,
        "config": config,
        "codex": codex,
        "registry": registry,
        "receipt_dir": tmp_path / "receipts",
        "tunnel_log": tunnel_log,
        "versions": versions,
    }


def _command(
    fixture: dict[str, Path | dict[str, str]],
    *,
    action: str,
    target: str,
    reason: str = "EXPLICIT_OPERATOR_SELECTION",
) -> list[str]:
    return [
        _powershell(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(OPERATOR),
        "-Action",
        action,
        "-TargetSlot",
        target,
        "-Reason",
        reason,
        "-Registry",
        str(fixture["registry"]),
        "-RegistrySha256",
        _sha(Path(fixture["registry"])),
        "-CodexConfig",
        str(fixture["config"]),
        "-CodexExecutable",
        str(fixture["codex"]),
        "-ReceiptDirectory",
        str(fixture["receipt_dir"]),
    ]


def _run(
    command: list[str], fixture: dict[str, Path | dict[str, str]]
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["USERPROFILE"] = str(fixture["user_profile"])
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )


def test_operator_contains_only_main_and_local_live_roles() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    assert (
        '"main-git-release" = '
        '"evidence-lane-plugin@evidence-lane-github"'
    ) in text
    assert (
        '"versioned-local-testing" = '
        '"evidence-lane-plugin@evidence-lane-v300-testing-new"'
    ) in text
    assert '"branch-commit-recovery" =' not in text
    assert '"fallback" =' not in text
    assert "evidence-lane.codex-two-slot-main-local-registry.v1" in text


@pytest.mark.parametrize("active_slot", [MAIN_SLOT, LOCAL_SLOT])
def test_verify_proves_exact_requested_main_or_local_slot(
    tmp_path: Path, active_slot: str
) -> None:
    fixture = _fixture(tmp_path, active_slot=active_slot)
    completed = _run(
        _command(fixture, action="Verify", target=active_slot), fixture
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["active_slot"] == active_slot
    assert payload["installed_slot_count"] == 2
    assert payload["obsolete_selector_active"] is False
    assert payload["pre_3_0_fallback_allowed"] is False


def test_obsolete_third_inventory_selector_fails_closed(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, include_obsolete_inventory=True)
    completed = _run(
        _command(fixture, action="Verify", target=LOCAL_SLOT), fixture
    )
    assert completed.returncode != 0
    assert "exact two-slot Evidence Lane boundary" in completed.stderr


def test_versioned_local_failure_can_prepare_only_verified_main(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, active_slot=LOCAL_SLOT)
    rejected = _run(
        _command(
            fixture,
            action="Prepare",
            target=LOCAL_SLOT,
            reason="VERSIONED_LOCAL_RUNTIME_FAILURE",
        ),
        fixture,
    )
    assert rejected.returncode != 0
    assert "may switch only to verified stable Git main" in rejected.stderr

    command = _command(
        fixture,
        action="Prepare",
        target=MAIN_SLOT,
        reason="VERSIONED_LOCAL_RUNTIME_FAILURE",
    )
    command.extend(
        [
            "-ConsecutiveFailures",
            "3",
            "-SampleWindowSeconds",
            "30",
            "-DistinctProbeTypes",
            "2",
        ]
    )
    prepared = _run(command, fixture)
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    payload = json.loads(prepared.stdout)
    assert payload["state"] == "PREPARED_NOT_SWITCHED"
    receipt = json.loads(Path(payload["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["source_slot"] == LOCAL_SLOT
    assert receipt["target_slot"] == MAIN_SLOT
    assert receipt["switched"] is False


def test_prepare_then_switch_changes_only_activation_and_requests_restart(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, active_slot=LOCAL_SLOT)
    prepared = _run(
        _command(fixture, action="Prepare", target=MAIN_SLOT), fixture
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)
    command = _command(fixture, action="Switch", target=MAIN_SLOT)
    command.extend(
        [
            "-PreparationReceipt",
            preparation["receipt_path"],
            "-PreparationReceiptSha256",
            preparation["receipt_sha256"],
            "-ConfirmSwitch",
        ]
    )
    switched = _run(command, fixture)
    assert switched.returncode == 0, switched.stdout + switched.stderr
    payload = json.loads(switched.stdout)
    assert payload["state"] == "TARGET_SELECTED_RESTART_REQUIRED"
    config = Path(fixture["config"]).read_text(encoding="utf-8")
    assert f'[plugins."{MAIN_SELECTOR}"]\nenabled = true' in config
    assert f'[plugins."{LOCAL_SELECTOR}"]\nenabled = false' in config
    assert Path(fixture["tunnel_log"]).read_text(encoding="utf-8").splitlines() == [
        "Stop",
        "Start",
    ]


def test_failed_target_start_restores_source_config(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, active_slot=LOCAL_SLOT, fail_start=True)
    before = Path(fixture["config"]).read_bytes()
    prepared = _run(
        _command(fixture, action="Prepare", target=MAIN_SLOT), fixture
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)
    command = _command(fixture, action="Switch", target=MAIN_SLOT)
    command.extend(
        [
            "-PreparationReceipt",
            preparation["receipt_path"],
            "-PreparationReceiptSha256",
            preparation["receipt_sha256"],
            "-ConfirmSwitch",
        ]
    )
    failed = _run(command, fixture)
    assert failed.returncode != 0
    assert Path(fixture["config"]).read_bytes() == before
    assert "Rolled back to versioned-local-testing" in failed.stderr
