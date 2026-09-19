from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.service import Service, request_owner_control
from evidence_lane_plugin.shortcuts import StudioShortcut, shell_link
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.studio_window import (
    StudioWindow,
    close_dedicated_browser_processes,
    dedicated_browser_processes,
    installed_browser,
)


def test_window_uses_visible_app_frame_and_private_profile_without_debug_port(tmp_path):
    browser = tmp_path / "chrome.exe"
    browser.touch()
    calls = []

    def spawn(arguments, **options):
        calls.append((arguments, options))
        return SimpleNamespace(poll=lambda: None)

    window = StudioWindow(
        tmp_path / "runtime",
        browser=browser,
        spawn=spawn,
        existing=lambda _profile: (),
    )
    url = "http://127.0.0.1:12345/studio/#ticket=" + "x" * 64
    assert window(url)
    command, options = calls[0]
    assert "--app=" + url in command
    assert "--user-data-dir=" + str(tmp_path / "runtime/studio-browser") in command
    assert not any("remote-debugging" in arg or "disable-web-security" in arg or "headless" in arg for arg in command)
    assert "shell" not in options
    assert options["stdout"] == subprocess.DEVNULL
    assert window.last_launch["close_stops_engine"] is False
    assert window.last_launch["minimize_stops_engine"] is False
    assert window.last_launch["visible_window_verified"] is False
    for invalid in ("https://remote.invalid/studio/#ticket=" + "x" * 64,
                    "http://127.0.0.1:12345/studio/?redirect=remote#ticket=" + "x" * 64,
                    "http://127.0.0.1:12345/v4/control#ticket=" + "x" * 64):
        with pytest.raises(LaneError):
            window(invalid)
    assert len(calls) == 1


def test_existing_private_profile_window_is_restored_without_spawning(tmp_path):
    browser = tmp_path / "chrome.exe"
    browser.touch()
    existing = SimpleNamespace(pid=42)
    spawned = []
    restored = []
    window = StudioWindow(
        tmp_path / "runtime",
        browser=browser,
        spawn=lambda *args, **kwargs: spawned.append((args, kwargs)),
        existing=lambda profile: [existing]
        if profile == tmp_path / "runtime/studio-browser"
        else [],
        restore=lambda processes: restored.append(processes) or True,
        authenticated_session=lambda: True,
    )
    url = "http://127.0.0.1:12345/studio/#ticket=" + "x" * 64
    assert window(url)
    assert spawned == []
    assert restored == [(existing,)]
    assert window.last_launch["mode"] == "existing_dedicated_browser_window"
    assert window.last_launch["visible_window_verified"] is True
    assert window.last_launch["existing_profile_process_count"] == 1


def test_owner_reopen_recovers_expired_session_without_extending_or_mutating_project(tmp_path):
    service = Service(tmp_path / "runtime", workers=1)
    window = service.studio_launcher
    browser = tmp_path / "chrome.exe"
    browser.touch()
    window.browser = browser
    processes = []
    launches = []
    closed = []

    def spawn(arguments, **_options):
        launches.append(arguments)
        processes.append(SimpleNamespace(pid=99))
        return SimpleNamespace(poll=lambda: None)

    def close(profile):
        assert profile == tmp_path / "runtime/studio-browser"
        closed.append(profile)
        processes.clear()

    window.spawn = spawn
    window.existing = lambda _profile: tuple(processes)
    window.restore = lambda _processes: True
    window.close_existing = close
    current = [datetime.now(UTC)]
    try:
        service.start()
        gateway = service.endpoint.studio
        gateway.clock = lambda: current[0]
        engine_instance = service.engine.instance_id
        service.open_studio()
        ticket = next(arg for arg in launches[-1] if arg.startswith("--app=")).split("#ticket=", 1)[1]
        token, session = gateway.exchange(ticket)
        assert gateway.has_live_session()
        assert session.expires_at == current[0] + timedelta(hours=12)
        service.open_studio()
        assert len(launches) == 1 and closed == []
        assert gateway.authenticate(gateway.cookie(token)).expires_at == session.expires_at

        current[0] += timedelta(hours=13)
        with pytest.raises(LaneError) as expired:
            gateway.authenticate(gateway.cookie(token))
        assert expired.value.code == "STUDIO_AUTHENTICATION_REQUIRED"
        assert not gateway.has_live_session()
        service.open_studio()
        assert len(launches) == 2 and len(closed) == 1 and len(processes) == 1
        assert window.last_launch["mode"] == "reconnected_dedicated_browser_window"
        ticket = next(arg for arg in launches[-1] if arg.startswith("--app=")).split("#ticket=", 1)[1]
        new_token, new_session = gateway.exchange(ticket)
        assert new_session.actor_id != session.actor_id
        assert gateway.authenticate(gateway.cookie(new_token), csrf=new_session.csrf) == new_session
        assert service.engine.instance_id == engine_instance
        assert service.engine.directory.entries() == {}
        assert not service.stop_requested.is_set()
    finally:
        service.close()


def test_new_engine_reopens_existing_profile_with_new_engine_ticket(tmp_path):
    old_gateway = StudioGateway(Engine(tmp_path / "old"))
    old_token, _ = old_gateway.exchange(old_gateway.issue_ticket())
    new_gateway = StudioGateway(Engine(tmp_path / "new"))
    browser = tmp_path / "chrome.exe"
    browser.touch()
    existing = [SimpleNamespace(pid=99)]
    launches = []
    window = StudioWindow(
        tmp_path / "runtime", browser=browser,
        existing=lambda _profile: tuple(existing),
        authenticated_session=new_gateway.has_live_session,
        close_existing=lambda _profile: existing.clear(),
        restore=lambda _processes: pytest.fail("An old-engine window cannot authenticate to the new engine"),
        spawn=lambda arguments, **_options: launches.append(arguments) or SimpleNamespace(poll=lambda: None),
    )
    ticket = new_gateway.issue_ticket()
    assert window("http://127.0.0.1:12345/studio/#ticket=" + ticket)
    assert len(launches) == 1 and existing == []
    with pytest.raises(LaneError):
        new_gateway.authenticate(old_gateway.cookie(old_token))
    token, session = new_gateway.exchange(ticket)
    assert new_gateway.authenticate(new_gateway.cookie(token)) == session


@pytest.mark.parametrize("close_fails", [True, False])
def test_reconnect_never_spawns_until_exact_profile_closure_is_confirmed(tmp_path, close_fails):
    browser = tmp_path / "chrome.exe"
    browser.touch()
    spawned = []

    def close(_profile):
        if close_fails:
            raise LaneError("STUDIO_WINDOW_CLOSE_FAILED", "Owned browser did not close.")

    window = StudioWindow(
        tmp_path / "runtime", browser=browser,
        existing=lambda _profile: (SimpleNamespace(pid=99),),
        close_existing=close,
        spawn=lambda *args, **kwargs: spawned.append((args, kwargs)),
    )
    with pytest.raises(LaneError) as failure:
        window("http://127.0.0.1:12345/studio/#ticket=" + "x" * 64)
    assert failure.value.code == "STUDIO_WINDOW_CLOSE_FAILED"
    assert spawned == []


def test_private_profile_process_selection_and_close_never_touch_other_browser(tmp_path):
    profile = tmp_path / "runtime/studio-browser"

    class Process:
        def __init__(self, pid, name, selected_profile):
            self.pid = pid
            self.info = {
                "name": name,
                "exe": str(tmp_path / name),
                "cmdline": [str(tmp_path / name), "--user-data-dir=" + str(selected_profile)],
            }
            self.terminated = False

        def terminate(self):
            self.terminated = True

        def kill(self):
            raise AssertionError("A graceful exact-profile close should finish before kill")

    owned = Process(41, "msedge.exe", profile)
    unrelated = Process(42, "msedge.exe", tmp_path / "normal-browser")
    wrong_program = Process(43, "notepad.exe", profile)

    def processes(_attributes):
        return [owned, unrelated, wrong_program]

    assert dedicated_browser_processes(profile, process_iter=processes) == (owned,)
    result = close_dedicated_browser_processes(
        profile,
        process_iter=processes,
        waiter=lambda selected, timeout: (selected, []),
    )
    assert result == {"matched": 1, "closed": 1}
    assert owned.terminated is True
    assert unrelated.terminated is False
    assert wrong_program.terminated is False


def test_reopening_uses_existing_browser_session_without_extending_expiry(tmp_path):
    gateway = StudioGateway(Engine(tmp_path))
    token, first = gateway.exchange(gateway.issue_ticket())
    for _ in range(24):
        next_token, session = gateway.exchange(gateway.issue_ticket(), cookie=gateway.cookie(token))
        assert next_token is None and session == first
    assert len(gateway._sessions) == 1
    gateway.logout(first)
    new_token, second = gateway.exchange(gateway.issue_ticket(), cookie=gateway.cookie(token))
    assert new_token and second.actor_id != first.actor_id


def test_shortcut_timeout_remains_bounded_and_redacts_captured_output(tmp_path, monkeypatch):
    def timeout(command, **options):
        assert options["timeout"] == 30
        raise subprocess.TimeoutExpired(command, 30, output="private captured output", stderr="private captured error")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(LaneError) as failure:
        shell_link(tmp_path / "temporary.lnk", specification={"target": str(Path(sys.executable).with_name("pythonw.exe"))})
    assert failure.value.code == "SHORTCUT_OPERATION_FAILED"
    assert failure.value.details == {"reason": "deadline_exceeded", "timeout_seconds": 30}
    assert "private captured" not in str(failure.value.public())


def test_shortcut_request_uses_child_environment_and_preserves_literal_data(tmp_path, monkeypatch):
    expected = {"target": str(Path(sys.executable).with_name("pythonw.exe")),
                "arguments": '"literal & $value"', "working_directory": "C:/litéral & $value/工具",
                "window_style": 1, "icon_location": "C:/literal & $value/icon.ico,0"}
    monkeypatch.setenv("EVIDENCE_LANE_SHORTCUT_REQUEST", "parent value stays")
    monkeypatch.setenv("PSModulePath", "parent PowerShell 7 modules stay")

    def run(command, **options):
        assert options["stdin"] == subprocess.DEVNULL and "input" not in options
        assert not any(key.casefold() == "psmodulepath" for key in options["env"])
        assert "EVIDENCE_LANE_SHORTCUT_REQUEST" not in options["env"]
        request = {
            key.removeprefix("EVIDENCE_LANE_SHORTCUT_").removesuffix("_B64").lower():
                base64.b64decode(value, validate=True).decode("utf-8")
            for key, value in options["env"].items()
            if key.startswith("EVIDENCE_LANE_SHORTCUT_")
        }
        request["window_style"] = int(request["window_style"])
        assert request == {"path": str(tmp_path / "literal & $value.lnk"), "mode": "write", **expected}
        script = base64.b64decode(command[-1], validate=True).decode("utf-16-le")
        assert command[-2] == "-EncodedCommand"
        assert "Console]::In.ReadToEnd" not in script and "Console]::OutputEncoding" not in script
        assert "ConvertFrom-Json" not in script and "ConvertTo-Json" not in script
        assert "litéral" not in script and "工具" not in script
        readback = json.dumps({key: value if key == "window_style" else base64.b64encode(
            str(value).encode("utf-8")).decode("ascii") for key, value in expected.items()})
        return SimpleNamespace(returncode=0, stdout=readback, stderr="EL_SHORTCUT_PHASE=readback_ready")

    monkeypatch.setattr(subprocess, "run", run)
    assert shell_link(tmp_path / "literal & $value.lnk", specification=expected) == expected
    assert os.environ["EVIDENCE_LANE_SHORTCUT_REQUEST"] == "parent value stays"
    assert os.environ["PSModulePath"] == "parent PowerShell 7 modules stay"


def test_shortcut_timeout_exposes_only_whitelisted_phase(tmp_path, monkeypatch):
    def timeout(command, **options):
        raise subprocess.TimeoutExpired(command, 30, output=b"private captured output",
            stderr=b"private captured error\nEL_SHORTCUT_PHASE=request_loaded\nEL_SHORTCUT_PHASE=private captured phase\n")

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(LaneError) as failure:
        shell_link(tmp_path / "temporary.lnk", specification={"target": str(Path(sys.executable).with_name("pythonw.exe"))})
    assert failure.value.details == {"reason": "deadline_exceeded", "timeout_seconds": 30, "phase": "request_loaded"}
    assert "private captured" not in str(failure.value.public())


@pytest.mark.parametrize("output", ["not valid base64!", base64.b64encode(b'{}').decode(), base64.b64encode(b'x' * 16_385).decode()])
def test_shortcut_rejects_invalid_or_oversized_readback(tmp_path, monkeypatch, output):
    monkeypatch.setattr(subprocess, "run", lambda *args, **options:
        SimpleNamespace(returncode=0, stdout=output, stderr="EL_SHORTCUT_PHASE=readback_ready"))
    with pytest.raises(LaneError) as failure:
        shell_link(tmp_path / "temporary.lnk")
    assert failure.value.details == {"reason": "invalid_readback", "phase": "readback_ready"}


def test_real_windows_shortcut_create_readback_and_collision_preservation(tmp_path_factory):
    root = tmp_path_factory.mktemp("link")
    interpreter = Path(sys.executable).with_name("pythonw.exe")

    def diagnosed_shell_link(path, **options):
        try:
            return shell_link(path, **options)
        except LaneError as error:
            pytest.fail(json.dumps(error.public(), sort_keys=True), pytrace=False)

    shortcut = StudioShortcut(interpreter, root / "state & $literal", root / "temporary menu", backend=diagnosed_shell_link)
    result = shortcut.install()
    assert result["registered"]
    assert diagnosed_shell_link(shortcut.path) == shortcut.specification()
    assert not shortcut.install()["changed"]
    assert not shortcut.uninstall()["registered"]
    assert not shortcut.path.exists()
    shortcut.path.write_bytes(b"unrelated user shortcut")
    before = shortcut.path.read_bytes()
    with pytest.raises(LaneError) as error:
        shortcut.install()
    assert error.value.code == "SHORTCUT_CHANGED"
    assert shortcut.path.read_bytes() == before


def test_background_start_is_windowless_and_explicit_reopen_keeps_engine(tmp_path):
    opened = []
    service = Service(tmp_path / "runtime", workers=1, studio_launcher=lambda url: opened.append(url) or True)
    try:
        service.start()
        instance = service.engine.instance_id
        assert opened == []
        assert request_owner_control(service.engine.root, "open_studio")["studio_launch"] == "requested"
        assert len(opened) == 1
        assert service.engine.instance_id == instance
        assert service.engine.phase == "running"
        assert service.engine.workers.status()["state"] == "ready"
        assert not service.stop_requested.is_set()
    finally:
        service.close()


def test_host_browser_detection_returns_existing_supported_executable():
    browser = installed_browser()
    assert browser is not None and browser.is_absolute() and browser.is_file()
    assert browser.name in {"msedge.exe", "chrome.exe"}
