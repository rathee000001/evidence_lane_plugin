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
