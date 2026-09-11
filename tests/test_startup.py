from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from evidence_lane_plugin.credentials import unprotect
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.jobs import JobQueue
from evidence_lane_plugin.local_transport import LocalTransport
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.sdk import ActionRequest, EvidenceLaneClient
from evidence_lane_plugin.service import Service, request_owner_control
from evidence_lane_plugin.startup import LoginStartup
from evidence_lane_plugin.writers import WriterLease


class FakeRunValue:
    def __init__(self, value=None):
        self.value = value

    def read(self):
        return self.value

    def write(self, value):
        self.value = value

    def remove(self):
        self.value = None


def test_login_entry_is_owned_quoted_visible_and_removable(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("startup")
    interpreter = tmp_path / "installed runtime" / "pythonw.exe"
    interpreter.parent.mkdir()
    interpreter.touch()
    registry = FakeRunValue()
    startup = LoginStartup(interpreter, tmp_path / "state", backend=registry)
    assert '"' in startup.command()
    assert " -I -m evidence_lane_plugin.service " in startup.command()
    assert not startup.status()["registered"]
    assert startup.install()["studio_on_start"] == "open"
    assert startup.install()["registered"]
    assert not startup.uninstall()["registered"]
    assert registry.value is None


def test_login_management_preserves_unknown_or_changed_entries(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("startup-change")
    interpreter = tmp_path / "pythonw.exe"
    interpreter.touch()
    registry = FakeRunValue("some other selected installation")
    startup = LoginStartup(interpreter, tmp_path / "state", backend=registry)
    for action in (startup.install, startup.uninstall):
        with pytest.raises(LaneError) as error:
            action()
        assert error.value.code == "STARTUP_ENTRY_CHANGED"
    assert registry.value == "some other selected installation"
    assert startup.install(expected_previous=registry.value)["registered"]


def test_service_reconciles_uncertain_jobs_and_keeps_studio_launch_explicit(tmp_path):
    opened = []
    service = Service(tmp_path / "runtime", workers=1, capabilities=CapabilityMonitor(device_probe=list),
                      studio_launcher=lambda url: opened.append(url) or True)
    source = tmp_path / "source"
    source.mkdir()
    record = service.engine.directory.register(tmp_path / "state", source_root=source, create=True, read_only=False)
    store = service.engine.directory.open(record["project_id"], write=True)
    queue = JobQueue(store)
    with WriterLease(store, "previous-engine") as lease:
        queue.initialize(lease)
        job = queue.enqueue(ActionRequest(action="fixture", project_id=store.project_id), "fixture", lease)
        claim = queue.claim(job, lease)
        queue.prepare_effect(job, "once", "Uncertain external effect fixture", lease, execution_id=claim.execution_id)
    try:
        service.start()
        assert queue.get(job)["state"] == "uncertain"
        assert service.recovery[0]["automatic_replay"] is False
        assert len(opened) == 1 and urlsplit(opened[0]).path == "/studio/"
        assert urlsplit(opened[0]).fragment.startswith("ticket=")
        assert service.engine.workers.status()["state"] == "ready"
        with LocalTransport(service.engine.root) as transport:
            assert EvidenceLaneClient(transport).call("runtime_status").result["engine_phase"] == "running"
            denied = transport.http.post("/v4/control", json={"operation": "shutdown", "instance_id": service.engine.instance_id})
            assert denied.status_code == 401
            assert not service.stop_requested.is_set()
        endpoint = json.loads(service.endpoint.path.read_text())
        master = unprotect(endpoint["credential"])
        with httpx.Client(trust_env=False, timeout=5) as client:
            url = opened[0].split("/studio/")[0] + "/v4/control"
            request = {"operation": "open_studio", "instance_id": service.engine.instance_id}
            assert client.post(url, json=request).status_code == 401
            headers = {"Authorization": "Bearer " + master}
            assert client.post(url, headers=headers, json=request).json()["visible_window_verified"] is False
            assert len(opened) == 2
            assert request_owner_control(service.engine.root, "open_studio")["studio_launch"] == "requested"
            assert len(opened) == 3
            assert client.post(url, headers=headers | {"Origin": "https://untrusted.invalid"}, json=request).status_code == 403
            request["operation"] = "shutdown"
            assert client.post(url, headers=headers, json=request).json()["shutdown_requested"]
            assert service.stop_requested.is_set()
    finally:
        service.close()
    assert service.engine.phase == "stopped"
    assert service.engine.workers.status()["state"] == "stopped"


def test_read_only_registered_project_is_unchanged_on_startup(tmp_path):
    seed = Engine(tmp_path / "runtime")
    source = tmp_path / "source"
    source.mkdir()
    registered = seed.directory.register(tmp_path / "state", source_root=source, create=True, read_only=False)
    seed.directory.register(tmp_path / "state", read_only=True)
    database = seed.directory.open(registered["project_id"]).database
    before = database.read_bytes()
    service = Service(seed.root, workers=1, capabilities=CapabilityMonitor(device_probe=list), studio_launcher=lambda _: True)
    try:
        service.start()
        assert service.recovery[0]["state"] == "read_only"
    finally:
        service.close()
    assert database.read_bytes() == before


def test_actual_parent_crash_terminates_bound_worker_by_kernel_handle(tmp_path):
    source = Path(__file__).parents[1] / "plugins/evidence-lane-plugin/src"
    ready = tmp_path / "worker-ready.json"
    code = """
import json,os,sys
from pathlib import Path
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.runtime_health import CapabilityMonitor
from evidence_lane_plugin.workers import WorkerPool, WorkerOperation
if __name__ == '__main__':
    pool=WorkerPool((WorkerOperation('hash_text','evidence_lane_plugin.worker_tasks','hash_text'),),workers=1)
    engine=Engine(Path(sys.argv[1]),worker_pool=pool,capabilities=CapabilityMonitor(device_probe=list))
    engine.start()
    Path(sys.argv[2]).write_text(json.dumps(pool.status()))
    sys.stdin.readline()
    os._exit(17)
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path / "runtime"), str(ready)],
                               env={**os.environ, "PYTHONPATH": str(source)}, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    handle = None
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.OpenProcess.restype = ctypes.c_void_p
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    try:
        deadline = time.monotonic() + 15
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "The owned child did not acknowledge startup"
        pid = json.loads(ready.read_text())["initialized_workers"][0]["pid"]
        handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE; query only, never terminate by PID.
        assert handle
        process.stdin.write(b"crash\n")
        process.stdin.flush()
        assert process.wait(timeout=15) == 17
        assert api.WaitForSingleObject(handle, 10_000) == 0
        with Engine(tmp_path / "runtime", capabilities=CapabilityMonitor(device_probe=list)) as recovered:
            assert recovered.previous_shutdown == "unclean"
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=15)
        if handle:
            api.CloseHandle(handle)
