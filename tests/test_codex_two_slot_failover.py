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
TASK_ID = "019ff25a-30f6-7382-993d-12c5979d696d"
PROJECT_ID = "test-codex-evidence-lane-plugin"
SESSION_ID = "session_01kz48pm60mt58v5fyzqq6yq1g"
HOST_SESSION_ID = "codex-evidence-lane-plugin-statetravel-task-2-20260812"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _powershell() -> str:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if executable is None:
        pytest.skip("PowerShell is required for two-slot operator tests.")
    return executable


def _fixture(
    tmp_path: Path,
    *,
    active_slot: str = "stable-build",
    fail_fallback_start: bool = False,
    tunnel_required: bool = True,
    include_disabled_history: bool = False,
) -> dict[str, Path]:
    codex_home = tmp_path / "codex"
    data_root = tmp_path / "EvidenceLanePV"
    selectors = {
        "stable-build": "evidence-lane-plugin@evidence-lane-v200-github",
        "fallback": "evidence-lane-plugin@evidence-lane-pv11-fallback",
    }
    config_lines: list[str] = ["[features]", "enabled = true", ""]
    for name in ("stable-build", "fallback"):
        enabled = "true" if name == active_slot else "false"
        selector = selectors[name]
        config_lines.extend(
            [
                f'[plugins."{selector}"]',
                f"enabled = {enabled}",
                "",
                f'[plugins."{selector}".mcp_servers."evidence-lane"]',
                f"enabled = {enabled}",
                "",
            ]
        )
    if include_disabled_history:
        historical = "evidence-lane-plugin@evidence-lane-v130-fallback"
        config_lines.extend(
            [
                f'[plugins."{historical}"]',
                "enabled = false",
                "",
                f'[plugins."{historical}".mcp_servers."evidence-lane"]',
                "enabled = false",
                "",
            ]
        )
    config = codex_home / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("\n".join(config_lines), encoding="utf-8")

    slots: dict[str, dict] = {}
    accepted_package_sha = "B" * 64
    for name in ("stable-build", "fallback"):
        version = (
            "2.1.0+codex.stable-build"
            if name == "stable-build"
            else "2.0.0+codex.accepted-pv11"
        )
        package_sha = "A" * 64 if name == "stable-build" else accepted_package_sha
        install = data_root / "installations" / f"{name}.json"
        _write_json(
            install,
            {
                "schema": "evidence-lane.codex-stable-installation.v2",
                "status": "PASS",
                "plugin": {"version": version},
                "archive_sha256": package_sha,
                "activation": {"state": "INSTALLED_RESTART_REQUIRED"},
                "candidate_created_or_accepted": False,
                "pointer_moved": False,
                "hil_inferred": False,
            },
        )
        cache = codex_home / "plugins" / "cache" / name / "evidence-lane-plugin" / version
        cache.mkdir(parents=True)
        runtime = data_root / "tunnels" / name
        marker = runtime / "evidence-lane-tunnel-installation.json"
        _write_json(
            marker,
            {
                "schema": (
                    "evidence-lane.versioned-secure-mcp-tunnel-installation.v1"
                ),
                "slot_role": name,
                "version": version,
                "byte_frozen": name == "fallback",
                "legacy_version_manager_authoritative": False,
            },
        )
        state = runtime / "ready.txt"
        state.write_text("true" if name == active_slot else "false", encoding="utf-8")
        manager = runtime / "Manage-EvidenceLaneTunnel.ps1"
        reject_start = name == "fallback" and fail_fallback_start
        reject_start_block = (
            "if ($Action -eq 'Start') { "
            "[ordered]@{status='BLOCKED';control_plane_poll_ready=$false;"
            "process_running=$false;task_registered=$true} | ConvertTo-Json; "
            "exit 1 }\n"
            if reject_start
            else ""
        )
        manager_text = (
            "[CmdletBinding()]\n"
            "param([string]$Action,[string]$RuntimeRoot,[string]$ProfileName,[string]$TaskName)\n"
            "$stateFile = Join-Path $RuntimeRoot 'ready.txt'\n"
            "if ($Action -eq 'Start') { Set-Content -LiteralPath $stateFile -Value 'true' -NoNewline }\n"
            "if ($Action -eq 'Stop') {\n"
            "  Set-Content -LiteralPath $stateFile -Value 'false' -NoNewline\n"
            "  [ordered]@{status='STOPPED_SAVED';control_plane_poll_ready=$false;process_running=$false;task_registered=$true} | ConvertTo-Json\n"
            "  exit 0\n"
            "}\n"
            "$isReady = (Get-Content -LiteralPath $stateFile -Raw).Trim() -eq 'true'\n"
            "[ordered]@{\n"
            "  status = if ($isReady) { 'PASS' } else { 'BLOCKED' }\n"
            "  control_plane_poll_ready = $isReady\n"
            "  process_running = $isReady\n"
            "  task_registered = $true\n"
            "} | ConvertTo-Json\n"
            "if ($isReady) { exit 0 } else { exit 1 }\n"
        )
        manager.write_text(
            manager_text.replace(
                "$stateFile = Join-Path $RuntimeRoot 'ready.txt'\n",
                "$stateFile = Join-Path $RuntimeRoot 'ready.txt'\n"
                + reject_start_block,
                1,
            ),
            encoding="utf-8",
        )
        slots[name] = {
            "slot_role": name,
            "plugin_selector": selectors[name],
            "plugin_version": version,
            "package_sha256": package_sha,
            "byte_frozen": name == "fallback",
            "accepted_pv": "PV11" if name == "fallback" else None,
            "accepted_generation": 11 if name == "fallback" else None,
            "install_receipt": str(install),
            "install_receipt_sha256": _sha(install),
            "cache_root": str(cache),
            "tunnel": {
                "runtime_root": str(runtime),
                "manager": str(manager),
                "marker": str(marker),
                "marker_sha256": _sha(marker),
                "task_name": f"EvidenceLane-{name}",
                "profile_name": f"evidence_lane_{name}",
            },
        }
    registry = data_root / "two-slot-registry.json"
    _write_json(
        registry,
        {
            "schema": "evidence-lane.codex-two-slot-registry.v1",
            "status": "PASS",
            "post_fuse_materialized": True,
            "accepted_pv": "PV11",
            "accepted_generation": 11,
            "accepted_package_sha256": accepted_package_sha,
            "accepted_plugin_version": slots["fallback"]["plugin_version"],
            "project_id": PROJECT_ID,
            "evidence_session_id": SESSION_ID,
            "task_id": TASK_ID,
            "host_session_id": HOST_SESSION_ID,
            "materialized_initial_slot": "stable-build",
            "exact_live_slot_count": 2,
            "max_enabled_plugin_count": 1,
            "max_active_tunnel_count": 1 if tunnel_required else 0,
            "tunnel_required": tunnel_required,
            "secret_material_present": False,
            "slots": slots,
        },
    )
    restart = data_root / "Fake-Restart-EvidenceLaneCodex.ps1"
    restart.write_text(
        "[CmdletBinding()]\n"
        "param([string]$Action,[string]$InstallReceipt,[string]$InstallReceiptSha256,"
        "[string]$ProjectId,[string]$EvidenceSessionId,[string]$TaskId,"
        "[string]$HostSessionId,[int]$TargetProcessId,[string]$PreparationReceipt,"
        "[string]$PreparationReceiptSha256,[string]$ReceiptDirectory,[switch]$ConfirmRestart)\n"
        "if ($Action -eq 'Prepare') {\n"
        "  New-Item -ItemType Directory -Path $ReceiptDirectory -Force | Out-Null\n"
        "  $receipt = Join-Path $ReceiptDirectory 'FAKE_RESTART_PREPARATION.json'\n"
        "  Set-Content -LiteralPath $receipt -Value '{\"status\":\"PASS\"}' -NoNewline\n"
        "  $sha = (Get-FileHash -LiteralPath $receipt -Algorithm SHA256).Hash\n"
        "  [ordered]@{status='PASS';receipt_path=$receipt;receipt_sha256=$sha} | ConvertTo-Json\n"
        "  exit 0\n"
        "}\n"
        "if ($Action -eq 'Restart' -and $ConfirmRestart) { exit 0 }\n"
        "exit 1\n",
        encoding="utf-8",
    )
    return {
        "codex_home": codex_home,
        "data_root": data_root,
        "config": config,
        "registry": registry,
        "restart": restart,
    }


def _base_command(fixture: dict[str, Path], *, action: str, target: str) -> list[str]:
    powershell = _powershell()
    return [
        powershell,
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
        "-Registry",
        str(fixture["registry"]),
        "-RegistrySha256",
        _sha(fixture["registry"]),
        "-ProjectId",
        PROJECT_ID,
        "-EvidenceSessionId",
        SESSION_ID,
        "-TaskId",
        TASK_ID,
        "-HostSessionId",
        HOST_SESSION_ID,
        "-CodexConfig",
        str(fixture["config"]),
        "-CodexHome",
        str(fixture["codex_home"]),
        "-DataRoot",
        str(fixture["data_root"]),
        "-RestartHelper",
        str(fixture["restart"]),
        "-ReceiptDirectory",
        str(fixture["data_root"] / "receipts"),
        "-PowerShellExecutable",
        powershell,
    ]


def test_operator_uses_the_sealed_registry_task_instead_of_a_source_constant() -> None:
    text = OPERATOR.read_text(encoding="utf-8")
    assert "ExpectedTaskId" not in text
    assert "019fedc7-cb86-7b40-94ce-1784a999f12b" not in text
    assert "$body.task_id -ne $TaskId" in text


def test_verify_proves_exactly_one_matching_plugin_and_tunnel(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    completed = subprocess.run(
        _base_command(fixture, action="Verify", target="fallback"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "PASS"
    assert payload["active_slot"] == "stable-build"
    assert payload["installed_slot_count"] == 2
    assert payload["enabled_plugin_count"] == 1
    assert payload["active_tunnel_count"] == 1
    assert payload["fallback_byte_frozen"] is True
    assert payload["fallback_accepted_pv"] == "PV11"
    assert payload["native_proof_required_after_restart"] is True
    assert payload["active_slot_source"] == "LIVE_CODEX_CONFIG_AND_TUNNEL_MATCH"


def test_verify_derives_fallback_from_live_config_and_tunnel_not_registry(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, active_slot="fallback")
    completed = subprocess.run(
        _base_command(fixture, action="Verify", target="stable-build"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["active_slot"] == "fallback"
    assert payload["enabled_plugin_count"] == 1
    assert payload["active_tunnel_count"] == 1


def test_local_durable_verify_and_switch_require_no_tunnel(tmp_path: Path) -> None:
    fixture = _fixture(
        tmp_path,
        tunnel_required=False,
        include_disabled_history=True,
    )
    shutil.rmtree(fixture["data_root"] / "tunnels")

    verified = subprocess.run(
        _base_command(fixture, action="Verify", target="fallback"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    verification = json.loads(verified.stdout)
    assert verification["active_slot"] == "stable-build"
    assert verification["active_slot_source"] == (
        "LIVE_CODEX_CONFIG_LOCAL_DURABLE_NO_TUNNEL"
    )
    assert verification["tunnel_required"] is False
    assert verification["active_tunnel_count"] == 0
    assert verification["disabled_historical_slot_count"] == 1

    prepare = _base_command(fixture, action="Prepare", target="fallback")
    prepare.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-ConfirmExplicitOperator",
        ]
    )
    prepared = subprocess.run(
        prepare,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)
    preparation_body = json.loads(
        Path(preparation["receipt_path"]).read_text(encoding="utf-8")
    )
    assert preparation_body["tunnel_required"] is False
    assert preparation_body["stop_source_before_start_target"] is False
    assert preparation_body["max_active_tunnels"] == 0

    switch = _base_command(fixture, action="Switch", target="fallback")
    switch.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-PreparationReceipt",
            preparation["receipt_path"],
            "-PreparationReceiptSha256",
            preparation["receipt_sha256"],
            "-ConfirmExplicitOperator",
            "-ConfirmSwitch",
        ]
    )
    switched = subprocess.run(
        switch,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert switched.returncode == 0, switched.stdout + switched.stderr
    transition = json.loads(
        (
            fixture["data_root"]
            / "receipts"
            / "CODEX_TWO_SLOT_SWITCH_TRANSITION.json"
        ).read_text(encoding="utf-8")
    )
    assert transition["tunnel_required"] is False
    assert transition["source_tunnel_stopped_first"] is None
    assert transition["target_tunnel_ready_before_plugin_switch"] is None
    assert transition["active_tunnel_count"] == 0

    post_switch = subprocess.run(
        _base_command(fixture, action="Verify", target="stable-build"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert post_switch.returncode == 0, post_switch.stdout + post_switch.stderr
    assert json.loads(post_switch.stdout)["active_slot"] == "fallback"
    config_after = fixture["config"].read_text(encoding="utf-8")
    assert (
        '[plugins."evidence-lane-plugin@evidence-lane-v130-fallback"]\n'
        "enabled = false"
    ) in config_after


def test_prepare_rejects_one_transient_failure_without_writes(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    decision = fixture["data_root"] / "transient-health.json"
    _write_json(
        decision,
        {
            "schema": "evidence-lane.stable-health-failure.v1",
            "status": "DETERMINISTIC_STABLE_FAILURE",
            "plugin_selector": "evidence-lane-plugin@evidence-lane-v200-github",
            "plugin_version": "2.1.0+codex.stable-build",
            "consecutive_failures": 1,
            "sample_window_seconds": 5,
            "distinct_probe_types": 1,
            "single_transient_error": True,
            "secret_material_present": False,
        },
    )
    before = fixture["config"].read_bytes()
    command = _base_command(fixture, action="Prepare", target="fallback")
    command.extend(
        [
            "-Reason",
            "DETERMINISTIC_STABLE_HEALTH_FAILURE",
            "-DecisionReceipt",
            str(decision),
            "-DecisionReceiptSha256",
            _sha(decision),
            "-TargetProcessId",
            str(os.getpid()),
        ]
    )
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode != 0
    assert "transient, stale, or incomplete" in completed.stderr
    assert fixture["config"].read_bytes() == before
    assert not (fixture["data_root"] / "receipts").exists()


def test_explicit_operator_can_prepare_but_does_not_switch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    command = _base_command(fixture, action="Prepare", target="fallback")
    command.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-ConfirmExplicitOperator",
        ]
    )
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["state"] == "PREPARED_NOT_SWITCHED"
    receipt = Path(payload["receipt_path"])
    body = json.loads(receipt.read_text(encoding="utf-8"))
    assert body["source_slot"] == "stable-build"
    assert body["target_slot"] == "fallback"
    assert body["stop_source_before_start_target"] is True
    assert body["transient_single_error_auto_switch_allowed"] is False
    assert "enabled = true" in fixture["config"].read_text(encoding="utf-8")


def test_switch_stops_source_before_starting_fallback_and_is_replay_verifiable(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    prepare = _base_command(fixture, action="Prepare", target="fallback")
    prepare.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-ConfirmExplicitOperator",
        ]
    )
    prepared = subprocess.run(
        prepare,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)

    switch = _base_command(fixture, action="Switch", target="fallback")
    switch.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-PreparationReceipt",
            preparation["receipt_path"],
            "-PreparationReceiptSha256",
            preparation["receipt_sha256"],
            "-ConfirmExplicitOperator",
            "-ConfirmSwitch",
        ]
    )
    switched = subprocess.run(
        switch,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert switched.returncode == 0, switched.stdout + switched.stderr
    config = fixture["config"].read_text(encoding="utf-8")
    assert (
        '[plugins."evidence-lane-plugin@evidence-lane-v200-github"]\n'
        "enabled = false"
    ) in config
    assert (
        '[plugins."evidence-lane-plugin@evidence-lane-pv11-fallback"]\n'
        "enabled = true"
    ) in config
    assert "[features]\nenabled = true" in config
    assert (
        fixture["data_root"] / "tunnels" / "stable-build" / "ready.txt"
    ).read_text(encoding="utf-8") == "false"
    assert (
        fixture["data_root"] / "tunnels" / "fallback" / "ready.txt"
    ).read_text(encoding="utf-8") == "true"

    verified = subprocess.run(
        _base_command(fixture, action="Verify", target="stable-build"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert verified.returncode == 0, verified.stdout + verified.stderr
    assert json.loads(verified.stdout)["active_slot"] == "fallback"
    transition = json.loads(
        (
            fixture["data_root"]
            / "receipts"
            / "CODEX_TWO_SLOT_SWITCH_TRANSITION.json"
        ).read_text(encoding="utf-8")
    )
    assert transition["source_tunnel_stopped_first"] is True
    assert transition["target_tunnel_ready_before_plugin_switch"] is True
    assert transition["active_slot_authority"] == (
        "LIVE_CODEX_CONFIG_AND_TUNNEL_MATCH"
    )


def test_verify_accepts_supported_config_api_serialized_mcp_tables(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, tunnel_required=False)
    shutil.rmtree(fixture["data_root"] / "tunnels")
    config = fixture["config"]
    text = config.read_text(encoding="utf-8")
    for selector in (
        "evidence-lane-plugin@evidence-lane-v200-github",
        "evidence-lane-plugin@evidence-lane-pv11-fallback",
    ):
        quoted = f'[plugins."{selector}".mcp_servers."evidence-lane"]'
        serialized = (
            f'[plugins."{selector}".mcp_servers]\n\n'
            f'[plugins."{selector}".mcp_servers.evidence-lane]'
        )
        text = text.replace(quoted, serialized)
    config.write_text(text, encoding="utf-8")

    completed = subprocess.run(
        _base_command(fixture, action="Verify", target="fallback"),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "PASS"
    assert result["active_slot"] == "stable-build"


def test_failed_fallback_start_rolls_back_stable_config_and_tunnel(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, fail_fallback_start=True)
    prepare = _base_command(fixture, action="Prepare", target="fallback")
    prepare.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-ConfirmExplicitOperator",
        ]
    )
    prepared = subprocess.run(
        prepare,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    preparation = json.loads(prepared.stdout)
    switch = _base_command(fixture, action="Switch", target="fallback")
    switch.extend(
        [
            "-Reason",
            "EXPLICIT_OPERATOR_FAILOVER",
            "-TargetProcessId",
            str(os.getpid()),
            "-PreparationReceipt",
            preparation["receipt_path"],
            "-PreparationReceiptSha256",
            preparation["receipt_sha256"],
            "-ConfirmExplicitOperator",
            "-ConfirmSwitch",
        ]
    )
    switched = subprocess.run(
        switch,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert switched.returncode != 0
    assert "Rolled back to stable-build" in switched.stderr
    config = fixture["config"].read_text(encoding="utf-8")
    assert (
        '[plugins."evidence-lane-plugin@evidence-lane-v200-github"]\n'
        "enabled = true"
    ) in config
    assert (
        fixture["data_root"] / "tunnels" / "stable-build" / "ready.txt"
    ).read_text(encoding="utf-8") == "true"
    assert (
        fixture["data_root"] / "tunnels" / "fallback" / "ready.txt"
    ).read_text(encoding="utf-8") == "false"
    failure = json.loads(
        (fixture["data_root"] / "receipts" / "LAST_FAILED_SWITCH.json").read_text(
            encoding="utf-8"
        )
    )
    assert failure["source_restored"] is True
    assert failure["target_disabled"] is True
    assert failure["config_restored"] is True
    assert failure["rollback_verified"] is True
    assert failure["status"] == "FAILED_ROLLED_BACK"
