from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
HOOKS = PLUGIN / "hooks"
STOP_FILES = (
    "behavior_handoff.py",
    "event_isolation.py",
    "event_isolation_policy.json",
    "hooks.json",
    "invoke_hook.py",
    "invoke_hook.ps1",
    "stop_response.py",
)


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _isolation_module() -> ModuleType:
    return _module(
        HOOKS / "event_isolation.py",
        "evidence_lane_stop_contract_isolation_test",
    )


def _payload(root: Path, *, stop_hook_active: bool) -> dict[str, object]:
    return {
        "cwd": str(root),
        "hook_event_name": "Stop",
        "last_assistant_message": "Bounded visible response.",
        "model": "gpt-5.6-sol",
        "permission_mode": "default",
        "session_id": "session-stop-contract",
        "stop_hook_active": stop_hook_active,
        "transcript_path": None,
        "turn_id": "turn-stop-contract",
    }


def _initialize_isolation(
    module: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = module.initialize_inactive_kill_switch(
        root,
        installation_id="stop-contract-test-installation",
    )
    monkeypatch.setenv("EVIDENCE_LANE_RUNTIME_CONTROL_ROOT", str(root))
    monkeypatch.setenv("EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT", receipt["path"])
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256",
        receipt["file_sha256"],
    )
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256",
        module.POLICY_SHA256,
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_stop_policy_distinguishes_upstream_wire_from_strict_project_subset() -> None:
    module = _isolation_module()
    policy = json.loads(
        (HOOKS / "event_isolation_policy.json").read_text(encoding="utf-8")
    )
    contract = policy["stop_contract"]
    stop_event = next(
        row for row in policy["events"] if row["event_name"] == "Stop"
    )

    assert contract["official_input_schema"] == "stop.command.input.schema.json"
    assert contract["official_output_schema"] == "stop.command.output.schema.json"
    assert contract["official_output_control_fields_permitted"] == [
        "continue",
        "decision",
        "reason",
        "stopReason",
        "suppressOutput",
        "systemMessage",
    ]
    assert stop_event["output_schema"] == contract["official_output_schema"]
    assert stop_event["project_output_profile"] == (
        "EXACT_EMPTY_OBJECT_STRICT_SUBSET"
    )
    assert contract["evidence_lane_exact_output"] == module.STOP_EXACT_OUTPUT == {}
    assert contract["continuation_requested"] is False
    assert contract["replay_identity_ignored_fields"] == ["stop_hook_active"]
    assert module.STOP_REPLAY_IGNORED_FIELDS == ("stop_hook_active",)
    assert module.stop_output() == {}
    assert module.stop_output() is not module.STOP_EXACT_OUTPUT


def test_source_stop_handler_first_delivery_and_replay_are_both_empty(
    tmp_path: Path,
) -> None:
    environment = os.environ.copy()
    environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(tmp_path / "source-store")
    for stop_hook_active in (False, True):
        completed = subprocess.run(
            [sys.executable, str(HOOKS / "stop_response.py")],
            input=json.dumps(
                _payload(tmp_path, stop_hook_active=stop_hook_active)
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert json.loads(completed.stdout) == {}
        assert "continue" not in completed.stdout


def test_real_stop_handler_executes_once_and_replays_one_empty_occurrence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _isolation_module()
    _initialize_isolation(module, tmp_path, monkeypatch)
    first = _payload(tmp_path, stop_hook_active=False)
    replay = _payload(tmp_path, stop_hook_active=True)

    assert module.execute_isolated_hook(
        "Stop",
        HOOKS / "stop_response.py",
        (),
        json.dumps(first),
    ) == {}
    assert module.execute_isolated_hook(
        "Stop",
        HOOKS / "stop_response.py",
        (),
        json.dumps(replay),
    ) == {}

    with sqlite3.connect(
        tmp_path / "hook-event-isolation" / "event_receipts.sqlite"
    ) as connection:
        rows = connection.execute(
            "SELECT correlation_id,status,event_name,output_json,failure_code "
            "FROM event_receipt"
        ).fetchall()
    assert len(rows) == 1
    correlation_id, status, event_name, output_json, failure_code = rows[0]
    assert correlation_id.startswith("hook_")
    assert status == "COMPLETE"
    assert event_name == "Stop"
    assert json.loads(output_json) == {}
    assert failure_code is None


def test_package_and_copied_installed_fixture_keep_identical_stop_contract(
    tmp_path: Path,
) -> None:
    installed = tmp_path / "installed-plugin"
    shutil.copytree(
        PLUGIN,
        installed,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "remote_adapter",
            "evidence",
            "release-channels.json",
        ),
    )
    for relative in STOP_FILES:
        assert _sha256(HOOKS / relative) == _sha256(installed / "hooks" / relative)

    package_hooks = json.loads(
        (installed / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    handlers = package_hooks["hooks"]["Stop"][0]["hooks"]
    assert [
        row["command"].rsplit("--handler ", 1)[1] for row in handlers
    ] == [
        "subhook_validate.py",
        "subhook_seal.py",
        "subhook_transport.py",
        "subhook_emit.py",
    ]
    assert [row["commandWindows"] for row in handlers] == [
        '& "${PLUGIN_ROOT}\\hooks\\EvidenceLaneHookHost.exe" '
        f"Stop {name}"
        for name in (
            "subhook_validate.py",
            "subhook_seal.py",
            "subhook_transport.py",
            "subhook_emit.py",
        )
    ]

    environment = os.environ.copy()
    environment["EVIDENCE_LANE_RUNTIME_CONTROL_ROOT"] = str(tmp_path / "installed-store")
    for stop_hook_active in (False, True):
        completed = subprocess.run(
            [sys.executable, str(installed / "hooks" / "stop_response.py")],
            input=json.dumps(
                _payload(installed, stop_hook_active=stop_hook_active)
            ),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert json.loads(completed.stdout) == {}


def test_both_launchers_keep_stop_failures_empty_and_reject_nonempty_output() -> None:
    python_source = (HOOKS / "invoke_hook.py").read_text(encoding="utf-8")
    powershell_source = (HOOKS / "invoke_hook.ps1").read_text(encoding="utf-8")

    assert 'body["event_name"] in {"SessionEnd", "Stop"}' in python_source
    assert "Stop may never block or request another model turn" in python_source
    assert "$EventName -in @('SessionEnd', 'Stop')" in powershell_source
    assert "$result = [ordered]@{}" in powershell_source
    assert "$EventName -eq 'Stop'" in powershell_source
    assert "STOP_OUTPUT_MUST_BE_EMPTY" in powershell_source
