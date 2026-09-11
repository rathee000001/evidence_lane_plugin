from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.service import Service, request_owner_control
from evidence_lane_plugin.shortcuts import StudioShortcut, shell_link
from evidence_lane_plugin.studio_gateway import StudioGateway
from evidence_lane_plugin.studio_window import StudioWindow, installed_browser


def test_window_uses_visible_app_frame_and_private_profile_without_debug_port(tmp_path):
    browser = tmp_path / "chrome.exe"
    browser.touch()
    calls = []

    def spawn(arguments, **options):
        calls.append((arguments, options))
        return SimpleNamespace(poll=lambda: None)

    window = StudioWindow(tmp_path / "runtime", browser=browser, spawn=spawn)
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


def test_real_windows_shortcut_create_readback_and_collision_preservation(tmp_path_factory):
    root = tmp_path_factory.mktemp("link")
    interpreter = Path(sys.executable).with_name("pythonw.exe")
    shortcut = StudioShortcut(interpreter, root / "state & $literal", root / "temporary menu")
    result = shortcut.install()
    assert result["registered"]
    assert shell_link(shortcut.path) == shortcut.specification()
    assert not shortcut.install()["changed"]
    assert not shortcut.uninstall()["registered"]
    assert not shortcut.path.exists()
    shortcut.path.write_bytes(b"unrelated user shortcut")
    before = shortcut.path.read_bytes()
    with pytest.raises(LaneError) as error:
        shortcut.install()
    assert error.value.code == "SHORTCUT_CHANGED"
    assert shortcut.path.read_bytes() == before


def test_reopen_does_not_replace_or_stop_engine(tmp_path):
    opened = []
    service = Service(tmp_path / "runtime", workers=1, studio_launcher=lambda url: opened.append(url) or True)
    try:
        service.start()
        instance = service.engine.instance_id
        assert request_owner_control(service.engine.root, "open_studio")["studio_launch"] == "requested"
        assert len(opened) == 2 and opened[0] != opened[1]
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
