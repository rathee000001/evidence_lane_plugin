from __future__ import annotations

import importlib.util
import io
import json
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


def test_exact_eight_event_hook_contract_keeps_behavior_skill_owned() -> None:
    contract = lifecycle_hook_contract()

    assert contract["schema"] == HOOK_CONTRACT_SCHEMA
    assert contract["version"] == 1
    assert tuple(contract["event_order"]) == HOOK_EVENT_NAMES == (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "Stop",
        "SessionEnd",
    )
    assert contract["registered_event_count"] == 8
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
    assert contract["windows_process_window_mode"] == "HIDDEN"
    assert contract["windows_path_lookup_allowed"] is False
    assert contract["session_end_host_timeout_seconds"] == 3
    assert contract["permission_request_policy"] == (
        "CONDITIONAL_ONLY_AFTER_EXPLICIT_HOST_CAPABILITY_PROOF"
    )
    assert contract["subagent_events_in_scope"] is False
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
    assert receipt["handler_count"] == 8
    assert len(receipt["contract_sha256"]) == 64
    assert len(receipt["configuration_sha256"]) == 64
    assert "PermissionRequest" not in configuration["hooks"]
    assert all("subagent" not in event.casefold() for event in configuration["hooks"])
    records = {row["event_name"]: row for row in receipt["handler_records"]}
    assert records["SessionEnd"]["timeout"] == 3
    assert all(
        row["timeout"] == 10
        for event_name, row in records.items()
        if event_name != "SessionEnd"
    )
    assert all(row["windows_process_window_mode"] == "HIDDEN" for row in records.values())
    assert all(
        row["windows_interpreter_resolution"] == "SEALED_DERIVED_RUNTIME_ONLY"
        for row in records.values()
    )
    for event_name, groups in configuration["hooks"].items():
        handler = groups[0]["hooks"][0]
        windows = handler["commandWindows"]
        assert windows.startswith(
            '& "$env:SystemRoot\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" '
        )
        assert '"${PLUGIN_ROOT}\\hooks\\invoke_hook.ps1"' in windows
        assert "%SystemRoot%" not in windows
        assert "%PLUGIN_ROOT%" not in windows
        assert "-NoProfile -NonInteractive" in windows
        assert "-WindowStyle Hidden" in windows
        assert "hooks\\invoke_hook.ps1" in windows
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


def test_hook_launcher_is_mapping_bound_secret_safe_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    launcher = _launcher_module()

    assert launcher.main(["--event", "NoSuchEvent", "--handler", "x.py"]) == 0
    unsupported = json.loads(capsys.readouterr().out)
    assert unsupported["continue"] is False
    assert "HOOK_EVENT_UNSUPPORTED" in unsupported["stopReason"]
    assert "SECRET_VALUE" not in json.dumps(unsupported)

    assert launcher.main(
        ["--event", "PreCompact", "--handler", "stop_response.py"]
    ) == 0
    mismatch = json.loads(capsys.readouterr().out)
    assert mismatch["continue"] is False
    assert "HOOK_EVENT_HANDLER_MAPPING_MISMATCH" in mismatch["stopReason"]
    diagnostic = mismatch["hookSpecificOutput"]["additionalContext"]
    assert "SEALED_DERIVED_RUNTIME_ONLY" in diagnostic
    assert "HIDDEN_ON_WINDOWS" in diagnostic

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(launcher, "_reexec_sealed_runtime", lambda *_: None)

    def fake_run_path(path: str, *, run_name: str) -> None:
        calls.append((Path(path).name, run_name))

    monkeypatch.setattr(launcher.runpy, "run_path", fake_run_path)
    assert launcher.main(
        ["--event", "PreCompact", "--handler", "lifecycle_boundary.py"]
    ) == 0
    assert calls == [("lifecycle_boundary.py", "__main__")]


def test_windows_hook_launcher_is_hidden_runtime_bound_and_noninteractive() -> None:
    source = (PLUGIN / "hooks" / "invoke_hook.ps1").read_text(encoding="utf-8")

    assert "[Console]::In.ReadToEnd()" in source
    assert "requirements_lock_sha256" in source
    assert "runtime_key" in source
    assert ".Substring(0, 32)" in source
    assert "venv\\Scripts\\python.exe" in source
    assert "SEALED_RUNTIME_INTERPRETER_NOT_FOUND" in source
    assert "SEALED_RUNTIME_INTERPRETER_AMBIGUOUS" in source
    assert "Start-Process" not in source
    assert "cmd.exe" not in source


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
        expected = {"continue", "systemMessage"}
        if not should_continue:
            expected.add("stopReason")
        assert set(output) == expected
        assert "hookSpecificOutput" not in output
        assert output["systemMessage"].startswith(
            "EVIDENCE_LANE_LIFECYCLE_BOUNDARY="
        )


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
    assert first["event_ordinal"] == 2
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
    supported = set(HOOK_EVENT_NAMES).difference({"SessionEnd"})
    receipt = hook_capability_receipt(supported)

    assert receipt["schema"] == HOOK_CAPABILITY_SCHEMA
    states = {row["event_name"]: row["state"] for row in receipt["events"]}
    assert states["SessionStart"] == "HOST_CAPABILITY_AVAILABLE"
    assert states["SessionEnd"] == "HOST_CAPABILITY_UNAVAILABLE"
    assert receipt["permission_request"] == {
        "state": "HOST_CAPABILITY_UNAVAILABLE",
        "registration_requires_explicit_contract_change": True,
    }
    assert receipt["subagent_events_in_scope"] is False
    assert receipt["unsupported_events_relabelled_as_success"] is False
    assert len(receipt["capability_receipt_sha256"]) == 64

    permission_available = hook_capability_receipt(
        supported,
        permission_request_supported=True,
    )
    assert permission_available["permission_request"]["state"] == (
        "HOST_CAPABILITY_AVAILABLE_NOT_REGISTERED"
    )
    with pytest.raises(HookContractError, match="UNKNOWN_HOST"):
        hook_capability_receipt([*supported, "SubagentStart"])
