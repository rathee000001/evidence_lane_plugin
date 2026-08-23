from __future__ import annotations

import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
from evidence_lane_plugin.hook_contract import (
    HOOK_CAPABILITY_SCHEMA,
    HOOK_CONTRACT_SCHEMA,
    HOOK_EVENT_NAMES,
    HOOK_TRANSPORT_SCHEMA,
    MAX_VISIBLE_INPUT_CHARS,
    HookContractError,
    build_hook_transport_envelope,
    hook_capability_receipt,
    lifecycle_hook_contract,
    validate_hook_configuration,
)

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "evidence-lane-plugin"
HOOK_ADAPTERS = (
    "session_start.py",
    "optional_event_observer.py",
    "prompt_submit.py",
    "pre_tool_use.py",
    "post_tool_use.py",
    "lifecycle_boundary.py",
    "stop_response.py",
)


def _launcher_module() -> ModuleType:
    path = PLUGIN / "hooks" / "invoke_hook.py"
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_invoke_hook_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lifecycle_boundary_module() -> ModuleType:
    path = PLUGIN / "hooks" / "lifecycle_boundary.py"
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_lifecycle_boundary_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _hook_adapter_module(name: str) -> ModuleType:
    path = PLUGIN / "hooks" / name
    spec = importlib.util.spec_from_file_location(
        f"evidence_lane_{path.stem}_test",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_eleven_event_hook_contract_keeps_behavior_skill_owned() -> None:
    contract = lifecycle_hook_contract()

    assert contract["schema"] == HOOK_CONTRACT_SCHEMA
    assert contract["version"] == 1
    assert tuple(contract["event_order"]) == HOOK_EVENT_NAMES == (
        "SessionStart",
        "SubagentStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SubagentStop",
        "Stop",
        "SessionEnd",
    )
    assert contract["registered_event_count"] == 11
    assert contract["hook_owner"] == (
        "VALIDATE_REDACT_BOUND_DEDUPLICATE_AND_TRANSPORT_ONLY"
    )
    assert contract["skill_owner"] == (
        "ENTRY_PREPARE_TOOL_BOUNDARIES_COMPACTION_COMMIT_NATIVE_READS_"
        "CLASSIFICATION_PLAN_REFRESH_GOAL_AND_HIL"
    )
    assert contract["skill_runtime_consumer"] == (
        "evidence_lane_plugin.hook_skill_runtime"
    )
    assert contract["windows_interpreter_resolution"] == (
        "SEALED_DERIVED_RUNTIME_ONLY"
    )
    assert contract["windows_process_window_mode"] == (
        "HOST_MANAGED_NO_CHILD_WINDOW"
    )
    assert contract["windows_command_launcher"] == "EvidenceLaneHookHost.exe"
    assert contract["windows_child_create_no_window"] is True
    assert contract["windows_path_lookup_allowed"] is False
    assert contract["session_end_host_timeout_seconds"] == 3
    assert contract["permission_request_policy"] == (
        "OBSERVE_ONLY_NEVER_GRANT_OR_DENY"
    )
    assert contract["subagent_events_in_scope"] is True
    assert contract["subagent_event_policy"] == (
        "BOUND_OBSERVATION_ONLY_NEVER_CONTROL"
    )
    assert contract["full_plan_allowed_in_hook_payload"] is False
    assert contract["linked_delta_json_allowed_in_hook_payload"] is False
    assert contract["private_reasoning_allowed"] is False
    assert len(contract["contract_sha256"]) == 64

    events = {row["event_name"]: row for row in contract["events"]}
    assert events["UserPromptSubmit"]["skill_action_owner"] == (
        "SKILL_PREPARE_THEN_NATIVE_READ_SEQUENCE"
    )
    assert events["SessionEnd"]["delivery"] == (
        "BEST_EFFORT_HOST_CAPABILITY_GATED"
    )


def test_package_hook_configuration_matches_contract_order_and_handlers() -> None:
    configuration = json.loads(
        (PLUGIN / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )
    receipt = validate_hook_configuration(configuration)

    assert receipt["status"] == "PASS"
    assert receipt["schema"] == HOOK_CONTRACT_SCHEMA
    assert receipt["event_order"] == list(HOOK_EVENT_NAMES)
    assert receipt["handler_count"] == 11
    assert len(receipt["contract_sha256"]) == 64
    assert len(receipt["configuration_sha256"]) == 64
    assert "PermissionRequest" in configuration["hooks"]
    assert {"SubagentStart", "SubagentStop"}.issubset(configuration["hooks"])
    records = {row["event_name"]: row for row in receipt["handler_records"]}
    assert records["SessionEnd"]["timeout"] == 3
    assert all(
        row["timeout"] == 10
        for event_name, row in records.items()
        if event_name != "SessionEnd"
    )
    assert all(
        row["windows_process_window_mode"] == "HOST_MANAGED_NO_CHILD_WINDOW"
        for row in records.values()
    )
    assert all(row["windows_child_create_no_window"] is True for row in records.values())
    assert all(
        row["windows_interpreter_resolution"] == "SEALED_DERIVED_RUNTIME_ONLY"
        for row in records.values()
    )
    for event_name, groups in configuration["hooks"].items():
        handler = groups[0]["hooks"][0]
        windows = handler["commandWindows"]
        assert windows.startswith(
            '& "${PLUGIN_ROOT}\\hooks\\EvidenceLaneHookHost.exe" '
        )
        assert "%SystemRoot%" not in windows
        assert "%PLUGIN_ROOT%" not in windows
        assert "powershell.exe" not in windows.casefold()
        assert "invoke_hook.ps1" not in windows.casefold()
        assert event_name in windows
        assert not windows.casefold().startswith("python ")

    reordered = json.loads(json.dumps(configuration))
    reordered["hooks"] = {
        name: reordered["hooks"][name] for name in reversed(HOOK_EVENT_NAMES)
    }
    with pytest.raises(HookContractError, match="HOOK_EVENT_ORDER"):
        validate_hook_configuration(reordered)

    stale_timeout = json.loads(json.dumps(configuration))
    stale_timeout["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] = 10
    with pytest.raises(HookContractError, match="HOOK_HOST_TIMEOUT_MISMATCH"):
        validate_hook_configuration(stale_timeout)


def test_hook_launcher_is_mapping_bound_secret_safe_and_host_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _launcher_module()

    assert launcher.main(["--event", "NoSuchEvent", "--handler", "x.py"]) == 0
    unsupported = json.loads(capsys.readouterr().out)
    assert set(unsupported) == {"systemMessage"}
    assert "HOOK_EVENT_UNSUPPORTED" in unsupported["systemMessage"]
    assert "SECRET_VALUE" not in json.dumps(unsupported)

    assert launcher.main(
        ["--event", "PreCompact", "--handler", "stop_response.py"]
    ) == 0
    mismatch = json.loads(capsys.readouterr().out)
    assert set(mismatch) == {"systemMessage"}
    assert "HOOK_EVENT_HANDLER_MAPPING_MISMATCH" in mismatch["systemMessage"]
    diagnostic = mismatch["systemMessage"]
    assert "SEALED_DERIVED_RUNTIME_ONLY" in diagnostic
    assert "HIDDEN_ON_WINDOWS" in diagnostic

    calls: list[tuple[str, tuple[str, ...], str]] = []
    monkeypatch.setattr(launcher, "_reexec_sealed_runtime", lambda *_: None)

    def fake_execute(
        _event_name: str,
        path: Path,
        handler_args: tuple[str, ...],
        raw_payload: str,
    ) -> dict[str, str]:
        calls.append((path.name, handler_args, raw_payload))
        return {"systemMessage": "bounded-isolation-test"}

    monkeypatch.setattr(launcher, "_execute_isolated_handler", fake_execute)
    monkeypatch.setattr(launcher.sys, "stdin", io.StringIO("{}"))
    assert launcher.main(
        ["--event", "PreCompact", "--handler", "lifecycle_boundary.py"]
    ) == 0
    assert calls == [("lifecycle_boundary.py", ("PreCompact",), "{}")]
    assert json.loads(capsys.readouterr().out) == {
        "systemMessage": "bounded-isolation-test"
    }


def test_windows_hook_launcher_is_hidden_runtime_bound_and_noninteractive() -> None:
    source = (PLUGIN / "hooks" / "invoke_hook.ps1").read_text(encoding="utf-8")

    assert "[Console]::In.ReadToEnd()" in source
    assert "requirements_lock_sha256" in source
    assert "runtime_key" in source
    assert ".Substring(0, 32)" in source
    assert "activation.runtime_prewarm.runtime_projection_root" in source
    assert "activation.plugin_add.installedPath" in source
    assert "$receipt.status -ne 'PASS'" in source
    assert "$receipt.activation.runtime_prewarm.status -ne 'PASS'" in source
    assert "EVIDENCE_LANE_HOOK_BOUND_RUNTIME_ROOT" in source
    assert "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT" in source
    assert "EVIDENCE_LANE_HOOK_KILL_SWITCH_RECEIPT_SHA256" in source
    assert "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256" in source
    assert "verified_before_install_activation" in source
    assert "persistent_kill_switch" in source
    assert "ConvertFrom-Json -ErrorAction Stop" in source
    assert "PRE_TOOL_USE_OUTPUT_SCHEMA_INVALID" in source
    assert "STOP_OUTPUT_MUST_BE_EMPTY" in source
    assert "SESSION_END_OUTPUT_MUST_BE_EMPTY" in source
    assert "INSTALL_*.json" in source
    assert "venv\\Scripts\\python.exe" in source
    assert "SEALED_RUNTIME_INTERPRETER_NOT_FOUND" in source
    assert "SEALED_RUNTIME_INTERPRETER_AMBIGUOUS" in source
    assert "function Get-Sha256" in source
    assert "[Security.Cryptography.SHA256]::Create()" in source
    assert "Get-FileHash" not in source
    assert "Start-Process" not in source
    assert "cmd.exe" not in source


@pytest.mark.skipif(sys.platform != "win32", reason="Windows hook transport")
def test_windows_hook_host_is_synchronous_and_never_spawns_a_child_console(
    tmp_path: Path,
) -> None:
    executable = PLUGIN / "hooks" / "EvidenceLaneHookHost.exe"
    source = (
        PLUGIN
        / "scripts"
        / "codex_release"
        / "windows_hook_host"
        / "EvidenceLaneHookHost.cs"
    ).read_text(encoding="utf-8")
    assert executable.is_file()
    assert "UseShellExecute = false" in source
    assert "CreateNoWindow = true" in source
    assert "WindowStyle = ProcessWindowStyle.Hidden" in source
    assert "RedirectStandardInput = true" in source
    assert "RedirectStandardOutput = true" in source
    assert "RedirectStandardError = true" in source
    assert '"SessionStart", "session_start.py"' in source
    assert '"SessionEnd", "lifecycle_boundary.py"' in source

    raw = executable.read_bytes()
    pe_offset = struct.unpack_from("<I", raw, 0x3C)[0]
    optional_header = pe_offset + 24
    magic = struct.unpack_from("<H", raw, optional_header)[0]
    subsystem_offset = optional_header + (88 if magic == 0x20B else 68)
    assert struct.unpack_from("<H", raw, subsystem_offset)[0] == 3

    environment = os.environ.copy()
    environment["EVIDENCE_LANE_DATA_ROOT"] = str(tmp_path)
    result = subprocess.run(
        [str(executable), "SessionStart", "session_start.py"],
        input=b'{"hook_event_name":"SessionStart"}',
        capture_output=True,
        check=False,
        env=environment,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout.decode("utf-8"))
    assert "SEALED_RUNTIME_INTERPRETER_NOT_FOUND" in output["systemMessage"]
    assert result.stderr == b""

    mismatch = subprocess.run(
        [str(executable), "SessionStart", "stop_response.py"],
        input=b"{}",
        capture_output=True,
        check=False,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert mismatch.returncode == 65
    assert mismatch.stdout == b""
    assert mismatch.stderr == b""


def test_compaction_boundary_uses_only_native_universal_output_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    boundary = _lifecycle_boundary_module()

    for event_name, should_continue in (("PreCompact", True), ("PostCompact", False)):
        monkeypatch.setattr(
            boundary,
            "_record",
            lambda _payload, _event, value=should_continue: (
                {"state": "RECORDED", "private_reasoning_stored": False},
                value,
            ),
        )
        monkeypatch.setattr(boundary.sys, "stdin", io.StringIO("{}"))
        monkeypatch.setattr(
            boundary.sys,
            "argv",
            ["lifecycle_boundary.py", event_name],
        )

        assert boundary.main() == 0
        output = json.loads(capsys.readouterr().out)
        expected = {"systemMessage"}
        if not should_continue:
            expected.update({"continue", "stopReason"})
        assert set(output) == expected
        assert "hookSpecificOutput" not in output
        assert output["systemMessage"].startswith(
            "EVIDENCE_LANE_LIFECYCLE_BOUNDARY="
        )


def test_pre_tool_use_uses_permission_schema_without_unsupported_common_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _hook_adapter_module("pre_tool_use.py")
    monkeypatch.setattr(
        adapter,
        "_guard",
        lambda _payload: ({"state": "TURN_CONTROL_GAP"}, False),
    )
    monkeypatch.setattr(adapter.sys, "stdin", io.StringIO("{}"))
    assert adapter.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert "continue" not in output
    assert "stopReason" not in output
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"]


def test_stop_success_never_requests_an_automatic_continuation(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _hook_adapter_module("stop_response.py")
    monkeypatch.setattr(
        adapter,
        "_record",
        lambda _payload: {"state": "TURN_CONTROL_NOT_REQUIRED_YET"},
    )
    monkeypatch.setattr(adapter.sys, "stdin", io.StringIO("{}"))
    assert adapter.main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_terminal_launcher_failures_are_output_inert(
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _launcher_module()
    for event_name in ("Stop", "SessionEnd"):
        assert launcher._failure(event_name, "TEST_FAILURE") == 0
        assert json.loads(capsys.readouterr().out) == {}


def test_unrelated_hook_events_are_output_inert(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prompt = _hook_adapter_module("prompt_submit.py")
    monkeypatch.setattr(
        prompt,
        "_record",
        lambda _payload: ({"state": "NOT_INDEXED"}, True),
    )
    monkeypatch.setattr(prompt.sys, "stdin", io.StringIO("{}"))
    assert prompt.main() == 0
    assert json.loads(capsys.readouterr().out) == {}

    pre_tool = _hook_adapter_module("pre_tool_use.py")
    monkeypatch.setattr(
        pre_tool,
        "_guard",
        lambda _payload: ({"state": "NOT_GOVERNED"}, True),
    )
    monkeypatch.setattr(pre_tool.sys, "stdin", io.StringIO("{}"))
    assert pre_tool.main() == 0
    assert json.loads(capsys.readouterr().out) == {}

    boundary = _lifecycle_boundary_module()
    monkeypatch.setattr(
        boundary,
        "_record",
        lambda _payload, event: ({"state": "NOT_GOVERNED"}, True),
    )
    monkeypatch.setattr(boundary.sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(
        boundary.sys,
        "argv",
        ["lifecycle_boundary.py", "PreCompact"],
    )
    assert boundary.main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_stop_and_post_tool_notices_do_not_embed_full_receipts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop_adapter = _hook_adapter_module("stop_response.py")
    monkeypatch.setattr(
        stop_adapter,
        "_record",
        lambda _payload: {
            "state": "COMMITTED",
            "receipt_sha256": "A" * 64,
            "secret_sentinel": "MUST_NOT_APPEAR",
        },
    )
    monkeypatch.setattr(stop_adapter.sys, "stdin", io.StringIO("{}"))
    assert stop_adapter.main() == 0
    stop_output = json.loads(capsys.readouterr().out)
    assert stop_output == {}
    assert "MUST_NOT_APPEAR" not in json.dumps(stop_output)

    post_adapter = _hook_adapter_module("post_tool_use.py")
    monkeypatch.setattr(
        post_adapter,
        "_project",
        lambda _payload: {
            "state": "TURN_CONTROL_GAP",
            "code": "BOUNDED_GAP",
            "error_type": "RuntimeError",
            "secret_sentinel": "MUST_NOT_APPEAR",
        },
    )
    monkeypatch.setattr(post_adapter.sys, "stdin", io.StringIO("{}"))
    assert post_adapter.main() == 0
    post_output = json.loads(capsys.readouterr().out)
    assert post_output == {
        "systemMessage": (
            "EVIDENCE_LANE_PERSISTENT_CHANGE_DISPLAY_GAP="
            "BOUNDED_GAP:RuntimeError"
        )
    }
    assert "MUST_NOT_APPEAR" not in json.dumps(post_output)


def test_post_tool_success_projects_only_the_bounded_current_change(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _hook_adapter_module("post_tool_use.py")
    monkeypatch.setattr(
        adapter,
        "_project",
        lambda _payload: {
            "state": "PROJECTED",
            "persistent_change_display": {
                "schema": "evidence-lane.codex-persistent-change-display.v1",
                "display_sha256": "D" * 64,
            },
            "secret_sentinel": "MUST_NOT_APPEAR",
        },
    )
    bounded_notice = {
        "schema": "evidence-lane.codex-persistent-change-system-notice.v2",
        "phase": "POST_TOOL_USE",
        "notice_sha256": "N" * 64,
        "tool_projection": {
            "tool_name": "mcp__evidence_lane__pv_status",
            "tool_input_stored": False,
            "tool_response_stored": False,
        },
    }
    monkeypatch.setattr(
        adapter,
        "_load_runtime",
        lambda: (
            None,
            None,
            lambda _display, *, phase, turn_receipt: (
                f"Evidence Lane CURRENT CHANGE | {phase}",
                bounded_notice,
            ),
        ),
    )
    monkeypatch.setattr(adapter.sys, "stdin", io.StringIO("{}"))
    assert adapter.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["continue"] is True
    assert output["systemMessage"].endswith(
        "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION=" + "N" * 24
    )
    specific = output["hookSpecificOutput"]
    assert specific["hookEventName"] == "PostToolUse"
    prefix = "EVIDENCE_LANE_PERSISTENT_CHANGE_TOOL_PROJECTION="
    assert json.loads(specific["additionalContext"].removeprefix(prefix)) == (
        bounded_notice
    )
    assert "MUST_NOT_APPEAR" not in json.dumps(output)


def test_python_launcher_rejects_receipt_runtime_binding_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = _launcher_module()
    plugin_root = tmp_path / "plugin"
    environment = tmp_path / "runtime-a" / "venv"
    marker = tmp_path / "runtime-a" / "RUNTIME_READY.json"
    python = environment / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("placeholder", encoding="utf-8")
    marker.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        launcher,
        "_runtime_contract",
        lambda _root: (
            lambda _plugin_root, _marker: True,
            lambda _plugin_root: environment,
            lambda _plugin_root: marker,
        ),
    )
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_BOUND_RUNTIME_ROOT",
        str(tmp_path / "runtime-b"),
    )
    with pytest.raises(RuntimeError, match="INSTALL_BINDING_MISMATCH"):
        launcher._reexec_sealed_runtime(plugin_root, [])


def test_launcher_failure_uses_event_specific_pre_tool_schema(
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _launcher_module()
    assert launcher._failure("PreToolUse", "TEST_FAILURE") == 0
    output = json.loads(capsys.readouterr().out)
    assert "continue" not in output
    assert "stopReason" not in output
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_session_end_is_transport_only_and_emits_no_ignored_contract_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    boundary = _lifecycle_boundary_module()

    monkeypatch.setattr(
        boundary,
        "_load_transport_builder",
        lambda: lambda _event, _payload: {"transport_receipt_sha256": "A" * 64},
    )
    monkeypatch.setattr(
        boundary,
        "_load_runtime",
        lambda: (_ for _ in ()).throw(AssertionError("durable runtime imported")),
    )
    receipt, should_continue = boundary._record({}, "SessionEnd")
    assert should_continue is True
    assert receipt["state"] == "BEST_EFFORT_TRANSPORT_SEALED"
    assert receipt["durable_skill_consumer_invoked"] is False

    monkeypatch.setattr(boundary, "_record", lambda _payload, _event: (receipt, True))
    monkeypatch.setattr(boundary.sys, "stdin", io.StringIO("{}"))
    monkeypatch.setattr(
        boundary.sys,
        "argv",
        ["lifecycle_boundary.py", "SessionEnd"],
    )
    assert boundary.main() == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_hook_adapters_delegate_behavior_to_the_skill_runtime() -> None:
    forbidden_direct_calls = (
        "prepare_turn(",
        "commit_turn(",
        "record_tool_event(",
        "record_lifecycle_boundary_event(",
        "session_start_control(",
        "current_persistent_change_display(",
    )
    for name in HOOK_ADAPTERS:
        source = (PLUGIN / "hooks" / name).read_text(encoding="utf-8")
        assert "hook_skill_runtime" in source
        for forbidden in forbidden_direct_calls:
            assert forbidden not in source

    consumer = (
        PLUGIN
        / "src"
        / "evidence_lane_plugin"
        / "hook_skill_runtime.py"
    ).read_text(encoding="utf-8")
    assert (
        'SKILL_RUNTIME_OWNER = "INSTALLED_EVIDENCE_LANE_CODE_LIFECYCLE_SKILL"'
        in consumer
    )
    assert "prepare_turn(" in consumer
    assert "commit_turn(" in consumer
    assert "record_tool_event(" in consumer
    assert "record_lifecycle_boundary_event(" in consumer


def test_optional_event_wrappers_bind_one_stable_event_to_the_shared_observer() -> None:
    wrappers = {
        "subagent_start.py": "SubagentStart",
        "permission_request.py": "PermissionRequest",
        "subagent_stop.py": "SubagentStop",
    }
    for name, event_name in wrappers.items():
        source = (PLUGIN / "hooks" / name).read_text(encoding="utf-8")
        assert "from optional_event_observer import run" in source
        assert f'run("{event_name}")' in source


def test_hook_transport_envelope_is_secret_safe_bounded_and_idempotent() -> None:
    payload = {
        "session_id": "host-session-one",
        "turn_id": "turn-one",
        "hook_event_id": "hook-occurrence-one",
        "cwd": str(ROOT),
        "prompt": "Inspect api_key=SECRET_VALUE_1234567890 and continue.",
        "model": "gpt-5.6-sol",
        "permission_mode": "standard",
    }

    first = build_hook_transport_envelope("UserPromptSubmit", payload)
    replay = build_hook_transport_envelope("UserPromptSubmit", payload)

    assert first == replay
    assert first["schema"] == HOOK_TRANSPORT_SCHEMA
    assert first["event_name"] == "UserPromptSubmit"
    assert first["event_ordinal"] == 3
    assert first["skill_action_owner"] == (
        "SKILL_PREPARE_THEN_NATIVE_READ_SEQUENCE"
    )
    assert "SECRET_VALUE" not in json.dumps(first)
    assert "[REDACTED]" in first["safe_payload"][
        "visible_input_after_redaction"
    ]
    assert first["transport_bytes"] < 65_536
    assert first["hook_behavior_executed"] is False
    assert first["native_pv_tool_called"] is False
    assert first["classification_performed"] is False
    assert first["plan_refreshed"] is False
    assert first["goal_mutated"] is False
    assert first["hil_inferred"] is False
    assert first["pointer_moved"] is False
    assert first["source_mutated"] is False
    assert first["candidate_created"] is False
    assert first["full_plan_included"] is False
    assert first["linked_delta_json_included"] is False
    assert first["raw_payload_stored"] is False
    assert first["raw_secret_stored"] is False
    assert first["private_reasoning_stored"] is False
    assert len(first["transport_receipt_sha256"]) == 64

    with pytest.raises(HookContractError, match="FORBIDDEN_PAYLOAD"):
        build_hook_transport_envelope(
            "UserPromptSubmit",
            {**payload, "private_reasoning": "never persist this"},
        )
    with pytest.raises(HookContractError, match="VISIBLE_INPUT_BOUND"):
        build_hook_transport_envelope(
            "UserPromptSubmit",
            {**payload, "prompt": "x" * (MAX_VISIBLE_INPUT_CHARS + 1)},
        )


def test_unavailable_host_events_are_reported_without_false_success() -> None:
    supported = set(HOOK_EVENT_NAMES).difference(
        {"PermissionRequest", "SessionEnd"}
    )
    receipt = hook_capability_receipt(supported)

    assert receipt["schema"] == HOOK_CAPABILITY_SCHEMA
    states = {row["event_name"]: row["state"] for row in receipt["events"]}
    assert states["SessionStart"] == "HOST_CAPABILITY_AVAILABLE"
    assert states["SessionEnd"] == "HOST_CAPABILITY_UNAVAILABLE"
    assert receipt["permission_request"] == {
        "state": "HOST_CAPABILITY_UNAVAILABLE",
        "caller_capability_hint_matched": True,
        "control_policy": "OBSERVE_ONLY_NEVER_GRANT_OR_DENY",
    }
    assert receipt["subagent_events_in_scope"] is True
    assert receipt["unsupported_events_relabelled_as_success"] is False
    assert len(receipt["capability_receipt_sha256"]) == 64

    permission_available = hook_capability_receipt(
        [*supported, "PermissionRequest"],
        permission_request_supported=True,
    )
    assert permission_available["permission_request"]["state"] == (
        "HOST_CAPABILITY_AVAILABLE"
    )
    with pytest.raises(HookContractError, match="UNKNOWN_HOST"):
        hook_capability_receipt([*supported, "NoSuchHook"])
