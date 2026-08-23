from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
from evidence_lane_plugin.installed_hook_receipts import (
    HOOK_FAILURE_FAILBACK_SCHEMA,
    HOOK_UI_PROJECTION_SCHEMA,
    INSTALLED_HOOK_INVENTORY_SCHEMA,
    INSTALLED_HOOK_INVOCATION_SCHEMA,
    InstalledHookReceiptError,
    build_host_hook_ui_projection_receipt,
    build_independent_hook_failback_request,
    build_installed_hook_diagnostic_receipt,
    build_installed_hook_invocation_receipt,
    build_invocation_receipt_from_codex_notifications,
    validate_installed_hook_inventory,
)

EVENTS = (
    ("SessionStart", "sessionStart"),
    ("SubagentStart", "subagentStart"),
    ("UserPromptSubmit", "userPromptSubmit"),
    ("PreToolUse", "preToolUse"),
    ("PermissionRequest", "permissionRequest"),
    ("PostToolUse", "postToolUse"),
    ("PreCompact", "preCompact"),
    ("PostCompact", "postCompact"),
    ("SubagentStop", "subagentStop"),
    ("Stop", "stop"),
    ("SessionEnd", "sessionEnd"),
)


def _probe_module() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "plugins"
        / "evidence-lane-plugin"
        / "scripts"
        / "codex_release"
        / "probe_installed_hooks.py"
    )
    spec = importlib.util.spec_from_file_location("installed_hook_probe_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reply(workspace: Path, selector: str) -> dict[str, object]:
    return {
        "result": {
            "data": [
                {
                    "cwd": str(workspace),
                    "warnings": [],
                    "errors": [],
                    "hooks": [
                        {
                            "key": f"{selector}:hooks/hooks.json:{host}:0:0",
                            "eventName": host,
                            "pluginId": selector,
                            "source": "plugin",
                            "isManaged": False,
                            "enabled": True,
                            "trustStatus": "trusted",
                            "currentHash": f"sha256:{index:064x}",
                            "handlerType": "command",
                            "sourcePath": str(workspace / "installed" / "hooks.json"),
                        }
                        for index, (_, host) in enumerate(EVENTS, start=1)
                    ],
                }
            ]
        }
    }


def test_installed_inventory_requires_one_warning_free_exact_selector(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-github"
    reply = _reply(tmp_path, selector)
    receipt = validate_installed_hook_inventory(
        reply,
        plugin_selector=selector,
        workspace=tmp_path,
    )

    assert receipt["schema"] == INSTALLED_HOOK_INVENTORY_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["hook_count"] == len(EVENTS) == 11
    assert [row["event_name"] for row in receipt["records"]] == [
        canonical for canonical, _ in EVENTS
    ]
    assert receipt["warnings"] == []
    assert receipt["multiple_evidence_lane_selectors"] is False
    assert receipt["installed_invocation_claimed"] is False
    assert len(receipt["inventory_receipt_sha256"]) == 64

    warning = _reply(tmp_path, selector)
    warning["result"]["data"][0]["warnings"] = [
        "clamping SessionEnd hook timeout to 3s"
    ]
    diagnostic = build_installed_hook_diagnostic_receipt(
        warning,
        plugin_selector=selector,
        workspace=tmp_path,
    )
    assert diagnostic["status"] == "HOST_LOAD_ISSUE"
    assert diagnostic["warning_count"] == 1
    assert diagnostic["warnings"][0]["message_after_redaction"] == (
        "clamping SessionEnd hook timeout to 3s"
    )
    assert diagnostic["raw_command_or_source_path_included"] is False
    assert diagnostic["raw_local_paths_in_diagnostic_text"] is False
    with pytest.raises(InstalledHookReceiptError, match="WARNINGS_PRESENT"):
        validate_installed_hook_inventory(
            warning,
            plugin_selector=selector,
            workspace=tmp_path,
        )

    duplicate = _reply(tmp_path, selector)
    duplicate["result"]["data"][0]["hooks"].append(
        {
            **duplicate["result"]["data"][0]["hooks"][0],
            "pluginId": "evidence-lane-plugin@evidence-lane-pv11-fallback",
        }
    )
    with pytest.raises(InstalledHookReceiptError, match="MULTIPLE_EVIDENCE"):
        validate_installed_hook_inventory(
            duplicate,
            plugin_selector=selector,
            workspace=tmp_path,
        )


def test_invocation_receipt_never_promotes_missing_host_events(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-github"
    inventory = validate_installed_hook_inventory(
        _reply(tmp_path, selector),
        plugin_selector=selector,
        workspace=tmp_path,
    )
    observations = [
        {
            "event_name": row["event_name"],
            "hook_key": row["hook_key"],
            "current_hash": row["current_hash"],
            "status": "COMPLETED",
            "host_started_event_id": f"start-{index}",
            "host_completed_event_id": f"complete-{index}",
            "host_session_id": "host-session",
        }
        for index, row in enumerate(inventory["records"], start=1)
    ]

    pending = build_installed_hook_invocation_receipt(
        inventory,
        observations[:-1],
    )
    assert pending["schema"] == INSTALLED_HOOK_INVOCATION_SCHEMA
    assert pending["status"] == "PENDING_INSTALLED_INVOCATION"
    assert pending["missing_events"] == ["SessionEnd"]
    assert pending["installed_invocation_proof_complete"] is False
    assert pending["unobserved_events_relabelled_unavailable"] is False

    complete = build_installed_hook_invocation_receipt(inventory, observations)
    assert complete["status"] == "PASS"
    assert complete["missing_events"] == []
    assert complete["observed_event_count"] == len(EVENTS)
    assert complete["installed_invocation_proof_complete"] is True
    assert len(complete["invocation_receipt_sha256"]) == 64

    poisoned = [dict(observations[0], prompt="secret text")]
    with pytest.raises(InstalledHookReceiptError, match="RAW_OR_PRIVATE"):
        build_installed_hook_invocation_receipt(inventory, poisoned)


def test_host_ui_projection_never_collapses_complete_installed_inventory(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-v300-testing-new"
    diagnostic = build_installed_hook_diagnostic_receipt(
        _reply(tmp_path, selector),
        plugin_selector=selector,
        workspace=tmp_path,
    )
    visible = [host for _, host in EVENTS if host != "sessionEnd"]
    receipt = build_host_hook_ui_projection_receipt(
        diagnostic,
        visible_host_events=visible,
        renderer_hook_title_mode="INDEX_ONLY_GENERIC",
        host_build="OpenAI.CodexBeta_26.727.4816.0",
        renderer_source_sha256="A" * 64,
    )

    assert receipt["schema"] == HOOK_UI_PROJECTION_SCHEMA
    assert receipt["status"] == "HOST_UI_PROJECTION_LIMITED"
    assert receipt["installed_hook_count"] == 11
    assert receipt["installed_hook_contract_complete"] is True
    assert receipt["missing_visible_host_events"] == ["sessionEnd"]
    assert receipt["renderer_uses_generic_index_titles"] is True
    assert (
        receipt["host_settings_projection_authoritative_for_plugin_inventory"]
        is False
    )
    assert (
        receipt["renderer_omission_relabelled_as_missing_plugin_hook"] is False
    )
    assert (
        receipt["plugin_repack_or_reinstall_expected_to_patch_signed_host_ui"]
        is False
    )
    assert receipt["host_update_required_for_full_ui_projection"] is True
    assert receipt["hook_enablement_mutated"] is False


def test_host_ui_projection_passes_only_with_all_events_and_strong_titles(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-github"
    diagnostic = build_installed_hook_diagnostic_receipt(
        _reply(tmp_path, selector),
        plugin_selector=selector,
        workspace=tmp_path,
    )
    receipt = build_host_hook_ui_projection_receipt(
        diagnostic,
        visible_host_events=[host for _, host in EVENTS],
        renderer_hook_title_mode="HOOK_KEY_OR_STATUS_AWARE",
        host_build="future-host-build",
        renderer_source_sha256="B" * 64,
    )

    assert receipt["status"] == "PASS"
    assert receipt["missing_visible_host_events"] == []
    assert receipt["host_update_required_for_full_ui_projection"] is False


def test_progressive_inventory_requires_only_named_enabled_hook(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-github"
    reply = _reply(tmp_path, selector)
    hooks = reply["result"]["data"][0]["hooks"]
    for hook in hooks:
        hook["enabled"] = hook["eventName"] == "preCompact"

    with pytest.raises(InstalledHookReceiptError, match="AUTHORITY_MISMATCH"):
        validate_installed_hook_inventory(
            reply,
            plugin_selector=selector,
            workspace=tmp_path,
        )

    inventory = validate_installed_hook_inventory(
        reply,
        plugin_selector=selector,
        workspace=tmp_path,
        required_events=("PreCompact",),
    )
    assert inventory["required_event_order"] == ["PreCompact"]
    assert inventory["progressive_subset"] is True
    assert [row["event_name"] for row in inventory["records"] if row["enabled"]] == [
        "PreCompact"
    ]

    reference = next(
        row for row in inventory["records"] if row["event_name"] == "PreCompact"
    )
    receipt = build_installed_hook_invocation_receipt(
        inventory,
        [
            {
                "event_name": "PreCompact",
                "hook_key": reference["hook_key"],
                "current_hash": reference["current_hash"],
                "status": "COMPLETED",
                "host_started_event_id": "precompact-started",
                "host_completed_event_id": "precompact-completed",
                "host_session_id": "progressive-host-session",
            }
        ],
    )
    assert receipt["status"] == "PASS"
    assert receipt["event_order"] == ["PreCompact"]
    assert receipt["missing_events"] == []
    assert receipt["observed_event_count"] == 1


def test_real_codex_notifications_are_correlated_without_raw_payloads(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-github"
    inventory = validate_installed_hook_inventory(
        _reply(tmp_path, selector),
        plugin_selector=selector,
        workspace=tmp_path,
    )
    source_path = str(tmp_path / "installed" / "hooks.json")
    notifications: list[dict[str, object]] = []
    for index, (_, host_event) in enumerate(EVENTS, start=1):
        run_id = f"hook-run-{index}"
        common = {
            "id": run_id,
            "eventName": host_event,
            "executionMode": "sync",
            "handlerType": "command",
            "scope": "plugin",
            "source": "plugin",
            "sourcePath": source_path,
            "startedAt": index * 100,
            "displayOrder": index,
            "entries": [{"kind": "stdout", "text": "api_key=DO_NOT_COPY"}],
        }
        notifications.extend(
            [
                {
                    "method": "hook/started",
                    "params": {
                        "threadId": "thread-secret-id",
                        "turnId": "turn-secret-id",
                        "run": {
                            **common,
                            "status": "running",
                            "completedAt": None,
                            "durationMs": None,
                        },
                    },
                },
                {
                    "method": "hook/completed",
                    "params": {
                        "threadId": "thread-secret-id",
                        "turnId": "turn-secret-id",
                        "run": {
                            **common,
                            "status": "completed",
                            "completedAt": index * 100 + 7,
                            "durationMs": 7,
                        },
                    },
                },
            ]
        )

    receipt = build_invocation_receipt_from_codex_notifications(
        inventory,
        notifications,
        host_session_id="host-session-secret-id",
    )
    assert receipt["status"] == "PASS"
    assert receipt["source"] == "CODEX_APP_SERVER_HOOK_NOTIFICATIONS"
    assert receipt["observed_event_count"] == len(EVENTS)
    assert receipt["notification_pair_count"] == len(EVENTS)
    assert receipt["raw_source_paths_included"] is False
    assert receipt["raw_thread_or_turn_ids_included"] is False
    assert receipt["raw_hook_output_included"] is False
    serialized = str(receipt)
    assert "thread-secret-id" not in serialized
    assert "turn-secret-id" not in serialized
    assert "host-session-secret-id" not in serialized
    assert "DO_NOT_COPY" not in serialized
    assert all(row["duration_ms"] == 7 for row in receipt["observations"])

    tampered = list(notifications)
    tampered[0] = {
        **tampered[0],
        "params": {
            **tampered[0]["params"],
            "run": {
                **tampered[0]["params"]["run"],
                "sourcePath": str(tmp_path / "different" / "hooks.json"),
            },
        },
    }
    with pytest.raises(InstalledHookReceiptError, match="IDENTITY_MISMATCH"):
        build_invocation_receipt_from_codex_notifications(
            inventory,
            tampered,
            host_session_id="host-session-secret-id",
        )


def test_failed_hook_failback_targets_only_one_native_hook_state(
    tmp_path: Path,
) -> None:
    selector = "evidence-lane-plugin@evidence-lane-github"
    inventory = validate_installed_hook_inventory(
        _reply(tmp_path, selector),
        plugin_selector=selector,
        workspace=tmp_path,
    )
    receipt = build_independent_hook_failback_request(
        inventory,
        event_name="PermissionRequest",
        failure_code="HOOK_EVENT_HANDLER_TIMEOUT",
    )

    assert receipt["schema"] == HOOK_FAILURE_FAILBACK_SCHEMA
    assert receipt["status"] == "PASS"
    assert receipt["supported_codex_api"] == "config/batchWrite"
    assert receipt["compare_and_swap_required"] is True
    assert receipt["post_write_hooks_list_readback_required"] is True
    state = receipt["config_edit"]["value"]
    target = next(
        row for row in inventory["records"] if row["event_name"] == "PermissionRequest"
    )
    assert state[target["hook_key"]]["enabled"] is False
    assert all(
        row["hook_key"] == target["hook_key"]
        or state[row["hook_key"]]["enabled"] is row["enabled"]
        for row in inventory["records"]
    )
    assert receipt["unrelated_hook_state_mutated"] is False
    assert receipt["execution_claimed"] is False


def test_read_only_probe_uses_no_window_and_never_writes_host_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module = _probe_module()
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fixture")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class Stream:
        def __init__(self, lines: list[str] | None = None) -> None:
            self.lines = list(lines or [])
            self.writes: list[str] = []

        def __iter__(self):
            return iter(self.lines)

        def write(self, value: str) -> int:
            self.writes.append(value)
            return len(value)

        def flush(self) -> None:
            return None

    class Process:
        def __init__(self) -> None:
            self.stdin = Stream()
            self.stdout = Stream()
            self.stderr = Stream()
            self.returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = 1

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return int(self.returncode or 0)

    captured: dict[str, object] = {}
    process = Process()

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        module,
        "threading",
        type("Threads", (), {"Thread": lambda *args, **kwargs: type(
            "Thread", (), {"start": lambda self: None}
        )()}),
    )

    with pytest.raises(RuntimeError, match="RESPONSE_TIMEOUT"):
        module.query_hooks_list(
            executable=executable.resolve(),
            codex_home=codex_home.resolve(),
            workspace=workspace.resolve(),
            timeout_seconds=0.01,
        )
    kwargs = captured["kwargs"]
    assert kwargs["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    writes = "".join(process.stdin.writes)
    assert "initialize" in writes
    assert "config/batchWrite" not in writes
    assert "plugin" not in writes.casefold()
