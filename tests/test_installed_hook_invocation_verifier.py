from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "plugins"
    / "evidence-lane-plugin"
    / "scripts"
    / "codex_release"
    / "verify_installed_hook_invocations.py"
)


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "evidence_lane_installed_hook_verifier_test",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_is_loopback_only_hidden_and_read_only() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"modelProvider": "row174-loopback"' in source
    assert '"base_url": mock.base_url' in source
    assert '"wire_api": "responses"' in source
    assert 'environment["NO_PROXY"] = "127.0.0.1,localhost"' in source
    assert 'environment[key] = "http://127.0.0.1:9"' in source
    assert 'getattr(subprocess, "CREATE_NO_WINDOW", 0)' in source
    assert '"sandbox": "read-only"' in source
    assert '"networkAccess": False' in source
    assert '"command": "Get-Location"' in source
    assert "OPENAI_BASE_URL" not in source


def test_probe_scripted_responses_have_one_safe_tool_and_compaction() -> None:
    verifier = _module()
    responses = verifier._scripted_responses()

    assert len(responses) == 3
    events = [
        json.loads(line.removeprefix("data: "))
        for response in responses
        for line in response.decode("utf-8").splitlines()
        if line.startswith("data: ")
    ]
    function_call = next(
        event["item"]
        for event in events
        if event.get("type") == "response.output_item.done"
        and event.get("item", {}).get("type") == "function_call"
    )
    assert function_call["name"] == "shell_command"
    assert json.loads(function_call["arguments"]) == {"command": "Get-Location"}
    compaction = next(
        event["item"]
        for event in events
        if event.get("type") == "response.output_item.done"
        and event.get("item", {}).get("type") == "compaction"
    )
    assert compaction["encrypted_content"] == "ROW174_ISOLATED_COMPACTION_SUMMARY"


def test_parser_supports_one_progressive_live_event() -> None:
    verifier = _module()
    parsed = verifier._parser().parse_args(
        [
            "--codex-executable",
            "codex.exe",
            "--codex-home",
            "codex-home",
            "--data-root",
            "data-root",
            "--workspace",
            "workspace",
            "--plugin-selector",
            "evidence-lane-plugin@testing",
            "--event",
            "PreCompact",
            "--live-codex-home",
        ]
    )
    assert parsed.event == ["PreCompact"]
    assert parsed.live_codex_home is True


def test_parser_supports_complete_progressive_matrix() -> None:
    verifier = _module()
    parsed = verifier._parser().parse_args(
        [
            "--codex-executable",
            "codex.exe",
            "--codex-home",
            "codex-home",
            "--data-root",
            "data-root",
            "--workspace",
            "workspace",
            "--plugin-selector",
            "evidence-lane-plugin@testing",
            "--progressive-all",
            "--live-codex-home",
        ]
    )
    assert parsed.progressive_all is True
    assert parsed.live_codex_home is True


def test_complete_progressive_matrix_keeps_passes_on_and_isolates_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    verifier = _module()
    events = tuple(verifier._CANONICAL_TO_HOST)

    def fake_probe(**kwargs):
        event = kwargs["event_name"]
        failed = event == "PermissionRequest"
        return {
            "status": "FAIL_CLOSED" if failed else "PASS",
            "disabled_only_failing_hook": failed,
            "receipt_sha256": f"probe-{event}",
        }

    def fake_state(**kwargs):
        assert kwargs["event_name"] == events[-1]
        return {
            "receipt_sha256": "final-state",
            "enabled_hook_count_after": len(events) - 1,
            "all_hooks_enabled_after": False,
            "disabled_events_after": ["permissionRequest"],
        }

    monkeypatch.setattr(verifier, "_progressive_probe", fake_probe)
    monkeypatch.setattr(verifier, "_set_progressive_hook_state", fake_state)
    receipt = verifier._progressive_matrix(
        executable=tmp_path / "codex.exe",
        codex_home=tmp_path / "codex-home",
        data_root=tmp_path / "data-root",
        workspace=tmp_path / "workspace",
        plugin_selector="evidence-lane-plugin@testing",
    )

    assert receipt["status"] == "FAIL_CLOSED"
    assert receipt["failed_events"] == ["PermissionRequest"]
    assert receipt["passing_events_kept_enabled"] is True
    assert receipt["failed_events_disabled_independently"] is True
    assert receipt["all_hooks_enabled_after"] is False
    assert receipt["next_action"] == "REPAIR_ONLY_FAILED_EVENTS_THEN_RERUN_MATRIX"
    assert receipt["goal_pause_requested"] is False
