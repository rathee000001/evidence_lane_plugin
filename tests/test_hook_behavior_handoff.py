from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "plugins" / "evidence-lane-plugin" / "hooks"
ADAPTERS = (
    "session_start.py",
    "optional_event_observer.py",
    "prompt_submit.py",
    "pre_tool_use.py",
    "post_tool_use.py",
    "lifecycle_boundary.py",
    "stop_response.py",
)


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _handoff_module() -> ModuleType:
    return _load(HOOKS / "behavior_handoff.py", "behavior_handoff_test")


def _adapter(name: str) -> ModuleType:
    return _load(HOOKS / name, f"behavior_handoff_{Path(name).stem}_test")


def _transport(event_name: str) -> dict[str, object]:
    return {
        "schema": "evidence-lane.codex-hook-transport.v1",
        "event_name": event_name,
        "transport_receipt_sha256": "A" * 64,
        "hook_behavior_executed": False,
    }


def _skill_receipt(
    module: ModuleType,
    event_name: str,
    transport: dict[str, object],
) -> dict[str, object]:
    return {
        "state": "BOUNDED_SKILL_RESULT",
        "hook_transport_envelope": dict(transport),
        "hook_runtime_role": module.HOOK_RUNTIME_ROLE,
        "skill_action_owner": module.SKILL_RUNTIME_OWNER,
        "skill_action": module.EVENT_SKILL_ACTIONS[event_name],
        "skill_action_executed": True,
        "hook_behavior_executed": False,
    }


def _skill_consumer(event_name: str):
    from evidence_lane_plugin import hook_skill_runtime

    names = {
        "SessionStart": "consume_session_start_transport",
        "SubagentStart": "consume_optional_observer_transport",
        "UserPromptSubmit": "consume_prompt_transport",
        "PreToolUse": "consume_pre_tool_transport",
        "PermissionRequest": "consume_optional_observer_transport",
        "PostToolUse": "consume_post_tool_transport",
        "PreCompact": "consume_boundary_transport",
        "PostCompact": "consume_boundary_transport",
        "SubagentStop": "consume_optional_observer_transport",
        "Stop": "consume_stop_transport",
    }
    return getattr(hook_skill_runtime, names[event_name])


def test_exact_skill_action_map_excludes_best_effort_session_end() -> None:
    module = _handoff_module()

    assert module.EVENT_SKILL_ACTIONS == {
        "SessionStart": "SESSION_START_BIND_OR_REENTRY",
        "SubagentStart": "BOUND_OPTIONAL_EVENT_OBSERVATION",
        "UserPromptSubmit": "PREPARE",
        "PreToolUse": "PROSPECTIVE_TOOL_BOUNDARY",
        "PermissionRequest": "BOUND_OPTIONAL_EVENT_OBSERVATION",
        "PostToolUse": "TOOL_RECEIPT_AND_CURRENT_CHANGE_PROJECTION",
        "PreCompact": "COMPACTION_OR_SESSION_BOUNDARY",
        "PostCompact": "COMPACTION_OR_SESSION_BOUNDARY",
        "SubagentStop": "BOUND_OPTIONAL_EVENT_OBSERVATION",
        "Stop": "COMMIT",
    }
    assert "SessionEnd" not in module.EVENT_SKILL_ACTIONS


def test_behavior_handoff_is_issued_consumed_and_content_addressed() -> None:
    module = _handoff_module()
    transport = _transport("UserPromptSubmit")
    skill_receipt = _skill_receipt(
        module,
        "UserPromptSubmit",
        transport,
    )

    issued = module.issue_behavior_handoff_receipt(
        "UserPromptSubmit",
        transport,
        skill_receipt,
        skill_consumer=_skill_consumer("UserPromptSubmit"),
    )
    consumed = module.consume_behavior_handoff_receipt(
        issued,
        event_name="UserPromptSubmit",
        transport=transport,
        skill_receipt=skill_receipt,
        skill_consumer=_skill_consumer("UserPromptSubmit"),
    )
    attached = module.attach_consumed_behavior_handoff(
        "UserPromptSubmit",
        transport,
        skill_receipt,
        skill_consumer=_skill_consumer("UserPromptSubmit"),
    )

    assert issued["status"] == "ISSUED_AFTER_SKILL_ACTION"
    assert consumed["status"] == "PASS_CONSUMED"
    assert consumed["issued_receipt_sha256"] == issued["issued_receipt_sha256"]
    assert len(consumed["consumed_receipt_sha256"]) == 64
    assert consumed["skill_action_owner"] == module.SKILL_RUNTIME_OWNER
    assert consumed["skill_action"] == "PREPARE"
    assert consumed["skill_action_executed"] is True
    assert consumed["hook_behavior_executed"] is False
    assert consumed["behavior_success_inferred"] is False
    assert consumed["raw_host_payload_stored"] is False
    assert attached["behavior_handoff_receipt"] == consumed


@pytest.mark.parametrize(
    "event_name",
    (
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
    ),
)
def test_each_behavior_capable_event_binds_its_exact_skill_action(
    event_name: str,
) -> None:
    module = _handoff_module()
    transport = _transport(event_name)
    skill_receipt = _skill_receipt(module, event_name, transport)

    attached = module.attach_consumed_behavior_handoff(
        event_name,
        transport,
        skill_receipt,
        skill_consumer=_skill_consumer(event_name),
    )

    handoff = attached["behavior_handoff_receipt"]
    assert handoff["status"] == "PASS_CONSUMED"
    assert handoff["event_name"] == event_name
    assert handoff["skill_action"] == module.EVENT_SKILL_ACTIONS[event_name]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    (
        ("skill_action_owner", "HOOK_ADAPTER"),
        ("skill_action", "COMMIT"),
        ("skill_action_executed", False),
        ("hook_behavior_executed", True),
        ("hook_runtime_role", "BEHAVIOR_OWNER"),
    ),
)
def test_behavior_handoff_fails_closed_on_skill_receipt_mismatch(
    field: str,
    bad_value: object,
) -> None:
    module = _handoff_module()
    transport = _transport("UserPromptSubmit")
    skill_receipt = _skill_receipt(
        module,
        "UserPromptSubmit",
        transport,
    )
    skill_receipt[field] = bad_value

    with pytest.raises(
        module.BehaviorHandoffError,
        match="BEHAVIOR_HANDOFF_SKILL_RECEIPT_INVALID",
    ):
        module.issue_behavior_handoff_receipt(
            "UserPromptSubmit",
            transport,
            skill_receipt,
            skill_consumer=_skill_consumer("UserPromptSubmit"),
        )


def test_behavior_handoff_fails_closed_on_transport_or_issue_tamper() -> None:
    module = _handoff_module()
    transport = _transport("UserPromptSubmit")
    skill_receipt = _skill_receipt(
        module,
        "UserPromptSubmit",
        transport,
    )
    mismatched_transport = dict(transport)
    mismatched_transport["transport_receipt_sha256"] = "B" * 64
    with pytest.raises(
        module.BehaviorHandoffError,
        match="BEHAVIOR_HANDOFF_TRANSPORT_MISMATCH",
    ):
        module.issue_behavior_handoff_receipt(
            "UserPromptSubmit",
            mismatched_transport,
            skill_receipt,
            skill_consumer=_skill_consumer("UserPromptSubmit"),
        )

    issued = module.issue_behavior_handoff_receipt(
        "UserPromptSubmit",
        transport,
        skill_receipt,
        skill_consumer=_skill_consumer("UserPromptSubmit"),
    )
    issued["skill_result_state"] = "TAMPERED"
    with pytest.raises(
        module.BehaviorHandoffError,
        match="BEHAVIOR_HANDOFF_ISSUED_RECEIPT_MISMATCH",
    ):
        module.consume_behavior_handoff_receipt(
            issued,
            event_name="UserPromptSubmit",
            transport=transport,
            skill_receipt=skill_receipt,
            skill_consumer=_skill_consumer("UserPromptSubmit"),
        )


def test_behavior_handoff_rejects_a_consumer_outside_the_installed_skill() -> None:
    module = _handoff_module()
    transport = _transport("UserPromptSubmit")
    skill_receipt = _skill_receipt(
        module,
        "UserPromptSubmit",
        transport,
    )

    with pytest.raises(
        module.BehaviorHandoffError,
        match="BEHAVIOR_HANDOFF_SKILL_CONSUMER_INVALID",
    ):
        module.issue_behavior_handoff_receipt(
            "UserPromptSubmit",
            transport,
            skill_receipt,
            skill_consumer=lambda: None,
        )


def test_behavior_handoff_binds_complete_event_isolation_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _handoff_module()
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_EVENT_CORRELATION_ID",
        "hook_" + "a" * 40,
    )
    monkeypatch.setenv("EVIDENCE_LANE_HOOK_EVENT_OWNER_SHA256", "B" * 64)
    monkeypatch.setenv("EVIDENCE_LANE_HOOK_EVENT_INPUT_SHA256", "C" * 64)
    monkeypatch.setenv("EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256", "D" * 64)
    transport = _transport("Stop")
    skill_receipt = _skill_receipt(module, "Stop", transport)

    attached = module.attach_consumed_behavior_handoff(
        "Stop",
        transport,
        skill_receipt,
        skill_consumer=_skill_consumer("Stop"),
    )

    binding = attached["behavior_handoff_receipt"]["event_isolation_binding"]
    assert binding == {
        "state": "EXACT_EVENT_ISOLATION_BOUND",
        "event_isolation_bound": True,
        "correlation_id": "hook_" + "a" * 40,
        "owner_sha256": "B" * 64,
        "occurrence_input_sha256": "C" * 64,
        "policy_sha256": "D" * 64,
    }


def test_behavior_handoff_rejects_partial_event_isolation_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _handoff_module()
    monkeypatch.setenv(
        "EVIDENCE_LANE_HOOK_EVENT_CORRELATION_ID",
        "hook_" + "a" * 40,
    )
    transport = _transport("Stop")
    skill_receipt = _skill_receipt(module, "Stop", transport)

    with pytest.raises(
        module.BehaviorHandoffError,
        match="BEHAVIOR_HANDOFF_ISOLATION_BINDING_PARTIAL",
    ):
        module.attach_consumed_behavior_handoff(
            "Stop",
            transport,
            skill_receipt,
            skill_consumer=_skill_consumer("Stop"),
        )


def test_session_end_cannot_claim_a_durable_skill_behavior_handoff() -> None:
    module = _handoff_module()
    transport = _transport("SessionEnd")
    skill_receipt = {
        "state": "BEST_EFFORT_TRANSPORT_SEALED",
        "hook_transport_envelope": dict(transport),
    }

    with pytest.raises(
        module.BehaviorHandoffError,
        match="BEHAVIOR_HANDOFF_EVENT_UNSUPPORTED",
    ):
        module.issue_behavior_handoff_receipt(
            "SessionEnd",
            transport,
            skill_receipt,
            skill_consumer=_skill_consumer("Stop"),
        )


def test_all_behavior_capable_adapters_complete_the_common_handoff() -> None:
    for name in ADAPTERS:
        source = (HOOKS / name).read_text(encoding="utf-8")
        assert "attach_consumed_behavior_handoff" in source
        assert "_load_behavior_handoff()" in source

    boundary_source = (HOOKS / "lifecycle_boundary.py").read_text(
        encoding="utf-8"
    )
    session_end_branch = boundary_source.split(
        'if event_name == "SessionEnd":',
        maxsplit=1,
    )[1].split("build_transport, consume_transport", maxsplit=1)[0]
    assert "durable_skill_consumer_invoked\": False" in session_end_branch
    assert "_load_behavior_handoff" not in session_end_branch


def test_prompt_adapter_attaches_handoff_after_consumer_returns(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = _adapter("prompt_submit.py")
    monkeypatch.setenv("EVIDENCE_LANE_DATA_ROOT", str(tmp_path))
    for name in (
        "EVIDENCE_LANE_HOOK_EVENT_CORRELATION_ID",
        "EVIDENCE_LANE_HOOK_EVENT_OWNER_SHA256",
        "EVIDENCE_LANE_HOOK_EVENT_INPUT_SHA256",
        "EVIDENCE_LANE_HOOK_ISOLATION_POLICY_SHA256",
    ):
        monkeypatch.delenv(name, raising=False)
    result, should_continue = adapter._record(
        {
            "session_id": "host-session-handoff-test",
            "turn_id": "turn-handoff-test",
            "cwd": str(ROOT),
            "prompt": "Verify the behavior handoff.",
        }
    )

    assert should_continue is True
    assert result["state"] == "NOT_INDEXED"
    assert result["behavior_handoff_receipt"]["status"] == "PASS_CONSUMED"
    assert result["behavior_handoff_receipt"]["skill_action"] == "PREPARE"


def test_stop_handoff_failure_is_output_inert_but_process_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    adapter = _adapter("stop_response.py")
    monkeypatch.setattr(
        adapter,
        "_record",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("handoff failed")),
    )
    monkeypatch.setattr(adapter.sys, "stdin", io.StringIO("{}"))

    assert adapter.main() == 1
    assert json.loads(capsys.readouterr().out) == {}
