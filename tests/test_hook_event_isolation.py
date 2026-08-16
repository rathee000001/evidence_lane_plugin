from __future__ import annotations

import importlib.util
import json
import sqlite3
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins" / "evidence-lane-plugin" / "hooks"


def _module() -> ModuleType:
    path = HOOKS / "event_isolation.py"
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_hook_event_isolation_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(event_name: str, root: Path) -> dict[str, object]:
    common: dict[str, object] = {
        "cwd": str(root),
        "hook_event_name": event_name,
        "model": "gpt-5.6-sol",
        "permission_mode": "default",
        "session_id": "session-hook-isolation",
        "transcript_path": None,
    }
    if event_name == "SessionStart":
        return {**common, "source": "startup"}
    if event_name == "UserPromptSubmit":
        return {**common, "prompt": "visible prompt", "turn_id": "turn-1"}
    if event_name == "PreToolUse":
        return {
            **common,
            "tool_input": {"path": "bounded"},
            "tool_name": "Read",
            "tool_use_id": "tool-1",
            "turn_id": "turn-1",
        }
    if event_name == "PostToolUse":
        return {
            **common,
            "tool_input": {"path": "bounded"},
            "tool_name": "Read",
            "tool_response": {"status": "PASS"},
            "tool_use_id": "tool-1",
            "turn_id": "turn-1",
        }
    if event_name in {"PreCompact", "PostCompact"}:
        common.pop("permission_mode")
        return {**common, "trigger": "auto", "turn_id": "turn-1"}
    if event_name == "Stop":
        return {
            **common,
            "last_assistant_message": "visible response",
            "stop_hook_active": False,
            "turn_id": "turn-1",
        }
    if event_name == "SessionEnd":
        return {
            "cwd": str(root),
            "hook_event_name": "SessionEnd",
            "reason": "other",
            "session_id": "session-hook-isolation",
            "transcript_path": None,
        }
    raise AssertionError(event_name)


def _initialize(module: ModuleType, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt = module.initialize_inactive_kill_switch(
        root,
        installation_id="test-installation",
    )
    monkeypatch.setenv("EVIDENCE_LANE_DATA_ROOT", str(root))
    monkeypatch.setenv("EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT", receipt["path"])
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256",
        receipt["file_sha256"],
    )
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256",
        module.POLICY_SHA256,
    )


def test_policy_tracks_exact_eight_governed_events_and_current_upstream_boundary() -> None:
    module = _module()
    policy = json.loads((HOOKS / "event_isolation_policy.json").read_text("utf-8"))

    assert policy["schema"] == module.POLICY_SCHEMA
    assert tuple(row["event_name"] for row in policy["events"]) == module.EVENT_ORDER
    assert policy["upstream_contract"]["upstream_event_count"] == 11
    assert policy["upstream_contract"]["governed_event_count"] == 8
    assert policy["upstream_contract"]["unsupported_events_not_relabelled"] == [
        "PermissionRequest",
        "SubagentStart",
        "SubagentStop",
    ]
    assert policy["events"][-1]["host_timeout_seconds"] == 3
    assert policy["events"][-1]["internal_handler_timeout_seconds"] == 2
    handoff = policy["behavior_handoff"]
    assert handoff["issue_timing"] == "AFTER_EXACT_SKILL_CONSUMER_RETURNS"
    assert handoff["consume_timing"] == "BEFORE_HANDLER_OUTPUT"
    assert handoff["consumer_module_function_path_and_sha256_required"] is True
    assert handoff["partial_event_isolation_binding"] == "FAIL_CLOSED"
    assert handoff["hook_behavior_executed"] is False
    assert handoff["skill_action_executed"] is True
    assert handoff["session_end_durable_skill_handoff_claimed"] is False
    assert len(module.POLICY_SHA256) == 64


@pytest.mark.parametrize("event_name", _module().EVENT_ORDER)
def test_each_event_has_an_exact_input_schema(event_name: str, tmp_path: Path) -> None:
    module = _module()
    raw = json.dumps(_payload(event_name, tmp_path))
    payload, occurrence_sha256 = module.validate_input(event_name, raw)

    assert payload["hook_event_name"] == event_name
    assert len(occurrence_sha256) == 64

    unexpected = dict(payload)
    unexpected["unapproved_field"] = "blocked"
    with pytest.raises(module.HookEventIsolationError, match="INPUT_SCHEMA_MISMATCH"):
        module.validate_input(event_name, json.dumps(unexpected))


def test_stop_replay_ignores_only_stop_hook_active_and_requires_empty_output(
    tmp_path: Path,
) -> None:
    module = _module()
    first = _payload("Stop", tmp_path)
    replay = {**first, "stop_hook_active": True}

    _, first_sha = module.validate_input("Stop", json.dumps(first))
    _, replay_sha = module.validate_input("Stop", json.dumps(replay))
    assert first_sha == replay_sha
    module.validate_output("Stop", {})
    with pytest.raises(
        module.HookEventIsolationError,
        match="STOP_OUTPUT_CONTRACT_MISMATCH",
    ):
        module.validate_output("Stop", {"continue": True})


def test_exact_stop_occurrence_executes_once_and_replays_empty_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    _initialize(module, tmp_path, monkeypatch)
    counter = tmp_path / "counter.txt"
    handler = tmp_path / "handler.py"
    handler.write_text(
        """from pathlib import Path
import json
import sys
json.load(sys.stdin)
path = Path(sys.argv[1])
count = int(path.read_text() if path.exists() else '0') + 1
path.write_text(str(count))
print('{}')
""",
        encoding="utf-8",
    )
    first = _payload("Stop", tmp_path)
    replay = {**first, "stop_hook_active": True}

    assert module.execute_isolated_hook(
        "Stop", handler, (str(counter),), json.dumps(first)
    ) == {}
    assert module.execute_isolated_hook(
        "Stop", handler, (str(counter),), json.dumps(replay)
    ) == {}
    assert counter.read_text(encoding="utf-8") == "1"

    database = sqlite3.connect(
        tmp_path / "hook-event-isolation" / "event_receipts.sqlite"
    )
    assert database.execute("SELECT COUNT(*) FROM event_receipt").fetchone()[0] == 1
    status, event_name, output = database.execute(
        "SELECT status,event_name,output_json FROM event_receipt"
    ).fetchone()
    assert (status, event_name, json.loads(output)) == ("COMPLETE", "Stop", {})


def test_exact_owner_reentrancy_lock_denies_nested_handler_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    _initialize(module, tmp_path, monkeypatch)
    payload = _payload("UserPromptSubmit", tmp_path)
    raw = json.dumps(payload)
    _, input_sha256 = module.validate_input("UserPromptSubmit", raw)
    owner_sha256, _ = module._identity(
        "UserPromptSubmit", payload, input_sha256
    )
    handler = tmp_path / "never.py"
    handler.write_text("raise SystemExit('must not execute')\n", encoding="utf-8")

    with (
        module._OwnerLock(owner_sha256),
        pytest.raises(
            module.HookEventIsolationError,
            match="REENTRANCY_DENIED",
        ),
    ):
        module.execute_isolated_hook(
            "UserPromptSubmit",
            handler,
            (),
            raw,
        )


def test_missing_or_active_kill_switch_fails_closed_before_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    monkeypatch.setenv("EVIDENCE_LANE_DATA_ROOT", str(tmp_path))
    with pytest.raises(module.HookEventIsolationError, match="RECEIPT_MISSING"):
        module.verify_inactive_kill_switch()

    _initialize(module, tmp_path, monkeypatch)
    path = module.kill_switch_path()
    body = json.loads(path.read_text(encoding="utf-8"))
    body["payload"]["state"] = "ACTIVE"
    body["payload_sha256"] = module._sha256(
        module._canonical_bytes(body["payload"])
    )
    path.write_bytes(module._canonical_bytes(body))
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256",
        module._sha256(path.read_bytes()),
    )
    with pytest.raises(module.HookEventIsolationError, match="KILL_SWITCH_ACTIVE"):
        module.verify_inactive_kill_switch()


def test_kill_switch_initialization_is_idempotent_and_generation_monotonic(
    tmp_path: Path,
) -> None:
    module = _module()
    first = module.initialize_inactive_kill_switch(
        tmp_path,
        installation_id="install-one",
    )
    replay = module.initialize_inactive_kill_switch(
        tmp_path,
        installation_id="install-one",
    )
    rotated = module.initialize_inactive_kill_switch(
        tmp_path,
        installation_id="install-two",
    )

    assert first["state"] == "INACTIVE_KILL_SWITCH_INITIALIZED"
    assert first["generation"] == 1
    assert replay["state"] == "INACTIVE_KILL_SWITCH_REUSED"
    assert replay["file_sha256"] == first["file_sha256"]
    assert replay["generation"] == 1
    assert rotated["state"] == "INACTIVE_KILL_SWITCH_INITIALIZED"
    assert rotated["generation"] == 2
    assert rotated["prior_file_sha256"] == first["file_sha256"]
    body = json.loads(Path(rotated["path"]).read_text(encoding="utf-8"))
    assert body["payload"]["installation_id"] == "install-two"
    assert body["payload"]["state"] == "INACTIVE"


def test_active_kill_switch_is_never_reset_by_a_new_install(
    tmp_path: Path,
) -> None:
    module = _module()
    receipt = module.initialize_inactive_kill_switch(
        tmp_path,
        installation_id="install-one",
    )
    path = Path(receipt["path"])
    body = json.loads(path.read_text(encoding="utf-8"))
    body["payload"]["state"] = "ACTIVE"
    body["payload_sha256"] = module._sha256(
        module._canonical_bytes(body["payload"])
    )
    path.write_bytes(module._canonical_bytes(body))
    active_sha256 = module._sha256(path.read_bytes())

    with pytest.raises(module.HookEventIsolationError, match="KILL_SWITCH_ACTIVE"):
        module.initialize_inactive_kill_switch(
            tmp_path,
            installation_id="install-two",
        )
    assert module._sha256(path.read_bytes()) == active_sha256
    assert json.loads(path.read_text(encoding="utf-8"))["payload"]["state"] == (
        "ACTIVE"
    )


def test_session_end_handler_is_killed_inside_host_three_second_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _module()
    _initialize(module, tmp_path, monkeypatch)
    handler = tmp_path / "slow.py"
    handler.write_text(
        "import json, sys, time\njson.load(sys.stdin)\ntime.sleep(5)\nprint('{}')\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    output = module.execute_isolated_hook(
        "SessionEnd",
        handler,
        (),
        json.dumps(_payload("SessionEnd", tmp_path)),
    )
    elapsed = time.monotonic() - started

    assert output == {}
    assert elapsed < 3
    database = sqlite3.connect(
        tmp_path / "hook-event-isolation" / "event_receipts.sqlite"
    )
    assert database.execute(
        "SELECT status,failure_code FROM event_receipt"
    ).fetchone() == ("FAIL_CLOSED", "HOOK_EVENT_HANDLER_TIMEOUT")


def test_windows_launcher_requires_install_bound_kill_switch_before_activation() -> None:
    source = (HOOKS / "invoke_hook.ps1").read_text(encoding="utf-8")
    assert "verified_before_install_activation" in source
    assert "persistent_kill_switch" in source
    assert "kill_switch_receipt_path" in source
    assert "kill_switch_receipt_sha256" in source
    assert "policy_sha256" in source
    assert "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT" in source
    assert "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256" in source
    assert "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256" in source
    assert "$runtimeMatches = @{}" in source
    assert "$matches = @{}" not in source.lower()
    assert "payload.state -notin @('INACTIVE', 'ACTIVE')" in source
