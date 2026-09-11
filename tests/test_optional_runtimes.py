from __future__ import annotations

import base64
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from evidence_lane_plugin import provider_probe
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.optional_runtimes import OptionalRuntime
from evidence_lane_plugin.provider_probe import ProbeUnavailable, verify_environment


@pytest.fixture
def installation(tmp_path, monkeypatch):
    environment = tmp_path / "environment"
    executable = environment / "Scripts/python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"fixture interpreter - never executed")
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    lock = contracts / "cpu.lock.txt"
    lock.write_text("fixture", encoding="utf-8")
    manifest = {"runtime_id": "cpu", "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
                "system": platform.system(), "machine": platform.machine(), "lock_file": lock.name,
                "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(), "packages": []}
    path = contracts / "cpu.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    record = {"runtime_id": "cpu", "python": str(executable), "manifest_path": str(path),
              "manifest_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
              "lock_sha256": manifest["lock_sha256"], "installation_state": "installed_from_locked_wheels"}
    (environment / "environment.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(environment))
    return environment, contracts, path, record


def test_manifest_and_lock_changes_cannot_qualify(installation):
    environment, contracts, path, _record = installation
    runtime = OptionalRuntime.from_installation(environment, contracts)
    assert verify_environment(path, runtime.manifest_sha256)["runtime_id"] == "cpu"
    (contracts / "cpu.lock.txt").write_text("changed")
    with pytest.raises(ProbeUnavailable, match="RUNTIME_LOCK_CHANGED"):
        verify_environment(path, runtime.manifest_sha256)
    path.write_text("{}")
    with pytest.raises(LaneError) as failure:
        OptionalRuntime.from_installation(environment, contracts)
    assert failure.value.code == "RUNTIME_MANIFEST_CHANGED"


def test_binding_cannot_switch_interpreter(installation):
    environment, contracts, _path, record = installation
    record["python"] = str(environment.parent / "injected.exe")
    (environment / "environment.json").write_text(json.dumps(record))
    with pytest.raises(LaneError) as failure:
        OptionalRuntime.from_installation(environment, contracts)
    assert failure.value.code == "RUNTIME_BINDING_CHANGED"


def test_wrong_platform_and_unisolated_runtime_refused(installation, monkeypatch):
    _, _, path, record = installation
    monkeypatch.setattr(platform, "machine", lambda: "OTHER_ARCH")
    with pytest.raises(ProbeUnavailable, match="RUNTIME_PLATFORM_MISMATCH"):
        verify_environment(path, record["manifest_sha256"])
    monkeypatch.undo()
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    with pytest.raises(ProbeUnavailable, match="ISOLATED_ENVIRONMENT_REQUIRED"):
        verify_environment(path, record["manifest_sha256"])


def test_installed_version_and_bytes_are_checked(installation, monkeypatch):
    environment, _, path, _ = installation
    manifest = json.loads(path.read_text())
    manifest["packages"] = [{"name": "example", "version": "1.0"}]
    path.write_text(json.dumps(manifest))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    binary = environment / "example.pyd"
    binary.write_bytes(b"original")
    hash_value = base64.urlsafe_b64encode(hashlib.sha256(binary.read_bytes()).digest()).rstrip(b"=").decode()
    entry = SimpleNamespace(hash=SimpleNamespace(mode="sha256", value=hash_value))
    distribution = SimpleNamespace(version="1.0", files=[entry], locate_file=lambda entry: binary)
    monkeypatch.setattr(provider_probe.importlib.metadata, "distribution", lambda name: distribution)
    assert verify_environment(path, digest)
    binary.write_bytes(b"tampered")
    with pytest.raises(ProbeUnavailable, match="PACKAGE_FILE_CHANGED"):
        verify_environment(path, digest)
    distribution.version = "2.0"
    with pytest.raises(ProbeUnavailable, match="LOCKED_PACKAGE_VERSION_CHANGED"):
        verify_environment(path, digest)


def test_directml_maps_an_nvidia_uuid_to_its_exact_dxgi_adapter(monkeypatch):
    adapter = {
        "device_index": 3,
        "vendor_id": 0x10DE,
        "device_id": "DXGI-1",
    }
    monkeypatch.setattr(provider_probe, "enumerate_dxgi", lambda: [adapter])
    monkeypatch.setattr(
        provider_probe,
        "nvidia_dxgi_adapter",
        lambda device_id, adapters: adapters[0]
        if device_id == "GPU-00000000-0000-0000-0000-000000000001"
        else None,
    )
    assert provider_probe.directml_adapter(
        {
            "device_index": 0,
            "device_id": "GPU-00000000-0000-0000-0000-000000000001",
        }
    ) == adapter


def test_directml_rejects_remapped_adapter_before_provider_import(monkeypatch):
    monkeypatch.setattr(provider_probe, "enumerate_dxgi", lambda: [
        {"device_index": 0, "vendor_id": 0x1002, "device_id": "DXGI-2"}])
    with pytest.raises(ProbeUnavailable, match="PROVIDER_DEVICE_IDENTITY_MISMATCH"):
        provider_probe.directml_probe({"device_index": 0, "device_id": "DXGI-1"})


def test_rocm_missing_amd_device_is_reported_before_import(monkeypatch):
    monkeypatch.setattr(provider_probe, "enumerate_dxgi", lambda: [{"vendor_id": 0x10DE}])
    with pytest.raises(ProbeUnavailable, match="AMD_ROCM_DEVICE_UNAVAILABLE"):
        provider_probe.torch_probe("rocm", {})


@pytest.mark.parametrize("fault", ["nonce", "runtime", "device", "digest", "oversize", "process"])
def test_child_results_must_match_trusted_request(installation, monkeypatch, fault):
    environment, contracts, _, _ = installation
    runtime = OptionalRuntime.from_installation(environment, contracts)

    def child(arguments, **kwargs):
        assert arguments[1] == "-I"
        assert Path(arguments[2]).name == "provider_probe.py"
        assert "CUDA_VISIBLE_DEVICES" not in kwargs["env"]
        request = json.loads(kwargs["input"])
        result = {"runtime_id": "cpu", "probe_id": request["probe_id"], "device_id": "cpu",
                  "self_test": "passed", "environment_digest": runtime.lock_sha256}
        fields = {"nonce": "probe_id", "runtime": "runtime_id", "device": "device_id", "digest": "environment_digest"}
        if fault in fields:
            result[fields[fault]] = "wrong"
        kwargs["stdout"].write(b"x" * 65_537 if fault == "oversize" else json.dumps(result).encode())
        return SimpleNamespace(returncode=1 if fault == "process" else 0)

    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "7")
    monkeypatch.setattr(subprocess, "run", child)
    assert runtime.probe()["self_test"] == "failed"


def test_timeout_returns_unavailable_and_never_retries(installation, monkeypatch):
    environment, contracts, _, _ = installation
    runtime = OptionalRuntime.from_installation(environment, contracts)
    calls = []

    def child(*args, **kwargs):
        calls.append(1)
        raise subprocess.TimeoutExpired("probe", 1)

    monkeypatch.setattr(subprocess, "run", child)
    assert runtime.probe(timeout=1)["reason"] == "PROVIDER_PROBE_TIMEOUT"
    assert calls == [1]
